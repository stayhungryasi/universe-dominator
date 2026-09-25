"""
generate_candidates.py — 잠재지배자 후보 100% 순수 규칙 자동 선정

선정 기준은 코드가 아니라 data/latent_criteria.json 에 있습니다.
그 파일의 숫자만 고치면 규칙이 바뀝니다. (아래는 기본값)

  ① 글로벌 시총 순위  글로벌_순위_최소 ~ 글로벌_순위_최대
  ② 1년 주가 모멘텀   모멘텀_1년_최소_퍼센트 이상
  ③ AI 가치사슬 섹터  AI섹터 목록 중 하나 (companiesmarketcap 카테고리)
  ④ 지역 TOP 20 제외  지역TOP20_제외 = true (이미 우주지배자면 제외)
  ⑤ 시총 하한         시총_최소_billion 이상
  ⑥ 최대 후보 수      최대_후보수, 순위순

  data/latent_overrides.json 은 (선택) 종목별 해설/테마를 예쁘게 덮어쓸 뿐,
  선정 자체엔 영향을 주지 않습니다. 없어도 자동 템플릿 해설로 동작.

  ★ 결측 보존 (2026-09-25, 9/13 13종 사고): 종목 페이지 파싱 실패는 '기준 미달'이
    아니라 '모름'이다. 기존 명단 종목이 파싱에 실패하면 전일 카드를 그대로 승계하고
    (carried), 결측_제외_연속일(기본 3) 거래일 연속일 때만 제외한다.
    대량 실패(관측 절반 이상)나 유니버스 수집 실패는 개별 결측이 아니라 장애다 —
    명단을 동결하고 fetch_status 원장에 http_error 로 남긴다(정비 관제탑이 읽는다).

  ★ 관성(히스테리시스, 2026-09-25): 9/10~9/25 에 경계 종목이 하루 단위로 들락거렸다.
    그날 기준을 통과한 순위 상위 14종을 매번 새로 뽑았기 때문이다. 이제 명단은 상태
    (data/latent_state.json)를 갖고, 들어오는 문턱과 나가는 문턱이 다르다.
      편입: 기준 충족 편입_연속_거래일(3) 연속 — 빈자리가 있을 때
      교체: 만석이면 가장 약한 멤버보다 교체_순위_격차(10)계단 이상 앞선 날이
            교체_연속_거래일(5) 연속일 때만. 그 전까지는 상태 파일에만 '대기'
      제외: 기준 미달 제외_연속_거래일(5) 연속 (순위·모멘텀·시총·TOP20 전부)
    거래일은 KST 평일. 하루 3회 full 이 돌아도 카운트는 거래일당 한 번만 움직인다.
    latent_overrides.json 은 여전히 해설·테마만 덮어쓴다 — 선정에는 관여하지 않는다.

  데모:      python generate_candidates.py --demo
  미리보기:  python generate_candidates.py --preview
  라이브:    python generate_candidates.py
"""
import sys
import copy
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ───────────────────────── 경로 ─────────────────────────
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
LATEST_PATH = DATA_DIR / "latest.json"
CRITERIA_PATH = DATA_DIR / "latent_criteria.json"
OVERRIDES_PATH = DATA_DIR / "latent_overrides.json"
PREVIEW_PATH = DATA_DIR / "latent_auto_preview.json"
STATE_PATH = DATA_DIR / "latent_state.json"
STATE_VERSION = 1
TRANSITIONS_KEEP = 200   # 상태 파일에 남길 확정 전이 수(주간 이력이 읽는다)

MAX_AUTO_FETCH = 60    # 종목 페이지 받을 최대 개수(부하 제한)
FETCH_DELAY = 1.0
MASS_FAIL_RATIO = 0.5  # 관측 중 파싱 실패가 이 비율 이상이면 장애 — 명단 동결
LEDGER_LABEL = "잠재지배자 선정"   # fetch_status 원장 키: latent:잠재지배자 선정

