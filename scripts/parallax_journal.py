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
2026-09-25(legend-audit A): 버핏존은 **2거래일 연속 확인 후에만** 확정·기록하고,
확정 전이는 소장 DM 으로도 보낸다(여유%·원인 명시). 하루짜리 역전은 '경계' 로그만.

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

# ── 버핏존 전이 확정 규칙 (2026-09-25 legend-audit A) ─────────────────
# 막으려는 것 한 문장: **판정선 위에 선 종목의 하루짜리 흔들림이 사건으로 기록·발송되는 것.**
# 9월 실측: 시장 전이 2건(META 9/12 여유 +0.3% · TSM 9/25 +1.5%)이 전부 판정선 ±2% 안쪽이었다.
# 선 위에서는 금리 몇 bp·주가 1% 로 존이 뒤집힌다 — 그걸 매번 적으면 노트가 로그가 된다.
#   ① 새 존이 **2거래일 연속**(KST 평일, 거래일당 1회 — 잠재지배자 명단과 같은 달력)
#      관측돼야 확정한다. 1일짜리 역전은 '경계' 로그만 남긴다.
#   ② 원인이 금리이고 금리 변동폭이 10bp 미만이면 확정하지 않는다(경계).
#   ③ 원인·여유는 **확정 직전의 제자리(anchor)** 에서 잰다 — 전 회차가 아니라 마지막으로
#      옛 존에 서 있던 관측과 비교해야 두 날에 걸친 움직임 전체가 원인에 잡힌다.
CONFIRM_DAYS = 2
RATE_MIN_BP = 10.0
ZONE_KO = {"pass": "통과", "prove_growth": "성장 입증 필요",
           "bond_inferior": "국채 열위", "untested": "미검정"}
CAUSE_KO = {"price": "주가", "rate": "금리", "scale": "눈금 변경"}
LINE_KO = {"pass": "통과가격", "prove": "국채 1.5배선 가격"}
SNAP_KEYS = ("price", "rate10y", "eps_adj_ttm", "g_used", "guard")


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
    state["dm_sent"] = {d: v for d, v in state.get("dm_sent", {}).items() if d >= cutoff}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def sig(ticker, before, after, day, kind="parallax"):
    key = f"{kind}:{ticker}:{before}>{after}:{day}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def margin_phrase(line, margin):
    """판정선 여유 한 마디 — **상태(선·여유)에서 만든다.** 기계 이름은 쓰지 않는다."""
    if line not in LINE_KO or margin is None:
        return ""
    if abs(margin) < 0.0005:
        return f"현재가가 {LINE_KO[line]}와 같음"
    side = "높음" if margin > 0 else "낮음"
    return f"현재가가 {LINE_KO[line]}보다 {abs(margin) * 100:.1f}% {side}"


def cause_phrase(cause, anchor, bench):
    """원인과 그 크기 — 금리는 bp, 주가는 %. 원인을 못 가렸으면 그렇게 말한다."""
    if cause == "rate":
        r0, r1 = (anchor or {}).get("rate10y"), bench.get("rate10y")
        if r0 is not None and r1 is not None:
            return f"원인 금리 {(r1 - r0) * 100:+.0f}bp"
    if cause == "price":
        p0, p1 = (anchor or {}).get("price"), bench.get("price")
        if p0 and p1:
            return f"원인 주가 {(p1 / p0 - 1) * 100:+.1f}%"
    if cause in CAUSE_KO:
        return f"원인 {CAUSE_KO[cause]}"
    return "원인 미상(비교 기준 없음)"


def buffett_phrase(ticker, before, after, bench, cause=None, anchor=None, days=CONFIRM_DAYS):
    """버핏존 확정 전이 한 줄 — 관측노트와 DM 이 같은 문장을 쓴다.

    선장님 지정 골격(쿠폰과 문턱을 나란히)은 그대로 두되 **화면에 나가는 문장이라**
    원장 키(coupon10y·10y×3·prove_growth)를 우리말로 바꿨다(작업 규칙 8).
    여유%와 원인을 함께 적는다 — 선 위 1% 에서 넘어간 것과 20% 를 뚫은 것은 다른 사건이다.
    """
    rate = bench.get("rate10y")
    bar = None if rate is None else 3.0 * (rate / 100.0)
    g = bench.get("g_used")
    g_txt = "—" if g is None else f"{g * 100:.1f}%"
    bits = [f"🔭 버핏존 — {ticker} {ZONE_KO.get(before, '—')}→{ZONE_KO.get(after, '—')}"
            f" ({days}거래일 확인)",
            cause_phrase(cause, anchor, bench)]
    mp = margin_phrase(bench.get("edge_line"), bench.get("edge_margin"))
    if mp:
        bits.append(mp)
    bits.append(f"10년 쿠폰 {_pct(bench.get('coupon10y'))} vs 국채×3 {_pct(bar)} · g {g_txt}")
    return " · ".join(bits)


