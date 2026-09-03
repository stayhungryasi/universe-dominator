#!/usr/bin/env python3
"""
시차 관측 (fetch_buffett) — 버핏 멀티플 괴리 일일 측정층
================================================================
판단층은 data/buffett_config.json 이다. 이 스크립트는 **판단을 만들지 않는다.**
정당 멀티플(fair_max)·유형·근거는 사람이 취재해 넣은 값이고, 여기서는
그 판단에 오늘의 시세를 곱해 '괴리'만 잰다. config는 절대 재생성하지 않는다.

측정:
  괴리 = 정당 MAX ÷ 현재 P/E − 1     (양수 = 정당선 아래, 음수 = 정당선 위)
  P/E  = 현재가 ÷ EPS
         · config의 forward_eps 가 있으면 그것       → basis "forward"
         · 없으면 Finnhub 후행 P/E 실측              → basis "trailing"
         · Finnhub이 못 주면(주로 비미국 티커)        → basis "미취재", 괴리 null

원칙상 측정하지 않는 바구니 (낙제가 아니라 원칙):
  추정불가 → "too_hard" · 비상장 → "unlisted" · 플로트형 → "float"

레전드벤치마크(2026-08-30, 1단계 — 표시층 없음):
  판단층 buffett_config.json 의 `buffett` 블록(사람이 취재)에 오늘의 시세·10년
  금리를 곱해 **버핏 채권 비교**를 잰다. 판단은 여기서 만들지 않는다.
    ey              = eps_adj_ttm ÷ price
    ey_minus_10y    = ey − 10y
    roe_minus_2x10y = roe_tangible − 2×10y
    g_used          = min(3년 CAGR, 컨센서스)   ← 둘 중 하나라도 null 이면 null
    coupon10y       = ey × (1+g)^10
    zone_buffett    = coupon10y ≥ 3×10y → pass
                      1.5×10y ≤ · < 3×10y → prove_growth
                      · < 1.5×10y        → bond_inferior
                      coupon10y 없음      → untested
  **null 은 null 로 둔다 — 0 으로 치환하지 않는다.** 못 잰 것과 0 은 다르다.
  분기 EPS 를 4배 해 TTM 을 만들지 않는다 — eps_adj_ttm 이 채워질 때까지 untested.
  cyclical_peak_guard 인 종목은 coupon10y·zone_buffett 을 아예 산출하지 않는다
  (정점 이익에서 수익률이 가장 좋아 보이는 함정 — 괴리 가드와 같은 이유).

출력: data/buffett.json
  { generated_label, asof, zones, rates, items:[...],
    history:{ticker:[{d,gap,coupon10y,zone_buffett}...]} }
  history 는 티커별 하루 1점, 90일 보관, 같은 날 재실행은 덮어쓰기(멱등).

원칙: 개별 티커가 실패해도 전체는 계속한다 (우아한 저하).
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
CFG_PATH = DATA_DIR / "buffett_config.json"
OUT_PATH = DATA_DIR / "buffett.json"
KST = timezone(timedelta(hours=9))

HISTORY_DAYS = 90
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 유형이 곧 '측정하지 않음'의 사유가 되는 바구니
TYPE_BASIS = {"추정불가": "too_hard", "비상장": "unlisted", "플로트형": "float"}

# Finnhub /stock/metric 의 후행 P/E 키 — 플랜·종목에 따라 주는 키가 달라
# 앞에서부터 실제로 값이 있는 첫 키를 채택한다 (어느 키를 썼는지 로그로 남긴다)
PE_KEYS = ["peBasicExclExtraTTM", "peExclExtraTTM", "peTTM", "peInclExtraTTM",
           "peNormalizedAnnual", "peBasicExclExtraAnnual", "peExclExtraAnnual"]

# ── 레전드벤치마크: 10년 금리 ──────────────────────────────────────
# v1 은 미국(FRED DGS10)만 구현한다. 한국·일본·대만·사우디 10년물은 아직
# 배선이 없어 null 이며, 그 시장 종목은 zone_buffett 이 untested 로 남는다
# (0 으로 치환하면 '금리 0%' 라는 거짓 판정이 되므로 절대 채우지 않는다).
# 순서는 선장님 지시대로 **FRED 우선**이고, 뒤는 같은 값을 주는 공식 대체원이다.
# 2026-08-30 실측: fred.stlouisfed.org(그래프·CSV 호스트)는 이 환경에서 40초를 줘도
# ReadTimeout 이었고, api.stlouisfed.org 는 HTTP 400(키 없음)으로 **정상 응답**했다.
# 즉 키 없는 CSV 경로만 막혀 있다. 러너에서도 같은지는 확인할 수 없으므로
# (첫 실전 호출) 사슬을 둔다 — 하나라도 살아 있으면 미국 종목은 측정된다.
# 교차 검증: 2026-08-28 기준 Treasury 4.73% · ^TNX 4.72% (일치).
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
FRED_API = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id=DGS10&file_type=json&sort_order=desc&limit=10&api_key=")
TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
TNX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX"
MARKET_BY_SUFFIX = {".KS": "KTB10", ".T": "JGB10", ".TW": "TW10", ".SR": "SA10"}
RATE_UNIMPLEMENTED = {"KTB10": "한국 10년물 미배선(v1)", "JGB10": "일본 10년물 미배선(v1)",
                      "TW10": "대만 10년물 미배선(v1)", "SA10": "사우디 10년물 미배선(v1)"}
# 10년 복리 가정의 보수 상한. 연 20% 를 10년 복리하면 6.2배다 — 그 이상을
# 10년 내내 가정하는 것은 전망이 아니라 소원이다. 넘으면 **캡을 씌우되 원값을
# 함께 적는다**(감춘 조정은 조정이 아니다).
G_CAP = 0.20
ZONE_PASS, ZONE_PROVE, ZONE_INFERIOR = "pass", "prove_growth", "bond_inferior"
ZONE_UNTESTED = "untested"


def fetch_price(ticker, tries=3):
    """Yahoo v8 chart — 전 거래소 공용(.KS/.T/.TW/.SR 포함). (가격, 통화)"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if isinstance(price, (int, float)) and price > 0:
                return float(price), meta.get("currency") or ""
        except Exception:
            pass
        if attempt < tries - 1:
            time.sleep(1.2 * (attempt + 1))
    return None, ""


