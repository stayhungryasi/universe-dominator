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

  데모:      python generate_candidates.py --demo
  미리보기:  python generate_candidates.py --preview
  라이브:    python generate_candidates.py
"""
import sys
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

MAX_AUTO_FETCH = 60    # 종목 페이지 받을 최대 개수(부하 제한)
FETCH_DELAY = 1.0

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

SUFFIX_FLAG = {".TW": "🇹🇼", ".T": "🇯🇵", ".HK": "🇭🇰", ".SS": "🇨🇳", ".SZ": "🇨🇳",
               ".L": "🇬🇧", ".KS": "🇰🇷", ".KQ": "🇰🇷", ".DE": "🇩🇪", ".PA": "🇫🇷",
               ".SW": "🇨🇭", ".AS": "🇳🇱", ".TO": "🇨🇦", ".SR": "🇸🇦", ".MI": "🇮🇹",
               ".MC": "🇪🇸", ".ST": "🇸🇪", ".HE": "🇫🇮"}


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
def regional_top20_tickers():
    keys = set()
    try:
        data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        for region in data.get("regions", {}).values():
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
    return (f"{c['theme']} · 글로벌 {c['rank']}위 · 1년 +{c['momentum_1y']}% — "
            f"폭발적 성장 모멘텀의 AI 가치사슬 후보.")


def build_card(c, overrides):
    ov = overrides.get((c["ticker"] or "").upper(), {})   # (선택) 해설/테마 미화
    return {
        "rank": c["rank"], "ticker": c["ticker"], "name": c["name"],
        "country": c["flag"], "mc": c["mc"], "momentum_1y": c["momentum_1y"],
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
    excluded = regional_top20_tickers() if crit.get("지역TOP20_제외", True) else set()
    cutoff = top20_cutoff()

    uni = scrape_universe(sectors)
    print(f"  AI 가치사슬 유니버스 {len(uni)}개 · 지역 TOP20 제외 {len(excluded)}개")
    pool = [c for tk, c in uni.items() if tk not in excluded and mc_floor <= c["mc"] < cutoff]
    pool.sort(key=lambda c: -c["mc"])
    pool = pool[:MAX_AUTO_FETCH]
    print(f"  모멘텀 확인 대상 {len(pool)}개")

    passed = []
    for c in pool:
        s = stock_stats(c)
        time.sleep(FETCH_DELAY)
        rank, mom = s["rank"], s["momentum"]
        if rank is None or mom is None:
            print(f"  [skip] {c['name']}: 데이터 파싱 실패"); continue
        if not (rank_lo <= rank <= rank_hi):
            print(f"  [skip] {c['name']}: {rank}위 (범위 밖)"); continue
        if mom < mom_min:
            print(f"  [skip] {c['name']}: 1Y {mom:+}% (미달)"); continue
        c2 = dict(c)
        c2["rank"] = rank; c2["momentum_1y"] = mom
        c2["mc"] = s["mc"] or c["mc"]
        c2["flag"] = s["flag"] or suffix_flag(c["ticker"]) or "🌐"
        passed.append(c2)
        print(f"  [pass] {rank}위 {c['name']} ({c['theme']}) 1Y +{mom}%")

    passed.sort(key=lambda c: c["rank"])
    cards = [build_card(c, overrides) for c in passed][:max_n]
    print(f"  최종 {len(cards)}개")
    if not cards:
        print("[정보] 조건 통과 0개 — latent 유지(덮어쓰지 않음)")
        return
    (write_preview if preview else write_latent)(cards)


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
