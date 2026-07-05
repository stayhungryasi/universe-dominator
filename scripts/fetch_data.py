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


# ─────────────────── 종목 한 줄 설명 ───────────────────
STOCK_DESCRIPTIONS = {
    # ===== 미국 NASDAQ/NYSE =====
    "NVDA": "AI 가속기 패권자",
    "AAPL": "프리미엄 디바이스·서비스",
    "MSFT": "엔터프라이즈 + Azure 클라우드",
    "GOOGL": "검색·광고 + Gemini AI",
    "GOOG": "검색·광고 + Gemini AI",
    "AMZN": "전자상거래 + AWS 클라우드",
    "META": "글로벌 SNS·광고 1위",
    "TSLA": "EV·자율주행·로봇",
    "AVGO": "통신·AI 반도체 거인",
    "TSM": "세계 1위 파운드리",
    "BRK-B": "워런 버핏의 투자 제국",
    "BRK.B": "워런 버핏의 투자 제국",
    "BRK-A": "워런 버핏의 투자 제국",
    "BRK.A": "워런 버핏의 투자 제국",
    "LLY": "GLP-1 비만약 美 선두",
    "MU": "美 최대 메모리 반도체",
    "WMT": "美 1위 소매유통",
    "JPM": "美 최대 은행",
    "V": "글로벌 결제 네트워크 1위",
    "MA": "글로벌 결제 네트워크 2위",
    "XOM": "美 최대 석유 메이저",
    "COST": "회원제 창고형 할인점",
    "ORCL": "엔터프라이즈 DB + 클라우드",
    "CVX": "美 2위 석유 메이저",
    "PG": "글로벌 생활용품 1위",
    "HD": "美 최대 홈인테리어",
    "JNJ": "글로벌 헬스케어 거인",
    "UNH": "美 최대 의료보험",
    "KO": "글로벌 음료 1위",
    "MRK": "글로벌 제약 빅3",
    "ABBV": "글로벌 제약 빅5",
    "BAC": "美 2위 은행",
    "PEP": "글로벌 음료·스낵",
    "CSCO": "엔터프라이즈 네트워크 1위",
    "ADBE": "디지털 크리에이티브 SW",
    "NFLX": "글로벌 1위 스트리밍",
    "TMUS": "美 3위 이동통신",
    "PFE": "글로벌 제약 빅5",
    "DIS": "글로벌 미디어 엔터테인먼트",
    "ACN": "글로벌 IT 컨설팅 1위",
    "INTC": "美 최대 종합 반도체",
    "AMD": "CPU·GPU 추격자",
    "QCOM": "모바일 칩 1위",
    "TXN": "아날로그 반도체 1위",
    "INTU": "美 1위 회계 SW",
    "IBM": "엔터프라이즈 + AI 컨설팅",
    "ASML": "노광장비 글로벌 독점",
    "GEV": "GE 전력·에너지 분사",
    "CRM": "1위 CRM SaaS",
    "ABT": "글로벌 헬스케어",
    "NKE": "글로벌 1위 스포츠웨어",
    "MS": "美 글로벌 IB",
    "GS": "글로벌 투자은행 1위",
    "BLK": "세계 최대 자산운용사",
    "WFC": "美 4대 은행",
    "C": "美 3대 은행",
    "AMGN": "美 최대 바이오텍",
    "GILD": "HIV·간염 신약 강자",
    "REGN": "美 바이오 빅5",
    "SBUX": "글로벌 1위 커피 체인",
    "MDLZ": "글로벌 과자 빅5",
    "BKNG": "글로벌 1위 호텔 예약",
    "PYPL": "글로벌 디지털 결제",
    "PLTR": "엔터프라이즈 AI 플랫폼",
    "ANET": "AI 데이터센터 네트워크",
    "MRVL": "광 인터커넥트 강자",
    "DELL": "AI 서버 OEM",
    "VRT": "데이터센터 쿨링 1위",
    "ARM": "AI 칩 CPU IP",
    "PANW": "사이버보안 1위",
    "FTNT": "사이버보안 빅3",
    "AMAT": "반도체 장비 美 1위",
    "LRCX": "식각·증착 장비 1위",
    "KLAC": "반도체 계측 1위",
    "CDNS": "EDA 1위 (반도체 설계 SW)",
    "SNPS": "EDA 2위 (반도체 설계 SW)",
    "RTX": "美 최대 방산기업",
    "LMT": "美 1위 방산 (전투기·미사일)",
    "BA": "美 1위 항공기 제조",
    "CAT": "글로벌 1위 건설기계",
    "F": "美 2위 자동차 (Ford)",
    "GM": "美 1위 자동차",
    "T": "美 최대 통신사 (AT&T)",
    "VZ": "美 2위 통신사",
    "SPGI": "글로벌 신용평가 빅3",
    "NOW": "엔터프라이즈 IT 워크플로",
    "UPS": "美 최대 배송",
    "MCD": "글로벌 1위 패스트푸드",
    "AXP": "美 1위 카드 발급사",
    "ABNB": "글로벌 숙박 공유",
    "PM": "글로벌 1위 담배",
    "LIN": "글로벌 1위 산업가스",
    "ISRG": "수술 로봇 다빈치",
    "MELI": "남미 1위 전자상거래",
    "NVS": "스위스 제약 빅3",
    "NVO": "GLP-1 비만약 글로벌 1위",
    "AZN": "영국 제약 빅3",
    "SHEL": "유럽 석유 메이저 (Shell)",
    "BP": "영국 석유 메이저",
    "HSBC": "글로벌 빅뱅크",
    
    # ===== 사우디 =====
    "2222.SR": "세계 최대 석유 기업",
    
    # ===== 한국 (KOSPI) =====
    "005930.KS": "메모리·스마트폰 글로벌 1위",
    "005935.KS": "삼성전자 우선주",
    "000660.KS": "HBM·D램 강자",
    "005380.KS": "글로벌 완성차 빅5",
    "373220.KS": "K배터리 1위",
    "012450.KS": "K방산 대표주",
    "207940.KS": "글로벌 CDMO 1위",
    "402340.KS": "SK ICT 지주회사",
    "000270.KS": "글로벌 완성차 빅5",
    "034020.KS": "원전·풍력 발전",
    "329180.KS": "세계 1위 조선",
    "105560.KS": "국내 1위 금융지주",
    "068270.KS": "K바이오시밀러 선두",
    "055550.KS": "국내 2위 금융지주",
    "012330.KS": "현대차 핵심 부품사",
    "042660.KS": "K방산 함정·LNG선",
    "032830.KS": "국내 1위 생명보험",
    "006400.KS": "K배터리 빅3",
    "267260.KS": "전력기기 글로벌 강자",
    "035420.KS": "국내 1위 검색 포털",
    "010130.KS": "국내 1위 비철금속",
    "028260.KS": "삼성그룹 지주격",
    "035720.KS": "국내 1위 메신저·플랫폼",
    "086790.KS": "국내 3위 금융지주",
    "316140.KS": "국내 4위 금융지주",
    "066570.KS": "가전·전장 글로벌",
    "003550.KS": "LG그룹 지주회사",
    "051910.KS": "국내 1위 화학",
    "015760.KS": "국내 최대 전력",
    "034730.KS": "SK그룹 지주회사",
    "047810.KS": "K방산 항공·우주",
    "064350.KS": "K방산 K2전차·K9자주포",
    "003490.KS": "국내 1위 항공",
    "011200.KS": "국내 1위 해운",
    "017670.KS": "국내 1위 이동통신",
    "030200.KS": "국내 2위 이동통신",
    "032640.KS": "국내 3위 이동통신",
    "000810.KS": "국내 1위 손해보험",
    "036570.KS": "국내 1위 게임 (리니지)",
    "352820.KS": "K-POP BTS 소속사",
    "009155.KS": "MLCC 글로벌 빅3",
    "005490.KS": "철강 + 이차전지 소재",
    "010140.KS": "국내 1위 조선 (삼성)",
    "086280.KS": "현대차그룹 글로벌 물류",
    "000720.KS": "국내 1위 건설",
    "138040.KS": "메리츠그룹 지주",
    "071050.KS": "한국투자증권 지주",
    "010950.KS": "국내 정유 빅4 (S-Oil)",
    "009830.KS": "한화그룹 화학·태양광",
    "018260.KS": "삼성 IT 서비스",
    "021240.KS": "국내 1위 정수기 렌탈",
    # KOSDAQ
    "247540.KQ": "K배터리 양극재 1위",
    "086520.KQ": "K배터리 소재 지주",
    "196170.KQ": "K바이오 신약 신성",
    "028300.KQ": "K바이오 항암 신약",
    
    # ===== 한국 ADR (NYSE) =====
    "KB": "국내 1위 금융지주",
    "SHG": "국내 2위 금융지주",
    "KEP": "국내 최대 전력",
    "LPL": "디스플레이 글로벌 빅3",
    "PKX": "철강 + 이차전지 소재",
    "WF": "국내 4위 금융지주",
    "SKM": "국내 1위 이동통신",
    
    # ===== 일본 (TSE) =====
    "7203.T": "글로벌 1위 자동차 (Toyota)",
    "9984.T": "글로벌 AI 투자 큰손",
    "8306.T": "일본 1위 메가뱅크",
    "6501.T": "DX·산업장비 (Hitachi)",
    "8316.T": "일본 3대 은행 (SMFG)",
    "6758.T": "게임·이미지센서 (Sony)",
    "9983.T": "유니클로 모회사",
    "8058.T": "일본 최대 종합상사",
    "8035.T": "반도체 장비 빅3",
    "6857.T": "반도체 테스터 1위",
    "8031.T": "일본 2위 상사 (Mitsui)",
    "8411.T": "일본 3대 은행 (Mizuho)",
    "4519.T": "일본 신약 강자 (Chugai)",
    "7011.T": "방산·중공업 (MHI)",
    "8001.T": "종합상사 빅5 (Itochu)",
    "6861.T": "산업 센서 글로벌",
    "9432.T": "일본 최대 통신사 (NTT)",
    "8766.T": "일본 1위 손해보험",
    "4063.T": "실리콘 웨이퍼 1위",
    "6503.T": "산업·인프라 전기",
    
    # ===== 중국 본토 (SH·SZ) =====
    "601939.SS": "중국 4대 국유은행 (CCB)",
    "601288.SS": "중국 4대 국유은행 (ABC)",
    "600519.SS": "중국 명품 백주 (마오타이)",
    "601988.SS": "중국 4대 국유은행 (BOC)",
    "300750.SZ": "세계 1위 배터리 (CATL)",
    "601628.SS": "중국 1위 생명보험",
    "601318.SS": "중국 1위 종합금융 (Ping An)",
    "601138.SS": "폭스콘 中 자회사",
    "601899.SS": "중국 최대 금속 광산",
    "601088.SS": "중국 최대 석탄",
    "002594.SZ": "세계 1위 EV (BYD)",
    "600028.SS": "중국 1위 정유사 (Sinopec)",
    "600900.SS": "중국 최대 수력발전",
    "601328.SS": "중국 5대 은행",
    "601658.SS": "中 최대 우편은행",
    "300308.SZ": "광 통신 모듈",
    "000333.SZ": "중국 1위 가전 (Midea)",
    "688256.SS": "중국판 NVIDIA (Cambricon)",
    "601998.SS": "중국 대형 은행 (CITIC)",
    "000858.SZ": "중국 2위 백주 (오량액)",
    
    # ===== 홍콩 (HKEX) =====
    "0700.HK": "위챗·게임·핀테크 (Tencent)",
    "1398.HK": "세계 1위 은행 (ICBC)",
    "0857.HK": "중국 최대 석유 (PetroChina)",
    "0005.HK": "글로벌 빅뱅크 (HSBC)",
    "9988.HK": "중국 1위 전자상거래 (Alibaba)",
    "0941.HK": "세계 1위 이통사",
    "0883.HK": "중국 3대 해양 석유 (CNOOC)",
    "3968.HK": "중국 빅5 은행 (CMB)",
    "1299.HK": "아시아 1위 생명보험 (AIA)",
    "1810.HK": "中 스마트폰·IoT (Xiaomi)",
    "0728.HK": "中 3대 통신 (China Telecom)",
    "0981.HK": "중국 최대 파운드리 (SMIC)",
    "0388.HK": "홍콩 증권거래소",
    "9633.HK": "中 1위 생수 (Nongfu)",
    "3993.HK": "중국 광산 다국적 (CMOC)",
    "3690.HK": "中 1위 배달 (Meituan)",
    "2388.HK": "중국은행 홍콩 (BOCHK)",
    "0016.HK": "홍콩 최대 부동산",
    "2328.HK": "중국 1위 손해보험 (PICC)",
    "2359.HK": "中 1위 CDMO (WuXi)",
    
    # ===== 유럽 (EU + UK + CH) =====
    "ASML.AS": "노광장비 글로벌 독점",
    "ROG.SW": "글로벌 제약 빅3 (Roche)",
    "HSBA.L": "글로벌 빅뱅크 (HSBC)",
    "AZN.L": "영국 제약 빅3 (AstraZeneca)",
    "NOVN.SW": "스위스 제약 빅3 (Novartis)",
    "MC.PA": "명품 콘글로머릿 1위 (LVMH)",
    "NESN.SW": "세계 최대 식품 (Nestlé)",
    "SIE.DE": "산업 자동화 거인 (Siemens)",
    "OR.PA": "세계 최대 화장품 (L'Oréal)",
    "SHEL.L": "유럽 석유 메이저 (Shell)",
    "LIN.DE": "글로벌 1위 산업가스",
    "SAP.DE": "유럽 최대 SW",
    "NOVO-B.CO": "GLP-1 비만약 글로벌 1위",
    "PRX.AS": "Naspers 자회사 (Prosus)",
    "RMS.PA": "명품 최고급 (Hermès)",
    "ITX.MC": "Zara 모회사 (Inditex)",
    "DTE.DE": "독일 1위 통신사",
    "TTE.PA": "프랑스 석유 메이저 (Total)",
    "SAN.MC": "스페인 1위 은행 (Santander)",
}


