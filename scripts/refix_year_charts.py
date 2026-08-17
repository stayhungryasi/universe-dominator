#!/usr/bin/env python3
"""
연도 차트 → 시간선 소급 전환 (일회성)
====================================
연도 계열을 0 기준 막대로 그리면 2008년과 2026년의 길이가 사실상 같아 아무것도
읽히지 않는다. 새 render_chart 는 연도 계열을 가로 시간선(점+라벨)으로 그린다.
이 스크립트는 **연도 차트를 가진 칼럼의 해당 차트 블록만** 다시 렌더한다.

계열 복원은 차트 수리 직전 스냅샷(BASE_REV)의 세로 막대 SVG에서 한다.
그쪽이 값·라벨이 1:1로 박혀 있어 역파싱이 정확하다. 현재 본문의 차트 블록과는
칼럼별 등장 순서로 짝을 맞춘다(수리 때 1:1 치환했으므로 순서가 보존돼 있다).

본문 텍스트(<h3>·<p>)와 연도가 아닌 차트는 건드리지 않는다.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from signal_column import render_chart, _is_year_series  # noqa: E402
from refix_column_charts import CHART_RE, SIGNAL_MARK, parse_signal_chart  # noqa: E402

HERE = Path(__file__).parent.parent
COLS_PATH = HERE / "data" / "columns.json"
BASE_REV = "20e55b2"          # 차트 수리 직전 커밋 (세로 막대 원본)


def load_base():
    r = subprocess.run(["git", "show", f"{BASE_REV}:data/columns.json"],
                       cwd=str(HERE), capture_output=True)
    if r.returncode:
        raise SystemExit(f"기준 스냅샷을 읽지 못했습니다 ({BASE_REV}): "
                         f"{r.stderr.decode('utf-8', 'replace')[:200]}")
    return json.loads(r.stdout.decode("utf-8"))


def main():
    data = json.loads(COLS_PATH.read_text(encoding="utf-8"))
    base = load_base()
    cols, bcols = data.get("columns", []), base.get("columns", [])

    # 기준 스냅샷의 칼럼을 (날짜, 제목)으로 찾는다 — 새 칼럼이 앞에 붙어도 안전
    bindex = {(c.get("date"), c.get("title")): c for c in bcols}

    swapped = skipped_cols = 0
    for col in cols:
        body = col.get("body") or ""
        if "col-chart" not in body:
            continue
        bcol = bindex.get((col.get("date"), col.get("title")))
        if not bcol:
            skipped_cols += 1
            continue
        bblocks = CHART_RE.findall(bcol.get("body") or "")
        cur = CHART_RE.findall(body)
        if len(bblocks) != len(cur):
            skipped_cols += 1
            print(f"  [건너뜀] {col.get('date')} {col.get('title', '')[:24]} — 차트 개수 불일치")
            continue

        seq = [-1]

        def repl(m, _b=bblocks, _seq=seq, _col=col):
            nonlocal swapped
            _seq[0] += 1
            old = _b[_seq[0]]
            if SIGNAL_MARK not in old:
                return m.group(0)
            ch = parse_signal_chart(old)
            if not ch:
                return m.group(0)
            series = [(str(s["label"]), float(s["value"])) for s in ch["series"]]
            if not _is_year_series(series, ch.get("unit", "")):
                return m.group(0)
            new = render_chart(ch)
            if not new:
                return m.group(0)
            swapped += 1
            print(f"  [전환] {_col.get('date')} {_col.get('title', '')[:26]} "
                  f"— {ch.get('title', '')[:30]}")
            return new

        new_body = CHART_RE.sub(repl, body)
        if new_body != body:
            col["body"] = new_body

    COLS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[연도차트 소급] 시간선 전환 {swapped}건 / 대조 불가로 건너뛴 칼럼 {skipped_cols}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
