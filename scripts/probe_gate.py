#!/usr/bin/env python3
"""
탐사 게시 관문 (probe_gate) — 무인 탐사선 산출물이 main 에 들어가기 전 마지막 문
================================================================================
배경 (2026-10-23): 탐사선은 '로컬 수동 실행'에만 기대다가 10월 회차를 통째로 빠뜨렸다
(9/2 이후 51일 정지). 수동 단계는 결함이다 — spacex-probe.yml 이 매월 1일·사건 연동으로
Claude Code 를 헤드리스로 돌리고, 이 관문이 게시 여부를 정한다.

하위 명령:
  prepare --mode monthly|event --reason "..."   회차 준비(직전 state 스냅샷·지시문 조립)
  check [--stage author|final]                   관문 검사 → _probe/gate.json, 실패 시 exit 1
  fix-prompt                                     검증관 불일치를 작성자에게 돌려줄 지시문
  publish                                        통과 → main 커밋 / 실패 → probe/ 브랜치 + PR

관문이 막는 것 (각각 한 문장 — '방지선은 정의로 검사한다' 지뢰 규칙 ①):
  ① 스키마: 다음 회차·화면이 읽을 수 없는 state 가 게시되는 것
  ② 확률: 시나리오 확률이 합 1 이 아닌 채로 게시되는 것
  ③ 판정: 정의되지 않은 판정 상태가 채점표에 섞이는 것
  ④ 사슬: 회차 번호·직전 회차 연결이 끊긴 채 게시되는 것
  ⑤ 출처: **추적할 수 없는 숫자**가 보고서에 실리는 것 — 표 행은 같은 표·캡션의 출처를
     물려받고, 산식(÷·×·=)으로 만든 파생값은 산식이 곧 근거다(줄 단위 표면 검사는
     정상 표를 통째로 오탐한다 — 9월 보고서로 실측, 2026-10-23)
  ⑥ 검증: 독립 검증관이 원문과 다르다고 한 숫자가 그대로 게시되는 것
  ⑦ 준법: 매매 행위를 권하는 문장·키가 공개 파일에 실리는 것
  ⑧ 별지: CI 가 포지션 판단 별지(_private)를 만드는 것

⚠️ CI 에서 Claude 에게 허용되는 Bash 는 `python scripts/probe_gate.py *` 뿐이다.
   작성자는 `check --stage author` 로 스스로 관문을 미리 두드려 볼 수 있다.
"""
import argparse
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

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
AR = HERE / "agent-research"
STATE = AR / "state" / "spacex_state.json"
VLIST = AR / "state" / "verify_list.json"
VRES = AR / "state" / "verify_result.json"
REPORTS = AR / "reports"
CHANGELOG = REPORTS / "changelog.md"
PROMPTS = AR / "prompts"
WORK = HERE / "_probe"
KST = timezone(timedelta(hours=9))

STATUSES = ("미판정", "진행", "달성", "달성(조기)", "미달")
UNSOURCED_MAX = 0.05
VERIFY_MIN = 10

# 준법 — 공개 state 의 금지 키(권고·포지션·진입·목표가 계열)
FORBIDDEN_KEY = re.compile(
    r"(^|_)(recommendation|recommend|position|positions|entry|target_price|price_target|"
    r"max_position_weight|weight)(_|$)", re.I)
# 준법 — 공개 문장. agent_report.is_reco(최후 방어선)에 더해 관망·목표가 계열까지
FORBIDDEN_TEXT = re.compile(
    r"관망|목표\s*주?가|매수\s*(의견|추천|권|판단)|매도\s*(의견|추천|권|판단)|"
    r"보유\s*(의견|권|판단)|비중\s*(확대|축소|유지)|(포트폴리오|최대|목표|투자)\s*비중|"
    r"진입\s*(가격|가|시점|구간|트리거)")
NEGATED = re.compile(r"(투자\s*권유|권유|목표\s*주?가|권고)\s*(?:가|이|는)?\s*(?:아님|아닙니다|아니다)")
# ⚠️ '비중 42%'(매출 구성비)·'저가 경쟁 진입'(경쟁 분석)은 권고가 아니다 — 포지션 비중 형태만
#    잡는다(2026-08-22 오차단 지뢰: 표면 낱말로 자르면 분석이 막힌다. 테스트가 정상 문장으로 오탐을 잰다)