def get_description(ticker):
    """티커로 종목 한 줄 설명 조회."""
    if not ticker:
        return ""
    t = ticker.strip()
    # 정확히 매칭
    if t in STOCK_DESCRIPTIONS:
        return STOCK_DESCRIPTIONS[t]
    # 대소문자 무시 매칭
    return STOCK_DESCRIPTIONS.get(t.upper(), "")


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
            "desc": get_description(ticker or ""),
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
    
    # ★ 지역 순서: 지구 - 미국 - 한국 - 일본 - 유럽 - 중국 - 홍콩
    result = {
        "regions": {
            "earth":  {**REGION_LABELS["earth"],  "stocks": raw.get("global", [])[:TOP_N]},
            "us":     {**REGION_LABELS["us"],     "stocks": raw.get("us", [])[:TOP_N]},
            "korea":  {**REGION_LABELS["korea"],  "stocks": raw.get("korea", [])[:TOP_N]},
            "japan":  {**REGION_LABELS["japan"],  "stocks": raw.get("japan", [])[:TOP_N]},
            "europe": {**REGION_LABELS["europe"], "stocks": europe[:TOP_N]},
            "china":  {**REGION_LABELS["china"],  "stocks": raw.get("china", [])[:TOP_N]},
            "hk":     {**REGION_LABELS["hk"],     "stocks": raw.get("hk", [])[:TOP_N]},
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
    
    # ─── 일별 스냅샷 저장 (주간 History 자동 생성용) ───
    # 스냅샷은 부가 기능 → 실패해도 메인 업데이트는 정상 완료되도록 보호
    try:
        save_daily_snapshot(new_data)
    except Exception as e:
        print(f"[경고] 스냅샷 저장 실패 (메인 업데이트는 정상): {e}")


def save_daily_snapshot(data):
    """
    오늘 날짜의 TOP 20 데이터를 data/snapshots/YYYY-MM-DD.json 으로 저장.
    주간 변동 비교(weekly_history.py)의 원천 데이터.
    잠재지배자/변천사 같은 큐레이션 영역은 제외하고 순수 시총 데이터만 보관.
    """
    snap_dir = HERE / "data" / "snapshots"
    
    # snapshots 경로가 파일로 존재하면 (잘못 생성된 경우) 제거 후 폴더로 재생성
    if snap_dir.exists() and not snap_dir.is_dir():
        try:
            snap_dir.unlink()
            print("[정리] snapshots가 파일로 존재 → 삭제 후 폴더 생성")
        except Exception as e:
            print(f"[경고] snapshots 파일 제거 실패: {e}")
    
    try:
        snap_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        # exist_ok=True에도 드물게 발생 → 이미 있으면 그냥 진행
        if not snap_dir.is_dir():
            print("[경고] snapshots 폴더 생성 불가 — 스냅샷 저장 건너뜀")
            return
    
    # 비교에 필요한 최소 데이터만 추림 (지역별 ticker/name/mc/순위)
    snapshot = {
        "date": data.get("meta", {}).get("fetched_date", TODAY_KST.strftime("%Y-%m-%d")),
        "regions": {},
    }
    for region_key, region in data.get("regions", {}).items():
        stocks = region.get("stocks", [])
        snapshot["regions"][region_key] = [
            {
                "rank": i + 1,
                "ticker": s.get("ticker", ""),
                "name": s.get("name", ""),
                "mc": s.get("mc", 0),
            }
            for i, s in enumerate(stocks)
        ]
    
    # 잠재지배자 큐레이션 목록도 스냅샷에 보관 (주간 변동 자동 감지용)
    snapshot["latent"] = [
        {
            "ticker": s.get("ticker", ""),
            "name": s.get("name", ""),
            "rank": s.get("rank"),
            "mc": s.get("mc", 0),
            "momentum_1y": s.get("momentum_1y"),
            "theme": s.get("theme", ""),
        }
        for s in data.get("latent", [])
    ]
    
    snap_path = snap_dir / f"{snapshot['date']}.json"
    snap_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] 스냅샷 저장: {snap_path.name}")
    
    # 오래된 스냅샷 정리 (최근 90일치만 보관)
    prune_old_snapshots(snap_dir, keep_days=90)


def prune_old_snapshots(snap_dir, keep_days=90):
    """90일보다 오래된 스냅샷 삭제 (저장소 비대화 방지)."""
    try:
        from datetime import timedelta
        cutoff = (TODAY_KST - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        removed = 0
        for f in snap_dir.glob("*.json"):
            # 파일명이 날짜 형식(YYYY-MM-DD.json)이고 cutoff보다 이전이면 삭제
            stem = f.stem
            if len(stem) == 10 and stem[4] == "-" and stem < cutoff:
                f.unlink()
                removed += 1
        if removed:
            print(f"[정리] 오래된 스냅샷 {removed}개 삭제 (>{keep_days}일)")
    except Exception as e:
        print(f"[정리] 스냅샷 정리 중 오류 (무시): {e}")


if __name__ == "__main__":
    main()
