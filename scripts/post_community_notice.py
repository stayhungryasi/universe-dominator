#!/usr/bin/env python3
"""
관제탑 봇 (post_community_notice) — 사이트 변동을 커뮤니티에 자동 공지
================================================================
파이프라인 후반에 실행. 아래 변동을 감지해 "UNIVERTRIX 관제탑" 명의로
커뮤니티(Firestore posts)에 요약 교신 1건을 발행한다.

감지 항목:
  👑 왕좌 교체 (지구 1위 변경)
  🌍 지구 TOP 20 진입 / 탈락
  ✦ 잠재지배자 신규 / 제외
  🔭 새 관측일지 발행

원칙:
  - 변화가 없으면 침묵 (스팸 금지)
  - 하루 3회 실행돼도 같은 소식은 1번만 (data/notice_state.json으로 발행 이력 관리)
  - 실패해도 파이프라인 계속 (continue-on-error)

인증: GitHub Secret FIREBASE_SERVICE_ACCOUNT (Firebase 서비스 계정 JSON 전체)
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
SNAP_DIR = DATA_DIR / "snapshots"
STATE_PATH = DATA_DIR / "notice_state.json"
KST = timezone(timedelta(hours=9))

BOT_UID = "univertrix_bot"
BOT_NAME = "UNIVERTRIX 관제탑"


# ─────────────────── 변동 감지 ───────────────────

def load_prev_snapshot(today_str):
    if not SNAP_DIR.exists():
        return None
    for s in sorted(SNAP_DIR.glob("*.json"), reverse=True):
        if today_str not in s.name:
            try:
                return json.loads(s.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def detect_events():
    latest = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    today = latest.get("meta", {}).get("fetched_date", "")
    prev = load_prev_snapshot(today)
    events = []

    cur_earth = latest["regions"]["earth"]["stocks"]
    cur_latent = latest.get("latent", [])

    if prev:
        prev_earth = prev.get("regions", {}).get("earth", [])
        prev_latent = prev.get("latent", [])

        # 👑 왕좌 교체
        if prev_earth and cur_earth and prev_earth[0]["ticker"] != cur_earth[0]["ticker"]:
            events.append(("throne",
                f"👑 왕좌 교체! {prev_earth[0]['name']} → {cur_earth[0]['name']} "
                f"(${cur_earth[0]['mc']/1000:.2f}T)"))

        # 🌍 지구 TOP 20 진입/탈락
        pv = {s["ticker"]: s["name"] for s in prev_earth}
        cv = {s["ticker"]: s["name"] for s in cur_earth}
        enters = [cv[t] for t in cv if t not in pv]
        exits = [pv[t] for t in pv if t not in cv]
        if enters or exits:
            parts = []
            if enters:
                parts.append("진입 " + " · ".join(enters[:3]))
            if exits:
                parts.append("탈락 " + " · ".join(exits[:3]))
            events.append(("earth20", "🌍 지구 TOP 20 변동 — " + " / ".join(parts)))

        # ✦ 잠재지배자 변동
        pl = {s["ticker"]: s["name"] for s in prev_latent}
        cl = {s["ticker"]: s["name"] for s in cur_latent}
        l_in = [cl[t] for t in cl if t not in pl]
        l_out = [pl[t] for t in pl if t not in cl]
        if l_in or l_out:
            parts = []
            if l_in:
                parts.append("신규 " + " · ".join(l_in[:3]))
            if l_out:
                parts.append("제외 " + " · ".join(l_out[:3]))
            events.append(("latent", "✦ 잠재지배자 변동 — " + " / ".join(parts)))

    # 🔭 새 관측일지
    cols_path = DATA_DIR / "columns.json"
    if cols_path.exists():
        state = load_state()
        known = set(state.get("columns", []))
        cols = json.loads(cols_path.read_text(encoding="utf-8")).get("columns", [])
        for c in cols:
            if c.get("title") and c["title"] not in known:
                events.append(("column", f"🔭 새 관측일지 발행 — 「{c['title']}」"))

    return today, events


# ─────────────────── 발행 이력 (중복 방지) ───────────────────

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"posted": {}, "columns": []}


def save_state(state):
    # 7일 지난 발행 이력은 청소
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    state["posted"] = {d: v for d, v in state.get("posted", {}).items() if d >= cutoff}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def sig(kind, msg, today):
    """발행 이력 서명 — 문구가 아니라 '사건'을 기억한다.
    왕좌/TOP20/잠재: 종류+날짜 (같은 날 문구가 달라도 재발행 금지)
    관측일지: 제목 앞 14자 (주간 칼럼이 재생성돼 부제가 바뀌어도 같은 주면 동일 사건)"""
    if kind == "column":
        title = msg.split("「", 1)[-1]  # 「제목」 부분만
        key = f"column:{title[:14]}"
    else:
        key = f"{kind}:{today}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ─────────────────── Firestore 발행 ───────────────────

def firestore_token(sa_info):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/datastore"])
    creds.refresh(Request())
    return creds.token


def post_to_community(sa_info, text, tags):
    pid = sa_info["project_id"]
    token = firestore_token(sa_info)
    doc_id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    doc = f"projects/{pid}/databases/(default)/documents/posts/{doc_id}"
    fields = {
        "uid": {"stringValue": BOT_UID},
        "name": {"stringValue": BOT_NAME},
        "photo": {"stringValue": ""},
        "text": {"stringValue": text},
        "tags": {"arrayValue": {"values": [{"stringValue": t} for t in tags[:3]]}},
        "img": {"stringValue": ""},
        "imgId": {"stringValue": ""},
        "pinned": {"booleanValue": False},
    }
    body = {"writes": [
        {"update": {"name": doc, "fields": fields},
         "currentDocument": {"exists": False}},
        {"transform": {"document": doc, "fieldTransforms": [
            {"fieldPath": "created", "setToServerValue": "REQUEST_TIME"}]}},
    ]}
    r = requests.post(
        f"https://firestore.googleapis.com/v1/projects/{pid}/databases/(default)/documents:commit",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=30)
    r.raise_for_status()


# ─────────────────── main ───────────────────

def main():
    sa_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not sa_raw:
        print("[관제탑] FIREBASE_SERVICE_ACCOUNT 미설정 — 건너뜀")
        return
    try:
        sa_info = json.loads(sa_raw)
    except Exception:
        print("[관제탑] 서비스 계정 JSON 파싱 실패 — 건너뜀", file=sys.stderr)
        return

    today, events = detect_events()
    if not events:
        print("[관제탑] 오늘 변동 없음 — 침묵")
        return

    state = load_state()
    posted_today = set(state.get("posted", {}).get(today, []))
    fresh = [(k, msg) for k, msg in events if sig(k, msg, today) not in posted_today]
    if not fresh:
        print(f"[관제탑] 변동 {len(events)}건 모두 발행 완료 상태 — 침묵")
        return

    date_label = datetime.now(KST).strftime("%m.%d")
    lines = [f"🛰️ 오늘의 우주 변동 ({date_label})", ""]
    lines += [msg for _, msg in fresh]
    lines += ["", "자세한 관측 → univertrix.com"]
    text = "\n".join(lines)[:500]

    try:
        post_to_community(sa_info, text, tags=["관제탑 공지"])
    except Exception as e:
        print(f"[관제탑] 발행 실패: {e}", file=sys.stderr)
        sys.exit(0)  # 실패해도 파이프라인·기존 상태 유지

    posted_today.update(sig(k, msg, today) for k, msg in fresh)
    state.setdefault("posted", {})[today] = sorted(posted_today)
    # 관측일지 발행 이력 갱신
    cols_path = DATA_DIR / "columns.json"
    if cols_path.exists():
        cols = json.loads(cols_path.read_text(encoding="utf-8")).get("columns", [])
        state["columns"] = [c.get("title", "") for c in cols]
    save_state(state)
    print(f"[관제탑] 발행 완료 — {len(fresh)}건 요약:")
    for _, msg in fresh:
        print(f"    {msg}")


if __name__ == "__main__":
    main()
