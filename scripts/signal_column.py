#!/usr/bin/env python3
"""
신호 심층 칼럼 (signal_column) — 필독 신호를 관측일지 칼럼으로 자동 승격
================================================================
필독(pin) 판정된 신호 중 아직 칼럼화되지 않은 최고 신호 1건을 골라
원문을 직접 수집해 읽고, Claude가 관측일지 문체의 상세분석 칼럼을 작성해
data/columns.json 맨 앞에 발행한다.

원칙:
  - 하루 최대 1편 (관측일지 오염 방지, data/signal_column_state.json)
  - 원문 수집 실패 시 제목·요약만으로 짧은 분석 (우아한 저하)
  - 발행되면 기존 사슬이 이어받는다: 관제탑 커뮤니티 공지 → 아침 브리핑
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SIG_PATH = DATA_DIR / "signals.json"
COLS_PATH = DATA_DIR / "columns.json"
STATE_PATH = DATA_DIR / "signal_column_state.json"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (UNIVERTRIX observatory; univertrix.com)"}


def fetch_article_text(url, limit=7000):
    """원문 본문 대강 추출 — 실패해도 빈 문자열로 우아하게"""
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        html = r.text
        html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z#0-9]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception as e:
        print(f"[신호칼럼] 원문 수집 실패({e}) — 요약 기반으로 진행", file=sys.stderr)
        return ""


def ask_claude(api_key, sig, article):
    ctx = {"제목": sig.get("ko") or sig["title"], "원제": sig["title"],
           "출처": sig.get("source", ""), "점수": sig.get("points"),
           "한줄": sig.get("why", ""), "URL": sig["url"]}
    prompt = f"""당신은 글로벌 시가총액 추적·AI 시대 대비 사이트 UNIVERTRIX(우주지배자)의 관측일지 필자입니다.
세계관: 기업=행성, 시총 1위=태양(왕좌). 문체: 우주 은유를 절제한 담백하고 격조 있는 한국어.
실리콘밸리의 필독 신호를 심층 분석하는 칼럼을 작성하세요.

신호 정보: {json.dumps(ctx, ensure_ascii=False)}
원문 발췌: {article[:6500] if article else "(원문 수집 불가 — 신호 정보와 당신의 지식으로 분석)"}

칼럼 구성 (각각 <h3> 소제목 + <p> 문단):
1. 무슨 일이 일어났나 — 사실 요약
2. 내용 해부 — 핵심 주장·기술·수치 (원문 근거)
3. 왜 중요한가 — 산업 판도와 AI 시대를 준비하는 개인의 관점
4. 우주지배자 관측 포인트 — 시총 순위표·가치사슬에 미칠 파장 (특정 종목 매수·매도 권유 금지)
마지막에 한 문장으로 맺기. 과장·확정적 예언 금지, 추정은 추정이라 표기.
분량: 문단 6~9개.

반드시 아래 JSON만 응답 (코드펜스·설명 금지):
{{"title": "칼럼 제목 (신호를 담되 60자 이내)", "summary": "한 줄 요약 100자 이내", "body": "<h3>·<p>만 쓴 HTML"}}"""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5", "max_tokens": 4500,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    text = re.sub(r"```(?:json)?", "", text).strip()
    col = json.loads(text[text.find("{"):text.rfind("}") + 1])
    assert col.get("title") and col.get("body")
    return col


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[신호칼럼] ANTHROPIC_API_KEY 미설정 — 건너뜀")
        return
    if not SIG_PATH.exists():
        print("[신호칼럼] signals.json 없음 — 건너뜀")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if state.get("last_date") == today:
        print("[신호칼럼] 오늘 이미 발행 — 침묵 (하루 1편)")
        return

    sig_data = json.loads(SIG_PATH.read_text(encoding="utf-8"))
    cutoff = (datetime.now(KST) - timedelta(days=2)).strftime("%Y-%m-%d")
    cands = [s for s in sig_data.get("signals", [])
             if s.get("pin") and not s.get("columned")
             and (s.get("captured") or "") >= cutoff]
    if not cands:
        print("[신호칼럼] 칼럼화할 필독 신호 없음 — 침묵")
        return
    sig = max(cands, key=lambda s: s.get("points") or 0)

    article = fetch_article_text(sig["url"])
    try:
        col = ask_claude(api_key, sig, article)
    except Exception as e:
        print(f"[신호칼럼] 생성 실패: {e} — 다음 실행에서 재시도", file=sys.stderr)
        sys.exit(0)

    src_line = ('<p style=\'font-size:12px;color:#8a8577\'>신호 출처: '
                f'<a href="{sig["url"]}" target="_blank" rel="noopener" '
                'style="color:#9c7a3a">원문 보기</a>'
                + (f' · Hacker News ▲{sig["points"]}' if sig.get("points") else "")
                + ' — 본 관측일지는 정보 제공 목적이며 특정 종목의 매수·매도 권유가 아닙니다. '
                  '자동 생성 분석으로 원문과 대조를 권합니다.</p>')

    cols = {"columns": []}
    if COLS_PATH.exists():
        try:
            cols = json.loads(COLS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    entry = {"date": datetime.now(KST).strftime("%Y.%m.%d"),
             "title": ("📡 " + col["title"])[:80],
             "summary": (col.get("summary") or "")[:120],
             "body": col["body"] + src_line,
             "auto": True, "viz": {"type": "constellation"}}
    cols["columns"] = [entry] + cols.get("columns", [])
    COLS_PATH.write_text(json.dumps(cols, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    sig["columned"] = True
    SIG_PATH.write_text(json.dumps(sig_data, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    STATE_PATH.write_text(json.dumps({"last_date": today}, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[신호칼럼] 발행 완료 — 「{entry['title']}」")


if __name__ == "__main__":
    main()
