#!/usr/bin/env python3
"""
정비 관제탑 (pipeline_sentinel) — 파이프라인의 '침묵 실패'를 시끄럽게 만드는 관측자
================================================================================
배경: 이 파이프라인은 전 수집 단계가 continue-on-error + 우아한 저하로 묶여 있어
      **절대 죽지 않는다.** 대신 조용히 비어간다. (Anthropic 공식 RSS가 404가 된 뒤
      몇 주간 아무도 몰랐던 전례 — 2026-08-17 실측으로야 발각)
      우아한 저하가 절반이면, 나머지 절반은 "실패는 시끄럽게"다. 이 스크립트가 그 절반.

원칙 (반드시 지킬 것):
  1. **관찰자로만 존재한다.** 수집 스크립트의 저하 동작을 절대 바꾸지 않는다.
     여기서는 산출물(JSON)만 읽는다 — 원인 불문, '결과가 비었는가'로 판정한다.
  2. **자신의 실패가 파이프라인을 죽이지 않는다.** 최상위 try로 감싸 항상 exit 0.
     단 예외를 삼키지 않고 traceback 전문을 stderr에 뱉는다
     (침묵 감시자가 침묵하는 아이러니 방지 — CLAUDE.md '프로브 침묵' 지뢰 참조).
  3. **정상이면 완전 침묵.** 매일 "정상입니다" 스팸 금지. 경보와 회복만 말한다.

실행 위치: daily-update.yml full 모드 맨 끝(git commit 직전), if: !cancelled()
발송 경로: 자체 구현 금지 — send_telegram_briefing 의 발송부를 재사용한다
           (브리핑은 매일 실전 발송되므로 그 경로는 매일 검증되는 셈)

⚠️ mtime 함정: CI는 매 실행 fresh checkout이라 **모든 파일 mtime = 체크아웃 시각**이다.
   파일 갱신 정체를 mtime으로 재면 영원히 "방금 갱신됨"으로 보여 경보가 절대 안 울린다
   (= 침묵하는 감시자). 그래서 정체 판정은 **파일 내용의 타임스탬프**를 쓰고,
   봇 실행 흔적은 워크플로가 주입하는 **스텝 결과(SENTINEL_STEPS)** 로 판정한다.
   mtime은 로컬 실행용 폴백일 뿐이며, 폴백일 때는 로그에 그 사실을 명시한다.
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:  # 윈도우 로컬 실행에서 한글 출력이 cp949로 죽는 것 방지 (관측자는 죽지 않는다)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))   # 같은 폴더 모듈(fetch_signals 등) 참조용

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
STATE_PATH = DATA_DIR / "sentinel_state.json"
CONFIG_PATH = DATA_DIR / "sentinel_config.json"   # 선택 — 없으면 아래 기본값
KST = timezone(timedelta(hours=9))

MAX_ALERTS = 5          # 회당 경보 상한 (초과분은 "외 N건")
KEEP_DAYS = 7           # 발송 서명 보관 일수
KEEP_BUFFETT = 14       # buffett 일별 관측 보관 일수

DEFAULTS = {
    # 소스가 이 횟수만큼 연속(full 실행 기준) 0건이면 경보
    "zero_streak_limit": 2,
    # 측정 종수 급감 판정 임계 (전일 대비 감소율)
    "buffett_drop_ratio": 0.30,
    # 갱신 정체 감시 — 파일별 허용 시간(h). 주기 항목은 주기를 감안해 따로 준다.
    #   calendar/gurus: 매 full 실행마다 generated_at 갱신 → 48h
    #   columns: 필독 신호가 없는 날은 칼럼이 없는 것이 정상(하루 1편 상한) →
    #            토요일 주간 칼럼 주기까지 감안해 72h
    "stale_hours": {"calendar.json": 48, "gurus.json": 48, "columns.json": 72},
    # 실행 흔적을 감시할 봇 (SENTINEL_STEPS 키 → 사람이 읽을 이름)
    "bots": {"parallax_journal": "관측노트 봇", "community_notice": "관제탑 공지",
             "companion_essays": "동행 관측 엔진"},
    # 소장이 던진 소재가 몇 회차 연속 잔존하면 경보할지 (full 기준)
    "materials_streak_limit": 2,
    # 피드 요청 실패(http_error/timeout)가 몇 회 연속이면 경보할지.
    # 0건(zero)보다 임계가 낮은 이유: 0건은 조용한 날일 수 있지만 요청 실패는
    # 언제나 장애다. 이 구분이 fetch_status.json 원장으로 비로소 가능해졌다.
    "feed_error_limit": 2,
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))   # deep copy
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update(user)
        except Exception as e:
            print(f"[정비] 설정 읽기 실패 → 기본값 ({e})", file=sys.stderr)
    return cfg


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_state():
    st = read_json(STATE_PATH, None)
    if not isinstance(st, dict):
        st = {}
    st.setdefault("version", 1)
    st.setdefault("sources", {})
    st.setdefault("buffett", [])
    st.setdefault("sent", {})
    st.setdefault("last_verdict", {})
    st.setdefault("materials", {"streak": 0, "alerted": False})
    st.setdefault("feeds", {})
    return st


def save_state(state, today):
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    state["sent"] = {d: v for d, v in state.get("sent", {}).items() if d >= cutoff}
    bcut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_BUFFETT)).strftime("%Y-%m-%d")
    state["buffett"] = sorted(
        (e for e in state.get("buffett", []) if e.get("date", "") >= bcut),
        key=lambda e: e.get("date", ""))
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")


def sig(item, today):
    """사건 서명 — 같은 항목·같은 날은 한 번만 발송한다."""
    return f"sentinel:{item}:{today}"


def alert(item, text, today, kind="alert"):
    return {"sig": sig(item, today), "text": text, "kind": kind}


# ────────────────────────────────────────────────────────────────
# 판정부 — 전부 순수 함수 (입력 = 산출물, 출력 = 경보 목록). 테스트 가능해야 한다.
# ────────────────────────────────────────────────────────────────

def source_names():
    """감시 대상 소스 이름 = fetch_signals 의 실제 설정 (설정 변경을 자동 추적)."""
    try:
        from fetch_signals import load_sources
        return [s.get("name", "") for s in load_sources().get("sources", []) if s.get("name")]
    except Exception as e:
        print(f"[정비] 소스 설정 로드 실패 → 신호 감시 축소 ({e})", file=sys.stderr)
        return []


def judge_signals(signals, names, state, today, cfg):
    """① 소스별 오늘 수집 건수. 연속 0건 → 경보, 0→N 회복 → ✅ 1회.

    signals: data/signals.json 의 signals 리스트 (captured = 'YYYY-MM-DD HH:MM' KST)
    """
    limit = int(cfg.get("zero_streak_limit", 2))
    counts = {n: 0 for n in names}
    for s in signals or []:
        n = s.get("source")
        if n in counts and str(s.get("captured", ""))[:10] == today:
            counts[n] += 1

    out = []
    srcs = state.setdefault("sources", {})
    for n in names:
        rec = srcs.setdefault(n, {"zero_streak": 0, "alerted": False, "ever_seen": False})
        c = counts[n]
        if c > 0:
            was_alerted = rec.get("alerted", False)
            rec["zero_streak"] = 0
            rec["alerted"] = False
            rec["ever_seen"] = True
            if was_alerted:
                out.append(alert(f"signals-recover:{n}",
                                 f"fetch_signals: {n} 회복 — 오늘 {c}건 ✅",
                                 today, kind="recover"))
            continue
        # 0건 — 한 번도 수집된 적 없는 신규 소스는 아직 판정하지 않는다(기준선 없음)
        rec["zero_streak"] = int(rec.get("zero_streak", 0)) + 1
        if not rec.get("ever_seen"):
            continue
        if rec["zero_streak"] >= limit and not rec.get("alerted"):
            rec["alerted"] = True
            out.append(alert(f"signals:{n}",
                             f"fetch_signals: {n} 0건 {rec['zero_streak']}회 연속", today))
    return out, counts


def judge_buffett(buf, state, today, cfg):
    """② 측정 종수 급감 / 선행(forward) 종수 감소 → 경보.

    비교 기준은 '가장 최근의 이전 날' 마지막 관측치 (하루 3회 실행이라 같은 날은 갱신만).
    """
    out = []
    items = (buf or {}).get("items")
    if not isinstance(items, list) or not items:
        return out, None
    cur = {
        "date": today,
        "measured": sum(1 for x in items if x.get("pe") is not None),
        "forward": sum(1 for x in items if x.get("basis") == "forward"),
    }
    prev = None
    for e in sorted(state.get("buffett", []), key=lambda e: e.get("date", "")):
        if e.get("date", "") < today:
            prev = e
    if prev:
        pm, cm = int(prev.get("measured", 0)), cur["measured"]
        if pm > 0 and (pm - cm) / pm >= float(cfg.get("buffett_drop_ratio", 0.30)):
            pct = round((pm - cm) / pm * 100)
            out.append(alert("buffett-measured", f"buffett: 측정 {pm}→{cm} 급감 (-{pct}%)", today))
        pf, cf = int(prev.get("forward", 0)), cur["forward"]
        if cf < pf:
            out.append(alert("buffett-forward",
                             f"buffett: 선행 {pf}→{cf} — EPS 취재분 유실 의심", today))
    # 오늘 관측치 기록 (같은 날 여러 회차면 마지막 값으로 갱신)
    hist = [e for e in state.get("buffett", []) if e.get("date") != today]
    hist.append(cur)
    state["buffett"] = hist
    return out, cur


def judge_snapshot(snap_dir, today):
    """③ 오늘 날짜 스냅샷 부재 → 경보 (시총 수집이 통째로 실패한 신호)."""
    if (Path(snap_dir) / f"{today}.json").exists():
        return []
    return [alert("snapshot", f"fetch_data: 오늘({today}) 스냅샷 없음 — 시총 수집 실패 의심", today)]


def _age_hours(ts, now):
    return (now - ts).total_seconds() / 3600.0


def _content_time(name, doc):
    """파일 내용에서 갱신 시각을 뽑는다 (CI에서 mtime은 체크아웃 시각이라 무용)."""
    if not isinstance(doc, dict):
        return None
    raw = doc.get("generated_at")
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=KST)
        except Exception:
            pass
    if name == "columns.json":
        dates = [c.get("date", "") for c in doc.get("columns", []) if isinstance(c, dict)]
        dates = [d.replace(".", "-") for d in dates if d]
        if dates:
            try:  # 날짜 단위 기록 → 그날 끝(23:59)을 갱신 시각으로 본다
                return datetime.strptime(max(dates), "%Y-%m-%d").replace(
                    hour=23, minute=59, tzinfo=KST)
            except Exception:
                pass
    return None


def judge_staleness(data_dir, cfg, today, now):
    """④ 산출물 갱신 정체 → 경보. 내용 타임스탬프 우선, 없으면 mtime 폴백(로그에 명시)."""
    out, notes = [], []
    for name, hours in (cfg.get("stale_hours") or {}).items():
        path = Path(data_dir) / name
        if not path.exists():
            out.append(alert(f"missing:{name}", f"{name}: 파일 없음", today))
            continue
        ts = _content_time(name, read_json(path, None))
        if ts is None:
            ts = datetime.fromtimestamp(path.stat().st_mtime, KST)
            notes.append(f"{name} 정체 판정에 mtime 폴백 사용 — CI에서는 신뢰 불가")
        age = _age_hours(ts, now)
        if age > float(hours):
            out.append(alert(f"stale:{name}",
                             f"{name}: {round(age)}시간째 갱신 정체 (허용 {hours}h)", today))
    return out, notes


def judge_bots(cfg, today, step_outcomes, data_dir, now):
    """⑤ 봇 실행 흔적 부재 → 경보.

    '기록할 사건 없음'은 정상이므로 **발행 건수는 보지 않는다.** 실행 자체만 본다.
    - CI: 워크플로가 넣어준 스텝 결과(SENTINEL_STEPS)가 유일하게 믿을 수 있는 흔적
    - 로컬: 상태 파일 mtime 폴백 (CI에서는 체크아웃 시각이라 무의미)
    """
    out, notes = [], []
    bots = cfg.get("bots") or {}
    if step_outcomes:
        for key, label in bots.items():
            oc = step_outcomes.get(key)
            if oc is None:
                notes.append(f"{key} 스텝 결과 미전달 — 판정 생략")
            elif oc != "success":
                out.append(alert(f"bot:{key}", f"{label}: 이번 회차 실행 흔적 없음 (스텝 {oc})", today))
        return out, notes

    notes.append("SENTINEL_STEPS 미전달 — 봇 실행 흔적을 mtime 폴백으로 판정 (CI라면 신뢰 불가)")
    fallback = {"parallax_journal": "parallax_state.json", "community_notice": "notice_state.json"}
    for key, label in bots.items():
        fname = fallback.get(key)
        if not fname:
            continue
        p = Path(data_dir) / fname
        if not p.exists():
            out.append(alert(f"bot:{key}", f"{label}: 상태 파일 없음 ({fname})", today))
            continue
        age = _age_hours(datetime.fromtimestamp(p.stat().st_mtime, KST), now)
        if age > 48:
            out.append(alert(f"bot:{key}", f"{label}: 상태 파일 {round(age)}시간째 정체", today))
    return out, notes


_KIND_KO = {"signals": "신호", "companion": "동행"}


def judge_feeds(status, state, today, cfg):
    """⑦ 소스별 fetch 결과 — **"조용한 날"과 "죽은 소스"를 구분한다.**

    2026-08-22 동행 7소스 503 사고 전까지 sentinel 은 signals.json 의 결과물만
    봤다. 그래서 동행 소스가 통째로 죽어도 아무 소리가 나지 않았다(사각지대).
    이제 feed_client 가 남기는 data/fetch_status.json 을 읽어 판정한다:

      http_error / timeout → **언제나 장애**다. 연속 2회면 경보.
      zero (성공했는데 0건) → 그날 발표가 없었을 수도 있다. 임계는 관대하게
        zero_streak_limit(운영값 4)를 그대로 쓴다.

    신호 소스의 zero 는 judge_signals 가 signals.json 기준으로 이미 보고 있어
    여기서 또 세지 않는다 — 같은 사실로 두 번 울리지 않기 위해서다.
    (관찰자 원칙 유지: 수집기를 건드리지 않고 산출물만 읽는다)
    """
    err_limit = int(cfg.get("feed_error_limit", 2))
    zero_limit = int(cfg.get("zero_streak_limit", 4))
    srcs = (status or {}).get("sources") or {}
    recs = state.setdefault("feeds", {})
    out, seen = [], {"error": 0, "zero": 0, "ok": 0}

    for key, s in sorted(srcs.items()):
        if not isinstance(s, dict):
            continue
        kind = s.get("kind", "")
        label = s.get("source", key)
        outcome = s.get("outcome", "")
        rec = recs.setdefault(key, {"err_streak": 0, "zero_streak": 0,
                                    "err_alerted": False, "zero_alerted": False})
        ko = _KIND_KO.get(kind, kind)

        if outcome in ("http_error", "timeout"):
            seen["error"] += 1
            rec["zero_streak"], rec["zero_alerted"] = 0, False
            rec["err_streak"] = int(rec.get("err_streak", 0)) + 1
            if rec["err_streak"] >= err_limit and not rec.get("err_alerted"):
                rec["err_alerted"] = True
                what = f"HTTP {s['code']}" if s.get("code") else outcome
                out.append(alert(f"feed:{key}",
                                 f"{ko} {label}: {what} {rec['err_streak']}회 연속", today))
            continue

        # 여기부터는 요청 자체는 성공한 경우
        was_err = rec.get("err_alerted", False)
        rec["err_streak"], rec["err_alerted"] = 0, False
        if was_err:
            out.append(alert(f"feed-recover:{key}", f"{ko} {label}: 응답 회복 ✅",
                             today, kind="recover"))
        if outcome == "zero":
            seen["zero"] += 1
            rec["zero_streak"] = int(rec.get("zero_streak", 0)) + 1
            # 신호 소스의 0건은 judge_signals 관할 — 중복 발화 금지
            if kind != "signals" and rec["zero_streak"] >= zero_limit \
                    and not rec.get("zero_alerted"):
                rec["zero_alerted"] = True
                out.append(alert(f"feed-zero:{key}",
                                 f"{ko} {label}: 0건 {rec['zero_streak']}회 연속", today))
        else:
            seen["ok"] += 1
            rec["zero_streak"], rec["zero_alerted"] = 0, False

    # 원장에서 사라진 소스의 기록은 정리한다 (설정에서 뺀 소스가 유령으로 남지 않게)
    for gone in [k for k in recs if k not in srcs]:
        recs.pop(gone, None)
    return out, seen


def judge_materials(state, comp_state, today, cfg):
    """⑥ 소장이 던진 소재가 침묵 속에 썩는 것 방지.

    관찰자 원칙 유지 — Firestore 를 직접 보지 않는다. 동행 엔진이 자기 상태 파일에
    남긴 '남은 미소비 건수'(companion_state.json)만 읽는다.
      pending > 0  → 엔진이 소비에 실패한 소재가 있다
      pending == -1 → 소재함 접근 자체가 실패했다 (더 나쁜 신호)
    full 2회 연속 잔존해야 경보한다 — 한 회차의 일시 오류로 소리치지 않기 위해서다.
    """
    limit = int(cfg.get("materials_streak_limit", 2))
    pending = comp_state.get("materials_pending", 0)
    rec = state.setdefault("materials", {"streak": 0, "alerted": False})
    try:
        pending = int(pending)
    except (TypeError, ValueError):
        pending = 0
    if pending == 0:
        was = rec.get("alerted", False)
        rec["streak"], rec["alerted"] = 0, False
        if was:
            return [alert("materials-recover", "동행 소재함: 잔존 소재 해소 ✅", today,
                          kind="recover")]
        return []
    rec["streak"] = int(rec.get("streak", 0)) + 1
    if rec["streak"] < limit or rec.get("alerted"):
        return []
    rec["alerted"] = True
    what = ("소재함 접근 실패" if pending < 0 else f"미소비 소재 {pending}건 잔존")
    return [alert("materials", f"동행 관측: {what} — {rec['streak']}회 연속", today)]


def parse_step_outcomes(raw):
    """워크플로 주입값 파싱 — JSON 또는 'k=v,k=v' 둘 다 받는다."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return {k: str(v).strip() for k, v in d.items() if str(v).strip()}
    except Exception:
        pass
    out = {}
    for part in raw.replace("\n", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def format_message(alerts, now):
    """한 메시지로 묶음 — 경보 우선, 회복은 뒤. 5건 상한 초과분은 '외 N건'."""
    hard = [a for a in alerts if a["kind"] == "alert"]
    soft = [a for a in alerts if a["kind"] != "alert"]
    ordered = hard + soft
    head = "🚨" if hard else "✅"
    lines = [f"{head} UNIVERTRIX 정비 관제 ({now.strftime('%m-%d %H:%M')})"]
    for a in ordered[:MAX_ALERTS]:
        lines.append(f"· {a['text']}")
    extra = len(ordered) - MAX_ALERTS
    if extra > 0:
        lines.append(f"· 외 {extra}건")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# 발송부
# ────────────────────────────────────────────────────────────────

def send_telegram(token, chat_id, text):
    """발송부는 자체 구현하지 않는다 — 모닝브리핑과 **같은 경로**를 재사용한다.

    이유: 브리핑은 매일 실전 발송되므로 이 경로는 사실상 매일 검증된다.
    경보는 1년에 몇 번 울릴까 말까 한 코드라, 자체 구현했다면 정작 필요한 날
    조용히 깨져 있을 위험이 크다 (침묵 실패를 잡는 코드가 침묵 실패하는 것).
    브리핑 경로는 parse_mode=HTML이므로 평문을 그대로 넘기지 않고 이스케이프한다.
    """
    from send_telegram_briefing import send_telegram as _send, esc
    _send(token, chat_id, esc(text))


def alert_chat_id():
    """전용 경보 채널이 있으면 그리로, 없으면 브리핑과 동일한 해석 체계로 폴백.

    (TELEGRAM_CHAT_ID → '@stayhungryasi' 순서는 send_telegram_briefing.main 과 같다.
     경보를 브리핑과 분리하고 싶어지면 TELEGRAM_ALERT_CHAT_ID 만 등록하면 된다 —
     지금은 미등록이 선장님 결정이며, 소음이 거슬릴 때 쓰는 퇴로로 남겨둔다.)
    """
    return (os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
            or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            or "@stayhungryasi")


def dispatch(alerts, now, today, state):
    """미발송 서명만 골라 한 통으로 발송. 성공 시에만 서명 기록(실패 시 다음 회차 재시도)."""
    sent_today = set(state.get("sent", {}).get(today, []))
    fresh = [a for a in alerts if a["sig"] not in sent_today]
    if not fresh:
        print(f"[정비] 경보 {len(alerts)}건 — 오늘 이미 발송된 서명 → 재발송 안 함")
        return False

    text = format_message(fresh, now)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[정비] TELEGRAM_BOT_TOKEN 미설정 — 발송 생략, 판정만 기록", file=sys.stderr)
        print(text)
        return False
    chat = alert_chat_id()
    try:
        send_telegram(token, chat, text)
    except Exception as e:
        print(f"[정비] 경보 발송 실패: {e}", file=sys.stderr)
        traceback.print_exc()
        return False

    state.setdefault("sent", {})[today] = sorted(sent_today | {a["sig"] for a in fresh})
    print(f"[정비] 경보 발송 완료 → {chat} ({len(fresh)}건)")
    for a in fresh:
        print(f"    {a['text']}")
    return True


def run():
    cfg = load_config()
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    state = load_state()

    names = source_names()
    signals = (read_json(DATA_DIR / "signals.json", {}) or {}).get("signals", [])
    sig_alerts, counts = judge_signals(signals, names, state, today, cfg)
    buf_alerts, buf_cur = judge_buffett(read_json(DATA_DIR / "buffett.json", {}), state, today, cfg)
    snap_alerts = judge_snapshot(DATA_DIR / "snapshots", today)
    stale_alerts, stale_notes = judge_staleness(DATA_DIR, cfg, today, now)
    steps = parse_step_outcomes(os.environ.get("SENTINEL_STEPS"))
    bot_alerts, bot_notes = judge_bots(cfg, today, steps, DATA_DIR, now)
    comp_state = read_json(DATA_DIR / "companion_state.json", {}) or {}
    mat_alerts = judge_materials(state, comp_state, today, cfg)
    feed_status = read_json(DATA_DIR / "fetch_status.json", {}) or {}
    feed_alerts, feed_seen = judge_feeds(feed_status, state, today, cfg)

    alerts = (sig_alerts + buf_alerts + snap_alerts + stale_alerts
              + bot_alerts + mat_alerts + feed_alerts)

    # 판정 요약은 항상 로그에 남긴다 (텔레그램은 문제 있을 때만 — 로그는 매번)
    sig_part = ", ".join(f"{n} {counts.get(n, 0)}건" for n in names) or "소스 없음"
    buf_part = (f" | buffett 측정 {buf_cur['measured']}·선행 {buf_cur['forward']}"
                if buf_cur else " | buffett 없음")
    step_part = f" | 스텝 {steps}" if steps else ""
    pend = comp_state.get("materials_pending", 0)
    comp_part = f" | 동행 소재 잔존 {pend}" if pend else ""
    feed_part = (f" | 피드 ok {feed_seen['ok']}·0건 {feed_seen['zero']}·실패 {feed_seen['error']}"
                 if any(feed_seen.values()) else "")
    print(f"[정비] 판정 {today} {now.strftime('%H:%M')} — "
          f"신호 {sig_part}{buf_part}{step_part}{comp_part}{feed_part}")
    for n in stale_notes + bot_notes:
        print(f"[정비] 참고: {n}", file=sys.stderr)

    if alerts:
        dispatch(alerts, now, today, state)
    else:
        print("[정비] 전 항목 정상 — 침묵")

    state["last_run"] = now.strftime("%Y-%m-%d %H:%M")
    state["last_verdict"] = {"at": state["last_run"], "ok": not alerts,
                             "alerts": [a["text"] for a in alerts]}
    save_state(state, today)
    return 0


def main():
    try:
        return run()
    except Exception:
        # 예외를 삼키지 않는다 — 전문을 남기고, 가능하면 시끄럽게 알린다
        traceback.print_exc()
        print("[정비] 관측 자체 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        try:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            if token:
                send_telegram(token, alert_chat_id(),
                              "🚨 UNIVERTRIX 정비 관제 자체가 실패했습니다 — "
                              "Actions 로그의 traceback 확인 필요")
        except Exception as e:
            print(f"[정비] 경보 발송 실패: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