def fetch_trailing_pe(ticker, key, used_key_box):
    """Finnhub 후행 P/E — 무료 플랜은 사실상 미국 상장분만 준다"""
    url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={key}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        time.sleep(1.1)          # 무료 60 call/min 준수 (fetch_calendar.py 와 같은 보폭)
        if r.status_code != 200:
            return None
        metric = (r.json() or {}).get("metric") or {}
    except Exception:
        return None
    for k in PE_KEYS:
        v = metric.get(k)
        if isinstance(v, (int, float)) and v > 0:
            if not used_key_box:
                used_key_box.append(k)
            return float(v)
    return None


def market_of(ticker):
    """티커 접미사로 상장 시장의 10년물 이름을 고른다 (접미사 없으면 미국)."""
    for suffix, market in MARKET_BY_SUFFIX.items():
        if (ticker or "").upper().endswith(suffix):
            return market
    return "UST10"


def _num(x):
    """숫자 · {"value": 숫자} · None 을 float|None 으로. **0 치환하지 않는다.**

    NaN·무한대는 값이 아니다 — 통과시키면 비교·min 이 조용히 어긋난다.
    """
    if isinstance(x, dict):
        x = x.get("value")
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(float(x)) else None


def parse_fred_csv(text):
    """fredgraph.csv 의 마지막 관측치. 휴일·주말은 '.' 이라 건너뛴다."""
    last = None
    for line in (text or "").splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        try:
            last = (parts[0], float(parts[1]))
        except ValueError:
            continue          # '.' = 그날 고시 없음 (주말·공휴일)
    return last


def parse_treasury_csv(text):
    """Treasury 일별 수익률곡선 CSV 에서 '10 Yr' 최신값. 최신 행이 맨 위다."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    try:
        col = header.index("10 Yr")
    except ValueError:
        return None
    for ln in lines[1:]:
        cells = [c.strip().strip('"') for c in ln.split(",")]
        if len(cells) <= col:
            continue
        try:
            return (cells[0], float(cells[col]))
        except ValueError:
            continue
    return None


def _get(url, timeout):
    """(응답, 코드) — 실패해도 예외를 밖으로 내보내지 않는다. 코드가 있으면 '닿긴 했다'."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        return r, r.status_code
    except Exception as e:
        print(f"[시차] 10년물 요청 실패 {url.split('/')[2]} ({type(e).__name__})",
              file=sys.stderr)
        return None, None