def now_kst():
    return datetime.now(KST)


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# 회차 식별
# ────────────────────────────────────────────────────────────────

def run_id_for(mode, today, prev_run_id, existing_reports):
    """회차 id — monthly: YYYY-MM(같은 id 가 있으면 -r2, -r3 …) / event: YYYY-MM-DD-event."""
    if mode == "event":
        base = today + "-event"
        rid, n = base, 2
        while rid in existing_reports:
            rid, n = f"{base}-r{n}", n + 1
        return rid
    base = today[:7]
    taken = set(existing_reports) | ({prev_run_id} if prev_run_id else set())
    rid, n = base, 2
    while rid in taken:
        rid, n = f"{base}-r{n}", n + 1
    return rid


def report_path_for(run_id):
    return REPORTS / f"{run_id}.md"


# ────────────────────────────────────────────────────────────────
# 검사 — 순수 함수(테스트가 직접 두드린다)
# ────────────────────────────────────────────────────────────────

def check_schema(st):
    """① 필수 키·타입."""
    bad = []
    if not isinstance(st, dict):
        return ["state 가 JSON 객체가 아님"]
    need = {"schema_version": int, "ticker": str, "run": dict, "scenarios": dict,
            "valuation": dict, "milestones": list, "triggers_next_12m": list,
            "probability_adjustment_rules": list, "open_questions_for_next_run": list,
            "axes": (dict, list)}
    for k, t in need.items():
        if k not in st:
            bad.append(f"필수 키 없음: {k}")
        elif not isinstance(st[k], t):
            bad.append(f"타입 불일치: {k}")
    run = st.get("run") or {}
    for k in ("run_id", "run_date", "run_number", "previous_run_id", "report_path"):
        if k not in run:
            bad.append(f"run.{k} 없음")
    if "run_number" in run and not isinstance(run["run_number"], int):
        bad.append("run.run_number 가 정수가 아님")
    for k in ("bull", "base", "bear"):
        sc = (st.get("scenarios") or {}).get(k)
        if not isinstance(sc, dict) or not isinstance(sc.get("probability"), (int, float)):
            bad.append(f"scenarios.{k}.probability 없음/숫자 아님")
    for m in st.get("milestones") or []:
        if not isinstance(m, dict):
            bad.append("이정표 항목이 객체가 아님")
            continue
        for k in ("id", "axis", "year", "statement", "status"):
            if k not in m:
                bad.append(f"이정표 {m.get('id', '?')}: {k} 없음")
        if "progress" not in m:
            bad.append(f"이정표 {m.get('id', '?')}: progress 없음(측정 불가면 null + 사유)")
        elif m["progress"] is None:
            if not (m.get("progress_reason") or "").strip():
                bad.append(f"이정표 {m.get('id', '?')}: progress null 인데 사유 없음")
        elif isinstance(m["progress"], dict):
            for k in ("current", "target", "unit", "asof", "source"):
                if k not in m["progress"]:
                    bad.append(f"이정표 {m.get('id', '?')}: progress.{k} 없음")
        else:
            bad.append(f"이정표 {m.get('id', '?')}: progress 형식 오류")
    return bad


def check_probabilities(st):
    """② 합 1.00 ± 0.001."""
    sc = st.get("scenarios") or {}
    try:
        tot = sum(float(sc[k]["probability"]) for k in ("bull", "base", "bear"))
    except Exception:
        return ["시나리오 확률을 읽을 수 없음"]
    return [] if abs(tot - 1.0) <= 0.001 else [f"시나리오 확률 합 {tot:.4f} ≠ 1.00"]


def check_statuses(st):
    """③ 판정 상태 집합."""
    return [f"이정표 {m.get('id')}: 정의되지 않은 판정 '{m.get('status')}'"
            for m in st.get("milestones") or []
            if isinstance(m, dict) and m.get("status") not in STATUSES]


