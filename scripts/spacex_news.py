#!/usr/bin/env python3
"""
SpaceX 뉴스 감시관 (spacex_news) — 공시·발사가 아닌 사건을 탐사 엔진으로 잇는 배선
================================================================================
배경 (2026-10-27, 외부 검수 G4 · 지뢰 ⑳): CEO 발언·신용등급·FAA/FCC 조치·Starlink 장애·소송·
Tesla 합병 협상·락업 보도처럼 **공시로 오지 않는 사건**은 엔진을 깨울 길이 없었다. 동행 피드는
회당 원본 100건을 이미 받고 있었는데 배선이 없었다('수단이 있어도 배선하지 않으면 없다').

하는 일 (daily full 회차마다, 측정층 spacex_watch 다음):
  ① 구글뉴스 RSS — 사건 유형별 질의(최근 48시간). 헤드라인·발행처·날짜만 쓴다(본문 없음).
  ② 처음 보는 헤드라인만 모아 **모델 1회 판정**(분류기 — 헤드라인 밖 내용은 쓰지 못하게 한다):
     material(새 사건인가) · severity(urgent/major/minor) · category · 영향 트리거 · 같은 사건 묶음(cluster) ·
     이미 기록된 사건인가(already_known) · 헤드라인이 말하는 사실 한 문장(claim_ko).
  ③ 결정(기계 규칙 — 모델이 정하지 않는다):
     중요 이상 + 새 사건 + (1급 발행처 1곳 이상 또는 서로 다른 발행처 2곳 이상) → 소장 DM + 엔진 사건 모드 발사
     중요 이상 + 새 사건 + 발행처 1곳(1급 아님)                              → DM 만('보도 단계')
     하루 엔진 깨움 상한 2회(긴급은 상한 밖). 같은 묶음(cluster)은 7일 동안 다시 깨우지 않는다.
  ④ 원장: fetch_status.json kind="spacex_news" — 질의별 결과 + 판정 모델 결과(실패는 실패로).

판단 근거로 쓰지 않는다: 보도는 '이정표 후보'다(탐사 규약 그대로). 이 감시관은 엔진을 **깨울** 뿐,
판단층(agent-research/)에는 쓰지 않는다. 경보는 소장 DM 전용(공개 채널 폴백 없음).
산출: data/spacex_news.json(상태) · data/spacex_watch.json 의 due·dispatch_queue(커밋 뒤 발사 단계가 쏜다)
"""
import hashlib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
NEWS_PATH = DATA_DIR / "spacex_news.json"
WATCH_PATH = DATA_DIR / "spacex_watch.json"
STATE_PATH = HERE / "agent-research" / "state" / "spacex_state.json"   # 읽기 전용
KST = timezone(timedelta(hours=9))

MODEL = os.environ.get("SPACEX_NEWS_MODEL", "").strip() or "claude-sonnet-5-5"
MAX_HEADLINES = 240         # 회당 판정 상한 — 2026-10-27 첫 실전: 2일 창에 441건(하루 약 220건). 60 이면 이월이 쌓여
                            # 옛 헤드라인이 2일 창 밖으로 판정 없이 빠져나간다(조용한 누락)
CHUNK = 40                  # 판정 호출 1회당 헤드라인 수 — 2026-10-27 18시 첫 판정 회차가 60건 묶음에서 실패
                            # (전 항목을 판정문으로 돌려받는 형식이라 응답이 길었다) → 중대한 것만 돌려받고 묶음도 줄인다
WAKE_CAP_PER_DAY = 2        # 뉴스로 엔진을 깨우는 하루 상한(긴급은 상한 밖)
CLUSTER_COOLDOWN_D = 7
KEEP_SEEN = 1500

