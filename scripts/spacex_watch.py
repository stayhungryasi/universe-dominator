#!/usr/bin/env python3
"""
SpaceX 측정층 (spacex_watch) — 이 사이트의 근원을 매일 기계가 잰다
================================================================================
배경 (2026-10-23): 탐사선(agent-research)은 9/2 이후 51일 멈춰 있었고, 그사이
9/28 Starship 첫 궤도 비행과 10/20 3Q26 실적(분기 GAAP 첫 흑자)이 지나갔다.
판단층이 멈춰도 **측정층은 매일 돌아야** 한다 — 무엇이 일어났는지를 기계가 먼저 안다.

측정 대상 (full 회차마다):
  ① SEC EDGAR submissions (CIK 0001181412) — **모든 양식**(2026-10-27 개정). 등급:
     긴급(8-K 1.02·1.03·1.05·2.04·2.05·2.06·3.01·4.01·4.02·5.01 · NT 10-Q/K) /
     중요(그 밖의 8-K 항목 · 10-Q·10-K · 424B·FWP·S-1/S-3/S-4·425 · 위임장 · 13D) /
     기록(9.01·7.01 단독 8-K · S-8 · CORRESP/UPLOAD · 13G 등). 긴급·중요 → DM + 엔진 사건 모드,
     기록 → DM 한 줄. 내부자 양식(3·4·5·144)은 원문 XML 을 읽어 누가·얼마·10b5-1 을 싣는다.
     ⚠️ 2026-10-26: $400억 GPU 금융리스 8-K 가 Item 8.01 이라 'filing' 으로 분류돼 DM·발사 없이
        기록만 됐다(외부 검수 G1) — 항목 네 개만 보던 분류표가 사각지대였다.
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
               "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")   # 옛 감시 목록(참고용)

# 공시 등급(2026-10-27) — 감시는 '모든 양식'에서 출발하고, 무엇을 얼마나 크게 울릴지만 표로 정한다.
# 빈 칸 = 결함: 표에 없는 양식도 최소 '기록'(DM 한 줄)이다. 조용히 버리는 양식은 없다.
URGENT_ITEMS = {"1.02", "1.03", "1.05", "2.04", "2.05", "2.06", "3.01", "4.01", "4.02", "5.01"}
MAJOR_FORMS = {"10-Q", "10-K", "S-1", "S-3", "S-3ASR", "S-4", "425", "FWP", "DEF 14A", "PRE 14A",
               "DEFM14A", "DEFA14A", "SC 13D", "SCHEDULE 13D", "SC TO-T", "SC TO-I", "SC 14D9",
               "15-12B", "25-NSE", "8-K12B"}
INSIDER_FORMS = {"3", "4", "5", "144"}          # 원문 XML 을 읽어 상세를 싣는다(insider_event)
GRADE_KO = {"urgent": "긴급", "major": "중요", "record": "기록"}
INSIDER_MAJOR_USD = 100e6       # 매도 합계 $1억 이상 → 중요
INSIDER_MAJOR_PCT = 0.05        # 보유의 5% 이상 처분 → 중요
INSIDER_FETCH_CAP = 8           # 회당 내부자 원문 상한(SEC 예의)
TEXT_FETCH_CAP = 3              # 회당 8-K 본문 상한

# 8-K 항목번호 → 화면 문구. 화면 문구는 상태(form·items)에서 생성한다(기계 키 노출 금지).
ITEM_KO = {
    "1.01": "중요 계약 체결", "1.02": "중요 계약 종료", "1.03": "파산·법정관리", "1.05": "중요 사이버보안 사고",
    "2.01": "자산 취득·처분 완료", "2.02": "실적 발표", "2.03": "직접 금융채무 발생",
    "2.04": "채무 가속·의무 촉발", "2.05": "구조조정", "2.06": "손상 인식",
    "3.01": "상장 요건 미달 통지", "3.02": "미등록 증권 발행", "3.03": "주주 권리 변경",
    "4.01": "감사인 변경", "4.02": "재무제표 신뢰 철회", "5.01": "지배권 변경",
    "5.02": "임원·이사 변동", "5.03": "정관 변경", "5.07": "주주총회 결과", "5.08": "주주 추천 기한",
    "7.01": "공정공시(Reg FD)", "8.01": "기타 중요 사건", "9.01": "재무제표·첨부",
}
FORM_KO = {
    "10-Q": "분기보고서", "10-K": "연차보고서", "S-1": "증권신고서", "S-3": "일괄신고서",
    "S-3ASR": "자동 일괄신고서", "SC 13D": "5% 이상 지분 보고(경영 참여)",
    "SC 13G": "5% 이상 지분 보고(단순 투자)", "SCHEDULE 13D": "5% 이상 지분 보고(경영 참여)",
    "SCHEDULE 13G": "5% 이상 지분 보고(단순 투자)",
    "S-8": "임직원 주식보상 등록", "S-4": "합병 증권신고서", "425": "합병 관련 자료",
    "FWP": "자유 서식 투자설명서", "DEF 14A": "주주총회 위임장", "PRE 14A": "주주총회 위임장(예비)",
    "DEFA14A": "주주총회 추가 자료", "DEFM14A": "합병 주주총회 위임장",
    "NT 10-Q": "분기보고서 지연 통지", "NT 10-K": "연차보고서 지연 통지",
    "CORRESP": "SEC 서신(회사 답변)", "UPLOAD": "SEC 서신(검토 의견)", "8-A12B": "증권 상장 등록",
    "3": "내부자 최초 보유 신고", "4": "내부자 거래 보고", "5": "내부자 연간 거래 보고",
    "144": "매도 예정 신고", "SC TO-T": "공개매수", "SC TO-I": "자기 공개매수", "SC 14D9": "공개매수 의견",
    "15-12B": "등록 말소", "25-NSE": "상장 폐지 통지", "D": "사모 발행 신고",
}

# 8.01 등 항목만으로 성격을 모르는 8-K — 본문 핵심어로 사건 종류를 다시 정한다(트리거 대조용)
TEXT_KIND = [("financing", r"finance lease|credit agreement|indenture|senior notes|notes due|term loan|"
                           r"commitment letter|debt financing|bond offering|revolving credit|loan agreement"),
             ("contract", r"merger agreement|business combination|definitive agreement|acquisition agreement|"
                          r"tender offer")]

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


def form_base(form):
    return (form or "").split("/")[0].strip()


def filing_grade(form, items):
    """공시 등급 — urgent(긴급) · major(중요) · record(기록). 표에 없는 양식도 최소 '기록'."""
    base = form_base(form)
    if base.startswith("NT "):
        return "urgent"
    if base in ("8-K", "8-K12B"):
        its = {i.strip() for i in (items or "").split(",") if i.strip()}
        its.discard("9.01")
        if its & URGENT_ITEMS:
            return "urgent"
        if not its or its <= {"7.01"}:
            return "record"
        return "major"
    if base.startswith("424B") or base in MAJOR_FORMS:
        return "major"
    return "record"


def refine_kind(kind, text):
    """8.01 처럼 항목만으로 성격을 모르는 8-K — 본문 핵심어로 종류를 다시 정한다.
    이미 실적·계약·조달로 분류된 사건은 그대로 둔다(항목번호가 1순위)."""
    if kind not in ("filing",) or not text:
        return kind
    low = re.sub(r"<[^>]+>", " ", text).lower()
    for k, pat in TEXT_KIND:
        if re.search(pat, low):
            return k
    return kind


def parse_filings(sub):
    """submissions JSON → (사건 목록, 내부자 양식 목록). 최근 공시만(recent 블록).

    2026-10-27: 모든 양식을 싣는다(등급 grade 포함). 내부자 양식(3·4·5·144)은 상세를 따로 읽는다."""
    r = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms = r.get("form") or []
    out, ins = [], []
    for i, form in enumerate(forms):
        def g(k):
            arr = r.get(k) or []
            return arr[i] if i < len(arr) else ""
        accn = g("accessionNumber")
        date = g("filingDate")
        doc = g("primaryDocument") or ""
        if form_base(form) in INSIDER_FORMS:
            ins.append({"date": date, "accn": accn, "form": form, "doc": doc})
            continue
        items = g("items") or ""
        url = (f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/"
               f"{accn.replace('-', '')}/{doc}") if accn and doc else ""
        out.append({"date": date, "form": form, "items": items,
                    "title": filing_title(form, items), "url": url, "accn": accn,
                    "source": "SEC EDGAR", "kind": event_kind(form, items),
                    "grade": filing_grade(form, items)})
    return out, ins


# ── 내부자 양식(3·4·5·144) — 원문 XML 에서 누가·얼마·10b5-1 ──────────────

def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def insider_xml_url(accn, doc):
    """submissions 의 primaryDocument 는 xsl 렌더 경로(xslF345X05/…xml) — 원문 XML 은 접수 폴더 바로 아래."""
    base = (doc or "").rsplit("/", 1)[-1]
    if not accn or not base:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/{accn.replace('-', '')}/{base}"


def parse_form4(xml_text):
    """Form 3/4/5 원문 XML → 요약. 실패하면 None(추정으로 채우지 않는다)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring((xml_text or "").strip())
    except Exception:
        return None

    def tx(el, path):
        n = el.find(path) if el is not None else None
        return (n.text or "").strip() if n is not None and n.text else ""

    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if tx(rel, "isDirector") in ("1", "true"):
            roles.append("이사")
        if tx(rel, "isOfficer") in ("1", "true"):
            roles.append(tx(rel, "officerTitle") or "임원")
        if tx(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% 주주")
        owners.append({"name": tx(ro, "reportingOwnerId/rptOwnerName"), "roles": roles})
    txs = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        txs.append({"code": tx(t, "transactionCoding/transactionCode"),
                    "date": tx(t, "transactionDate/value"),
                    "shares": _num(tx(t, "transactionAmounts/transactionShares/value")) or 0.0,
                    "price": _num(tx(t, "transactionAmounts/transactionPricePerShare/value")),
                    "ad": tx(t, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                    "after": _num(tx(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))})
    sold = [t for t in txs if t["ad"] == "D" and t["code"] == "S"]
    disposed = sum(t["shares"] for t in txs if t["ad"] == "D")
    after = next((t["after"] for t in reversed(txs) if t["after"] is not None), None)
    plan = tx(root, "aff10b5One") in ("1", "true") or "10b5-1" in (xml_text or "")
    return {"owners": owners,
            "sold_shares": sum(t["shares"] for t in sold),
            "sold_usd": sum(t["shares"] * (t["price"] or 0) for t in sold),
            "disposed_shares": disposed,
            "acquired_shares": sum(t["shares"] for t in txs if t["ad"] == "A"),
            "codes": sorted({t["code"] for t in txs if t["code"]}),
            "after": after,
            "pct_disposed": (disposed / (after + disposed)) if (after is not None and disposed and after + disposed > 0) else None,
            "plan_10b5_1": plan, "n_tx": len(txs)}


def parse_form144(xml_text):
    """Form 144(매도 예정) 원문 XML → 이름·예정 주식수·시가 합계. 태그 이름만 보고 관대하게 읽는다."""
    def pick(tag):
        m = re.search("<(?:[A-Za-z0-9_]+:)?" + tag + ">([^<]+)<", xml_text or "")
        return m.group(1).strip() if m else ""
    name = pick("nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold")
    if not name:
        return None
    return {"owners": [{"name": name, "roles": []}],
            "planned_shares": _num(pick("noOfUnitsSold")),
            "planned_usd": _num(pick("aggregateMarketValue")),
            "plan_10b5_1": "10b5" in (xml_text or "")}


def insider_grade(form, info):
    """내부자 거래 등급 — 머스크 거래 · 매도 $1억 이상 · 보유 5% 이상 처분 → 중요, 나머지 기록."""
    if not info:
        return "record"
    names = " ".join(o.get("name", "") for o in info.get("owners") or [])
    if "musk" in names.lower():
        return "major"
    usd = info.get("sold_usd") or info.get("planned_usd") or 0
    if usd >= INSIDER_MAJOR_USD:
        return "major"
    if (info.get("pct_disposed") or 0) >= INSIDER_MAJOR_PCT:
        return "major"
    return "record"


def insider_title(form, info):
    """화면·DM 문구 — 상태에서 생성(원문 영문을 옮기지 않는다)."""
    base = form_base(form)
    if not info:
        return FORM_KO.get(base, base) + " — 원문 해석 실패(링크 확인)"
    who = ", ".join((o.get("name") or "?") + ("(" + "·".join(o["roles"]) + ")" if o.get("roles") else "")
                    for o in info.get("owners") or []) or "?"
    plan = " · 10b5-1 계획" if info.get("plan_10b5_1") else ""
    if base == "144":
        sh = info.get("planned_shares")
        usd = info.get("planned_usd")
        return (f"매도 예정 신고 — {who}"
                + (f" {sh:,.0f}주" if sh else "") + (f" · 시가 약 ${usd / 1e6:,.1f}M" if usd else "") + plan)
    parts = []
    if info.get("sold_shares"):
        parts.append(f"매도 {info['sold_shares']:,.0f}주 · 약 ${info['sold_usd'] / 1e6:,.1f}M")
    other = (info.get("disposed_shares") or 0) - (info.get("sold_shares") or 0)
    if other > 0:
        parts.append(f"매도 외 처분 {other:,.0f}주(코드 {'·'.join(c for c in info.get('codes') or [] if c != 'S')})")
    if info.get("acquired_shares"):
        parts.append(f"취득 {info['acquired_shares']:,.0f}주")
    if info.get("pct_disposed"):
        parts.append(f"보유의 {info['pct_disposed'] * 100:.1f}% 처분")
    if info.get("after") is not None:
        parts.append(f"거래 후 보유 {info['after']:,.0f}주")
    return f"{FORM_KO.get(base, '내부자 거래')} — {who}: " + (" · ".join(parts) or "거래 없음") + plan


def insider_event(row, xml_text):
    """내부자 양식 한 건 → 사건(kind insider, 등급 포함)."""
    base = form_base(row.get("form"))
    info = parse_form144(xml_text) if base == "144" else parse_form4(xml_text)
    url = (f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/"
           f"{row['accn'].replace('-', '')}/{row.get('doc') or ''}") if row.get("accn") else ""
    return {"date": row.get("date"), "form": row.get("form"), "items": "", "accn": row.get("accn"),
            "title": insider_title(row.get("form"), info), "url": url, "source": "SEC EDGAR",
            "kind": "insider", "grade": insider_grade(row.get("form"), info), "insider": info}


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
            # FCF 는 언제나 계산값이다(공시 원문에 분기 FCF 줄이 없다) — 화면 '계산' 표식(외부 검수 G6)
            row["src"]["fcf_usd_m"] = {"how": "영업현금흐름 − 유형자산 취득(같은 분기 도출값)", "derived": True}
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
    ④ 그 밖의 긴급·중요 사건(grade) → '긴급 공시' / '중요 사건' (①에 걸린 사건은 두 번 울지 않는다)
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
    hit = {d["key"].split(":event:", 1)[1] for d in out if ":event:" in d["key"]}
    for ev in new_events:
        ek = ev.get("accn") or ev.get("id") or ev.get("date")
        if ev.get("kind") == "contract":
            key = f"contract:{ev.get('accn')}"
            if key not in already:
                out.append({"key": key, "trigger_id": None, "name": "대형 계약 공시",
                            "reason": "대형 계약 공시", "event": ev.get("title"),
                            "event_date": ev.get("date"), "detected_at": today,
                            "grade": ev.get("grade") or "major"})
            continue
        # ④ (2026-10-27) 트리거와 무관하게 긴급·중요 사건은 엔진을 깨운다 — 8-K 항목 네 개만 보던
        #    분류표가 $400억 금융리스 8-K(8.01)를 '기록'으로만 남긴 사각지대(외부 검수 G1)
        g = ev.get("grade")
        if g in ("urgent", "major") and ek not in hit:
            key = f"{g}:{ek}"
            if key not in already:
                out.append({"key": key, "trigger_id": None,
                            "name": ev.get("title") or "새 사건",
                            "reason": "긴급 공시" if g == "urgent" else "중요 사건",
                            "event": ev.get("title"), "event_date": ev.get("date"),
                            "url": ev.get("url"), "detected_at": today, "grade": g})
    for d in out:
        if ":event:" in d["key"]:
            d.setdefault("grade", "major")
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
    failed = ok in ("Failure", "Partial Failure")
    starship = "starship" in (row.get("name") or "").lower()
    return {"id": row.get("id"), "date": (row.get("net") or "")[:10], "form": "발사",
            "items": "", "title": f"{row.get('name', '')} — {res}",
            "kind": "starship" if starship else ("launch_fail" if failed else "launch"),
            # 실패·부분 실패는 기체 불문 긴급(Falcon 실패 = 비행 중단·면허 조사로 이어진다), Starship 비행은 중요
            "grade": "urgent" if failed else ("major" if starship else "record"),
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
    # 내부자 양식 건수(30일) — 상세는 run() 이 원문 XML 을 읽어 사건으로 싣는다
    cut = (now_kst() - timedelta(days=30)).strftime("%Y-%m-%d")
    watch["form4_30d"] = sum(1 for x in f4 if x["date"] >= cut and form_base(x.get("form", "4")) == "4")
    return events, sub


def collect_sec_light(fc, logs):
    """매시간 감시용 — 공시 목록(submissions)만. 재무팩트·표지 주식수는 하루 3회 full 이 맡는다."""
    hdr = sec_headers()
    if not hdr:
        logs.append("SEC_USER_AGENT 미설정 — SEC 측정 생략")
        return [], None
    sub, oc, code = _get_json(fc, SEC_SUB, "SEC 공시목록", hdr)
    if sub is None:
        return [], None
    return parse_filings(sub)[0], sub


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
    # 2026-10-27(G3): Starship 외 기체의 실패·부분 실패도 사건으로 — 최근 7일, LL2 상태 4(실패)·7(부분 실패)
    w0 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q4 = (f"{LL2}/launches/mini/?lsp__id={LL2_SPACEX_ID}&status__ids=4,7&net__gte={w0}"
          f"&net__lte={iso}&limit=5")
    d, oc4, _ = _get_json(fc, q4, "발사 기록(LL2)", hdr)
    if d is None:
        logs.append("LL2 실패 발사 조회 실패 — 다음 회차에 다시(캐시 미사용)")
        watch["launches"]["ok"] = False
    else:
        known = {e["id"] for e in evs}
        evs += [e for e in (launch_event(r, "실패") for r in (d.get("results") or []))
                if e and e["id"] not in known]
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
    """(옛 문안 — 호환용) 트리거 due 만 담은 DM."""
    lines = ["🛰 SpaceX 트리거 감시 — 판단층 점검 대상"]
    for d in dues[:6]:
        what = d.get("event") or d.get("expected") or ""
        lines.append(f"· {d.get('trigger_id') or '—'} {d['name']}: {d['reason']}"
                     + (f" ({what})" if what else ""))
    lines.append("탐사 엔진 사건 모드 발사 대기열에 올렸습니다.")
    return "\n".join(lines)


def watch_message(dues, records, fired, migration_note=None):
    """감시 DM 한 통 — 긴급·중요(엔진을 깨우는 것) 먼저, 기록(한 줄씩) 뒤. 문구는 상태에서 생성."""
    n_u = sum(1 for d in dues if d.get("grade") == "urgent")
    n_m = len(dues) - n_u
    head = "🚨 SpaceX 긴급 사건" if n_u else "🛰 SpaceX 감시"
    lines = [f"{head} — 엔진 깨움 {len(dues)}건(긴급 {n_u} · 중요 {n_m}) · 기록 {len(records)}건"]
    for d in sorted(dues, key=lambda d: d.get("grade") != "urgent")[:8]:
        tag = "🚨 [긴급]" if d.get("grade") == "urgent" else "· [중요]"
        what = d.get("event") or d.get("expected") or ""
        tid = d.get("trigger_id")
        lines.append(f"{tag} {(tid + ' ') if tid else ''}{d['name']}: {d['reason']}"
                     + (f" ({what})" if what and what != d["name"] else "")
                     + (f" {d['event_date']}" if d.get("event_date") else ""))
        if d.get("url"):
            lines.append("   " + d["url"])
    for e in records[:12]:
        lines.append(f"· [기록] {e.get('title') or e.get('form')} · {e.get('date') or ''}"
                     + (f"\n   {e['url']}" if e.get("url") else ""))
    if len(records) > 12:
        lines.append(f"· … 기록 {len(records) - 12}건 더")
    if migration_note:
        lines.append(migration_note)
    if dues:
        lines.append("탐사 엔진 사건 모드를 즉시 발사했습니다." if fired else
                     "탐사 엔진 사건 모드 발사 대기열에 올렸습니다(커밋 뒤 발사).")
    return "\n".join(lines)


def heartbeat_message(watch, hstate, st, now, status=None):
    """3중 — 하루 한 번 소장 DM. 아무 일이 없어도 온다: 이 메시지가 안 오면 감시가 멈춘 것이다."""
    today = now.date()
    since = (today - timedelta(days=1)).isoformat()
    evs = {}
    for e in list((hstate or {}).get("events") or []) + list((watch or {}).get("events") or []):
        k = e.get("accn") or e.get("id") or e.get("url")
        if k and (e.get("date") or "") >= since:
            evs[k] = e
    cnt = {"urgent": 0, "major": 0, "record": 0}
    by = {"sec": 0, "insider": 0, "launch": 0, "ir": 0}
    for e in evs.values():
        cnt[e.get("grade") or "record"] = cnt.get(e.get("grade") or "record", 0) + 1
        k = e.get("kind")
        by["insider" if k == "insider" else "launch" if k in ("starship", "launch", "launch_fail")
           else "ir" if k == "ir" else "sec"] += 1
    wd = "월화수목금토일"[today.weekday()]
    lines = [f"🫀 SpaceX 관제 일일 점검 — {today.month}/{today.day}({wd})",
             f"· 최근 24시간 새 사건 {len(evs)}건: 공시 {by['sec']} · 내부자 {by['insider']} · 발사 {by['launch']} · 보도자료 {by['ir']}"
             f" (긴급 {cnt['urgent']} · 중요 {cnt['major']} · 기록 {cnt['record']})"]
    eng = ((watch or {}).get("engine") or {}).get("last_run") or {}
    if eng:
        lines.append(f"· 탐사 엔진 마지막 실행: {(eng.get('created_at') or '')[:10]} "
                     f"{'사건 모드' if eng.get('event') == 'repository_dispatch' else '정기·수동'} · {eng.get('conclusion') or '?'}")
    upc = []
    for t in (st or {}).get("triggers_next_12m") or []:
        if (t.get("status") or "대기") != "대기":
            continue
        d = parse_trigger_date(t.get("expected_date"))
        if d and d >= today:
            upc.append((d, t.get("id", ""), t.get("name", "")))
    upc.sort()
    if upc:
        lines.append("· 다가오는 확인점: " + " · ".join(f"{tid} {d.month}/{d.day}(D-{(d - today).days})"
                                                for d, tid, _ in upc[:3]))
    srcs = []
    for r in ((status or {}).get("sources") or {}).values():
        if isinstance(r, dict) and r.get("kind") == "spacex":
            srcs.append(f"{r.get('source')} {'정상' if r.get('outcome') == 'ok' else r.get('outcome')}")
    if srcs:
        lines.append("· 측정 원천(최근 full): " + " · ".join(srcs[:6]))
    hr = (watch or {}).get("hourly") or {}
    if hr.get("last_success_at"):
        lines.append(f"· 매시간 감시 마지막 성공: {hr['last_success_at'][:16].replace('T', ' ')}")
    lines.append("· 이 점검이 내일 아침에 오지 않으면 감시가 멈춘 것입니다.")
    return "\n".join(lines)


def _dispatch(q):
    """대기열 항목들 → repository_dispatch 한 번. (성공 여부, HTTP 코드)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not tok:
        print("[SpaceX] GITHUB_REPOSITORY/GITHUB_TOKEN 없음 — 발사 불가", file=sys.stderr)
        return False, None
    import requests
    reason = " · ".join(sorted({f"{x.get('trigger_id') or ''} {x.get('name')}: {x.get('reason')}".strip()
                                for x in q}))
    urls = [u for x in q for u in (x.get("urls") or [])]
    if urls:
        reason += " · 원문: " + " ".join(dict.fromkeys(urls))
    reason = reason[:900]
    r = requests.post(f"https://api.github.com/repos/{repo}/dispatches",
                      headers={"Authorization": "Bearer " + tok,
                               "Accept": "application/vnd.github+json"},
                      json={"event_type": "spacex-event",
                            "client_payload": {"reason": reason,
                                               "keys": [x.get("key") for x in q][:20]}},
                      timeout=30)
    if r.status_code in (200, 204):
        print(f"[SpaceX] 탐사 엔진 사건 모드 발사 완료 ({len(q)}건): {reason}")
        return True, r.status_code
    msg = f"🚨 SpaceX 탐사 엔진 사건 모드 발사 실패 — HTTP {r.status_code}: {r.text[:200]}"
    print(msg, file=sys.stderr)
    send_dm(msg)
    return False, r.status_code


def dispatch_queue():
    """커밋 뒤 단계 — 대기열의 사건 모드 발사. GITHUB_TOKEN 의 repository_dispatch 는
    새 워크플로 실행을 만든다(GitHub 문서: GITHUB_TOKEN 예외 = workflow_dispatch·
    repository_dispatch). 실패는 실패로 보고한다(exit 1 + DM).
    대기열 항목은 다음 날 회차가 지운다(오늘 날짜만 남긴다) — 결과는 실행 로그와 엔진 실행 기록에 남는다."""
    watch = read_json(WATCH_PATH, {})
    rid = os.environ.get("GITHUB_RUN_ID", "")
    # 이번 실행이 올린 항목만 쏜다 — 하루 3회차가 같은 대기열을 거듭 쏘지 않게
    q = [x for x in (watch.get("dispatch_queue") or []) if x.get("state") == "queued"
         and x.get("run_id", "") == rid]
    if not q:
        print("[SpaceX] 발사 대기열 비어 있음 — 침묵")
        return 0
    ok, _ = _dispatch(q)
    return 0 if ok else 1


# ────────────────────────────────────────────────────────────────

SCHEMA = 2                       # 2026-10-27: 모든 양식 감시 · 등급 · 내부자 상세
HOURLY_PATH = DATA_DIR / "spacex_hourly.json"     # 매시간 감시가 쓰는 유일한 파일(데일리와 충돌 없음)
HOURLY_WORKFLOW = "spacex-watch.yml"
HOURLY_STALE_H = 6


def _key(e):
    return e.get("accn") or e.get("id")


def collect_hourly_runs(fc, watch, logs):
    """감시자의 감시자 — 매시간 감시(spacex-watch.yml)의 마지막 성공 시각을 데일리가 확인한다."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "univertrix-spacex-watch"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{HOURLY_WORKFLOW}/runs?per_page=10"
    doc, oc, code = _get_json(fc, url, "매시간 감시 실행기록", hdr)
    if doc is None:
        return
    runs = doc.get("workflow_runs") or []
    ok = [r for r in runs if r.get("conclusion") == "success"]
    watch["hourly"] = {"checked_at": now_kst().isoformat(timespec="minutes"),
                       "last_success_at": (ok[0].get("updated_at") if ok else None),
                       "last_conclusion": (runs[0].get("conclusion") if runs else None)}


def _hourly_stale_h(watch, now):
    t = ((watch or {}).get("hourly") or {}).get("last_success_at")
    if not t:
        return None
    try:
        return (now.astimezone(timezone.utc) - datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return None


def run(mode="daily"):
    """측정 한 회차. mode="daily"(full 회차: 공시·재무·발사·IR·엔진) | "hourly"(공시·IR 만, 즉시 발사).

    데일리는 data/spacex_watch.json·spacex_kpi.json 을, 매시간은 data/spacex_hourly.json 만 쓴다.
    서로의 '이미 울린 사건'을 읽어 같은 사건으로 두 번 울지 않는다."""
    import feed_client as fc
    hourly = mode == "hourly"
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    watch = read_json(WATCH_PATH, {}) or {}
    hstate = read_json(HOURLY_PATH, {}) or {}
    kpi = read_json(KPI_PATH, {}) or {}
    logs = []
    baseline = bool(watch.get("seen"))
    if hourly and (not baseline or int(watch.get("schema") or 1) < SCHEMA):
        print("[SpaceX] 매시간 감시 보류 — 데일리 회차의 기준선·이관(schema 2)이 먼저다")
        return 0
    migrating = (not hourly) and baseline and int(watch.get("schema") or 1) < SCHEMA
    seen = set(watch.get("seen") or []) | set(hstate.get("alerted") or [])

    if hourly:
        sec_events, sub = collect_sec_light(fc, logs)
        launch_events = []
        ir_events = collect_ir(fc, logs)
    else:
        sec_events, sub = collect_sec(fc, watch, kpi, logs)
        launch_events = collect_launches(fc, watch, logs)
        ir_events = collect_ir(fc, logs)
        collect_engine(fc, watch, logs)
        collect_hourly_runs(fc, watch, logs)
        fc.flush()
    ins_rows = parse_filings(sub)[1] if sub else []

    old_cut = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    fresh, quiet = [], []
    for e in sec_events + launch_events + ir_events:
        k = _key(e)
        if k and k not in seen:
            seen.add(k)
            # 이관 회차: 새로 감시하게 된 양식의 '옛' 공시(이틀 전 이전)는 기준선으로 — 몰아 울리지 않는다
            (quiet if (migrating and (e.get("date") or "") < old_cut) else fresh).append(e)

    # 내부자 양식 — 원문 XML 을 읽어 사건으로(회당 상한, 못 읽은 것은 다음 회차로 미룬다)
    hdr = sec_headers()
    ins_events, cap = [], (20 if migrating else INSIDER_FETCH_CAP)
    ins_cut = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    for row in ins_rows:
        if not row.get("accn") or row["accn"] in seen:
            continue
        is_old = migrating and (row.get("date") or "") < old_cut
        if is_old and (row.get("date") or "") < ins_cut:
            seen.add(row["accn"])          # 60일보다 옛 내부자 양식은 기준선으로만
            continue
        if not baseline or not hdr or cap <= 0:
            if not baseline:
                seen.add(row["accn"])
            continue
        url = insider_xml_url(row["accn"], row.get("doc"))
        text, oc, code = fc.fetch(url, "SEC 내부자 원문", "spacex", headers=hdr, timeout=30) if url else (None, "skip", None)
        cap -= 1
        if text is None:
            continue                        # 다음 회차에 다시(seen 에 넣지 않는다)
        seen.add(row["accn"])
        ev = insider_event(row, text)
        (quiet if is_old else ins_events).append(ev)
        time.sleep(0.15)

    # 이관 회차: 옛 분류표가 '기록'으로만 남긴 최근 7일 사건을 한 번 다시 판정한다(10/26 8.01 금융리스)
    catchup = []
    if migrating:
        due_txt = " ".join(str(d.get("key")) for d in (watch.get("due") or []))
        c7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        for e in watch.get("events") or []:
            if e.get("source") != "SEC EDGAR" or (e.get("date") or "") < c7 or not e.get("accn"):
                continue
            if e["accn"] in due_txt:
                continue
            g = filing_grade(e.get("form", ""), e.get("items", ""))
            if g in ("urgent", "major") and e.get("kind") in ("filing", None):
                catchup.append(dict(e, grade=g))

    # 8.01 같은 '항목만으로 모르는' 8-K — 본문 핵심어로 종류를 다시 정한다(회당 상한)
    tcap = TEXT_FETCH_CAP
    for e in fresh + catchup:
        if e.get("kind") == "filing" and form_base(e.get("form")) == "8-K" and e.get("url") and hdr and tcap > 0:
            text, oc, code = fc.fetch(e["url"], "SEC 공시 본문", "spacex", headers=hdr, timeout=60)
            tcap -= 1
            if text:
                e["kind"] = refine_kind(e["kind"], text)

    all_events = sec_events + launch_events + ir_events + ins_events + quiet
    for e in catchup:                 # 재판정 결과를 화면 사건 목록에도 반영
        for x in all_events:
            if x.get("accn") == e["accn"]:
                x.update(kind=e["kind"], grade=e["grade"])

    st = read_json(STATE_PATH, {}) or {}
    triggers = st.get("triggers_next_12m") or []
    already = {d.get("key") for d in (watch.get("due") or [])} | {d.get("key") for d in (hstate.get("due") or [])}
    alert = fresh + ins_events + catchup
    new_due, records = [], []
    if not baseline:
        logs.append(f"기준선 확보 회차 — 과거 사건 {len(fresh)}건 적재, 트리거·DM·발사 생략")
        new_due = judge_triggers(triggers, [], today, already)
        for d in new_due:
            d["baseline"] = True
        watch["due"] = (watch.get("due") or []) + new_due
        new_due = []
    else:
        new_due = judge_triggers(triggers, alert, today, already,
                                 written_on=((st.get("run") or {}).get("run_date") or "")[:10] or None)
        woke = {str(d.get("key")).split(":")[-1] for d in new_due}
        records = [e for e in alert if _key(e) not in woke and (e.get("grade") in ("record", None))]
        for d in new_due:            # 대기열·DM 에 원문 링크를 싣는다
            if not d.get("url"):
                ev = next((e for e in alert if _key(e) and str(d.get("key")).endswith(_key(e))), None)
                if ev and ev.get("url"):
                    d["url"] = ev["url"]

    note = None
    if migrating:
        note = (f"(감시 개정 이관 회차 — 새로 감시하는 양식의 옛 공시 {len(quiet)}건은 기준선으로 기록만,"
                f" 최근 7일 재판정 {len(catchup)}건)")
    fired = False
    q_item = None
    if new_due:
        q_item = {"key": "q:" + today + ":" + now.strftime("%H%M") + ":" + str(len(new_due)),
                  "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                  "queued_at": now.isoformat(timespec="minutes"),
                  "state": "queued", "trigger_id": ",".join(sorted({str(d.get("trigger_id") or "") for d in new_due})),
                  "name": " · ".join(d["name"] for d in new_due)[:200],
                  "reason": "트리거·중요 사건 " + str(len(new_due)) + "건",
                  "urls": [d["url"] for d in new_due if d.get("url")][:5]}
        if hourly:                   # 매시간 감시는 커밋을 기다리지 않는다 — 원문 링크를 사유에 싣고 바로 쏜다
            fired, code = _dispatch([q_item])
            q_item["state"] = "sent" if fired else "failed"
            q_item["http"] = code
    if new_due or records or (note and (quiet or catchup)):
        send_dm(watch_message(new_due, records, fired, note))

    def heartbeat():
        """하루 한 번 · 08시 이후 첫 회차 · 워크플로가 SPACEX_HEARTBEAT=1 일 때만. 이번 회차 사건까지 합친 뒤 센다."""
        if os.environ.get("SPACEX_HEARTBEAT") == "1" and now.hour >= 8 and \
                today not in (watch.get("heartbeat_date"), hstate.get("heartbeat_date")):
            send_dm(heartbeat_message(watch, hstate, st, now, read_json(DATA_DIR / "fetch_status.json", {})))
            (hstate if hourly else watch)["heartbeat_date"] = today     # DM 실패여도 같은 날 다시 시도하지 않는다

    if hourly:
        hstate["alerted"] = sorted((set(hstate.get("alerted") or []) |
                                    {_key(e) for e in fresh + ins_events if _key(e)}))[-KEEP_SEEN:]
        if new_due:
            hstate["due"] = ((hstate.get("due") or []) + new_due)[-60:]
        if fresh or ins_events:
            hstate["events"] = merge_events(hstate.get("events"), fresh + ins_events)[:40]
        if q_item:
            hstate["dispatches"] = ((hstate.get("dispatches") or []) + [q_item])[-20:]
        heartbeat()
        # 실행 시각은 쓰지 않는다 — 바뀐 것이 없으면 커밋도 없게(Cloudflare 빌드 낭비 방지)
        write_json(HOURLY_PATH, hstate)
    else:
        # 매시간 감시가 먼저 잡은 사건(내부자 상세 포함)·due 를 화면 목록에 합친다
        watch["events"] = merge_events(watch.get("events"),
                                       list(hstate.get("events") or []) + all_events)
        watch["seen"] = sorted(seen)[-KEEP_SEEN:]
        watch["cik"] = CIK
        watch["schema"] = SCHEMA
        watch["generated_at"] = now.isoformat(timespec="minutes")
        dues = list(watch.get("due") or [])
        known = {d.get("key") for d in dues}
        dues += [d for d in (hstate.get("due") or []) if d.get("key") not in known]
        if new_due:
            dues += new_due
        watch["due"] = dues[-60:]
        queue = [x for x in (watch.get("dispatch_queue") or []) if x.get("state") == "queued"
                 and x.get("queued_at", "")[:10] == today]       # 지난 회차 대기열은 발사 단계가 처리했다
        if q_item:
            queue.append(q_item)
        watch["dispatch_queue"] = queue
        # 감시자의 감시자 — 매시간 감시가 6시간 넘게 성공하지 못했으면 하루 한 번 DM
        stale = _hourly_stale_h(watch, now)
        if stale is not None and stale > HOURLY_STALE_H and watch.get("hourly_stale_alerted") != today:
            send_dm(f"⚠️ SpaceX 매시간 감시가 {stale:.0f}시간째 성공하지 못했습니다 — Actions 의 spacex-watch 실행 기록을 확인하세요.")
            watch["hourly_stale_alerted"] = today
        heartbeat()
        write_json(WATCH_PATH, watch)
        write_json(KPI_PATH, kpi)

    top = ((kpi.get("auto") or {}).get("quarters") or [{}])[0]
    print(f"[SpaceX] {'매시간' if hourly else '데일리'} · 공시 {len(sec_events)} · 발사 {len(launch_events)} · "
          f"IR {len(ir_events)} · 내부자 {len(ins_events)} · 새 사건 {len(fresh)} · 재판정 {len(catchup)} · "
          f"due {len(new_due)} · 기록 {len(records)}"
          + ("" if hourly else f" · 최신 분기 {top.get('end')} 매출 {top.get('revenue_usd_m')}M"))
    for n in logs:
        print(f"[SpaceX] 참고: {n}", file=sys.stderr)
    return 0


def main():
    if "--dispatch" in sys.argv:
        return dispatch_queue()
    mode = "hourly" if "--hourly" in sys.argv else "daily"
    try:
        return run(mode)
    except Exception:
        traceback.print_exc()
        print("[SpaceX] 측정 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
