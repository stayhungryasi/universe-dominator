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

    # ── 2026-08 관측노트 누락 사고 재발 방지: 페이지 목록 단일화 검증 ──
    # 사고 원인: 주입 대상 페이지 목록이 build_site.py 5개 함수에 각각 하드코딩돼
    # 있어 새 페이지가 일부 주입에서만 누락됐다. ALL_PAGES 단일 상수로 통합한 뒤,
    # 그 단일화가 (ⓐ 상수 존재 ⓑ 전 주입 함수가 이 상수만 사용 ⓒ 실제 페이지와
    # 일치) 유지되는지 여기서 매 실행 검증한다.
    import inspect as _inspect
    import re as _re
    import build_site as _bs

    _INJECTORS = ("fix_nav", "inject_footer_links", "inject_presence",
                  "inject_header_fix", "inject_aurora_tokens")

    check("ALL_PAGES: 상수 존재", isinstance(getattr(_bs, "ALL_PAGES", None), tuple), True)
    check("ALL_PAGES: 중복 없음",
          sorted(p for p in set(_bs.ALL_PAGES)
                 if list(_bs.ALL_PAGES).count(p) > 1), [])
    check("ALL_PAGES: .html 파일명만",
          sorted(p for p in _bs.ALL_PAGES if not p.endswith(".html")), [])

    # ⓑ 5개 주입 함수 전부가 ALL_PAGES 를 쓰는가 (지역 목록 부활 차단)
    _not_using = []
    _has_local = []
    for _name in _INJECTORS:
        _fn = getattr(_bs, _name, None)
        if _fn is None:
            _not_using.append(f"{_name}(없음)")
            continue
        _src = _inspect.getsource(_fn)
        if "ALL_PAGES" not in _src:
            _not_using.append(_name)
        # 함수 안에서 .html 문자열을 목록처럼 나열하면 지역 목록 부활로 간주
        if _re.search(r"pages\s*=\s*\[", _src):
            _has_local.append(_name)
    check("주입 함수 전부 ALL_PAGES 사용", _not_using, [])
    check("주입 함수 내 지역 페이지 목록 없음", _has_local, [])

    # ⓒ 실제 루트 HTML 과 목록이 일치하는가 (새 페이지 등록 누락 즉시 탐지)
    _root = Path(__file__).parent.parent
    _on_disk = {f.name for f in _root.glob("*.html")} - set(_bs.UNMANAGED_PAGES)
    _missing = sorted(_on_disk - set(_bs.ALL_PAGES))   # 파일은 있는데 목록에 없음
    _ghost = sorted(set(_bs.ALL_PAGES) - _on_disk)     # 목록에 있는데 파일이 없음
    check("ALL_PAGES: 미등록 페이지 없음 (파일↔목록)", _missing, [])
    check("ALL_PAGES: 유령 항목 없음 (목록↔파일)", _ghost, [])

    # ── 2026-08 f-string 문법 사고 재발 방지: 전 스크립트 컴파일 전수검사 ──
    # (러너 파이썬을 3.12로 고정해 검증 환경과 일치시키고, 여기서 전 스크립트를
    #  실제 컴파일해 어떤 문법 오류든 수집 단계 진입 전에 차단한다)
    import py_compile as _pyc
    _bad = []
    for _f in sorted(Path(__file__).parent.glob("*.py")):
        try:
            _pyc.compile(str(_f), doraise=True)
        except Exception as _e:
            _bad.append(f"{_f.name}: {str(_e)[:60]}")
    check("전 스크립트 컴파일 (문법 전수검사)", _bad, [])

    if FAILS:
        print(f"[자가진단] ❌ 실패 {len(FAILS)}건 — 수집을 중단합니다: {FAILS}",
              file=sys.stderr)
        sys.exit(1)
    print("[자가진단] ✅ 전체 통과 — 수집을 시작합니다")


if __name__ == "__main__":
    main()
