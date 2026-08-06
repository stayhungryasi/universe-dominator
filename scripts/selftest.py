#!/usr/bin/env python3
"""
자가진단 (selftest) — 파이프라인 시작 시 핵심 로직 회귀 테스트
================================================================
목적: 코드가 조용히 망가진 채 데이터를 오염시키는 것을 원천 차단.
2026-07 AMD 누락 사고(clean_name이 이름=티커 기업을 소멸시킴) 이후 도입.

이 스크립트가 실패하면 파이프라인이 즉시 중단된다 (continue-on-error: false).
→ 잘못된 코드로는 단 하루치 데이터도 만들지 않는다.

새 버그를 수리할 때마다 그 사례를 여기에 케이스로 추가할 것.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

FAILS = []


def check(label, got, want):
    if got == want:
        print(f"  ✅ {label}")
    else:
        FAILS.append(label)
        print(f"  ❌ {label} — 결과 {got!r}, 기대 {want!r}", file=sys.stderr)


def main():
    print("[자가진단] 시작")
    from fetch_data import clean_name, parse_mc, check_rank_gaps

    # ── clean_name: 2026-07 AMD 누락 사고 재발 방지 ──
    check("clean_name: 이름=티커(AMD) 보존", clean_name("AMD", "AMD"), "AMD")
    check("clean_name: 이름=티커(ASML) 보존", clean_name("ASML", "ASML"), "ASML")
    check("clean_name: 이름=티커(HSBC) 보존", clean_name("HSBC", "HSBC"), "HSBC")
    check("clean_name: 이름=티커(SAP) 보존", clean_name("SAP", "SAP"), "SAP")
    check("clean_name: 붙은 티커 제거", clean_name("NVIDIANVDA", "NVDA"), "NVIDIA")
    check("clean_name: 한국형 접미 제거", clean_name("Samsung005930.KS", "005930.KS"), "Samsung")
    check("clean_name: 점 없는 변형", clean_name("AppleAAPL", "AAPL"), "Apple")
    check("clean_name: 한 글자 티커 미훼손(AT&T)", clean_name("AT&T", "T"), "AT&T")
    check("clean_name: 한 글자 티커 미훼손(Visa)", clean_name("Visa", "V"), "Visa")

    # ── parse_mc: 시총 단위 해석 ──
    check("parse_mc: 조 단위", parse_mc("$5.109 T"), 5109.0)
    check("parse_mc: 십억 단위", parse_mc("$909.69 B"), 909.69)

    # ── suffix_flag: 2026-07 DELTA.BK 태국 국기 누락 재발 방지 ──
    from generate_candidates import suffix_flag
    check("suffix_flag: 태국(.BK)", suffix_flag("DELTA.BK"), "🇹🇭")
    check("suffix_flag: 대만(.TW)", suffix_flag("2308.TW"), "🇹🇼")
    check("suffix_flag: 인도(.NS)", suffix_flag("RELIANCE.NS"), "🇮🇳")
    check("suffix_flag: 미국(무접미)", suffix_flag("NVDA"), "🇺🇸")

    # ── check_rank_gaps: 행 탈락 감시망 자체 검증 ──
    rows_ok = [{"_rank": i} for i in range(1, 21)]
    check("rank_gaps: 정상(구멍 없음)", check_rank_gaps(rows_ok, "test"), [])
    rows_gap = [{"_rank": i} for i in (1, 2, 3, 5, 6)]
    got = check_rank_gaps(rows_gap, "test")
    check("rank_gaps: 4위 탈락 감지", len(got) == 1 and "[4]" in got[0], True)

    # ── load_watch: 2026-08 PLTR 실적 누락 사고 재발 방지 ──
    # 지역 TOP 20에 못 드는 글로벌 21~100위(watch100)가 감시 풀에 들어가는지.
    import json as _json
    import tempfile
    import fetch_calendar as _fc
    fake = {
        "regions": {"earth": {"stocks": [{"ticker": "NVDA", "name": "NVIDIA"}]}},
        "latent": [{"ticker": "INTC", "name": "Intel"}],
        "watch100": [
            {"rank": 25, "ticker": "PLTR", "name": "Palantir"},
            {"rank": 40, "ticker": "005930.KS", "name": "삼성전자"},  # 점 티커는 제외돼야
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(_json.dumps(fake, ensure_ascii=False))
        tmp_path = Path(tf.name)
    orig_latest = _fc.LATEST_PATH
    try:
        _fc.LATEST_PATH = tmp_path
        w = _fc.load_watch()
    finally:
        _fc.LATEST_PATH = orig_latest
        tmp_path.unlink(missing_ok=True)
    check("load_watch: watch100의 PLTR 편입", "PLTR" in w, True)
    check("load_watch: 지역·잠재 유지", "NVDA" in w and "INTC" in w, True)
    check("load_watch: 점 티커 제외 유지", "005930.KS" not in w, True)

    if FAILS:
        print(f"[자가진단] ❌ 실패 {len(FAILS)}건 — 수집을 중단합니다: {FAILS}",
              file=sys.stderr)
        sys.exit(1)
    print("[자가진단] ✅ 전체 통과 — 수집을 시작합니다")


if __name__ == "__main__":
    main()
