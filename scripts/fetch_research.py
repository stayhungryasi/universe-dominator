"""
fetch_research.py — 우주지배자·잠재지배자 종목 리서치/분석 기사 자동 수집

소스: Google News RSS (무료, API 키 불필요)
대상: 글로벌(지구) TOP 20 + 잠재지배자 전체
수집: 종목당 최신 기사 최대 N개 (제목·링크·출처·날짜)
출력: data/research.json  → build_site.py 가 research.html 로 렌더

실패해도 기존 research.json 유지 (파이프라인 안 깨짐).
"""
import sys
import json
import re
import time
import html as html_mod
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
LATEST_PATH = DATA_DIR / "latest.json"
OUT_PATH = DATA_DIR / "research.json"

PER_STOCK = 4          # 종목당 기사 수
MAX_AGE_DAYS = 14      # 최근 2주 내 기사만
FETCH_DELAY = 0.6

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": UA}

# 검색어 미세조정: 리서치·분석 결이 강한 기사 우선
QUERY_SUFFIX = ' stock (analyst OR "price target" OR research OR outlook)'


def rss_url(query, lang="en"):
    q = urllib.parse.quote(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch(url, retries=3, delay=2):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.text
        except Exception as e:
            print(f"  [{i+1}/{retries}] {e}", file=sys.stderr)
        time.sleep(delay)
    return None


def parse_rss(xml_text, limit=PER_STOCK):
    """RSS → [{title, link, source, date_iso, date_label}]"""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    cutoff = TODAY - timedelta(days=MAX_AGE_DAYS)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        # 제목 끝의 " - 출처" 제거 (구글 뉴스 형식)
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        title = html_mod.unescape(title)
        dt = None
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except Exception:
            pass
        if dt and dt < cutoff:
            continue
        date_label = dt.astimezone(KST).strftime("%m/%d") if dt else ""
        out.append({"title": title[:140], "link": link, "source": source[:40],
                    "date": dt.isoformat() if dt else "", "date_label": date_label})
        if len(out) >= limit:
            break
    return out


def collect_targets():
    """지구 TOP 20 + 잠재지배자 → [{ticker, name, group, rank}]"""
    data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    targets, seen = [], set()
    earth = data["regions"]["earth"]
    stocks = earth.get("stocks", earth) if isinstance(earth, dict) else earth
    for i, s in enumerate(stocks or []):
        tk = (s.get("ticker") or "").upper()
        if tk and tk not in seen:
            seen.add(tk)
            targets.append({"ticker": s.get("ticker",""), "name": s.get("name",""),
                            "group": "universe", "rank": i + 1})
    for s in data.get("latent", []):
        tk = (s.get("ticker") or "").upper()
        if tk and tk not in seen:
            seen.add(tk)
            targets.append({"ticker": s.get("ticker",""), "name": s.get("name",""),
                            "group": "latent", "rank": s.get("rank")})
    return targets


# 검색 정확도용: 한국어 우선 종목 (한국 시장 기사가 더 풍부)
KOREAN_QUERY = {"삼성전자", "SK하이닉스"}


def query_for(name):
    if name in KOREAN_QUERY or re.search(r"[가-힣]", name):
        return name + " 주가 (증권사 OR 목표주가 OR 리포트)", "ko"
    return name + QUERY_SUFFIX, "en"


def main():
    print(f"[research] 시작 {TODAY.isoformat()}")
    targets = collect_targets()
    print(f"  대상 {len(targets)}개 (우주 TOP 20 + 잠재)")

    stocks_out, ok = [], 0
    for t in targets:
        q, lang = query_for(t["name"])
        xml_text = fetch(rss_url(q, lang))
        time.sleep(FETCH_DELAY)
        arts = parse_rss(xml_text) if xml_text else []
        if arts:
            ok += 1
            print(f"  [ok] {t['name']}: {len(arts)}건")
        else:
            print(f"  [–] {t['name']}: 기사 없음/실패")
        stocks_out.append({**t, "articles": arts})

    if ok == 0:
        print("[중단] 수집 0건 — 기존 research.json 유지")
        sys.exit(0)

    out = {"generated_at": TODAY.isoformat(),
           "generated_label": TODAY.strftime("%Y.%m.%d %H:%M"),
           "stocks": stocks_out}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] research.json 저장: {ok}/{len(targets)}개 종목 수집")


if __name__ == "__main__":
    main()
