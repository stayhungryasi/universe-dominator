#!/usr/bin/env python3
"""
레전드벤치마크 기계 판단층 (fetch_buffett_auto) — 공시에서 직접 잰다
================================================================================
헌법 개정(2026-08-31): 판단층을 사람만 채우던 시절에는 34종 중 1종만 취재됐고
나머지 33종은 영원히 '미검정'이었다. 이제 기계가 매일 채우고, 사람이 덮어쓴다.

  산출물: data/buffett_auto.json   ← **이 파일만 쓴다.** buffett_config.json 은
         사람의 것이라 읽지도 않고 쓰지도 않는다(유형·가드만 참조).

측정 (미국 상장):
  eps_adj_ttm = Σ(최근 4분기 조정순이익) ÷ 최신 희석주식수
    조정순이익 = NetIncomeLoss − 투자손익 × (1 − 21%)
    투자손익은 **존재하는 태그만** 합산한다. 회사마다 쓰는 태그가 달라서,
    없는 태그를 0 으로 치면 조정이 조용히 사라진다 — 어떤 태그를 썼는지 method 에 적는다.
  roe_tangible = 조정 TTM 순이익 ÷ (자본총계 − 영업권 − 무형자산)
    유형자기자본이 0 이하면 null. 음수로 나눈 ROE 는 숫자가 아니라 착시다.
  g_cagr3y = 3년 조정 EPS CAGR (4분기 합산 3개 시점)
  g_forward = **전망** 성장률 (yfinance 장기 → Finnhub 연간 추정 CAGR)
    과거 성장률(epsGrowth5Y 등)은 쓰지 않는다 — 잘 나간 구간을 영원히 이어붙이는
    것이라 정점 이익 함정의 다른 얼굴이다(2026-09-02 확정).
    → 둘 중 **하나라도 없으면 g 는 null**. 측정층 pick_g 의 원칙을 그대로 따른다
      (낙관 단일값 금지 — 사양서 §2).

해외 상장(.T/.TW/.KS/.SR 및 ADR):
  SEC XBRL 이 없다. yfinance 의 TTM 희석 EPS 를 **조정 없이** 싣고
  method 에 "GAAP 미조정" 이라고 못박는다. 조정하지 않은 값을 조정한 값인 척하면
  같은 표에서 서로 다른 자로 잰 숫자가 나란히 앉게 된다.

원칙: 개별 종목이 실패해도 전체는 계속한다. 못 잰 칸은 null 로 남긴다 — 0 치환 금지.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
CFG_PATH = DATA_DIR / "buffett_config.json"
OUT_PATH = DATA_DIR / "buffett_auto.json"
KST = timezone(timedelta(hours=9))

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

TAX_RATE = 0.21          # 투자손익 세후 근사 (사양서 지정)
FOREIGN_SUFFIX = (".T", ".TW", ".KS", ".SR")
# SEC XBRL 이 없는 해외 발행사 — 티커에 접미사가 없어도 미국 공시 대상이 아니다
FOREIGN_TICKERS = {"ASML", "TSM", "ARM", "SPCX"}

NET_INCOME_TAGS = ["NetIncomeLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic",
                   "ProfitLoss"]
# 투자손익 계열 — 회사마다 쓰는 태그가 다르다. 있는 것만 합산한다.
INVEST_TAGS = ["GainLossOnInvestments",
               "UnrealizedGainLossOnInvestments",
               "EquitySecuritiesFvNiGainLoss",
               "EquitySecuritiesFvNiUnrealizedGainLoss",
               "GainLossOnInvestmentsExcludingOtherThanTemporaryImpairments",
               "MarketableSecuritiesRealizedGainLoss",
               "DebtAndEquitySecuritiesGainLoss"]
DILUTED_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfDilutedSharesOutstandingBasicAndDiluted"]
EQUITY_TAGS = ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
GOODWILL_TAGS = ["Goodwill"]
INTANGIBLE_TAGS = ["FiniteLivedIntangibleAssetsNet",
                   "IntangibleAssetsNetExcludingGoodwill"]


# ────────────────────────────────────────────────────────────────
# 순수 함수부 — 네트워크·시각 의존 없음. 픽스처로 검산 가능해야 한다.
# ────────────────────────────────────────────────────────────────

def is_foreign(ticker):
    t = (ticker or "").upper()
    return t.endswith(FOREIGN_SUFFIX) or t in FOREIGN_TICKERS


def norm(concept):
    """concept 를 비교용으로 정규화한다.

    2026-08-31 첫 실전에서 미국 19종이 전부 '순이익 태그 없음' 으로 떨어졌다.
    Finnhub 응답은 정상이었다(ok/200, 분기 33~49건) — 즉 **태그 이름 표기가
    내 기대와 달랐다.** 회사·기간마다 'us-gaap_NetIncomeLoss' · 'us-gaap:NetIncomeLoss' ·
    'NetIncomeLoss' 가 섞여 온다. 정확 일치로 재면 표기 하나 다른 것 때문에
    측정 전체가 조용히 0 이 된다.
    """
    c = str(concept or "")
    for sep in (":", "_"):
        if sep in c:
            c = c.rsplit(sep, 1)[-1]
    return c.lower()


def to_num(v):
    """숫자 · 숫자문자열 → float. 아니면 None (0 으로 치지 않는다)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace(",", "").replace("$", "")
        if t.startswith("(") and t.endswith(")"):      # 회계식 음수 표기
            t = "-" + t[1:-1]
        try:
            return float(t)
        except ValueError:
            return None
    return None


