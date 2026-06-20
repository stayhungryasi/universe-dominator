"""
fetch_data.py — companiesmarketcap.com에서 데이터 수집
v2: 이름·티커 분리 + 국기 이모지 도출 + 권역 순서 변경
"""
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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

REGION_URLS = {
    "global":  "https://companiesmarketcap.com/",
    "us":      "https://companiesmarketcap.com/usa/largest-companies-in-the-usa-by-market-cap/",
    "korea":   "https://companiesmarketcap.com/south-korea/largest-companies-in-south-korea-by-market-cap/",
    "japan":   "https://companiesmarketcap.com/japan/largest-companies-in-japan-by-market-cap/",
    "china":   "https://companiesmarketcap.com/china/largest-companies-in-china-by-market-cap/",
    "hk":      "https://companiesmarketcap.com/hong-kong/largest-companies-in-hong-kong-by-market-cap/",
    "eu":      "https://companiesmarketcap.com/european-union/largest-companies-in-the-eu-by-market-cap/",
    "uk":      "https://companiesmarketcap.com/united-kingdom/largest-companies-in-the-uk-by-market-cap/",
    "ch":      "https://companiesmarketcap.com/switzerland/largest-companies-in-switzerland-by-market-cap/",
}

REGION_LABELS = {
    "earth":  {"label": "지구",   "subtitle": "Global"},
    "us":     {"label": "미국",   "subtitle": "United States"},
    "korea":  {"label": "한국",   "subtitle": "KRX"},
    "japan":  {"label": "일본",   "subtitle": "TSE"},
    "china":  {"label": "중국",   "subtitle": "Mainland (SH·SZ)"},
    "hk":     {"label": "홍콩",   "subtitle": "HKEX"},
    "europe": {"label": "유럽",   "subtitle": "EU + UK + CH"},
}

TOP_N = 20

# ─────────────────── 국기 도출 ───────────────────
ADR_OVERRIDES = {
    "TSM": "🇹🇼", "BABA": "🇨🇳", "PDD": "🇨🇳", "BIDU": "🇨🇳",
    "NTES": "🇨🇳", "JD": "🇨🇳", "TCOM": "🇨🇳", "TCEHY": "🇨🇳",
    "ARM": "🇬🇧", "ASML": "🇳🇱", "NVO": "🇩🇰", "AZN": "🇬🇧",
    "NVS": "🇨🇭", "RHHBY": "🇨🇭", "TM": "🇯🇵", "SONY": "🇯🇵",
    "HSBC": "🇬🇧", "SAP": "🇩🇪", "SHEL": "🇬🇧", "BP": "🇬🇧",
    "RY": "🇨🇦", "TD": "🇨🇦", "UL": "🇬🇧", "DEO": "🇬🇧",
}

SUFFIX_FLAGS = {
    "KS": "🇰🇷", "KQ": "🇰🇷",
    "SS": "🇨🇳", "SZ": "🇨🇳",
    "HK": "🇭🇰", "T": "🇯🇵",
    "L": "🇬🇧", "PA": "🇫🇷", "SW": "🇨🇭",
    "DE": "🇩🇪", "F": "🇩🇪", "AS": "🇳🇱",
    "MI": "🇮🇹", "MC": "🇪🇸", "CO": "🇩🇰",
    "ST": "🇸🇪", "OL": "🇳🇴", "BR": "🇧🇪",
    "LS": "🇵🇹", "VI": "🇦🇹", "SR": "🇸🇦",
    "TW": "🇹🇼", "TWO": "🇹🇼",
    "AX": "🇦🇺", "TO": "🇨🇦", "V": "🇨🇦",
    "BO": "🇮🇳", "NS": "🇮🇳", "MX": "🇲🇽",
    "SA": "🇧🇷", "BK": "🇹🇭", "JK": "🇮🇩",
    "SI": "🇸🇬", "KL": "🇲🇾", "JO": "🇿🇦",
    "ME": "🇷🇺", "WA": "🇵🇱", "IS": "🇹🇷",
    "TA": "🇮🇱", "CN": "🇨🇦",
}