def _src_fred_api(api_key):
    if not api_key:
        return None, None, ""
    r, code = _get(FRED_API + api_key, 15)
    if not r or code != 200:
        return None, code, ""
    for o in ((r.json() or {}).get("observations") or []):   # sort_order=desc
        try:
            return float(o.get("value")), code, o.get("date", "")
        except (TypeError, ValueError):
            continue                                          # '.' = 고시 없는 날
    return None, code, ""


def _src_fred_csv(_):
    r, code = _get(FRED_CSV, 10)
    if not r or code != 200:
        return None, code, ""
    got = parse_fred_csv(r.text)
    return (got[1], code, got[0]) if got else (None, code, "")


def _src_treasury(_):
    year = datetime.now(KST).strftime("%Y")
    r, code = _get(TREASURY_CSV.format(year=year), 15)
    if not r or code != 200:
        return None, code, ""
    got = parse_treasury_csv(r.text)
    return (got[1], code, got[0]) if got else (None, code, "")


def _src_tnx(_):
    r, code = _get(TNX_URL, 15)
    if not r or code != 200:
        return None, code, ""
    try:
        meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
        v = meta.get("regularMarketPrice")
        return (float(v) if isinstance(v, (int, float)) and v > 0 else None), code, ""
    except Exception:
        return None, code, ""


RATE_SOURCES = [("FRED API", _src_fred_api), ("FRED CSV", _src_fred_csv),
                ("Treasury.gov", _src_treasury), ("Yahoo ^TNX", _src_tnx)]


def fetch_ust10(api_key=""):
    """미국 10년물(%) — (값, outcome, code, 관측일, 출처).

    outcome 은 fetch_status 원장 규약 그대로: ok · zero · http_error.
      ok         = 어느 한 곳에서든 숫자를 얻었다
      zero       = 어딘가는 응답했는데 숫자가 하나도 없었다
      http_error = 전 사슬이 응답조차 못 받았다
    '조용한 날'과 '죽은 소스'를 가르는 그 규약 그대로다.
    """
    last_code, reached = None, False
    for name, fn in RATE_SOURCES:
        val, code, obs_day = fn(api_key)
        if code is not None:
            reached, last_code = True, code
        if val is not None and val > 0:
            return val, "ok", code, obs_day, name
    return None, ("zero" if reached else "http_error"), last_code, "", ""


def earnings_yield(eps_ttm, price):
    """ey = 조정 TTM EPS ÷ 주가. 분기 EPS 를 4배 하지 않는다 — 그건 다른 수다."""
    eps_ttm, price = _num(eps_ttm), _num(price)
    if eps_ttm is None or price is None or price <= 0:
        return None
    return eps_ttm / price


def _rate(x):
    """성장률 한 칸 읽기 — **단위를 명시적으로 다룬다.**

      {"value": 13.0, "unit": "%"} → 0.13   (사람 판단층 관례: 퍼센트로 적는다)
      {"value": 0.13}  · 0.13              → 0.13 (자동층 관례: 소수)

    단위를 추측하지 않는 이유: 13.0 을 소수로 읽으면 1300% 가 되고, 그러면
    20% 캡이 그 오류를 조용히 20% 로 덮어버린다 — 방지선이 오류를 가려주는
    최악의 형태다. 그래서 사람 값은 unit 을 반드시 적고, 여기서 그것만 믿는다.
    (자동층 값은 NVDA 1.62 처럼 1 을 넘는 정상 소수가 있어 크기로는 못 가른다)
    """
    if isinstance(x, dict):
        v = _num(x.get("value"))
        if v is None:
            return None
        return v / 100.0 if str(x.get("unit", "")).strip() == "%" else v
    return _num(x)


def pick_g(cagr3y, forward):
    """g_used = 둘 중 **작은** 쪽. 하나라도 없으면 null — 낙관 단일값 금지.

    NaN 방어가 여기 있는 이유: min(x, NaN) 은 파이썬에서 **x 를 돌려준다.**
    전망치가 NaN 으로 들어오면 "둘 다 있어야 한다"는 규칙이 조용히 무너지고
    과거 CAGR 단독으로 결론이 나간다 (2026-09-02 실사고).
    """
    a, b = _rate(cagr3y), _rate(forward)
    if a is None or b is None:
        return None
    return min(a, b)


