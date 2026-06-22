"""
generate_candidates.py — 잠재지배자 후보 생성 (감독추천 고정 + 자동발굴)

[감독추천 고정]  data/latent_overrides.json 의 "keep": true 항목
  → 기준(순위·모멘텀·섹터) 무시하고 항상 카드로 유지. 부장님 손글씨 그대로.
  → MLB 올스타의 감독추천/팬투표 고정픽에 해당.

[자동발굴 — 팬투표]
  ① AI 가치사슬 카테고리 유니버스 (반도체·소프트웨어·AI·테크·전력·인터넷)
  ② 시총 밴드로 글로벌 ~21~200위권 후보 압축 + 지역 TOP 20(이미 우주지배자) 제외
  ③ 각 후보 종목 페이지에서 글로벌순위·1년모멘텀·시총·국가 파싱
  ④ 순위 21~200위 + 모멘텀 +80% 이상만 통과 → 순위순 카드화

  최종 = 감독추천 고정 + 자동발굴 (티커 중복 시 감독추천 우선), 순위순.

  데모:      python generate_candidates.py --demo
  미리보기:  python generate_candidates.py --preview   (사이트 미반영, 파일만)
  라이브:    python generate_candidates.py             (latest.json 갱신)
"""
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ───────────────────────── 설정 ─────────────────────────
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
LATEST_PATH = DATA_DIR / "latest.json"
OVERRIDES_PATH = DATA_DIR / "latent_overrides.json"
PREVIEW_PATH = DATA_DIR / "latent_auto_preview.json"

RANK_LO = 21           # ① 글로벌 순위 하한 (TOP 20 바로 밖)
RANK_HI = 200          # ① 상한 (확대: 200위까지)
MOM_MIN = 80           # ②③ 1년 모멘텀 최소 (%)
MC_FLOOR = 70          # 자동발굴 시총 하한($B, ≈글로벌 200위권). 너무 작은 종목 컷
MAX_CANDIDATES = 14    # 최종 카드 최대 개수
MAX_AUTO_FETCH = 55    # 자동발굴 시 종목 페이지 받을 최대 개수(부하 제한)
FETCH_DELAY = 1.0      # 종목 페이지 사이 간격(초)

BASE = "https://companiesmarketcap.com"
# AI 가치사슬 유니버스 (먼저 매칭된 섹터가 테마로)
CATEGORY_URLS = {
    "AI 반도체":     BASE + "/semiconductors/largest-semiconductor-companies-by-market-cap/",
    "AI 소프트웨어": BASE + "/software/largest-software-companies-by-market-cap/",
    "AI":           BASE + "/artificial-intelligence/largest-ai-companies-by-marketcap/",
    "AI 테크":       BASE + "/tech/largest-tech-companies-by-market-cap/",
    "AI 전력 인프라": BASE + "/electricity/largest-electricity-companies-by-market-cap/",
    "AI 인터넷":     BASE + "/internet/largest-internet-companies-by-market-cap/",
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


def scrape_universe():
    """AI 가치사슬 카테고리 합집합 → {ticker(대문자): {name, mc, url, theme}}."""
    uni = {}
    for theme, url in CATEGORY_URLS.items():
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
    """종목 페이지 → {rank, momentum, flag, mc}. 실패 항목은 None."""
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
    """모든 지역 TOP 20 티커 → 이미 우주지배자라 제외."""
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
    """글로벌 TOP 20 의 최소 시총($B) — 이보다 크면 사실상 TOP 20 권이라 자동발굴서 제외."""
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
    return (f"{c['theme']} 분야 폭발 성장 모멘텀 — 글로벌 {c['rank']}위, "
            f"1년 +{c['momentum_1y']}%. AI 가치사슬 핵심 후보.")


def build_auto_card(c, overrides):
    ov = overrides.get((c["ticker"] or "").upper(), {})
    return {
        "rank": c["rank"], "ticker": c["ticker"], "name": c["name"],
        "country": c["flag"], "mc": c["mc"], "momentum_1y": c["momentum_1y"],
        "theme": ov.get("theme", c["theme"]),
        "story": ov.get("story", template_story(c)),
        "auto": True,
    }


def build_keep_card(tk, ov):
    """감독추천 고정 카드 — override 의 전체 데이터로 구성 (스크래핑 불필요)."""
    return {
        "rank": ov.get("rank"), "ticker": ov.get("ticker", tk), "name": ov.get("name", tk),
        "country": ov.get("country", "🌐"), "mc": ov.get("mc", 0),
        "momentum_1y": ov.get("momentum_1y"),
        "theme": ov.get("theme", ""), "story": ov.get("story", ""),
        "auto": False, "keep": True,
    }


def merge_cards(keeps, autos):
    seen, out = set(), []
    for c in keeps:                       # 감독추천 우선, 항상 포함
        tk = (c["ticker"] or "").upper()
        if tk and tk not in seen:
            seen.add(tk); out.append(c)
    for c in autos:                       # 자동발굴은 남는 자리 채움
        tk = (c["ticker"] or "").upper()
        if tk in seen:
            continue
        if len(out) >= MAX_CANDIDATES:
            break
        seen.add(tk); out.append(c)
    out.sort(key=lambda c: c["rank"] if c.get("rank") is not None else 9999)
    return out


# ───────────────────────── 출력 ─────────────────────────
def write_latent(cards):
    data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    data["latent"] = cards
    LATEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] latest.json latent 갱신: {len(cards)}개")


