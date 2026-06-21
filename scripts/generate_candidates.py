"""
generate_candidates.py — 잠재지배자 후보 완전 자동 생성

파이프라인:
  ① companiesmarketcap 글로벌 랭킹 스크래핑 → 글로벌 순위 21~100위
  ② companiesmarketcap AI/반도체/소프트웨어 카테고리 → AI 가치사슬 종목 집합 (섹터 태그)
  ③ Stooq 무료 CSV로 각 후보의 1년 주가 수익률 계산 → +60% 이상만
  ④ 순위 정렬 → 상위 N개를 latest.json 의 latent 로 자동 기록

  ※ 해설(story)·테마(theme)는 자동 템플릿. 단, data/latent_overrides.json 에
    종목별 수동 해설이 있으면 그것을 우선 사용 → "완전 자동" 이되 원하면 개별 카드 보완 가능.

라이브 스크래핑/Stooq는 GitHub Actions(개방 네트워크)에서 동작.
처음엔 수동 실행(Run workflow)으로 결과 확인 후 일일 파이프라인에 연결 권장.

  데모(네트워크 없이 로직 검증):  python generate_candidates.py --demo
"""
import sys
import io
import csv
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

RANK_LO = 21          # ① 글로벌 순위 하한 (TOP 20 바로 밖)
RANK_HI = 100         # ① 상한 (글로벌 페이지 1장 ≈ 100위까지)
MOM_MIN = 60          # ②③ 1년 모멘텀 최소 (%)
MAX_CANDIDATES = 12   # ④ 최종 카드 최대 개수

GLOBAL_URL = "https://companiesmarketcap.com/"
# 섹터 태그용 (AI 가치사슬). key = 화면 표시 테마, value = 카테고리 URL
SECTOR_URLS = {
    "AI 반도체":     "https://companiesmarketcap.com/semiconductors/largest-semiconductor-companies-by-market-cap/",
    "AI 소프트웨어": "https://companiesmarketcap.com/software/largest-software-companies-by-market-cap/",
    "AI":           "https://companiesmarketcap.com/artificial-intelligence/largest-ai-companies-by-marketcap/",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"}

# Stooq 거래소 접미사 매핑 (없으면 미국 .us 로 가정)
STOOQ_SUFFIX = {
    ".L": ".uk", ".T": ".jp", ".HK": ".hk", ".SS": ".cn", ".SZ": ".cn",
    ".PA": ".fr", ".DE": ".de", ".AS": ".nl", ".SW": ".ch", ".TW": ".tw",
    ".KS": ".kr", ".KQ": ".kr",
}


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
    val = float(m.group(1))
    return val * 1000 if m.group(2) == "T" else val


FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


def parse_company_list(html, limit=100):
    """companiesmarketcap 목록 테이블 → [{rank, name, ticker, mc, flag}]"""
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
        # 시총
        mc = 0.0
        for td in tds:
            txt = td.get_text(strip=True)
            if "$" in txt and re.search(r"[\d.]+\s*[TB]", txt):
                mc = parse_mc(txt)
                break
        if mc == 0:
            continue
        # 국기 (행 텍스트에서 regional-indicator 이모지)
        fm = FLAG_RE.search(tr.get_text(" ", strip=True))
        flag = fm.group(0) if fm else "🌐"
        rows.append({"name": name, "ticker": ticker or "", "mc": round(mc, 2), "flag": flag})
        if len(rows) >= limit:
            break
    # 순위 부여 (목록은 시총 내림차순)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def scrape_ai_sectors():
    """섹터 카테고리들을 긁어 {ticker: theme} 매핑 생성. AI 가치사슬 종목 집합."""
    theme_by_ticker = {}
    for theme, url in SECTOR_URLS.items():
        html = fetch(url)
        if not html:
            continue
        for r in parse_company_list(html, limit=200):
            tk = (r["ticker"] or "").upper()
            if tk and tk not in theme_by_ticker:  # 먼저 매칭된 섹터 우선 (반도체>소프트>AI)
                theme_by_ticker[tk] = theme
        time.sleep(1)
    return theme_by_ticker


# ───────────────────────── 모멘텀 (Stooq) ─────────────────────────
def stooq_symbol(ticker):
    t = (ticker or "").strip()
    for suf, s in STOOQ_SUFFIX.items():
        if t.endswith(suf):
            return t[: -len(suf)].lower() + s
    return t.lower() + ".us"  # 기본: 미국


def momentum_1y(ticker):
    """Stooq 일별 종가로 ~1년 수익률(%) 계산. 실패 시 None."""
    sym = stooq_symbol(ticker)
    d2 = TODAY.strftime("%Y%m%d")
    d1 = (TODAY - timedelta(days=375)).strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d&d1={d1}&d2={d2}"
    txt = fetch(url, retries=2, delay=2)
    if not txt or "Date" not in txt:
        return None
    try:
        rows = list(csv.DictReader(io.StringIO(txt)))
        closes = [float(r["Close"]) for r in rows
                  if r.get("Close") not in (None, "", "N/D")]
        if len(closes) < 100:
            return None
        first, last = closes[0], closes[-1]
        if first <= 0:
            return None
        return round((last - first) / first * 100)
    except Exception:
        return None


# ───────────────────────── 해설 템플릿 / override ─────────────────────────
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
        "rank": c["rank"],
        "ticker": c["ticker"],
        "name": c["name"],
        "country": c["flag"],
        "mc": c["mc"],
        "momentum_1y": c["momentum_1y"],
        "theme": ov.get("theme", c["theme"]),
        "story": ov.get("story", template_story(c)),
        "auto": True,
    }


