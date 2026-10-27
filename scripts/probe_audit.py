#!/usr/bin/env python3
"""
SpaceX 숫자 감사관 (probe_audit) — 2중 검증: 관제실 숫자를 원천에서 다시 잰다
================================================================================
배경 (2026-10-27, 외부 검수 S5): 관제실 숫자는 두 경로로 들어온다 — 기계(XBRL companyfacts → spacex_kpi.json)와
탐사 엔진(보도자료를 읽고 state.kpi_history 에 기입). 엔진의 검증관(verify_run)은 **같은 회차의 보고서**만
대조한다. 이 감사관은 판단층을 정답으로 쓰지 않고, 매 full 회차마다 **최신 실적 8-K 의 Ex.99.1 원문**을
측정층과 다른 코드로 다시 받아 숫자가 원문에 실제로 있는지, 도출값이 산식대로인지 본다.

대조 (최신 분기):
  ① 기계값(XBRL) — 매출·영업이익·순이익·Capex·현금·유가증권 → 원문에 그 값이 있는가(백만 달러, 천 단위 쉼표)
  ② 엔진 기입값(state.kpi_history) — 부문 매출 3종·소비자 가입자·ARPU·명판 GW·발사 수 → 원문에 있는가
  ③ 교차 — 부문 매출 합 = XBRL 매출 · FCF = 영업현금흐름 − Capex · 시가총액 = 기준가 × 발행주식수
산출: data/spacex_audit.json → 관제실 상태줄 '숫자 감사 n/n'. 불일치 → 소장 DM(같은 불일치 7일 재알림).
판단층(agent-research/)에는 쓰지 않는다.
"""
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
AUDIT_PATH = DATA_DIR / "spacex_audit.json"
WATCH_PATH = DATA_DIR / "spacex_watch.json"
KPI_PATH = DATA_DIR / "spacex_kpi.json"
STATE_PATH = HERE / "agent-research" / "state" / "spacex_state.json"     # 읽기 전용
KST = timezone(timedelta(hours=9))
REALERT_DAYS = 7

MACH_ITEMS = [("revenue_usd_m", "매출"), ("operating_income_usd_m", "영업이익"), ("net_income_usd_m", "순이익"),
              ("capex_usd_m", "Capex"), ("cash_usd_m", "현금"), ("marketable_securities_usd_m", "유가증권")]
PROBE_ITEMS = [("rev_connectivity_usd_m", "Connectivity 매출", "m"), ("rev_si_usd_m", "SI 매출", "m"),
               ("rev_space_usd_m", "Space 매출", "m"), ("subscribers_m", "소비자 가입자(백만)", "dec"),
               ("arpu_usd", "ARPU", "usd"), ("ai_nameplate_gw", "명판 컴퓨트(GW)", "dec"), ("launches", "발사 수", "int")]


def now_kst():
    return datetime.now(KST)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# 순수 함수
# ────────────────────────────────────────────────────────────────

def plain(html):
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = re.sub(r"&#36;", "$", t)
    return re.sub(r"\s+", " ", t)


def number_forms(v, kind):
    """값 → 원문에 나타날 수 있는 표기들. 추정 없이 표기만 바꾼다(반올림 허용 없음 — 감사는 정확 일치)."""
    if v is None:
        return []
    if kind == "m":                        # 백만 달러 표의 칸: 14,575 / (1,054) 음수 괄호
        n = int(round(v))
        s = f"{abs(n):,}"
        return [s] if n >= 0 else [s, f"({s})"]
    if kind == "dec":
        s = f"{v:g}"
        return [s]
    if kind == "usd":
        return [f"${v:,.0f}", f"$ {v:,.0f}"]
    if kind == "int":
        return [f"{int(v)}"]
    return [str(v)]


def present(text, forms):
    """표기가 원문에 '숫자 경계'로 있는가 — 14,575 가 114,575 안에서 맞지 않게."""
    for f in forms:
        if re.search(r"(?<![\d,.])" + re.escape(f) + r"(?![\d,])", text):
            return True
    return False