def cap_g(g):
    """상한 20% 캡 → (적용값, 원값|None).

    원값을 함께 돌려주는 이유: 캡을 씌웠다는 사실이 화면에서 사라지면 그건
    조정이 아니라 은폐다. 표기는 "g 20% 캡(전망 34%)" 처럼 둘 다 적는다.
    """
    v = _num(g)
    if v is None:
        return None, None
    return (G_CAP, v) if v > G_CAP else (v, None)


def coupon_10y(ey, g):
    """10년 뒤 쿠폰 = ey × (1+g)^10. g 가 null 이면 성장을 0으로 가정하지 않는다."""
    ey, g = _num(ey), _num(g)
    if ey is None or g is None:
        return None
    return ey * ((1.0 + g) ** 10)


def zone_of_buffett(coupon, rate_pct):
    """채권 대비 판정. rate_pct 는 % 표기(4.25), 비교는 소수로 환산해 한다."""
    r = _num(rate_pct)
    c = _num(coupon)
    if c is None or r is None or r <= 0:
        return ZONE_UNTESTED
    r = r / 100.0
    if c >= 3.0 * r:
        return ZONE_PASS
    if c >= 1.5 * r:
        return ZONE_PROVE
    return ZONE_INFERIOR


def pass_price(eps_ttm, g, rate_pct):
    """버핏존이 열리는 가격 — 쿠폰이 국채×3 과 같아지는 주가.

        coupon = (eps/price)×(1+g)^10 = 3r  →  price = eps×(1+g)^10 ÷ (3r)

    목표가가 아니라 **지금 눈금으로 계산한 산술**이다. g 가정이 바뀌면 이 가격도
    바뀐다 — 그래서 화면에 g 를 늘 함께 적는다.
    """
    eps, gg, r = _num(eps_ttm), _num(g), _num(rate_pct)
    if eps is None or gg is None or r is None or r <= 0 or eps <= 0:
        return None
    return round(eps * ((1.0 + gg) ** 10) / (3.0 * (r / 100.0)), 2)


def classify_cause(prev, now):
    """존이 바뀐 원인 — price · rate · scale.

    scale = 잰 자가 바뀐 것(EPS 취재·성장률 갱신·가드 토글). 시장 사건이 아니므로
    관측노트는 이걸 기록하지 않는다 (기존 fingerprint 규율과 같은 뿌리 —
    2026-08-17 AMZN 사고: 주가가 1원도 안 움직였는데 눈금이 바뀌어 전이가 찍혔다).
    price·rate 는 둘 다 움직였을 때 로그 변화폭이 큰 쪽을 원인으로 본다.
    """
    if not isinstance(prev, dict):
        return None
    for k in ("eps_adj_ttm", "g_used", "guard"):
        if prev.get(k) != now.get(k):
            return "scale"
    p0, p1 = _num(prev.get("price")), _num(now.get("price"))
    r0, r1 = _num(prev.get("rate10y")), _num(now.get("rate10y"))
    dp = abs(math.log(p1 / p0)) if p0 and p1 and p0 > 0 and p1 > 0 else 0.0
    dr = abs(math.log(r1 / r0)) if r0 and r1 and r0 > 0 and r1 > 0 else 0.0
    if dp == 0.0 and dr == 0.0:
        return None
    return "price" if dp >= dr else "rate"


