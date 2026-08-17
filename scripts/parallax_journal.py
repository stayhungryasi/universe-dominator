#!/usr/bin/env python3
"""
시차 관측 → 관측노트 자동 기록 (parallax_journal)
================================================================
매일의 괴리 숫자를 전부 적으면 노트가 아니라 로그가 된다. 여기서는
**임계 돌파 사건만** 적는다 — 전일 대비 '존'이 바뀐 티커만.

존 경계: 탐색선 +60% · 본격선 +40% · 정당선 0%  (config zones 기준)
  고평가(<0) < 관망(0~40%) < 본격(40~60%) < 탐색(60%↑)

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
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"zones": {}, "gaps": {}, "posted": {}}


def save_state(state):
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    state["posted"] = {d: v for d, v in state.get("posted", {}).items() if d >= cutoff}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def sig(ticker, before, after, day):
    key = f"parallax:{ticker}:{before}>{after}:{day}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


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


def detect_events(items, zones, prev_zones, prev_gaps):
    """전일 존과 다른 티커만 사건으로 본다. 첫 관측(전일 기록 없음)은 사건이 아니다."""
    events = []
    for x in items:
        t = x.get("ticker")
        gap = x.get("gap")
        now_zone = zone_of(gap, zones)
        if now_zone is None:
            continue                      # 못 잰 종목은 사건이 될 수 없다
        before = prev_zones.get(t)
        if before is None or before == now_zone:
            continue                      # 첫 관측이거나 변화 없음 → 침묵
        events.append({
            "ticker": t, "before": before, "after": now_zone,
            "gap_before": prev_gaps.get(t), "gap_after": gap,
            "text": phrase(t, before, now_zone, prev_gaps.get(t), gap),
        })
    # 변화 폭이 큰 순서로 — 상한에 걸릴 때 더 중요한 사건이 남도록
    events.sort(key=lambda e: abs(ZONE_ORDER.index(e["after"]) - ZONE_ORDER.index(e["before"])),
                reverse=True)
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
    today = datetime.now(KST).strftime("%Y-%m-%d")

    events = detect_events(items, zones, prev_zones, prev_gaps)

    # 오늘의 존을 다음 실행의 기준선으로 먼저 갱신 (발행 실패해도 기준선은 전진)
    new_zones, new_gaps = dict(prev_zones), dict(prev_gaps)
    for x in items:
        z = zone_of(x.get("gap"), zones)
        if z:
            new_zones[x["ticker"]] = z
            new_gaps[x["ticker"]] = x.get("gap")
    state["zones"], state["gaps"] = new_zones, new_gaps

    if not events:
        save_state(state)
        print("[시차노트] 존 전이 없음 — 침묵")
        return 0

    posted_today = set(state.get("posted", {}).get(today, []))
    fresh = [e for e in events if sig(e["ticker"], e["before"], e["after"], today) not in posted_today]
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
            posted_today.add(sig(e["ticker"], e["before"], e["after"], today))
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
