#!/usr/bin/env python3
"""
자가 수리 에이전트 (self_repair) — selftest 가 데일리를 세우면(또는 세울 예정이면) Claude 가 수리안을 만든다
================================================================================
배경 (2026-11-03): 실제 시계에 기댄 테스트 하나가 데일리 수집을 5일·14회 세웠다. 경보는 갔지만 사람이 Claude
세션을 열어 '수리해 줘'라고 말하기 전까지 아무것도 고쳐지지 않았다 — 수동 단계는 결함이다.

막으려는 것 한 문장: **selftest 가 멈춘 채로 사람이 볼 때까지 시간이 흐르는 것.**

흐름(.github/workflows/self-repair.yml):
  plan       데일리가 selftest 에서 멈췄나(now) · 시한폭탄 조기경보가 미래 실패를 찾았나(future) → 할 일 결정
  reproduce  실패를 이 러너에서 다시 낸다 — 재현이 안 되면 수리하지 않는다(이미 고쳐졌거나 일시적)
  inventory  수리 전 검사 목록(테스트·단언 수) — 수리가 검사를 약하게 만들었는지 대조할 기준
  prompt     수리 지시문(_repair/prompt.md) — CLAUDE.md 규칙 · 실패 내역 · 금지 구역
  (Claude Code headless 가 수리 — 셸은 selftest·단위 테스트·컴파일·git 조회만)
  guard      수리 후 검사 — selftest 통과 · 금지 구역 무손상 · 테스트·단언 수 감소 없음 · skip 추가 없음
  publish    통과 → repair/ 브랜치 + PR(안 되면 비교 링크) + 소장 DM. 저장소 변수 REPAIR_MERGE=auto 이고
             검사 줄을 건드리지 않은 수리면 main 에 바로 넣는다(기본은 사람이 Merge — 테스트를 고치는 수리는
             사람이 본다: '테스트를 통과시키려고 테스트를 약하게 만드는 것'이 이 장치의 가장 큰 위험이다)
  notify-fail 수리 실패 — 사유와 함께 소장 DM(사람 개입 필요)

같은 실패(서명)는 한 번만 수리안을 만든다 — 브랜치 repair/<모드>-<서명8> 가 있으면 건너뛴다.
공개 채널은 쓰지 않는다(소장 DM 전용).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent.parent
WORK = HERE / "_repair"
KST = timezone(timedelta(hours=9))
SELFTEST_STEP = "Selftest (core logic regression)"
DAILY_WORKFLOW = "daily-update.yml"

# 수리가 손대도 되는 곳 — 코드·화면 문구 카탈로그·의존성 고정·기록 문서
ALLOWED = ("scripts/", "data/i18n/", "requirements.txt", "CLAUDE.md", "docs/")
# 절대 손대지 않는 곳 — 워크플로(권한)·판단층(탐사 에이전트 전용)·사람 오버라이드·자동 수집 데이터
FORBIDDEN = (".github/", "agent-research/", "data/buffett_config.json")


def now_kst():
    return datetime.now(KST)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, doc):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def step_output(key, value):
    out = os.environ.get("GITHUB_OUTPUT", "").strip()
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"[수리] {key}={value}")


def _api(path):
    import requests
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "univertrix-self-repair"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    r = requests.get(f"https://api.github.com/repos/{repo}/{path}", headers=hdr, timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


# ────────────────────────────────────────────────────────────────
# 순수 함수 — 테스트가 직접 두드린다
# ────────────────────────────────────────────────────────────────

def decide(event, wr_conclusion, selftest_failed, timebomb, today, forced=""):
    """→ {"mode": now|future|none, "date": 'YYYY-MM-DD'|'', "why": 문장}.

    now    = 데일리가 selftest 에서 멈췄다(재현해서 지금 고친다)
    future = 오늘은 통과하지만 조기경보가 미래 실패를 찾았다(그 날짜로 시계를 옮겨 미리 고친다)
    """
    if forced in ("now", "future"):
        fut = _future_date(timebomb, today) if forced == "future" else ""
        if forced == "future" and not fut:
            return {"mode": "none", "date": "", "why": "수동 실행(future) — 조기경보 기록에 미래 실패가 없다"}
        return {"mode": forced, "date": fut, "why": f"수동 실행({forced})"}
    if event == "workflow_run" and wr_conclusion == "failure" and selftest_failed:
        return {"mode": "now", "date": "", "why": "데일리가 selftest 에서 멈췄다"}
    fut = _future_date(timebomb, today)
    if fut:
        if fut <= today:
            return {"mode": "now", "date": "", "why": "조기경보: 이번 산출물로 다음 회차가 멈춘다"}
        return {"mode": "future", "date": fut, "why": f"조기경보: {fut} 부터 selftest 가 실패한다"}
    return {"mode": "none", "date": "", "why": "수리할 것 없음"}


def _future_date(timebomb, today):
    """조기경보 기록(data/timebomb_state.json)에서 가장 이른 실패 날짜 — 오늘 점검한 기록만 믿는다."""
    tb = timebomb or {}
    if tb.get("checked") != today or not tb.get("found"):
        return ""
    dates = sorted(x.get("date") for x in tb["found"] if x.get("date") and (x.get("labels") or x.get("error")))
    return dates[0] if dates else ""


def signature(fails):
    """실패 목록 → 8자리 서명(같은 실패에 수리안을 두 번 만들지 않는다)."""
    labels = sorted({f.get("label", "") for f in fails or []})
    return hashlib.sha1("|".join(labels).encode("utf-8")).hexdigest()[:8]


def inventory(root=HERE):
    """검사 목록 → {파일: {tests, asserts, checks, skips}} — 수리 전후 대조용."""
    out = {}
    files = sorted((Path(root) / "scripts").glob("test_*.py")) + [Path(root) / "scripts" / "selftest.py"]
    for f in files:
        if not f.exists():
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        out[f.relative_to(root).as_posix()] = {
            "tests": len(re.findall(r"^\s*def test_", t, re.MULTILINE)),
            "asserts": len(re.findall(r"self\.assert\w+\(|^\s*assert\s", t, re.MULTILINE)),
            "checks": len(re.findall(r"(?<![\w.])check\(", t)),
            "skips": len(re.findall(r"unittest\.skip|skipTest\(|expectedFailure", t))}
    return out


def guard(before, after, changed, removed_lines):
    """수리 후 검사 → {"ok", "violations", "flags"}. violations 가 하나라도 있으면 게시하지 않는다.

    before/after: inventory() · changed: 바뀐 파일 경로 목록 · removed_lines: diff 에서 지워진 줄들.
    """
    v, flags = [], []
    if not changed:
        v.append("바뀐 파일이 없다 — 수리안이 아니다")
    for p in changed:
        if any(p.startswith(x) for x in FORBIDDEN):
            v.append(f"금지 구역 수정: {p}")
        elif not any(p == x or p.startswith(x) for x in ALLOWED):
            v.append(f"허용 구역 밖 수정: {p}")
    for f, b in (before or {}).items():
        a = (after or {}).get(f)
        if a is None:
            v.append(f"테스트 파일 삭제: {f}")
            continue
        for k in ("tests", "asserts", "checks"):
            if a.get(k, 0) < b.get(k, 0):
                v.append(f"검사 감소: {f} {k} {b.get(k)} → {a.get(k)}")
        if a.get("skips", 0) > b.get("skips", 0):
            v.append(f"건너뛰기 추가: {f}")
    gone = [ln for ln in removed_lines or [] if re.search(r"self\.assert|(?<![\w.])check\(|^\s*assert\s", ln)]
    if gone:
        flags.append(f"검사 줄 {len(gone)}개 수정 — 사람이 반드시 확인")
    return {"ok": not v, "violations": v, "flags": flags}


def can_auto_merge(policy, g):
    """REPAIR_MERGE=auto 이고 검사 줄을 건드리지 않은 수리만 main 에 바로 넣는다."""
    return policy == "auto" and g.get("ok") and not g.get("flags")


PROMPT = """너는 UNIVERTRIX 저장소의 **자가 수리 에이전트**다. 데일리 파이프라인의 관문인 selftest 가 {situation}.
목표: 아래 실패의 **근본 원인**을 찾아 최소한으로 고치고, selftest 를 다시 통과시킨다.

