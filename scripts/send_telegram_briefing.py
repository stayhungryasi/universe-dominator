#!/usr/bin/env python3
"""
텔레그램 모닝브리핑 봇 (send_telegram_briefing)
================================================================
매일 첫 파이프라인 실행에서 채널로 아침 브리핑을 발사한다.
텔레그램 = 푸시, 사이트 = 홈 — 모든 링크는 univertrix.com으로 향한다.

브리핑 구성:
  👑 오늘의 태양계 (지구 TOP 5 + 전일 증감)
  🌌 우주 변동 (왕좌 교체·진입/탈락·잠재 변동 — 관제탑 감지 엔진 재사용)
  ✦ 잠재지배자 모멘텀 TOP 3
  📅 다가오는 주요일정 (D-2 이내)
  💱 USD/KRW

원칙:
  - 하루 1회만 (data/briefing_state.json), 이후 실행은 침묵
  - 토큰 미설정이면 조용히 건너뜀 (파이프라인 무해)
  - 실패해도 파이프라인 계속

인증(GitHub Secrets):
  TELEGRAM_BOT_TOKEN  — BotFather에서 발급한 봇 토큰
  TELEGRAM_CHAT_ID    — 채널 주소 (미설정 시 @stayhungryasi)
"""
import html
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SNAP_DIR = DATA_DIR / "snapshots"
STATE_PATH = DATA_DIR / "briefing_state.json"
KST = timezone(timedelta(hours=9))
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

SITE = "univertrix.com"


def esc(s):
    return html.escape(str(s or ""), quote=False)


def fmt_mc(mc):
    return f"${mc/1000:.2f}T" if mc >= 1000 else f"${mc:.0f}B"


def load_prev_earth(today_str):
    """어제 스냅샷의 지구 리스트 (전일 시총·순위 비교용)"""
    if not SNAP_DIR.exists():
        return []
    for s in sorted(SNAP_DIR.glob("*.json"), reverse=True):
        if today_str in s.name:
            continue
        try:
            prev = json.loads(s.read_text(encoding="utf-8"))
            rows = prev.get("regions", {}).get("earth", [])
            return rows if isinstance(rows, list) else rows.get("stocks", [])
        except Exception:
            continue
    return []