# (이름, 질의) — 사건 유형마다 하나. '무엇이 일어날 수 있는가'에서 출발한다(지뢰 ⑳ 규칙 ①)
QUERIES = [
    ("규제·안전", 'SpaceX (FAA OR mishap OR investigation OR grounded OR "launch license")'),
    ("Starship", 'Starship (flight OR launch OR "static fire" OR explosion OR catch OR anomaly)'),
    ("Starlink", 'Starlink (outage OR ban OR license OR subscribers OR pricing OR FCC)'),
    ("소송·조사", 'SpaceX (lawsuit OR sued OR probe OR subpoena OR antitrust)'),
    ("자금·신용", 'SpaceX (Moody OR "S&P Global" OR Fitch OR rating OR bond OR debt OR financing OR lease)'),
    ("지배구조", 'SpaceX (merger OR "special committee" OR board OR CFO OR resigns OR appoints)'),
    ("주식·수급", 'SPCX (lockup OR "insider" OR "index inclusion" OR downgrade OR upgrade OR offering)'),
    ("정부 계약", 'SpaceX (Pentagon OR "Space Force" OR NASA OR NRO OR "Golden Dome") contract'),
    ("경쟁", '"Amazon Leo" OR Kuiper OR "New Glenn" (launch OR pricing OR service OR satellites)'),
    ("CEO 발언", 'Musk SpaceX (says OR announces OR posted OR plans)'),
]
WINDOW = "when:2d"

# 1급 발행처 — 1곳만 보도해도 엔진을 깨운다(나머지는 서로 다른 발행처 2곳 이상)
TIER1 = {"reuters", "bloomberg", "bloomberg.com", "the wall street journal", "wsj", "financial times",
         "cnbc", "associated press", "ap news", "spacenews", "ars technica", "nasa", "faa",
         "federal aviation administration", "federal communications commission", "spacex",
         "the new york times", "semafor", "axios", "the information"}

JUDGE_PROMPT = """너는 SpaceX(Space Exploration Technologies Corp., Nasdaq: SPCX) 감시 분류기다.
아래 '헤드라인'은 지난 48시간의 뉴스 제목이다. 기사 본문은 없다 — 제목이 말하는 것만 판단하고, 제목 밖의 사실을 지어내지 마라.
각 헤드라인 i 마다 판정하라:
- material: SpaceX 의 사업·재무·규제·지배구조·주식 공급(락업·내부자·증자)·핵심 경쟁 구도에 실질 영향이 있는 **새 사건**이면 true.
  의견·전망·주가 해설·목표가 기사·재탕·과거 사건 회고·홍보·일반 소개는 false.
- severity: "urgent"(사고·폭발·비행 중단·규제 제재·대형 소송·경영진 이탈·신용등급 하향·대규모 장애) |
  "major"(계약·자금 조달·합병 협상·규제 승인·경쟁사 출시·락업 해제·대량 매도) | "minor"
- category: "규제·안전" | "발사·Starship" | "Starlink" | "자금·신용" | "지배구조" | "주식·수급" | "정부 계약" | "경쟁" | "소송" | "기타"
- triggers: 영향받는 트리거 id 목록 — 아래 '트리거' 목록의 id 만 쓸 것(없으면 [])
- cluster: 같은 사건을 다루는 헤드라인끼리 같은 짧은 영어 라벨(소문자-하이픈, 예: "apollo-gpu-lease")
- already_known: 아래 '이미 기록된 사건'과 같은 사건이면 true
- claim_ko: 제목이 말하는 사실 한 문장(한국어). 제목에 없는 숫자·사실을 넣지 마라.
출력은 JSON 하나만: {"items":[{"i":0,"material":true,"severity":"major","category":"자금·신용","triggers":["T5"],
"cluster":"apollo-gpu-lease","already_known":false,"claim_ko":"..."}, ...]}
— **material 이 true 인 헤드라인만** items 에 넣어라. 넣지 않은 i 는 '중대하지 않음'으로 친다. 하나도 없으면 {"items":[]}."""


def now_kst():
    return datetime.now(KST)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# 순수 함수 — 테스트가 직접 두드린다
# ────────────────────────────────────────────────────────────────

