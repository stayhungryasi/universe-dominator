#!/usr/bin/env python3
"""
XBRL 원천 진단 (diag_xbrl_bs) — 원천(Finnhub financials-reported) 응답을 눈으로 본다
================================================================================
2026-10-22 원천 대조 F3·F7 용. **수집 파이프라인이 부르지 않는다** — 사람이 로컬에서 돌린다.

  python scripts/diag_xbrl_bs.py AMZN MSFT NVDA CRWD          # 분기 보고 최신 4건
  python scripts/diag_xbrl_bs.py CRWD --n 12 --annual         # 12건 + 연간 보고

출력(보고 한 건당 한 줄):
  연·분기 라벨 / 양식 / 기간(시작~종료) / 제출일 / 대차대조표 줄 수 / 'Equity' 포함 항목
  → F3: 최신 보고에 대차대조표 구역이 비었나(줄 0) · 항목 이름이 바뀌었나(Equity 항목 다름)
  → F7: 1월 결산 회사의 '연도' 라벨이 회계연도인가 달력연도인가(라벨과 기간을 나란히 본다)

FINNHUB_API_KEY 가 없으면 아무것도 부르지 않고 그 사실만 말하고 끝난다(종료코드 2).
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import fetch_buffett_auto as fa   # noqa: E402  같은 진단 줄(bs_diag)을 쓴다 — 두 벌 금지


def fetch(ticker, key, freq):
    url = ("https://finnhub.io/api/v1/stock/financials-reported"
           f"?symbol={ticker}&freq={freq}&token={key}")
    r = requests.get(url, headers=fa.UA, timeout=25)
    time.sleep(1.1)                                   # 무료 60 call/min
    if r.status_code != 200:
        return None, r.status_code
    return (r.json() or {}).get("data") or [], 200


def main():
    ap = argparse.ArgumentParser(description="Finnhub XBRL 원천 진단")
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--n", type=int, default=4, help="보고 몇 건을 볼지(최신순)")
    ap.add_argument("--annual", action="store_true", help="연간 보고도 함께")
    a = ap.parse_args()
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        print("[진단] FINNHUB_API_KEY 없음 — 원천을 부르지 않았다. 원인 미확증.", file=sys.stderr)
        return 2
    for t in a.tickers:
        q, code = fetch(t, key, "quarterly")
        if q is None:
            print(f"== {t}: 분기 HTTP {code}")
            continue
        ann = []
        if a.annual:
            ann, _ = fetch(t, key, "annual")
        print(f"== {t}: 분기 {len(q)}건" + (f" · 연간 {len(ann or [])}건" if a.annual else ""))
        print(f"   응답 순서 그대로 앞 {a.n}건(reports[0..]):")
        for i, r in enumerate(q[:a.n]):
            print(f"   [{i}] " + fa.bs_diag([r])[0])
        print(f"   종료일 최신순(분기+연간) {a.n}건:")
        for line in fa.bs_diag(fa.recent_reports(q, ann or [], n=a.n)):
            print("   · " + line)
        found = fa.equity_scan(q, ann or [])
        print(f"   → 자본 채택: {found['label'] + ' 종료 ' + found['end'] if found else '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