REGION_DEFAULT_FLAG = {
    "us": "🇺🇸", "korea": "🇰🇷", "japan": "🇯🇵",
    "china": "🇨🇳", "hk": "🇭🇰",
}


def derive_flag(ticker, region_key=None):
    """티커 + 권역 컨텍스트로 국기 이모지 도출."""
    # 권역별 단일 국가 페이지: 그 국가로 통일
    if region_key in REGION_DEFAULT_FLAG:
        return REGION_DEFAULT_FLAG[region_key]
    
    if not ticker:
        return "🇺🇸"
    
    # ADR 특별 케이스 (NYSE 표기 → 실제 국가)
    t_base = ticker.upper().split(".")[0]
    if t_base in ADR_OVERRIDES:
        return ADR_OVERRIDES[t_base]
    
    # 거래소 접미사로 판단
    if "." in ticker:
        suffix = ticker.split(".")[-1].upper()
        if suffix in SUFFIX_FLAGS:
            return SUFFIX_FLAGS[suffix]
    
    return "🇺🇸"  # 기본값 (미국 NYSE/NASDAQ)


def clean_name(name, ticker):
    """이름 뒤에 티커가 붙어 있으면 제거."""
    if not name:
        return ""
    name = name.strip()
    if not ticker:
        return name
    
    # "NVIDIANVDA" → "NVIDIA", "Samsung005930.KS" → "Samsung"
    ticker_str = ticker.strip()
    if name.endswith(ticker_str):
        name = name[:-len(ticker_str)].strip()
    
    # 점 없는 변형 ("AppleAAPL" → "Apple")
    ticker_base = ticker_str.split(".")[0]
    if ticker_base and ticker_base != ticker_str and name.endswith(ticker_base):
        name = name[:-len(ticker_base)].strip()
    
    return name


# KOSPI/KOSDAQ 한국 종목 한국어 이름 매핑
KOREAN_NAMES = {
    # KOSPI 대형주
    "005930": "삼성전자",         "005935": "삼성전자우",
    "000660": "SK하이닉스",       "005380": "현대차",
    "373220": "LG에너지솔루션",   "012450": "한화에어로스페이스",
    "207940": "삼성바이오로직스", "402340": "SK스퀘어",
    "000270": "기아",             "034020": "두산에너빌리티",
    "329180": "HD현대중공업",     "105560": "KB금융",
    "068270": "셀트리온",         "055550": "신한지주",
    "012330": "현대모비스",       "042660": "한화오션",
    "032830": "삼성생명",         "006400": "삼성SDI",
    "267260": "HD현대일렉트릭",   "035420": "NAVER",
    "010130": "고려아연",         "028260": "삼성물산",
    "035720": "카카오",           "086790": "하나금융지주",
    "316140": "우리금융지주",     "066570": "LG전자",
    "003550": "LG",               "051910": "LG화학",
    "015760": "한국전력",         "034730": "SK",
    "010140": "삼성중공업",       "047810": "한국항공우주",
    "064350": "현대로템",         "003490": "대한항공",
    "011200": "HMM",              "017670": "SK텔레콤",
    "030200": "KT",               "032640": "LG유플러스",
    "000810": "삼성화재",         "139480": "이마트",
    "097950": "CJ제일제당",       "024110": "기업은행",
    "036570": "엔씨소프트",       "352820": "하이브",
    "041510": "에스엠",           "035900": "JYP Ent.",
    "011170": "롯데케미칼",       "010620": "현대미포조선",
    "009155": "삼성전기",         "005490": "POSCO홀딩스",
    "086280": "현대글로비스",     "000720": "현대건설",
    "030000": "제일기획",         "088980": "맥쿼리인프라",
    "008770": "호텔신라",         "004020": "현대제철",
    "138040": "메리츠금융지주",   "071050": "한국금융지주",
    "010950": "S-Oil",            "009830": "한화솔루션",
    "018260": "삼성에스디에스",   "021240": "코웨이",
    # KOSDAQ 대형주
    "247540": "에코프로비엠",     "086520": "에코프로",
    "196170": "알테오젠",         "028300": "HLB",
    "263750": "펄어비스",         "022100": "포스코DX",
    "091990": "셀트리온헬스케어", "293490": "카카오게임즈",
    "112040": "위메이드",         "067310": "하나마이크론",
}


