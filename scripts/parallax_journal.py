#!/usr/bin/env python3
"""
시차 관측 → 관측노트 자동 기록 (parallax_journal)
================================================================
매일의 괴리 숫자를 전부 적으면 노트가 아니라 로그가 된다. 여기서는
**임계 돌파 사건만** 적는다 — 전일 대비 '존'이 바뀐 티커만.

존 경계: 탐색선 +60% · 본격선 +40% · 정당선 0%  (config zones 기준)
  고평가(<0) < 관망(0~40%) < 본격(40~60%) < 탐색(60%↑)

레전드벤치마크(2026-08-30): 괴리존과 **별개로** 버핏존(zone_buffett) 전이도 적는다.
단 untested 가 낀 전이와 cause=scale(잰 자가 바뀜)은 시장 사건이 아니므로 제외한다.

첫 실행일은 비교 기준이 없으므로 아무것도 적지 않는다(기준선만 저장).
사건 서명 parallax:{ticker}:{전존>후존}:{날짜} 로 중복 발행을 막고,
하루 최대 5건까지만 적는다(도배 방지).

기록 대상: Firestore 'judgments' 컬렉션 — 관측노트(journal.html)가 읽는 곳.
쓰기는 서비스 계정(post_community_notice.py 와 같은 경로)으로 adminUid 소유로 남긴다.
FIREBASE_SERVICE_ACCOUNT 없으면 조용히 건너뛴다 (파이프라인 안 깨짐).
"""
import hashlib
import json
import os
import random
import string
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
BUFFETT_PATH = DATA_DIR / "buffett.json"
CFG_PATH = DATA_DIR / "firebase_config.json"
STATE_PATH = DATA_DIR / "parallax_state.json"
KST = timezone(timedelta(hours=9))

MAX_PER_DAY = 5
COLLECTION = "judgments"      # 관측노트가 읽는 컬렉션
TAG = "시차"

ZONE_ORDER = ["고평가", "관망", "본격", "탐색"]

# ── 레전드벤치마크: 버핏존 전이 ────────────────────────────────────
# 기록 대상은 **판정이 실제로 바뀐 것**뿐이다. 두 가지는 사건이 아니다:
#   ① untested 가 낀 전이 — '못 쟀다 → 쟀다'는 시장 사건이 아니라 취재 사건이다.
#      (전 종목이 untested 인 1단계에서 취재가 시작되는 순간 34건이 터지는 것을 막는다)
#   ② cause=scale — 잰 자가 바뀐 것(EPS 취재·성장률 갱신·가드 토글).
#      기존 fingerprint 규율과 같은 뿌리: 눈금이 바뀐 것을 시장이 움직였다고 적으면 안 된다.
BUFFETT_ZONES = ["bond_inferior", "prove_growth", "pass"]
UNTESTED = "untested"


def zone_of(gap, zones):
    if gap is None:
        return None
    if gap >= zones.get("explore", 0.60):
        return "탐색"
    if gap >= zones.get("commit", 0.40):
        return "본격"
    if gap >= 0:
        return "관망"
    return "고평가"


def load_state():
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                st.setdefault("bzones", {})
                return st
        except Exception:
            pass
    return {"zones": {}, "gaps": {}, "fp": {}, "bzones": {}, "posted": {}}


def save_state(state):
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    state["posted"] = {d: v for d, v in state.get("posted", {}).items() if d >= cutoff}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def sig(ticker, before, after, day, kind="parallax"):
    key = f"{kind}:{ticker}:{before}>{after}:{day}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def buffett_phrase(ticker, before, after, bench):
    """선장님 지정 문구 — coupon10y 와 문턱(10y×3)을 나란히 적어 근거를 남긴다."""
    rate = bench.get("rate10y")
    bar = None if rate is None else 3.0 * (rate / 100.0)
    g = bench.get("g_used")
    g_txt = "—" if g is None else f"{g * 100:.1f}%"
    return (f"{ticker} 버핏존 {before}→{after} · "
            f"coupon10y {_pct(bench.get('coupon10y'))} vs 10y×3 {_pct(bar)} · g={g_txt}")


def detect_buffett_events(items, prev_bzones):
    """버핏존이 바뀐 종목만. untested 가 낀 전이와 cause=scale 은 사건이 아니다."""
    events, skipped = [], []
    for x in items:
        bench = x.get("bench")
        if not isinstance(bench, dict):
            continue
        after = bench.get("zone_buffett")
        before = prev_bzones.get(x.get("ticker"))
        if not after or not before or before == after:
            continue                       # 첫 관측·미산출(가드)·변화 없음 → 침묵
        if after == UNTESTED or before == UNTESTED:
            skipped.append(f"{x.get('ticker')}(untested 전이)")
            continue
        if bench.get("cause") == "scale":
            skipped.append(f"{x.get('ticker')}(자가 바뀜)")
            continue
        events.append({
            "ticker": x.get("ticker"), "before": before, "after": after,
            "kind": "buffett",
            "text": buffett_phrase(x.get("ticker"), before, after, bench),
        })
    # 채권 우위로 떨어진 것부터 — 상한에 걸릴 때 나쁜 소식이 먼저 남도록
    events.sort(key=lambda e: BUFFETT_ZONES.index(e["after"])
                if e["after"] in BUFFETT_ZONES else 9)
    if skipped:
        print(f"[시차노트] 버핏존 전이 {len(skipped)}건은 기록 대상 아님 "
              f"({', '.join(skipped[:6])})")
    return events