def write_preview(cards):
    PREVIEW_PATH.write_text(
        json.dumps({"generated_at": TODAY.isoformat(), "count": len(cards),
                    "candidates": cards}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[OK] 미리보기 저장: data/latent_auto_preview.json ({len(cards)}개) — 라이브 미반영")


# ───────────────────────── 실행 ─────────────────────────
def run_live(preview=False):
    print(f"[generate] 시작 {TODAY.isoformat()} ({'미리보기' if preview else '라이브'})")
    overrides = load_overrides()
    excluded = regional_top20_tickers()
    cutoff = top20_cutoff()
    print(f"  지역 TOP 20 제외 {len(excluded)}개 · TOP20 시총컷 ≈ ${cutoff:.0f}B")

    # 1) 감독추천 고정
    keeps = [build_keep_card(tk, ov) for tk, ov in overrides.items() if ov.get("keep")]
    keep_tk = {(k["ticker"] or "").upper() for k in keeps}
    print(f"  감독추천 고정 {len(keeps)}개: {sorted(keep_tk)}")

    # 2) 자동발굴 유니버스 → 시총밴드 압축
    uni = scrape_universe()
    print(f"  AI 가치사슬 유니버스 {len(uni)}개")
    pool = [c for tk, c in uni.items()
            if tk not in excluded and tk not in keep_tk and MC_FLOOR <= c["mc"] < cutoff]
    pool.sort(key=lambda c: -c["mc"])
    pool = pool[:MAX_AUTO_FETCH]
    print(f"  모멘텀 확인 대상 {len(pool)}개")

    # 3) 종목 페이지에서 순위·모멘텀 확인
    auto = []
    for c in pool:
        s = stock_stats(c)
        time.sleep(FETCH_DELAY)
        rank, mom = s["rank"], s["momentum"]
        if rank is None or mom is None:
            print(f"  [skip] {c['name']}: 데이터 파싱 실패")
            continue
        if not (RANK_LO <= rank <= RANK_HI):
            print(f"  [skip] {c['name']}: {rank}위 (범위 밖)")
            continue
        if mom < MOM_MIN:
            print(f"  [skip] {c['name']}: 1Y {mom:+}% (미달)")
            continue
        c2 = dict(c)
        c2["rank"] = rank
        c2["momentum_1y"] = mom
        c2["mc"] = s["mc"] or c["mc"]
        c2["flag"] = s["flag"] or suffix_flag(c["ticker"]) or "🌐"
        auto.append(c2)
        print(f"  [pass] {rank}위 {c['name']} ({c['theme']}) 1Y +{mom}%")
    auto.sort(key=lambda c: c["rank"])
    auto_cards = [build_auto_card(c, overrides) for c in auto]

    cards = merge_cards(keeps, auto_cards)
    print(f"  최종 {len(cards)}개 (고정 {len(keeps)} + 자동 {len(cards)-len(keeps)})")
    if not cards:
        print("[정보] 결과 0개 — latent 유지(덮어쓰지 않음)")
        return
    (write_preview if preview else write_latent)(cards)


def run_demo():
    print("[DEMO] 모의 데이터로 로직 검증\n")
    overrides = {
        "9984": {"keep": True, "rank": 48, "ticker": "9984", "name": "SoftBank Group",
                 "country": "🇯🇵", "mc": 307, "momentum_1y": 89,
                 "theme": "AI 투자 컨글로머릿", "story": "(고정) Arm·OpenAI·Stargate 익스포저."},
        "MRVL": {"theme": "AI 광 인터커넥트", "story": "(폴리시) 젠슨 황 지목."},
    }
    uni = {
        "INTC": {"name": "Intel", "ticker": "INTC", "mc": 673, "url": "u", "theme": "AI 반도체"},
        "GEV":  {"name": "GE Vernova", "ticker": "GEV", "mc": 260, "url": "u", "theme": "AI 전력 인프라"},
        "ANET": {"name": "Arista", "ticker": "ANET", "mc": 200, "url": "u", "theme": "AI 테크"},
        "VRT":  {"name": "Vertiv", "ticker": "VRT", "mc": 142, "url": "u", "theme": "AI 테크"},
        "MRVL": {"name": "Marvell", "ticker": "MRVL", "mc": 254, "url": "u", "theme": "AI 반도체"},
        "DELL": {"name": "Dell", "ticker": "DELL", "mc": 273, "url": "u", "theme": "AI 테크"},
    }
    mock = {  # 종목 페이지 파싱 결과 모의 (rank, momentum)
        "INTC": (21, 536), "GEV": (65, 145), "ANET": (92, 120),
        "VRT": (110, 270), "MRVL": (66, 87), "DELL": (60, 62),
    }
    excluded = {"INTC"}  # 미국 TOP 20 가정
    cutoff = 480
    keeps = [build_keep_card(tk, ov) for tk, ov in overrides.items() if ov.get("keep")]
    keep_tk = {(k["ticker"] or "").upper() for k in keeps}
    pool = [c for tk, c in uni.items()
            if tk not in excluded and tk not in keep_tk and MC_FLOOR <= c["mc"] < cutoff]
    auto = []
    for c in pool:
        rank, mom = mock[c["ticker"]]
        if not (RANK_LO <= rank <= RANK_HI):
            print(f"  [skip] {c['name']}: {rank}위 범위밖"); continue
        if mom < MOM_MIN:
            print(f"  [skip] {c['name']}: 1Y {mom:+}% 미달"); continue
        c2 = dict(c); c2["rank"] = rank; c2["momentum_1y"] = mom; c2["flag"] = "🇺🇸"
        auto.append(c2)
        print(f"  [pass] {rank}위 {c['name']} 1Y +{mom}%")
    cards = merge_cards(keeps, [build_auto_card(c, overrides) for c in auto])
    print("\n최종 카드 (고정+자동, 순위순):")
    for c in cards:
        tag = "[고정]" if c.get("keep") else "[자동]"
        print(f"  {tag} {c['rank']}위 {c['name']} ({c['theme']}) 1Y "
              f"{('+'+str(c['momentum_1y'])+'%') if c['momentum_1y'] is not None else '-'}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    elif "--preview" in sys.argv:
        run_live(preview=True)
    else:
        run_live(preview=False)