def query_url(q):
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + "+" + WINDOW + "&hl=en-US&gl=US&ceid=US:en"


def clean_title(title, publisher):
    """구글뉴스 제목 꼬리(' - 발행처')를 뗀다."""
    t = (title or "").strip()
    p = (publisher or "").strip()
    if p and t.endswith(" - " + p):
        t = t[: -len(" - " + p)]
    return t.strip()


def headline_id(title):
    norm = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def is_tier1(publisher):
    return (publisher or "").strip().lower() in TIER1


def decide(headlines, verdicts, wakes_today, cooldown, today):
    """모델 판정 → 기계 결정. 반환 (깨울 묶음 목록, DM 만 할 묶음 목록, 무시 수).

    headlines: [{i, title, publisher, date, url}] · verdicts: 모델 items(i 로 연결)
    wakes_today: 오늘 이미 깨운 횟수 · cooldown: {cluster: 마지막 깨운 날짜}
    """
    by_i = {h["i"]: h for h in headlines}
    clusters = {}
    ignored = 0
    for v in verdicts or []:
        h = by_i.get(v.get("i"))
        if not h:
            continue
        sev = str(v.get("severity") or "minor")
        if not v.get("material") or v.get("already_known") or sev not in ("urgent", "major"):
            ignored += 1
            continue
        key = re.sub(r"[^a-z0-9-]+", "-", str(v.get("cluster") or h["id"]).lower()).strip("-")[:60] or h["id"]
        c = clusters.setdefault(key, {"cluster": key, "severity": "major", "items": [], "triggers": set(),
                                      "category": v.get("category") or "기타", "claim": v.get("claim_ko") or ""})
        if sev == "urgent":
            c["severity"] = "urgent"
        c["items"].append(h)
        c["triggers"].update(t for t in (v.get("triggers") or []) if isinstance(t, str))
    wake, dm_only = [], []
    n = wakes_today
    for c in sorted(clusters.values(), key=lambda c: (c["severity"] != "urgent", -len(c["items"]))):
        pubs = {(h.get("publisher") or "").strip().lower() for h in c["items"] if h.get("publisher")}
        c["publishers"] = sorted(pubs)
        c["corroborated"] = any(p in TIER1 for p in pubs) or len(pubs) >= 2
        c["triggers"] = sorted(c["triggers"])
        last = cooldown.get(c["cluster"])
        cooling = bool(last) and (datetime.strptime(today, "%Y-%m-%d") -
                                  datetime.strptime(last, "%Y-%m-%d")).days < CLUSTER_COOLDOWN_D
        if cooling:
            ignored += 1
            continue
        if c["corroborated"] and (c["severity"] == "urgent" or n < WAKE_CAP_PER_DAY):
            wake.append(c)
            if c["severity"] != "urgent":
                n += 1
        else:
            c["why_not"] = "발행처 1곳(보도 단계)" if not c["corroborated"] else "오늘 깨움 상한 도달"
            dm_only.append(c)
    return wake, dm_only, ignored


def news_message(wake, dm_only, judged, model_ok=True):
    lines = [("🚨" if any(c["severity"] == "urgent" for c in wake) else "📰")
             + f" SpaceX 뉴스 감시 — 엔진 깨움 {len(wake)} · 보도 단계 {len(dm_only)} (판정 {judged}건)"]
    for tag, rows in (("깨움", wake), ("보도 단계", dm_only)):
        for c in rows:
            sev = "긴급" if c["severity"] == "urgent" else "중요"
            trg = (" · " + "·".join(c["triggers"])) if c.get("triggers") else ""
            lines.append(f"· [{tag}·{sev}] {c['category']}{trg}: {c['claim']}"
                         + (f" ({c['why_not']})" if c.get("why_not") else ""))
            for h in c["items"][:3]:
                lines.append(f"   - {h['publisher'] or '?'} {h.get('date') or ''}: {h['title'][:110]}")
                if h.get("url"):
                    lines.append(f"     {h['url']}")
    if wake:
        lines.append("탐사 엔진 사건 모드 발사 대기열에 올렸습니다(커밋 뒤 발사). 보도는 판단 근거가 아니라 확인 대상입니다.")
    if not model_ok:
        lines.append("⚠️ 판정 모델 호출 실패 — 이번 회차 헤드라인은 다음 회차에 다시 판정합니다.")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# 수집 · 판정