BASE = "https://companiesmarketcap.com"
# 기준파일의 섹터 약칭 → (표시 테마, 카테고리 URL)
SECTOR_MAP = {
    "반도체":     ("AI 반도체",      BASE + "/semiconductors/largest-semiconductor-companies-by-market-cap/"),
    "소프트웨어": ("AI 소프트웨어",  BASE + "/software/largest-software-companies-by-market-cap/"),
    "AI":         ("AI",            BASE + "/artificial-intelligence/largest-ai-companies-by-marketcap/"),
    "테크":       ("AI 테크",        BASE + "/tech/largest-tech-companies-by-market-cap/"),
    "전력":       ("AI 전력 인프라", BASE + "/electricity/largest-electricity-companies-by-market-cap/"),
    "인터넷":     ("AI 인터넷",      BASE + "/internet/largest-internet-companies-by-market-cap/"),
}

DEFAULT_CRITERIA = {
    "글로벌_순위_최소": 21,
    "글로벌_순위_최대": 200,
    "모멘텀_1년_최소_퍼센트": 80,
    "시총_최소_billion": 70,
    "최대_후보수": 14,
    "지역TOP20_제외": True,
    "AI섹터": ["반도체", "소프트웨어", "AI", "테크", "전력", "인터넷"],
    "결측_제외_연속일": 3,
    "편입_연속_거래일": 3,
    "제외_연속_거래일": 5,
    "교체_순위_격차": 10,
    "교체_연속_거래일": 5,
    "신규_표시_일수": 7,
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"}

FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
MC_RE = re.compile(r"[\d.]+\s*[TB]")
URL_RE = re.compile(r"/[^/]+/marketcap/?$")
COUNTRY_RE = re.compile(r"([\U0001F1E6-\U0001F1FF]{2})\s+[\w .&'\-]+?\s+Country\b")
RANK_RE = re.compile(r"#(\d+)\s+Rank")
PAGE_MC_RE = re.compile(r"\$\s*([\d.]+)\s*([TB])\s+Marketcap")
Y1_RE_A = re.compile(r"(-?\d+(?:\.\d+)?)\s*%\s*Change\s*\(1\s*year\)", re.I)
Y1_RE_B = re.compile(r"Change\s*\(1\s*year\)\s*(-?\d+(?:\.\d+)?)\s*%", re.I)

SUFFIX_FLAG = {".TW": "🇹🇼", ".TWO": "🇹🇼", ".T": "🇯🇵", ".HK": "🇭🇰", ".SS": "🇨🇳", ".SZ": "🇨🇳",
               ".L": "🇬🇧", ".KS": "🇰🇷", ".KQ": "🇰🇷", ".DE": "🇩🇪", ".PA": "🇫🇷",
               ".SW": "🇨🇭", ".AS": "🇳🇱", ".TO": "🇨🇦", ".SR": "🇸🇦", ".MI": "🇮🇹",
               ".MC": "🇪🇸", ".ST": "🇸🇪", ".HE": "🇫🇮",
               # 2026-07 DELTA.BK 국기 누락 수리 — 잠재 후보권(30~200위) 거래소 보강
               ".BK": "🇹🇭", ".NS": "🇮🇳", ".BO": "🇮🇳", ".AX": "🇦🇺", ".OL": "🇳🇴",
               ".CO": "🇩🇰", ".BR": "🇧🇪", ".VI": "🇦🇹", ".SA": "🇧🇷", ".JK": "🇮🇩",
               ".KL": "🇲🇾", ".SI": "🇸🇬", ".IS": "🇹🇷", ".TA": "🇮🇱", ".JO": "🇿🇦"}


def suffix_flag(ticker):
    tk = (ticker or "").upper()
    for suf, fl in SUFFIX_FLAG.items():
        if tk.endswith(suf):
            return fl
    return "🇺🇸" if tk and "." not in tk else None


def load_criteria():
    c = dict(DEFAULT_CRITERIA)
    if CRITERIA_PATH.exists():
        try:
            c.update(json.loads(CRITERIA_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[warn] latent_criteria.json 읽기 실패 → 기본값 사용 ({e})", file=sys.stderr)
    return c


def is_trading_day(day):
    """KST 평일만 거래일로 센다. 주말 회차는 시세가 멈춰 있어 카운트를 움직이지 않는다."""
    return datetime.strptime(day, "%Y-%m-%d").weekday() < 5


def ledger(outcome, items):
    """선정 결과를 fetch_status 원장에 남긴다 — **내용 기준**(파싱 결과), 파일 시각 아님.

    ok: 기준 통과 종목 있음 · zero: 수집은 됐으나 통과 0 · http_error: 수집 자체가 무너짐.
    원장 기록 실패가 선정을 막지는 않는다(관측은 관측, 기록은 기록).
    """
    try:
        import feed_client
        feed_client.record("latent", LEDGER_LABEL, outcome, None, items)
        feed_client.flush()
    except Exception as e:
        print(f"[warn] fetch_status 기록 실패 ({e})", file=sys.stderr)


# ───────────────────────── 네트워크 ─────────────────────────
def fetch(url, retries=4, delay=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  [{i+1}/{retries}] HTTP {r.status_code} — {url}", file=sys.stderr)
        except Exception as e:
            last = str(e)
            print(f"  [{i+1}/{retries}] {e}", file=sys.stderr)
        time.sleep(delay)
    print(f"[fail] {url} ({last})", file=sys.stderr)
    return None


def parse_mc(text):
    s = text.replace("$", "").replace(",", "").strip()
    m = re.search(r"([\d.]+)\s*([TB])", s)
    if not m:
        return 0.0
    v = float(m.group(1))
    return v * 1000 if m.group(2) == "T" else v


def parse_company_list(html, limit=300):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table") or soup
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name_el = tr.find(class_="company-name")
        code_el = tr.find(class_="company-code")
        name = name_el.get_text(strip=True) if name_el else None
        ticker = code_el.get_text(strip=True) if code_el else None
        if not name:
            continue
        mc = 0.0
        for td in tds:
            txt = td.get_text(strip=True)
            if "$" in txt and MC_RE.search(txt):
                mc = parse_mc(txt)
                break
        if mc == 0:
            continue
        link = tr.find("a", href=URL_RE)
        url = None
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else BASE + href
        rows.append({"name": name, "ticker": ticker or "", "mc": round(mc, 2), "url": url})
        if len(rows) >= limit:
            break
    return rows


def scrape_universe(sectors):
    """기준의 AI섹터 카테고리들 → {ticker(대문자): {name, mc, url, theme}}."""
    uni = {}
    for key in sectors:
        if key not in SECTOR_MAP:
            print(f"[warn] 알 수 없는 섹터 '{key}' 건너뜀", file=sys.stderr)
            continue
        theme, url = SECTOR_MAP[key]
        html = fetch(url)
        if not html:
            continue
        for r in parse_company_list(html, limit=300):
            tk = (r["ticker"] or "").upper()
            if tk and tk not in uni and r.get("url"):
                uni[tk] = {"name": r["name"], "ticker": r["ticker"], "mc": r["mc"],
                           "url": r["url"], "theme": theme}
        time.sleep(1)
    return uni


YCHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"


def fetch_multi_momentum(ticker):
    """최종 통과 종목 한정: Yahoo 차트로 1M/3M/6M 모멘텀(%) — 실패 시 None (표시만 생략)"""
    out = {"m1": None, "m3": None, "m6": None}
    tk = (ticker or "").strip()
    if not tk:
        return out
    try:
        r = requests.get(YCHART.format(sym=tk), headers=HEADERS, timeout=20)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c]
        if len(closes) < 25:
            return out
        last = closes[-1]
        for key, days in (("m1", 21), ("m3", 63), ("m6", 126)):  # 거래일 기준
            if len(closes) > days and closes[-days - 1]:
                out[key] = round((last / closes[-days - 1] - 1) * 100)
    except Exception as e:
        print(f"  [warn] {tk} 다기간 모멘텀 실패({e}) — 1M/3M/6M 표시 생략", file=sys.stderr)
    return out


def stock_stats(row):
    """종목 페이지 → {rank, momentum, flag, mc}."""
    out = {"rank": None, "momentum": None, "flag": None, "mc": None}
    url = row.get("url")
    if not url:
        return out
    html = fetch(url, retries=2, delay=2)
    if not html:
        return out
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = Y1_RE_A.search(text) or Y1_RE_B.search(text)
    if m:
        try:
            out["momentum"] = round(float(m.group(1)))
        except Exception:
            pass
    rm = RANK_RE.search(text)
    if rm:
        out["rank"] = int(rm.group(1))
    fm = COUNTRY_RE.search(text)
    if fm:
        out["flag"] = fm.group(1)
    mm = PAGE_MC_RE.search(text)
    if mm:
        out["mc"] = round(float(mm.group(1)) * (1000 if mm.group(2) == "T" else 1), 2)
    return out


# ───────────────────────── 기준 데이터 ─────────────────────────
def regional_top20_tickers(regions=None):
    """지정 지역들의 TOP 20 티커 집합. regions=None이면 전 지역(과거 동작)."""
    keys = set()
    try:
        data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        for rname, region in data.get("regions", {}).items():
            if regions is not None and rname not in regions:
                continue
            stocks = region.get("stocks", region) if isinstance(region, dict) else region
            for s in (stocks or []):
                tk = (s.get("ticker") or "").upper().strip()
                if tk:
                    keys.add(tk)
    except Exception:
        pass
    return keys


def top20_cutoff(default=480.0):
    try:
        data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        earth = data["regions"]["earth"]
        stocks = earth.get("stocks", earth) if isinstance(earth, dict) else earth
        mcs = [s.get("mc", 0) for s in stocks if s.get("mc")]
        if mcs:
            return min(mcs)
    except Exception:
        pass
    return default


def load_overrides():
    if OVERRIDES_PATH.exists():
        try:
            return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ───────────────────────── 카드 ─────────────────────────
def template_story(c):
    return (f"{c['theme']} · 글로벌 {c['rank']}위 · 1년 {c['momentum_1y']:+}% — "
            f"폭발적 성장 모멘텀의 AI 가치사슬 후보.")


def build_card(c, overrides):
    ov = overrides.get((c["ticker"] or "").upper(), {})   # (선택) 해설/테마 미화
    return {
        "rank": c["rank"], "ticker": c["ticker"], "name": c["name"],
        "country": c["flag"], "mc": c["mc"], "momentum_1y": c["momentum_1y"],
        "momentum_1m": c.get("m1"), "momentum_3m": c.get("m3"), "momentum_6m": c.get("m6"),
        "theme": ov.get("theme", c["theme"]),
        "story": ov.get("story", template_story(c)),
        "auto": True,
    }


# ───────────────────────── 출력 ─────────────────────────
def write_latent(cards):
    data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    data["latent"] = cards
    LATEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] latest.json latent 갱신: {len(cards)}개")
    # 오늘 스냅샷의 latent 도 동기화 → 주간 히스토리 비교가 당일 목록 기준으로 정확해짐
    snap = DATA_DIR / "snapshots" / f"{TODAY.strftime('%Y-%m-%d')}.json"
    if snap.exists():
        try:
            sd = json.loads(snap.read_text(encoding="utf-8"))
            sd["latent"] = [
                {"ticker": c.get("ticker",""), "name": c.get("name",""),
                 "rank": c.get("rank"), "mc": c.get("mc", 0),
                 "momentum_1y": c.get("momentum_1y"), "theme": c.get("theme", "")}
                for c in cards
            ]
            snap.write_text(json.dumps(sd, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] 오늘 스냅샷 latent 동기화: {snap.name}")
        except Exception as e:
            print(f"[warn] 스냅샷 동기화 실패(무시): {e}")


def write_preview(cards):
    PREVIEW_PATH.write_text(
        json.dumps({"generated_at": TODAY.isoformat(), "count": len(cards),
                    "candidates": cards}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[OK] 미리보기 저장: data/latent_auto_preview.json ({len(cards)}개) — 라이브 미반영")


# ───────────────────────── 실행 ─────────────────────────
def run_live(preview=False):
    crit = load_criteria()
    rank_lo = int(crit["글로벌_순위_최소"]); rank_hi = int(crit["글로벌_순위_최대"])
    mom_min = int(crit["모멘텀_1년_최소_퍼센트"]); mc_floor = float(crit["시총_최소_billion"])
    max_n = int(crit["최대_후보수"]); sectors = crit.get("AI섹터", list(SECTOR_MAP.keys()))

    print(f"[generate] {TODAY.isoformat()} ({'미리보기' if preview else '라이브'})")
    print(f"  기준: 순위 {rank_lo}~{rank_hi} · 모멘텀 ≥{mom_min}% · 시총 ≥${mc_floor:.0f}B · 최대 {max_n}개")
    print(f"  섹터: {sectors}")

    overrides = load_overrides()
    # ★ 2026-08 규칙 개정: 지구·미국 TOP 20만 제외. 비미국 지역 챔피언(한국·일본·유럽 등)은
    #   조건 충족 시 편입 — "다른 지역의 왕은 지구의 왕좌에 가장 가까운 도전자"
    excl_regions = crit.get("제외_지역", ["earth", "us"])
    excluded = regional_top20_tickers(excl_regions) if crit.get("지역TOP20_제외", True) else set()
    cutoff = top20_cutoff()

    uni = scrape_universe(sectors)
    if not uni:
        print("[장애] AI 가치사슬 유니버스 수집 실패 — latent 유지(덮어쓰지 않음)")
        ledger("http_error", 0)
        return
    excl_label = "+".join(excl_regions)
    print(f"  AI 가치사슬 유니버스 {len(uni)}개 · 제외({excl_label} TOP20) {len(excluded)}개")
    pool = [c for tk, c in uni.items() if tk not in excluded and mc_floor <= c["mc"] < cutoff]
    pool.sort(key=lambda c: -c["mc"])
    pool = pool[:MAX_AUTO_FETCH]
    print(f"  모멘텀 확인 대상 {len(pool)}개")

    # 기존 멤버는 관측권(시총 상위 60) 밖이어도, TOP20 에 들었어도 반드시 관측한다 —
    # 관측하지 않으면 '미달'과 '모름'을 구분할 수 없다.
    prev_cards = {(c.get("ticker") or "").upper(): c for c in load_prev_latent()}
    state = load_state() or seed_state(prev_cards.values(), crit)
    in_pool = {(c["ticker"] or "").upper() for c in pool}
    watch = [uni[tk] for tk in sorted(state["members"]) if tk in uni and tk not in in_pool]
    if watch:
        print(f"  기존 멤버 추가 관측 {len(watch)}개: {', '.join(c['name'] for c in watch)}")

    obs, rows = {}, {}
    for c in pool + watch:
        tk = (c["ticker"] or "").upper()
        s = stock_stats(c)
        time.sleep(FETCH_DELAY)
        rank, mom = s["rank"], s["momentum"]
        if rank is None or mom is None:
            obs[tk] = {"known": False, "name": c["name"]}
            print(f"  [skip] {c['name']}: 데이터 파싱 실패"); continue
        mc = s["mc"] or c["mc"]
        o = {"known": True, "ok": False, "rank": rank, "momentum_1y": mom, "mc": mc,
             "name": c["name"]}
        obs[tk] = o
        if tk in excluded:
            print(f"  [skip] {c['name']}: {rank}위 ({excl_label} TOP20)")
        elif not (mc_floor <= c["mc"] < cutoff):
            print(f"  [skip] {c['name']}: 시총 ${c['mc']:.0f}B (범위 밖)")
        elif not (rank_lo <= rank <= rank_hi):
            print(f"  [skip] {c['name']}: {rank}위 (범위 밖)")
        elif mom < mom_min:
            print(f"  [skip] {c['name']}: 1Y {mom:+}% (미달)")
        else:
            o["ok"] = True
            print(f"  [pass] {rank}위 {c['name']} ({c['theme']}) 1Y +{mom}%")
        c2 = dict(c)
        c2["rank"] = rank; c2["momentum_1y"] = mom; c2["mc"] = mc
        c2["flag"] = s["flag"] or suffix_flag(c["ticker"]) or "🌐"
        rows[tk] = c2

    unknown = sum(1 for o in obs.values() if not o["known"])
    passed = sum(1 for o in obs.values() if o.get("ok"))
    if obs and unknown >= len(obs) * MASS_FAIL_RATIO:
        print(f"[장애] 파싱 실패 {unknown}/{len(obs)} — 개별 결측이 아니라 수집 붕괴."
              " latent 유지(덮어쓰지 않음)")
        ledger("http_error", passed)
        return
    if not passed:
        print("[정보] 조건 통과 0개 — latent 유지(덮어쓰지 않음)")
        ledger("zero", 0)
        return
    ledger("ok", passed)

    today = TODAY.strftime("%Y-%m-%d")
    state = step(state, obs, today, crit)
    lim = limits(crit)
    for t in state["transitions"]:
        if t.get("date") == today:
            word = "편입" if t.get("dir") == "in" else "제외"
            print(f"  [전이] {t.get('name')} {word} 확정")
    for tk, cnd in sorted(state["candidates"].items(), key=lambda kv: kv[1].get("rank") or 999):
        print(f"  [대기] {cnd.get('name')} {cnd.get('rank')}위 — 편입 {cnd.get('streak_in')}/{lim['in']}"
              f" · 교체 {cnd.get('swap_streak', 0)}/{lim['swap_days']}")

    cards = []
    members = state["members"]
    for tk in sorted(members, key=lambda t: members[t].get("rank") or 999):
        m = members[tk]
        if tk in rows:
            c = rows[tk]
            c.update(fetch_multi_momentum(c["ticker"]))   # 멤버만 조회 (≤최대후보수 회)
            time.sleep(0.5)
            card = build_card(c, overrides)
        elif tk in prev_cards:
            card = {k: v for k, v in prev_cards[tk].items() if k not in STATUS_KEYS}
            card["carried"] = True
            print(f"  [승계] {card.get('name')}: 파싱 실패 → 전일 값 유지 "
                  f"(결측 {m.get('carried_days', 0)}/{lim['miss']})")
        else:
            print(f"  [warn] {m.get('name') or tk}: 관측도 전일 카드도 없음 — 이번 회차 표시 생략")
            continue
        card.update(status_fields(m, today, lim))
        cards.append(card)
    print(f"  최종 {len(cards)}개 (멤버 {len(members)} · 대기 {len(state['candidates'])})")
    if preview:
        write_preview(cards)          # 미리보기는 상태를 전진시키지 않는다
        return
    save_state(state)
    if not cards:
        print("[정보] 표시할 카드 0개 — latent 유지(덮어쓰지 않음)")
        return
    write_latent(cards)


# ───────────────────────── 명단 상태(관성) ─────────────────────────
STATUS_KEYS = ("status", "carried", "carried_days", "miss_limit", "streak_out",
               "out_limit", "joined", "new_day")


def limits(crit):
    return {"in": int(crit.get("편입_연속_거래일", 3)),
            "out": int(crit.get("제외_연속_거래일", 5)),
            "margin": int(crit.get("교체_순위_격차", 10)),
            "swap_days": int(crit.get("교체_연속_거래일", 5)),
            "miss": int(crit.get("결측_제외_연속일", 3)),
            "new_days": int(crit.get("신규_표시_일수", 7)),
            "cap": int(crit.get("최대_후보수", 14))}


def load_prev_latent():
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8")).get("latent") or []
    except Exception:
        return []


def load_state():
    if not STATE_PATH.exists():
        return None
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("members"), dict):
            return st
        print("[warn] latent_state.json 형식 이상 → 현재 명단으로 재시드", file=sys.stderr)
    except Exception as e:
        print(f"[warn] latent_state.json 읽기 실패({e}) → 현재 명단으로 재시드", file=sys.stderr)
    return None


def seed_state(cards, crit):
    """첫 실행: 현재 명단 전원을 멤버로 시작한다(편입 카운트 충족 간주).

    빈 상태로 시작하면 첫 full 에서 현 명단 전원이 '신규 후보'로 떨어져 명단이 비거나
    요동한다. 시드 멤버는 편입일이 없으므로 '신규' 배지도 붙지 않는다.
    """
    need = limits(crit)["in"]
    members = {}
    for c in cards:
        tk = (c.get("ticker") or "").upper()
        if tk:
            members[tk] = {"name": c.get("name", ""), "joined": None, "streak_in": need,
                           "streak_out": 0, "carried_days": 0, "rank": c.get("rank")}
    print(f"[시드] latent_state.json 없음 → 현재 명단 {len(members)}종을 멤버로 시작")
    return {"version": STATE_VERSION, "date": None, "members": members,
            "candidates": {}, "transitions": []}


def save_state(state):
    out = {"_설명": ("잠재지배자 명단 관성 상태 — generate_candidates.py 가 full 마다 쓴다. "
                   "base 는 같은 날 재실행의 출발점, transitions 는 확정 전이(주간 이력이 읽는다).")}
    out.update(state)
    out["generated_at"] = TODAY.isoformat()
    out["transitions"] = (state.get("transitions") or [])[-TRANSITIONS_KEEP:]
    STATE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8")
    print(f"[OK] latent_state.json 저장: 멤버 {len(state['members'])} · 대기 {len(state['candidates'])}")


def step(state, obs, today, crit):
    """하루치 관측을 명단 상태에 반영한다 — 순수 함수(네트워크·파일 없음).

    obs: {티커: {"known", "ok", "rank", "momentum_1y", "mc", "name"}}
      known=False 는 파싱 실패(모름). 관측에 아예 없는 기존 멤버도 모름으로 친다.
    같은 날 재실행은 그날의 출발점(base)에서 다시 계산한다 — 카운트는 거래일당 1회.
    """
    st = copy.deepcopy(state)
    if st.get("date") == today and isinstance(st.get("base"), dict):
        st["members"] = copy.deepcopy(st["base"]["members"])
        st["candidates"] = copy.deepcopy(st["base"]["candidates"])
        st["transitions"] = [t for t in st.get("transitions", []) if t.get("date") != today]
    else:
        st["base"] = {"members": copy.deepcopy(st["members"]),
                      "candidates": copy.deepcopy(st.get("candidates") or {})}
    st["date"] = today
    st["version"] = STATE_VERSION
    members = st["members"]
    cands = st.setdefault("candidates", {})
    trans = st.setdefault("transitions", [])
    if not is_trading_day(today):
        return st                      # 주말: 명단·카운트 동결
    lim = limits(crit)

    def record_t(tk, name, direction, o, **why):
        t = {"date": today, "ticker": tk, "name": name, "dir": direction}
        for k in ("rank", "momentum_1y", "mc"):
            if o.get(k) is not None:
                t[k] = o[k]
        t.update(why)
        trans.append(t)

    def expel(tk, **why):
        m = members.pop(tk)
        o = obs.get(tk) or {}
        record_t(tk, m.get("name", ""), "out",
                 o if o.get("known") else {"rank": m.get("rank")}, **why)

    def admit(tk, **why):
        c = cands.pop(tk)
        o = obs[tk]
        members[tk] = {"name": c.get("name", ""), "joined": today, "streak_in": c["streak_in"],
                       "streak_out": 0, "carried_days": 0, "rank": c.get("rank")}
        record_t(tk, c.get("name", ""), "in", o, streak_in=c["streak_in"], limit=lim["in"], **why)

    # ① 기존 멤버 — 모름은 승계(결측 카운트), 미달은 제외 카운트
    for tk in sorted(members):
        m = members[tk]
        o = obs.get(tk)
        if not o or not o.get("known"):
            m["carried_days"] = int(m.get("carried_days", 0)) + 1
            if m["carried_days"] >= lim["miss"]:
                expel(tk, carried_days=m["carried_days"], limit=lim["miss"])
            continue
        m["carried_days"] = 0
        m["rank"] = o.get("rank") or m.get("rank")      # 순위 모름은 마지막 값 유지
        if o.get("ok"):
            m["streak_out"] = 0
        else:
            m["streak_out"] = int(m.get("streak_out", 0)) + 1
            if m["streak_out"] >= lim["out"]:
                expel(tk, streak_out=m["streak_out"], limit=lim["out"])

    # ② 비멤버 — 편입 카운트. 모름은 카운트를 움직이지 않고, 미달·관측권 밖은 초기화
    for tk, o in obs.items():
        if tk in members or not o.get("known"):
            continue
        if o.get("ok"):
            c = cands.setdefault(tk, {"streak_in": 0, "swap_streak": 0})
            c["name"], c["rank"] = o.get("name", ""), o.get("rank") or c.get("rank")
            c["streak_in"] = int(c.get("streak_in", 0)) + 1
        else:
            cands.pop(tk, None)
    for tk in [t for t in cands if t not in obs or t in members]:
        cands.pop(tk)

    def ranked_ready():
        return sorted((t for t in cands if (obs.get(t) or {}).get("ok")),
                      key=lambda t: (cands[t].get("rank") or 999, t))

    # ③ 빈자리 편입 — 충족 카운트를 채운 후보를 순위순으로
    for tk in ranked_ready():
        if len(members) < lim["cap"] and cands[tk]["streak_in"] >= lim["in"]:
            admit(tk)

    # ④ 만석 교체 — 가장 약한 멤버보다 격차 이상 앞선 날이 swap_days 연속일 때만
    for tk in ranked_ready():
        c = cands[tk]
        if len(members) < lim["cap"] or not members:
            c["swap_streak"] = 0
            continue
        weakest = max(members, key=lambda t: (members[t].get("rank") or 999, t))
        w_rank = members[weakest].get("rank") or 999
        if (c.get("rank") or 999) <= w_rank - lim["margin"]:
            c["swap_streak"] = int(c.get("swap_streak", 0)) + 1
        else:
            c["swap_streak"] = 0
        if c["swap_streak"] >= lim["swap_days"] and c["streak_in"] >= lim["in"]:
            w_name = members[weakest].get("name", "")
            expel(weakest, replaced_by=tk, replaced_by_name=c.get("name", ""),
                  margin=lim["margin"], swap_streak=c["swap_streak"])
            admit(tk, replacing=weakest, replacing_name=w_name, swap_streak=c["swap_streak"])
    return st


def status_fields(m, today, lim):
    """카드에 싣는 상태 — 화면 문구는 템플릿이 이 숫자들에서 만든다(필드명 노출 금지)."""
    out = {"status": "member", "streak_out": int(m.get("streak_out", 0)),
           "out_limit": lim["out"], "carried_days": int(m.get("carried_days", 0)),
           "miss_limit": lim["miss"], "joined": m.get("joined")}
    if out["streak_out"] or out["carried_days"]:
        out["status"] = "watch"
    elif m.get("joined"):
        t0 = datetime.strptime(m["joined"], "%Y-%m-%d")
        age = (datetime.strptime(today, "%Y-%m-%d") - t0).days
        if age < lim["new_days"]:
            out["status"] = "new"
            out["new_day"] = age + 1
    return out


def run_demo():
    print("[DEMO] 모의 데이터로 규칙 검증 (순수 규칙, 감독추천 없음)\n")
    crit = DEFAULT_CRITERIA
    uni = {
        "INTC": {"name": "Intel", "ticker": "INTC", "mc": 673, "theme": "AI 반도체"},
        "GEV":  {"name": "GE Vernova", "ticker": "GEV", "mc": 260, "theme": "AI 전력 인프라"},
        "ANET": {"name": "Arista", "ticker": "ANET", "mc": 200, "theme": "AI 테크"},
        "VRT":  {"name": "Vertiv", "ticker": "VRT", "mc": 142, "theme": "AI 테크"},
        "DELL": {"name": "Dell", "ticker": "DELL", "mc": 273, "theme": "AI 테크"},
        "MRVL": {"name": "Marvell", "ticker": "MRVL", "mc": 254, "theme": "AI 반도체"},
    }
    mock = {"INTC": (21, 536), "GEV": (65, 145), "ANET": (92, 120),
            "VRT": (110, 270), "DELL": (60, 62), "MRVL": (66, 87)}
    excluded = {"INTC"}  # 미국 TOP 20 가정
    overrides = {"MRVL": {"story": "(선택 해설) 젠슨 황 지목."}}
    passed = []
    for tk, c in uni.items():
        if tk in excluded:
            print(f"  [skip] {c['name']}: 이미 우주지배자"); continue
        rank, mom = mock[tk]
        if not (crit["글로벌_순위_최소"] <= rank <= crit["글로벌_순위_최대"]):
            print(f"  [skip] {c['name']}: {rank}위 범위밖"); continue
        if mom < crit["모멘텀_1년_최소_퍼센트"]:
            print(f"  [skip] {c['name']}: 1Y {mom:+}% 미달"); continue
        c2 = dict(c); c2["rank"] = rank; c2["momentum_1y"] = mom; c2["flag"] = "🇺🇸"
        passed.append(c2)
    passed.sort(key=lambda c: c["rank"])
    print("\n최종 (순위순):")
    for c in [build_card(x, overrides) for x in passed]:
        print(f"  {c['rank']}위 {c['name']} ({c['theme']}) 1Y +{c['momentum_1y']}% — {c['story'][:38]}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    elif "--preview" in sys.argv:
        run_live(preview=True)
    else:
        run_live(preview=False)