def build_briefing():
    latest = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    meta = latest.get("meta", {})
    today = meta.get("fetched_date", "")
    now = datetime.now(KST)
    head_date = f"{now.month:02d}.{now.day:02d} ({WEEKDAY_KO[now.weekday()]})"

    lines = [f"🛰️ <b>UNIVERTRIX 모닝브리핑</b> — {head_date}", ""]

    # ── 👑 우주지배자 TOP 5 (순위 변동 + 전일 증감 + 왕좌 격차) ──
    earth = latest["regions"]["earth"]["stocks"]
    prev_earth = load_prev_earth(today)
    prev_mc = {r["ticker"]: r["mc"] for r in prev_earth if r.get("ticker") and r.get("mc")}
    prev_rank = {r["ticker"]: i for i, r in enumerate(prev_earth, 1) if r.get("ticker")}
    lines.append("👑 <b>우주지배자 TOP 5</b>")
    for i, s in enumerate(earth[:5], 1):
        pr = prev_rank.get(s["ticker"])
        move = ""
        if pr and pr != i:
            move = f" (↑{pr - i})" if pr > i else f" (↓{i - pr})"
        chg = ""
        pm = prev_mc.get(s["ticker"])
        if pm:
            pct = (s["mc"] - pm) / pm * 100
            if abs(pct) >= 0.05:
                arrow = "🔺" if pct > 0 else "🔻"
                chg = f" {arrow}{abs(pct):.1f}%"
        lines.append(f"{i}. {esc(s['name'])}{move} {fmt_mc(s['mc'])}{chg}")
    # 왕좌 격차 내러티브
    if len(earth) >= 2:
        gap = earth[0]["mc"] - earth[1]["mc"]
        trend = ""
        if (earth[0]["ticker"] in prev_mc and earth[1]["ticker"] in prev_mc):
            prev_gap = prev_mc[earth[0]["ticker"]] - prev_mc[earth[1]["ticker"]]
            if abs(gap - prev_gap) >= 5:
                trend = " — 어제보다 좁혀짐 🔥" if gap < prev_gap else " — 어제보다 벌어짐"
        lines.append(f"왕좌 격차: {fmt_mc(gap)}{trend}")
    lines.append("")

    # ── 🔥 오늘의 별 (TOP 20 시총 증감 최상·최하) ──
    movers = []
    for s in earth:
        pm = prev_mc.get(s["ticker"])
        if pm:
            movers.append((s, (s["mc"] - pm) / pm * 100))
    if movers:
        hot = max(movers, key=lambda x: x[1])
        cold = min(movers, key=lambda x: x[1])
        if hot[1] >= 0.3 or cold[1] <= -0.3:
            parts = []
            if hot[1] >= 0.3:
                parts.append(f"가장 뜨거운 별 {esc(hot[0]['name'])} 🔺{hot[1]:.1f}%")
            if cold[1] <= -0.3:
                parts.append(f"가장 식은 별 {esc(cold[0]['name'])} 🔻{abs(cold[1]):.1f}%")
            lines.append("🔥 <b>오늘의 별</b> (TOP 20)")
            lines.append(" · ".join(parts))
            lines.append("")

    # ── 🇰🇷 한국 지배자 (지구 순위) ──
    kr = [(i, s) for i, s in enumerate(earth, 1) if str(s.get("ticker", "")).endswith(".KS")]
    if kr:
        lines.append("🇰🇷 " + " · ".join(
            f"{esc(s['name'])} 지구 {i}위" for i, s in kr[:2]))
        lines.append("")

    # ── 🌌 우주 변동 (관제탑 감지 엔진 재사용) ──
    try:
        from post_community_notice import detect_events
        _, events = detect_events()
        # 관측일지는 브리핑에선 제외(사이트 소식 위주), 시장 변동만
        market_events = [m for k, m in events if k in ("throne", "earth20", "latent")]
    except Exception:
        market_events = []
    lines.append("🌌 <b>우주 변동</b>")
    if market_events:
        lines += [esc(m) for m in market_events]
    else:
        lines.append("지구 궤도 평온 — 왕좌와 상위권 이상 무")
    lines.append("")

    # ── ✦ 잠재지배자 모멘텀 TOP 3 ──
    latent = sorted(latest.get("latent", []),
                    key=lambda x: -(x.get("momentum_1y") or 0))[:3]
    if latent:
        lines.append("✦ <b>잠재지배자 모멘텀 TOP 3</b>")
        lines.append(" · ".join(
            f"{esc(x['name'])} +{x['momentum_1y']}%" for x in latent))
        lines.append("")

    # ── 📅 다가오는 주요일정 (D-2 이내, 최대 4건) ──
    cal_path = DATA_DIR / "calendar.json"
    if cal_path.exists():
        try:
            events = json.loads(cal_path.read_text(encoding="utf-8")).get("events", [])
            upcoming = []
            for e in events:
                try:
                    d = datetime.strptime(e["date"], "%Y-%m-%d").date()
                except Exception:
                    continue
                dday = (d - now.date()).days
                if 0 <= dday <= 2:
                    upcoming.append((dday, e["title"]))
            upcoming.sort()
            if upcoming:
                lines.append("📅 <b>다가오는 관측 포인트</b>")
                for dday, title in upcoming[:4]:
                    lines.append(f"D-{dday} {esc(title)}")
                lines.append("")
        except Exception:
            pass

    # ── 💱 환율 ──
    usd_krw = meta.get("usd_krw")
    if isinstance(usd_krw, (int, float)):
        lines.append(f"💱 USD/KRW {usd_krw:,.2f}")
        lines.append("")

    lines.append(f"🔭 오늘의 우주 전체 보기 → {SITE}")
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API 오류: {data}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[브리핑] TELEGRAM_BOT_TOKEN 미설정 — 건너뜀")
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or "@stayhungryasi"

    today = datetime.now(KST).strftime("%Y-%m-%d")
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if state.get("last_sent") == today:
        print(f"[브리핑] 오늘({today}) 이미 발사 완료 — 침묵")
        return

    text = build_briefing()
    if len(text) > 4000:  # 텔레그램 한도 4096 여유
        text = text[:3990] + "…"
    try:
        send_telegram(token, chat_id, text)
    except Exception as e:
        print(f"[브리핑] 발사 실패: {e}", file=sys.stderr)
        sys.exit(0)  # 상태 미기록 → 다음 실행에서 재시도

    STATE_PATH.write_text(json.dumps({"last_sent": today}, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[브리핑] 발사 완료 → {chat_id} ({len(text)}자)")


if __name__ == "__main__":
    main()