def phrase(ticker, before, after, gap_before, gap_after):
    """존 전이 한 줄 — 올라섰는지 내려섰는지를 방향으로 말한다"""
    up = ZONE_ORDER.index(after) > ZONE_ORDER.index(before)
    if after == "본격":
        tail = "본격선 위로" if up else "본격선 아래로"
    elif after == "탐색":
        tail = "탐색선 위로"
    elif after == "고평가":
        tail = "정당선 위로 (고평가 구간)"
    else:
        tail = "본격선 아래로" if before in ("본격", "탐색") else "정당선 아래로"
    gb = "—" if gap_before is None else f"{gap_before * 100:+.0f}%"
    ga = "—" if gap_after is None else f"{gap_after * 100:+.0f}%"
    return f"🔭 시차 관측 — {ticker} 괴리 {gb} → {ga}, {tail}"


def is_zoned(x):
    """존 판정 대상인가 — 측정층이 매긴 zoned 를 그대로 읽기만 한다.

    자격 규칙(선행 기준일 것 · 시클리컬이 아닐 것)은 fetch_buffett.py 한 곳에만 둔다.
    여기서 basis·type 을 다시 해석하면 규칙이 두 벌이 되어 언젠가 어긋난다.
    zoned 가 없는 구 데이터는 대상 없음으로 본다 — 침묵이 오탐보다 안전하다."""
    return bool(x.get("zoned"))


def fingerprint(x):
    """이 종목을 '무엇으로 쟀는가'의 지문 — 자가 바뀌면 전후를 비교하면 안 된다.

    2026-08-17 사고: AMZN 이 후행→선행으로 바뀌자 주가가 1원도 안 움직였는데
    (262.65 → 262.65) 괴리가 +42%→+6% 로 튀어 "본격선 아래로" 가 기록됐다.
    시장이 움직인 게 아니라 눈금이 바뀐 것이다. 지문이 다르면 재기준만 잡고 침묵한다.
    판단층의 정당 MAX·EPS 취재 시점이 바뀌는 경우(실적 시즌 갱신)도 같은 부류."""
    return "|".join([str(x.get("basis") or ""), str(x.get("fair_max")), str(x.get("eps_asof") or "")])


def detect_events(items, zones, prev_zones, prev_gaps, prev_fp=None):
    """전일 존과 다른 티커만 사건으로 본다. 첫 관측(전일 기록 없음)은 사건이 아니다."""
    prev_fp = prev_fp or {}
    events, remeasured = [], []
    for x in items:
        t = x.get("ticker")
        gap = x.get("gap")
        if not is_zoned(x):
            continue                      # 후행·미취재는 임계 이벤트 대상이 아니다
        now_zone = zone_of(gap, zones)
        if now_zone is None:
            continue                      # 못 잰 종목은 사건이 될 수 없다
        before = prev_zones.get(t)
        if before is None or before == now_zone:
            continue                      # 첫 관측이거나 변화 없음 → 침묵
        was = prev_fp.get(t)
        if was and was != fingerprint(x):
            remeasured.append(t)          # 자가 바뀐 것 — 시장 사건이 아니다
            continue
        events.append({
            "ticker": t, "before": before, "after": now_zone,
            "gap_before": prev_gaps.get(t), "gap_after": gap,
            "text": phrase(t, before, now_zone, prev_gaps.get(t), gap),
        })
    # 변화 폭이 큰 순서로 — 상한에 걸릴 때 더 중요한 사건이 남도록
    events.sort(key=lambda e: abs(ZONE_ORDER.index(e["after"]) - ZONE_ORDER.index(e["before"])),
                reverse=True)
    if remeasured:
        print(f"[시차노트] 측정 기준이 바뀐 {len(remeasured)}종은 기록하지 않고 재기준 "
              f"({', '.join(remeasured[:6])})")
    return events


def firestore_token(sa_info):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/datastore"])
    creds.refresh(Request())
    return creds.token


def post_note(sa_info, token, admin_uid, text):
    pid = sa_info["project_id"]
    doc_id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    doc = f"projects/{pid}/databases/(default)/documents/{COLLECTION}/{doc_id}"
    body = {"writes": [
        {"update": {"name": doc, "fields": {
            "uid": {"stringValue": admin_uid},
            "text": {"stringValue": text},
            "tags": {"arrayValue": {"values": [{"stringValue": TAG}]}},
        }}, "currentDocument": {"exists": False}},
        {"transform": {"document": doc, "fieldTransforms": [
            {"fieldPath": "created", "setToServerValue": "REQUEST_TIME"}]}},
    ]}
    r = requests.post(
        f"https://firestore.googleapis.com/v1/projects/{pid}/databases/(default)/documents:commit",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=30)
    r.raise_for_status()