def _snap(bench):
    return {k: bench.get(k) for k in SNAP_KEYS}


def is_trading_day(day):
    """KST 평일만 거래일로 센다 — 잠재지배자 명단(generate_candidates)과 같은 달력.
    주말 회차는 시세가 멈춰 있어 확인 일수를 움직이지 않는다."""
    return datetime.strptime(day, "%Y-%m-%d").weekday() < 5


def new_bstate(zones=None):
    return {"zones": dict(zones or {}), "anchor": {}, "cand": {}, "date": "", "base": None}


def step_buffett(bstate, items, today):
    """하루치 관측을 버핏존 확정 상태에 반영한다 — **순수 함수**(네트워크·파일 없음).

    bstate: {zones: 확정 존, anchor: 확정 존에 마지막으로 서 있던 관측, cand: 후보,
             date, base: 그날 출발점}
    반환: (새 상태, 확정 사건 목록, 경계 로그 목록)
    같은 날 재실행은 그날의 출발점에서 다시 계산한다 — 확인 일수는 거래일당 1회만 는다.
    """
    import copy
    import fetch_buffett                     # 원인 규칙은 측정층 한 곳에만 둔다(두 벌 금지)
    st = copy.deepcopy(bstate) if bstate else new_bstate()
    for k in ("zones", "anchor", "cand"):
        st.setdefault(k, {})
    if st.get("date") == today and isinstance(st.get("base"), dict):
        for k in ("zones", "anchor", "cand"):
            st[k] = copy.deepcopy(st["base"][k])
    else:
        st["base"] = {k: copy.deepcopy(st[k]) for k in ("zones", "anchor", "cand")}
    st["date"] = today
    zones, anchor, cand = st["zones"], st["anchor"], st["cand"]
    events, logs = [], []
    trading = is_trading_day(today)

    for x in items:
        bench = x.get("bench")
        tk = x.get("ticker")
        if not isinstance(bench, dict) or not tk:
            continue
        z = bench.get("zone_buffett")
        if not z:
            continue
        conf = zones.get(tk)
        if conf is None:                     # 첫 관측 — 기준선만
            zones[tk], anchor[tk] = z, _snap(bench)
            continue
        if z == UNTESTED:                    # 못 쟀다 = 모름. 확정·후보 모두 동결
            continue
        if conf == UNTESTED:                 # 못 쟀다 → 쟀다: 취재 사건이지 시장 사건 아님
            zones[tk], anchor[tk] = z, _snap(bench)
            cand.pop(tk, None)
            logs.append(f"{tk} 미검정→{ZONE_KO.get(z, z)} — 첫 판정, 재기준(기록 안 함)")
            continue
        if not trading:
            continue                         # 주말: 확정·후보·제자리 모두 동결
        if z == conf:
            c = cand.pop(tk, None)
            if c:
                logs.append(f"{tk} 경계 — {ZONE_KO.get(c['zone'], c['zone'])} "
                            f"{c['days']}일 만에 {ZONE_KO.get(z, z)}로 복귀(역전, 기록 안 함)")
            anchor[tk] = _snap(bench)
            continue
        cause =fetch_buffett.classify_cause(anchor.get(tk), bench)
        if cause == "scale":
            zones[tk], anchor[tk] = z, _snap(bench)
            cand.pop(tk, None)
            logs.append(f"{tk} {ZONE_KO.get(conf, conf)}→{ZONE_KO.get(z, z)} — 눈금 변경, "
                        f"재기준(기록 안 함)")
            continue
        c = cand.get(tk)
        if c and c.get("zone") == z:
            c["days"] = int(c.get("days", 0)) + 1
        else:
            c = {"zone": z, "days": 1, "since": today}
        cand[tk] = c
        mp = margin_phrase(bench.get("edge_line"), bench.get("edge_margin"))
        r0, r1 = (anchor.get(tk) or {}).get("rate10y"), bench.get("rate10y")
        if cause == "rate" and r0 is not None and r1 is not None \
                and abs(r1 - r0) * 100 < RATE_MIN_BP:
            logs.append(f"{tk} 경계 — {ZONE_KO.get(conf, conf)}→{ZONE_KO.get(z, z)} 원인 금리 "
                        f"{(r1 - r0) * 100:+.0f}bp(<{RATE_MIN_BP:.0f}bp) · {mp} — 기록 보류")
            continue
        if c["days"] < CONFIRM_DAYS:
            logs.append(f"{tk} 경계 — {ZONE_KO.get(conf, conf)}→{ZONE_KO.get(z, z)} 후보 "
                        f"{c['days']}/{CONFIRM_DAYS}거래일 · {cause_phrase(cause, anchor.get(tk), bench)}"
                        f" · {mp} — 확인 대기")
            continue
        events.append({
            "ticker": tk, "before": conf, "after": z, "kind": "buffett", "cause": cause,
            "text": buffett_phrase(tk, conf, z, bench, cause, anchor.get(tk), c["days"]),
        })
        zones[tk], anchor[tk] = z, _snap(bench)
        cand.pop(tk, None)

    # 명단에서 빠진 종목의 흔적은 정리한다 — 되살아나는 날 유령 전이를 막는다
    live = {x.get("ticker") for x in items}
    for k in ("zones", "anchor", "cand"):
        st[k] = {t: v for t, v in st[k].items() if t in live}
    # 채권 우위로 떨어진 것부터 — 상한에 걸릴 때 나쁜 소식이 먼저 남도록
    events.sort(key=lambda e: BUFFETT_ZONES.index(e["after"])
                if e["after"] in BUFFETT_ZONES else 9)
    return st, events, logs