# 한국 회사의 NYSE ADR 티커 (.KS 접미사 없이 표시되는 경우)
KOREAN_ADRS = {
    "KB":   "KB금융",
    "SHG":  "신한지주",
    "KEP":  "한국전력",
    "LPL":  "LG디스플레이",
    "PKX":  "POSCO홀딩스",
    "WF":   "우리금융지주",
    "SKM":  "SK텔레콤",
    "KT":   "KT",
    "POSCO": "POSCO홀딩스",
}


def localize_name(name, ticker):
    """한국 종목이면 한국어 이름으로 교체."""
    if not ticker:
        return name
    ticker_upper = ticker.upper()
    
    # 1) KOSPI/KOSDAQ (.KS, .KQ 접미사)
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        code = ticker.split(".")[0]
        return KOREAN_NAMES.get(code, name)
    
    # 2) NYSE ADR 티커 (한국 회사이지만 미국 거래소 티커로 표시되는 경우)
    if ticker_upper in KOREAN_ADRS:
        return KOREAN_ADRS[ticker_upper]
    
    return name


# ─────────────────── 환율 ───────────────────
def fetch_exchange_rate():
    """USD/KRW 환율. frankfurter.app(ECB 데이터) → exchangerate-api 백업.
    실패 시 None."""
    apis = [
        "https://api.frankfurter.app/latest?from=USD&to=KRW",
        "https://api.exchangerate-api.com/v4/latest/USD",
    ]
    for url in apis:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                # frankfurter: data["rates"]["KRW"]
                # exchangerate-api: data["rates"]["KRW"]
                rate = data.get("rates", {}).get("KRW")
                if rate and 1000 < rate < 2500:
                    print(f"[환율] {rate:.2f} ({url.split('/')[2]})")
                    return float(rate)
        except Exception as e:
            print(f"[환율] {url.split('/')[2]} 실패: {e}", file=sys.stderr)
    return None


# ─────────────────── HTTP fetch ───────────────────
def fetch(url, retries=4, delay=3):
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


def parse_mc(text):
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


# ─────────────────── 테이블 파싱 ───────────────────
def parse_table(html, region_key, limit=100):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    
    table = soup.find("table") or soup
    
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        
        # ── 이름·티커 ──
        # 1순위: 정확한 클래스 매칭
        name_el = tr.find(class_="company-name")
        code_el = tr.find(class_="company-code")
        
        name = name_el.get_text(strip=True) if name_el else None
        ticker = code_el.get_text(strip=True) if code_el else None
        
        # 2순위(폴백): 컨테이너에서 자식 추출
        if not name:
            container = tr.find(class_=re.compile(r"name-?div|company", re.I))
            if container:
                divs = container.find_all(["div", "span", "a"])
                if len(divs) >= 1 and not name:
                    name = divs[0].get_text(strip=True)
                if len(divs) >= 2 and not ticker:
                    ticker = divs[1].get_text(strip=True)
        
        if not name:
            continue
        
        # 이름에서 티커 제거 (안전망)
        name = clean_name(name, ticker)
        if not name:
            continue
        
        # 한국 종목이면 한국어 이름으로 교체
        name = localize_name(name, ticker)
        
        # ── 시가총액 ──
        mc_value = 0.0
        for td in tds:
            text = td.get_text(strip=True)
            if "$" in text and re.search(r"[\d.]+\s*[TB]", text):
                mc_value = parse_mc(text)
                break
        
        if mc_value == 0:
            continue
        
        # ── 국기 ──
        flag = derive_flag(ticker, region_key=region_key)
        
        rows.append({
            "name": name,
            "ticker": ticker or "",
            "flag": flag,
            "mc": round(mc_value, 2),
        })
        
        if len(rows) >= limit:
            break
    
    return rows


