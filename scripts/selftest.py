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

    # ── 동행 관측: 판단층/기계층 분리와 침묵 규율 ──
    # 이 엔진의 가장 중요한 성질은 '쓰지 않는 것'이다. 논제 없는 회사를 집필하거나
    # 인용 규율을 어기면 노트가 스크랩으로 전락하므로 매 실행 방어선을 확인한다.
    import companion_essays as _ce
    check("동행: 3사 논제 원장 존재",
          sorted(p.name for p in (Path(__file__).parent.parent / "data" / "thesis").glob("*.md")),
          ["alphabet.md", "anthropic.md", "spacex.md"])
    # 원장 v1 반영(2026-08-22) 이후: 3사가 잠긴 채로 배포되면 엔진이 영영 침묵한다.
    # 머리글 형식(인용줄)을 파서가 못 읽는 순간이 곧 조용한 정지이므로 매 실행 확인한다.
    check("동행: 3사 논제 v1 활성 (placeholder 아님)",
          [_ce.read_thesis(s)["placeholder"] for s in ("spacex", "alphabet", "anthropic")],
          [False, False, False])
    check("동행: 논제 머리글 버전·갱신일 파싱",
          sorted({(_ce.read_thesis(s)["version"], _ce.read_thesis(s)["updated"])
                  for s in ("spacex", "alphabet", "anthropic")}),
          [("1.0", "2026-08-22")])
    check("동행: 에세이 헌법 프롬프트 존재 (5축 규율 포함)",
          "5축" in _ce.load_prompt() or "① 실적" in _ce.load_prompt(), True)
    check("동행: 슬러그 멱등",
          _ce.make_slug("spacex", "제목", "2026-01-02"),
          _ce.make_slug("spacex", "제목", "2026-01-02"))
    check("동행: verdict 안전 폴백",
          _ce.build_entry("spacex", "t", "이상값", "<p>x</p>", [], "auto", "2026-01-02")["verdict"],
          "판단 보류")
    check("동행: anthropic 만 이해관계 고지",
          ["이해관계 고지" in _ce.tail_html("anthropic", []),
           "이해관계 고지" in _ce.tail_html("spacex", [])], [True, False])
    check("동행: 인용 규율 — 정상 통과", _ce.check_quotes('<p>"short quote"</p>', 1), [])
    check("동행: 인용 규율 — 긴 인용 적발",
          len(_ce.check_quotes('<p>"' + " ".join(["w"] * 20) + '"</p>', 1)) > 0, True)
    check("동행: 직접 게재는 하루 1편 상한 밖",
          _ce.published_today([{"company": "spacex", "date": "2026-01-02", "origin": "직접"}],
                              "spacex", "2026-01-02"), False)

    # ── 피드 공용 클라이언트: 레이트 리밋 방어 (2026-08-22 동행 7소스 503 사고) ──
    # 한 번 던지고 포기하면 우아한 저하가 '조용히 비어가는' 것으로 끝난다.
    import feed_client as _fc
    check("피드: 503 은 재시도 대상", 503 in _fc.RETRY_CODES and 429 in _fc.RETRY_CODES, True)
    check("피드: 백오프가 커지는가", list(_fc.BACKOFF) == sorted(_fc.BACKOFF)
          and len(_fc.BACKOFF) >= 3, True)
    check("피드: 같은 호스트 최소 간격", _fc.MIN_GAP >= 1.0, True)
    _mix = [{"url": "https://news.google.com/1"}, {"url": "https://news.google.com/2"},
            {"url": "https://openai.com/a"}, {"url": "https://deepmind.google/b"}]
    _h = [_fc._host(x["url"]) for x in _fc.interleave_by_host(_mix)]
    check("피드: 같은 호스트 연타 없음",
          sum(1 for i in range(1, len(_h)) if _h[i] == _h[i - 1]), 0)

    # ── 보안 규칙 정본(firestore.rules)과 클라이언트 설정의 adminUid 일치 ──
    # 두 값이 어긋나면 소장이 자기 소재함에서 잠긴다. 콘솔에 붙여넣기 전에
    # 여기서 걸러야 "게시했는데 안 된다"를 겪지 않는다.
    _root = Path(__file__).parent.parent
    _rules_path = _root / "firestore.rules"
    check("규칙: firestore.rules 존재", _rules_path.exists(), True)
    if _rules_path.exists():
        _rules = _rules_path.read_text(encoding="utf-8")
        _admin = _json.loads((_root / "data" / "firebase_config.json")
                             .read_text(encoding="utf-8")).get("adminUid", "")
        check("규칙: adminUid 가 firebase_config 와 일치", bool(_admin) and _admin in _rules, True)
        check("규칙: materials 절 존재 (v8)", "match /materials/" in _rules, True)
        # 소재함이 실수로 공개되면 소장의 미발행 판단이 새어 나간다
        check("규칙: materials 는 소장 전용",
              "allow read: if isAdmin();" in _rules, True)
        # 클라이언트가 실제로 쓰는 컬렉션이 규칙에 다 들어 있는가 (누락 = 기능 정지)
        _need = ["posts", "users", "images", "judgments", "presence", "materials"]
        check("규칙: 사용 중인 컬렉션 전부 등재",
              [c for c in _need if f"match /{c}/" not in _rules], [])

    # ── 거인의 어깨: 동일 발행사 복수 클래스 표기 (2026-08-22 수리) ──
    # 사고: 버크셔 카드에 "Alphabet Inc" 가 두 줄로 똑같이 찍혔다(A주/C주).
    # 합산 병합은 금지 — 클래스별 증감(+45% vs +658%)이 매수 패턴 정보다.
    from fetch_gurus import class_label as _cl, label_holdings as _lh
    check("gurus: CL A 판별", _cl("CAP STK CL A"), "Class A")
    check("gurus: CLASS B 판별", _cl("CLASS B"), "Class B")
    check("gurus: 상투어는 판별 불가", [_cl(t) for t in ("COM", "SHS", "PAR $.01", "")],
          [None, None, None, None])
    check("gurus: ETF 신탁은 종목명이 클래스 필드에", _cl("RUSSELL 2000 ETF"), "Russell 2000 Etf")
    _lab = lambda rows: [e["label"] for e in _lh(
        [{"base": b, "mark": m, "title": t} for b, m, t in rows])]
    check("gurus: 알파벳 A/C 구분",
          _lab([("Alphabet Inc", "", "CAP STK CL A"), ("Alphabet Inc", "", "CAP STK CL C")]),
          ["Alphabet Inc (Class A)", "Alphabet Inc (Class C)"])
    check("gurus: 중복 아니면 접미 없음",
          _lab([("Apple Inc", "", "COM"), ("Alphabet Inc", "", "CAP STK CL A")]),
          ["Apple Inc", "Alphabet Inc"])
    check("gurus: 판별 불가 폴백 + 순번",
          _lab([("Foo Inc", "", "COM"), ("Foo Inc", "", "COM")]),
          ["Foo Inc (별도 클래스)", "Foo Inc (별도 클래스 2)"])
    check("gurus: PUT 표기는 클래스 뒤에",
          _lab([("Spdr Tr", " (PUT)", "ENERGY SELECT SECTOR"),
                ("Spdr Tr", " (PUT)", "FINANCIAL SELECT SECT")]),
          ["Spdr Tr (Energy Select Sector) (PUT)", "Spdr Tr (Financial Select Sect) (PUT)"])
    check("gurus: PUT/보통주는 원래 다른 줄 — 접미 없음",
          _lab([("Ishares Tr", "", "COM"), ("Ishares Tr", " (PUT)", "COM")]),
          ["Ishares Tr", "Ishares Tr (PUT)"])

    # ── 정비 관제탑(pipeline_sentinel) 판정 로직 — 침묵 실패 감시망의 자체 검증 ──
    # 경보가 '울려야 할 때만' 울리는지. 순수 함수만 부르므로 부작용·네트워크 없음.
    # (전 케이스는 scripts/test_sentinel.py — 여기엔 회귀 핵심만 둔다)
    import pipeline_sentinel as _ps
    _st = {"sources": {}, "buffett": [], "sent": {}}
    _names = ["srcA"]
    _sig = lambda n, day: [{"source": "srcA", "captured": f"{day} 10:00"} for _ in range(n)]
    _ps.judge_signals(_sig(3, "2026-01-01"), _names, _st, "2026-01-01", _ps.DEFAULTS)  # 기준선
    _a1, _ = _ps.judge_signals([], _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 0건 1회는 침묵", _a1, [])
    _a2, _ = _ps.judge_signals([], _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 0건 2회 연속 경보", len(_a2) == 1 and _a2[0]["kind"] == "alert", True)
    _a3, _ = _ps.judge_signals(_sig(2, "2026-01-02"), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 회복 알림 1회", len(_a3) == 1 and _a3[0]["kind"] == "recover", True)
    _a4, _ = _ps.judge_signals(_sig(2, "2026-01-02"), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 회복 후 재침묵", _a4, [])
    _bst = {"buffett": []}
    _mk = lambda m, f: {"items": [{"pe": 1, "basis": "forward"}] * f
                        + [{"pe": 1, "basis": "trailing"}] * (m - f)}
    _ps.judge_buffett(_mk(21, 13), _bst, "2026-01-01", _ps.DEFAULTS)
    _ba, _ = _ps.judge_buffett(_mk(13, 9), _bst, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: buffett 급감·선행 유실 감지", len(_ba), 2)
    check("sentinel: 서명 형식", _ps.sig("buffett", "2026-01-02"), "sentinel:buffett:2026-01-02")
    # 발송 경로 배선 — sentinel 은 발송부를 자체 구현하지 않고 모닝브리핑 경로를
    # 재사용한다. 그 배선이 살아 있는지 매 실행 확인한다(실제 발송은 하지 않음).
    import send_telegram_briefing as _tg
    _captured = []
    _orig_send = _tg.send_telegram
    try:
        _tg.send_telegram = lambda tok, chat, txt: _captured.append((tok, chat, txt))
        _ps.send_telegram("tok", "@chan", "소스 <A & B> 0건")
    finally:
        _tg.send_telegram = _orig_send
    check("sentinel: 발송부는 브리핑 경로 재사용", len(_captured), 1)
    check("sentinel: HTML parse_mode용 이스케이프",
          _captured[0][2] if _captured else "", "소스 &lt;A &amp; B&gt; 0건")

    check("sentinel: 경보 5건 상한",
          _ps.format_message([_ps.alert(f"x{i}", f"항목{i}", "2026-01-02") for i in range(8)],
                             __import__("datetime").datetime(2026, 1, 2)).endswith("· 외 3건"), True)

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
