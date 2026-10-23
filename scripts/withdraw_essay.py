#!/usr/bin/env python3
"""
에세이 철회 — '데이터 수정 → 재빌드 → quick 배포 → 라이브 0회 확인'을 한 동작으로 (2026-10-23)
================================================================================
사고: Anthropic 에세이를 data/essays.json 에서만 철회했다. 루트 journal.html 은 옛 빌드
그대로라 본문('ARR $65B' 포함)이 라이브에 남았다. 데이터를 고친 것과 독자가 못 보게 된
것은 다르다 — 철회는 **라이브에서 사라진 것을 확인할 때까지** 끝나지 않는다.

사용:
  python scripts/withdraw_essay.py --slug SLUG --reason "사유" [--correction "정정 문구"]
  python scripts/withdraw_essay.py --verify          # 표식이 남은 철회의 라이브 확인만
  옵션: --no-deploy (데이터·빌드·로컬 확인까지만) · --timeout 900

단계 (하나라도 실패하면 멈추고 무엇이 남았는지 말한다):
  1. git pull (자동 커밋 파일은 원격 최신 기준)
  2. 철회 기록(status=withdrawn·사유·일시·정정 줄·pending_deploy 표식) + 하루 1편 잠금 해제
  3. build_site 로 루트 HTML 재빌드 → 철회 제목 0회 확인(로컬)
  4. 데이터 + 루트 HTML 커밋·푸시 → daily-update quick 실행 → 완료 대기
  5. 라이브(univertrix.com) 페이지에서 제목 0회 확인 → pending_deploy 표식 해제 커밋

selftest 와 verify_pages 는 철회 제목이 루트 HTML 에 남아 있으면 실패한다 — 데이터만 고치고
이 도구를 건너뛰면 다음 파이프라인 회차가 selftest 에서 멈춘다(시끄러운 실패가 의도다).
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import companion_essays as ce   # noqa: E402

ROOT = Path(__file__).parent.parent
LIVE = "https://univertrix.com/"
PAGES = ["journal"]            # 에세이가 렌더되는 공개 페이지(확장자 없는 주소 — 308 리다이렉트 대응)


def sh(*args, check=True):
    r = subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"[철회] 실패: {' '.join(args)}\n{r.stderr.strip()[:500]}")
    return r


def root_pages():
    return [(f.name, f.read_text(encoding="utf-8", errors="replace")) for f in sorted(ROOT.glob("*.html"))]


def build(tries=4):
    """로컬 재빌드 — 윈도우 원자적 교체 errno 22 플레이크는 재시도(메모리 기록)."""
    for i in range(tries):
        r = sh(sys.executable, "scripts/build_site.py", check=False)
        if r.returncode == 0:
            return
        print(f"[철회] 빌드 재시도 {i + 1}/{tries}", file=sys.stderr)
    raise SystemExit("[철회] 빌드 실패 — 루트 HTML 이 갱신되지 않았다(철회 미완)")


def live_leaks(doc):
    import requests
    pages = []
    for p in PAGES:
        url = f"{LIVE}{p}?nocache={int(time.time())}"
        r = requests.get(url, timeout=30, headers={"Cache-Control": "no-cache"})
        r.raise_for_status()
        pages.append((p, r.text))
    return ce.html_leaks(doc, pages)


def wait_run(run_id, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = sh("gh", "run", "view", str(run_id), "--json", "status,conclusion",
                "-q", '.status+" "+.conclusion', check=False).stdout.strip()
        if st.startswith("completed"):
            return st
        time.sleep(20)
    return "timeout"


def verify_live(timeout):
    """표식이 남은 철회를 라이브에서 확인 → 사라진 것만 표식 해제 커밋."""
    doc = ce.load_essays()
    pend = [s for p in doc.get("pending_deploy", []) for s in p.get("slugs", [])]
    if not pend:
        print("[철회] 라이브 확인할 표식 없음")
        return 0
    t0 = time.time()
    while True:
        leaks = live_leaks(doc)
        left = {slug for _, slug in leaks}
        done = [s for s in pend if s not in left]
        if not left or time.time() - t0 > timeout:
            break
        print(f"[철회] 라이브에 아직 남음: {sorted(left)} — 30초 뒤 재확인")
        time.sleep(30)
    if done:
        ce.clear_pending(doc, set(done))
        ce.save_essays(doc)
        sh("git", "add", "data/essays.json")
        sh("git", "commit", "-m", "chore(companion): 철회 라이브 확인 — " + ", ".join(done)
           + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        sh("git", "pull", "--rebase")
        sh("git", "push")
    print(f"[철회] 라이브 0회 확인: {done} · 남음: {sorted(left)}")
    return 0 if not left else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", default=[])
    ap.add_argument("--reason", default="")
    ap.add_argument("--correction", default="")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    if a.verify:
        return verify_live(a.timeout)
    if not a.slug or not a.reason:
        ap.error("--slug 와 --reason 이 필요하다")

    sh("git", "pull", "--rebase")
    doc = ce.load_essays()
    when = datetime.now(ce.KST).strftime("%Y-%m-%d %H:%M KST")
    targets = [e for e in doc["essays"] if e.get("slug") in a.slug]
    if not targets:
        raise SystemExit(f"[철회] 원장에 없는 슬러그: {a.slug}")
    corr = None
    if a.correction:
        corr = {"date": when[:10], "company": targets[0].get("company"), "text": a.correction,
                "slugs": a.slug}
    done = ce.withdraw(doc, set(a.slug), a.reason, when, correction=corr)
    ce.save_essays(doc)
    st = ce.load_state()
    for co in {e.get("company") for e in targets}:
        pub = [e["date"] for e in doc["essays"] if e.get("company") == co and ce.is_public(e)]
        st["published"][co] = max(pub) if pub else ""
    ce.save_state(st)
    print(f"[철회] 데이터 반영: {done or '(이미 철회됨)'}")

    build()
    leaks = ce.html_leaks(doc, root_pages())
    if leaks:
        raise SystemExit(f"[철회] 재빌드 후에도 루트 HTML 에 남음: {leaks}")
    print("[철회] 루트 HTML 0회 확인(로컬 빌드)")
    if a.no_deploy:
        print("[철회] --no-deploy — 커밋·배포·라이브 확인은 하지 않았다(표식 유지)")
        return 0

    sh("git", "add", "data/essays.json", "data/companion_state.json", *[n for n, _ in root_pages()])
    sh("git", "commit", "-m", "fix(companion): 에세이 철회 — " + ", ".join(a.slug) + "\n\n" + a.reason
       + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    sh("git", "pull", "--rebase")
    sh("git", "push")
    sh("gh", "workflow", "run", "daily-update.yml", "-f", "mode=quick")
    time.sleep(8)
    rid = sh("gh", "run", "list", "--workflow", "daily-update.yml", "--limit", "1",
             "--json", "databaseId", "-q", ".[0].databaseId").stdout.strip()
    st = wait_run(rid, a.timeout)
    print(f"[철회] quick 배포 {rid}: {st}")
    sh("git", "pull", "--rebase")
    return verify_live(a.timeout)


if __name__ == "__main__":
    sys.exit(main())