# ───────────────────────── 스크리닝 ─────────────────────────
def screen(global_rows, theme_by_ticker, momentum_fn):
    cands = []
    for r in global_rows:
        if not (RANK_LO <= r["rank"] <= RANK_HI):      # ①
            continue
        tk = (r["ticker"] or "").upper()
        theme = theme_by_ticker.get(tk)
        if not theme:                                   # ④ AI 가치사슬만
            continue
        mom = momentum_fn(r["ticker"])                  # ②③
        if mom is None:
            print(f"  [skip] {r['name']}: 모멘텀 데이터 없음")
            continue
        if mom < MOM_MIN:
            print(f"  [skip] {r['name']}: 1Y +{mom}% (미달)")
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


def write_preview(cands_cards):
    """미리보기 — 실제 사이트(latest.json)는 안 건드리고 별도 파일에만 저장."""
    PREVIEW_PATH = DATA_DIR / "latent_auto_preview.json"
    PREVIEW_PATH.write_text(
        json.dumps({"generated_at": TODAY.isoformat(), "count": len(cands_cards),
                    "candidates": cands_cards}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[OK] 미리보기 저장: data/latent_auto_preview.json ({len(cands_cards)}개) — 라이브 미반영")


# ───────────────────────── main ─────────────────────────
def run_live(preview=False):
    mode = "미리보기" if preview else "라이브"
    print(f"[generate] 시작 {TODAY.isoformat()} ({mode})")
    html = fetch(GLOBAL_URL)
    if not html:
        print("[중단] 글로벌 랭킹 스크래핑 실패"); sys.exit(1)
    global_rows = parse_company_list(html, limit=RANK_HI)
    print(f"  글로벌 {len(global_rows)}위까지 수집")
    theme_by_ticker = scrape_ai_sectors()
    print(f"  AI 가치사슬 종목 {len(theme_by_ticker)}개 식별")
    cands = screen(global_rows, theme_by_ticker, momentum_1y)
    if not cands:
        print("[정보] 조건 통과 후보 0개 — latent 유지(덮어쓰지 않음)")
        return
    overrides = load_overrides()
    cards = [build_card(c, overrides) for c in cands]
    if preview:
        write_preview(cards)
    else:
        write_latent(cards)


def run_demo():
    """네트워크 없이 로직 검증."""
    print("[DEMO] 모의 데이터로 로직 검증\n")
    global_rows = [
        {"rank": 18, "name": "Broadcom",  "ticker": "AVGO", "mc": 1200, "flag": "🇺🇸"},
        {"rank": 25, "name": "Palantir",  "ticker": "PLTR", "mc": 480,  "flag": "🇺🇸"},
        {"rank": 31, "name": "Arm Holdings","ticker":"ARM",  "mc": 390,  "flag": "🇬🇧"},
        {"rank": 38, "name": "AMD",       "ticker": "AMD",  "mc": 320,  "flag": "🇺🇸"},
        {"rank": 52, "name": "CoreWeave", "ticker": "CRWV", "mc": 120,  "flag": "🇺🇸"},
        {"rank": 70, "name": "KB Financial","ticker":"KB",  "mc": 60,   "flag": "🇰🇷"},
    ]
    theme_by_ticker = {"AVGO": "AI 반도체", "PLTR": "AI 소프트웨어", "ARM": "AI 반도체",
                       "AMD": "AI 반도체", "CRWV": "AI"}  # KB 없음(비AI)
    mock_mom = {"PLTR": 155, "ARM": 98, "AMD": 72, "CRWV": 210, "AVGO": 80}
    cands = screen(global_rows, theme_by_ticker, lambda t: mock_mom.get((t or "").upper()))
    overrides = {"PLTR": {"theme": "AI 소프트웨어", "story": "(수동 해설 예시) AIP 엔터프라이즈 표준."}}
    cards = [build_card(c, overrides) for c in cands]
    print("\n생성된 카드:")
    print(json.dumps(cards, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    elif "--preview" in sys.argv:
        run_live(preview=True)
    else:
        run_live(preview=False)