# ─────────────────── 수집 ───────────────────
def collect():
    raw = {}
    errors = []
    print(f"[fetch] 시작: {TODAY_KST.isoformat()}")
    
    for region, url in REGION_URLS.items():
        try:
            print(f"  → {region} ({url})")
            html = fetch(url)
            # global → earth 매핑, 그 외 권역은 region 키 그대로
            ctx_key = "earth" if region == "global" else region
            rows = parse_table(html, region_key=ctx_key, limit=100)
            print(f"     ✓ {len(rows)}개 종목")
            raw[region] = rows
            time.sleep(2)
        except Exception as e:
            print(f"     ✗ 실패: {e}", file=sys.stderr)
            errors.append((region, str(e)))
            raw[region] = []
    
    # 유럽 = EU + UK + CH 통합 후 시총 내림차순
    europe = []
    seen = set()
    for src in ("eu", "uk", "ch"):
        for row in raw.get(src, []):
            key = (row["name"], row["ticker"])
            if key not in seen:
                europe.append(row)
                seen.add(key)
    europe.sort(key=lambda x: -x["mc"])
    
    # ★ 권역 순서: 지구 - 미국 - 한국 - 일본 - 중국 - 홍콩 - 유럽
    result = {
        "regions": {
            "earth":  {**REGION_LABELS["earth"],  "stocks": raw.get("global", [])[:TOP_N]},
            "us":     {**REGION_LABELS["us"],     "stocks": raw.get("us", [])[:TOP_N]},
            "korea":  {**REGION_LABELS["korea"],  "stocks": raw.get("korea", [])[:TOP_N]},
            "japan":  {**REGION_LABELS["japan"],  "stocks": raw.get("japan", [])[:TOP_N]},
            "china":  {**REGION_LABELS["china"],  "stocks": raw.get("china", [])[:TOP_N]},
            "hk":     {**REGION_LABELS["hk"],     "stocks": raw.get("hk", [])[:TOP_N]},
            "europe": {**REGION_LABELS["europe"], "stocks": europe[:TOP_N]},
        },
        "meta": {
            "fetched_at": TODAY_KST.isoformat(),
            "fetched_date": TODAY_KST.strftime("%Y-%m-%d"),
            "usd_krw": None,  # main()에서 fetch_exchange_rate() 적용
            "errors": errors,
        },
    }
    
    failed_regions = [k for k, v in result["regions"].items() if len(v["stocks"]) < 10]
    if failed_regions:
        msg = f"권역 수집 부족: {failed_regions}"
        print(f"[WARN] {msg}", file=sys.stderr)
        if len(failed_regions) >= 5:
            raise RuntimeError(f"대부분 권역 수집 실패: {failed_regions}")
    
    return result


def main():
    try:
        new_data = collect()
    except Exception as e:
        print(f"[FATAL] 수집 실패: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 잠재지배자/변천사는 큐레이션 영역 → 보존
    existing_latent = []
    existing_history = []
    existing_usd_krw = 1480.0
    if DATA_PATH.exists():
        try:
            existing = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            existing_latent = existing.get("latent", [])
            existing_history = existing.get("history", [])
            existing_usd_krw = existing.get("meta", {}).get("usd_krw") or 1480.0
        except Exception:
            pass
    
    new_data["latent"] = existing_latent
    new_data["history"] = existing_history
    
    # 환율: 실시간 fetch → 실패 시 직전 값 → 그래도 없으면 1480
    rate = fetch_exchange_rate()
    if rate is None:
        rate = existing_usd_krw
        print(f"[환율] fetch 실패, 직전 값 사용: {rate}")
    new_data["meta"]["usd_krw"] = round(rate, 2)
    
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] {DATA_PATH} 저장 완료")


if __name__ == "__main__":
    main()
