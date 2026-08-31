#!/usr/bin/env python3
"""
레전드벤치마크 AI 취재 (buffett_scout) — 정성 칸을 근거와 함께 채운다
================================================================================
숫자(eps_adj_ttm·ROE·g)는 fetch_buffett_auto 가 공시에서 직접 잰다. 여기서 채우는
것은 사람이 읽어야 알 수 있던 칸이다: franchise 3조건 · risk5 · capalloc 5항목 ·
owner_earnings A/B/C 근거 · notes.

산출물은 buffett_auto.json 의 같은 종목 블록에 **병합**된다(숫자 칸은 건드리지 않는다).
사람 판단층(buffett_config.json)은 여기서도 읽지도 쓰지도 않는다.

세 가지 규율:

  ① 근거 없는 항목은 null — 추측 금지.
     ○△✕ 는 세 글자지만 판단이다. 모델이 "그럴듯해서" 고른 기호는 판단이 아니라
     장식이다. 그래서 모든 항목에 evidence(원문 근거 문장)를 요구하고, evidence 가
     비면 그 항목을 통째로 버린다. 판정만 오고 근거가 없으면 그건 환각이다.

  ② 평시엔 침묵 — 동행 관측과 같은 밀도 원칙.
     분기가 바뀌었거나(신규 실적) 사양서 §6 이벤트 키워드가 잡힐 때만 재취재한다.
     매일 돌면 매일 조금씩 다른 ○△✕ 가 나와 눈금이 흔들린다.

  ③ 값이 바뀌어도 관측노트에 적지 않는다 — **눈금 변경은 시장 사건이 아니다.**
     (2026-08-17 AMZN 사고의 뿌리와 같다: 자가 바뀐 것을 시장이 움직였다고 적으면 안 된다)
     대신 텔레그램에 한 줄 남겨 운영자가 눈금이 흔들린 사실을 알게 한다.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
sys.path.insert(0, str(Path(__file__).parent))

CFG_PATH = DATA_DIR / "buffett_config.json"
AUTO_PATH = DATA_DIR / "buffett_auto.json"
STATE_PATH = DATA_DIR / "buffett_scout_state.json"
KST = timezone(timedelta(hours=9))

MODEL = "claude-haiku-4-5"
MAX_PER_RUN = 6          # 한 회차 취재 상한 — 토큰·시간 폭주 방지

# 사양서 §6 이벤트 키워드 — 하나라도 잡히면 분기 중이라도 재취재
EVENT_KEYWORDS = ["buyback", "offering", "convertible", "senior notes",
                  "capex guidance", "CEO", "compensation", "13F Berkshire"]

# AI 가 채우는 칸 — 숫자 칸은 여기 없다(그건 공시가 잰다)
QUAL_FIELDS = ["franchise", "risk5", "capalloc", "owner_earnings", "notes"]

MARK = ("○", "△", "✕")


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")


# ────────────────────────────────────────────────────────────────
# 판정부 — 순수 함수
# ────────────────────────────────────────────────────────────────

def needs_scout(ticker, auto_block, state, headlines):
    """재취재해야 하는가 → (여부, 사유). 평시엔 False 여야 한다."""
    prev = (state.get("items") or {}).get(ticker) or {}
    period = (auto_block or {}).get("period")
    if not prev.get("scouted_at"):
        return True, "첫 취재"
    if period and period != prev.get("period"):
        return True, f"신규 분기 {period}"
    hit = [k for k in EVENT_KEYWORDS
           if any(k.lower() in (h or "").lower() for h in (headlines or []))]
    if hit:
        return True, "이벤트 " + ", ".join(hit[:3])
    return False, ""


def clean_marks(d, keys):
    """○△✕ 만 남긴다. 모델이 다른 글자를 보내면 그 칸은 없는 것으로 친다."""
    if not isinstance(d, dict):
        return None
    out = {k: d.get(k) for k in keys if d.get(k) in MARK}
    return out or None


def sanitize(raw):
    """모델 응답 → 판단층에 실을 수 있는 블록. **근거 없는 항목은 버린다.**

    evidence 가 비어 있으면 그 항목 전체를 null 로 만든다 — 판정만 있고 근거가
    없는 것은 취재가 아니라 추측이다(규율 ①).
    """
    if not isinstance(raw, dict):
        return {}, []
    out, kept = {}, []
    ev = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}

    def has_ev(field):
        s = ev.get(field)
        return isinstance(s, str) and len(s.strip()) >= 10

    fr = clean_marks(raw.get("franchise"), ["need", "no_substitute", "no_price_reg"])
    if fr and has_ev("franchise"):
        out["franchise"] = fr
        kept.append("franchise")

    r5 = clean_marks(raw.get("risk5"), ["business_certainty", "mgmt_ability",
                                        "mgmt_fidelity", "price", "tax_inflation"])
    if r5 and has_ev("risk5"):
        out["risk5"] = r5
        kept.append("risk5")

    ca = raw.get("capalloc")
    if isinstance(ca, dict) and has_ev("capalloc"):
        # score 는 모델이 보낸 값을 믿지 않고 **여기서 다시 센다** — 합이 안 맞는
        # 점수는 표에서 가장 눈에 안 띄는 거짓말이다
        flags = {k: int(v) for k, v in ca.items()
                 if k not in ("period", "score")
                 and isinstance(v, (int, bool)) and int(v) in (0, 1)}
        if flags:
            flags["score"] = sum(flags.values())
            if ca.get("period"):
                flags["period"] = str(ca["period"])[:16]
            out["capalloc"] = flags
            kept.append("capalloc")

    oe = raw.get("owner_earnings")
    if isinstance(oe, dict) and oe.get("display") in ("A", "B", "C") and has_ev("owner_earnings"):
        out["owner_earnings"] = oe
        kept.append("owner_earnings")

    nt = raw.get("notes")
    if isinstance(nt, str) and nt.strip():
        out["notes"] = nt.strip()[:300]
        kept.append("notes")

    out["confidence"] = "하"          # AI 추출은 사람 취재보다 낮다 — 항상 명시
    out["_evidence"] = {k: ev.get(k) for k in kept if ev.get(k)}
    return out, kept


def changed_fields(before, after):
    """무엇이 바뀌었나 — 텔레그램 한 줄에 쓸 목록."""
    out = []
    for f in QUAL_FIELDS:
        if json.dumps(before.get(f), ensure_ascii=False, sort_keys=True) != \
           json.dumps(after.get(f), ensure_ascii=False, sort_keys=True):
            out.append(f)
    return out


# ────────────────────────────────────────────────────────────────
# 수집·질의
# ────────────────────────────────────────────────────────────────

def fetch_headlines(name, ticker):
    """구글뉴스 — feed_client 로 페이싱·원장 공유. 실패하면 빈 목록(침묵)."""
    try:
        import feed_client
        from urllib.parse import quote
        q = quote(f"{name} {ticker} earnings OR buyback OR offering")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        text, outcome, code = feed_client.fetch(url, f"scout:{ticker}", "scout")
        titles = []
        if text:
            titles = re.findall(r"<title>(.*?)</title>", text, re.S)[1:25]
        feed_client.record("scout", ticker, outcome if not titles else "ok", code, len(titles))
        return [re.sub(r"<[^>]+>", "", t) for t in titles] if titles else []
    except Exception as e:
        print(f"[취재] {ticker} 헤드라인 실패 ({type(e).__name__}: {e})", file=sys.stderr)
        return []


def ask(api_key, c, headlines):
    """Haiku 질의 — 근거 문장을 반드시 함께 요구한다."""
    lines = "\n".join(f"- {h}" for h in headlines[:20]) or "(최근 헤드라인 없음)"
    schema = (
        '{"franchise": {"need": "○|△|✕", "no_substitute": "○|△|✕", "no_price_reg": "○|△|✕"},'
        ' "risk5": {"business_certainty": "○|△|✕", "mgmt_ability": "○|△|✕",'
        ' "mgmt_fidelity": "○|△|✕", "price": "○|△|✕", "tax_inflation": "○|△|✕"},'
        ' "capalloc": {"period": "2026H1", "cash_positive": 0|1, "buyback": 0|1,'
        ' "no_dilution": 0|1, "debt_discipline": 0|1},'
        ' "owner_earnings": {"display": "A|B|C", "display_reason": "한 문장"},'
        ' "notes": "한 문장",'
        ' "evidence": {"franchise": "근거 문장", "risk5": "근거 문장",'
        ' "capalloc": "근거 문장", "owner_earnings": "근거 문장"}}')
    prompt = (
        f"너는 버핏 주주서한의 눈금으로 기업을 평가하는 관측자다.\n"
        f"대상: {c.get('name')} ({c.get('ticker')}) · 유형 {c.get('type')}\n"
        f"참고 판단: {c.get('rationale')}\n\n"
        f"최근 헤드라인:\n{lines}\n\n"
        "아래 스키마의 JSON만 출력하라(설명·코드펜스 금지).\n"
        "**가장 중요한 규칙: 근거가 없는 항목은 그 칸을 통째로 빼라.** "
        "evidence 에 실제 근거 문장을 쓸 수 없는 항목은 추측해서 채우지 말고 생략하라. "
        "모르면 비우는 것이 이 관측소의 규율이다.\n"
        f"스키마: {schema}")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    text = re.sub(r"```(?:json)?", "", text).strip()
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def notify(changes):
    """텔레그램 한 줄 — 관측노트에는 적지 않는다(눈금 변경 ≠ 시장 사건)."""
    if not changes:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[취재] TELEGRAM_BOT_TOKEN 없음 — 갱신 알림 생략", file=sys.stderr)
        return
    try:
        from send_telegram_briefing import send_telegram, esc
        chat = (os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
                or os.environ.get("TELEGRAM_CHAT_ID", "").strip() or "@stayhungryasi")
        for tk, fields in changes[:5]:
            send_telegram(token, chat, esc(f"{tk} 자동 취재 갱신: {', '.join(fields)}"))
        print(f"[취재] 갱신 알림 {min(len(changes), 5)}건 발송")
    except Exception as e:
        print(f"[취재] 알림 실패 ({e})", file=sys.stderr)


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[취재] ANTHROPIC_API_KEY 없음 — 건너뜀")
        return 0
    cfg = load_json(CFG_PATH, {})
    auto = load_json(AUTO_PATH, {"items": {}})
    items = auto.get("items") or {}
    state = load_json(STATE_PATH, {"items": {}})
    state.setdefault("items", {})
    now = datetime.now(KST)

    changes, done, skipped = [], 0, 0
    for c in cfg.get("items", []):
        tk = c.get("ticker", "")
        if not tk or done >= MAX_PER_RUN:
            continue
        block = items.get(tk) or {}
        heads = []
        prev = state["items"].get(tk) or {}
        # 헤드라인은 '분기가 안 바뀐' 종목에만 필요하다 — 첫 취재·신규 분기는 이미 확정
        if prev.get("scouted_at") and (block.get("period") == prev.get("period")):
            heads = fetch_headlines(c.get("name", ""), tk)
        go, why = needs_scout(tk, block, state, heads)
        if not go:
            skipped += 1
            continue
        try:
            raw = ask(api_key, c, heads)
        except Exception as e:
            print(f"[취재] {tk} 질의 실패 → 건너뜀 ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        clean, kept = sanitize(raw)
        if not kept:
            print(f"[취재] {tk}: 근거 있는 항목 0 — 아무것도 쓰지 않는다 ({why})")
            state["items"][tk] = {"scouted_at": now.strftime("%Y-%m-%d %H:%M"),
                                  "period": block.get("period")}
            done += 1
            continue
        diff = changed_fields(block, clean)
        block.update(clean)
        items[tk] = block
        state["items"][tk] = {"scouted_at": now.strftime("%Y-%m-%d %H:%M"),
                              "period": block.get("period")}
        if diff and prev.get("scouted_at"):
            changes.append((tk, diff))
        done += 1
        print(f"[취재] {tk}: {', '.join(kept)} ({why})")

    auto["items"] = items
    auto["scout_label"] = now.strftime("%Y-%m-%d %H:%M")
    AUTO_PATH.write_text(json.dumps(auto, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    save_state(state)
    try:
        import feed_client
        feed_client.flush()
    except Exception:
        pass
    notify(changes)
    print(f"[취재] 취재 {done}종 · 평시 침묵 {skipped}종 · 갱신 알림 {len(changes)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