def main():
    if not BUFFETT_PATH.exists():
        print("[시차노트] buffett.json 없음 — 건너뜀")
        return 0
    data = json.loads(BUFFETT_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])
    zones = data.get("zones", {"explore": 0.60, "commit": 0.40})

    state = load_state()
    prev_zones = state.get("zones", {})
    prev_gaps = state.get("gaps", {})
    prev_fp = state.get("fp", {})
    today = datetime.now(KST).strftime("%Y-%m-%d")

    events = detect_events(items, zones, prev_zones, prev_gaps, prev_fp)
    for e in events:
        e.setdefault("kind", "parallax")
    events += detect_buffett_events(items, state.get("bzones", {}))

    # 오늘의 존을 다음 실행의 기준선으로 먼저 갱신 (발행 실패해도 기준선은 전진)
    # 기준선도 존 판정 대상(선행)만 남긴다 — 후행 잔재가 남아 있으면 다음 날
    # 그 종목이 사라지거나 되살아날 때 유령 전이가 잡힌다
    zoned_now = {x["ticker"] for x in items if is_zoned(x) and zone_of(x.get("gap"), zones)}
    purged = [t for t in prev_zones if t not in zoned_now]
    new_zones = {t: z for t, z in prev_zones.items() if t in zoned_now}
    new_gaps = {t: g for t, g in prev_gaps.items() if t in zoned_now}
    new_fp = {t: f for t, f in prev_fp.items() if t in zoned_now}
    for x in items:
        if not is_zoned(x):
            continue
        z = zone_of(x.get("gap"), zones)
        if z:
            new_zones[x["ticker"]] = z
            new_gaps[x["ticker"]] = x.get("gap")
            new_fp[x["ticker"]] = fingerprint(x)
    if purged:
        print(f"[시차노트] 기준선 정리 — 존 판정 대상 아닌 {len(purged)}종 제외 "
              f"({', '.join(purged[:6])}{' 외' if len(purged) > 6 else ''})")
    state["zones"], state["gaps"], state["fp"] = new_zones, new_gaps, new_fp

    # 버핏존 기준선 — 산출된 종목만 남긴다. 가드로 미산출(None)인 종목을 남겨두면
    # 가드가 풀리는 날 '없음 → 판정' 이 전이로 잡힌다(유령 전이).
    state["bzones"] = {x.get("ticker"): (x.get("bench") or {}).get("zone_buffett")
                       for x in items
                       if isinstance(x.get("bench"), dict)
                       and (x.get("bench") or {}).get("zone_buffett")}

    if not events:
        save_state(state)
        print("[시차노트] 존 전이 없음 — 침묵")
        return 0

    posted_today = set(state.get("posted", {}).get(today, []))
    fresh = [e for e in events
             if sig(e["ticker"], e["before"], e["after"], today, e.get("kind", "parallax"))
             not in posted_today]
    if not fresh:
        save_state(state)
        print(f"[시차노트] 전이 {len(events)}건 모두 기록 완료 상태 — 침묵")
        return 0

    dropped = max(0, len(fresh) - MAX_PER_DAY)
    fresh = fresh[:MAX_PER_DAY]

    sa_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not sa_raw:
        save_state(state)
        print(f"[시차노트] FIREBASE_SERVICE_ACCOUNT 미설정 — 기록 건너뜀 "
              f"(전이 {len(events)}건 감지됨)")
        return 0
    try:
        sa_info = json.loads(sa_raw)
    except Exception:
        save_state(state)
        print("[시차노트] 서비스 계정 JSON 파싱 실패 — 건너뜀", file=sys.stderr)
        return 0

    admin_uid = ""
    if CFG_PATH.exists():
        try:
            admin_uid = json.loads(CFG_PATH.read_text(encoding="utf-8")).get("adminUid", "")
        except Exception:
            pass
    if not admin_uid:
        save_state(state)
        print("[시차노트] adminUid 없음 — 기록 건너뜀", file=sys.stderr)
        return 0

    try:
        token = firestore_token(sa_info)
    except Exception as e:
        save_state(state)
        print(f"[시차노트] 토큰 발급 실패 → 건너뜀 ({e})", file=sys.stderr)
        return 0

    done = 0
    for e in fresh:
        try:
            post_note(sa_info, token, admin_uid, e["text"])
            posted_today.add(sig(e["ticker"], e["before"], e["after"], today,
                                 e.get("kind", "parallax")))
            done += 1
            print(f"[시차노트] 기록: {e['text']}")
        except Exception as ex:
            print(f"[시차노트] {e['ticker']} 기록 실패 → 건너뜀 ({ex})", file=sys.stderr)

    state.setdefault("posted", {})[today] = sorted(posted_today)
    save_state(state)
    if dropped:
        print(f"[시차노트] 하루 상한 {MAX_PER_DAY}건 초과 — {dropped}건은 기록하지 않음")
    print(f"[시차노트] 존 전이 {len(events)}건 중 {done}건 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main())
