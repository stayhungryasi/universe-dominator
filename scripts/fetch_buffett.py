#!/usr/bin/env python3
"""
시차 관측 (fetch_buffett) — 버핏 멀티플 괴리 일일 측정층
================================================================
판단층은 data/buffett_config.json 이다. 이 스크립트는 **판단을 만들지 않는다.**
정당 멀티플(fair_max)·유형·근거는 사람이 취재해 넣은 값이고, 여기서는
그 판단에 오늘의 시세를 곱해 '괴리'만 잰다. config는 절대 재생성하지 않는다.

측정:
  괴리 = 정당 MAX ÷ 현재 P/E − 1     (양수 = 정당선 아래, 음수 = 정당선 위)
  P/E  = 현재가 ÷ EPS
         · config의 forward_eps 가 있으면 그것       → basis "forward"
         · 없으면 Finnhub 후행 P/E 실측              → basis "trailing"
         · Finnhub이 못 주면(주로 비미국 티커)        → basis "미취재", 괴리 null

원칙상 측정하지 않는 바구니 (낙제가 아니라 원칙):
  추정불가 → "too_hard" · 비상장 → "unlisted" · 플로트형 → "float"

출력: data/buffett.json
  { generated_label, asof, zones, items:[...], history:{ticker:[{d,gap}...]} }
  history 는 티커별 하루 1점, 90일 보관, 같은 날 재실행은 덮어쓰기(멱등).

원칙: 개별 티커가 실패해도 전체는 계속한다 (우아한 저하).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
CFG_PATH = DATA_DIR / "buffett_config.json"
OUT_PATH = DATA_DIR / "buffett.json"
KST = timezone(timedelta(hours=9))

HISTORY_DAYS = 90
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 유형이 곧 '측정하지 않음'의 사유가 되는 바구니
TYPE_BASIS = {"추정불가": "too_hard", "비상장": "unlisted", "플로트형": "float"}

# Finnhub /stock/metric 의 후행 P/E 키 — 플랜·종목에 따라 주는 키가 달라
# 앞에서부터 실제로 값이 있는 첫 키를 채택한다 (어느 키를 썼는지 로그로 남긴다)
PE_KEYS = ["peBasicExclExtraTTM", "peExclExtraTTM", "peTTM", "peInclExtraTTM",
           "peNormalizedAnnual", "peBasicExclExtraAnnual", "peExclExtraAnnual"]


def fetch_price(ticker, tries=3):
    """Yahoo v8 chart — 전 거래소 공용(.KS/.T/.TW/.SR 포함). (가격, 통화)"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if isinstance(price, (int, float)) and price > 0:
                return float(price), meta.get("currency") or ""
        except Exception:
            pass
        if attempt < tries - 1:
            time.sleep(1.2 * (attempt + 1))
    return None, ""


def fetch_trailing_pe(ticker, key, used_key_box):
    """Finnhub 후행 P/E — 무료 플랜은 사실상 미국 상장분만 준다"""
    url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={key}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        time.sleep(1.1)          # 무료 60 call/min 준수 (fetch_calendar.py 와 같은 보폭)
        if r.status_code != 200:
            return None
        metric = (r.json() or {}).get("metric") or {}
    except Exception:
        return None
    for k in PE_KEYS:
        v = metric.get(k)
        if isinstance(v, (int, float)) and v > 0:
            if not used_key_box:
                used_key_box.append(k)
            return float(v)
    return None


def load_history():
    if not OUT_PATH.exists():
        return {}
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8")).get("history") or {}
    except Exception:
        return {}


def append_history(history, ticker, day, gap):
    """하루 1점 · 같은 날은 덮어쓰기(멱등) · 90일 보관"""
    if gap is None:
        return
    series = [p for p in history.get(ticker, []) if p.get("d") != day]
    series.append({"d": day, "gap": round(gap, 4)})
    series.sort(key=lambda p: p["d"])
    history[ticker] = series[-HISTORY_DAYS:]


def main():
    if not CFG_PATH.exists():
        print("[시차] buffett_config.json 없음 — 판단층이 있어야 측정한다", file=sys.stderr)
        return 1
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    items_cfg = cfg.get("items", [])
    fh_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not fh_key:
        print("[시차] FINNHUB_API_KEY 없음 — 후행 P/E 폴백 생략 (forward_eps 있는 종목만 측정)")

    now = datetime.now(KST)
    day = now.strftime("%m-%d")
    history = load_history()
    used_key_box = []
    out_items = []
    n_measured = n_skipped = n_unpriced = 0

    for c in items_cfg:
        ticker = c.get("ticker", "")
        row = {"ticker": ticker, "name": c.get("name", ""), "type": c.get("type", ""),
               "fair_max": c.get("fair_max"), "rationale": c.get("rationale", ""),
               "eps_asof": c.get("eps_asof"), "price": None, "currency": "",
               "pe": None, "gap": None, "basis": "미취재", "note": ""}

        # ① 원칙상 재지 않는 바구니 — 시세도 부르지 않는다
        basis = TYPE_BASIS.get(c.get("type"))
        if basis:
            row["basis"] = basis
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 측정 제외 ({c.get('type')}) — 원칙 바구니")
            continue

        if row["fair_max"] is None:
            row["note"] = "정당 MAX 미지정"
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 정당 MAX 없음 — 건너뜀")
            continue

        # ② 시세
        price, currency = fetch_price(ticker)
        row["price"], row["currency"] = price, currency
        if price is None:
            row["note"] = "시세 취득 실패"
            out_items.append(row)
            n_unpriced += 1
            print(f"[시차] {ticker}: 시세 실패 — 괴리 미산출", file=sys.stderr)
            continue

        # ③ P/E — forward 우선, 없으면 후행 폴백
        pe = None
        fwd_eps = c.get("forward_eps")
        if isinstance(fwd_eps, (int, float)) and fwd_eps > 0:
            pe = price / float(fwd_eps)
            row["basis"] = "forward"
        elif fh_key:
            tpe = fetch_trailing_pe(ticker, fh_key, used_key_box)
            if tpe:
                pe = tpe
                row["basis"] = "trailing"

        if pe is None or pe <= 0:
            row["note"] = "EPS 취재 전 — 괴리 미산출"
            out_items.append(row)
            n_skipped += 1
            print(f"[시차] {ticker}: 미취재 (P/E 없음)")
            continue

        gap = float(row["fair_max"]) / pe - 1.0
        row["pe"] = round(pe, 2)
        row["gap"] = round(gap, 4)
        out_items.append(row)
        append_history(history, ticker, day, gap)
        n_measured += 1
        print(f"[시차] {ticker}: P/E {row['pe']} ({row['basis']}) "
              f"· 정당 {row['fair_max']} → 괴리 {gap * 100:+.1f}%")
        time.sleep(0.2)

    # 측정 못 한 티커의 과거 흔적은 남기되, config에서 빠진 티커는 정리
    live = {c.get("ticker") for c in items_cfg}
    history = {k: v for k, v in history.items() if k in live}

    payload = {
        "generated_label": now.strftime("%Y-%m-%d %H:%M"),
        "asof": cfg.get("asof", ""),
        "zones": cfg.get("zones", {"explore": 0.60, "commit": 0.40}),
        "items": out_items,
        "history": history,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if used_key_box:
        print(f"[시차] Finnhub 후행 P/E 채택 키: {used_key_box[0]}")
    print(f"[시차] 측정 {n_measured}종 / 원칙·미취재 제외 {n_skipped}종 / "
          f"시세실패 {n_unpriced}종 (전체 {len(items_cfg)}종)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
