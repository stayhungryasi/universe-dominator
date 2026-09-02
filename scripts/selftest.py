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
    # 인용 규율(원문 대조) — 2026-08-22 오탐 사고 재발 방지.
    # 표면 특징으로 검사하면 한국어 강조 표기가 인용으로 오인돼 정상 글이 막힌다.
    _src_ko = ("회사는 재사용이 발사 비용을 낮추는 핵심이라고 밝혔으며 이번 분기 매출은 "
               "43억 달러로 전체의 55퍼센트를 차지했다고 설명했다")
    check("동행: 인용 규율 — 원문에 없는 강조 표기는 통과",
          _ce.check_quotes('<p>이른바 "검색 사망론"은 아직 실적에 없다</p>', _src_ko, 1), [])
    check("동행: 인용 규율 — 원문 복제는 적발",
          len(_ce.check_quotes("<p>" + _src_ko + "</p>", _src_ko, 3)) > 0, True)
    check("동행: 인용 규율 — 수집 텍스트 없으면 판정 없음",
          _ce.check_quotes('<p>"무엇이든"</p>', "", 1), [])
    check("동행: 길이 기준이 언어별",
          (_ce.EN_WORD_LIMIT, _ce.KO_CHAR_LIMIT), (15, 60))
    # 모델 꼬리 제거 — 제목 변형에 취약했던 정규식(2026-08-23). 잘라낼 것과
    # 지킬 것을 함께 본다: 느슨하면 분석 절을 먹고, 빡빡하면 꼬리가 새어 나간다.
    _cut = ["<h3>출처</h3><ul><li>a</li></ul>", "<h3>참고 자료</h3><p>a</p>",
            "<h3>Sources</h3><p>a</p>", "<h4>출처:</h4><p>a</p>",
            "<p><strong>면책:</strong> 권유 아님</p>"]
    check("동행: 꼬리 제목 변형 전부 제거",
          [_ce.strip_model_tail("<p>본문</p>" + t) for t in _cut],
          ["<p>본문</p>"] * len(_cut))
    _keep = "<p>본문</p><h3>출처의 신뢰도는 어떠한가</h3><p>분석</p>"
    check("동행: 분석 절은 살아남는다 (오탐 방지)", _ce.strip_model_tail(_keep), _keep)
    check("동행: 직접 게재는 하루 1편 상한 밖",
          _ce.published_today([{"company": "spacex", "date": "2026-01-02", "origin": "직접"}],
                              "spacex", "2026-01-02"), False)

    # ── 피드 공용 클라이언트: 레이트 리밋 방어 (2026-08-22 동행 7소스 503 사고) ──
    import pipeline_sentinel as _ps_early
    _ps_feeds = lambda status, st: _ps_early.judge_feeds(
        status, st, "2026-01-02", dict(_ps_early.DEFAULTS, feed_error_limit=2))
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
    # 원장이 있어야 '조용한 날'과 '죽은 소스'가 갈린다 — 사각지대가 닫혔는지 매 실행 확인
    _fst = {"sources": {"companion:x": {"kind": "companion", "source": "x",
                                        "outcome": "http_error", "code": 503}}}
    _fs = {"feeds": {}}
    _f1, _ = _ps_feeds(_fst, _fs)
    _f2, _ = _ps_feeds(_fst, _fs)
    check("피드: 요청 실패 1회 침묵 · 2회 경보", [_f1, len(_f2)], [[], 1])
    _fz = {"sources": {"companion:y": {"kind": "companion", "source": "y",
                                       "outcome": "zero", "code": 200}}}
    _fs2 = {"feeds": {}}
    check("피드: 0건은 실패보다 관대(즉시 경보 아님)", _ps_feeds(_fz, _fs2)[0], [])

    # ── 주입 지점 오염 방지 (2026-08-23 사고) ──
    # 주입 함수는 전부 html.replace("</head>", CSS, 1) 로 **첫 번째** </head> 를 바꾼다.
    # 템플릿 본문·주석에 그 문자열이 한 번이라도 더 있으면 주입 블록이 거기로 끼어들어
    # <style> 한복판을 갈라 놓는다 — 실제로 CSS 절반이 파싱되지 않는 사고가 났다.
    _root = Path(__file__).parent.parent
    _bad_head = []
    for _t in sorted(Path(__file__).parent.glob("*-template.html")):
        _txt = _t.read_text(encoding="utf-8")
        if _txt.count("</head>") != 1:
            _bad_head.append(f"{_t.name}({_txt.count('</head>')}회)")
    check("템플릿에 </head> 는 정확히 1회", _bad_head, [])
    _bad_built = []
    for _name in ("journal.html", "observatory.html", "research.html", "community.html"):
        _f = _root / _name
        if not _f.exists():
            continue
        _h = _f.read_text(encoding="utf-8")
        _head = _h[:_h.find("</head>")]
        # 첫 <style> 블록 안에 또 다른 <style> 이 끼어들었는가 = 주입 지점 오염
        _i = _head.find("<style")
        if _i >= 0:
            _seg = _head[_i:_head.find("</style>") if "</style>" in _head else len(_head)]
            if "<style" in _seg[6:]:
                _bad_built.append(_name)
    check("산출물 <style> 블록 오염 없음", _bad_built, [])

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

    # ── 레전드벤치마크(fetch_buffett) — 판단층·측정층 분리 검증 ──────────────
    # 이 절의 존재 이유는 하나다: **기존 괴리 값이 1원도 바뀌지 않는 것.**
    # 새 판단층(buffett 블록)이 기존 정당MAX 괴리 경로에 한 방울도 섞이면 안 된다.
    import json as _json
    import fetch_buffett as _fb
    _DATA = Path(__file__).parent.parent / "data"

    # ① 회귀 핵심 — buffett 블록이 있든 없든 괴리·P/E·zoned 가 완전히 동일해야 한다
    _base = {"ticker": "AAPL", "type": "씨즈형", "fair_max": 28, "forward_eps": 9.1}
    _rich = dict(_base, buffett={
        "as_of": "2026-08-30", "period": "2026Q2", "cyclical_peak_guard": False,
        # 분기 EPS 2.85 는 forward_eps 9.1 과 자릿수가 다르다 — 섞이면 괴리가 폭주한다
        "eps_adj": {"value": 2.85, "unit": "USD/qtr"},
        "eps_adj_ttm": {"value": 11.4}, "roe_tangible": 0.35,
        "g_cagr3y": 0.08, "g_forward": 0.06})
    check("벤치: buffett 블록은 괴리 경로에 안 섞인다",
          _fb.measure_gap(_rich, 200.0, None), _fb.measure_gap(_base, 200.0, None))
    check("벤치: 괴리 값 자체도 종전 그대로",
          _fb.measure_gap(_rich, 200.0, None)[:4], (21.98, 0.274, "forward", True))

    # 실제 판단층 전 종목으로 같은 대조 — 한 종목이라도 어긋나면 즉시 실패
    _cfg_all = _json.loads((_DATA / "buffett_config.json").read_text(encoding="utf-8"))
    _mismatch = []
    for _it in _cfg_all.get("items", []):
        _stripped = {k: v for k, v in _it.items() if k != "buffett"}
        if _fb.measure_gap(_it, 137.0, 25.0) != _fb.measure_gap(_stripped, 137.0, 25.0):
            _mismatch.append(_it.get("ticker"))
    check("벤치: 실제 34종 전부 괴리 불변(블록 유무 대조)", _mismatch, [])

    # ② null 은 null 로 — 0 치환 금지
    check("벤치: g 한쪽만 있으면 null", _fb.pick_g(0.08, None), None)
    check("벤치: g 는 둘 중 작은 쪽", _fb.pick_g(0.08, 0.06), 0.06)
    check("벤치: ey 는 EPS 없으면 null", _fb.earnings_yield(None, 200.0), None)
    check("벤치: coupon 은 g 없으면 null(성장 0 가정 금지)",
          _fb.coupon_10y(0.05, None), None)

    # ③ 존 경계 — 10y 4.0% 기준 3× = 12%, 1.5× = 6%
    check("벤치: 3배 이상은 pass", _fb.zone_of_buffett(0.12, 4.0), "pass")
    check("벤치: 1.5~3배는 prove_growth", _fb.zone_of_buffett(0.119, 4.0), "prove_growth")
    check("벤치: 1.5배 미만은 bond_inferior", _fb.zone_of_buffett(0.059, 4.0), "bond_inferior")
    check("벤치: coupon 없으면 untested", _fb.zone_of_buffett(None, 4.0), "untested")
    check("벤치: 금리 없으면 untested", _fb.zone_of_buffett(0.12, None), "untested")

    # ④ 분기 EPS 연환산 금지 — GOOG 는 eps_adj 가 있어도 TTM 이 없으면 untested
    _goog = next(i for i in _cfg_all["items"] if i["ticker"] == "GOOG")
    _gb = _fb.measure_bench(_goog, 200.0, {"UST10": 4.0})
    check("벤치: GOOG 는 untested (분기 EPS 연환산 금지)", _gb["zone_buffett"], "untested")
    check("벤치: GOOG coupon10y 는 null", _gb["coupon10y"], None)
    check("벤치: GOOG ey 도 null (2.85×4 를 쓰지 않는다)", _gb["ey"], None)

    # ⑤ 시클리컬 가드 — coupon10y·zone_buffett 을 아예 산출하지 않는다
    _cyc = {"ticker": "MU", "type": "시클리컬",
            "buffett": {"cyclical_peak_guard": True, "eps_adj_ttm": {"value": 10.0},
                        "g_cagr3y": 0.05, "g_forward": 0.05}}
    _cb = _fb.measure_bench(_cyc, 100.0, {"UST10": 4.0})
    check("벤치: 가드는 coupon10y 미산출", _cb["coupon10y"], None)
    check("벤치: 가드도 존은 untested (못 잰 것은 한 칸에)", _cb["zone_buffett"], "untested")
    check("벤치: 가드 사유가 note 에 남는다", "정점 가드" in _cb["note"], True)
    # 가드가 없었다면 pass 였을 값이라는 것까지 확인 — 가드가 진짜로 막고 있는가
    _cyc_off = {"ticker": "MU", "type": "시클리컬",
                "buffett": {"cyclical_peak_guard": False, "eps_adj_ttm": {"value": 10.0},
                            "g_cagr3y": 0.05, "g_forward": 0.05}}
    check("벤치: 가드를 끄면 실제로 판정된다(가드가 일하고 있다는 증거)",
          _fb.measure_bench(_cyc_off, 100.0, {"UST10": 4.0})["zone_buffett"], "pass")

    # ⑥ 시장별 10년물 — v1 은 미국만. 미배선 시장은 0 으로 채우지 않는다
    check("벤치: .KS 는 한국물", _fb.market_of("005930.KS"), "KTB10")
    check("벤치: 접미사 없으면 미국", _fb.market_of("AAPL"), "UST10")
    _kr = {"ticker": "005930.KS", "type": "씨즈형",
           "buffett": {"eps_adj_ttm": {"value": 5000}, "g_cagr3y": 0.05, "g_forward": 0.05}}
    _kb = _fb.measure_bench(_kr, 70000.0, {"UST10": 4.0, "KTB10": None})
    check("벤치: 미배선 시장은 untested", _kb["zone_buffett"], "untested")
    check("벤치: 미배선 금리는 null (0 아님)", _kb["rate10y"], None)

    # ⑦ FRED CSV 파싱 — 주말·공휴일의 '.' 은 건너뛰고 마지막 실측치를 쓴다
    check("벤치: FRED '.' 공백 건너뜀",
          _fb.parse_fred_csv("observation_date,DGS10\n2026-08-28,4.21\n2026-08-29,.\n"),
          ("2026-08-28", 4.21))

    _tcsv = ('Date,"1 Mo","10 Yr","30 Yr"' + chr(10)
             + "08/28/2026,3.84,4.73,5.22" + chr(10)
             + "08/27/2026,3.85,4.70,5.20" + chr(10))
    check("벤치: Treasury CSV 는 최신 행이 맨 위",
          _fb.parse_treasury_csv(_tcsv), ("08/28/2026", 4.73))
    check("벤치: Treasury CSV 에 10Yr 열이 없으면 null",
          _fb.parse_treasury_csv('Date,"1 Mo"' + chr(10) + "08/28/2026,3.84" + chr(10)), None)

    # ⑧ cause — 잰 자가 바뀌면 scale (관측노트가 이걸 보고 침묵한다)
    _prev = {"eps_adj_ttm": 10.0, "g_used": 0.05, "guard": False,
             "price": 100.0, "rate10y": 4.0}
    check("벤치: EPS 취재가 바뀌면 scale",
          _fb.classify_cause(_prev, dict(_prev, eps_adj_ttm=11.0)), "scale")
    check("벤치: 주가만 움직이면 price",
          _fb.classify_cause(_prev, dict(_prev, price=120.0)), "price")
    check("벤치: 금리만 움직이면 rate",
          _fb.classify_cause(_prev, dict(_prev, rate10y=4.8)), "rate")
    check("벤치: 첫 관측은 원인 없음", _fb.classify_cause(None, _prev), None)

    # ⑨ 히스토리 — 점 집합은 종전 그대로(gap 이 있을 때만), 칸만 얹는다
    _h = {}
    _fb.append_history(_h, "AAPL", "08-30", None, {"coupon10y": 0.1})
    check("벤치: gap 없으면 점을 만들지 않는다(종전 규약)", _h, {})
    _fb.append_history(_h, "AAPL", "08-30", 0.27, {"coupon10y": 0.1, "zone_buffett": "pass"})
    check("벤치: 점에 벤치 칸이 얹힌다",
          _h["AAPL"][-1], {"d": "08-30", "gap": 0.27, "coupon10y": 0.1, "zone_buffett": "pass"})

    # ── 판단층 병합(buffett_layers) — 기계가 채우고 사람이 덮어쓴다 ──────────
    import buffett_layers as _bl

    _human = {"eps_adj_ttm": {"value": None, "note": "미취재"},   # 자리표시자 = 없는 값
              "roe_tangible": None,
              "risk5": {"business_certainty": "✕"},              # 사람이 취재한 칸
              "cyclical_peak_guard": False}                       # False 는 값이다
    _auto = {"eps_adj_ttm": {"value": 9.2}, "roe_tangible": 0.31,
             "risk5": {"business_certainty": "○"}, "cyclical_peak_guard": True,
             "g_cagr3y": 0.08}
    _m, _o = _bl.merge_block(_human, _auto)
    check("병합: 사람이 비운 칸은 자동값", (_m["eps_adj_ttm"], _o["eps_adj_ttm"]),
          ({"value": 9.2}, "auto"))
    check("병합: 사람 값이 있으면 사람 값", (_m["risk5"], _o["risk5"]),
          ({"business_certainty": "✕"}, "human"))
    check("병합: 자리표시자(value null)는 값이 아니다", _o["roe_tangible"], "auto")
    check("병합: False 는 값이다(가드는 사람 것)",
          (_m["cyclical_peak_guard"], _o["cyclical_peak_guard"]), (False, "human"))
    check("병합: 둘 다 없으면 null + origin 없음",
          (_m["conversion"], _o["conversion"]), (None, None))
    check("병합: 자동만 있는 칸도 실린다", (_m["g_cagr3y"], _o["g_cagr3y"]), (0.08, "auto"))
    # 필드 단위여야 한다 — 블록 통째로 고르면 취재가 늘수록 화면이 비는 역설이 생긴다
    check("병합: 한 칸 취재가 다른 칸을 지우지 않는다",
          _m["eps_adj_ttm"] is not None and _m["risk5"]["business_certainty"] == "✕", True)
    check("병합: 사람 판단층 원본 불변", _human["eps_adj_ttm"], {"value": None, "note": "미취재"})
    _mi = _bl.merged_items({"items": [{"ticker": "T1", "buffett": _human}]}, {"T1": _auto})
    check("병합: merged_items 가 origin 을 함께 싣는다",
          _mi[0]["buffett_origin"]["eps_adj_ttm"], "auto")
    check("병합: 자동층 없으면 사람 값 그대로",
          _bl.merged_items({"items": [{"ticker": "T1", "buffett": _human}]}, {})[0]["buffett"]
          ["risk5"], {"business_certainty": "✕"})

    # 회귀: 판단층이 두 겹이 돼도 **괴리 경로는 1원도 안 바뀐다**
    _cfg_m = _bl.merged_items(_cfg_all, {t["ticker"]: {"eps_adj_ttm": {"value": 99.0},
                                                       "roe_tangible": 0.5}
                                         for t in _cfg_all["items"]})
    _bad = [a.get("ticker") for a, b in zip(_cfg_m, _cfg_all["items"])
            if _fb.measure_gap(a, 137.0, 25.0) != _fb.measure_gap(b, 137.0, 25.0)]
    check("병합: 자동값이 들어와도 괴리 경로 불변(34종)", _bad, [])

    # ── 자동 측정(fetch_buffett_auto) — XBRL 픽스처 검산 ─────────────────────
    import fetch_buffett_auto as _fa

    # 사양서 GOOG 2026Q2 분해를 그대로 재현한 픽스처:
    #   GAAP EPS 9.11 · 미실현이익 세후 6.26 → 조정 EPS 2.85
    #   세후 6.26 이므로 세전 투자손익 = 6.26 / (1−0.21) = 7.9241/주
    # 실제 응답 형태를 따른다: year·quarter 가 있고 손익계산서는 **누적(YTD)** 이다.
    _SH = 12_000_000_000.0
    import datetime as _dt

    def _rpt(year, q, ytd_ni, ytd_gain=None, sh=_SH, days=None):
        rows = [{"concept": "us-gaap_NetIncomeLoss", "value": ytd_ni * sh},
                {"concept": "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding",
                 "value": sh}]
        if ytd_gain is not None:
            rows.append({"concept": "us-gaap_EquitySecuritiesFvNiGainLoss",
                         "value": ytd_gain * sh})
        end = _dt.date(year, 3 * q, 28)
        span = days if days is not None else 91 * q          # 누적 기간
        return {"year": year, "quarter": q,
                "startDate": str(end - _dt.timedelta(days=span)), "endDate": str(end),
                "report": {"ic": rows}}

    # 분기별 EPS 9.11/4 · 투자손익 7.9241/4 이 매 분기 같다고 두고 누적으로 싣는다
    _per_ni, _per_gain = 9.11 / 4, 7.9241 / 4
    _ytd = [_rpt(2026, q, _per_ni * q, _per_gain * q) for q in (4, 3, 2, 1)]
    _eps, _method, _n = _fa.eps_adj_ttm_from(_ytd)
    check("XBRL: 누적 보고를 차분해 조정 EPS 2.85 (±5%)",
          _eps is not None and abs(_eps - 2.85) / 2.85 <= 0.05, True)
    check("XBRL: 쓴 태그를 method 에 남긴다",
          "EquitySecuritiesFvNiGainLoss" in (_method or ""), True)

    # 2026-09-02 실전 사고 회귀 — 기간 길이로 거르면 Q1 만 남아 '4개 연도의 Q1 합'이 된다.
    # 그 합이 그럴듯해 보였던 것이 이 사고의 핵심이다(애플 9.61).
    _q1only = [_rpt(y, 1, 5.0) for y in (2026, 2025, 2024, 2023)]
    check("XBRL: 서로 다른 해의 Q1 4개는 TTM 이 아니다(연속 아님 → 거부)",
          _fa.eps_adj_ttm_from(_q1only)[0], None)
    check("XBRL: 거부 사유가 로그에 남는다",
          "연속이 아님" in _fa.eps_adj_ttm_from(_q1only)[1], True)
    # 회사가 이미 분기 단위로 싣는 경우(누적 아님)는 차분하지 않는다
    _disc = [_rpt(2026, q, 2.0, days=91) for q in (4, 3, 2, 1)]
    check("XBRL: 분기 단위로 싣는 회사는 그대로 합산", round(_fa.eps_adj_ttm_from(_disc)[0], 2), 8.0)
    check("XBRL: 진짜 분기 목록은 최신순", [(y, q) for y, q, _, _ in _fa.quarter_incomes(_ytd)],
          [(2026, 4), (2026, 3), (2026, 2), (2026, 1)])
    check("XBRL: 차분 결과는 분기값", round(_fa.quarter_incomes(_ytd)[0][2] / _SH, 4),
          round(_per_ni - _per_gain * (1 - 0.21), 4))

    # 투자손익 태그가 하나도 없으면 **0 으로 치지 않고** 무조정임을 밝힌다
    _plain = [_rpt(2026, q, 2.0 * q) for q in (4, 3, 2, 1)]
    _e2, _m2, _ = _fa.eps_adj_ttm_from(_plain)
    check("XBRL: 투자손익 태그 없으면 무조정 명시", ("무조정" in _m2, round(_e2, 2)), (True, 8.0))
    check("XBRL: 분기 4개 미만이면 null", _fa.eps_adj_ttm_from(_ytd[:3])[0], None)
    check("XBRL: 순이익 태그 없으면 null",
          _fa.eps_adj_ttm_from([{"year": 2026, "quarter": q, "report": {"ic": []}}
                                for q in (4, 3, 2, 1)])[0], None)

    # 아래는 손으로 만든 dict 대신 **flatten 을 거쳐** 실제 경로를 탄다
    _flat = lambda rows: _fa.flatten({"ic": [{"concept": c, "value": v} for c, v in rows]})
    check("XBRL: 있는 투자손익 태그만 합산",
          _fa.invest_gain(_flat([("us-gaap_GainLossOnInvestments", 10.0),
                                 ("EquitySecuritiesFvNiGainLoss", 5.0)])),
          (15.0, ["us-gaap_GainLossOnInvestments", "EquitySecuritiesFvNiGainLoss"]))
    check("XBRL: 투자손익 태그 전무면 None(0 아님)",
          _fa.invest_gain(_flat([("NetIncomeLoss", 1)]))[0], None)
    check("XBRL: 유형자기자본 음수면 null",
          _fa.tangible_equity(_flat([("StockholdersEquity", 100.0), ("Goodwill", 90.0),
                                     ("FiniteLivedIntangibleAssetsNet", 30.0)])), None)
    check("XBRL: 유형자기자본 정상 산출",
          _fa.tangible_equity(_flat([("StockholdersEquity", 100.0), ("Goodwill", 20.0)])), 80.0)
    check("XBRL: us-gaap_ 접두사를 벗겨 맞춘다",
          _fa.pick(_flat([("us-gaap_NetIncomeLoss", 7.0)]), ["NetIncomeLoss"]),
          (7.0, "us-gaap_NetIncomeLoss"))
    check("XBRL: 콜론 접두사도 같다",
          _fa.pick(_flat([("us-gaap:NetIncomeLoss", 7.0)]), ["NetIncomeLoss"])[0], 7.0)
    check("XBRL: 문자열 숫자도 읽는다", _fa.to_num("1,234.5"), 1234.5)
    check("XBRL: 회계식 음수 표기", _fa.to_num("(2,000)"), -2000.0)
    check("XBRL: 숫자 아닌 값은 None(0 아님)", _fa.to_num("N/A"), None)
    check("XBRL: 주식수 없으면 희석EPS 로 역산",
          _fa.diluted_shares(_flat([("us-gaap_NetIncomeLoss", 5000.0),
                                    ("us-gaap_EarningsPerShareDiluted", 5.0)]))[0], 1000.0)
    check("XBRL: 선행 4배 초과는 보류", _fa.implausible(40.0, 9.1), True)
    check("XBRL: 정상 범위는 통과", _fa.implausible(12.0, 9.1), False)
    check("XBRL: 급감 방향은 막지 않는다", _fa.implausible(1.0, 9.1), False)
    check("XBRL: 선행 EPS 없으면 대조 불가 → 통과", _fa.implausible(40.0, None), False)

    # 3년 CAGR — 진짜 분기 목록 위에서 12칸이 곧 3년이다
    _long = []
    for y in (2026, 2025, 2024, 2023, 2022):
        base = 4.0 if y >= 2026 else 2.0 if y >= 2023 else 1.0
        _long += [_rpt(y, q, base * q) for q in (4, 3, 2, 1)]
    _g3, _why3 = _fa.cagr3y_from(_long)     # 최근 TTM 16 vs 3년 전 8 → 2배/3년
    check("XBRL: 3년 CAGR (12분기 뒤 = 3년)",
          abs(_g3 - (2 ** (1 / 3) - 1)) < 0.01, True)
    check("XBRL: 3년 치가 없으면 null(짧은 이력을 늘려 적지 않는다)",
          _fa.cagr3y_from(_long[:8])[0], None)
    check("XBRL: 못 잰 이유를 함께 돌려준다", bool(_fa.cagr3y_from(_long[:8])[1]), True)

    # ── 전망 g (2026-09-02) — 과거 성장률을 미래 가정으로 쓰지 않는다 ─────────
    # 막으려는 것 한 문장: **잘 나간 구간의 성장을 영원히 이어붙이는 것.**
    check("전망g: +5y 행을 고른다",
          _fa.ltg_from_growth_table([("0q", 0.05), ("+1y", 0.10), ("+5y", 0.08)]), 0.08)
    check("전망g: 과거 행(-5y)은 절대 고르지 않는다",
          _fa.ltg_from_growth_table([("-5y", 0.30)]), None)
    check("전망g: 과거 행만 있고 전망이 없으면 null (과거로 대체 금지)",
          _fa.ltg_from_growth_table([("0q", 0.05), ("-5y", 0.42)]), None)
    check("전망g: LTG 라벨도 인식", _fa.ltg_from_growth_table([("LTG", 0.07)]), 0.07)
    check("전망g: 퍼센트 표기 방어", _fa.ltg_from_growth_table([("+5y", 12.0)]), 0.12)
    check("전망g: 연간 추정 2개년 CAGR",
          round(_fa.growth_from_annual_estimates(
              [{"period": "2027-12-31", "epsAvg": 12.1},
               {"period": "2026-12-31", "epsAvg": 10.0}]), 3), 0.21)
    check("전망g: 추정 1개면 null",
          _fa.growth_from_annual_estimates([{"period": "2026-12-31", "epsAvg": 10.0}]), None)
    check("전망g: 적자 추정이면 null",
          _fa.growth_from_annual_estimates(
              [{"period": "2026-12-31", "epsAvg": -1.0},
               {"period": "2027-12-31", "epsAvg": 2.0}]), None)
    _nofwd = {"ticker": "X", "type": "씨즈형",
              "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.20, "g_forward": None}}
    check("전망g: 전망 없으면 존은 미검정(과거 CAGR 단독 사용 금지)",
          _fb.measure_bench(_nofwd, 100.0, {"UST10": 4.0})["zone_buffett"], "untested")
    _both = {"ticker": "X", "type": "씨즈형",
             "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.20, "g_forward": 0.06}}
    check("전망g: 둘 다 있으면 작은 쪽(전망)이 쓰인다",
          _fb.measure_bench(_both, 100.0, {"UST10": 4.0})["g_used"], 0.06)

    check("XBRL: 적자 구간 CAGR 은 null", _fa.cagr(-1.0, 2.0, 3.0), None)
    check("XBRL: CAGR 계산", round(_fa.cagr(100.0, 133.1, 3.0), 4), 0.1)
    check("자동: 해외 상장 판별",
          [_fa.is_foreign(t) for t in ("005930.KS", "ASML", "TSM", "AAPL", "GOOG")],
          [True, True, True, False, False])

    # ── AI 취재(buffett_scout) — 근거 없는 판정은 버린다 ────────────────────
    # 이 절이 지키는 것은 정확도가 아니라 **정직**이다. Haiku 의 ○△✕ 는 근거 문장
    # 없이는 장식이다. 근거가 없으면 칸을 비우는 쪽이 채우는 쪽보다 낫다.
    import buffett_scout as _bs

    _full = {"franchise": {"need": "○", "no_substitute": "△", "no_price_reg": "○"},
             "risk5": {"business_certainty": "✕", "mgmt_ability": "△",
                       "mgmt_fidelity": "✕", "price": "△", "tax_inflation": "○"},
             "capalloc": {"period": "2026H1", "cash_positive": 1, "buyback": 0,
                          "no_dilution": 0, "debt_discipline": 0, "score": 9},
             "owner_earnings": {"display": "C", "display_reason": "캐펙스 전액"},
             "notes": "증자 49.6B",
             "evidence": {"franchise": "검색 점유율이 90%를 넘는다고 공시했다",
                          "risk5": "반독점 소송이 진행 중이라고 밝혔다",
                          "capalloc": "자사주 매입은 없었다고 공시했다",
                          "owner_earnings": "캐펙스 가이던스를 상향했다"}}
    _c, _kept = _bs.sanitize(_full)
    check("취재: 근거 있는 항목은 실린다", sorted(_kept),
          ["capalloc", "franchise", "notes", "owner_earnings", "risk5"])
    check("취재: score 는 모델 값을 믿지 않고 다시 센다", _c["capalloc"]["score"], 1)
    check("취재: confidence 는 항상 '하'", _c["confidence"], "하")
    check("취재: 근거 문장을 함께 보관", "franchise" in _c["_evidence"], True)

    # 근거가 없으면 — 판정이 아무리 그럴듯해도 버린다
    _noev = dict(_full); _noev["evidence"] = {}
    _c2, _kept2 = _bs.sanitize(_noev)
    check("취재: 근거 없으면 정성 항목 전부 버림", sorted(_kept2), ["notes"])
    check("취재: 버린 칸은 아예 안 실린다", "risk5" in _c2, False)
    _short = dict(_full); _short["evidence"] = {"risk5": "짧음"}
    check("취재: 형식만 갖춘 근거(10자 미만)도 근거가 아니다",
          "risk5" in _bs.sanitize(_short)[0], False)
    check("취재: ○△✕ 아닌 기호는 무시",
          _bs.clean_marks({"need": "GOOD", "no_substitute": "△"},
                          ["need", "no_substitute"]), {"no_substitute": "△"})
    check("취재: 응답이 dict 아니면 아무것도 안 실린다", _bs.sanitize("nope"), ({}, []))

    # 평시 침묵 — 분기가 그대로면 취재하지 않는다 (동행 관측과 같은 밀도 원칙)
    _st = {"items": {"GOOG": {"scouted_at": "2026-08-01 10:00", "last_period": "2026Q2"}}}
    check("취재: 첫 취재는 실행", _bs.needs_scout("NEW", {"period": "2026Q2"}, _st, [])[0], True)
    check("취재: 분기 그대로 + 이벤트 없음 → 침묵",
          _bs.needs_scout("GOOG", {"period": "2026Q2"}, _st, ["Alphabet stock rises"])[0], False)
    check("취재: 신규 분기면 재취재",
          _bs.needs_scout("GOOG", {"period": "2026Q3"}, _st, [])[0], True)
    _ev = _bs.needs_scout("GOOG", {"period": "2026Q2"}, _st,
                          ["Alphabet announces $70B buyback"])
    check("취재: 이벤트 키워드면 분기 중에도 재취재", (_ev[0], "buyback" in _ev[1]), (True, True))
    check("취재: 갱신된 항목만 집어낸다",
          _bs.changed_fields({"risk5": {"a": "○"}, "notes": "x"},
                             {"risk5": {"a": "✕"}, "notes": "x"}), ["risk5"])

    # ── 채널 분리(2026-09-01) — 내부 알림이 공개 채널로 새지 않는다 ──────────
    # 막으려는 것 한 문장: **운영 내부 사정이 구독자에게 보이는 것.**
    # 그래서 '공개 채널 변수가 있어도 발송하지 않는가'로 검사한다.
    import os as _os
    import pipeline_sentinel as _ps
    import buffett_scout as _bs
    _leak_env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "@public",
                 "TELEGRAM_ALERT_CHAT_ID": ""}
    _saved = {k: _os.environ.get(k) for k in _leak_env}
    try:
        _os.environ.update(_leak_env)
        check("채널: 수신처 미등록이면 None (공개 폴백 없음)", _ps.alert_chat_id(), None)
        _sent = []
        _o = _ps.send_telegram
        try:
            _ps.send_telegram = lambda t, c, x: _sent.append(c)
            _st_d = {"sent": {}}
            _ps.dispatch([_ps.alert("x", "내부 경보", "2026-09-01")],
                         __import__("datetime").datetime(2026, 9, 1), "2026-09-01", _st_d)
        finally:
            _ps.send_telegram = _o
        check("채널: 공개 채널이 설정돼 있어도 경보는 안 나간다", _sent, [])
        check("채널: 못 보낸 경보는 서명도 남기지 않는다(다음 회차 재시도)",
              _st_d.get("sent", {}).get("2026-09-01"), None)
        # 등록되면 그리로만 간다
        _os.environ["TELEGRAM_ALERT_CHAT_ID"] = "12345"
        check("채널: 등록되면 DM 으로", _ps.alert_chat_id(), "12345")
        _sent2 = []
        _o = _ps.send_telegram
        try:
            _ps.send_telegram = lambda t, c, x: _sent2.append(c)
            _ps.dispatch([_ps.alert("y", "내부 경보", "2026-09-01")],
                         __import__("datetime").datetime(2026, 9, 1), "2026-09-01", {"sent": {}})
        finally:
            _ps.send_telegram = _o
        check("채널: 수신처는 DM 하나뿐", _sent2, ["12345"])
    finally:
        for k, v in _saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v

    # ── 스카우트 게이트 재현 — 같은 period 면 Haiku 를 부르지 않는다 ──────────
    _prev = {"items": {"AAPL": {"scouted_at": "2026-09-01 10:00",
                                "last_period": "2026Q3", "last_event_hash": ""}}}
    check("스카우트: 같은 period·이벤트 없음 → 침묵",
          _bs.needs_scout("AAPL", {"period": "2026Q3"}, _prev, ["Apple stock rises"])[0], False)
    check("스카우트: 새 period → 재취재",
          _bs.needs_scout("AAPL", {"period": "2026Q4"}, _prev, [])[0], True)
    _h1 = ["Apple announces $100B buyback"]
    check("스카우트: 새 이벤트 → 재취재",
          _bs.needs_scout("AAPL", {"period": "2026Q3"}, _prev, _h1)[0], True)
    # 같은 이벤트가 계속 잡혀도 한 번만 — 해시가 같으면 침묵
    _prev2 = {"items": {"AAPL": dict(_prev["items"]["AAPL"],
                                     last_event_hash=_bs.event_hash(_h1))}}
    check("스카우트: 같은 이벤트 재등장은 침묵",
          _bs.needs_scout("AAPL", {"period": "2026Q3"}, _prev2, _h1)[0], False)
    check("스카우트: 기사 문구가 달라도 키워드가 같으면 같은 지문",
          _bs.event_hash(["A announces buyback"]) == _bs.event_hash(["B plans buyback now"]),
          True)

    # 실전 재현 — main() 을 실제로 두 번 돌려 **호출 횟수**를 센다.
    # 게이트가 Haiku 호출 '앞'에 있는지는 함수 단위로는 증명되지 않는다(배선 문제).
    import json as _js2
    _tmp = Path(__file__).parent.parent / "data" / "_selftest_scout"
    _calls = {"ask": 0, "send": 0}
    _o_ask, _o_head, _o_notify = _bs.ask, _bs.fetch_headlines, None
    _o_cfg, _o_auto, _o_state = _bs.CFG_PATH, _bs.AUTO_PATH, _bs.STATE_PATH
    try:
        _tmp.mkdir(parents=True, exist_ok=True)
        (_tmp / "cfg.json").write_text(_js2.dumps(
            {"items": [{"ticker": "AAPL", "name": "Apple", "type": "씨즈형"}]},
            ensure_ascii=False), encoding="utf-8")
        (_tmp / "auto.json").write_text(_js2.dumps(
            {"items": {"AAPL": {"period": "2026Q3"}}}, ensure_ascii=False), encoding="utf-8")
        (_tmp / "state.json").write_text("{}", encoding="utf-8")
        _bs.CFG_PATH, _bs.AUTO_PATH = _tmp / "cfg.json", _tmp / "auto.json"
        _bs.STATE_PATH = _tmp / "state.json"
        _bs.fetch_headlines = lambda *a, **k: []

        def _fake_ask(*a, **k):
            _calls["ask"] += 1
            return {"risk5": {"business_certainty": "○"},
                    "evidence": {"risk5": "공시에서 확인된 근거 문장이다"}}
        _bs.ask = _fake_ask
        _envp = {"ANTHROPIC_API_KEY": "k", "TELEGRAM_BOT_TOKEN": "t",
                 "TELEGRAM_ALERT_CHAT_ID": "12345"}
        _sv = {k: _os.environ.get(k) for k in _envp}
        _os.environ.update(_envp)
        import send_telegram_briefing as _tg2
        _o_send = _tg2.send_telegram
        _tg2.send_telegram = lambda *a, **k: _calls.__setitem__("send", _calls["send"] + 1)
        try:
            _bs.main()                                  # 1회차 — 첫 취재
            _first = _calls["ask"]
            _bs.main()                                  # 2회차 — 같은 period
            check("스카우트: 같은 period 로 두 번 돌려도 호출은 1회뿐",
                  (_first, _calls["ask"]), (1, 1))
            check("스카우트: 값이 안 바뀌면 DM 0건", _calls["send"], 0)
            # period 를 바꾸면 다시 취재하고 DM 1건
            (_tmp / "auto.json").write_text(_js2.dumps(
                {"items": {"AAPL": {"period": "2026Q4"}}}, ensure_ascii=False), encoding="utf-8")
            _bs.ask = lambda *a, **k: (_calls.__setitem__("ask", _calls["ask"] + 1) or
                                       {"risk5": {"business_certainty": "✕"},
                                        "evidence": {"risk5": "새 분기 공시의 근거 문장이다"}})
            _bs.main()
            check("스카우트: 새 period 면 재취재", _calls["ask"], 2)
            check("스카우트: 갱신 DM 은 정확히 1건", _calls["send"], 1)
            _bs.main()                                  # 같은 날 재실행 — 묶음 중복 금지
            check("스카우트: 같은 날 재실행해도 DM 은 늘지 않는다", _calls["send"], 1)
        finally:
            _tg2.send_telegram = _o_send
            for k, v in _sv.items():
                if v is None:
                    _os.environ.pop(k, None)
                else:
                    _os.environ[k] = v
    finally:
        _bs.ask, _bs.fetch_headlines = _o_ask, _o_head
        _bs.CFG_PATH, _bs.AUTO_PATH, _bs.STATE_PATH = _o_cfg, _o_auto, _o_state
        import shutil as _sh
        _sh.rmtree(_tmp, ignore_errors=True)
    check("스카우트: 구 상태 필드명(period)도 읽어 헛 재취재를 막는다",
          _bs.needs_scout("GOOG", {"period": "2026Q2"},
                          {"items": {"GOOG": {"scouted_at": "x", "period": "2026Q2"}}},
                          [])[0], False)
    check("스카우트: 묶음 문구 형식",
          _bs.digest([("AAPL", ["risk5"]), ("GOOG", ["capalloc"])]),
          "자동 취재 갱신 2종: AAPL·GOOG")

    # ── 관측노트(parallax_journal) — 버핏존 전이 기록 규율 ──────────────────
    import parallax_journal as _pj
    _mk_b = lambda t, z, cause=None: {"ticker": t, "bench": {
        "zone_buffett": z, "cause": cause, "coupon10y": 0.13, "rate10y": 4.0, "g_used": 0.06}}
    check("노트: untested→판정 은 사건 아님",
          _pj.detect_buffett_events([_mk_b("A", "pass")], {"A": "untested"}), [])
    check("노트: 판정→untested 도 사건 아님",
          _pj.detect_buffett_events([_mk_b("A", "untested")], {"A": "pass"}), [])
    check("노트: cause=scale 은 사건 아님",
          _pj.detect_buffett_events([_mk_b("A", "pass", "scale")], {"A": "prove_growth"}), [])
    check("노트: 변화 없으면 침묵",
          _pj.detect_buffett_events([_mk_b("A", "pass", "price")], {"A": "pass"}), [])
    check("노트: 첫 관측은 침묵",
          _pj.detect_buffett_events([_mk_b("A", "pass", "price")], {}), [])
    _bev = _pj.detect_buffett_events([_mk_b("A", "pass", "price")], {"A": "prove_growth"})
    check("노트: 실제 전이는 1건 기록", len(_bev), 1)
    check("노트: 버핏존 문구 형식", _bev[0]["text"],
          "A 버핏존 prove_growth→pass · coupon10y 13.0% vs 10y×3 12.0% · g=6.0%")
    check("노트: 괴리존과 서명이 겹치지 않는다",
          _pj.sig("A", "x", "y", "2026-08-30") != _pj.sig("A", "x", "y", "2026-08-30", "buffett"),
          True)

    # ── 정비 관제탑(pipeline_sentinel) 판정 로직 — 침묵 실패 감시망의 자체 검증 ──
    # 경보가 '울려야 할 때만' 울리는지. 순수 함수만 부르므로 부작용·네트워크 없음.
    # (전 케이스는 scripts/test_sentinel.py — 여기엔 회귀 핵심만 둔다)
    import pipeline_sentinel as _ps
    _st = {"version": _ps.STATE_VERSION, "sources": {}, "feeds": {}, "buffett": [], "sent": {}}
    _names = ["srcA"]
    # 판정 자로는 fetch_status 의 outcome — '새 글이 몇 건인가'가 아니라 '응답이 있었나'
    _led = lambda outcome, items: {"sources": {"signals:srcA": {
        "kind": "signals", "source": "srcA", "outcome": outcome,
        "code": 200, "items": items}}}
    _ps.judge_signals(_led("ok", 3), _names, _st, "2026-01-01", _ps.DEFAULTS)  # 기준선
    # 회귀 핵심 ①: 응답이 멀쩡하면 새 글이 며칠 없어도 침묵 (2026-08-30 오경보의 정체)
    for _i in range(7):
        _a0, _ = _ps.judge_signals(_led("ok", 5), _names, _st, "2026-01-02", _ps.DEFAULTS)
        if _a0:
            break
    check("sentinel: 조용한 발행처는 무경보(ok 7회)", _a0, [])
    _a1, _ = _ps.judge_signals(_led("zero", 0), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 0건 1회는 침묵", _a1, [])
    _a2, _ = _ps.judge_signals(_led("zero", 0), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 0건 2회 연속 경보", len(_a2) == 1 and _a2[0]["kind"] == "alert", True)
    _a3, _ = _ps.judge_signals(_led("ok", 2), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 회복 알림 1회", len(_a3) == 1 and _a3[0]["kind"] == "recover", True)
    _a4, _ = _ps.judge_signals(_led("ok", 2), _names, _st, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 회복 후 재침묵", _a4, [])
    # 회귀 핵심 ②: 요청 실패는 judge_feeds 단독 관할 — 한 사건에 두 번 울리지 않는다
    _est = {"version": _ps.STATE_VERSION, "sources": {}, "feeds": {}, "buffett": [], "sent": {}}
    _ps.judge_signals(_led("ok", 3), _names, _est, "2026-01-01", _ps.DEFAULTS)
    for _i in range(2):
        _sa, _ = _ps.judge_signals(_led("http_error", 0), _names, _est, "2026-01-02", _ps.DEFAULTS)
        _fa, _ = _ps.judge_feeds(_led("http_error", 0), _est, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: 요청 실패는 정확히 1건만 경보", len(_sa + _fa), 1)
    # 회귀 핵심 ③: 폐기된 자로의 눈금(v1)을 이어받아 헛 회복 알림을 쏘지 않는다
    _mst = {"version": 1, "sources": {"srcA": {"zero_streak": 7, "alerted": True,
                                               "ever_seen": True}}}
    _ps.migrate_state(_mst)
    _ma, _ = _ps.judge_signals(_led("ok", 5), _names, _mst, "2026-01-02", _ps.DEFAULTS)
    check("sentinel: v1 이관 후 헛 회복 알림 없음", _ma, [])
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
