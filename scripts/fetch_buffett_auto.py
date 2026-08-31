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
  g_consensus = Finnhub 장기 성장률 컨센서스
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


def eps_adj_ttm_from(reports):
    """최근 4분기 → (eps, method, 분기수). 4분기가 안 되면 (None, 사유, n)."""
    only, dropped = quarterly_only(reports)
    quarters = only[:4]
    if len(quarters) < 4:
        return None, f"분기 부족({len(quarters)}/4, 누적기간 {dropped}건 제외)", len(quarters)
    total, notes = 0.0, []
    for q in quarters:
        flat = flatten(q.get("report") if isinstance(q, dict) else None)
        inc, why = adjusted_income(flat)
        if inc is None:
            return None, f"{why} — 조정 불가 · 실제 태그: {sample_concepts(flat)}", len(quarters)
        total += inc
        if why not in notes:
            notes.append(why)
    head = flatten((quarters[0] or {}).get("report"))
    shares, sh_tag = diluted_shares(head)
    if not shares or shares <= 0:
        return None, f"희석주식수 없음 · 실제 태그: {sample_concepts(head)}", len(quarters)
    return total / shares, "SEC XBRL 조정 · " + " / ".join(notes) + f" ÷ {sh_tag}", len(quarters)


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
    reports, _ = quarterly_only(reports)
    if len(reports or []) < 16:
        return None
    def ttm(idx):
        tot = 0.0
        for q in reports[idx:idx + 4]:
            inc, _ = adjusted_income(flatten((q or {}).get("report")))
            if inc is None:
                return None
            tot += inc
        return tot
    now, before = ttm(0), ttm(12)
    return cagr(before, now, 3.0)


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


def fetch_consensus_growth(ticker, key):
    """Finnhub 장기 성장률 컨센서스(%) → 소수. 없으면 None."""
    url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={key}"
    try:
        r = requests.get(url, headers=UA, timeout=20)
        time.sleep(1.1)
        if r.status_code != 200:
            return None
        m = (r.json() or {}).get("metric") or {}
    except Exception:
        return None
    for k in ("epsGrowth5Y", "epsGrowth3Y", "revenueGrowth5Y"):
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v) / 100.0
    return None


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
        "g_cagr3y": None, "g_consensus": None,
        "method": None, "source": None, "confidence": None,
    }

    if is_foreign(ticker):
        eps = fetch_foreign_eps(ticker)
        block["eps_adj_ttm"] = {"value": eps} if eps is not None else None
        block["method"] = "GAAP 미조정 (해외 공시 — 투자손익 조정 없음)"
        block["source"] = "yfinance TTM 희석 EPS"
        block["confidence"] = "중" if eps is not None else None
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

    block["g_cagr3y"] = (lambda v: round(v, 4) if v is not None else None)(cagr3y_from(reports))
    block["g_consensus"] = (lambda v: round(v, 4) if v is not None else None)(
        fetch_consensus_growth(ticker, key))
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
