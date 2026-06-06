"""
fetch_data.py — companiesmarketcap.com에서 데이터 수집

작동:
1. 글로벌 + 7개 권역 페이지를 차례로 스크래핑
2. 각 권역의 TOP 20 추출 (지구·미국은 글로벌 페이지에서 분기)
3. data/latest.json 으로 저장

실패 시 처리:
- 일부 권역 실패해도 다른 권역 데이터는 살림
- 전체 실패 시 종전 데이터 유지 (덮어쓰지 않음)
- GitHub Actions 로그에 상세 출력
"""
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ───────────────────────────────────────────────────────────
# 설정
# ───────────────────────────────────────────────────────────
HERE = Path(__file__).parent.parent
DATA_PATH = HERE / "data" / "latest.json"

KST = timezone(timedelta(hours=9))
TODAY_KST = datetime.now(KST)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

# 권역별 URL
REGION_URLS = {
    "global":  "https://companiesmarketcap.com/",
    "us":      "https://companiesmarketcap.com/usa/largest-companies-in-the-usa-by-market-cap/",
    "china":   "https://companiesmarketcap.com/china/largest-companies-in-china-by-market-cap/",
    "hk":      "https://companiesmarketcap.com/hong-kong/largest-companies-in-hong-kong-by-market-cap/",
    "korea":   "https://companiesmarketcap.com/south-korea/largest-companies-in-south-korea-by-market-cap/",
    "japan":   "https://companiesmarketcap.com/japan/largest-companies-in-japan-by-market-cap/",
    # 유럽은 EU·UK·CH를 따로 합쳐서 정렬
    "eu":      "https://companiesmarketcap.com/european-union/largest-companies-in-the-eu-by-market-cap/",
    "uk":      "https://companiesmarketcap.com/united-kingdom/largest-companies-in-the-uk-by-market-cap/",
    "ch":      "https://companiesmarketcap.com/switzerland/largest-companies-in-switzerland-by-market-cap/",
}

# 권역 라벨 (사이트 표시용)
REGION_LABELS = {
    "earth":  {"label": "지구",   "subtitle": "Global"},
    "us":     {"label": "미국",   "subtitle": "United States"},
    "china":  {"label": "중국",   "subtitle": "Mainland (SH·SZ)"},
    "hk":     {"label": "홍콩",   "subtitle": "HKEX"},
    "europe": {"label": "유럽",   "subtitle": "EU + UK + CH"},
    "korea":  {"label": "한국",   "subtitle": "KRX"},
    "japan":  {"label": "일본",   "subtitle": "TSE"},
}

# 권역별 표시할 종목 수
TOP_N = 20


# ───────────────────────────────────────────────────────────
# HTTP fetch (재시도 포함)
# ───────────────────────────────────────────────────────────
def fetch(url, retries=4, delay=3):
    """Cloudflare 보호가 있을 수 있으니 재시도 로직 포함."""
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  [{i+1}/{retries}] HTTP {r.status_code} — {url}", file=sys.stderr)
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            print(f"  [{i+1}/{retries}] {e}", file=sys.stderr)
            last_err = str(e)
        time.sleep(delay * (i + 1))
    raise RuntimeError(f"fetch 실패: {url} — {last_err}")


# ───────────────────────────────────────────────────────────
# 시가총액 문자열 파서: "$5.11 T" / "$842.32 B" → 842.32 (단위: bn)
# ───────────────────────────────────────────────────────────
def parse_mc(text):
    """문자열을 USD billion 단위 float로."""
    if not text:
        return 0.0
    s = text.replace("$", "").replace(",", "").strip()
    m = re.match(r"([\d.]+)\s*([TBMK]?)", s.upper())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "T":
        return val * 1000
    if unit == "B":
        return val
    if unit == "M":
        return val / 1000
    return val


