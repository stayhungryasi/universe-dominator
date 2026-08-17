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


def backfill_column_urls():
    """기존 칼럼에 원문 url 소급 부여 (멱등).

    2026-08 이전 칼럼 레코드에는 url 필드가 없다. 하지만 본문 하단 '신호 출처' 줄에
    원문 링크가 그대로 박혀 있으므로 거기서 정확히 되찾는다(추측 아님).
    url이 있어야 해부실('해부실에서 보기' 버튼)과 연결된다.
    """
    if not COLS_PATH.exists():
        return
    try:
        data = json.loads(COLS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    pat = re.compile(r'신호 출처:[\s\S]{0,200}?<a href="([^"]+)"')
    n = 0
    for c in data.get("columns", []):
        if c.get("url"):
            continue
        m = pat.search(c.get("body") or "")
        if m:
            c["url"] = m.group(1)
            n += 1
    if n:
        COLS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"[신호칼럼] 기존 칼럼 원문 url 소급 연결: {n}건")


def _svg_esc(s):
    """SVG 텍스트 노드 이스케이프 — 라벨에 & < > 가 섞여도 문서가 깨지지 않게"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _text_w(s, fs):
    """문자열의 대략적 렌더 폭(px) — 한글·전각은 1.0em, 그 외는 0.55em로 추정"""
    w = 0.0
    for ch in str(s):
        w += fs * (1.0 if ord(ch) > 0x2000 else 0.55)
    return w


def _wrap(label, fs, maxw, max_lines=2):
    """폭 안에 들어가도록 줄바꿈 — 공백이 있으면 공백 기준, 없으면 글자 기준"""
    if _text_w(label, fs) <= maxw:
        return [label]
    lines, cur = [], ""
    for tok in (label.split(" ") if " " in label else list(label)):
        sep = " " if (" " in label and cur) else ""
        cand = cur + sep + tok
        if cur and _text_w(cand, fs) > maxw:
            lines.append(cur)
            cur = tok
            if len(lines) == max_lines:
                break
        else:
            cur = cand
    if len(lines) < max_lines and cur:
        lines.append(cur)
    return lines[:max_lines]


def _fmt_value(v, unit, label=""):
    """수치 표기 — 연도류는 천단위 콤마 없이 원형으로"""
    is_year = (float(v).is_integer() and 1000 <= abs(v) <= 2100
               and ("년" in unit or "년" in label or not unit))
    if is_year:
        num = f"{v:.0f}"          # 2008년 — 콤마 금지
    elif float(v).is_integer():
        num = f"{v:,.0f}"
    else:
        num = f"{v:g}"
    return num + unit


def _is_year_series(series, unit):
    """전 항목이 연도인가 — 0 기준 막대로 그리면 길이가 거의 같아 의미가 없는 계열

    판정: 정수이면서 1900~2100 범위 + 단위가 비었거나 '년' 표시가 있을 것.
    ('%'·'건' 같은 실제 단위가 붙은 1900~2100 값은 연도가 아니라 수치로 본다)
    """
    if len(series) < 2:
        return False
    for label, v in series:
        if not float(v).is_integer() or not (1900 <= abs(v) <= 2100):
            return False
        if unit and "년" not in unit and "년" not in label:
            return False
    return True


def _render_timeline(title, subtitle, unit, series):
    """연도 계열 → 가로 시간선 위의 점 + 라벨

    막대는 0을 기준으로 길이를 재므로 2008년과 2026년이 사실상 같은 길이가 된다.
    시간선은 값의 '간격'을 그대로 x 위치로 옮겨 연도 사이의 거리를 보여준다.
    """
    W = 700
    AX0, AX1 = 74, 626           # 시간선 양 끝
    AY = 156                     # 시간선 y
    H = 250
    events = sorted(series, key=lambda t: t[1])
    lo = min(v for _, v in events)
    hi = max(v for _, v in events)

    o = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    o.append(f'<rect width="{W}" height="{H}" fill="#10141f"/>')
    o.append(f'<text x="26" y="34" fill="#f2ba3c" font-size="18" font-weight="800">{title}</text>')
    o.append(f'<text x="26" y="56" fill="#8a94b8" font-size="12" font-weight="600">{subtitle}</text>')
    o.append(f'<line x1="{AX0-30}" y1="{AY}" x2="{AX1+30}" y2="{AY}" stroke="#3a4568" stroke-width="2"/>')

    fs, yfs, maxw = 11.5, 13.5, 168
    placed = []
    for i, (label, v) in enumerate(events):
        # 전부 같은 해면 시간 위에 흩어놓지 않고 한 점에 모은다 (사실대로)
        x = AX0 + (v - lo) / (hi - lo) * (AX1 - AX0) if hi > lo else (AX0 + AX1) / 2
        lines = _wrap(label, fs, maxw)
        ytxt = _fmt_value(v, unit, label)
        halfw = max([_text_w(ln, fs) for ln in lines] + [_text_w(ytxt, yfs)]) / 2
        placed.append({"x": x, "lines": lines, "ytxt": ytxt, "halfw": halfw,
                       "above": i % 2 == 0})

    # 같은 쪽 라벨끼리 겹치지 않게 두 번 훑는다 (점은 실제 연도 위치에 고정)
    GAP = 8
    for above in (True, False):
        side = [q for q in placed if q["above"] is above]
        edge = -1e9
        for p in side:                                  # ① 왼→오: 오른쪽으로 밀어내기
            lx = min(max(p["x"], 26 + p["halfw"]), W - 26 - p["halfw"])
            p["lx"] = max(lx, edge + GAP + p["halfw"])
            edge = p["lx"] + p["halfw"]
        edge = 1e9
        for p in reversed(side):                        # ② 오→왼: 오른쪽 끝 넘침을 되밀기
            p["lx"] = max(min(p["lx"], edge - GAP - p["halfw"], W - 26 - p["halfw"]),
                          26 + p["halfw"])
            edge = p["lx"] - p["halfw"]

    for p in placed:
        x, lx, up = p["x"], p["lx"], p["above"]
        sign = -1 if up else 1
        # 점이 라벨과 어긋났으면 잇는 선을 그어 짝을 알아보게 한다
        o.append(f'<line x1="{x:.1f}" y1="{AY + sign*10}" x2="{lx:.1f}" y2="{AY + sign*22}" '
                 f'stroke="#5a648a" stroke-width="1"/>')
        o.append(f'<circle cx="{x:.1f}" cy="{AY}" r="6" fill="#f2ba3c"/>')
        o.append(f'<circle cx="{x:.1f}" cy="{AY}" r="2.4" fill="#10141f"/>')
        ybase = AY - 30 if up else AY + 38
        o.append(f'<text x="{lx:.1f}" y="{ybase}" text-anchor="middle" fill="#f2ba3c" '
                 f'font-size="{yfs:g}" font-weight="800">{_svg_esc(p["ytxt"])}</text>')
        for k, ln in enumerate(p["lines"]):
            if up:
                ly = ybase - 18 - (len(p["lines"]) - 1 - k) * 14
            else:
                ly = ybase + 18 + k * 14
            o.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" fill="#e6ebfa" '
                     f'font-size="{fs:g}" font-weight="700">{_svg_esc(ln)}</text>')

    o.append(f'<text x="26" y="{H-14}" fill="#5a648a" font-size="10">자료: 원문 기사에서 추출 (자동)</text>')
    o.append(f'<text x="{W-26}" y="{H-14}" text-anchor="end" fill="#9c7a3a" '
             f'font-size="11.5" font-weight="800">UNIVERTRIX · univertrix.com</text>')
    o.append('</svg>')
    return '<div class="col-chart">' + ''.join(o) + '</div>'


def render_chart(ch):
    """추출 수치 → UNIVERTRIX 표준 가로 막대 차트 SVG

    가로 막대인 이유: 한글 카테고리 라벨을 자르지 않고 왼쪽에 온전히 놓을 수 있고,
    수치 라벨을 막대 끝에 붙일 수 있다.
    색은 url(#gradient) 대신 단색 — 같은 문서에 여러 칼럼 본문이 동시에 주입되므로
    id 충돌·display:none 하위 참조로 paint server가 해석되지 않는 사고를 원천 차단한다.
    """
    try:
        raw = [(str(s["label"]), float(s["value"])) for s in ch.get("series", [])
               if isinstance(s, dict) and s.get("label") is not None][:6]
        series = [(l, v) for l, v in raw if v == v and abs(v) != float("inf") and abs(v) < 1e15]
        if len(series) < 2:
            return ""
        title = _svg_esc(str(ch.get("title", ""))[:40])
        subtitle = _svg_esc(str(ch.get("subtitle", ""))[:60])
        unit = str(ch.get("unit", ""))[:8]

        # 연도 계열은 막대가 아니라 시간선으로 — 0 기준 막대는 길이 차가 거의 없다
        if _is_year_series(series, unit):
            return _render_timeline(title, subtitle, unit, series)

        maxv = max(abs(v) for _, v in series) or 1
        n = len(series)

        W = 700
        LX = 26                      # 라벨 시작 x
        LW = 178                     # 라벨 영역 폭
        BX = LX + LW + 12            # 막대 시작 x
        VW = 96                      # 수치 라벨 자리
        TRACK = W - 26 - VW - BX     # 막대가 쓸 수 있는 최대 길이
        TOP = 84                     # 제목·부제 아래
        ROW = 46
        FOOT = 40
        H = TOP + ROW * n + FOOT

        o = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
        o.append(f'<rect width="{W}" height="{H}" fill="#10141f"/>')
        o.append(f'<text x="{LX}" y="34" fill="#f2ba3c" font-size="18" font-weight="800">{title}</text>')
        o.append(f'<text x="{LX}" y="56" fill="#8a94b8" font-size="12" font-weight="600">{subtitle}</text>')
        o.append(f'<line x1="{BX-6}" y1="{TOP-8}" x2="{BX-6}" y2="{TOP + ROW*n - 6}" stroke="#3a4568"/>')

        for i, (label, v) in enumerate(series):
            top = TOP + ROW * i
            cy = top + ROW / 2 - 6           # 이 행의 세로 중심
            bh = 22
            by = cy - bh / 2
            # 최솟값도 반드시 보이도록 트랙의 6% 하한
            bw = max(abs(v) / maxv * TRACK, TRACK * 0.06)
            color = "#f2ba3c" if v >= 0 else "#5b8def"

            # 카테고리 라벨 — 자르지 않고 폭에 맞춰 축소·줄바꿈
            fs = 12.5
            lines = _wrap(label, fs, LW)
            if len(lines) > 1:
                fs = 11.0
                lines = _wrap(label, fs, LW)
            while len(lines) > 1 and _text_w(lines[0], fs) > LW and fs > 9.5:
                fs -= 0.5
                lines = _wrap(label, fs, LW)
            if len(lines) == 1:
                o.append(f'<text x="{LX}" y="{cy+4:.1f}" fill="#e6ebfa" font-size="{fs:g}" '
                         f'font-weight="700">{_svg_esc(lines[0])}</text>')
            else:
                for k, ln in enumerate(lines):
                    ly = cy - 2 + k * (fs + 2.5)
                    o.append(f'<text x="{LX}" y="{ly:.1f}" fill="#e6ebfa" font-size="{fs:g}" '
                             f'font-weight="700">{_svg_esc(ln)}</text>')

            o.append(f'<rect x="{BX}" y="{by:.1f}" width="{bw:.1f}" height="{bh}" rx="6" fill="{color}"/>')
            vtxt = _svg_esc(_fmt_value(v, unit, label))
            o.append(f'<text x="{BX + bw + 9:.1f}" y="{cy+5:.1f}" fill="#ffffff" '
                     f'font-size="14" font-weight="800">{vtxt}</text>')

        o.append(f'<text x="{LX}" y="{H-14}" fill="#5a648a" font-size="10">자료: 원문 기사에서 추출 (자동)</text>')
        o.append(f'<text x="{W-26}" y="{H-14}" text-anchor="end" fill="#9c7a3a" '
                 f'font-size="11.5" font-weight="800">UNIVERTRIX · univertrix.com</text>')
        o.append('</svg>')
        return '<div class="col-chart">' + ''.join(o) + '</div>'
    except Exception:
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

추가로, 원문에서 수치(건수·퍼센트·금액·연도별 값 등)를 찾아 차트 데이터를 뽑으세요.
- 원문에 수치가 하나라도 있으면 charts에 1~2개를 반드시 구성 (막대 2~6개, 비교·추이·비율 등)
- 수치가 정말 전무할 때만 charts를 빈 배열로

반드시 아래 JSON만 응답 (코드펜스·설명 금지):
{{"title": "칼럼 제목 (신호를 담되 60자 이내)", "summary": "한 줄 요약 100자 이내", "body": "<h3>·<p>만 쓴 HTML",
"charts": [{{"title": "차트 제목", "subtitle": "부제·단위 설명", "unit": "단위(%·건·B 등, 없으면 빈 문자열)", "series": [{{"label": "항목", "value": 숫자}}]}}]}}"""
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
    # 해부실 연결용 소급 작업 — API 키·발행 여부와 무관하게 매 실행 먼저 수행
    backfill_column_urls()

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
    # 이중 잠금: 상태 파일이 유실돼도 columns.json에 오늘 자 📡 칼럼이 있으면 침묵
    if COLS_PATH.exists():
        try:
            today_dot = datetime.now(KST).strftime("%Y.%m.%d")
            existing = json.loads(COLS_PATH.read_text(encoding="utf-8")).get("columns", [])
            if any(c.get("title", "").startswith("📡") and c.get("date") == today_dot
                   for c in existing):
                print("[신호칼럼] columns.json에 오늘 자 📡 칼럼 존재 — 침묵 (이중 잠금)")
                STATE_PATH.write_text(json.dumps({"last_date": today}, ensure_ascii=False),
                                      encoding="utf-8")
                return
        except Exception:
            pass
    # 이중 안전벨트: 상태 파일이 유실·경합돼도 columns.json에 오늘 📡 칼럼이 있으면 침묵
    today_dot = datetime.now(KST).strftime("%Y.%m.%d")
    if COLS_PATH.exists():
        try:
            existing = json.loads(COLS_PATH.read_text(encoding="utf-8")).get("columns", [])
            if any(c.get("date") == today_dot and (c.get("title") or "").startswith("📡")
                   for c in existing):
                print("[신호칼럼] columns.json에 오늘 📡 칼럼 존재 — 침묵 (이중 안전벨트)")
                STATE_PATH.write_text(json.dumps({"last_date": today}, ensure_ascii=False),
                                      encoding="utf-8")
                return
        except Exception:
            pass

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
    charts_html = "".join(render_chart(ch) for ch in (col.get("charts") or [])[:2])
    if charts_html:
        print(f"[신호칼럼] 원문 수치 차트 {charts_html.count('<svg')}개 동봉")
    entry = {"date": datetime.now(KST).strftime("%Y.%m.%d"),
             "title": ("📡 " + col["title"])[:80],
             "summary": (col.get("summary") or "")[:120],
             "body": col["body"] + charts_html + src_line,
             "url": sig["url"],   # 해부실 연결 키 (fetch_translations의 id 산출 근거)
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
