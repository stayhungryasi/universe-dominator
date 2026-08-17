#!/usr/bin/env python3
"""
기발행 관측일지 차트 소급 수리 (일회성)
=====================================
사고: 칼럼 본문이 한 문서에 전부 주입되는데(renderColumns), 차트 SVG가 모두
같은 gradient id("sg","wg","wg2"...)를 쓰고 fill="url(#sg)" 에 폴백 색이 없었다.
→ 브라우저는 문서에서 첫 번째 #sg 만 해석하고, 그 정의가 display:none 인 다른
   칼럼 본문 안에 있으면 paint server 해석에 실패 → 막대가 아예 그려지지 않음.
   (수치 라벨은 literal #fff 이라 남아서 "허공에 뜬 숫자"로 보였다)

수리:
  A. 신호 칼럼 차트(render_chart 산출물) → 저장된 SVG를 역파싱해 새 렌더러로 재생성
     (가로 막대·단색·최소 6% 길이·연도 콤마 제거·라벨 줄바꿈)
  B. 그 외 차트(주간 칼럼·수기 칼럼) → 구조는 그대로 두고 gradient id 만
     칼럼별로 고유화해 충돌을 제거

본문 텍스트(<h3>·<p>)는 일절 건드리지 않는다. <div class="col-chart"> 블록만 교체.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from signal_column import render_chart  # noqa: E402

HERE = Path(__file__).parent.parent
COLS_PATH = HERE / "data" / "columns.json"

CHART_RE = re.compile(r'<div class="col-chart">[\s\S]*?</div>')
# 신호 칼럼 차트 식별자 — render_chart 만 이 각주를 단다
SIGNAL_MARK = "자료: 원문 기사에서 추출 (자동)"

TITLE_RE = re.compile(r'<text x="\d+" y="34" fill="#f2ba3c"[^>]*>([\s\S]*?)</text>')
SUB_RE = re.compile(r'<text x="\d+" y="56" fill="#8a94b8"[^>]*>([\s\S]*?)</text>')
# 세로 막대 시절: 값=#fff, 카테고리=#c8d0ea
VAL_RE = re.compile(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" fill="#fff"[^>]*>([\s\S]*?)</text>')
CAT_RE = re.compile(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" fill="#c8d0ea"[^>]*>([\s\S]*?)</text>')
NUM_RE = re.compile(r'^\s*(-?[\d,]*\.?\d+)\s*(.*)$')


def _unesc(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")).strip()


def parse_signal_chart(svg):
    """저장된 세로 막대 SVG → render_chart 가 먹는 차트 dict 로 역파싱"""
    t = TITLE_RE.search(svg)
    s = SUB_RE.search(svg)
    vals = VAL_RE.findall(svg)
    cats = CAT_RE.findall(svg)
    if len(vals) < 2 or len(vals) != len(cats):
        return None
    # x 좌표로 짝을 맞춘다 (같은 막대의 값·카테고리는 x가 같다)
    cat_by_x = {x: c for x, c in cats}
    series, units = [], []
    for x, vtxt in vals:
        label = cat_by_x.get(x)
        if label is None:
            return None
        m = NUM_RE.match(_unesc(vtxt))
        if not m:
            return None
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        units.append(m.group(2).strip())
        series.append({"label": _unesc(label), "value": value})
    # 단위는 막대들이 공유한다 — 가장 흔한 값을 채택
    unit = max(set(units), key=units.count) if units else ""
    return {"title": _unesc(t.group(1)) if t else "",
            "subtitle": _unesc(s.group(1)) if s else "",
            "unit": unit, "series": series}


BAR_RE = re.compile(r'<rect ([^>]*?)/>')


def clamp_legacy_bars(block, floor=0.06):
    """수기·주간 칼럼의 옛 막대 — 구조·문구는 그대로 두고 길이만 최소 6% 로 끌어올린다"""
    bars = []
    for m in BAR_RE.finditer(block):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        if not {"x", "y", "width", "height", "rx"} <= set(a):
            continue          # 배경 rect 등은 제외
        try:
            bars.append((m, float(a["x"]), float(a["y"]), float(a["width"]), float(a["height"])))
        except ValueError:
            continue
    if len(bars) < 2:
        return block, False
    hs = {round(b[4], 2) for b in bars}     # 높이 집합
    ws = {round(b[3], 2) for b in bars}     # 폭 집합
    horizontal = len(hs) == 1               # 높이가 일정 → 길이는 폭
    vertical = len(ws) == 1 and not horizontal
    if not (horizontal or vertical):
        return block, False

    mx = max(b[3] if horizontal else b[4] for b in bars) or 1
    lo = mx * floor

    changed = False
    out, last = [], 0
    for m, x, y, w, h in bars:
        cur = w if horizontal else h
        if cur >= lo:
            continue
        new = round(lo, 1)
        s = m.group(0)
        if horizontal:
            s2 = re.sub(r'width="[\d.]+"', f'width="{new}"', s, count=1)
        else:
            # 세로 막대는 바닥(축)을 고정한 채 위로 늘린다
            s2 = re.sub(r'height="[\d.]+"', f'height="{new}"', s, count=1)
            s2 = re.sub(r'y="[\d.]+"', f'y="{round(y + h - new, 1)}"', s2, count=1)
        out.append(block[last:m.start()])
        out.append(s2)
        last = m.end()
        changed = True
    if not changed:
        return block, False
    out.append(block[last:])
    return "".join(out), True


def uniquify_ids(block, tag):
    """gradient 등 id 를 칼럼별로 고유화 + url() 참조도 함께 갱신"""
    ids = set(re.findall(r'<(?:linearGradient|radialGradient|filter|clipPath|pattern|mask)\s[^>]*id="([^"]+)"', block))
    if not ids:
        return block, False
    for i in sorted(ids, key=len, reverse=True):
        block = re.sub(r'(<(?:linearGradient|radialGradient|filter|clipPath|pattern|mask)\s[^>]*id=")' + re.escape(i) + r'(")',
                       lambda m: m.group(1) + i + "_" + tag + m.group(2), block)
        block = block.replace(f'url(#{i})', f'url(#{i}_{tag})')
    return block, True


def main():
    data = json.loads(COLS_PATH.read_text(encoding="utf-8"))
    cols = data.get("columns", [])
    regen = reid = failed = 0

    clamped = 0

    for idx, col in enumerate(cols):
        body = col.get("body") or ""
        if "col-chart" not in body:
            continue
        seq = [0]

        def repl(m, _idx=idx, _seq=seq):
            nonlocal regen, reid, failed, clamped
            block = m.group(0)
            _seq[0] += 1
            tag = f"c{_idx}x{_seq[0]}"      # 차트 블록마다 고유 — 같은 칼럼 안에서도 충돌 없음
            if SIGNAL_MARK in block:
                ch = parse_signal_chart(block)
                if ch:
                    new = render_chart(ch)
                    if new:
                        regen += 1
                        return new
                failed += 1
                print(f"  [경고] col{_idx}: 신호 차트 역파싱 실패 — 최소 수리만 적용")
            block, did_clamp = clamp_legacy_bars(block)
            if did_clamp:
                clamped += 1
            out, changed = uniquify_ids(block, tag)
            if changed:
                reid += 1
            return out

        new_body = CHART_RE.sub(repl, body)
        if new_body != body:
            col["body"] = new_body

    COLS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[소급수리] 신호 차트 재생성 {regen}건 / 그 외 id 고유화 {reid}건 / "
          f"옛 막대 최소길이 보정 {clamped}건 / 역파싱 실패 {failed}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