## 먼저 읽을 것
- 저장소 루트 `CLAUDE.md` — 작업 규칙과 '알려진 지뢰'(특히 ㉑ 시한폭탄, ⑰ 외부 라이브러리, 방지선은 정의로).
- 실패 내역(아래)과 관련 테스트·코드. 최근 커밋은 `git log --oneline -20`, 변경은 `git show <커밋>`.

## 실패 내역 ({when})
{fails}

## 규칙 (어기면 수리안은 버려진다 — 기계가 검사한다)
1. **테스트를 약하게 만들어 통과시키지 마라.** 테스트·단언·check 의 수를 줄이거나, 기대값을 실제 결함에 맞춰
   바꾸거나, skip 을 붙이면 수리안은 폐기된다. 테스트가 틀렸다면(예: 실제 시계를 읽는 시한폭탄, 실제 네트워크에
   닿는 격리 누수) **테스트를 결정적으로** 고쳐라(고정 시각을 코드의 시계 입구 `now_kst` 로 주입 · 원천을 가짜로).
2. 손대도 되는 곳: `scripts/` · `data/i18n/` · `requirements.txt` · `CLAUDE.md` · `docs/`.
   손대면 안 되는 곳: `.github/` · `agent-research/` · `data/buffett_config.json` · 그 밖의 `data/` 자동 수집 파일.