def measure_bench(c, price, rates, prev_bench=None):
    """레전드벤치마크 한 종목 — **순수 함수**(네트워크·시각 의존 없음).

    판단층에 없는 칸은 null 로 남긴다. 0 은 '쟀더니 0' 이라는 뜻이라 쓰지 않는다.
    """
    b = c.get("buffett") or {}
    market = market_of(c.get("ticker", ""))
    rate = _num((rates or {}).get(market))
    guard = bool(b.get("cyclical_peak_guard"))

    eps_ttm = _num(b.get("eps_adj_ttm"))
    roe = _num(b.get("roe_tangible"))
    g_raw = pick_g(b.get("g_cagr3y"), b.get("g_forward"))
    g, g_capped_from = cap_g(g_raw)
    # 전망만으로 판정에 이른 경우 — 과거 실적으로 교차 검증되지 않은 가정이다.
    # (현행 pick_g 는 둘 다 요구하므로 평시엔 켜지지 않는다. 규칙이 완화되는 날을
    #  대비한 표식이며, 켜지면 화면이 '가정 약함'이라고 말한다.)
    g_weak = bool(g is not None and _rate(b.get("g_cagr3y")) is None
                  and _rate(b.get("g_forward")) is not None)
    ey = earnings_yield(eps_ttm, price)

    out = {
        "market": market, "rate10y": rate, "guard": guard,
        "eps_adj_ttm": eps_ttm, "roe_tangible": roe, "g_used": g, "price": _num(price),
        "g_capped_from": g_capped_from, "g_weak": g_weak,
        "ey": None if ey is None else round(ey, 6),
        "ey_minus_10y": None if (ey is None or rate is None) else round(ey - rate / 100.0, 6),
        "roe_minus_2x10y": (None if (roe is None or rate is None)
                            else round(roe - 2.0 * rate / 100.0, 6)),
        "coupon10y": None, "zone_buffett": None, "cause": None, "note": "",
    }

    if guard:
        # 정점 이익에서 수익률이 가장 좋아 보이는 함정 — coupon10y 를 산출하지 않는다.
        # coupon10y 가 없으면 존은 규칙상 untested 다(= '아직 시험하지 않았다').
        # 별도의 None 상태를 만들지 않는 이유: 못 잰 것은 전부 untested 한 칸에
        # 모아야 "쟀는데 결론이 없다"와 헷갈리지 않는다. 사유는 note 로 남긴다.
        out["zone_buffett"] = ZONE_UNTESTED
        out["note"] = "시클리컬 정점 가드 — coupon10y 미산출"
        return out

    if rate is None:
        out["note"] = RATE_UNIMPLEMENTED.get(market, f"{market} 미배선")
    coupon = coupon_10y(ey, g)
    out["coupon10y"] = None if coupon is None else round(coupon, 6)
    out["zone_buffett"] = zone_of_buffett(coupon, rate)
    if out["zone_buffett"] == ZONE_UNTESTED and not out["note"]:
        out["note"] = ("eps_adj_ttm 미취재 — 분기 EPS 연환산 금지" if eps_ttm is None
                       else ("성장률 미취재(3y CAGR·전망 중 결측)" if g is None
                             else "10년물 없음"))
    out["pass_price"] = pass_price(eps_ttm, g, rate)
    out["cause"] = classify_cause(prev_bench, out)
    return out


def measure_gap(c, price, trailing_pe):
    """기존 괴리 측정 — **레전드벤치마크와 완전히 분리된 경로.**

    정당 MAX 괴리는 forward_eps(없으면 후행 P/E)로만 잰다. buffett 블록의
    eps_adj 는 여기 절대 섞이지 않는다 — 자가 다르면 같은 눈금에 못 올린다.
    반환: (pe, gap, basis, zoned, note)
    """
    fair_max = c.get("fair_max")
    pe, basis = None, "미취재"
    fwd_eps = c.get("forward_eps")
    if isinstance(fwd_eps, (int, float)) and not isinstance(fwd_eps, bool) and fwd_eps > 0:
        pe = _num(price) / float(fwd_eps) if _num(price) else None
        basis = "forward"
    elif isinstance(trailing_pe, (int, float)) and trailing_pe > 0:
        pe, basis = float(trailing_pe), "trailing"
    if pe is None or pe <= 0 or fair_max is None:
        return None, None, basis, False, "EPS 취재 전 — 괴리 미산출"
    gap = float(fair_max) / pe - 1.0
    # 존 판정 자격 — 두 관문을 모두 통과해야 한다 (규칙은 여기 한 곳에만 둔다)
    #  ① 선행 기준일 것: 정당 MAX 는 '선행' 눈금으로 매긴 자다. 후행 P/E 는
    #     성장주·사이클 저점에서 구조적으로 높게 잡혀 같은 자에 대면 자동으로
    #     고평가로 쏠린다 (2026-08-17 실측: 후행 16종 중 양수 1종뿐).
    #  ② 시클리컬이 아닐 것: 시클리컬은 '정점 이익'에서 P/E 가 가장 낮게 찍혀
    #     제일 싸 보인다. 정점 EPS × 사이클 멀티플은 가짜 신호다 — 괴리는
    #     계산해 보여주되 존에는 올리지 않는다.
    cyclical = "시클리컬" in (c.get("type") or "")
    return round(pe, 2), round(gap, 4), basis, (basis == "forward" and not cyclical), ""