def flatten(report):
    """한 회차 report(bs/ic/cf) → {정규화 concept: (값, 원래이름)}.

    같은 concept 이 여러 번 나오면 **처음 것**을 쓴다(연결 → 부문 순으로 오는 관례).
    """
    out = {}
    for section in ("ic", "bs", "cf"):
        for row in (report or {}).get(section) or []:
            c = norm((row or {}).get("concept"))
            if not c or c in out:
                continue
            v = to_num((row or {}).get("value"))
            if v is None:
                continue
            out[c] = (v, (row or {}).get("concept"))
    return out


def pick(flat, tags):
    """태그 목록 중 **먼저 발견되는** 값 → (값, 원래 태그명). 없으면 (None, None)."""
    for t in tags:
        hit = flat.get(norm(t))
        if hit is not None:
            return hit[0], hit[1]
    return None, None


def sample_concepts(flat, n=14):
    """진단용 — 실제로 무엇이 왔는지 눈으로 본다.

    태그가 안 맞을 때 '없다'만 찍으면 다음에도 똑같이 헤맨다. 무엇이 왔는지를
    남겨야 다음 회차에 맞출 수 있다 (침묵은 통과가 아니다).
    """
    return ", ".join(sorted(orig or k for k, (v, orig) in list(flat.items())[:n]))


def invest_gain(flat):
    """존재하는 투자손익 태그만 합산 → (합계, 쓴 태그들). 하나도 없으면 (None, [])."""
    used, total = [], 0.0
    for t in INVEST_TAGS:
        hit = flat.get(norm(t))
        if hit is not None:
            total += hit[0]
            used.append(hit[1] or t)
    return (total, used) if used else (None, [])


def adjusted_income(flat):
    """한 분기 조정순이익 → (값, 근거설명). 순이익이 없으면 (None, 사유)."""
    ni, ni_tag = pick(flat, NET_INCOME_TAGS)
    if ni is None:
        return None, "순이익 태그 없음"
    gain, used = invest_gain(flat)
    if gain is None:
        return ni, f"{ni_tag} (투자손익 태그 없음 — 무조정)"
    return ni - gain * (1.0 - TAX_RATE), f"{ni_tag} − ({'+'.join(used)})×{1 - TAX_RATE:.2f}"