def check_chain(st, prev):
    """④ run_number = 직전 + 1, previous_run_id = 직전 run_id."""
    bad = []
    run, prun = st.get("run") or {}, (prev or {}).get("run") or {}
    if not prun:
        return bad
    if run.get("run_number") != (prun.get("run_number") or 0) + 1:
        bad.append(f"run_number {run.get('run_number')} ≠ 직전 {prun.get('run_number')} + 1")
    if run.get("previous_run_id") != prun.get("run_id"):
        bad.append(f"previous_run_id {run.get('previous_run_id')!r} ≠ 직전 run_id {prun.get('run_id')!r}")
    if run.get("run_id") == prun.get("run_id"):
        bad.append("run_id 가 직전 회차와 같음")
    return bad


_SRC = re.compile(r"https?://|추정|가정|산식|역산|출처")
_CALC = re.compile(r"[÷×]|\d\s*[−\-+*/]\s*\(?\$?\d[^|]*=|=\s*\$?[\d(]")
# 한국어는 숫자 바로 뒤에 글자가 붙는다('2026년') — \b 는 여기서 경계를 못 잡는다(지뢰 '언어 가정').
_COUNTER = re.compile(r"(?<![\d.,])\d{1,2}\s*(가지|개|건|줄|회차|단계|번째|축|위|명의|년치)")
_DATE = re.compile(r"(?<![\d.,$])(?:19|20)\d{2}(?:[-./]\d{1,2}(?:[-./]\d{1,2})?)?(?![\d.,])"
                   r"|(?<![\d.,$])\d{1,2}/\d{1,2}(?![\d.,])")
_IDS = re.compile(r"(?<![A-Za-z\d])(?:[A-E]-\d{4}-\d|[1-4]Q\d{2}|[1-4]H\d{2}|Q[1-4]|T\d+|F\d+|V\d)"
                  r"(?![A-Za-z\d])|PHASE\s*\d|Flight\s*\d+|\d+-\d+\s*참조")
# 정의 표 — 사실 주장이 아니라 판정 기준·규칙(이정표 기준값, 확률 조정 규칙, 트리거 목록)
_DEF_TABLE = re.compile(r"이정표|판정|조건|규칙|트리거|확인 방법|달성 시|미달 시")


def _has_number(line):
    s = re.sub(r"^\s*(\d+\.|[-*>])\s+", "", line)
    s = _DATE.sub("", s)
    s = _IDS.sub("", s)
    s = _COUNTER.sub("", s)
    return bool(re.search(r"\d", s))


def unsourced_lines(md):
    """⑤ 추적할 수 없는 숫자 줄 → (숫자 줄 수, 누락 줄 목록).

    표 행은 표 안 어느 칸이든, 또는 표 바로 위·아래 문단(캡션)에 출처가 있으면 상속한다.
    산식이 적힌 줄은 파생값 — 산식이 근거다. 머리글은 세지 않는다.
    """
    lines = (md or "").splitlines()
    n = len(lines)
    table_src = {}
    i = 0
    while i < n:
        if lines[i].strip().startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            block = lines[i:j]
            ctx = []
            k = i - 1
            while k >= 0 and len(ctx) < 2:          # 위쪽 캡션(빈 줄 건너 최대 2줄)
                if lines[k].strip():
                    if lines[k].lstrip().startswith("#"):
                        break
                    ctx.append(lines[k])
                k -= 1
            k = j
            while k < n and len(ctx) < 4:           # 아래쪽 출처 줄
                if lines[k].strip():
                    if lines[k].lstrip().startswith(("#", "|")):
                        break
                    ctx.append(lines[k])
                    break
                k += 1
            ok = (any(_SRC.search(x) for x in block + ctx)
                  or bool(_DEF_TABLE.search(block[0])))      # 머리행이 정의 표임을 말한다
            for r in range(i, j):
                table_src[r] = ok
            i = j
        else:
            i += 1
    num, bad = 0, []
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#") or re.fullmatch(r"\|?[\s:\-|]+\|?", s):
            continue
        if not _has_number(s):
            continue
        num += 1
        if _SRC.search(s) or _CALC.search(s) or table_src.get(idx):
            continue
        bad.append(s[:120])
    return num, bad


def check_sources(md):
    num, bad = unsourced_lines(md)
    ratio = (len(bad) / num) if num else 0.0
    out = [] if ratio <= UNSOURCED_MAX else [
        f"출처 누락 {len(bad)}/{num}줄({ratio:.1%}) > {UNSOURCED_MAX:.0%} — 예: " + " / ".join(bad[:3])]
    return out, {"numeric_lines": num, "unsourced": len(bad), "ratio": round(ratio, 4)}


