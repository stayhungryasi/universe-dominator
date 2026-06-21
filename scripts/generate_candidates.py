"""
generate_candidates.py — 잠재지배자 후보 완전 자동 생성

파이프라인 (모두 companiesmarketcap 한 소스 — GitHub Actions에서 차단 안 됨):
  ① 글로벌 랭킹 스크래핑 → 글로벌 순위 21~100위 (+ 각 종목 페이지 URL)
  ② AI/반도체/소프트웨어 카테고리 → AI 가치사슬 종목 집합 (섹터 태그)
  ③ 각 후보의 개별 종목 페이지에서 'Change (1 year)' 파싱 → +60% 이상만
  ④ 순위 정렬 → 상위 N개를 latest.json 의 latent 로 자동 기록

  ※ 해설(story)·테마(theme)는 자동 템플릿. data/latent_overrides.json 에
    종목별 수동 해설이 있으면 그것을 우선 사용 → 완전 자동이되 개별 카드 보완 가능.

  데모(네트워크 없이 로직 검증):  python generate_candidates.py --demo
  미리보기(사이트 미반영, 파일만):  python generate_candidates.py --preview
  라이브(latest.json 직접 갱신):     python generate_candidates.py
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

RANK_LO = 21          # ① 글로벌 순위 하한 (TOP 20 바로 밖)
RANK_HI = 100         # ① 상한 (글로벌 페이지 1장 ≈ 100위)
MOM_MIN = 60          # ②③ 1년 모멘텀 최소 (%)
MAX_CANDIDATES = 12   # ④ 최종 카드 최대 개수
FETCH_DELAY = 1.2     # 종목 페이지 사이 예의상 간격(초)

BASE = "https://companiesmarketcap.com"
GLOBAL_URL = BASE + "/"
SECTOR_URLS = {  # key = 화면 테마, value = 카테고리 URL (먼저 매칭된 섹터 우선)
    "AI 반도체":     BASE + "/semiconductors/largest-semiconductor-companies-by-market-cap/",
    "AI 소프트웨어": BASE + "/software/largest-software-companies-by-market-cap/",
    "AI":           BASE + "/artificial-intelligence/largest-ai-companies-by-marketcap/",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"}

FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
MC_RE = re.compile(r"[\d.]+\s*[TB]")
URL_RE = re.compile(r"/[^/]+/marketcap/?$")
# 종목 페이지의 1년 변동률 (숫자가 라벨 앞/뒤 양쪽 대응)
Y1_RE_A = re.compile(r"(-?\d+(?:\.\d+)?)\s*%\s*Change\s*\(1\s*year\)", re.I)
Y1_RE_B = re.compile(r"Change\s*\(1\s*year\)\s*(-?\d+(?:\.\d+)?)\s*%", re.I)


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


def parse_company_list(html, limit=100):
    """목록 테이블 → [{rank, name, ticker, mc, flag, url}]  (url = 종목 페이지)"""
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
        fm = FLAG_RE.search(tr.get_text(" ", strip=True))
        flag = fm.group(0) if fm else "🌐"
        link = tr.find("a", href=URL_RE)
        url = None
        if link and link.get("href"):
            href = link["href"]
            url = href if href.startswith("http") else BASE + href
        rows.append({"name": name, "ticker": ticker or "", "mc": round(mc, 2),
                     "flag": flag, "url": url})
        if len(rows) >= limit:
            break
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def scrape_ai_sectors():
    """섹터 카테고리들 → {ticker(대문자): theme}. AI 가치사슬 종목 집합."""
    info = {}
    for theme, url in SECTOR_URLS.items():
        html = fetch(url)
        if not html:
            continue
        for r in parse_company_list(html, limit=300):
            tk = (r["ticker"] or "").upper()
            if tk and tk not in info:
                info[tk] = {"theme": theme, "url": r.get("url")}
        time.sleep(1)
    return info


def momentum_1y(row):
    """종목 페이지에서 'Change (1 year)' 파싱 → 정수 %. 실패 시 None."""
    url = row.get("url")
    if not url:
        return None
    html = fetch(url, retries=2, delay=2)
    if not html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = Y1_RE_A.search(text) or Y1_RE_B.search(text)
    if not m:
        return None
    try:
        return round(float(m.group(1)))
    except Exception:
        return None


# ───────────────────────── 해설 / override ─────────────────────────
def load_overrides():
    if OVERRIDES_PATH.exists():
        try:
            return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def template_story(c):
    return (f"{c['theme']} 분야 폭발 성장 모멘텀 — 글로벌 {c['rank']}위, "
            f"1년 +{c['momentum_1y']}%. AI 가치사슬 핵심 후보.")


def build_card(c, overrides):
    ov = overrides.get((c["ticker"] or "").upper(), {})
    return {
        "rank": c["rank"], "ticker": c["ticker"], "name": c["name"],
        "country": c["flag"], "mc": c["mc"], "momentum_1y": c["momentum_1y"],
        "theme": ov.get("theme", c["theme"]),
        "story": ov.get("story", template_story(c)),
        "auto": True,
    }


# ───────────────────────── 스크리닝 ─────────────────────────
def screen(global_rows, theme_by_ticker, momentum_fn, delay=0.0):
    cands = []
    for r in global_rows:
        if not (RANK_LO <= r["rank"] <= RANK_HI):       # ①
            continue
        tk = (r["ticker"] or "").upper()
        info = theme_by_ticker.get(tk)
        if not info:                                     # ④ AI 가치사슬만
            continue
        theme = info["theme"]
        if not r.get("url"):                             # 글로벌에 링크 없으면 AI쪽 URL 사용
            r["url"] = info.get("url")
        mom = momentum_fn(r)                             # ②③ (종목 페이지)
        if delay:
            time.sleep(delay)
        if mom is None:
            print(f"  [skip] {r['name']}: 1년 변동률 파싱 실패")
            continue
        if mom < MOM_MIN:
            print(f"  [skip] {r['name']}: 1Y {mom:+}% (미달)")
            continue
        r2 = dict(r); r2["theme"] = theme; r2["momentum_1y"] = mom
        cands.append(r2)
        print(f"  [pass] {r['rank']}위 {r['name']} ({theme}) 1Y +{mom}%")
    cands.sort(key=lambda c: c["rank"])
    return cands[:MAX_CANDIDATES]


def write_latent(cards):
    data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    data["latent"] = cards
    LATEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] latest.json latent 갱신: {len(cards)}개 후보")


def write_preview(cards):
    PREVIEW_PATH.write_text(
        json.dumps({"generated_at": TODAY.isoformat(), "count": len(cards),
                    "candidates": cards}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[OK] 미리보기 저장: data/latent_auto_preview.json ({len(cards)}개) — 라이브 미반영")


# ───────────────────────── 실행 ─────────────────────────
def run_live(preview=False):
    print(f"[generate] 시작 {TODAY.isoformat()} ({'미리보기' if preview else '라이브'})")
    html = fetch(GLOBAL_URL)
    if not html:
        print("[중단] 글로벌 랭킹 스크래핑 실패"); sys.exit(1)
    global_rows = parse_company_list(html, limit=RANK_HI)
    print(f"  글로벌 {len(global_rows)}위까지 수집")
    theme_by_ticker = scrape_ai_sectors()
    print(f"  AI 가치사슬 종목 {len(theme_by_ticker)}개 식별")
    cands = screen(global_rows, theme_by_ticker, momentum_1y, delay=FETCH_DELAY)
    if not cands:
        print("[정보] 조건 통과 후보 0개 — latent 유지(덮어쓰지 않음)")
        return
    cards = [build_card(c, load_overrides()) for c in cands]
    (write_preview if preview else write_latent)(cards)


def run_demo():
    print("[DEMO] 모의 데이터로 로직 검증\n")
    rows = [
        {"rank": 18, "name": "Broadcom",   "ticker": "AVGO", "mc": 1200, "flag": "🇺🇸", "url": "u"},
        {"rank": 25, "name": "Palantir",   "ticker": "PLTR", "mc": 480,  "flag": "🇺🇸", "url": "u"},
        {"rank": 31, "name": "Arm Holdings","ticker": "ARM", "mc": 390,  "flag": "🇬🇧", "url": "u"},
        {"rank": 38, "name": "AMD",        "ticker": "AMD",  "mc": 320,  "flag": "🇺🇸", "url": "u"},
        {"rank": 52, "name": "CoreWeave",  "ticker": "CRWV", "mc": 120,  "flag": "🇺🇸", "url": "u"},
        {"rank": 70, "name": "KB Financial","ticker": "KB",  "mc": 60,   "flag": "🇰🇷", "url": "u"},
    ]
    theme_by_ticker = {k: {"theme": v, "url": "u"} for k, v in
                       {"AVGO": "AI 반도체", "PLTR": "AI 소프트웨어", "ARM": "AI 반도체",
                        "AMD": "AI 반도체", "CRWV": "AI"}.items()}
    mock = {"PLTR": 155, "ARM": 98, "AMD": 72, "CRWV": 210, "AVGO": 80}
    cands = screen(rows, theme_by_ticker, lambda r: mock.get((r["ticker"] or "").upper()))
    overrides = {"PLTR": {"theme": "AI 소프트웨어", "story": "(수동 해설 예시) AIP 엔터프라이즈 표준."}}
    print("\n생성된 카드:")
    print(json.dumps([build_card(c, overrides) for c in cands], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    elif "--preview" in sys.argv:
        run_live(preview=True)
    else:
        run_live(preview=False)
