#!/usr/bin/env python3
"""
버크셔 측정층 (brk_watch) — 버크셔 탐사선의 눈 · 2026-11-09
================================================================================
SpaceX 측정층(spacex_watch)과 같은 규율로, 버크셔 해서웨이(CIK 0001067983)를 기계가 잰다.
판단층(agent-research/berkshire/)은 **읽기만** 한다 — 쓰는 파일은 data/brk_watch.json 하나.

무엇을 막는가(각각 한 문장 — '방지선은 정의로 검사한다'):
  ① 감지: 버크셔가 낸 공시가 아무리 커도 엔진을 깨우지 못하는 것(지뢰 ⑳) → **모든 양식**을 받고
     등급(긴급·중요·기록)으로 크기만 정한다. 표에 없는 양식도 최소 '기록'(DM 한 줄).
  ② 침묵: SEC 가 계속 거절하는데 '조용한 날'로 보이는 것(지뢰 ⑰) → 원천별 연속 실패를 세고,
     응답이 왔는데 공시 0건이면 형식 변화로 보고 실패로 센다.
  ③ 정지: 판단층이 멈췄는데 아무도 모르는 것(9/2 이후 51일 정지의 재발) → 관측 공백 35일 초과면
     매일 DM, 33일 초과면 월간 회차를 **스스로 발사**(GitHub 예약은 시계가 아니다 — 지뢰 ㉓).
  ④ 시한폭탄: 테스트가 실제 시계를 읽는 것(지뢰 ㉑) → 시계는 `now_kst` 입구 하나.
  ⑤ 중복: 하루 3회차가 같은 공시로 세 번 우는 것 → 공시 번호(accn) seen + 발사는 이번 실행분만.

하위 명령:
  (기본)        측정 → data/brk_watch.json · DM(소장 전용) · 발사 대기열 적재
  --dispatch    커밋 뒤 단계 — 이번 실행이 올린 대기열만 repository_dispatch(brk-event)

공개 채널 변수는 받지 않는다 — 경보·사건 DM 은 TELEGRAM_ALERT_CHAT_ID(소장 DM) 전용,
미등록이면 로그로만(공개 폴백 없음).
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
WATCH_PATH = DATA_DIR / "brk_watch.json"
STATE_PATH = HERE / "agent-research" / "berkshire" / "state" / "berkshire_state.json"   # 읽기 전용
KST = timezone(timedelta(hours=9))

CIK = "0001067983"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
ENGINE_WORKFLOW = "brk-probe.yml"
SCHEMA = 1

STALE_DM_DAYS = 35          # 관측 공백 — 매일 DM
BACKSTOP_DAYS = 33          # 관측 공백 — 월간 회차 자가 발사(예약이 빠져도 판단층이 서지 않게)
BACKSTOP_COOLDOWN_DAYS = 3
SOURCE_FAIL_DM = 3          # 원천 연속 실패 n회 → DM 1통
REALERT_DAYS = 7
KEEP_EVENTS = 80

# 등급 — 긴급·중요는 엔진을 깨운다. 기록은 DM 한 줄.
URGENT, MAJOR, RECORD = "urgent", "major", "record"
GRADE_KO = {URGENT: "긴급", MAJOR: "중요", RECORD: "기록"}

# 8-K 항목 → 성격(kind). 항목번호로 성격을 모르면 '수시공시'
ITEM_KIND = {"5.02": "officer", "2.02": "results", "1.01": "deal", "2.01": "deal", "2.03": "financing",
             "3.02": "equity", "5.07": "vote", "7.01": "disclosure", "8.01": "other_event",
             "4.01": "auditor", "4.02": "restatement"}
URGENT_ITEMS = {"5.02", "4.02", "4.01"}

# 사건 제목(ko) — 화면은 같은 kind 로 카탈로그(bk.ev.*)에서 그 언어로 만든다(상태에서 생성 — 규칙 8)
KIND_KO = {
    "quarterly": "분기 보고서(실적)", "annual": "연차 보고서(주주서한 동봉 연차보고)",
    "holdings": "보유 주식 명세(13F)", "officer": "수시공시 — 임원·이사 변동",
    "results": "수시공시 — 실적 발표", "deal": "수시공시 — 인수·중요 계약",
    "financing": "수시공시 — 차입·채무", "equity": "수시공시 — 주식 발행",
    "vote": "수시공시 — 주총 표결 결과", "disclosure": "수시공시 — 공정 공시",
    "other_event": "수시공시 — 기타 중요 사건", "auditor": "수시공시 — 감사인 변경",
    "restatement": "수시공시 — 재무제표 신뢰 불가(정정)", "current": "수시공시",
    "proxy": "주주총회 위임장(주총 안건)", "late": "정기 보고서 제출 지연 통지",
    "insider": "내부자·대량 보유자 거래 보고", "stake": "5% 이상 대량 보유 보고",
    "registration": "증권 신고(발행 등록)", "prospectus": "투자설명서·발행 조건",
    "other": "그 밖의 공시",
}


def now_kst():
    """측정층 시계의 유일한 입구 — 테스트는 여기만 고정한다(지뢰 ㉑)."""
    return datetime.now(KST)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, doc):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def sec_headers():
    ua = os.environ.get("SEC_USER_AGENT", "").strip() or "UNIVERTRIX univertrix.com contact@univertrix.com"
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


# ────────────────────────────────────────────────────────────────
# 순수 함수 — 테스트가 직접 두드린다
# ────────────────────────────────────────────────────────────────

def split_items(items):
    return [x.strip() for x in re.split(r"[,\s]+", items or "") if re.fullmatch(r"\d\.\d{2}", x.strip())]


def classify(form, items=""):
    """(등급, kind) — 표에 없는 양식도 '기록'으로 받는다. 조용히 버리는 양식은 없다."""
    f = (form or "").upper().strip()
    base = f[:-2] if f.endswith("/A") else f
    its = split_items(items)
    if base.startswith("NT "):
        return URGENT, "late"
    if base == "8-K":
        if any(i in URGENT_ITEMS for i in its):
            return URGENT, next(ITEM_KIND[i] for i in its if i in URGENT_ITEMS)
        for i in its:
            if i in ITEM_KIND and ITEM_KIND[i] not in ("disclosure",):
                return MAJOR, ITEM_KIND[i]
        # 버크셔는 8-K 를 드물게 낸다 — 성격을 모르는 8-K 도 엔진을 깨운다(⑳ 규칙 ②)
        return MAJOR, (ITEM_KIND.get(its[0]) if its else None) or "current"
    if base == "10-Q":
        return MAJOR, "quarterly"
    if base == "10-K":
        return MAJOR, "annual"
    if base.startswith("13F-HR"):
        return MAJOR, "holdings"
    if base in ("DEF 14A", "DEFA14A", "PRE 14A"):
        return MAJOR, "proxy"
    if base in ("SC 13D", "SCHEDULE 13D"):
        return MAJOR, "stake"
    if base in ("SC 13G", "SCHEDULE 13G"):
        return RECORD, "stake"
    if base in ("3", "4", "5", "144"):
        return RECORD, "insider"
    if base.startswith(("S-3", "S-4", "S-8", "F-")):
        return RECORD, "registration"
    if base.startswith(("424B", "FWP")):
        return RECORD, "prospectus"
    return RECORD, "other"


def event_title(kind, form="", lang="ko"):
    """사건 제목(ko) — DM·기록용. 화면은 kind 로 카탈로그에서 만든다."""
    t = KIND_KO.get(kind) or KIND_KO["other"]
    return f"{t} ({form})" if form else t


def filing_url(accn, primary_doc=""):
    a = (accn or "").replace("-", "")
    cik = str(int(CIK))
    if primary_doc:
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{a}/{primary_doc}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{a}/"


def parse_recent(subs):
    """submissions JSON → 공시 행 목록(최신순). 형식이 바뀌어 읽을 수 없으면 [] (호출 쪽이 실패로 센다)."""
    rec = ((subs or {}).get("filings") or {}).get("recent") or {}
    accns = rec.get("accessionNumber") or []
    out = []
    for i, accn in enumerate(accns):
        def g(k):
            v = rec.get(k) or []
            return v[i] if i < len(v) else ""
        out.append({"accn": accn, "form": g("form"), "filed": g("filingDate"),
                    "report_date": g("reportDate"), "items": g("items"),
                    "primary_doc": g("primaryDocument"), "desc": g("primaryDocDescription")})
    return out


def iso_dates(text):
    return re.findall(r"20\d{2}-\d{2}-\d{2}", text or "")


def trigger_due(triggers, today):
    """대기 중인 트리거 중 예상일(ISO 날짜가 적힌 것)이 오늘 이전·당일인 것. 자유 문장 상태는 읽지 않는다."""
    out = []
    for t in triggers or []:
        if not isinstance(t, dict):
            continue
        st = str(t.get("status") or "")
        if st and not st.startswith("대기"):
            continue
        ds = iso_dates(str(t.get("expected_date") or ""))
        if not ds:
            continue
        try:
            d = date.fromisoformat(ds[0])
        except ValueError:
            continue
        if d <= today:
            out.append({"id": t.get("id"), "name": t.get("name"), "expected": ds[0],
                        "days_over": (today - d).days})
    return out


def probe_age_days(state, today):
    """판단층 관측 공백(일). state 가 없으면 None."""
    rd = ((state or {}).get("run") or {}).get("run_date")
    try:
        return (today - date.fromisoformat(str(rd)[:10])).days
    except Exception:
        return None


# XBRL — 분기 프레임(CYyyyyQn, 순간값은 …I)으로만 읽는다. 누적 차분은 하지 않는다(라벨 지뢰 F7 회피).
FACT_KEYS = [
    ("net_income_attrib_usd_m", ["NetIncomeLoss"], "duration"),
    ("revenues_usd_m", ["Revenues"], "duration"),
    ("buyback_usd_m", ["PaymentsForRepurchaseOfCommonStock"], "duration"),
    ("equity_usd_m", ["StockholdersEquity"], "instant"),
    ("cash_usd_m", ["CashAndCashEquivalentsAtCarryingValue", "Cash"], "instant"),
]


def quarter_facts(facts, keys=FACT_KEYS, last_n=6):
    """companyfacts → {분기: {키: 백만 달러}} + coverage. 못 잰 키는 사유를 남긴다(지뢰 ㉒)."""
    gaap = ((facts or {}).get("facts") or {}).get("us-gaap") or {}
    quarters, status = {}, {}
    for key, concepts, kind in keys:
        found = False
        for c in concepts:
            units = ((gaap.get(c) or {}).get("units") or {}).get("USD") or []
            pat = r"CY(\d{4})Q([1-4])I" if kind == "instant" else r"CY(\d{4})Q([1-4])"
            rows = [u for u in units if re.fullmatch(pat, str(u.get("frame") or ""))]
            if not rows:
                continue
            for u in rows:
                m = re.fullmatch(pat, u["frame"])
                q = f"{m.group(2)}Q{m.group(1)[2:]}"
                quarters.setdefault(q, {})[key] = round(float(u["val"]) / 1e6, 1)
            status[key] = f"ok:{c}"
            found = True
            break
        if not found:
            status[key] = "missing: " + "·".join(concepts) + " 분기 프레임 없음"

    def qkey(q):
        return (int(q[2:]), int(q[0]))
    order = sorted(quarters, key=qkey)[-last_n:]
    cov = {k: sum(1 for q in order if quarters[q].get(k) is not None) for k, _, _ in keys}
    return {q: quarters[q] for q in order}, cov, status


def should_alert(prev, cur_streak, now, threshold=SOURCE_FAIL_DM, realert=REALERT_DAYS):
    """원천 연속 실패 경보 — 임계 도달 시 1회 · 미해결이면 7일 재알림. (보낼까, 새 상태)."""
    prev = dict(prev or {})
    if cur_streak < threshold:
        return False, prev
    last = prev.get("alerted_at")
    if not last:
        prev["alerted_at"] = now.isoformat(timespec="minutes")
        return True, prev
    try:
        if (now - datetime.fromisoformat(last)).days >= realert:
            prev["alerted_at"] = now.isoformat(timespec="minutes")
            return True, prev
    except Exception:
        prev["alerted_at"] = now.isoformat(timespec="minutes")
        return True, prev
    return False, prev


# ────────────────────────────────────────────────────────────────
# 입출력
# ────────────────────────────────────────────────────────────────

DM_LOG = []


def send_dm(text):
    """소장 DM 전용 — 미등록이면 로그로만(공개 채널 폴백 없음). 결과는 DM_LOG 에."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    head = (text or "").split("\n", 1)[0][:80]
    at = now_kst().isoformat(timespec="minutes")
    if not token or not chat:
        print("[버크셔] DM 수신처 미등록 — 발송 생략(공개 폴백 없음). 내용:\n" + text)
        DM_LOG.append({"at": at, "ok": False, "why": "unregistered", "head": head})
        return False
    try:
        from send_telegram_briefing import send_telegram, esc
        send_telegram(token, chat, esc(text))
        DM_LOG.append({"at": at, "ok": True, "head": head})
        return True
    except Exception as e:
        print(f"[버크셔] DM 발송 실패: {e}", file=sys.stderr)
        DM_LOG.append({"at": at, "ok": False, "why": type(e).__name__, "head": head})
        return False


