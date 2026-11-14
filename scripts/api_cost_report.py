#!/usr/bin/env python3
"""
API 비용 일일 보고 (2026-11-14 ㉗) — 계량기(api_meter)가 쌓은 원장을 읽어 하루 한 번 소장 DM 으로 알린다.
11월은 13일 뒤에 Console 배너로 알았다. 이제는 매일 아침 '어제 얼마 · 이번 달 얼마 · 이 속도면 얼마'가 온다.

규칙:
  · 하루 1회 — 08시(KST) 이후 첫 full 회차. 보냈으면 원장 `report.last_dm` 에 날짜를 찍는다.
    DM 이 실패하면 찍지 않는다 — 다음 회차가 다시 보낸다(침묵은 통과가 아니다)
  · 경보 줄(같은 DM 머리에 ⚠️): 이 속도면 월 예산(API_BUDGET_USD, 기본 200 = 포함 크레딧) 초과 ·
    어제 어떤 스크립트가 직전 7일 중앙값의 3배 넘게 씀(그리고 $1 이상) · 호출 실패율 20% 초과(버려지는 돈) ·
    단가표에 없는 모델(비용 미산정)
  · 범위를 정직하게: 이 저장소 데일리 스크립트만 센다. 탐사·자가 수리(구독)와 다른 저장소의 봇은 여기 없다
  · 수신처는 TELEGRAM_ALERT_CHAT_ID(소장 DM) 전용 — 미등록이면 로그로만(공개 폴백 없음)
실행: python scripts/api_cost_report.py
"""
import calendar
import os
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import api_meter as am   # noqa: E402

REPORT_HOUR = 8
SPIKE_X, SPIKE_MIN_USD, FAIL_RATE = 3.0, 1.0, 0.20


def day_total(day):
    return round(sum((r or {}).get("usd", 0.0) for r in (day or {}).values()), 4)


def month_to_date(days, now):
    pre = now.strftime("%Y-%m-")
    return round(sum(day_total(v) for d, v in days.items() if d.startswith(pre)), 4)


def projection(days, now):
    """이번 달 누계를 '기록이 있는 날 수'로 나눠 한 달로 편다 — 계량 첫 달의 빈 날이 평균을 낮추지 않게."""
    pre = now.strftime("%Y-%m-")
    seen = [d for d in days if d.startswith(pre) and d < now.strftime("%Y-%m-%d") and day_total(days[d]) > 0]
    if not seen:
        return None
    per_day = sum(day_total(days[d]) for d in seen) / len(seen)
    return round(per_day * calendar.monthrange(now.year, now.month)[1], 2)


def alerts(days, now, budget):
    out = []
    yk = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    y = days.get(yk) or {}
    proj = projection(days, now)
    if proj is not None and proj > budget:
        out.append(f"이 속도면 이번 달 ${proj:.0f} — 예산 ${budget:.0f} 초과")
    for name, row in sorted(y.items()):
        hist = [((days.get((now - timedelta(days=i)).strftime("%Y-%m-%d")) or {}).get(name) or {}).get("usd", 0.0)
                for i in range(2, 9)]
        hist = [h for h in hist if h > 0]
        usd = row.get("usd", 0.0)
        if len(hist) >= 3 and usd >= SPIKE_MIN_USD and usd > SPIKE_X * statistics.median(hist):
            out.append(f"{name} 어제 ${usd:.2f} — 직전 7일 중앙값 ${statistics.median(hist):.2f} 의 {usd / statistics.median(hist):.1f}배")
        calls, fail = row.get("calls", 0), row.get("fail", 0)
        if calls >= 5 and fail / calls > FAIL_RATE:
            out.append(f"{name} 호출 실패 {fail}/{calls} — 버려지는 호출")
        if row.get("unpriced"):
            out.append(f"{name} 단가표에 없는 모델 {row['unpriced']}건 — 비용 미산정(api_meter.PRICES 에 추가할 것)")
    return out


def build_message(days, now, budget):
    yk = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    y = days.get(yk) or {}
    mtd, proj = month_to_date(days, now), projection(days, now)
    al = alerts(days, now, budget)
    lines = [("⚠️ " if al else "") + f"💳 API 비용 일일 보고 — {yk}"]
    lines += ["⚠️ " + a for a in al]
    lines.append(f"어제 ${day_total(y):.2f} · 이번 달 ${mtd:.2f}"
                 + (f" · 이 속도면 월 ${proj:.0f} / 예산 ${budget:.0f}" if proj is not None else " · 월 전망은 내일부터"))
    top = sorted(y.items(), key=lambda kv: -kv[1].get("usd", 0.0))[:5]
    for name, row in top:
        lines.append(f"· {name} ${row.get('usd', 0.0):.2f} — 호출 {row.get('calls', 0)}"
                     + (f"(실패 {row['fail']})" if row.get("fail") else "")
                     + f" · 입력 {row.get('in', 0) + row.get('cr', 0) + row.get('cw', 0):,} · 출력 {row.get('out', 0):,}")
    if not y:
        lines.append("· 어제 기록 없음(계량 시작 전이거나 full 회차가 돌지 않았다)")
    lines.append("범위: 이 저장소 데일리 스크립트. 탐사·자가 수리(구독)와 다른 저장소 봇은 제외.")
    return "\n".join(lines)


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("[비용 보고] DM 수신처 미등록 — 발송 생략(공개 폴백 없음)")
        return False
    from send_telegram_briefing import send_telegram, esc
    send_telegram(token, chat, esc(text))
    return True


def run(path=None, now=None):
    path = am.LEDGER if path is None else path
    now = now or am.now_kst()
    try:
        budget = float(os.environ.get("API_BUDGET_USD") or 200)
    except ValueError:
        budget = 200.0
    doc = am._load(path)
    text = build_message(doc["days"], now, budget)
    print(text)
    today = now.strftime("%Y-%m-%d")
    rep = doc.setdefault("report", {})
    if rep.get("last_dm") == today:
        print("[비용 보고] 오늘 이미 보냄")
        return 0
    if now.hour < REPORT_HOUR:
        print(f"[비용 보고] {REPORT_HOUR}시 전 — 이번 회차는 보내지 않는다")
        return 0
    try:
        sent = send(text)
    except Exception as e:
        print(f"[비용 보고] DM 발송 실패 — 날짜를 찍지 않는다(다음 회차가 다시 보낸다): {e}", file=sys.stderr)
        return 1
    if sent:
        rep["last_dm"] = today
        am._save(path, doc)
    return 0


if __name__ == "__main__":
    sys.exit(run())