# ────────────────────────────────────────────────────────────────

def collect(fc, logs):
    from fetch_signals import parse_rss
    from email.utils import parsedate_to_datetime
    ua = {"User-Agent": "Mozilla/5.0 (UNIVERTRIX spacex-news; univertrix.com)"}
    out = []
    for name, q in QUERIES:
        label = "SpaceX 뉴스:" + name
        text, oc, code = fc.fetch(query_url(q), label, "spacex_news", headers=ua)
        if text is None:
            logs.append(f"{label} 수집 실패({oc} {code})")
            continue
        rows = parse_rss(text, name)
        fc.record("spacex_news", label, "ok" if rows else "zero", code, len(rows), items_raw=len(rows))
        for r in rows:
            pub = r.get("feed_source") or ""
            try:
                d = parsedate_to_datetime(r.get("pub", "")).astimezone(KST).strftime("%Y-%m-%d")
            except Exception:
                d = ""
            t = clean_title(r.get("title"), pub)
            out.append({"id": headline_id(t), "title": t, "publisher": pub, "date": d,
                        "url": r.get("url") or "", "query": name})
    seen, uniq = set(), []
    for h in out:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        uniq.append(h)
    uniq.sort(key=lambda h: h.get("date") or "", reverse=True)
    return uniq


def order_fresh(fresh):
    """판정 순서 — 날짜 최신순, 같은 날 안에서는 질의(사건 유형)별로 번갈아. 상한에 걸려도 한 유형이 굶지 않게
    (질의 순서대로 쌓으면 뒤쪽 '경쟁'·'CEO 발언'이 늘 잘린다)."""
    by_day = {}
    for h in fresh:
        by_day.setdefault(h.get("date") or "", []).append(h)
    out = []
    for day in sorted(by_day, reverse=True):
        lanes = {}
        for h in by_day[day]:
            lanes.setdefault(h.get("query") or "", []).append(h)
        qs = list(lanes)
        while any(lanes[q] for q in qs):
            for q in qs:
                if lanes[q]:
                    out.append(lanes[q].pop(0))
    return out