def audit(kpi, state, ex99_text, latest_mc=None):
    """→ (검사 목록, 분기 라벨). 검사 = {id, label, value, verdict(ok|mismatch|unverifiable), why}"""
    quarters = ((kpi or {}).get("auto") or {}).get("quarters") or []
    q = quarters[0] if quarters else {}
    qlabel = q.get("quarter") or ""
    checks = []
    text = plain(ex99_text or "")

    def add(cid, label, value, ok, why=""):
        checks.append({"id": cid, "label": label, "value": value,
                       "verdict": "unverifiable" if ok is None else ("ok" if ok else "mismatch"), "why": why})

    for key, label in MACH_ITEMS:
        v = q.get(key)
        if v is None:
            continue
        add("xbrl:" + key, f"{qlabel} {label}(공시 자동)", v,
            present(text, number_forms(v, "m")) if text else None,
            "" if text else "원문 미수신")
    hist = {h.get("quarter"): h for h in (state or {}).get("kpi_history") or [] if isinstance(h, dict)}
    h = hist.get(qlabel) or {}
    for key, label, kind in PROBE_ITEMS:
        v = h.get(key)
        if v is None:
            continue
        add("probe:" + key, f"{qlabel} {label}(탐사 기입)", v,
            present(text, number_forms(v, kind)) if text else None,
            "" if text else "원문 미수신")
    segs = [h.get(k) for k in ("rev_connectivity_usd_m", "rev_si_usd_m", "rev_space_usd_m")]
    if all(isinstance(x, (int, float)) for x in segs) and q.get("revenue_usd_m") is not None:
        add("cross:segments", f"{qlabel} 부문 매출 합 = 매출", sum(segs), abs(sum(segs) - q["revenue_usd_m"]) <= 1,
            f"합 {sum(segs):,.0f} vs 매출 {q['revenue_usd_m']:,.0f}")
    if None not in (q.get("ocf_usd_m"), q.get("capex_usd_m"), q.get("fcf_usd_m")):
        add("cross:fcf", f"{qlabel} FCF = 영업현금흐름 − Capex", q["fcf_usd_m"],
            q["fcf_usd_m"] == q["ocf_usd_m"] - q["capex_usd_m"], "")
    mkt = (state or {}).get("market") or {}
    shares = (((kpi or {}).get("auto") or {}).get("shares_outstanding_m"))
    price = mkt.get("price_usd")
    mc = mkt.get("market_cap_usd_bn")
    if price and shares and mc:
        calc = price * shares / 1000
        add("cross:mcap", "시가총액 = 기준가 × 발행주식수", mc, abs(calc - mc) / mc <= 0.01,
            f"{price} × {shares:,}백만주 = {calc:,.0f}십억 vs 기록 {mc:,.0f}")
    if mc and latest_mc:
        gap = latest_mc / mc - 1
        add("info:mcap_fresh", "관제실 시가총액(엔진 기준가) vs 일별 시가총액", mc, None if abs(gap) > 0.10 else True,
            f"일별 {latest_mc:,.0f}십억 · 엔진 {mc:,.0f}십억(기준가 {mkt.get('price_asof', '')}) · 차이 {gap * 100:+.1f}%")
    return checks, qlabel


def summary(checks):
    n = len([c for c in checks if not c["id"].startswith("info:")])
    ok = len([c for c in checks if c["verdict"] == "ok" and not c["id"].startswith("info:")])
    bad = [c for c in checks if c["verdict"] == "mismatch"]
    unv = [c for c in checks if c["verdict"] == "unverifiable" and not c["id"].startswith("info:")]
    return n, ok, bad, unv


def latest_earnings_8k(watch):
    for e in (watch or {}).get("events") or []:
        if e.get("source") == "SEC EDGAR" and e.get("kind") == "earnings" and e.get("url"):
            return e
    return None


def spcx_mc(latest):
    """일별 순위표의 SPCX 시가총액(십억 달러)."""
    for reg in ((latest or {}).get("regions") or {}).values():
        for s in (reg or {}).get("stocks") or []:
            if s.get("ticker") == "SPCX" and s.get("mc"):
                return s.get("mc")
    return None


def run():
    import feed_client as fc
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    prev = read_json(AUDIT_PATH, {}) or {}
    watch = read_json(WATCH_PATH, {}) or {}
    kpi = read_json(KPI_PATH, {}) or {}
    state = read_json(STATE_PATH, {}) or {}
    latest = read_json(DATA_DIR / "latest.json", {}) or {}
    ev = latest_earnings_8k(watch)
    text, src = None, None
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ev and ua:
        from companion_essays import sec_exhibit_url
        src = sec_exhibit_url(ev, {"User-Agent": ua})
        t, oc, code = fc.fetch(src, "SEC 실적 원문(감사)", "spacex", headers={"User-Agent": ua}, timeout=60)
        if t:
            text = t
            fc.record("spacex", "SEC 실적 원문(감사)", "ok", code, 1)
    fc.flush()
    checks, qlabel = audit(kpi, state, text, spcx_mc(latest))
    n, ok, bad, unv = summary(checks)
    doc = {"checked_at": now.isoformat(timespec="minutes"), "quarter": qlabel, "source_url": src,
           "source_event": (ev or {}).get("accn"), "n": n, "ok": ok,
           "mismatch": [{k: c[k] for k in ("id", "label", "value", "why")} for c in bad],
           "unverifiable": [c["label"] for c in unv], "checks": checks,
           "alerted": prev.get("alerted") or {}}
    sig = "|".join(sorted(c["id"] for c in bad))
    last = (prev.get("alerted") or {}).get(sig)
    if bad and (not last or (now.date() - datetime.strptime(last, "%Y-%m-%d").date()).days >= REALERT_DAYS):
        try:
            from spacex_watch import send_dm
            send_dm("🧮 SpaceX 숫자 감사 — 원문과 다른 값 " + str(len(bad)) + "건 (" + qlabel + ")\n" +
                    "\n".join(f"· {c['label']}: {c['value']} — {c['why'] or '원문(Ex.99.1)에 이 값이 없다'}" for c in bad[:8]) +
                    (f"\n원문: {src}" if src else ""))
        except Exception:
            pass
        doc["alerted"] = {sig: today}
    elif not bad:
        doc["alerted"] = {}
    write_json(AUDIT_PATH, doc)
    print(f"[숫자 감사] {qlabel} · {ok}/{n} 일치 · 불일치 {len(bad)} · 대조 불가 {len(unv)}"
          + ("" if text else " · 원문 미수신"))
    return 1 if bad else 0


def main():
    try:
        return run()
    except Exception:
        traceback.print_exc()
        print("[숫자 감사] 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
