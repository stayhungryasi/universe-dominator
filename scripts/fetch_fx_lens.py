#!/usr/bin/env python3
"""
fetch_fx_lens.py — 관측 위치 렌즈 환율 (4개 언어 관측소 단계 4 · daily full, build 앞)
================================================================================
막으려는 것 한 문장: **화면이 브라우저에서 환율을 받아 오는 것**(클라이언트 fetch 금지 · 숫자는 빌드가 심는다).
그래서 환율은 full 회차가 한 번 받아 data/fx_lens.json 에 남기고, 빌드는 그 파일만 읽는다(quick 도 같은 값).

  원천: ECB 기준환율(frankfurter, 키 불필요) — 1 USD 당 EUR·MXN·KRW·JPY
  KRW·JPY 는 헤더 시장 지표(macro)가 1순위(사이트가 이미 보여 주는 값과 같아야 한다) — 여기 값은 폴백
  내용 속 시각: generated_at(수집 시각) · date(ECB 기준일) — mtime 으로 판정하지 않는다(CI 지뢰)
  원장: fetch_status.json kind="fx" — 요청 실패는 관제탑 judge_feeds 가 2회 연속에 운다
  실패하면 기존 파일을 그대로 둔다(낡은 값이 없는 값보다 낫다 — 화면은 기준일을 함께 적는다)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import feed_client as fc   # noqa: E402

KST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent.parent / "data" / "fx_lens.json"
URL = "https://api.frankfurter.dev/v1/latest?from=USD&to=EUR,MXN,KRW,JPY"
WANT = ("EUR", "MXN", "KRW", "JPY")


def parse(text):
    """응답 → {"date", "rates"} | None. 값이 양수인 통화만."""
    try:
        doc = json.loads(text or "")
    except ValueError:
        return None
    rates = {k: float(v) for k, v in (doc.get("rates") or {}).items()
             if k in WANT and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
    if not rates or not doc.get("date"):
        return None
    return {"date": str(doc["date"])[:10], "rates": rates}


def main():
    text, outcome, code = fc.fetch(URL, "ECB", "fx", timeout=15)
    got = parse(text) if outcome == "ok" else None
    if outcome == "ok":
        fc.record("fx", "ECB", "ok" if got else "decode_fail", code, len((got or {}).get("rates") or {}),
                  items_raw=1)
    fc.flush()
    if not got:
        print(f"[환율] 수집 실패({outcome}) — 기존 data/fx_lens.json 유지", file=sys.stderr)
        return 1
    doc = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "base": "USD",
           "date": got["date"], "rates": got["rates"], "source": "ECB (frankfurter)"}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[환율] ECB {got['date']} · " + " · ".join(f"{k} {v}" for k, v in sorted(got["rates"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
