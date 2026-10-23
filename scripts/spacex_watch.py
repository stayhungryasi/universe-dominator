#!/usr/bin/env python3
"""
SpaceX 측정층 (spacex_watch) — 이 사이트의 근원을 매일 기계가 잰다
================================================================================
배경 (2026-10-23): 탐사선(agent-research)은 9/2 이후 51일 멈춰 있었고, 그사이
9/28 Starship 첫 궤도 비행과 10/20 3Q26 실적(분기 GAAP 첫 흑자)이 지나갔다.
판단층이 멈춰도 **측정층은 매일 돌아야** 한다 — 무엇이 일어났는지를 기계가 먼저 안다.

측정 대상 (full 회차마다):
  ① SEC EDGAR submissions (CIK 0001181412) — 새 공시: 8-K(항목번호) · 10-Q · 10-K ·
     S-1/S-3/424B · SC 13D/G. Form 4 는 건수만 센다.
  ② SEC companyfacts — 분기 매출·영업이익·순이익·Capex·현금·유가증권·부채·리스,
     발행주식수(10-Q/10-K 표지). 값마다 filed·end·개념명을 보존한다.
  ③ Launch Library 2 (2.4.0) — SpaceX 연간 발사 수, Starship 직전·다음 비행.
     무료 한도(시간당 15회)를 지키려 성공 후 6시간 캐시. 실패하면 캐시를 쓰지 않는다.
  ④ SpaceX IR 보도자료 RSS — 회사 공식 발표(구글뉴스 의존을 줄인다).
  ⑤ 월간 엔진(spacex-probe.yml) 마지막 실행 결론 — GitHub API (관제탑이 읽는다).

산출: data/spacex_watch.json (사건·발사·due·엔진) · data/spacex_kpi.json ("auto" 블록)
원장: 모든 요청 결과를 fetch_status.json kind="spacex" 로 → sentinel 이 판정한다.

트리거 감시(M2): agent-research/state/spacex_state.json 의 triggers_next_12m 을 **읽기만**
  하고(쓰지 않는다 — 판단층 불가침), 사건과 대조해 due 를 기록 → 소장 DM 1건 +
  탐사 엔진 event 모드 발사 대기열(dispatch_queue)에 올린다. 실제 발사는 커밋 **뒤**
  `--dispatch` 단계가 한다(엔진이 이번 회차 산출물을 읽게 하려고).

⚠️ agent-research/ 에는 어떤 경우에도 쓰지 않는다(CLAUDE.md '무인 탐사선 영역').
⚠️ 첫 실행(기준선 없음)은 과거 공시를 사건으로 싣되 **트리거·DM·발사는 하지 않는다** —
   기준선 확보 회차에 51일치 과거를 '새 사건'으로 울리면 양치기 감시자가 된다.
"""
import json
import os
import re
import sys
import time
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
WATCH_PATH = DATA_DIR / "spacex_watch.json"
KPI_PATH = DATA_DIR / "spacex_kpi.json"
STATE_PATH = HERE / "agent-research" / "state" / "spacex_state.json"   # 읽기 전용
KST = timezone(timedelta(hours=9))

CIK = "0001181412"
CIK_INT = "1181412"
SEC_SUB = f"https://data.sec.gov/submissions/CIK{CIK}.json"
SEC_FACTS = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
LL2 = "https://ll.thespacedevs.com/2.4.0"
LL2_SPACEX_ID = 121
LL2_CACHE_H = 6
IR_RSS = "https://ir.spacex.com/rss/pressrelease.aspx"
IR_BASE = "https://ir.spacex.com"
PROBE_WORKFLOW = "spacex-probe.yml"
KEEP_EVENTS = 80
KEEP_SEEN = 400

WATCH_FORMS = ("8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "S-1", "S-1/A",
               "S-3", "S-3ASR", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
               "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")

# 8-K 항목번호 → 화면 문구. 화면 문구는 상태(form·items)에서 생성한다(기계 키 노출 금지).
ITEM_KO = {
    "1.01": "중요 계약 체결", "1.02": "중요 계약 종료", "2.01": "자산 취득·처분 완료",
    "2.02": "실적 발표", "2.03": "직접 금융채무 발생", "2.05": "구조조정", "2.06": "손상 인식",
    "3.02": "미등록 증권 발행", "3.03": "주주 권리 변경", "5.02": "임원·이사 변동",
    "5.03": "정관 변경", "5.07": "주주총회 결과", "7.01": "공정공시(Reg FD)",
    "8.01": "기타 중요 사건", "9.01": "재무제표·첨부",
}
FORM_KO = {
    "10-Q": "분기보고서", "10-K": "연차보고서", "S-1": "증권신고서", "S-3": "일괄신고서",
    "S-3ASR": "자동 일괄신고서", "SC 13D": "5% 이상 지분 보고(경영 참여)",
    "SC 13G": "5% 이상 지분 보고(단순 투자)", "SCHEDULE 13D": "5% 이상 지분 보고(경영 참여)",
    "SCHEDULE 13G": "5% 이상 지분 보고(단순 투자)",
}

