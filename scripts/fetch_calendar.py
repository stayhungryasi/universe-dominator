"""
fetch_calendar.py — 시장 주요일정 자동 수집

3층 구조:
  ① 고정 일정 (data/calendar_fixed.json) — FOMC 등 공표된 확정 일정. 수동 관리(연 1회).
  ② 실적발표 — Finnhub 무료 API (FINNHUB_API_KEY 없으면 건너뜀)
       대상: 우주지배자(전 지역) + 잠재지배자 중 미국 상장 티커
  ③ 뉴스 이벤트 — 이미 수집된 research.json 기사에서 Claude(Haiku)가
       미래 일정(실적 예정·상장·분할 등)만 추출 (ANTHROPIC_API_KEY 없으면 건너뜀)

출력: data/calendar.json  (오늘 이후 60일 내 일정, 날짜순)
모든 층은 실패해도 서로 독립 — 파이프라인 안 깨짐.
"""
import os
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone, date

import requests

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)
TODAY_D = TODAY.date()

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
LATEST_PATH = DATA_DIR / "latest.json"
RESEARCH_PATH = DATA_DIR / "research.json"
FIXED_PATH = DATA_DIR / "calendar_fixed.json"
OUT_PATH = DATA_DIR / "calendar.json"

HORIZON_DAYS = 60   # 오늘부터 N일 내 일정만
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


# ───────────────────────── 대상 종목 ─────────────────────────
def load_watch():
    """우주(전 지역)+잠재 종목 → {us_ticker: name}. 미국형 티커(점 없음)만."""
    watch = {}
    try:
        d = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        pools = []
        for region in d.get("regions", {}).values():
            stocks = region.get("stocks", region) if isinstance(region, dict) else region
            pools.extend(stocks or [])
        pools.extend(d.get("latent", []))
        for s in pools:
            tk = (s.get("ticker") or "").strip().upper()
            if tk and "." not in tk and tk not in watch:
                watch[tk] = s.get("name", tk)
    except Exception as e:
        print(f"[warn] watch 목록 실패: {e}", file=sys.stderr)
    return watch


def in_horizon(dstr):
    try:
        dd = datetime.strptime(dstr, "%Y-%m-%d").date()
        return TODAY_D <= dd <= TODAY_D + timedelta(days=HORIZON_DAYS)
    except Exception:
        return False


# ───────────────────────── ① 고정 일정 ─────────────────────────
def load_fixed():
    if not FIXED_PATH.exists():
        return []
    try:
        items = json.loads(FIXED_PATH.read_text(encoding="utf-8")).get("events", [])
        out = [e for e in items if in_horizon(e.get("date", ""))]
        print(f"  [고정] {len(out)}건 (지평 내)")
        return out
    except Exception as e:
        print(f"  [고정] 읽기 실패: {e}", file=sys.stderr)
        return []


# ───────────────────────── ② 실적발표 (Finnhub) ─────────────────────────
def fetch_earnings(watch):
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        print("  [실적] FINNHUB_API_KEY 없음 — 건너뜀 (finnhub.io 무료 가입 후 Secrets 등록 시 활성화)")
        return []
    frm = TODAY_D.strftime("%Y-%m-%d")
    to = (TODAY_D + timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%d")
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}&token={key}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code != 200:
            print(f"  [실적] HTTP {r.status_code} — 건너뜀", file=sys.stderr)
            return []
        rows = r.json().get("earningsCalendar", [])
    except Exception as e:
        print(f"  [실적] 실패: {e}", file=sys.stderr)
        return []
    out, seen = [], set()
    for x in rows:
        sym = (x.get("symbol") or "").upper()
        dstr = x.get("date") or ""
        if sym in watch and in_horizon(dstr) and (sym, dstr) not in seen:
            seen.add((sym, dstr))
            hour = {"bmo": "장전", "amc": "장후", "dmh": "장중"}.get(x.get("hour", ""), "")
            out.append({
                "date": dstr, "type": "earnings",
                "title": f"{watch[sym]} ({sym}) 실적 발표" + (f" · {hour}" if hour else ""),
                "ticker": sym,
            })
    print(f"  [실적] 관심종목 {len(out)}건 / 전체 {len(rows)}건")
    return out


# ───────────────────────── ③ 뉴스 이벤트 (Claude) ─────────────────────────
def extract_news_events():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not RESEARCH_PATH.exists():
        print("  [뉴스이벤트] 키 또는 research.json 없음 — 건너뜀")
        return []
    try:
        research = json.loads(RESEARCH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    arts = []
    for s in research.get("stocks", []):
        for a in s.get("articles", []):
            arts.append({"stock": s.get("name", ""), "title": a.get("title", ""),
                         "summary": " ".join(a.get("summary_ko", []))[:200]})
    if not arts:
        return []
    prompt = (
        f"오늘은 {TODAY_D.isoformat()} 입니다. 아래 기사 목록에서 **미래의 확정된 시장 일정**만 추출하세요 "
        "(실적 발표 예정일, 상장/ADR 상장, 주식 분할, 신제품 발표 행사, 컨퍼런스 등). "
        "규칙: 기사에 날짜가 명시된 미래 일정만. 추측·과거·모호한 것은 제외. 없으면 빈 배열. "
        'JSON 배열만 출력: [{"date":"YYYY-MM-DD","title":"한국어 한 줄","type":"event"}]\\n\\n'
        + json.dumps(arts[:80], ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 3000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"  [뉴스이벤트] API HTTP {r.status_code}", file=sys.stderr)
            return []
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        i, j = text.find("["), text.rfind("]")
        if i == -1 or j <= i:
            return []
        events = json.loads(text[i:j + 1])
    except Exception as e:
        print(f"  [뉴스이벤트] 실패(무시): {e}", file=sys.stderr)
        return []
    out = []
    for e in events:
        if isinstance(e, dict) and in_horizon(e.get("date", "")) and e.get("title"):
            out.append({"date": e["date"], "type": "event",
                        "title": str(e["title"])[:120]})
    print(f"  [뉴스이벤트] {len(out)}건 추출")
    return out


# ───────────────────────── main ─────────────────────────
def main():
    print(f"[calendar] 시작 {TODAY.isoformat()}")
    watch = load_watch()
    print(f"  관심종목(미국형 티커) {len(watch)}개")

    events = []
    events += load_fixed()
    events += fetch_earnings(watch)
    events += extract_news_events()

    # 중복 제거 (date+title 유사) 후 날짜순
    seen, dedup = set(), []
    for e in events:
        k = (e["date"], re.sub(r"\s+", "", e["title"])[:40])
        if k not in seen:
            seen.add(k)
            dedup.append(e)
    dedup.sort(key=lambda e: (e["date"], e.get("type", "")))

    out = {"generated_at": TODAY.isoformat(),
           "generated_label": TODAY.strftime("%Y.%m.%d %H:%M"),
           "events": dedup}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] calendar.json 저장: {len(dedup)}건 (향후 {HORIZON_DAYS}일)")


if __name__ == "__main__":
    main()
