#!/usr/bin/env python3
"""
금리·원자재 공용 수집기 (fred_client) — FRED 우선 사슬 한 벌
================================================================================
2026-09-26 헤더 시장 지표 띠를 넣으며 fetch_buffett.py 에서 떼어냈다. 레전드의 10년물과
헤더의 10년물이 **같은 함수·같은 사슬**을 타야 두 화면이 다른 10년물을 보이지 않는다
(중복 구현 금지). 호출부:
  fetch_data.collect_macro  — 헤더 띠(UST10·UST30·WTI) — 회차의 **유일한 측정점**
  fetch_buffett             — 이 모듈의 파서·사슬을 그대로 다시 내보낸다(구 이름 호환)

사슬(국채): FRED API(키 있을 때) → FRED CSV → Treasury.gov → Yahoo(^TNX/^TYX)
사슬(WTI) : FRED API → FRED CSV   (DCOILWTICO 는 **전일 종가** 시리즈다)

outcome 규약은 fetch_status 원장 그대로:
  ok         = 어느 한 곳에서든 숫자를 얻었다
  zero       = 어딘가는 응답했는데 숫자가 하나도 없었다 (내용 기준)
  http_error = 전 사슬이 응답조차 못 받았다
"""
import sys
from datetime import datetime, timezone, timedelta

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 2026-08-30 실측: fred.stlouisfed.org(CSV 호스트)는 키 없이 ReadTimeout 이 잦고,
# api.stlouisfed.org 는 키가 있으면 정상 응답한다. 그래서 키가 있으면 API 가 1순위다.
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_API = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id={sid}&file_type=json&sort_order=desc&limit=10&api_key=")
TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

# 만기별 사슬 재료 — FRED 시리즈 · Treasury CSV 열 이름 · Yahoo 지수
TENORS = {10: ("DGS10", "10 Yr", "%5ETNX"),
          30: ("DGS30", "30 Yr", "%5ETYX")}
WTI_SERIES = "DCOILWTICO"


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


def parse_treasury_csv(text, col="10 Yr"):
    """Treasury 일별 수익률곡선 CSV 에서 해당 만기 열의 최신값. 최신 행이 맨 위다."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    try:
        idx = header.index(col)
    except ValueError:
        return None
    for ln in lines[1:]:
        cells = [c.strip().strip('"') for c in ln.split(",")]
        if len(cells) <= idx:
            continue
        try:
            return (cells[0], float(cells[idx]))
        except ValueError:
            continue
    return None


def _get(url, timeout):
    """(응답, 코드) — 실패해도 예외를 밖으로 내보내지 않는다. 코드가 있으면 '닿긴 했다'."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        return r, r.status_code
    except Exception as e:
        print(f"[FRED사슬] 요청 실패 {url.split('/')[2]} ({type(e).__name__})",
              file=sys.stderr)
        return None, None


def _src_fred_api(sid, api_key):
    if not api_key:
        return None, None, ""
    r, code = _get(FRED_API.format(sid=sid) + api_key, 15)
    if not r or code != 200:
        return None, code, ""
    for o in ((r.json() or {}).get("observations") or []):   # sort_order=desc
        try:
            return float(o.get("value")), code, o.get("date", "")
        except (TypeError, ValueError):
            continue                                          # '.' = 고시 없는 날
    return None, code, ""


def _src_fred_csv(sid):
    r, code = _get(FRED_CSV.format(sid=sid), 10)
    if not r or code != 200:
        return None, code, ""
    got = parse_fred_csv(r.text)
    return (got[1], code, got[0]) if got else (None, code, "")


def _src_treasury(col):
    year = datetime.now(KST).strftime("%Y")
    r, code = _get(TREASURY_CSV.format(year=year), 15)
    if not r or code != 200:
        return None, code, ""
    got = parse_treasury_csv(r.text, col)
    return (got[1], code, iso_day(got[0])) if got else (None, code, "")


def iso_day(s):
    """관측일 표기를 YYYY-MM-DD 로 — Treasury CSV 는 MM/DD/YYYY 로 준다.
    출처마다 날짜 모양이 다르면 '전일' 판정·표기 비교가 조용히 어긋난다."""
    s = str(s or "").strip()
    try:
        return datetime.strptime(s, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return s


def _src_yahoo(sym):
    r, code = _get(YAHOO_CHART.format(sym=sym), 15)
    if not r or code != 200:
        return None, code, ""
    try:
        meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
        v = meta.get("regularMarketPrice")
        return (float(v) if isinstance(v, (int, float)) and v > 0 else None), code, ""
    except Exception:
        return None, code, ""


def run_chain(sources):
    """[(이름, 호출)] 을 앞에서부터 — (값, outcome, 코드, 관측일, 출처)."""
    last_code, reached = None, False
    for name, fn in sources:
        val, code, obs_day = fn()
        if code is not None:
            reached, last_code = True, code
        if val is not None and val > 0:
            return val, "ok", code, obs_day, name
    return None, ("zero" if reached else "http_error"), last_code, "", ""


def fetch_ust(tenor=10, api_key=""):
    """미국 국채 금리(%) — (값, outcome, 코드, 관측일, 출처)."""
    sid, col, sym = TENORS[tenor]
    return run_chain([("FRED API", lambda: _src_fred_api(sid, api_key)),
                      ("FRED CSV", lambda: _src_fred_csv(sid)),
                      ("Treasury.gov", lambda: _src_treasury(col)),
                      (f"Yahoo {sym.replace('%5E', '^')}", lambda: _src_yahoo(sym))])


def fetch_ust10(api_key=""):
    """레전드 구 이름 호환 — fetch_ust(10)."""
    return fetch_ust(10, api_key)


def fetch_wti(api_key=""):
    """WTI 현물(DCOILWTICO, 달러/배럴) — FRED 전용. 전일 종가 시리즈다."""
    return run_chain([("FRED API", lambda: _src_fred_api(WTI_SERIES, api_key)),
                      ("FRED CSV", lambda: _src_fred_csv(WTI_SERIES))])