def load_bstate(state):
    """구 상태(bzones: {티커: 존})를 확정 존으로 이관한다 — 이관 사실은 로그에 남긴다."""
    bs = state.get("bstate")
    if isinstance(bs, dict) and isinstance(bs.get("zones"), dict):
        return bs
    old = state.get("bzones") or {}
    print(f"[시차노트] 버핏존 상태 이관 — 구 기준선 {len(old)}종을 확정 존으로 승계"
          f"(비교 기준 anchor 없음 → 첫 확정 문구는 '원인 미상')")
    return new_bstate(old)


def notify_dm(events, state, today):
    """확정 전이 DM — **소장 DM 전용.** 수신처가 없으면 공개로 폴백하지 않고 로그만.

    같은 날 재실행에서 같은 사건을 두 번 보내지 않는다(서명 원장).
    """
    if not events:
        return 0
    sent = set((state.get("dm_sent") or {}).get(today, []))
    fresh = [e for e in events
             if sig(e["ticker"], e["before"], e["after"], today, "buffett-dm") not in sent]
    if not fresh:
        print(f"[시차노트] 버핏존 확정 {len(events)}건 — 오늘 이미 DM 발송 → 재발송 안 함")
        return 0
    text = "\n".join(e["text"] for e in fresh)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print(f"[시차노트] DM 수신처 미등록 — 발송 생략(공개 채널 폴백 없음): {text}",
              file=sys.stderr)
        return 0
    try:
        from send_telegram_briefing import send_telegram, esc
        send_telegram(token, chat, esc(text))
    except Exception as ex:
        print(f"[시차노트] 버핏존 DM 실패 ({ex})", file=sys.stderr)
        return 0
    for e in fresh:
        sent.add(sig(e["ticker"], e["before"], e["after"], today, "buffett-dm"))
    state.setdefault("dm_sent", {})[today] = sorted(sent)
    print(f"[시차노트] 버핏존 확정 DM {len(fresh)}건 발송 → DM")
    return len(fresh)


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
    bstate, b_events, b_logs = step_buffett(load_bstate(state), items, today)
    for line in b_logs:
        print(f"[시차노트] {line}")
    events += b_events

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

    # 버핏존 확정 상태 — bzones 는 **확정 존**의 거울로 남긴다(구 판독기 호환).
    # 회차별 존이 아니라 확정 존이다: 하루짜리 역전이 기준선을 흔들지 않는다.
    state["bstate"] = bstate
    state["bzones"] = dict(bstate["zones"])
    # DM 은 관측노트 기록(Firebase)과 독립이다 — 서비스 계정이 없다고 DM 까지 죽지 않는다
    notify_dm(b_events, state, today)

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
