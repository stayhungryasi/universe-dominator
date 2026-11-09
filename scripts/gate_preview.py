#!/usr/bin/env python3
"""
다음 데일리 관문 미리보기 (gate_preview) — 다른 워크플로가 데이터를 커밋한 직후 selftest 를 돌려 본다
================================================================================
2026-11-06: 매시간 감시(spacex-watch)가 00:01 에 data/spacex_hourly.json 을 커밋했는데, 그 파일을 몰래 읽던
측정층 테스트가 깨져 08:15 데일리부터 3.5일·27회 멈췄다. 데일리의 '+0일 미리보기'(timebomb_probe)는 **데일리 자신의
산출물**만 본다 — 다른 워크플로가 그사이 커밋한 데이터는 다음 데일리의 selftest 에서야 처음 드러났다.

이 스크립트는 데이터를 커밋한 워크플로가 커밋 직후에 돌린다(지금은 매시간 감시 — 바뀐 회차만, 하루 몇 번).
실패하면 **다음 데일리 전에** 소장 DM + 자가 수리 에이전트 호출. 통과면 조용하다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent.parent


def run_selftest():
    """→ (통과 여부, 실패 상세[{label, got}])."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fails.json"
        r = subprocess.run([sys.executable, "scripts/selftest.py"], cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=dict(os.environ, SELFTEST_FAILS_OUT=str(out)))
        try:
            detail = json.loads(out.read_text(encoding="utf-8")).get("detail") or []
        except Exception:
            detail = []
        return r.returncode == 0, detail


def message(source, detail):
    lab = " / ".join(f"{d.get('label', '')} → {str(d.get('got', ''))[:140]}" for d in detail[:3])
    return (f"⚠️ UNIVERTRIX 다음 데일리 정지 예고 — {source} 커밋 직후 selftest 실패\n"
            f"· 실패: {lab or '실패 목록 없음(실행 기록 확인)'}\n"
            "· 고치지 않으면 다음 데일리(08:10·12:10·18:10)부터 수집이 멈춥니다.\n"
            "· 자가 수리 에이전트를 불렀습니다 — 수리안 PR 이 오면 Merge 하시면 됩니다.")


def main(source="매시간 감시", runner=run_selftest, send=None, dispatch=None):
    ok, detail = runner()
    if ok:
        print("[미리보기] 다음 데일리 selftest 통과")
        return 0
    print(f"[미리보기] 실패 {len(detail)}건 — 소장 DM + 자가 수리 호출")
    if send is None:
        sys.path.insert(0, str(HERE / "scripts"))
        from spacex_watch import send_dm as send
    if dispatch is None:
        sys.path.insert(0, str(HERE / "scripts"))
        from repair_dispatch import dispatch_repair as dispatch
    send(message(source, detail))
    dispatch("now")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "매시간 감시"))