def http_json(url, headers=None, timeout=30):
    import requests
    r = requests.get(url, headers=headers or sec_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def engine_runs(limit=10):
    """탐사 엔진(brk-probe.yml) 최근 실행 — 감시자의 감시자. 조회 불가면 None."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return None
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "univertrix-brk-watch"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    try:
        d = http_json(f"https://api.github.com/repos/{repo}/actions/workflows/{ENGINE_WORKFLOW}/runs?per_page={limit}",
                      headers=hdr)
        return [{"id": r.get("id"), "status": r.get("status"), "conclusion": r.get("conclusion"),
                 "event": r.get("event"), "created_at": r.get("created_at")} for r in d.get("workflow_runs") or []]
    except Exception as e:
        print(f"[버크셔] 엔진 실행 기록 조회 실패: {e}", file=sys.stderr)
        return None


def _queue(watch, item):
    item = dict(item, state="queued", run_id=os.environ.get("GITHUB_RUN_ID", ""),
                queued_at=now_kst().isoformat(timespec="minutes"))
    q = [x for x in (watch.get("dispatch_queue") or [])
         if str(x.get("queued_at", ""))[:10] == now_kst().date().isoformat()]
    q.append(item)
    watch["dispatch_queue"] = q


def run(fetch_subs=None, fetch_facts=None, fetch_runs=None):
    """측정 1회. fetch_* 는 테스트가 갈아끼운다(네트워크 없이 배선을 검사한다)."""
    fetch_subs = fetch_subs or (lambda: http_json(SUBMISSIONS_URL))
    fetch_facts = fetch_facts or (lambda: http_json(FACTS_URL))
    fetch_runs = fetch_runs or engine_runs
    now = now_kst()
    today = now.date()
    watch = read_json(WATCH_PATH, {}) or {}
    state = read_json(STATE_PATH, {}) or {}
    src = watch.get("sources") or {}
    seen = set(watch.get("seen") or [])
    events = list(watch.get("events") or [])
    alerts = dict(watch.get("alerts") or {})
    dm_lines, wake = [], []

    # ① 공시 — 모든 양식
    sub_state = dict(src.get("sec_submissions") or {})
    rows = None
    try:
        rows = parse_recent(fetch_subs())
        if not rows:
            raise ValueError("응답은 왔지만 공시 0건 — 형식 변화로 본다(⑰)")
        sub_state.update(outcome="ok", streak=0, at=now.isoformat(timespec="minutes"), n=len(rows))
        if sub_state.pop("alerted_at", None):
            dm_lines.append("✅ SEC 공시목록 응답 회복")
    except Exception as e:
        rows = None
        sub_state["outcome"] = "fail"
        sub_state["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        sub_state["streak"] = int(sub_state.get("streak") or 0) + 1
        sub_state["at"] = now.isoformat(timespec="minutes")
        send, sub_state = should_alert(sub_state, sub_state["streak"], now)
        if send:
            dm_lines.append(f"⚠️ SEC 공시목록 {sub_state['streak']}회 연속 실패 — {sub_state['error']}")
    src["sec_submissions"] = sub_state

    baseline = not watch.get("baselined")
    if rows is not None:
        for r in rows:
            if r["accn"] in seen:
                continue
            grade, kind = classify(r["form"], r["items"])
            ev = {"accn": r["accn"], "date": r["filed"], "form": r["form"], "items": r["items"],
                  "grade": grade, "kind": kind, "title": event_title(kind, r["form"]),
                  "url": filing_url(r["accn"], r["primary_doc"]), "baseline": baseline}
            seen.add(r["accn"])
            events.append(ev)
            if baseline:
                continue
            dm_lines.append(f"{'🚨' if grade == URGENT else '📄'} [{GRADE_KO[grade]}] {r['filed']} · "
                            f"{ev['title']} · {ev['url']}")
            if grade in (URGENT, MAJOR):
                wake.append(ev)
        if baseline:
            watch["baselined"] = now.isoformat(timespec="minutes")
            print(f"[버크셔] 첫 회차 — 기존 공시 {len(rows)}건을 기준선으로만 기록(발사·DM 없음)")
    events.sort(key=lambda e: (e.get("date") or "", e.get("accn") or ""), reverse=True)
    events = events[:KEEP_EVENTS]

    # ② XBRL 분기 팩트 — 실패해도 공시 감시는 계속
    fact_state = dict(src.get("sec_facts") or {})
    try:
        q, cov, status = quarter_facts(fetch_facts())
        watch["facts"] = {"quarters": q, "coverage": cov, "status": status,
                          "measured_at": now.isoformat(timespec="minutes")}
        zero = [k for k, v in cov.items() if v == 0]
        fact_state.update(outcome="ok", streak=0, at=now.isoformat(timespec="minutes"), zero_keys=zero)
        fact_state.pop("alerted_at", None)
    except Exception as e:
        fact_state["outcome"] = "fail"
        fact_state["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        fact_state["streak"] = int(fact_state.get("streak") or 0) + 1
        send, fact_state = should_alert(fact_state, fact_state["streak"], now)
        if send:
            dm_lines.append(f"⚠️ SEC 재무팩트 {fact_state['streak']}회 연속 실패 — {fact_state['error']}")
    src["sec_facts"] = fact_state

    # ③ 트리거 due — 예상일이 지난 '대기' 트리거는 한 번 울고 엔진을 깨운다
    fired = set(watch.get("due_fired") or [])
    due = trigger_due(state.get("triggers_next_12m"), today)
    for d in due:
        k = f"{d['id']}@{d['expected']}"
        if k in fired:
            continue
        fired.add(k)
        dm_lines.append(f"⏰ 트리거 {d['id']} 예상일 도래({d['expected']}) — {str(d['name'])[:80]}")
        wake.append({"title": f"트리거 {d['id']} 예상일 {d['expected']}", "url": "", "kind": "trigger"})
    watch["due"] = due
    watch["due_fired"] = sorted(fired)[-200:]

    # ④ 판단층 정지 감시 + 월간 자가 발사
    age = probe_age_days(state, today)
    watch["probe_age_days"] = age
    if age is None:
        dm_lines.append("⚠️ 버크셔 판단층 state 를 읽을 수 없음 — 탐사선 화면이 비어 있을 수 있음")
    else:
        if age > STALE_DM_DAYS and alerts.get("stale_dm") != today.isoformat():
            alerts["stale_dm"] = today.isoformat()
            dm_lines.append(f"⚠️ 버크셔 탐사 관측 공백 {age}일 — 월간 회차가 빠졌다")
        last_bs = alerts.get("backstop")
        cool = True
        if last_bs:
            try:
                cool = (today - date.fromisoformat(last_bs)).days >= BACKSTOP_COOLDOWN_DAYS
            except ValueError:
                cool = True
        if age > BACKSTOP_DAYS and cool:
            alerts["backstop"] = today.isoformat()
            _queue(watch, {"mode": "monthly", "reason": f"관측 공백 {age}일 — 월간 회차 자가 발사(예약 누락 대비)",
                           "keys": ["backstop"]})
            dm_lines.append(f"🛰 관측 공백 {age}일 → 월간 회차 자가 발사 대기열 적재")

    # ⑤ 엔진 실행 결과 — 실패는 DM(7일 재알림)
    runs = fetch_runs()
    if runs is not None:
        done = [r for r in runs if r.get("status") == "completed"]
        last = done[0] if done else None
        watch["engine"] = {"last": last, "checked_at": now.isoformat(timespec="minutes")}
        if last and last.get("conclusion") not in ("success", "skipped", "cancelled", None):
            key = f"engine:{last.get('id')}"
            prev = alerts.get(key)
            ok_send = not prev
            if prev:
                try:
                    ok_send = (today - date.fromisoformat(prev)).days >= REALERT_DAYS
                except ValueError:
                    ok_send = True
            if ok_send:
                alerts[key] = today.isoformat()
                dm_lines.append(f"🚨 버크셔 탐사 엔진 실행 {last.get('conclusion')} ({str(last.get('created_at'))[:16]})")
        alerts = {k: v for k, v in alerts.items() if not k.startswith("engine:") or (last and k == f"engine:{last.get('id')}")}

    # ⑥ 사건 모드 발사 대기열 — 긴급·중요 공시와 트리거 due 를 한 번에
    if wake:
        reason = " · ".join(dict.fromkeys(f"{e.get('title')}" for e in wake))
        urls = [e.get("url") for e in wake if e.get("url")]
        if urls:
            reason += " · 원문: " + " ".join(dict.fromkeys(urls))
        _queue(watch, {"mode": "event", "reason": reason[:900],
                       "keys": [e.get("accn") or e.get("kind") for e in wake][:20]})

    if dm_lines:
        send_dm("🏛 버크셔 측정층\n" + "\n".join(dm_lines[:25]))

    watch.update({"schema": SCHEMA, "generated_at": now.isoformat(timespec="minutes"), "cik": CIK,
                  "sources": src, "seen": sorted(seen), "events": events, "alerts": alerts,
                  "dm_log": (DM_LOG + list(watch.get("dm_log") or []))[:30]})
    write_json(WATCH_PATH, watch)
    n_new = sum(1 for e in events if not e.get("baseline") and e.get("accn") in seen
                and str(e.get("date", "")) >= (today - timedelta(days=3)).isoformat())
    sec_txt = "실패" if sub_state.get("outcome") != "ok" else ("기준선" if baseline else "정상")
    print(f"[버크셔] 측정 완료 — 공시 {sec_txt} · 최근 3일 사건 {n_new} · "
          f"깨움 {len(wake)} · 관측 공백 {age}일")
    # 원천이 이번에 실패했으면 실패로 보고한다(침묵은 통과가 아니다) — 그래도 파일은 썼다
    return 0 if sub_state.get("outcome") == "ok" else 1


def dispatch(post=None):
    """커밋 뒤 단계 — 이번 실행이 올린 대기열만 쏜다. 실패는 실패로(exit 1 + DM)."""
    watch = read_json(WATCH_PATH, {}) or {}
    rid = os.environ.get("GITHUB_RUN_ID", "")
    q = [x for x in (watch.get("dispatch_queue") or []) if x.get("state") == "queued" and x.get("run_id", "") == rid]
    if not q:
        print("[버크셔] 발사 대기열 비어 있음 — 침묵")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not tok:
        print("[버크셔] GITHUB_REPOSITORY/GITHUB_TOKEN 없음 — 발사 불가", file=sys.stderr)
        return 1
    if post is None:
        import requests
        post = requests.post
    mode = "monthly" if any(x.get("mode") == "monthly" for x in q) else "event"
    reason = " · ".join(dict.fromkeys(str(x.get("reason")) for x in q))[:900]
    r = post(f"https://api.github.com/repos/{repo}/dispatches",
             headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"},
             json={"event_type": "brk-event", "client_payload": {"mode": mode, "reason": reason}}, timeout=30)
    code = getattr(r, "status_code", None)
    if code in (200, 204):
        print(f"[버크셔] 탐사 엔진 발사 완료 ({mode} · {len(q)}건): {reason}")
        return 0
    msg = f"🚨 버크셔 탐사 엔진 발사 실패 — HTTP {code}: {str(getattr(r, 'text', ''))[:200]}"
    print(msg, file=sys.stderr)
    send_dm(msg)
    return 1


def main():
    if "--dispatch" in sys.argv[1:]:
        return dispatch()
    return run()


if __name__ == "__main__":
    sys.exit(main())