# ───────────────────────────────────────────────────────────
# 페이지에서 종목 테이블 파싱
# ───────────────────────────────────────────────────────────
def parse_table(html, limit=200):
    """
    companiesmarketcap.com 의 종목 테이블 파싱.
    
    구조 (관찰 기반):
    <tr class="company-row" ...>
       <td>...순위...</td>
       <td>
         <div class="name-div">
           <div class="company-name">NVIDIA</div>
           <div class="company-code">NVDA</div>
         </div>
       </td>
       <td>...</td>
       <td>...$5.11 T...</td>  ← 시가총액
       ...
    </tr>
    
    실제 클래스명이 변경될 가능성에 대비해 여러 패턴으로 시도.
    """
    soup = BeautifulSoup(html, "html.parser")
    
    rows = []
    
    # 패턴 1: 회사 행이 있는 테이블 찾기
    table = soup.find("table") or soup
    
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        
        # 회사명 / 티커 추출
        name = None
        ticker = None
        # 패턴 A: company-name / company-code
        name_el = tr.find(class_=re.compile(r"company-name|name", re.I))
        code_el = tr.find(class_=re.compile(r"company-code|code|ticker", re.I))
        if name_el:
            name = name_el.get_text(strip=True)
        if code_el:
            ticker = code_el.get_text(strip=True)
        # 패턴 B: 이름이 div 안에 있고, 그 div 다음 div가 코드
        if not name:
            name_div = tr.find("div", class_=re.compile(r"name", re.I))
            if name_div:
                divs = name_div.find_all("div")
                if len(divs) >= 1:
                    name = divs[0].get_text(strip=True)
                if len(divs) >= 2 and not ticker:
                    ticker = divs[1].get_text(strip=True)
        
        if not name:
            continue
        
        # 시가총액 찾기: td 내용 중 "$xx.x T/B" 패턴이 있는 셀
        mc_value = 0.0
        for td in tds:
            text = td.get_text(strip=True)
            if "$" in text and re.search(r"[\d.]+\s*[TB]", text):
                mc_value = parse_mc(text)
                break
        
        if mc_value == 0:
            continue
        
        # 국가 플래그 (이미지 alt 등에서)
        flag = ""
        flag_img = tr.find("img", alt=re.compile(r"flag|country", re.I))
        if flag_img:
            flag = flag_img.get("alt", "").replace("flag", "").strip()
        
        rows.append({
            "name": name,
            "ticker": ticker or "",
            "flag": flag,
            "mc": round(mc_value, 2),
        })
        
        if len(rows) >= limit:
            break
    
    return rows


# ───────────────────────────────────────────────────────────
# 권역별 데이터 수집
# ───────────────────────────────────────────────────────────
def collect():
    raw = {}
    errors = []
    
    print(f"[fetch] 시작: {TODAY_KST.isoformat()}")
    
    for region, url in REGION_URLS.items():
        try:
            print(f"  → {region} ({url})")
            html = fetch(url)
            rows = parse_table(html, limit=100)
            print(f"     ✓ {len(rows)}개 종목")
            raw[region] = rows
            time.sleep(2)  # rate limit 회피
        except Exception as e:
            print(f"     ✗ 실패: {e}", file=sys.stderr)
            errors.append((region, str(e)))
            raw[region] = []
    
    # 유럽 = EU + UK + CH 합쳐서 시총 내림차순 TOP 20
    europe = []
    seen = set()
    for src in ("eu", "uk", "ch"):
        for row in raw.get(src, []):
            key = (row["name"], row["ticker"])
            if key not in seen:
                europe.append(row)
                seen.add(key)
    europe.sort(key=lambda x: -x["mc"])
    
    # 결과 구성 (사이트 형식)
    result = {
        "regions": {
            "earth":  {**REGION_LABELS["earth"],  "stocks": raw.get("global", [])[:TOP_N]},
            "us":     {**REGION_LABELS["us"],     "stocks": raw.get("us", [])[:TOP_N]},
            "china":  {**REGION_LABELS["china"],  "stocks": raw.get("china", [])[:TOP_N]},
            "hk":     {**REGION_LABELS["hk"],     "stocks": raw.get("hk", [])[:TOP_N]},
            "europe": {**REGION_LABELS["europe"], "stocks": europe[:TOP_N]},
            "korea":  {**REGION_LABELS["korea"],  "stocks": raw.get("korea", [])[:TOP_N]},
            "japan":  {**REGION_LABELS["japan"],  "stocks": raw.get("japan", [])[:TOP_N]},
        },
        "meta": {
            "fetched_at": TODAY_KST.isoformat(),
            "fetched_date": TODAY_KST.strftime("%Y-%m-%d"),
            "errors": errors,
        },
    }
    
    # 권역별 수집 종목 수 점검 — TOP 10 미만이면 실패로 간주
    failed_regions = [k for k, v in result["regions"].items()
                       if len(v["stocks"]) < 10]
    
    if failed_regions:
        msg = f"권역 수집 부족: {failed_regions}"
        print(f"[WARN] {msg}", file=sys.stderr)
        # 전체가 실패면 종전 데이터 유지
        if len(failed_regions) >= 5:
            raise RuntimeError(f"대부분 권역 수집 실패: {failed_regions}")
    
    return result


def main():
    try:
        new_data = collect()
    except Exception as e:
        print(f"[FATAL] 수집 실패: {e}", file=sys.stderr)
        # 종전 데이터 그대로 유지
        sys.exit(1)
    
    # 잠재지배자/변천사는 이 스크립트로 자동 갱신 어려움 (큐레이션 영역)
    # → 기존 latest.json 의 latent/history 보존
    existing_latent = []
    existing_history = []
    if DATA_PATH.exists():
        try:
            existing = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            existing_latent = existing.get("latent", [])
            existing_history = existing.get("history", [])
        except Exception:
            pass
    
    new_data["latent"] = existing_latent
    new_data["history"] = existing_history
    
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] {DATA_PATH} 저장 완료")


if __name__ == "__main__":
    main()