# 분기 KPI — (출력 필드, 후보 개념들(앞이 우선), 유형)
KPI_CONCEPTS = [
    ("revenue_usd_m", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], "flow"),
    ("operating_income_usd_m", ["OperatingIncomeLoss"], "flow"),
    ("net_income_usd_m", ["NetIncomeLoss"], "flow"),
    ("capex_usd_m", ["PaymentsToAcquirePropertyPlantAndEquipment"], "flow"),
    ("ocf_usd_m", ["NetCashProvidedByUsedInOperatingActivities"], "flow"),
    ("cash_usd_m", ["CashAndCashEquivalentsAtCarryingValue"], "instant"),
    ("marketable_securities_usd_m", ["MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], "instant"),
    ("debt_and_leases_usd_m", ["LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "DebtAndCapitalLeaseObligations"], "instant"),
]


def now_kst():
    return datetime.now(KST)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def sec_headers():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    return {"User-Agent": ua} if ua else None


# ────────────────────────────────────────────────────────────────
# 순수 함수 — 테스트가 직접 두드린다
# ────────────────────────────────────────────────────────────────

def filing_title(form, items):
    """공시 제목 — 상태(form·items)에서 생성한다. 원문 영문 설명을 번역하지 않는다."""
    base = form.split("/")[0]
    amend = " 정정" if form.endswith("/A") else ""
    if base == "8-K":
        its = [i.strip() for i in (items or "").split(",") if i.strip() and i.strip() != "9.01"]
        names = [ITEM_KO.get(i, f"항목 {i}") for i in its]
        return "수시공시" + amend + (" — " + " · ".join(names) if names else "")
    if base.startswith("424B"):
        return "증권 발행 설명서" + amend
    return FORM_KO.get(base, base) + amend


def event_kind(form, items):
    """사건 분류 — 트리거 대조의 근거."""
    its = {i.strip() for i in (items or "").split(",")}
    if form.startswith("8-K"):
        if "2.02" in its:
            return "earnings"
        if "1.01" in its:
            return "contract"
        if its & {"2.03", "3.02"}:
            return "financing"
        return "filing"
    if form.startswith(("424B", "S-1", "S-3")):
        return "financing"
    if form.startswith(("10-Q", "10-K")):
        return "report"
    return "filing"


def parse_filings(sub):
    """submissions JSON → (사건 목록, Form 4 목록). 최근 공시만(recent 블록)."""
    r = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms = r.get("form") or []
    out, f4 = [], []
    for i, form in enumerate(forms):
        def g(k):
            arr = r.get(k) or []
            return arr[i] if i < len(arr) else ""
        accn = g("accessionNumber")
        date = g("filingDate")
        if form in ("4", "4/A"):
            f4.append({"date": date, "accn": accn})
            continue
        if not (form in WATCH_FORMS or form.startswith("424B")):
            continue
        items = g("items") or ""
        doc = g("primaryDocument") or ""
        url = (f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/"
               f"{accn.replace('-', '')}/{doc}") if accn and doc else ""
        out.append({"date": date, "form": form, "items": items,
                    "title": filing_title(form, items), "url": url, "accn": accn,
                    "source": "SEC EDGAR", "kind": event_kind(form, items)})
    return out, f4


def _dur_days(f):
    try:
        return (datetime.strptime(f["end"], "%Y-%m-%d") - datetime.strptime(f["start"], "%Y-%m-%d")).days
    except Exception:
        return None


def _latest_by_period(facts):
    """같은 기간(start,end)을 여러 공시가 보고하면 가장 늦게 제출된 값을 쓴다."""
    best = {}
    for f in facts:
        if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        k = (f.get("start"), f["end"])
        if k not in best or f.get("filed", "") > best[k].get("filed", ""):
            best[k] = f
    return best


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _quarter_bounds(end):
    """분기 끝 날짜 → (분기 시작, 끝). 달력 분기 기준(3·6·9·12월 말)."""
    e = _d(end)
    m0 = e.month - 2
    return datetime(e.year, m0, 1).date(), e


def quarter_flow(facts, end):
    """end 로 끝나는 분기 값 → (값, 근거 fact, 산식). 세 길로 만든다(먼저 되는 것):

    ① 분기 보고값(≈3개월 기간)
    ② 누적 차분: 같은 시작의 누적(~end) − 누적(~분기 시작 전날)   예) 9M − 6M = 3Q
    ③ 앞쪽 차분: 누적(분기 시작~E) − 기간(분기 끝 다음날~E)        예) 상반기 − 2Q = 1Q
    어느 길로도 안 되면 None — 추정으로 채우지 않는다.
    """
    best = _latest_by_period(facts)
    qs, qe = _quarter_bounds(end)
    for (s, e), f in best.items():
        d = _dur_days(f)
        if e == end and d is not None and 80 <= d <= 100:
            return f["val"], f, "분기 보고값"
    prev_end = (qs - timedelta(days=1)).isoformat()
    for (s, e), f in best.items():
        if e == end and s and _d(s) < qs:
            p = best.get((s, prev_end))
            if p:
                return f["val"] - p["val"], f, f"누적 {s}~{end} − 누적 {s}~{prev_end}"
    nxt = (qe + timedelta(days=1)).isoformat()
    for (s, e), f in best.items():
        if s == qs.isoformat() and _d(e) > qe:
            p = best.get((nxt, e))
            if p:
                return f["val"] - p["val"], f, f"누적 {s}~{e} − 기간 {nxt}~{e}"
    return None, None, None


def instant_at(facts, end):
    best = None
    for f in facts:
        if f.get("end") == end and not f.get("start") and f.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            if best is None or f.get("filed", "") > best.get("filed", ""):
                best = f
    return (best["val"], best) if best else (None, None)


def recent_quarter_ends(latest_end, n=8):
    """최신 분기 끝부터 거슬러 n 개 달력 분기 끝."""
    e = _d(latest_end)
    out, y, m = [], e.year, e.month
    for _ in range(n):
        last = (datetime(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)).date()
        out.append(last.isoformat())
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return out


def quarter_label(end):
    e = _d(end)
    return f"{(e.month - 1) // 3 + 1}Q{str(e.year)[2:]}"


def build_quarters(cf, max_q=8):
    """companyfacts → 최근 max_q 개 분기 행(최신 먼저). 값은 백만 달러, 근거(개념·filed·산식) 보존.

    회사가 XBRL 로 낸 공시(10-Q·10-K)만 기계가 읽는다. 4분기는 10-K 의 연간(FY) − 같은 해 9개월 누적
    (quarter_flow ② 누적 차분)으로 계산한다. 상장 전 연간(FY)은 S-1 본문에만 있어 XBRL 이 없다 →
    빈칸 + 사유(추정 금지). 그 칸은 월간 엔진이 S-1 원문에서 기입하고 검증관이 대조한다(monthly_run.md 7).
    """
    gaap = ((cf or {}).get("facts") or {}).get("us-gaap") or {}

    def facts_of(names):
        for n in names:
            arr = ((gaap.get(n) or {}).get("units") or {}).get("USD")
            if arr:
                return n, arr
        return None, []

    _, rev = facts_of(KPI_CONCEPTS[0][1])
    ends = [f["end"] for f in rev if f.get("form", "").startswith(("10-Q", "10-K"))]
    if not ends:
        return []
    rows = []
    for end in recent_quarter_ends(max(ends), max_q):
        row = {"end": end, "quarter": quarter_label(end), "src": {}}
        filed = []
        for field, names, typ in KPI_CONCEPTS:
            concept, arr = facts_of(names)
            if typ == "flow":
                v, f, how = quarter_flow(arr, end)
            else:
                v, f = instant_at(arr, end)
                how = "기말 잔액"
            if v is None:
                row[field] = None
                row["src"][field] = {"note": ("XBRL 공시에 이 해의 연간(FY) 값이 없어 '연간 − 9개월'로 4분기를 "
                                              "계산할 수 없음(상장 전 연간은 S-1 본문에만) — 월간 엔진 S-1 기입 대상"
                                              if _d(end).month == 12 and typ == "flow"
                                              else "공시 원천에 해당 기간 값 없음") if concept else "개념 미보고"}
                continue
            row[field] = round(v / 1e6)
            # derived = 누적 공시의 차분으로 계산한 값(연간 − 9개월 · 상반기 − 2분기 등) — 화면에 '계산' 표식
            row["src"][field] = {"concept": concept, "filed": f.get("filed"), "end": f.get("end"),
                                 "form": f.get("form"), "accn": f.get("accn"), "how": how,
                                 "derived": how not in ("분기 보고값", "기말 잔액")}
            filed.append(f.get("filed", ""))
            row.setdefault("fp", f.get("fp"))
            row.setdefault("fy", f.get("fy"))
        if row.get("ocf_usd_m") is not None and row.get("capex_usd_m") is not None:
            row["fcf_usd_m"] = row["ocf_usd_m"] - row["capex_usd_m"]
            row["src"]["fcf_usd_m"] = {"how": "영업현금흐름 − 유형자산 취득(같은 분기 도출값)"}
        else:
            row["fcf_usd_m"] = None
            row["src"]["fcf_usd_m"] = {"note": "영업현금흐름 또는 Capex 분기 값 없음"}
        row["filed"] = max(filed) if filed else None
        rows.append(row)
    return rows


def parse_cover_shares(html):
    """10-Q/10-K 표지 dei:EntityCommonStockSharesOutstanding 를 클래스별로 합산.

    companyfacts 에는 dei 네임스페이스가 실리지 않는다(2026-10-23 실측) — 표지가 유일한 원천.
    → (주식수 합, 기준일, 클래스 수) 또는 (None, None, 0)
    """
    vals, ctxs = [], []
    for m in re.finditer(r'<ix:nonFraction([^>]*name="dei:EntityCommonStockSharesOutstanding"[^>]*)>'
                         r'(.*?)</ix:nonFraction>', html or "", re.S):
        attrs, raw = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        num = re.sub(r"[^\d]", "", raw)
        if not num:
            continue
        scale = re.search(r'scale="(-?\d+)"', attrs)
        v = int(num) * (10 ** int(scale.group(1)) if scale else 1)
        vals.append(v)
        c = re.search(r'contextRef="([^"]+)"', attrs)
        if c:
            ctxs.append(c.group(1))
    if not vals:
        return None, None, 0
    asof = None
    for c in ctxs:
        m = re.search(r'<xbrli:context id="' + re.escape(c) + r'">.*?<xbrli:instant>(\d{4}-\d{2}-\d{2})',
                      html, re.S)
        if m:
            asof = m.group(1)
            break
    return sum(vals), asof, len(vals)


def parse_trigger_date(s):
    """트리거 예상일 문자열 → 시작일(date) 또는 None. 형식이 제각각이라 관대하게 읽는다.

    '2026-11-초(추정)'→11/1 · '2026-12-08 ~ 2027-01-07'→12/8 · '2026-4Q'→10/1 ·
    '2026-2H ~ 2027-1H'→7/1 · '2027-1H(추정)'→1/1 · '2026-11'→11/1 · 중순→11일 · 말→21일
    """
    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    m = re.match(r"(\d{4})-([1-4])Q", s)
    if m:
        return datetime(int(m.group(1)), (int(m.group(2)) - 1) * 3 + 1, 1).date()
    m = re.match(r"(\d{4})-([12])H", s)
    if m:
        return datetime(int(m.group(1)), 1 if m.group(2) == "1" else 7, 1).date()
    m = re.match(r"(\d{4})-(\d{2})(?:-(초|중|말|중순|하순))?", s)
    if m:
        day = {"중": 11, "중순": 11, "말": 21, "하순": 21}.get(m.group(3) or "", 1)
        return datetime(int(m.group(1)), int(m.group(2)), day).date()
    return None


TRIGGER_KIND = [("earnings", r"실적"), ("starship", r"Starship|스타십"),
                ("financing", r"조달|증자|채권|전환사채"), ("contract", r"계약")]


def trigger_kind(name):
    for k, pat in TRIGGER_KIND:
        if re.search(pat, name or "", re.I):
            return k
    return None


def judge_triggers(triggers, new_events, today, already, written_on=None):
    """트리거 대조 → 새 due 목록. already = 이미 기록된 due 키 집합(같은 사유로 두 번 울지 않는다).

    ① 새 사건의 종류가 '대기' 트리거의 종류와 맞으면 → '사건 감지'
    ② 대형 계약(8-K 1.01)은 트리거가 없어도 → '대형 계약 공시'
    ③ 예상일(시작일)이 오늘 이전이고 아직 대기면 → '예상일 도래'
    """
    out = []
    for t in triggers or []:
        if (t.get("status") or "대기") != "대기":
            continue
        tid, name = t.get("id", ""), t.get("name", "")
        k = trigger_kind(name)
        for ev in new_events:
            if k and ev.get("kind") == k:
                key = f"{tid}:event:{ev.get('accn') or ev.get('id') or ev.get('date')}"
                if key not in already:
                    out.append({"key": key, "trigger_id": tid, "name": name,
                                "reason": "사건 감지", "event": ev.get("title"),
                                "event_date": ev.get("date"), "detected_at": today})
        d = parse_trigger_date(t.get("expected_date"))
        # 트리거를 쓴 날(state.run.run_date) 이미 시작된 구간은 '도래'가 아니다 — 2026-10-23:
        # 10월 회차가 '2026-4Q ~ 2027-1H' 트리거를 쓰자마자 구간 첫날(10/01)을 도래로 보고
        # 사건 모드가 발사됐다(사건 없음). 날짜 도래는 작성 이후에 처음 오는 시작일만 센다.
        if d and written_on and d.isoformat() <= written_on:
            d = None
        if d and d.isoformat() <= today:
            key = f"{tid}:date:{d.isoformat()}"
            if key not in already:
                out.append({"key": key, "trigger_id": tid, "name": name,
                            "reason": "예상일 도래", "expected": t.get("expected_date"),
                            "detected_at": today})
    for ev in new_events:
        if ev.get("kind") == "contract":
            key = f"contract:{ev.get('accn')}"
            if key not in already:
                out.append({"key": key, "trigger_id": None, "name": "대형 계약 공시",
                            "reason": "대형 계약 공시", "event": ev.get("title"),
                            "event_date": ev.get("date"), "detected_at": today})
    return out


def merge_events(old, new):
    """사건 병합 — url/accn/id 로 중복 제거, 날짜 최신순, 상한 유지."""
    seen, out = set(), []
    for e in list(new) + list(old or []):
        k = e.get("accn") or e.get("id") or e.get("url") or (e.get("date"), e.get("title"))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    out.sort(key=lambda e: e.get("date") or "", reverse=True)
    return out[:KEEP_EVENTS]


def launch_event(row, label):
    if not row:
        return None
    st = (row.get("status") or {})
    ok = st.get("abbrev") or st.get("name") or ""
    res = {"Success": "발사 성공", "Failure": "발사 실패", "Partial Failure": "부분 실패"}.get(ok, "결과 미확정")
    return {"id": row.get("id"), "date": (row.get("net") or "")[:10], "form": "발사",
            "items": "", "title": f"{row.get('name', '')} — {res}", "kind": "starship",
            "url": row.get("url") or "", "source": "Launch Library 2", "label": label}


# ────────────────────────────────────────────────────────────────
# 수집 — 전부 feed_client 경유(페이싱·재시도·원장)
# ────────────────────────────────────────────────────────────────

def _get_json(fc, url, label, headers=None):
    text, outcome, code = fc.fetch(url, label, "spacex", headers=headers)
    if text is None:
        return None, outcome, code
    try:
        return json.loads(text), "ok", code
    except Exception as e:
        fc.record("spacex", label, "http_error", code, 0)
        print(f"[SpaceX] {label}: JSON 해석 실패 ({e})", file=sys.stderr)
        return None, "http_error", code


def collect_sec(fc, watch, kpi, logs):
    hdr = sec_headers()
    if not hdr:
        logs.append("SEC_USER_AGENT 미설정 — SEC 측정 생략(로컬이면 정상, CI면 결함)")
        return [], None
    sub, oc, code = _get_json(fc, SEC_SUB, "SEC 공시목록", hdr)
    events, f4 = [], []
    if sub is not None:
        events, f4 = parse_filings(sub)
        fc.record("spacex", "SEC 공시목록", "ok" if events else "zero", code, len(events))
    time.sleep(0.2)
    cf, oc2, code2 = _get_json(fc, SEC_FACTS, "SEC 재무팩트", hdr)
    if cf is not None:
        rows = build_quarters(cf)
        fc.record("spacex", "SEC 재무팩트", "ok" if rows else "zero", code2, len(rows))
        auto = kpi.setdefault("auto", {})
        prev_rows = {r["end"]: r for r in auto.get("quarters", [])}
        for r in rows:          # 표지 주식수는 분기 행에 이어 붙인다(이전 측정 보존)
            for k in ("shares_outstanding_m", "shares_asof", "shares_src"):
                if k in prev_rows.get(r["end"], {}):
                    r[k] = prev_rows[r["end"]][k]
        auto["quarters"] = rows
        auto["source"] = "SEC companyfacts (CIK " + CIK + ")"
        auto["measured_at"] = now_kst().isoformat(timespec="minutes")
    # 발행주식수 — 최신 10-Q/10-K 표지. 같은 공시는 다시 받지 않는다(캐시)
    rep = next((e for e in events if e["form"].startswith(("10-Q", "10-K"))), None)
    auto = kpi.setdefault("auto", {})
    if rep and auto.get("shares_accn") != rep["accn"]:
        time.sleep(0.2)
        text, oc3, code3 = fc.fetch(rep["url"], "SEC 표지 주식수", "spacex", headers=hdr, timeout=60)
        if text is not None:
            n, asof, k = parse_cover_shares(text)
            fc.record("spacex", "SEC 표지 주식수", "ok" if n else "zero", code3, k)
            if n:
                auto["shares_outstanding_m"] = round(n / 1e6)
                auto["shares_asof"] = asof
                auto["shares_accn"] = rep["accn"]
                auto["shares_src"] = {"form": rep["form"], "filed": rep["date"], "url": rep["url"],
                                      "classes": k}
    if auto.get("shares_outstanding_m") and auto.get("quarters"):
        top = auto["quarters"][0]
        top["shares_outstanding_m"] = auto["shares_outstanding_m"]
        top["shares_asof"] = auto.get("shares_asof")
        top["shares_src"] = auto.get("shares_src")
    # Form 4 는 건수만
    cut = (now_kst() - timedelta(days=30)).strftime("%Y-%m-%d")
    watch["form4_30d"] = sum(1 for x in f4 if x["date"] >= cut)
    return events, sub


def collect_launches(fc, watch, logs):
    """LL2 — 성공 후 6시간 캐시. 직전이 실패였으면 캐시를 쓰지 않고 다시 잰다."""
    prev = watch.get("launches") or {}
    try:
        age_h = (now_kst() - datetime.fromisoformat(prev.get("fetched_at", ""))).total_seconds() / 3600
    except Exception:
        age_h = None
    if age_h is not None and age_h < LL2_CACHE_H and prev.get("ok"):
        fc.record("spacex", "발사 기록(LL2)", "ok", 200, prev.get("year_count") or 0)
        logs.append(f"LL2 캐시 사용({age_h:.1f}h 전 성공 측정) — 무료 한도 보호")
        return []
    now = datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    y0 = f"{now.year}-01-01T00:00:00Z"
    hdr = {"User-Agent": "UNIVERTRIX spacex-watch (univertrix.com)"}
    q1 = f"{LL2}/launches/mini/?lsp__id={LL2_SPACEX_ID}&net__gte={y0}&net__lte={iso}&limit=1"
    q2 = (f"{LL2}/launches/mini/?lsp__id={LL2_SPACEX_ID}&search=Starship&ordering=-net"
          f"&net__lte={iso}&limit=2")
    q3 = (f"{LL2}/launches/mini/?lsp__id={LL2_SPACEX_ID}&search=Starship&ordering=net"
          f"&net__gte={iso}&limit=1")
    a, oc1, c1 = _get_json(fc, q1, "발사 기록(LL2)", hdr)
    b, oc2, _ = _get_json(fc, q2, "발사 기록(LL2)", hdr) if a is not None else (None, "skip", None)
    c, oc3, _ = _get_json(fc, q3, "발사 기록(LL2)", hdr) if b is not None else (None, "skip", None)
    if a is None or b is None or c is None:
        watch["launches"] = dict(prev, ok=False)
        return []
    last = (b.get("results") or [None])[0]
    nxt = (c.get("results") or [None])[0]
    fc.record("spacex", "발사 기록(LL2)", "ok", c1, int(a.get("count") or 0))
    watch["launches"] = {
        "ok": True, "fetched_at": now_kst().isoformat(timespec="minutes"),
        "year": now.year, "year_count": int(a.get("count") or 0),
        "starship_last": launch_event(last, "직전 Starship"),
        "starship_next": ({"name": nxt.get("name"), "date": (nxt.get("net") or "")[:10],
                           "status": (nxt.get("status") or {}).get("name"),
                           "precision": (nxt.get("net_precision") or {}).get("name")}
                          if nxt else None),
        "source": "Launch Library 2 (2.4.0)",
    }
    evs = [e for e in (launch_event(r, "Starship") for r in (b.get("results") or [])) if e]
    return evs


def collect_ir(fc, logs):
    try:
        from fetch_signals import parse_rss
    except Exception as e:
        logs.append(f"RSS 파서 로드 실패 — IR 생략 ({e})")
        return []
    text, oc, code = fc.fetch(IR_RSS, "IR 보도자료", "spacex",
                              headers={"User-Agent": "UNIVERTRIX spacex-watch (univertrix.com)"})
    if text is None:
        return []
    rows = parse_rss(text, "SpaceX IR")
    fc.record("spacex", "IR 보도자료", "ok" if rows else "zero", code, len(rows))
    from email.utils import parsedate_to_datetime
    out = []
    for r in rows[:15]:
        url = r["url"]
        if url.startswith("/"):
            url = IR_BASE + url
        try:
            d = parsedate_to_datetime(r.get("pub", "")).strftime("%Y-%m-%d")
        except Exception:
            d = ""
        # IR 원제목은 회사 발표 원문이다 — 화면에는 '회사 보도자료' 라벨과 함께 원제목을 링크로 싣는다
        out.append({"id": "ir:" + url, "date": d, "form": "보도자료", "items": "",
                    "title": r["title"], "kind": "ir",     # 실적 감지는 SEC 8-K 2.02 단독 관할
                    "url": url, "source": "SpaceX IR"})
    return out


def collect_engine(fc, watch, logs):
    """월간 엔진 마지막 실행 결론 — GitHub API. 관제탑(E6)과 화면(D5)이 읽는다."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        logs.append("GITHUB_REPOSITORY 없음 — 엔진 상태 확인 생략(로컬)")
        return
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "univertrix-spacex-watch"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{PROBE_WORKFLOW}/runs?per_page=5"
    doc, oc, code = _get_json(fc, url, "월간 엔진 실행기록", hdr)
    if doc is None:
        return
    runs = doc.get("workflow_runs") or []
    fc.record("spacex", "월간 엔진 실행기록", "ok", code, len(runs))
    done = [r for r in runs if r.get("status") == "completed"]
    last = done[0] if done else None
    watch["engine"] = {
        "checked_at": now_kst().isoformat(timespec="minutes"),
        "last_run": ({"conclusion": last.get("conclusion"), "created_at": last.get("created_at"),
                      "event": last.get("event"), "url": last.get("html_url"),
                      "run_id": last.get("id")} if last else None),
        "in_progress": any(r.get("status") != "completed" for r in runs),
    }


# ────────────────────────────────────────────────────────────────
# 알림 · 발사
# ────────────────────────────────────────────────────────────────

def send_dm(text):
    """소장 DM 전용 — 미등록이면 로그로만(공개 채널 폴백 없음)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("[SpaceX] DM 수신처 미등록 — 발송 생략(공개 폴백 없음). 내용:\n" + text)
        return False
    try:
        from send_telegram_briefing import send_telegram, esc
        send_telegram(token, chat, esc(text))
        return True
    except Exception as e:
        print(f"[SpaceX] DM 발송 실패: {e}", file=sys.stderr)
        return False


def due_message(dues):
    lines = ["🛰 SpaceX 트리거 감시 — 판단층 점검 대상"]
    for d in dues[:6]:
        what = d.get("event") or d.get("expected") or ""
        lines.append(f"· {d.get('trigger_id') or '—'} {d['name']}: {d['reason']}"
                     + (f" ({what})" if what else ""))
    lines.append("탐사 엔진 사건 모드 발사 대기열에 올렸습니다.")
    return "\n".join(lines)


def dispatch_queue():
    """커밋 뒤 단계 — 대기열의 사건 모드 발사. GITHUB_TOKEN 의 repository_dispatch 는
    새 워크플로 실행을 만든다(GitHub 문서: GITHUB_TOKEN 예외 = workflow_dispatch·
    repository_dispatch). 실패는 실패로 보고한다(exit 1 + DM)."""
    watch = read_json(WATCH_PATH, {})
    rid = os.environ.get("GITHUB_RUN_ID", "")
    # 이번 실행이 올린 항목만 쏜다 — 하루 3회차가 같은 대기열을 거듭 쏘지 않게
    q = [x for x in (watch.get("dispatch_queue") or []) if x.get("state") == "queued"
         and x.get("run_id", "") == rid]
    if not q:
        print("[SpaceX] 발사 대기열 비어 있음 — 침묵")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not tok:
        print("[SpaceX] GITHUB_REPOSITORY/GITHUB_TOKEN 없음 — 발사 불가", file=sys.stderr)
        return 1
    import requests
    reason = " · ".join(sorted({f"{x.get('trigger_id') or ''} {x.get('name')}: {x.get('reason')}".strip()
                                for x in q}))[:900]
    r = requests.post(f"https://api.github.com/repos/{repo}/dispatches",
                      headers={"Authorization": "Bearer " + tok,
                               "Accept": "application/vnd.github+json"},
                      json={"event_type": "spacex-event",
                            "client_payload": {"reason": reason,
                                               "keys": [x.get("key") for x in q][:20]}},
                      timeout=30)
    if r.status_code in (200, 204):
        print(f"[SpaceX] 탐사 엔진 사건 모드 발사 완료 ({len(q)}건): {reason}")
        return 0
    msg = f"🚨 SpaceX 탐사 엔진 사건 모드 발사 실패 — HTTP {r.status_code}: {r.text[:200]}"
    print(msg, file=sys.stderr)
    send_dm(msg)
    return 1


# ────────────────────────────────────────────────────────────────

def run():
    import feed_client as fc
    today = now_kst().strftime("%Y-%m-%d")
    watch = read_json(WATCH_PATH, {}) or {}
    kpi = read_json(KPI_PATH, {}) or {}
    logs = []
    baseline = bool(watch.get("seen"))
    seen = set(watch.get("seen") or [])

    sec_events, _ = collect_sec(fc, watch, kpi, logs)
    launch_events = collect_launches(fc, watch, logs)
    ir_events = collect_ir(fc, logs)
    collect_engine(fc, watch, logs)
    fc.flush()

    fresh = []
    for e in sec_events + launch_events + ir_events:
        k = e.get("accn") or e.get("id")
        if k and k not in seen:
            fresh.append(e)
            seen.add(k)
    watch["events"] = merge_events(watch.get("events"), sec_events + launch_events + ir_events)
    watch["seen"] = sorted(seen)[-KEEP_SEEN:]
    watch["cik"] = CIK
    watch["generated_at"] = now_kst().isoformat(timespec="minutes")

    # 트리거 감시 — 판단층은 읽기만
    st = read_json(STATE_PATH, {}) or {}
    triggers = st.get("triggers_next_12m") or []
    already = {d.get("key") for d in (watch.get("due") or [])}
    queue = [x for x in (watch.get("dispatch_queue") or []) if x.get("state") == "queued"
             and x.get("queued_at", "")[:10] == today]       # 지난 회차 대기열은 발사 단계가 처리했다
    if not baseline:
        logs.append(f"기준선 확보 회차 — 과거 사건 {len(fresh)}건 적재, 트리거·DM·발사 생략")
        # 이미 지난 예상일도 기준선으로 덮는다(첫 회차에 몰아 울리지 않게)
        new_due = judge_triggers(triggers, [], today, already)
        for d in new_due:
            d["baseline"] = True
        watch["due"] = (watch.get("due") or []) + new_due
    else:
        new_due = judge_triggers(triggers, fresh, today, already,
                                 written_on=((st.get("run") or {}).get("run_date") or "")[:10] or None)
        if new_due:
            watch["due"] = ((watch.get("due") or []) + new_due)[-60:]
            send_dm(due_message(new_due))
            queue.append({"key": "q:" + today + ":" + str(len(new_due)),
                          "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                          "queued_at": now_kst().isoformat(timespec="minutes"),
                          "state": "queued", "trigger_id": ",".join(sorted({str(d.get("trigger_id") or "") for d in new_due})),
                          "name": " · ".join(d["name"] for d in new_due)[:200],
                          "reason": "트리거 " + str(len(new_due)) + "건"})
    watch["dispatch_queue"] = queue
    write_json(WATCH_PATH, watch)
    write_json(KPI_PATH, kpi)

    top = ((kpi.get("auto") or {}).get("quarters") or [{}])[0]
    print(f"[SpaceX] 공시 {len(sec_events)} · 발사 {len(launch_events)} · IR {len(ir_events)} · "
          f"새 사건 {len(fresh)} · due {len(new_due)} · 최신 분기 {top.get('end')} 매출 "
          f"{top.get('revenue_usd_m')}M")
    for n in logs:
        print(f"[SpaceX] 참고: {n}", file=sys.stderr)
    return 0


def main():
    if "--dispatch" in sys.argv:
        return dispatch_queue()
    try:
        return run()
    except Exception:
        traceback.print_exc()
        print("[SpaceX] 측정 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