def load_prev():
    """이전 회차 산출물 — 금리 전일값 폴백과 cause 비교의 근거."""
    if not OUT_PATH.exists():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def prev_bench_map(prev):
    """{ticker: 지난 회차 bench} — 없으면 빈 dict."""
    out = {}
    for x in (prev.get("items") or []):
        if isinstance(x, dict) and isinstance(x.get("bench"), dict):
            out[x.get("ticker")] = x["bench"]
    return out


def append_history(history, ticker, day, gap, bench=None):
    """하루 1점 · 같은 날은 덮어쓰기(멱등) · 90일 보관.

    점을 만드는 조건은 **예전 그대로 gap 이 있을 때만**이다. 레전드벤치마크는
    기존 점에 칸을 얹기만 한다 — 시계열의 점 집합을 바꾸면 기존 스파크라인이
    (gap 이 null 인 점을 만나) 조용히 깨진다. 기존 값 보존이 먼저다.
    """
    if gap is None:
        return
    series = [p for p in history.get(ticker, []) if p.get("d") != day]
    point = {"d": day, "gap": round(gap, 4)}
    if isinstance(bench, dict):
        point["coupon10y"] = bench.get("coupon10y")
        point["zone_buffett"] = bench.get("zone_buffett")
    series.append(point)
    series.sort(key=lambda p: p["d"])
    history[ticker] = series[-HISTORY_DAYS:]


def collect_rates(prev_rates):
    """10년 금리 묶음 — 미국만 실측, 나머지는 v1 미배선(null).

    실패하면 **전일값을 유지**한다(주말·공휴일 공백 포함). 다만 폴백을 탔다는
    사실은 반드시 로그에 남긴다 — 조용한 폴백은 검증한 줄 착각하게 만든다.
    """
    rates = {m: None for m in MARKET_BY_SUFFIX.values()}
    prev_rates = prev_rates if isinstance(prev_rates, dict) else {}
    val, outcome, code, obs_day, src = fetch_ust10(os.environ.get("FRED_API_KEY", "").strip())
    note = ""
    if val is None:
        val = _num(prev_rates.get("UST10"))
        src = prev_rates.get("source") or ""
        if val is None:
            note = f"UST10 취득 실패({outcome}) — 전일값도 없음 → 미국 종목도 untested"
        else:
            # 폴백을 탔다는 사실은 반드시 시끄럽게 남긴다 (조용한 폴백 = 가짜 정상)
            note = f"UST10 취득 실패({outcome}) — 전일값 {val}% 유지 (폴백)"
        print(f"[시차] {note}", file=sys.stderr)
    else:
        print(f"[시차] UST10 {val}% ({src} {obs_day})")
    rates["UST10"] = val
    rates["source"] = src
    rates["as_of"] = obs_day or prev_rates.get("as_of") or ""
    rates["note"] = note or "한국·일본·대만·사우디 10년물은 v1 미배선 — 해당 시장은 untested"

    try:                      # 원장 기록 — 관제탑이 죽은 금리 소스를 볼 수 있게
        import feed_client
        feed_client.record("rates", "ust10", outcome, code, 1 if val is not None else 0)
        feed_client.flush()
    except Exception as e:
        print(f"[시차] fetch_status 기록 실패 ({e})", file=sys.stderr)
    return rates, outcome