def period_days(rep):
    """이 보고가 덮는 기간(일). 날짜가 없으면 None."""
    a, b = (rep or {}).get("startDate"), (rep or {}).get("endDate")
    if not a or not b:
        return None
    try:
        d0 = datetime.strptime(str(a)[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(b)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    n = (d1 - d0).days
    return n if n > 0 else None


def quarterly_only(reports):
    """**분기 보고만** 남긴다 → (걸러진 목록, 버린 수).

    2026-08-31 2차 실전 사고: 태그를 고치고 나니 값이 나왔는데 **2~3배 부풀어 있었다**
    (MSFT 35.74 · AAPL 17.57 — 실제 TTM 의 약 2.7배). Finnhub 의 'quarterly' 피드에는
    누적 기간 보고(반기·9개월·연간)가 섞여 온다. 그걸 4개 더하면 같은 분기를 여러 번
    세게 된다. 무서운 점은 **결과가 그럴듯해 보인다는 것** — 자릿수가 맞고 부호도 맞아서
    화면에 '통과' 라는 결론까지 정상적으로 찍혔다(MSFT 쿠폰 34.9%).
    그래서 기간 길이로 거른다: 80~100일만 분기다.
    날짜가 아예 없으면 거르지 않되(구 응답 호환) 그 사실을 세어 로그에 남긴다.
    """
    kept, dropped = [], 0
    for r in reports or []:
        n = period_days(r)
        if n is None or 80 <= n <= 100:
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def diluted_shares(flat):
    """희석주식수 → (값, 근거). 태그가 없으면 순이익 ÷ 희석EPS 로 되돌려 구한다.

    GOOG·AVGO 는 주식수 태그를 싣지 않고 EarningsPerShareDiluted 만 싣는다.
    거기서 포기하면 대형주가 통째로 빠진다 — 같은 공시 안에 답이 있는데도.
    """
    sh, tag = pick(flat, DILUTED_TAGS)
    if sh and sh > 0:
        return sh, tag
    ni, _ = pick(flat, NET_INCOME_TAGS)
    eps, eps_tag = pick(flat, ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"])
    if ni and eps and eps != 0:
        derived = ni / eps
        if derived > 0:
            return derived, f"{eps_tag} 역산"
    return None, None


def implausible(eps_ttm, fwd_eps):
    """판단층의 선행 EPS 와 대조한 타당성 — 과대 산출만 막는다.

    막으려는 것 한 문장: **집계 오류로 부풀려진 EPS 가 화면에 결론으로 나가는 것.**
    그래서 '부풀림' 방향만 본다(4배 초과). 반대쪽(실적 급감·사이클 저점)은 정상일 수
    있으므로 막지 않는다 — 표면 특징으로 정상을 막지 않기 위해서다(2026-08-22 교훈).
    선행 EPS 가 없으면 대조할 자가 없으니 통과시킨다.
    """
    if eps_ttm is None or not isinstance(fwd_eps, (int, float)) or isinstance(fwd_eps, bool):
        return False
    if fwd_eps <= 0:
        return False
    return eps_ttm > fwd_eps * 4.0


def quarter_incomes(reports):
    """진짜 분기 조정순이익 목록 → [(연, 분기, 값, 근거)] 최신순.

    2026-09-02 진단이 지목한 것: 필터를 통과한 '분기'가 16건인데 12년을 덮고 있었다.
    즉 **연 1건만 남아 있었다.** 원인은 Finnhub 분기 보고의 손익계산서가 **누적(YTD)**
    이라는 것 — Q1 91일 · Q2 182일 · Q3 273일 · FY 365일이다. 80~100일 필터는 Q1 만
    남겼고, 그 결과 TTM 이 '서로 다른 4개 연도의 Q1 합' 이 되어 있었다.

    무서운 대목: 그 합이 **그럴듯했다.** 애플은 Q1(홀리데이)이 가장 큰 분기라
    네 해치 Q1 을 더하니 연간 EPS 와 비슷한 9.61 이 나왔다. 자릿수가 맞으니
    아무도 되묻지 않는다.

    그래서 기간 길이로 거르지 않고 **차분한다**: 누적 보고(100일 초과)면
    같은 해 직전 분기의 누적을 빼서 그 분기만 남긴다. 회사가 이미 분기 단위로
    싣는 경우(100일 이하)는 그대로 쓴다.
    """
    ytd = {}
    for r in reports or []:
        y, q = (r or {}).get("year"), (r or {}).get("quarter")
        if not isinstance(y, int) or q not in (1, 2, 3, 4):
            continue
        if (y, q) in ytd:
            continue                       # 같은 분기의 수정 공시 — 최신 것만
        inc, why = adjusted_income(flatten((r or {}).get("report")))
        if inc is None:
            continue
        ytd[(y, q)] = (inc, why, period_days(r))

    out = []
    for (y, q), (inc, why, days) in ytd.items():
        if q == 1 or (days is not None and days <= 100):
            out.append((y, q, inc, why))    # 이미 분기 단위
            continue
        prev = ytd.get((y, q - 1))
        if prev is None:
            continue                        # 직전 누적이 없으면 차분 불가 → 버린다
        out.append((y, q, inc - prev[0], why))
    out.sort(reverse=True)
    return out


def eps_adj_ttm_from(reports):
    """최근 4분기 → (eps, method, 분기수). 4분기가 안 되면 (None, 사유, n)."""
    qs = quarter_incomes(reports)
    if len(qs) < 4:
        flat0 = flatten(((reports or [{}])[0] or {}).get("report"))
        return None, (f"분기 부족({len(qs)}/4) · 실제 태그: {sample_concepts(flat0)}"), len(qs)
    window = qs[:4]
    # 창이 실제로 1년을 덮는지 확인한다 — 연 1건만 남는 종류의 사고를 여기서 잡는다
    span = (window[0][0] - window[-1][0]) * 4 + (window[0][1] - window[-1][1])
    if span != 3:
        return None, (f"최근 4분기가 연속이 아님({window[-1][0]}Q{window[-1][1]}"
                      f"~{window[0][0]}Q{window[0][1]})"), len(qs)
    total = sum(x[2] for x in window)
    notes = []
    for x in window:
        if x[3] not in notes:
            notes.append(x[3])
    head = flatten(((reports or [{}])[0] or {}).get("report"))
    shares, sh_tag = diluted_shares(head)
    if not shares or shares <= 0:
        return None, f"희석주식수 없음 · 실제 태그: {sample_concepts(head)}", len(qs)
    return (total / shares,
            "SEC XBRL 조정 · " + " / ".join(notes) + f" ÷ {sh_tag}", len(qs))


def tangible_equity(flat):
    """유형자기자본 = 자본총계 − 영업권 − 무형자산. 0 이하면 None(착시 방지)."""
    eq, _ = pick(flat, EQUITY_TAGS)
    if eq is None:
        return None
    gw, _ = pick(flat, GOODWILL_TAGS)
    intan, _ = pick(flat, INTANGIBLE_TAGS)
    te = eq - (gw or 0.0) - (intan or 0.0)
    return te if te > 0 else None


def cagr(first, last, years):
    """CAGR — 시작·끝이 양수일 때만. 적자에서의 성장률은 숫자가 아니다."""
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def cagr3y_from(reports):
    """3년 조정 EPS CAGR — 4분기 합산을 지금·3년 전 두 시점에서 비교한다.

    분기 보고가 12개(3년) 이상 있어야 성립한다. 없으면 null — 짧은 이력을
    긴 성장률로 늘려 적는 것이 이 자리에서 가장 하기 쉬운 거짓말이다.
    """
    qs = quarter_incomes(reports)
    if len(qs) < 16:
        return None, f"분기 부족({len(qs)}/16 — 3년 비교 불가)"

    def ttm(i):
        w = qs[i:i + 4]
        if len(w) < 4:
            return None
        if (w[0][0] - w[-1][0]) * 4 + (w[0][1] - w[-1][1]) != 3:
            return None                     # 결번이 낀 창은 쓰지 않는다
        return sum(x[2] for x in w)

    # 3년 전 창은 **분기 번호로** 잡는다(정확히 12분기 뒤). 날짜·인덱스로 잡던 두 번의
    # 시도가 모두 실패했다: 인덱스는 결번에 흔들렸고(NVDA 482%), 날짜는 누적 보고
    # 때문에 창 자체를 못 찾았다(전 종목 null). 진짜 분기 목록 위에서는 12칸이 3년이다.
    now, before = ttm(0), ttm(12)
    if now is None or before is None:
        return None, f"연속 4분기 창 확보 실패(현재 {now} · 3년전 {before})"
    v = cagr(before, now, 3.0)
    return v, ("3년 창" if v is not None
               else f"CAGR 불가(현재 {now:.0f} · 과거 {before:.0f} — 적자 구간)")


# ────────────────────────────────────────────────────────────────
# 수집부
# ────────────────────────────────────────────────────────────────

def _record(source, outcome, code, items):
    try:
        import feed_client
        feed_client.record("finnhub_xbrl", source, outcome, code, items)
    except Exception as e:
        print(f"[자동취재] 원장 기록 실패 ({e})", file=sys.stderr)


def fetch_reports(ticker, key):
    """Finnhub financials-reported (분기) → (reports, outcome, code)."""
    url = ("https://finnhub.io/api/v1/stock/financials-reported"
           f"?symbol={ticker}&freq=quarterly&token={key}")
    try:
        r = requests.get(url, headers=UA, timeout=25)
        time.sleep(1.1)                      # 무료 60 call/min 준수
        if r.status_code != 200:
            return [], "http_error", r.status_code
        data = (r.json() or {}).get("data") or []
        return data, ("ok" if data else "zero"), r.status_code
    except Exception as e:
        print(f"[자동취재] {ticker} XBRL 실패 ({type(e).__name__})", file=sys.stderr)
        return [], "timeout", None


# ── 전망 성장률 ──────────────────────────────────────────────────
# **과거 성장률을 미래 가정으로 쓰지 않는다** (2026-09-02 선장님 확정).
# epsGrowth5Y 같은 실적 성장률을 10년 쿠폰의 g 에 넣으면, 잘 나간 구간의 성장을
# 영원히 이어붙이는 셈이 된다 — 정점 이익 함정의 다른 얼굴이다. 실제로 그 값으로
# AAPL·LLY 가 '통과' 판정을 받았다. 전망치가 없으면 **null → 미검정**이 정답이다.
LTG_LABELS = ("+5y", "5y", "ltg", "longterm", "long term", "next 5 years")


def ltg_from_growth_table(pairs):
    """(라벨, 값) 목록에서 장기 성장률 한 개를 고른다 → 소수 | None.

    yfinance growth_estimates 의 행 라벨은 버전마다 '+5y' · 'LTG' 등으로 다르다.
    과거 행('-5y')은 **절대 고르지 않는다** — 그게 이 규율의 핵심이다.
    """
    for label, value in pairs or []:
        key = str(label).strip().lower()
        if key.startswith("-"):
            continue                      # 과거 행 — 쳐다보지 않는다
        if any(k in key for k in LTG_LABELS):
            v = to_num(value)
            if v is None:
                continue
            return v / 100.0 if abs(v) > 1.5 else v      # 퍼센트 표기 방어
    return None


def growth_from_annual_estimates(rows):
    """연간 EPS 추정 2개년 → CAGR. 추정이 2개 미만이거나 적자면 None.

    rows: [{"period": "2027-12-31", "epsAvg": 9.9}, ...] (순서 무관)
    """
    pts = []
    for r in rows or []:
        v = to_num((r or {}).get("epsAvg"))
        d = str((r or {}).get("period") or "")[:10]
        if v is None or len(d) < 10:
            continue
        try:
            pts.append((datetime.strptime(d, "%Y-%m-%d"), v))
        except ValueError:
            continue
    pts.sort()
    if len(pts) < 2:
        return None
    (d0, v0), (d1, v1) = pts[0], pts[-1]
    years = (d1 - d0).days / 365.25
    if years < 0.5:
        return None
    return cagr(v0, v1, years)


def fetch_forward_growth(ticker, key):
    """전망 성장률 → (값, 출처). 없으면 (None, 사유).

    1순위 yfinance 장기 성장률(+5y/LTG) · 2순위 Finnhub 연간 EPS 추정 2개년 CAGR.
    """
    try:
        import yfinance
        tbl = yfinance.Ticker(ticker).growth_estimates
        if tbl is not None and len(tbl):
            col = tbl.columns[0]
            pairs = [(idx, tbl.loc[idx, col]) for idx in tbl.index]
            v = ltg_from_growth_table(pairs)
            if v is not None:
                return v, "yfinance 장기 성장률"
    except Exception as e:
        print(f"[자동취재] {ticker} yfinance 전망 실패 ({type(e).__name__})", file=sys.stderr)

    if not key:
        return None, "전망 없음(yfinance 미확보 · Finnhub 키 없음)"
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/eps-estimate"
                         f"?symbol={ticker}&freq=annual&token={key}",
                         headers=UA, timeout=20)
        time.sleep(1.1)
        if r.status_code != 200:
            return None, f"Finnhub 추정 HTTP {r.status_code}"
        v = growth_from_annual_estimates((r.json() or {}).get("data") or [])
        if v is not None:
            return v, "Finnhub 연간 추정 CAGR"
    except Exception as e:
        print(f"[자동취재] {ticker} Finnhub 추정 실패 ({type(e).__name__})", file=sys.stderr)
    return None, "전망 성장률 미확보"


def fetch_foreign_eps(ticker):
    """해외 상장 TTM 희석 EPS — yfinance. 실패하면 None(추정하지 않는다)."""
    try:
        import yfinance
    except Exception as e:
        print(f"[자동취재] yfinance 없음 → 해외 종목 건너뜀 ({e})", file=sys.stderr)
        return None
    try:
        info = yfinance.Ticker(ticker).info or {}
    except Exception as e:
        print(f"[자동취재] {ticker} yfinance 실패 ({type(e).__name__})", file=sys.stderr)
        return None
    for k in ("trailingEps", "epsTrailingTwelveMonths"):
        v = info.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0:
            return float(v)
    return None


def build_block(c, key, now):
    """종목 하나의 자동 판단 블록. 못 잰 칸은 null 로 남긴다."""
    ticker = c.get("ticker", "")
    block = {
        "as_of": now.strftime("%Y-%m-%d"),
        "period": None,
        "cyclical_peak_guard": ("시클리컬" in (c.get("type") or "")),
        "eps_adj_ttm": None, "roe_tangible": None,
        "g_cagr3y": None, "g_forward": None, "g_forward_source": None,
        "method": None, "source": None, "confidence": None,
    }

    if is_foreign(ticker):
        eps = fetch_foreign_eps(ticker)
        block["eps_adj_ttm"] = {"value": eps} if eps is not None else None
        block["method"] = "GAAP 미조정 (해외 공시 — 투자손익 조정 없음)"
        block["source"] = "yfinance TTM 희석 EPS"
        block["confidence"] = "중" if eps is not None else None
        gf, gf_src = fetch_forward_growth(ticker, "")     # 해외는 yfinance 만
        block["g_forward"] = round(gf, 4) if gf is not None else None
        block["g_forward_source"] = gf_src if gf is not None else None
        return block, ("ok" if eps is not None else "zero")

    if not key:
        block["method"] = None
        return block, "zero"

    reports, outcome, code = fetch_reports(ticker, key)
    _record(ticker, outcome, code, len(reports))
    if not reports:
        return block, outcome

    eps, method, nq = eps_adj_ttm_from(reports)
    block["source"] = f"Finnhub financials-reported · 분기 {len(reports)}건"
    if eps is not None and implausible(eps, c.get("forward_eps")):
        print(f"[자동취재] {ticker}: EPS {eps:.2f} 가 선행 {c.get('forward_eps')} 의 4배 초과 "
              f"— 집계 오류 의심으로 보류", file=sys.stderr)
        eps, method = None, f"타당성 보류 — 산출 {eps:.2f} vs 선행 {c.get('forward_eps')}"
    if eps is None:
        block["method"] = f"산출 불가 — {method}"
        block["confidence"] = None
    else:
        block["eps_adj_ttm"] = {"value": round(eps, 4)}
        block["method"] = method
        block["confidence"] = "중"          # 공시 직접 추출이나 태그 선택은 근사다
        latest = reports[0] or {}
        block["period"] = (f"{latest.get('year')}Q{latest.get('quarter')}"
                           if latest.get("year") else None)
        flat = flatten(latest.get("report"))
        te = tangible_equity(flat)
        if te:
            ni_ttm = eps * (pick(flat, DILUTED_TAGS)[0] or 0)
            if ni_ttm:  # pick 은 (값, 태그명) 을 준다 — [0] 이 값
                block["roe_tangible"] = round(ni_ttm / te, 4)
        else:
            block["notes"] = "유형자기자본 0 이하 — ROE 미산출"

    g3, g3_why = cagr3y_from(reports)
    block["g_cagr3y"] = round(g3, 4) if g3 is not None else None
    if g3 is None:
        # 왜 못 쟀는지를 남긴다 — '없다'만 찍으면 다음에도 똑같이 헤맨다
        print(f"[자동취재] {ticker}: 3년 CAGR 미산출 — {g3_why}", file=sys.stderr)
    gf, gf_src = fetch_forward_growth(ticker, key)
    block["g_forward"] = round(gf, 4) if gf is not None else None
    block["g_forward_source"] = gf_src if gf is not None else None
    if gf is None:
        print(f"[자동취재] {ticker}: 전망 성장률 미확보 — {gf_src}", file=sys.stderr)
    return block, ("ok" if eps is not None else "zero")


def main():
    if not CFG_PATH.exists():
        print("[자동취재] buffett_config.json 없음", file=sys.stderr)
        return 1
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        print("[자동취재] FINNHUB_API_KEY 없음 — 미국 종목 XBRL 생략", file=sys.stderr)

    now = datetime.now(KST)
    items, n_ok = {}, 0
    for c in cfg.get("items", []):
        tk = c.get("ticker", "")
        if not tk:
            continue
        try:
            block, outcome = build_block(c, key, now)
        except Exception as e:
            print(f"[자동취재] {tk} 예외 → 건너뜀 ({e})", file=sys.stderr)
            continue
        items[tk] = block
        got = block.get("eps_adj_ttm") is not None
        n_ok += 1 if got else 0
        print(f"[자동취재] {tk}: eps_adj_ttm "
              f"{(block['eps_adj_ttm'] or {}).get('value') if got else '—'}"
              f" · ROE {block.get('roe_tangible')} · g3y {block.get('g_cagr3y')}"
              f" · {block.get('method') or '미산출'}")

    payload = {"generated_label": now.strftime("%Y-%m-%d %H:%M"),
               "generated_at": now.isoformat(), "items": items}
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    try:
        import feed_client
        feed_client.flush()
    except Exception:
        pass
    print(f"[자동취재] {n_ok}/{len(items)}종 eps_adj_ttm 산출 → {OUT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
