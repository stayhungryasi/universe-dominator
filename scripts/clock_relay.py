#!/usr/bin/env python3
"""
시계 중계 (clock_relay) — 데일리 수집을 **정해진 KST 시각**에 깨운다
================================================================================
배경 (2026-11-04): 데일리는 GitHub 예약(08:17·12:17·18:17 KST)으로 돌았는데, 실측으로 예약이 **3~7시간씩 늦게**
울렸다 — '08:17' 회차가 11시대, '18:17' 회차가 다음 날 새벽 1~2시에 돌았다. 아침 브리핑은 '그 날의 첫 full 회차'에
나가므로 새벽 2:26 에 발송됐고, 그 회차는 미국 장중(정오 ET)이라 증감도 종가가 아니었다.

막으려는 것 한 문장: **데일리 수집과 아침 브리핑이 GitHub 예약의 지연에 끌려다니는 것.**

장치: cron-job.org 가 15분마다 깨우는 SpaceX 감시(spacex-watch.yml, 실측 1주 683회 전부 성공)가 이 스크립트를
돌려 시각이 된 슬롯이면 daily-update(full)을 실행 요청한다. 데일리 자체의 GitHub 예약은 지웠다 — 예약이 늦게
울리면 cancel-in-progress 로 진행 중인 회차를 끊었다(감시의 GitHub 예약 '47분'이 이 중계의 예비 시계다).

규칙(멱등):
  · 슬롯 시각 이후 **수집(full)** 회차가 이미 있으면 → 아무것도 안 한다(같은 슬롯에 두 번 쏘지 않는다).
    재빌드(quick)는 수집이 아니라 세지 않고, 취소된 수집도 실행된 것이 아니라 한 번 다시 쏜다(슬롯당 두 번까지)
  · 데일리가 진행 중·대기 중이면 → 기다린다(cancel-in-progress 로 빼앗지 않는다)
  · 재시도 슬롯(09:10)은 아침 슬롯(08:10) 이후 **성공한** 회차가 없을 때만 — 브리핑을 살리는 두 번째 기회
  · 슬롯이 지나고 유예 시간이 넘으면 쏘지 않는다(감시가 오래 멈췄다 돌아와 한꺼번에 쏘지 않게) — 다음 슬롯이 맡는다
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KST = timezone(timedelta(hours=9))
DAILY_WORKFLOW = "daily-update.yml"
# (시각, 종류, 유예 분) — 미국 정규장 마감(06:00 KST, 서머타임 05:00) 뒤 · 다음 개장(23:30) 전에만 돈다
SLOTS = (("08:10", "morning", 170), ("09:10", "retry", 60), ("12:10", "slot", 120), ("18:10", "slot", 120))


def now_kst():
    return datetime.now(KST)


def _t(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def slot_times(now, slots=SLOTS):
    """오늘(KST) 슬롯 → [(라벨, 종류, 시각, 유예끝)]."""
    out = []
    for hhmm, kind, grace in slots:
        h, m = (int(x) for x in hhmm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        out.append((hhmm, kind, t, t + timedelta(minutes=grace)))
    return out


QUICK_TAG = "· quick"     # daily-update.yml run-name 의 꼬리 — 실행 기록 API 는 입력값(mode)을 돌려주지 않는다


def is_full(r):
    """수집(full) 회차인가 — 실행 이름의 모드 꼬리로 가른다. 꼬리가 없는 옛 회차(2026-11-09 이전)는 full 로 친다
    (보수적: 같은 슬롯에 두 번 쏘지 않는 쪽).

    2026-11-09 실측: 다른 세션의 quick(업로드 반영)이 슬롯 뒤에 돌면 '이미 실행됨'으로 세어 그 슬롯의 수집을
    통째로 건너뛸 수 있었다. 재빌드는 수집이 아니다."""
    return QUICK_TAG not in str(r.get("display_title") or "")


def decide(now, runs, slots=SLOTS):
    """→ (쏠 슬롯 라벨 | None, 사유). **순수 함수** — runs: [{created_at, status, conclusion, display_title}]
    (데일리 실행 기록). 진행 중 판정은 모드 무관(무엇이든 돌고 있으면 기다린다), '이미 실행됨'·'성공'은 full 만 센다."""
    busy = [r for r in runs or [] if r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested")]
    full = [r for r in runs or [] if is_full(r)]
    sl = slot_times(now, slots)
    morning = next((t for _, k, t, _ in sl if k == "morning"), None)
    for label, kind, t, end in reversed(sl):        # 가장 최근 슬롯부터 — 09:15 에는 아침 슬롯보다 재시도 슬롯이 먼저
        if not (t <= now < end):
            continue
        if busy:
            return None, f"{label} 슬롯 — 데일리가 진행 중이라 기다린다(빼앗지 않는다)"
        if kind == "retry":
            ok = [r for r in full if r.get("conclusion") == "success" and morning and _t(r["created_at"]) >= morning]
            if ok:
                return None, f"{label} 재시도 슬롯 — 아침 슬롯 뒤 성공한 수집 회차가 있다"
            # 재시도는 한 번만 — 2026-11-06~09 실측: 실패한 회차가 2분 만에 끝나 15분마다 다시 쏘았다(09:15·09:30·09:45·10:00)
            if [r for r in full if _t(r["created_at"]) >= t]:
                return None, f"{label} 재시도 슬롯 — 이미 한 번 다시 쐈다"
            return label, f"{label} 재시도 슬롯 — 아침 슬롯 뒤 성공한 수집 회차가 없다(브리핑 두 번째 기회)"
        since = [r for r in full if _t(r["created_at"]) >= t]
        done = [r for r in since if r.get("conclusion") != "cancelled"]
        if done:
            return None, f"{label} 슬롯 — 이미 실행됨({len(done)}회)"
        # 취소된 수집은 실행된 것이 아니다(11/09 02:14Z: 복구 full 이 quick 에 끊겼다) — 다시 쏘되 슬롯당 두 번까지
        if len(since) >= 2:
            return None, f"{label} 슬롯 — 수집이 {len(since)}번 취소됐다. 이번 슬롯은 접는다(다음 슬롯이 맡는다)"
        if since:
            return label, f"{label} 슬롯 — 수집 회차가 취소돼 한 번 다시 요청"
        return label, f"{label} 슬롯 — 실행 요청"
    return None, "슬롯 시각 아님"


def _req(method, path, body=None):
    import requests
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "univertrix-clock-relay"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    url = f"https://api.github.com/repos/{repo}/{path}"
    r = requests.request(method, url, headers=hdr, json=body, timeout=30)
    return r.status_code, (r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else {})


def run(dispatch=True):
    if not os.environ.get("GITHUB_REPOSITORY"):
        print("[시계] GITHUB_REPOSITORY 없음 — 로컬에선 판정만 하지 않는다")
        return 0
    now = now_kst()
    code, doc = _req("GET", f"actions/workflows/{DAILY_WORKFLOW}/runs?per_page=20")
    if code != 200:
        print(f"[시계] 데일리 실행 기록 조회 실패(HTTP {code}) — 이번엔 쏘지 않는다")
        return 0
    runs = [{"created_at": r.get("created_at"), "status": r.get("status"), "conclusion": r.get("conclusion"),
             "display_title": r.get("display_title")}
            for r in doc.get("workflow_runs") or []]
    label, why = decide(now, runs)
    print(f"[시계] {now:%m/%d %H:%M} KST — {why}")
    if not label or not dispatch:
        return 0
    c, d = _req("POST", f"actions/workflows/{DAILY_WORKFLOW}/dispatches", {"ref": "main", "inputs": {"mode": "full"}})
    if c in (200, 204):
        print(f"[시계] daily-update(full) 실행 요청 완료 — {label} 슬롯")
        return 0
    msg = f"[시계] 실행 요청 실패(HTTP {c}) {json.dumps(d, ensure_ascii=False)[:200]}"
    print(msg)
    # 슬롯의 첫 시도에서만 소장 DM(15분마다 다시 시도하므로 같은 실패를 여러 번 울리지 않게) — 계속 실패하면
    # 일일 점검의 '데일리 수집 마지막 성공' 줄이 30시간에 ⚠️ 로 다시 말한다
    t = next((t for lb, _k, t, _e in slot_times(now) if lb == label), None)
    if t is not None and now - t < timedelta(minutes=15):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from spacex_watch import send_dm
            send_dm(f"⚠️ UNIVERTRIX 시계 중계 — {label} 슬롯 데일리 실행 요청 실패(HTTP {c}). 15분마다 다시 시도합니다.")
        except Exception as e:
            print(f"[시계] DM 실패({type(e).__name__})")
    return 1


if __name__ == "__main__":
    sys.exit(run(dispatch="--dry" not in sys.argv))