def main():
    if not CFG_PATH.exists():
        print("[시차] buffett_config.json 없음 — 판단층이 있어야 측정한다", file=sys.stderr)
        return 1
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    # 판단층은 두 겹(기계 + 사람)이다. 병합 규칙은 buffett_layers 한 곳에만 둔다 —
    # 여기서 다시 해석하면 표시층과 어긋나 같은 종목이 두 얼굴을 갖게 된다.
    try:
        import buffett_layers
        items_cfg = buffett_layers.merged_items(cfg)
        t = buffett_layers.origin_tally(items_cfg)
        print(f"[시차] 판단층 병합 — 사람 {t['human']}칸 · 자동 {t['auto']}칸 · 빈칸 {t['none']}")
    except Exception as e:
        items_cfg = cfg.get("items", [])
        print(f"[시차] 판단층 병합 실패 → 사람 판단층만 사용 ({e})", file=sys.stderr)
    fh_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not fh_key:
        print("[시차] FINNHUB_API_KEY 없음 — 후행 P/E 폴백 생략 (forward_eps 있는 종목만 측정)")

    now = datetime.now(KST)
    day = now.strftime("%m-%d")
    prev = load_prev()
    history = prev.get("history") or {}
    prev_bench = prev_bench_map(prev)
    rates, rate_outcome = collect_rates(prev.get("rates"))
    used_key_box = []
    out_items = []
    n_measured = n_skipped = n_unpriced = 0

    for c in items_cfg:
        ticker = c.get("ticker", "")
        row = {"ticker": ticker, "name": c.get("name", ""), "type": c.get("type", ""),
               "fair_max": c.get("fair_max"), "rationale": c.get("rationale", ""),
               "eps_asof": c.get("eps_asof"), "price": None, "currency": "",
               "pe": None, "gap": None, "basis": "미취재", "note": "", "zoned": False,
               "cyclical": ("시클리컬" in (c.get("type") or "")),
               "bench": measure_bench(c, None, rates, prev_bench.get(ticker))}

        # ① 원칙상 재지 않는 바구니 — 시세도 부르지 않는다
        basis = TYPE_BASIS.get(c.get("type"))
        if basis:
            row["basis"] = basis
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 측정 제외 ({c.get('type')}) — 원칙 바구니")
            continue

        if row["fair_max"] is None:
            row["note"] = "정당 MAX 미지정"
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 정당 MAX 없음 — 건너뜀")
            continue

        # ② 시세
        price, currency = fetch_price(ticker)
        row["price"], row["currency"] = price, currency
        if price is None:
            row["note"] = "시세 취득 실패"
            out_items.append(row)
            n_unpriced += 1
            print(f"[시차] {ticker}: 시세 실패 — 괴리 미산출", file=sys.stderr)
            continue

        # ③ 레전드벤치마크 — 시세가 잡힌 뒤 한 번만 계산한다 (측정층 단일 산출)
        row["bench"] = measure_bench(c, price, rates, prev_bench.get(ticker))

        # ④ P/E — forward 우선, 없으면 후행 폴백. **buffett 블록은 여기 안 쓴다.**
        trailing = None
        fwd_eps = c.get("forward_eps")
        if not (isinstance(fwd_eps, (int, float)) and not isinstance(fwd_eps, bool)
                and fwd_eps > 0) and fh_key:
            trailing = fetch_trailing_pe(ticker, fh_key, used_key_box)
        pe, gap, basis, zoned, note = measure_gap(c, price, trailing)
        row["basis"] = basis
        if gap is None:
            row["note"] = note
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 미취재 (P/E 없음)")
            continue

        row["pe"] = pe
        row["gap"] = gap
        row["zoned"] = zoned
        out_items.append(row)
        append_history(history, ticker, day, gap, row["bench"])
        n_measured += 1
        cp = row["bench"].get("coupon10y")
        cp_txt = "—" if cp is None else f"{cp * 100:.2f}%"
        print(f"[시차] {ticker}: P/E {row['pe']} ({row['basis']}) "
              f"· 정당 {row['fair_max']} → 괴리 {gap * 100:+.1f}% "
              f"| 벤치 {row['bench'].get('zone_buffett')} (coupon10y {cp_txt})")
        time.sleep(0.2)

    # 측정 못 한 티커의 과거 흔적은 남기되, config에서 빠진 티커는 정리
    live = {c.get("ticker") for c in items_cfg}
    history = {k: v for k, v in history.items() if k in live}

    payload = {
        "generated_label": now.strftime("%Y-%m-%d %H:%M"),
        "asof": cfg.get("asof", ""),
        "zones": cfg.get("zones", {"explore": 0.60, "commit": 0.40}),
        "rates": rates,
        "items": out_items,
        "history": history,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if used_key_box:
        print(f"[시차] Finnhub 후행 P/E 채택 키: {used_key_box[0]}")
    print(f"[시차] 측정 {n_measured}종 / 원칙·미취재 제외 {n_skipped}종 / "
          f"시세실패 {n_unpriced}종 (전체 {len(items_cfg)}종)")
    zone_tally = {}
    for x in out_items:      # 전 종목 집계 — 원칙 바구니·미취재까지 포함해야 대조가 된다
        z = (x.get("bench") or {}).get("zone_buffett")
        zone_tally[z] = zone_tally.get(z, 0) + 1
    tally = ", ".join(f"{k or '미산출(가드)'} {v}종" for k, v in sorted(
        zone_tally.items(), key=lambda kv: str(kv[0])))
    print(f"[시차] 레전드벤치마크 — UST10 {rates.get('UST10')}% "
          f"({rate_outcome}, 출처 {rates.get('source') or '없음'}) · {tally or '대상 없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