def judge(api_key, headlines, triggers, known):
    import requests
    payload = {"헤드라인": [{"i": h["i"], "제목": h["title"], "발행처": h["publisher"], "날짜": h["date"]}
                         for h in headlines],
               "트리거": [{"id": t.get("id"), "이름": t.get("name")} for t in triggers if t.get("id")],
               "이미 기록된 사건": known[:60]}
    body = {"model": MODEL, "max_tokens": 6000,
            "messages": [{"role": "user", "content": JUDGE_PROMPT + "\n\n" + json.dumps(payload, ensure_ascii=False)}]}
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=240)
    r.raise_for_status()
    data = r.json()
    use = data.get("usage") or {}
    if data.get("stop_reason") == "max_tokens":
        raise ValueError("응답 잘림(max_tokens) — 묶음을 줄여야 한다")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```(?:json)?", "", text).strip()
    doc = json.loads(text[text.find("{"): text.rfind("}") + 1])
    items = doc.get("items")
    if not isinstance(items, list):
        raise ValueError("판정 응답에 items 목록이 없다")
    return items, use


def known_events(watch, state, today):
    """이미 기록된 사건(모델에 '재탕' 판정 근거로 준다) — 측정층 사건 14일 + 지난 뉴스 판정."""
    cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
    out = [f"{e.get('date')} {e.get('title')}" for e in (watch.get("events") or [])
           if (e.get("date") or "") >= cut]
    out += [f"{d.get('date')} {d.get('claim')}" for d in (state.get("decisions") or [])
            if (d.get("date") or "") >= cut and d.get("claim")]
    return out


def run():
    import feed_client as fc
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    logs = []
    state = read_json(NEWS_PATH, {}) or {}
    watch = read_json(WATCH_PATH, {}) or {}
    st = read_json(STATE_PATH, {}) or {}
    triggers = [t for t in (st.get("triggers_next_12m") or []) if (t.get("status") or "대기") == "대기"]

    heads = collect(fc, logs)
    seen = set(state.get("seen") or [])
    first = not seen
    fresh = order_fresh([h for h in heads if h["id"] not in seen])
    batch = fresh[:MAX_HEADLINES]
    for i, h in enumerate(batch):
        h["i"] = i
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    wake, dm_only, ignored, model_ok, use = [], [], 0, True, {}
    judged, calls, err = [], 0, None
    if first:
        logs.append(f"기준선 회차 — 헤드라인 {len(heads)}건을 판정 없이 적재(첫 회차에 몰아 울리지 않게)")
        seen.update(h["id"] for h in heads)
    elif batch and not api_key:
        logs.append("ANTHROPIC_API_KEY 없음 — 판정 생략(헤드라인은 다음 회차로 미룬다)")
        model_ok = None
    elif batch:
        verdicts = []
        known = known_events(watch, state, today)
        for k in range(0, len(batch), CHUNK):
            chunk = batch[k:k + CHUNK]
            try:
                v, u = judge(api_key, chunk, triggers, known)
            except Exception as e:
                model_ok = False
                err = f"{type(e).__name__}: {str(e)[:200]}"     # 원장에 남긴다 — 실행 로그를 못 볼 때도 원인이 보이게
                fc.record("spacex_news", "판정 모델", "http_error", None, 0)
                logs.append(f"판정 모델 실패 — {err}")
                break                                  # 나머지 묶음은 다음 회차에(seen 에 넣지 않는다)
            calls += 1
            verdicts += v
            judged += chunk
            for key in ("input_tokens", "output_tokens"):
                use[key] = (use.get(key) or 0) + int((u or {}).get(key) or 0)
        if judged:
            fc.record("spacex_news", "판정 모델", "ok" if model_ok else "http_error", 200, len(verdicts))
            wakes_today = (state.get("wakes") or {}).get(today, 0)
            wake, dm_only, ignored = decide(judged, verdicts, wakes_today, state.get("cooldown") or {}, today)
            ignored += max(0, len(judged) - len({v.get("i") for v in verdicts}))   # 생략 = 중대하지 않음
            seen.update(h["id"] for h in judged)       # 판정이 끝난 것만 seen — 실패하면 다음 회차에 다시
    fc.flush()

    # 이월 감시 — 처리 한도를 넘어 미판정이 2회 연속 남으면 DM(한 번) · 해소되면 한 번
    deferred = 0 if first else len(fresh) - len(judged)
    streak = int(state.get("deferred_streak") or 0)
    streak = streak + 1 if deferred > 0 else 0
    over_msg = None
    if streak == 2:
        over_msg = (f"⚠️ SpaceX 뉴스 감시관 — 처리 한도를 넘었습니다: 미판정 {deferred}건이 2회 연속 이월"
                    f"(회당 상한 {MAX_HEADLINES}). 2일 창을 벗어난 헤드라인은 판정 없이 사라집니다 — 상한을 올리거나 질의를 좁혀야 합니다.")
        state["deferred_alerted"] = today
    elif streak == 0 and state.get("deferred_alerted"):
        over_msg = "✅ SpaceX 뉴스 감시관 — 이월 해소(미판정 0건)"
        state.pop("deferred_alerted", None)
    state["deferred_streak"] = streak

    if wake or dm_only or model_ok is False or over_msg:
        try:
            from spacex_watch import send_dm
        except Exception:
            send_dm = None
        out = []
        if wake or dm_only or model_ok is False:
            out.append(news_message(wake, dm_only, len(judged), model_ok is not False))
        if over_msg:
            out.append(over_msg)
        for msg in out:
            if send_dm:
                send_dm(msg)
            else:
                print(msg)

    if wake:
        # 측정층 파일에 due·발사 대기열을 싣는다 — 커밋 뒤 'Dispatch SpaceX probe' 스텝이 쏜다
        q = watch.get("dispatch_queue") or []
        dues = []
        for c in wake:
            dues.append({"key": f"news:{c['cluster']}:{today}", "trigger_id": ",".join(c["triggers"]) or None,
                         "name": c["claim"] or c["cluster"], "reason": "보도 감지(" + "·".join(c["publishers"][:3]) + ")",
                         "event": c["items"][0]["title"], "event_date": c["items"][0].get("date"),
                         "url": c["items"][0].get("url"), "detected_at": today, "grade": c["severity"],
                         "source": "news"})
        watch["due"] = ((watch.get("due") or []) + dues)[-60:]
        q.append({"key": "qn:" + today + ":" + now.strftime("%H%M"), "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                  "queued_at": now.isoformat(timespec="minutes"), "state": "queued",
                  "trigger_id": ",".join(sorted({t for c in wake for t in c["triggers"]})),
                  "name": " · ".join((c["claim"] or c["cluster"])[:80] for c in wake)[:200],
                  "reason": "보도 감지 " + str(len(wake)) + "건(확인 대상 — 보도는 판단 근거가 아니다)",
                  "urls": [h.get("url") for c in wake for h in c["items"][:2] if h.get("url")][:5]})
        watch["dispatch_queue"] = q
        write_json(WATCH_PATH, watch)
        cd = state.get("cooldown") or {}
        for c in wake:
            cd[c["cluster"]] = today
        state["cooldown"] = {k: v for k, v in cd.items()
                             if (now.date() - datetime.strptime(v, "%Y-%m-%d").date()).days < 30}
        w = state.get("wakes") or {}
        w[today] = w.get(today, 0) + sum(1 for c in wake if c["severity"] != "urgent")
        state["wakes"] = {k: v for k, v in w.items() if k >= (now - timedelta(days=7)).strftime("%Y-%m-%d")}

    dec = state.get("decisions") or []
    for tag, rows in (("wake", wake), ("report", dm_only)):
        for c in rows:
            dec.append({"date": today, "action": tag, "cluster": c["cluster"], "severity": c["severity"],
                        "category": c["category"], "claim": c["claim"], "publishers": c["publishers"],
                        "triggers": c["triggers"], "titles": [h["title"] for h in c["items"][:3]],
                        "urls": [h.get("url") for h in c["items"][:3]]})
    state["decisions"] = dec[-80:]
    state["seen"] = sorted(seen)[-KEEP_SEEN:]
    state["last_run"] = {"date": today, "headlines": len(heads), "fresh": len(fresh), "judged": len(judged),
                         "deferred": deferred, "baseline": first, "calls": calls,
                         "wake": len(wake), "report": len(dm_only), "ignored": ignored,
                         "model": MODEL, "model_ok": model_ok, "error": err,
                         "tokens_in": use.get("input_tokens"), "tokens_out": use.get("output_tokens")}
    write_json(NEWS_PATH, state)
    print(f"[SpaceX 뉴스] 헤드라인 {len(heads)} · 새 {len(fresh)} · 판정 {len(judged)}(호출 {calls}) · 이월 {deferred} · "
          f"깨움 {len(wake)} · 보도 단계 {len(dm_only)} · 무시 {ignored}")
    for n in logs:
        print(f"[SpaceX 뉴스] 참고: {n}", file=sys.stderr)
    return 1 if model_ok is False else 0


def main():
    try:
        return run()
    except Exception:
        traceback.print_exc()
        print("[SpaceX 뉴스] 감시 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
