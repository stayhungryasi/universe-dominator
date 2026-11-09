#!/usr/bin/env python3
"""
시한폭탄 조기경보 (timebomb_probe) — 미래 날짜로 selftest 를 미리 돌린다
================================================================================
배경 (2026-11-03): 측정층 테스트 하나가 실제 시계에 기대고 있었다(고정 날짜 10/29 의 발사를 '다가오는 발사'로
가정). 그 날짜가 실제로 지나자 selftest 가 실패했고, selftest 는 데일리 수집의 관문이라 **5일·14회** 수집·빌드·
커밋이 멈췄다. 같은 이유로 탐사 엔진 산출물(10/29 사건·11월 월간)도 게시되지 못했다.

막으려는 것 한 문장: **오늘은 통과하지만 며칠 뒤 저절로 실패할 테스트가 그날 파이프라인을 세우는 것.**
그래서 selftest 를 시계만 미래로 옮겨(+3·+14·+45일) 돌려 보고, 실패하면 **그날이 오기 전에** 소장 DM 으로 알린다.
+0일은 '다음 회차 관문 미리보기'다 — 이 스텝은 빌드 뒤·커밋 앞에 돌므로, 이번 회차 산출물(데이터·HTML)이
다음 회차 selftest 를 세우는지를 커밋 전에 본다(시계가 아니라 산출물이 원인인 정지).
오늘의 수집은 막지 않는다(비차단) — 막으면 시한폭탄이 터지기 45일 전부터 파이프라인이 서 버린다.

· 하루 한 번(같은 날 두 번째 full 회차부터는 건너뜀) · 같은 실패는 7일마다 재알림 · 해소되면 1회 알림
· 결과는 data/timebomb_state.json(바뀔 때만 쓴다)
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent.parent
STATE_PATH = HERE / "data" / "timebomb_state.json"
KST = timezone(timedelta(hours=9))
OFFSETS = (0, 3, 14, 45)
REALERT_DAYS = 7

# 자식 프로세스 — 시계만 옮겨 selftest 를 그대로 돌린다(같은 코드·같은 데이터)
CHILD = (
    "import sys, runpy, datetime, time_machine\n"
    "sys.path.insert(0, sys.argv[3])\n"
    "dest = datetime.datetime.fromisoformat(sys.argv[1])\n"
    "with time_machine.travel(dest, tick=True):\n"
    "    try:\n"
    "        runpy.run_path(sys.argv[2], run_name='__main__')\n"
    "    except SystemExit as e:\n"
    "        sys.exit(e.code if isinstance(e.code, int) else (0 if e.code is None else 1))\n"
)


def now_kst():
    return datetime.now(KST)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def run_at(dest, selftest=None, timeout=900):
    """dest(aware datetime) 시각으로 selftest 를 돌린다 → (실패 목록[{label, got}], 판정 불가 사유 또는 None)."""
    selftest = Path(selftest or HERE / "scripts" / "selftest.py")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fails.json"
        env = dict(os.environ, SELFTEST_FAILS_OUT=str(out))
        try:
            r = subprocess.run([sys.executable, "-c", CHILD, dest.isoformat(), str(selftest), str(selftest.parent)],
                               cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return [], "시간 초과"
        doc = read_json(out, None)
        if doc is None:
            # 실패 목록을 못 남기고 죽었다 — '통과'가 아니라 판정 불가(침묵은 통과가 아니다)
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
            return [], f"실행 실패(rc={r.returncode}) {tail[0][:160]}"
        return list(doc.get("detail") or []), None


def probe(offsets=OFFSETS, now=None, selftest=None):
    """→ [{offset, date, fails, error}] — 실패나 판정 불가가 있는 오프셋만."""
    now = now or now_kst()
    out = []
    for off in offsets:
        dest = now + timedelta(days=off)
        fails, err = run_at(dest, selftest)
        if fails or err:
            out.append({"offset": off, "date": dest.strftime("%Y-%m-%d"), "fails": fails, "error": err})
    return out


def signature(found):
    return "|".join(sorted({f["label"] for x in found for f in x["fails"]} | {"ERR:" + x["error"] for x in found if x["error"]}))


def message(found):
    first = min(found, key=lambda x: x["offset"])
    if first["offset"] == 0:
        lines = ["⚠️ UNIVERTRIX 다음 회차 정지 예고 — 이번 회차 산출물로 selftest 가 실패합니다",
                 "· 이번 회차는 커밋합니다(데이터 보존). 수리 전까지 다음 데일리부터 수집·빌드·커밋이 멈춥니다."]
    else:
        lines = [f"⏰ UNIVERTRIX 시한폭탄 조기경보 — 오늘은 통과하지만 {first['date']}(+{first['offset']}일)부터 "
                 "selftest 가 실패합니다",
                 "· 그날이 오면 데일리 수집·빌드·커밋이 멈춥니다(2026-10-29 실제 사고: 5일 정지)."]
    for x in sorted(found, key=lambda x: x["offset"]):
        if x["error"]:
            lines.append(f"· +{x['offset']}일: 판정 불가 — {x['error']}")
        for f in x["fails"][:3]:
            lines.append(f"· +{x['offset']}일: {f['label']} → {f.get('got', '')[:160]}")
    lines.append("· Claude 세션에 '시한폭탄 조기경보 수리'라고 요청하세요.")
    return "\n".join(lines)


def _dispatch(mode):
    try:
        from repair_dispatch import dispatch_repair
        return dispatch_repair(mode)
    except Exception as e:
        print(f"[시한폭탄] 자가 수리 호출 실패({type(e).__name__})")
        return False


def run(force=False, offsets=OFFSETS, selftest=None, send=None, dispatch=_dispatch):
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    state = read_json(STATE_PATH, {}) or {}
    if state.get("checked") == today and not force:
        print(f"[시한폭탄] 오늘({today}) 이미 점검 — 건너뜀")
        return 0
    if send is None:
        from spacex_watch import send_dm as send
    found = probe(offsets, now, selftest)
    sig = signature(found)
    prev = dict(state)
    state["checked"] = today
    if found:
        last = state.get("alerted_on")
        stale = not last or state.get("sig") != sig or \
            (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days >= REALERT_DAYS
        if stale:
            send(message(found))
            state["alerted_on"] = today
            # 미래 실패는 그날이 오기 전에 고친다 — 자가 수리 에이전트를 직접 부른다(+0일이면 now, 아니면 future)
            if dispatch is not None:
                dispatch("now" if min(x["offset"] for x in found) == 0 else "future")
        state["sig"] = sig
        state["found"] = [{"offset": x["offset"], "date": x["date"], "labels": [f["label"] for f in x["fails"]],
                           "error": x["error"]} for x in found]
    else:
        if state.get("alerted_on"):
            send("✅ UNIVERTRIX 시한폭탄 조기경보 해소 — 미래 날짜(+" + "·+".join(str(o) for o in offsets) + "일) selftest 전부 통과")
        for k in ("alerted_on", "sig", "found"):
            state.pop(k, None)
    # 바뀐 것이 '점검 날짜'뿐이어도 쓴다 — 하루 한 번 점검의 흔적(커밋은 데일리 커밋에 묻어간다)
    if state != prev:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[시한폭탄] +{'·+'.join(str(o) for o in offsets)}일 점검 — "
          + ("실패 " + str(len(found)) + "개 날짜" if found else "전부 통과"))
    return 0


def at_date(date_str, selftest=None):
    """`--at YYYY-MM-DD` — 시계만 그 날(정오 KST)로 옮겨 selftest 를 한 번 돌린다(자가 수리 에이전트의 검증용).
    실패·판정 불가면 1, 통과면 0."""
    dest = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, tzinfo=KST)
    fails, err = run_at(dest, selftest)
    if err:
        print(f"[시한폭탄] {date_str}: 판정 불가 — {err}")
        return 1
    for f in fails:
        print(f"[시한폭탄] {date_str}: {f.get('label')} → {str(f.get('got', ''))[:300]}")
    print(f"[시한폭탄] {date_str}: " + (f"실패 {len(fails)}건" if fails else "전부 통과"))
    return 1 if fails else 0


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    if "--at" in sys.argv:
        return at_date(sys.argv[sys.argv.index("--at") + 1])
    try:
        return run(force="--force" in sys.argv)
    except Exception as e:
        print(f"[시한폭탄] 점검 실패 — {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