def check_verify(vlist, vres):
    """⑥ 검증관 결과 — 불일치 0, 검증 대상 최소 수."""
    bad = []
    items = vlist if isinstance(vlist, list) else (vlist or {}).get("items")
    if not isinstance(items, list) or len(items) < VERIFY_MIN:
        bad.append(f"verify_list 항목 {len(items) if isinstance(items, list) else 0}개 < {VERIFY_MIN}")
    else:
        miss = [x.get("claim", "?") for x in items
                if not (isinstance(x, dict) and x.get("source_url") and x.get("value") is not None)]
        if miss:
            bad.append(f"verify_list 출처/값 없는 항목 {len(miss)}개")
    if not isinstance(vres, dict):
        bad.append("verify_result 없음 — 검증관이 돌지 않았다")
        return bad
    mm = vres.get("mismatch") or []
    if mm:
        bad.append(f"검증관 불일치 {len(mm)}건")
    if not vres.get("verified_at"):
        bad.append("verify_result.verified_at 없음")
    return bad


def _walk_keys(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "." + str(k), str(k), v
            yield from _walk_keys(v, path + "." + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk_keys(v, f"{path}[{i}]")


def _walk_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _walk_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_strings(v)


def reco_text_hits(text):
    """공개 문장 중 권고 형태 — agent_report.is_reco + 관망·목표가 계열."""
    try:
        from agent_report import is_reco
    except Exception:
        def is_reco(_):
            return False
    hits = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        # 부정형 고지('투자 권유 아님 · 목표가 아님')는 권고의 반대다 — 부정 구절만 지우고 다시 본다.
        # 같은 줄에 다른 권고 표현이 남아 있으면 그대로 걸린다(고지문을 방패로 쓸 수 없다).
        t = NEGATED.sub(" ", s)
        if t.strip() and (is_reco(t) or FORBIDDEN_TEXT.search(t)):
            hits.append(s[:100])
    return hits


def public_files(root=AR):
    """공개 원본 — reports/*.md(_private 제외) · state/*.json(_private 제외). changelog 포함."""
    out = []
    for p in sorted((root / "reports").glob("*.md")):
        if not p.stem.endswith("_private"):
            out.append(p)
    for p in sorted((root / "state").glob("*.json")):
        if not p.stem.endswith("_private"):
            out.append(p)
    return out


def check_compliance(files):
    """⑦ 공개 파일의 금지 키·문장 0."""
    bad = []
    for p in files:
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if p.suffix == ".json":
            try:
                doc = json.loads(raw)
            except Exception:
                bad.append(f"{p.name}: JSON 해석 실패")
                continue
            keys = [path for path, k, _ in _walk_keys(doc) if FORBIDDEN_KEY.search(k)]
            if keys:
                bad.append(f"{p.name}: 금지 키 {keys[:4]}")
            texts = [h for s in _walk_strings(doc) for h in reco_text_hits(s)]
        else:
            texts = reco_text_hits(raw)
        if texts:
            bad.append(f"{p.name}: 권고 형태 문장 {len(texts)}건 — {texts[0]}")
    return bad


def check_private(root=AR, ci=None):
    """⑧ CI 실행은 _private 파일을 만들지 않는다(포지션 판단은 로컬 전용)."""
    ci = os.environ.get("CI") == "true" if ci is None else ci
    if not ci:
        return []
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*_private*"))
    return [f"CI 가 별지를 만들었다: {found}"] if found else []


def soft_checks(st):
    """경고(실패 아님) — 관제실 화면을 먹여 살리는 데이터가 빠졌는가(2026-10-23 v2).

    progress.parts(숫자 진척)·run_history(이번 회차 1줄)·kpi_history(운영 KPI) 누락은
    게시를 막지 않는다. 대신 관문 결과에 경고로 남겨 다음 회차가 채우게 한다.
    """
    warn = []
    if not isinstance(st, dict):
        return warn
    miss = [m.get("id") for m in st.get("milestones") or []
            if isinstance(m, dict) and not (
                (isinstance(m.get("progress"), dict) and isinstance(m["progress"].get("parts"), list))
                or isinstance(m.get("progress_parts"), list))]
    if miss:
        warn.append(f"progress.parts 누락 {len(miss)}개: {miss[:6]}")
    rid = (st.get("run") or {}).get("run_id")
    if rid and not any(isinstance(r, dict) and r.get("run_id") == rid for r in st.get("run_history") or []):
        warn.append(f"run_history 에 이번 회차({rid}) 줄 없음")
    if not st.get("kpi_history"):
        warn.append("kpi_history 없음(운영 KPI 분기 기록)")
    return warn


FIN_KEYS = ("revenue_usd_m", "operating_income_usd_m", "net_income_usd_m", "ocf_usd_m", "capex_usd_m",
            "cash_usd_m", "debt_and_leases_usd_m")


def check_kpi_fin(st, vlist):
    """S-1 원문에서 기입한 분기 재무(state.kpi_history 의 공시 재무 키) — 출처·검증관 대조 필수(2026-10-23).

    기계(XBRL)가 못 만든 분기를 탐사 엔진이 채울 수 있게 했으니, 그 값은 반드시 원문 URL 을 달고
    verify_list 에 올라 검증관의 숫자 대조를 받아야 한다. 하나라도 빠지면 게시하지 않는다.
    """
    fails = []
    items = (vlist or {}).get("items") if isinstance(vlist, dict) else (vlist or [])
    where = {}
    for it in items or []:
        if isinstance(it, dict):
            where[re.sub(r"\s+", " ", str(it.get("where_in_report", ""))).strip()] = it
    for h in (st or {}).get("kpi_history") or []:
        if not isinstance(h, dict):
            continue
        keys = [k for k in FIN_KEYS if h.get(k) is not None]
        if not keys:
            continue
        q = h.get("quarter", "?")
        if not str(h.get("fin_source", "")).startswith("http"):
            fails.append(f"kpi_history {q}: 공시 재무 값({', '.join(keys)})에 fin_source(S-1 원문 URL) 없음")
        for k in keys:
            it = where.get(f"state.kpi_history {q}.{k}")
            if not it:
                fails.append(f"kpi_history {q}.{k}: verify_list 에 없음(검증관 대조 누락)")
                continue
            try:
                same = abs(float(it.get("value")) - float(h[k])) < 0.5
            except (TypeError, ValueError):
                same = False
            if not same:
                fails.append(f"kpi_history {q}.{k}: verify_list 값({it.get('value')})과 state 값({h[k]}) 불일치")
    return fails


def run_checks(stage="final", st=None, prev=None, md=None, vlist=None, vres=None,
               files=None, root=AR, ci=None):
    st = read_json(STATE) if st is None else st
    prev = read_json(WORK / "prev_state.json", {}) if prev is None else prev
    fails, stats = [], {}
    fails += check_schema(st)
    if isinstance(st, dict):
        fails += check_probabilities(st)
        fails += check_statuses(st)
        fails += check_chain(st, prev)
        if md is None:
            rp = (st.get("run") or {}).get("report_path") or ""
            p = AR / rp if rp else None
            md = p.read_text(encoding="utf-8") if p and p.exists() else None
        if md is None:
            fails.append("보고서 파일 없음(run.report_path)")
        else:
            f, stats = check_sources(md)
            fails += f
    if stage == "final":
        fails += check_kpi_fin(st, read_json(VLIST) if vlist is None else vlist)
        fails += check_verify(read_json(VLIST) if vlist is None else vlist,
                              read_json(VRES) if vres is None else vres)
    fails += check_compliance(public_files(root) if files is None else files)
    fails += check_private(root, ci)
    return {"ok": not fails, "stage": stage, "failures": fails, "stats": stats,
            "warnings": soft_checks(st),
            "checked_at": now_kst().isoformat(timespec="minutes")}


def content_hashes():
    """작성자 산출물(보고서·state·changelog) 지문 — 검증 단계 전후 대조용."""
    import hashlib
    st = read_json(STATE, {}) or {}
    rp = (st.get("run") or {}).get("report_path") or ""
    out = {}
    for p in (STATE, CHANGELOG, AR / rp if rp else None):
        if p and p.is_file():
            out[str(p.relative_to(HERE))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def only_verify_failures(gate):
    """불일치만 남은 실패인가 — 작성자 1회 수정 → 재검증 경로의 조건."""
    f = gate.get("failures") or []
    return bool(f) and all(x.startswith("검증관 불일치") for x in f)


# ────────────────────────────────────────────────────────────────
# 하위 명령
# ────────────────────────────────────────────────────────────────

def cmd_prepare(mode, reason):
    WORK.mkdir(exist_ok=True)
    for old in list(WORK.glob("gate_*.json")) + [WORK / "fix_prompt.md"]:
        if old.exists():
            old.unlink()                     # 지난 실행의 관문 결과가 이번 게시를 정하지 못하게
    prev = read_json(STATE, {}) or {}
    write_json(WORK / "prev_state.json", prev)
    # 지난 회차의 검증 산출물을 치운다 — 낡은 '불일치 0' 이 이번 회차를 통과시키지 못하게
    for p in (VLIST, VRES):
        if p.exists():
            (WORK / ("prev_" + p.name)).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            p.unlink()
    today = now_kst().strftime("%Y-%m-%d")
    existing = [p.stem for p in REPORTS.glob("*.md")]
    rid = run_id_for(mode, today, (prev.get("run") or {}).get("run_id"), existing)
    watch = read_json(HERE / "data" / "spacex_watch.json", {}) or {}
    events = (watch.get("events") or [])[:20]
    due = [d for d in (watch.get("due") or []) if not d.get("baseline")][-10:]
    ctx = {"mode": mode, "reason": reason, "run_id": rid, "today": today,
           "report_path": f"reports/{rid}.md",
           "previous_run_id": (prev.get("run") or {}).get("run_id"),
           "previous_run_number": (prev.get("run") or {}).get("run_number"),
           "prepared_at": now_kst().isoformat(timespec="minutes")}
    write_json(WORK / "context.json", ctx)
    body = (PROMPTS / "monthly_run.md").read_text(encoding="utf-8")
    ev_lines = "\n".join(f"- {e.get('date')} · {e.get('source')} · {e.get('title')} · {e.get('url')}"
                         for e in events) or "- (측정층 사건 없음)"
    due_lines = "\n".join(f"- {d.get('trigger_id') or '—'} {d.get('name')}: {d.get('reason')} "
                          f"({d.get('event') or d.get('expected') or ''})" for d in due) or "- (없음)"
    author = "\n".join([
        body, "", "---", "# 이번 실행 정보 (워크플로가 주입)",
        f"- 모드: {mode}", f"- 사유: {reason}", f"- 회차 id: {rid}",
        f"- 보고서 경로: agent-research/{ctx['report_path']}",
        f"- 직전 회차: {ctx['previous_run_id']} (run_number {ctx['previous_run_number']})",
        f"- 실행일(KST): {today}",
        "- 실행 환경: GitHub Actions(CI). `_private` 파일을 만들지 마라 — 포지션 판단은 로컬 실행 전용이다.",
        "- 끝내기 전에 `python scripts/probe_gate.py check --stage author` 를 실행해 관문 실패 항목을 고쳐라.",
        "", "## 측정층 최근 사건 (data/spacex_watch.json)", ev_lines,
        "", "## 트리거 due (측정층 감지)", due_lines,
    ])
    (WORK / "author_prompt.md").write_text(author, encoding="utf-8")
    verify = "\n".join([
        (PROMPTS / "verify_run.md").read_text(encoding="utf-8"), "", "---",
        f"- 대상 보고서: agent-research/{ctx['report_path']}",
        "- 대상 목록: agent-research/state/verify_list.json",
        "- 결과 파일: agent-research/state/verify_result.json",
        f"- verified_at 은 ISO 형식(예: {now_kst().isoformat(timespec='minutes')})으로 적어라.",
    ])
    (WORK / "verify_prompt.md").write_text(verify, encoding="utf-8")
    print(f"[관문] 준비 완료 — {mode} · {rid} · 사건 {len(events)} · due {len(due)}")
    return 0


def cmd_fix_prompt():
    vres = read_json(VRES, {}) or {}
    ctx = read_json(WORK / "context.json", {}) or {}
    mm = vres.get("mismatch") or []
    txt = "\n".join([
        "# 검증관 불일치 수정 (1회 한정)",
        "독립 검증관이 아래 숫자가 원문과 다르다고 판정했다. 원문을 다시 열어 확인하고,",
        "틀렸으면 보고서·state·verify_list 를 고쳐라. 검증관이 틀렸다고 판단되면 원문 위치를",
        "verify_list 의 해당 항목 note 에 적어라(값을 바꾸지 않는다). verify_result.json 은 건드리지 마라.",
        f"- 보고서: agent-research/{ctx.get('report_path', '')}",
        "", "## 불일치 목록", json.dumps(mm, ensure_ascii=False, indent=1),
        "", "끝나면 `python scripts/probe_gate.py check --stage author` 로 확인하라.",
    ])
    (WORK / "fix_prompt.md").write_text(txt, encoding="utf-8")
    print(f"[관문] 수정 지시문 작성 — 불일치 {len(mm)}건")
    return 0


def _git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()[:300]}")
    return r


def send_dm(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("[관문] DM 수신처 미등록 — 발송 생략(공개 폴백 없음). 내용:\n" + text)
        return
    try:
        from send_telegram_briefing import send_telegram, esc
        send_telegram(token, chat, esc(text))
    except Exception as e:
        print(f"[관문] DM 발송 실패: {e}", file=sys.stderr)


def prob_line(prev, st):
    out = []
    for k, ko in (("bull", "강세"), ("base", "기본"), ("bear", "약세")):
        a = (((prev or {}).get("scenarios") or {}).get(k) or {}).get("probability")
        b = (((st or {}).get("scenarios") or {}).get(k) or {}).get("probability")
        if isinstance(b, (int, float)):
            out.append(f"{ko} {a * 100:.0f}→{b * 100:.0f}%" if isinstance(a, (int, float))
                       else f"{ko} {b * 100:.0f}%")
    return " · ".join(out)


def summary_lines(st, md):
    """DM 3줄 요약 — state.run.summary(있으면) 또는 보고서의 '한 문장 결론'."""
    s = ((st or {}).get("run") or {}).get("summary")
    if isinstance(s, list) and s:
        return [str(x)[:120] for x in s[:3]]
    lines = [ln.strip() for ln in (md or "").splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if "한 문장 결론" in ln and i + 1 < len(lines):
            return [re.sub(r"^[>*\-\s]+", "", lines[i + 1])[:160]]
    return []


def cmd_publish():
    ctx = read_json(WORK / "context.json", {}) or {}
    gate = (read_json(WORK / "gate_final2.json") or read_json(WORK / "gate_final.json")
            or {"ok": False, "failures": ["관문 결과 없음"]})
    mode, rid = ctx.get("mode", "monthly"), ctx.get("run_id", now_kst().strftime("%Y-%m"))
    force_pr = os.environ.get("PROBE_PUBLISH", "").strip().lower() == "pr"
    st, prev = read_json(STATE, {}) or {}, read_json(WORK / "prev_state.json", {}) or {}
    rp = AR / ((st.get("run") or {}).get("report_path") or "")
    md = rp.read_text(encoding="utf-8") if rp.is_file() else ""
    vres = read_json(VRES, {}) or {}
    vtxt = (f"검증 일치 {vres.get('match', 0)}+반올림 {vres.get('rounding', 0)}"
            f" / {vres.get('checked', 0)} · 불일치 {len(vres.get('mismatch') or [])}"
            f" · 확인 불가 {len(vres.get('unverifiable') or [])}") if vres else "검증 결과 없음"
    if os.environ.get("SELFTEST_OUTCOME") == "failure":
        # 저장소 selftest 를 세울 산출물은 main 에 넣지 않는다 — 다음 daily 회차가 멈춘다
        gate = {"ok": False, "failures": (gate.get("failures") or [])
                + ["저장소 selftest 실패 — 이 산출물을 main 에 넣으면 daily 파이프라인이 멈춘다"]}
    if check_private(AR, ci=True) and os.environ.get("CI") == "true":
        gate = {"ok": False, "failures": (gate.get("failures") or []) + check_private(AR, ci=True)}
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    _git("add", "agent-research")
    if not _git("diff", "--cached", "--quiet", check=False).returncode:
        send_dm(f"🛰 SpaceX 탐사 {rid} ({mode}): 산출물 변경 없음 — 게시할 것이 없습니다.\n"
                f"관문: {'통과' if gate.get('ok') else '실패 — ' + '; '.join(gate.get('failures', [])[:3])}")
        print("[관문] 변경 없음 — 게시 생략")
        return 0 if gate.get("ok") else 1
    msg = f"probe: {rid} ({mode})"
    body = "\n\n" + ("관문 통과" if gate.get("ok") else "관문 실패:\n- " + "\n- ".join(gate.get("failures", [])))
    body += "\n" + vtxt + "\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    if gate.get("ok") and not force_pr:
        _git("commit", "-m", msg + body)
        for attempt in (1, 2):
            if _git("pull", "--rebase", "origin", "main", check=False).returncode == 0 and \
                    _git("push", "origin", "HEAD:main", check=False).returncode == 0:
                break
            _git("rebase", "--abort", check=False)
            if attempt == 2:
                raise RuntimeError("main 게시 실패(재시도 후에도 충돌)")
        lines = [f"🛰 SpaceX 탐사 {rid} ({mode}) 게시 완료"] + \
            [f"· {x}" for x in summary_lines(st, md)] + \
            [f"· 확률 {prob_line(prev, st)}", f"· {vtxt}"]
        send_dm("\n".join(lines))
        print("[관문] main 게시 완료")
        return 0
    branch = f"probe/{rid}"
    _git("checkout", "-B", branch)
    _git("commit", "-m", msg + body)
    _git("push", "-f", "origin", branch)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    reason = "PROBE_PUBLISH=pr" if gate.get("ok") else "; ".join(gate.get("failures", [])[:4])
    pr_url = ""
    r = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch,
                        "--title", msg, "--body", "탐사 관문 결과\n" + body],
                       cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        pr_url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    else:
        # 저장소 설정(Actions 의 PR 생성 허용)이 꺼져 있으면 여기로 온다 — 브랜치는 남아 있다
        pr_url = f"https://github.com/{repo}/compare/main...{branch} (PR 자동 생성 실패: {r.stderr.strip()[:120]})"
    send_dm(f"🛰 SpaceX 탐사 {rid} ({mode}) — main 미게시, 검토 필요\n· 사유: {reason}\n"
            f"· {vtxt}\n· {pr_url}")
    print(f"[관문] 브랜치 게시 — {branch} · {pr_url}")
    return 0 if gate.get("ok") else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--mode", choices=("monthly", "event"), default="monthly")
    p.add_argument("--reason", default="")
    c = sub.add_parser("check")
    c.add_argument("--stage", choices=("author", "final", "final2"), default="final")
    sub.add_parser("fix-prompt")
    sub.add_parser("publish")
    a = ap.parse_args()
    if a.cmd == "prepare":
        return cmd_prepare(a.mode, a.reason)
    if a.cmd == "check":
        stage = "final" if a.stage == "final2" else a.stage
        gate = run_checks(stage=stage)
        # 검증관은 숫자만 본다 — 보고서·state 를 고쳤다면 독립성이 깨진 것이다
        cur = content_hashes()
        if stage == "author":
            write_json(WORK / "author_hashes.json", cur)
        else:
            before = read_json(WORK / "author_hashes.json")
            if before and before != cur:
                changed = sorted(k for k in set(before) | set(cur) if before.get(k) != cur.get(k))
                gate["failures"].append(f"검증 단계에서 작성자 산출물이 바뀌었다: {changed}")
                gate["ok"] = False
        write_json(WORK / f"gate_{a.stage}.json", gate)
        for w in gate.get("warnings") or []:
            print(f"[관문] 경고(게시는 막지 않음): {w}")
        print(f"[관문] {a.stage} {'통과' if gate['ok'] else '실패'} — " +
              ("; ".join(gate["failures"]) if gate["failures"] else
               f"숫자 줄 {gate['stats'].get('numeric_lines')} · 출처 누락 {gate['stats'].get('unsourced')}"))
        return 0 if gate["ok"] else 1
    if a.cmd == "fix-prompt":
        return cmd_fix_prompt()
    return cmd_publish()


if __name__ == "__main__":
    sys.exit(main())
