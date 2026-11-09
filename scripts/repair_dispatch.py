#!/usr/bin/env python3
"""
자가 수리 에이전트 직접 호출 (repair_dispatch) — `python scripts/repair_dispatch.py now|future`
================================================================================
2026-11-04~09 실측: self-repair.yml 의 workflow_run 트리거는 GITHUB_TOKEN 이 띄운 데일리(시계 중계가 띄운다)가
끝날 때 울리지 않는다 — GitHub 규칙(GITHUB_TOKEN 이 일으킨 사건은 새 실행을 만들지 않는다, dispatch 만 예외).
시계 중계를 넣은 날부터 자가 수리는 한 번도 불리지 않았는데, 경성 실패 DM 은 27번 '자가 수리 에이전트가
수리안을 만듭니다'라고 약속했다. 이제 데일리(selftest 실패 시)와 시한폭탄 조기경보(미래 실패)가 직접 부른다.
"""
import os
import sys


def dispatch_repair(mode="now"):
    """→ 성공 여부. 저장소·토큰이 없으면(로컬·테스트) 아무것도 안 한다."""
    repo, tok = os.environ.get("GITHUB_REPOSITORY", "").strip(), os.environ.get("GITHUB_TOKEN", "").strip()
    if not (repo and tok):
        print("[수리 호출] 저장소·토큰 없음 — 생략")
        return False
    try:
        import requests
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/self-repair.yml/dispatches",
                          headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"},
                          json={"ref": "main", "inputs": {"mode": mode}}, timeout=30)
    except Exception as e:
        print(f"[수리 호출] 실패({type(e).__name__})")
        return False
    print(f"[수리 호출] self-repair({mode}) HTTP {r.status_code}")
    return r.status_code in (200, 204)


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "now"
    sys.exit(0 if dispatch_repair(m if m in ("now", "future") else "now") else 1)
