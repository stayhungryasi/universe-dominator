#!/usr/bin/env python3
"""
무인 탐사선 보고서 렌더러 (agent_report) — 판단층을 읽어 공개용 HTML 로
================================================================================
`agent-research/` 는 로컬 수동 실행으로만 갱신되는 판단층이다. 여기서는 **읽기만**
하고, 빌드 시점에 정적 HTML 을 만든다(클라이언트 fetch 금지 — 공개 페이지가
판단층 파일을 직접 긁으면 필터를 우회할 길이 생긴다).

준법 필터 — 막으려는 것 한 문장:
    **"무엇을 얼마나 언제 사라"는 투자 권고가 공개 화면에 실리는 것.**

그 문장 그대로 검사한다:
  ① PHASE 4(현 시점 투자 판단) 절은 제목부터 다음 PHASE 머리글까지 통째로 제외
  ② 권고 문구가 든 블록(문단·목록항목·표행·인용)은 개별 제외
  ③ state 의 recommendation·최대 비중 계열 필드는 카드에 싣지 않는다

⚠️ 표면 단어로만 자르지 않는다(2026-08-22 오차단 교훈). 예를 들어 "저가 경쟁 진입",
   "성숙기 진입", "매출 내 비중 42%" 는 **권고가 아니라 분석**이다. 그래서 '진입'·'비중'
   같은 낱말 단독으로는 자르지 않고, 권고의 형태를 갖춘 표현만 자른다.
   자른 자리에는 안내 문구를 남긴다 — 조용히 사라지면 독자가 원문이 그런 줄 안다.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent.parent
ROOT = HERE / "agent-research"

# 권고의 형태를 갖춘 표현들. 낱말 하나가 아니라 **행위 지시**를 잡는다.
RECO_PHRASES = [
    "권고", "권장", "분할 매수", "분할 진입", "진입 방식", "포지션 규모",
    "최대 비중", "비중 0%", "비중 2%", "비중은 0%", "매수 개시", "매수 1차",
    "목표주가", "보유 비중",
]
CUT_NOTE = ("[투자 판단 관련 문단은 공개 화면에서 제외했습니다 — "
            "이 페이지는 관측 기록이지 권유가 아닙니다.]")


def read_state():
    try:
        return json.loads((ROOT / "state" / "spacex_state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def report_files():
    """reports/YYYY-MM.md 를 최신순으로. changelog 는 따로 다룬다."""
    try:
        return sorted((p for p in (ROOT / "reports").glob("*.md")
                       if re.fullmatch(r"\d{4}-\d{2}", p.stem)),
                      key=lambda p: p.stem, reverse=True)
    except Exception:
        return []


def read_changelog():
    try:
        return (ROOT / "reports" / "changelog.md").read_text(encoding="utf-8")
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────────
# 준법 필터 — 순수 함수. 테스트가 이 함수들을 직접 두드린다.
# ────────────────────────────────────────────────────────────────

def drop_phase4(md):
    """PHASE 4 절 제거 → (남은 본문, 제거 여부).

    제목이 'PHASE 4' 로 시작하는 머리글부터 **다음 같은 級 머리글 직전**까지.
    다음 절을 못 찾으면 문서 끝까지 자른다(뒤에 권고가 이어질 수 있으므로).
    """
    lines = (md or "").splitlines()
    out, cutting, found = [], False, False
    for ln in lines:
        m = re.match(r"^(#{2,3})\s*(.+)$", ln.strip())
        if m:
            title = m.group(2)
            if re.search(r"PHASE\s*4", title, re.I):
                cutting, found = True, True
                out.append("")
                out.append(CUT_NOTE)
                continue
            if cutting and len(m.group(1)) <= 2:      # 다음 ## 머리글에서 재개
                cutting = False
        if not cutting:
            out.append(ln)
    return "\n".join(out), found


def is_reco(text):
    """이 덩어리가 '권고'인가 — 낱말이 아니라 표현으로 판정한다."""
    t = text or ""
    if any(p in t for p in RECO_PHRASES):
        return True
    # '진입'·'비중'은 분석에서도 흔히 쓰인다. 매수 행위와 붙었을 때만 권고로 본다.
    if re.search(r"(진입|비중)", t) and re.search(r"(매수|매도|포지션|보유를|담는)", t):
        return True
    return False


def drop_reco_blocks(md):
    """권고 블록 제거 → (남은 본문, 제거 개수).

    블록 = 빈 줄로 갈리는 덩어리. 단 표는 행 단위로 본다 — 표 하나에 권고 행이
    섞였다고 표 전체를 지우면 분석까지 함께 사라진다.
    """
    blocks = re.split(r"\n\s*\n", md or "")
    out, cut = [], 0
    for b in blocks:
        lines = b.splitlines()
        if any(ln.lstrip().startswith("|") for ln in lines):
            kept = []
            for ln in lines:
                if ln.lstrip().startswith("|") and is_reco(ln):
                    cut += 1
                    continue
                kept.append(ln)
            if kept:
                out.append("\n".join(kept))
            continue
        if is_reco(b):
            cut += 1
            continue
        out.append(b)
    return "\n\n".join(out), cut


def sanitize(md):
    """공개 렌더용 본문 → (본문, {phase4, blocks})."""
    body, had4 = drop_phase4(md)
    body, n = drop_reco_blocks(body)
    return body, {"phase4": had4, "blocks": n}


# ────────────────────────────────────────────────────────────────
# 마크다운 → HTML (필요한 문법만. 외부 의존 없음)
# ────────────────────────────────────────────────────────────────

def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(t):
    t = esc(t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def md_to_html(md, base_level=3):
    """지원 문법: 머리글·표·목록·인용·구분선·문단. 그 외는 문단으로 흘린다."""
    html, lines, i = [], (md or "").splitlines(), 0
    ul = False

    def close_ul():
        nonlocal ul
        if ul:
            html.append("</ul>")
            ul = False

    while i < len(lines):
        ln = lines[i]
        st = ln.strip()
        if not st:
            close_ul()
            i += 1
            continue
        if st.startswith("---") and set(st) <= set("-"):
            close_ul()
            html.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s*(.+)$", st)
        if m:
            close_ul()
            lv = min(6, base_level + len(m.group(1)) - 1)
            html.append(f"<h{lv}>{_inline(m.group(2))}</h{lv}>")
            i += 1
            continue
        if st.startswith("|"):
            close_ul()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            body = [r for r in cells
                    if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
            if not body:
                continue
            head, rest = body[0], body[1:]
            t = "<div class='ag-tw'><table class='ag-table'><thead><tr>"
            t += "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            for r in rest:
                t += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
            html.append(t + "</tbody></table></div>")
            continue
        if st.startswith(">"):
            close_ul()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            html.append("<blockquote>" + "<br>".join(_inline(q) for q in quote if q)
                        + "</blockquote>")
            continue
        m = re.match(r"^[-*]\s+(.+)$", st) or re.match(r"^\d+\.\s+(.+)$", st)
        if m:
            if not ul:
                html.append("<ul>")
                ul = True
            html.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue
        close_ul()
        html.append(f"<p>{_inline(st)}</p>")
        i += 1
    close_ul()
    return "\n".join(html)