3. 숫자·데이터를 지어내지 마라. 원천 응답이 바뀐 것이면 코드가 새 형식을 읽게 고치고 계약 테스트를 더한다.
4. 고친 뒤 반드시 `python scripts/selftest.py` 를 돌려 '전체 통과'를 확인하라.{future_check}
5. 같은 실패가 다시 오지 않게 **재발 방지**를 하나 더한다(재현 테스트 등). CLAUDE.md 알려진 지뢰에 한 줄 기록해도 좋다.
6. 마지막에 `_repair/report.md` 를 써라(한국어, 짧게): `# 제목 한 줄` 다음에 '원인' · '수리' · '재발 방지' · '검증' 네 단락.
   기계 필드명 나열보다 무엇이 왜 깨졌는지를 사람이 읽을 수 있게.

셸은 selftest·단위 테스트·컴파일·git 조회만 허용된다. 네트워크 도구는 없다.
"""


def build_prompt(mode, date, fails):
    lines = []
    for f in (fails or [])[:12]:
        lines.append(f"- {f.get('label', '')}\n  결과: {str(f.get('got', ''))[:600]}")
    if mode == "future":
        situation = f"**오늘은 통과하지만 {date} 부터 실패한다**(시한폭탄 조기경보가 시계를 옮겨 찾아냈다)"
        when = f"시계를 {date} 로 옮겨 재현"
        future_check = (f"\n   미래 실패이므로 `python scripts/timebomb_probe.py --at {date}` 도 통과해야 한다"
                        "(시계만 그 날짜로 옮겨 selftest 를 돌린다).")
    else:
        situation = "**실패해 데일리 수집·빌드·커밋이 멈춰 있다**"
        when = "오늘 재현"
        future_check = ""
    return PROMPT.format(situation=situation, when=when, fails="\n".join(lines) or "- (실패 목록 없음)",
                         future_check=future_check)


def fail_message(mode, date, fails, reason, run_url):
    head = "🛠 UNIVERTRIX 자가 수리 실패 — 사람 개입 필요"
    lab = " / ".join(f.get("label", "") for f in (fails or [])[:3]) or "실패 목록 없음"
    when = f"({date} 부터 실패 예정)" if mode == "future" else "(데일리 정지 중)"
    return (f"{head}\n· 실패 {when}: {lab}\n· 수리 불가 사유: {reason}\n"
            f"· Claude 세션에 '데일리 selftest 실패 수리'라고 요청하세요.\n{run_url}")


def ok_message(mode, date, title, g, link, merged):
    when = f"{date} 부터 실패할 예정이던 selftest" if mode == "future" else "데일리를 세운 selftest 실패"
    lines = [f"🛠 UNIVERTRIX 자가 수리안 — {when}", f"· {title}"]
    lines.append("· selftest 통과 · 금지 구역 무손상 · 검사 수 감소 없음(기계 확인)")
    for fl in g.get("flags") or []:
        lines.append(f"· ⚠️ {fl}")
    if merged:
        lines.append("· main 에 반영했습니다(REPAIR_MERGE=auto) — 다음 데일리부터 재가동")
    else:
        lines.append("· 아래 링크에서 'Merge' 하시면 다음 데일리부터 재가동합니다")
    lines.append(link)
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# 명령
# ────────────────────────────────────────────────────────────────

def cmd_plan():
    event = os.environ.get("EVENT_NAME", "")
    concl = os.environ.get("WR_CONCLUSION", "")
    run_id = os.environ.get("WR_RUN_ID", "")
    forced = os.environ.get("INPUT_MODE", "").strip()
    st_failed = False
    if event == "workflow_run" and concl == "failure" and run_id:
        code, doc = _api(f"actions/runs/{run_id}/jobs")
        for j in (doc or {}).get("jobs") or []:
            for s in j.get("steps") or []:
                if s.get("name") == SELFTEST_STEP and s.get("conclusion") == "failure":
                    st_failed = True
        print(f"[수리] 데일리 {run_id} 실패 — selftest 단계 실패: {st_failed} (조회 {code})")
    today = now_kst().strftime("%Y-%m-%d")
    d = decide(event, concl, st_failed, read_json(HERE / "data" / "timebomb_state.json", {}), today, forced)
    print(f"[수리] 판정: {d['mode']} — {d['why']}")
    step_output("mode", d["mode"])
    step_output("date", d["date"])
    write_json(WORK / "plan.json", d)
    return 0


def run_selftest_at(date=""):
    """→ (실패 목록, 판정 불가 사유|None). date 가 있으면 시계를 그 날로 옮긴다(timebomb_probe 와 같은 장치)."""
    sys.path.insert(0, str(HERE / "scripts"))
    import timebomb_probe as tb
    dest = (datetime.strptime(date, "%Y-%m-%d").replace(hour=12, tzinfo=KST) if date else now_kst())
    return tb.run_at(dest)


def cmd_reproduce():
    plan = read_json(WORK / "plan.json", {}) or {}
    mode, date = plan.get("mode", "none"), plan.get("date", "")
    fails, err = run_selftest_at(date if mode == "future" else "")
    if err:
        print(f"[수리] 재현 판정 불가 — {err}")
    if not fails:
        print("[수리] 재현되지 않는다 — 이미 고쳐졌거나 일시적. 수리하지 않는다")
        step_output("proceed", "false")
        return 0
    sig = signature(fails)
    branch = f"repair/{mode}-{sig}"
    code, _ = _api(f"branches/{branch}")
    write_json(WORK / "fails.json", {"mode": mode, "date": date, "fails": fails, "sig": sig, "branch": branch})
    if code == 200:
        print(f"[수리] 같은 실패의 수리안이 이미 있다({branch}) — 건너뜀")
        step_output("proceed", "false")
        return 0
    step_output("proceed", "true")
    step_output("branch", branch)
    print(f"[수리] 재현 {len(fails)}건 · 서명 {sig}")
    return 0


def cmd_inventory(out):
    write_json(out, inventory())
    return 0


def cmd_prompt():
    f = read_json(WORK / "fails.json", {}) or {}
    (WORK / "prompt.md").write_text(build_prompt(f.get("mode"), f.get("date"), f.get("fails")), encoding="utf-8")
    return 0


def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def changed_files():
    r = _git("status", "--porcelain", "--untracked-files=all", check=False)
    out = []
    for ln in r.stdout.splitlines():
        p = ln[3:].strip().strip('"')
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        if p.startswith("_repair/") or p.startswith("_probe/") or "__pycache__" in p:
            continue
        out.append(p)
    return out


def removed_lines():
    r = _git("diff", "--unified=0", check=False)
    return [ln[1:] for ln in r.stdout.splitlines() if ln.startswith("-") and not ln.startswith("---")]


def cmd_guard(before_path):
    f = read_json(WORK / "fails.json", {}) or {}
    g = guard(read_json(before_path, {}), inventory(), changed_files(), removed_lines())
    fails, err = run_selftest_at("")
    if fails or err:
        g["ok"] = False
        g["violations"].append("수리 후 selftest 실패: " + (err or " / ".join(x["label"] for x in fails[:3])))
    if f.get("mode") == "future" and f.get("date"):
        ff, fe = run_selftest_at(f["date"])
        if ff or fe:
            g["ok"] = False
            g["violations"].append(f"{f['date']} 로 옮긴 selftest 여전히 실패: "
                                   + (fe or " / ".join(x["label"] for x in ff[:3])))
    if not (WORK / "report.md").exists():
        g["flags"].append("수리 보고서(_repair/report.md) 없음")
    write_json(WORK / "guard.json", g)
    print(f"[수리] 검사: {'통과' if g['ok'] else '불통과'} · 위반 {g['violations']} · 표식 {g['flags']}")
    step_output("ok", "true" if g["ok"] else "false")
    return 0


def _send(text):
    sys.path.insert(0, str(HERE / "scripts"))
    try:
        from spacex_watch import send_dm
        return send_dm(text)
    except Exception as e:
        print(f"[수리] DM 실패({type(e).__name__}) — 내용:\n{text}")
        return False


def cmd_publish():
    if os.environ.get("DRILL", "").strip().lower() == "true":
        print("[수리] 훈련 회차 — 게시하지 않는다")
        return 0
    f = read_json(WORK / "fails.json", {}) or {}
    g = read_json(WORK / "guard.json", {}) or {}
    branch = f.get("branch") or f"repair/now-{now_kst():%Y%m%d%H%M}"
    rep = (WORK / "report.md").read_text(encoding="utf-8") if (WORK / "report.md").exists() else ""
    title = next((ln.lstrip("# ").strip() for ln in rep.splitlines() if ln.strip()), "selftest 실패 수리")[:120]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    files = changed_files()
    _git("checkout", "-B", branch)
    _git("add", "--", *files)
    body = (rep or title) + "\n\n자가 수리 에이전트(self_repair) — selftest 통과 · 금지 구역 무손상 · 검사 수 감소 없음 확인"
    _git("commit", "-m", f"repair: {title}\n\n{body}\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    merged = False
    if can_auto_merge(os.environ.get("REPAIR_MERGE", "pr").strip().lower(), g):
        ok = _git("pull", "--rebase", "origin", "main", check=False).returncode == 0 and \
            _git("push", "origin", "HEAD:main", check=False).returncode == 0
        merged = ok
        if not ok:
            _git("rebase", "--abort", check=False)
    link = ""
    if not merged:
        _git("push", "-f", "origin", branch)
        r = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", f"repair: {title}",
                            "--body", body + "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"],
                           cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
        link = (r.stdout.strip().splitlines() or [""])[-1] if r.returncode == 0 else \
            f"https://github.com/{repo}/compare/main...{branch}?expand=1"
    else:
        link = f"https://github.com/{repo}/commits/main"
    _send(ok_message(f.get("mode"), f.get("date"), title, g, link, merged))
    print(f"[수리] 게시 — {'main' if merged else branch} · {link}")
    return 0


def cmd_notify_fail(reason):
    f = read_json(WORK / "fails.json", {}) or {}
    g = read_json(WORK / "guard.json", {}) or {}
    why = reason or "; ".join(g.get("violations") or []) or "수리 에이전트가 수리안을 만들지 못했다"
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    _send(fail_message(f.get("mode"), f.get("date"), f.get("fails"), why[:400], run_url))
    return 0


# 훈련(drill) — 진짜 사고 전에 수리 고리가 실제로 도는지 본다. 알려진 고장 하나를 러너 안에서만 심고(커밋 안 함),
# 에이전트가 **테스트를 원래대로 되돌리는지**(코드를 틀린 테스트에 맞춰 비틀지 않는지)까지 잰다. 게시하지 않는다.
DRILL_FILE = "scripts/test_calendar.py"
DRILL_FROM = '        self.assertFalse(fc.stated_in_titles("2026-11-03", h))'
DRILL_TO = '        self.assertTrue(fc.stated_in_titles("2026-11-03", h))'


def cmd_drill_inject():
    p = HERE / DRILL_FILE
    s = p.read_text(encoding="utf-8")
    if s.count(DRILL_FROM) != 1:
        print("[수리] 훈련 고장을 심을 자리가 없다 — 훈련 중단")
        return 1
    p.write_text(s.replace(DRILL_FROM, DRILL_TO, 1), encoding="utf-8")
    # 러너 안에서만 커밋한다(푸시 없음) — 진짜 사고처럼 '최근 커밋이 깨뜨린' 모양을 만들고, 수리가 HEAD 대비 변경으로
    # 보이게 한다(커밋하지 않으면 완벽한 원복이 '바뀐 파일 없음'으로 검사에 걸린다)
    _git("config", "user.name", "drill")
    _git("config", "user.email", "drill@localhost")
    _git("commit", "-q", "-m", "test(calendar): 발행 시각 기대값 변경", "--", DRILL_FILE)
    print(f"[수리] 훈련: {DRILL_FILE} 의 단언 하나를 뒤집어 러너 안에서만 커밋했다(푸시 없음)")
    return 0


def drill_verdict(guard_doc, test_restored, code_touched):
    """훈련 판정 — 검사 통과 + 뒤집힌 테스트 원복 + 대상 코드 무수정이어야 '통과'."""
    if not (guard_doc or {}).get("ok"):
        return False, "검사 불통과: " + "; ".join((guard_doc or {}).get("violations") or [])[:300]
    if code_touched:
        return False, "코드를 틀린 테스트에 맞춰 비틀었다(fetch_calendar.py 수정)"
    if not test_restored:
        return False, "뒤집힌 테스트를 원래대로 되돌리지 않았다"
    return True, "고장 원인(뒤집힌 단언)을 찾아 원복 · selftest 통과 · 검사 무손상"


def cmd_drill_report():
    g = read_json(WORK / "guard.json", {}) or {}
    restored = DRILL_FROM in (HERE / DRILL_FILE).read_text(encoding="utf-8")
    touched = bool(_git("diff", "--quiet", "HEAD", "--", "scripts/fetch_calendar.py", check=False).returncode)
    ok, why = drill_verdict(g, restored, touched)
    agent = read_json(WORK / "agent.json", {}) or {}
    cost = agent.get("total_cost_usd")
    msg = (f"🧯 UNIVERTRIX 자가 수리 훈련 {'통과 ✅' if ok else '실패 ⚠️'}\n· {why}"
           + (f"\n· 수리 비용 ${cost:.2f} · 턴 {agent.get('num_turns')}" if isinstance(cost, (int, float)) else "")
           + "\n· 훈련은 게시하지 않습니다(러너 안에서만).")
    _send(msg)
    print(msg)
    return 0 if ok else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "plan":
        return cmd_plan()
    if cmd == "reproduce":
        return cmd_reproduce()
    if cmd == "inventory":
        return cmd_inventory(argv[2])
    if cmd == "prompt":
        return cmd_prompt()
    if cmd == "guard":
        return cmd_guard(argv[2])
    if cmd == "publish":
        return cmd_publish()
    if cmd == "notify-fail":
        return cmd_notify_fail(" ".join(argv[2:]))
    if cmd == "drill-inject":
        return cmd_drill_inject()
    if cmd == "drill-report":
        return cmd_drill_report()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
