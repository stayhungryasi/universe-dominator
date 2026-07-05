"""
fetch_research.py — 우주지배자·잠재지배자 종목 리서치/분석 기사 자동 수집

소스: Google News RSS (무료, API 키 불필요)
대상: 글로벌(지구) TOP 20 + 잠재지배자 전체
수집: 종목당 최신 기사 최대 N개 (제목·링크·출처·날짜)
출력: data/research.json  → build_site.py 가 research.html 로 렌더

실패해도 기존 research.json 유지 (파이프라인 안 깨짐).
"""
import os
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
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
LATEST_PATH = DATA_DIR / "latest.json"
OUT_PATH = DATA_DIR / "research.json"

PER_STOCK = 4          # 종목당 기사 수
SUMMARIZE_PER_STOCK = 2  # 종목당 본문 요약 대상 (상위 N건, 비용·시간 관리)
BODY_MIN = 400           # 본문 최소 길이(자) — 이보다 짧으면 요약 스킵
BODY_MAX = 2800          # Claude 에 보낼 본문 최대 길이
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


def fetch_article_body(link):
    """기사 원문 본문 추출. 실패/유료벽/리다이렉트 미해결 시 None."""
    try:
        r = requests.get(link, headers=HEADERS, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # 구글 중간 페이지에 머문 경우: 원문 링크 후보 탐색
        if "news.google.com" in (r.url or ""):
            a = soup.find("a", href=re.compile(r"^https?://(?!news\.google)"))
            if not a:
                return None
            r = requests.get(a["href"], headers=HEADERS, timeout=12, allow_redirects=True)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script", "style", "nav", "header", "footer", "aside"]):
            bad.decompose()
        paras = [pg.get_text(" ", strip=True) for pg in soup.find_all("p")]
        body = " ".join(x for x in paras if len(x) > 40)
        if len(body) < BODY_MIN:
            return None
        return body[:BODY_MAX]
    except Exception:
        return None


def summarize_articles_ko(stocks_out):
    """종목당 상위 N건의 기사 본문을 Claude(Haiku)로 2~3줄 한글 불릿 요약 → summary_ko.
    API 키 없거나 본문 접근 실패 시 조용히 건너뜀."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("  [요약] API 키 없음 — 본문 요약 건너뜀")
        return
    # 요약 대상: 종목당 상위 SUMMARIZE_PER_STOCK 건, 본문 확보 성공분만
    todo = []
    for s in stocks_out:
        for a in s.get("articles", [])[:SUMMARIZE_PER_STOCK]:
            body = fetch_article_body(a.get("link", ""))
            time.sleep(0.3)
            if body:
                todo.append({"a": a, "body": body})
    print(f"  [요약] 본문 확보 {len(todo)}건 (대상 중 접근 실패분 제외)")
    if not todo:
        return
    done = 0
    BATCH = 8
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        payload = [{"i": j, "title": x["a"]["title"], "body": x["body"]}
                   for j, x in enumerate(batch)]
        prompt = (
            "다음 주식 리서치 기사들을 각각 한국어 불릿 2~3개로 요약하세요. "
            "각 불릿은 한 문장, 핵심 수치(목표주가·등락률·시총 등)를 우선 포함. "
            "기업명·티커는 원문 그대로. 반드시 JSON 배열만 출력: "
            '[{"i": 인덱스, "bullets": ["...", "..."]}]\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 8000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=180,
            )
            if r.status_code != 200:
                print(f"  [요약] API 오류 HTTP {r.status_code} — 배치 건너뜀", file=sys.stderr)
                continue
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            i1, i2 = text.find("["), text.rfind("]")
            if i1 == -1 or i2 <= i1:
                continue
            results = json.loads(text[i1:i2 + 1])
            for item in results:
                idx = item.get("i")
                bl = [str(b).strip()[:180] for b in item.get("bullets", []) if str(b).strip()]
                if isinstance(idx, int) and 0 <= idx < len(batch) and bl:
                    batch[idx]["a"]["summary_ko"] = bl[:3]
                    done += 1
        except Exception as e:
            print(f"  [요약] 배치 실패(무시): {e}", file=sys.stderr)
    print(f"  [요약] 완료: {done}건")


def translate_titles_ko(stocks_out):
    """영어 기사 제목을 Claude API(Haiku)로 일괄 한글 번역 → title_ko 필드 추가.
    ANTHROPIC_API_KEY 환경변수가 없거나 호출 실패 시 조용히 건너뜀 (영문만 표시)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("  [번역] API 키 없음 — 한글 번역 건너뜀 (GitHub Secrets에 ANTHROPIC_API_KEY 등록 시 활성화)")
        return
    # 한글이 아닌 제목만 수집
    todo = []
    for s in stocks_out:
        for a in s.get("articles", []):
            if not re.search(r"[가-힣]", a.get("title", "")):
                todo.append(a)
    if not todo:
        return
    print(f"  [번역] 영어 제목 {len(todo)}건 한글 번역 시도")
    titles = [a["title"] for a in todo]
    prompt = (
        "다음은 주식 리서치 기사 제목 목록입니다. 각 제목을 자연스러운 한국어로 번역하세요. "
        "종목명·기업명은 원문 그대로 두고, 금융 용어는 한국 증권가 표현(목표주가, 상향, 급등 등)을 쓰세요. "
        "반드시 입력과 같은 길이의 JSON 문자열 배열만 출력하세요. 다른 텍스트 금지.\n\n"
        + json.dumps(titles, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 8000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"  [번역] API 오류 HTTP {r.status_code} — 건너뜀", file=sys.stderr)
            return
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        # 응답에서 JSON 배열 부분만 견고하게 추출 (코드펜스·부가텍스트 대응)
        i, j = text.find("["), text.rfind("]")
        if i == -1 or j == -1 or j <= i:
            print("  [번역] 응답에 JSON 배열 없음 — 건너뜀", file=sys.stderr)
            return
        ko = json.loads(text[i:j+1])
        if not isinstance(ko, list) or len(ko) != len(todo):
            print(f"  [번역] 응답 형식 불일치 ({len(ko) if isinstance(ko,list) else '?'}/{len(todo)}) — 건너뜀", file=sys.stderr)
            return
        for a, k in zip(todo, ko):
            if isinstance(k, str) and k.strip():
                a["title_ko"] = k.strip()[:140]
        print(f"  [번역] 완료: {len(todo)}건")
    except Exception as e:
        print(f"  [번역] 실패(무시): {e}", file=sys.stderr)


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

    translate_titles_ko(stocks_out)
    summarize_articles_ko(stocks_out)

    out = {"generated_at": TODAY.isoformat(),
           "generated_label": TODAY.strftime("%Y.%m.%d %H:%M"),
           "stocks": stocks_out}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] research.json 저장: {ok}/{len(targets)}개 종목 수집")


if __name__ == "__main__":
    main()
