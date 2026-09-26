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

    # ⑤ 시클리컬 가드 — **측정과 판정의 분리**(2026-09-25 legend-audit B)
    #    막으려는 것 한 문장: 정점 이익으로 계산한 쿠폰이 **판정**(존·통과가격)이 되는 것.
    #    쿠폰 자체는 측정이다 — 재는 것까지 막으면 가드 종목은 사이클이 돌아도 영원히
    #    숫자가 없어, 가드를 풀지 말지 판단할 재료마저 사라진다.
    _cyc = {"ticker": "MU", "type": "시클리컬",
            "buffett": {"cyclical_peak_guard": True, "eps_adj_ttm": {"value": 10.0},
                        "g_cagr3y": 0.05, "g_forward": 0.05}}
    _cb = _fb.measure_bench(_cyc, 100.0, {"UST10": 4.0})
    check("B 가드: 쿠폰은 산출한다(측정)", _cb["coupon10y"] is not None, True)
    check("B 가드: 쿠폰 값은 가드 없을 때와 같다",
          _cb["coupon10y"], round(0.1 * 1.05 ** 10, 6))
    check("벤치: 가드도 존은 untested (판정 보류)", _cb["zone_buffett"], "untested")
    check("B 가드: 통과가격·경계는 만들지 않는다(판정의 파생)",
          (_cb.get("pass_price"), _cb.get("borderline")), (None, False))
    check("B 가드: 비고는 '존 판정 보류'", "존 판정 보류" in _cb["note"], True)
    check("벤치: 가드 사유가 note 에 남는다", "정점 가드" in _cb["note"], True)
    # 가드 근거 칸 — 사람 판단층 전용. 병합기가 실어 날라야 화면이 '미기재'를 가릴 수 있다
    import buffett_layers as _blg
    check("B 가드: 병합기가 가드 근거 칸을 싣는다",
          _blg.merge_block({"guard_reason": "사유"}, {})[0].get("guard_reason"), "사유")
    # 표시층 원장 — 가드인데 근거가 없으면 그 상태가 화면까지 실려야 한다
    import build_site as _bsg
    _lrows = getattr(_bsg, "legend_rows", None)
    if _lrows is None:
        check("B 가드: 표시층 원장 함수(legend_rows) 존재", None, "legend_rows")
    else:
        _lr = _lrows([{"ticker": "MU", "buffett": {"cyclical_peak_guard": True},
                       "buffett_origin": {}},
                      {"ticker": "LRCX", "buffett": {"cyclical_peak_guard": True,
                                                     "guard_reason": "웨이퍼팹 캐펙스 사이클"},
                       "buffett_origin": {"guard_reason": "human"}}])
        check("B 가드: 근거 없는 가드 종목은 '미기재' 상태로 실린다",
              (_lr["MU"].get("guard_reason"), _lr["LRCX"].get("guard_reason")),
              (None, "웨이퍼팹 캐펙스 사이클"))
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

    # ── 판단층 기준일 (2026-09-25 legend-audit D) ───────────────────────────
    # 막으려는 것 한 문장: **매일 갱신되는 자동값이 사람 값의 날짜 뒤에 가려지는 것.**
    # 실측: 화면 "판단층 기준일 2026-09-03" 은 사람 파일의 asof 였다. 자동층은 9/25 까지
    # 매 회차 갱신됐고 스카우트도 9/25 에 돌았는데, 병합에서 as_of 는 사람 값이 이겨
    # 행 비고까지 "기준 2026-09-03" 으로 굳어 있었다.
    _ap = getattr(_bl, "asof_pair", None)
    if _ap is None:
        check("D 기준일: asof_pair 존재", None, "asof_pair")
    else:
        check("D 기준일: 사람·자동 각각의 최신일",
              _ap({"as_of": "2026-09-03",
                   "cagr3y_human": {"value": 19.1, "unit": "%", "asof": "2026-09-05"}},
                  {"as_of": "2026-09-25"}),
              {"human": "2026-09-05", "auto": "2026-09-25", "latest": "2026-09-25"})
        check("D 기준일: 자동이 없으면 사람 날짜가 최신",
              _ap({"as_of": "2026-09-03"}, None)["latest"], "2026-09-03")
        _mi2 = _bl.merged_items({"items": [{"ticker": "T1", "buffett": {"as_of": "2026-09-03"}}]},
                                {"T1": {"as_of": "2026-09-25"}})
        check("D 기준일: 병합 행에 두 날짜가 함께 실린다",
              (_mi2[0].get("buffett_asof") or {}).get("latest"), "2026-09-25")
    import build_site as _bsd
    _la = getattr(_bsd, "legend_asof", None)
    if _la is None:
        check("D 기준일: 헤더 기준일(legend_asof) 존재", None, "legend_asof")
    else:
        check("D 기준일: 헤더는 사람·자동 중 최신 갱신일",
              _la({"asof": "2026-09-03"},
                  {"generated_at": "2026-09-25T18:06:00+09:00", "scout_label": "2026-09-25 18:07"},
                  [{"buffett_asof": {"human": "2026-09-05", "auto": "2026-09-25"}}]),
              {"latest": "2026-09-25", "human": "2026-09-05", "auto": "2026-09-25"})

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
    # 실제 응답에는 Q4 가 없다 — 10-K(연간)에서 FY−Q3 로 복원해야 한다
    _ytd = _fa._attach_annual(
        [_rpt(2026, q, _per_ni * q, _per_gain * q) for q in (3, 2, 1)],
        [_rpt(2026, 4, _per_ni * 4, _per_gain * 4, days=365)])
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
    check("XBRL: 거부 사유에 조립된 분기를 남긴다",
          "조립된 분기" in _fa.eps_adj_ttm_from(_q1only)[1], True)
    # 결번이 하나 있어도 한 칸 밀린 **연속** 창으로 측정한다 (전멸 방지)
    # 최신 분기가 외따로 떨어져 있으면(직전 분기 결번) 한 칸 밀린 연속 창을 쓴다
    _gap = ([_rpt(2027, 1, 3.0)]                      # 2026Q3·Q4 결번 → 2027Q1 은 고립
            + [_rpt(2026, q, 2.0 * q) for q in (2, 1)]
            + [_rpt(2025, q, 2.0 * q) for q in (4, 3, 2, 1)])
    _ge, _gm, _ = _fa.eps_adj_ttm_from(_gap)
    check("XBRL: 최신 분기가 고립되면 한 칸 밀린 연속 창으로 측정", round(_ge, 2), 8.0)
    check("XBRL: 밀린 창은 그 사실을 밝힌다", "결번" in _gm, True)
    # 회사가 이미 분기 단위로 싣는 경우(누적 아님)는 차분하지 않는다
    _disc = _fa._attach_annual([_rpt(2026, q, 2.0, days=91) for q in (3, 2, 1)],
                               [_rpt(2026, 4, 8.0, days=365)])
    check("XBRL: 분기 단위로 싣는 회사는 그대로 합산", round(_fa.eps_adj_ttm_from(_disc)[0], 2), 8.0)
    check("XBRL: 진짜 분기 목록은 최신순",
          [(y, q) for y, q, *_ in _fa.quarter_incomes(_ytd, _ytd.annual)],
          [(2026, 4), (2026, 3), (2026, 2), (2026, 1)])
    check("XBRL: 차분 결과는 분기값",
          round(_fa.quarter_incomes(_ytd, _ytd.annual)[0][2]['adj'] / _SH, 4),
          round(_per_ni - _per_gain * (1 - 0.21), 4))

    # ── XBRL 신선도 가드 (2026-09-05) ────────────────────────────────────────
    # 막으려는 것 한 문장: **낡은 분기로 만든 TTM 이 조용히 존 판정에 쓰이는 것.**
    # 실전 발각: GOOG 의 자동 EPS 32.3984 가 **2012Q3** 필링에서 나왔다. 분할 전
    # 값이라 자릿수가 그럴듯해 EY 9.6% 로 원장에 앉아 있었고, g 만 채워졌으면
    # 그날 2012년 이익으로 존이 매겨졌을 것이다. 무증상이 이 사고의 핵심이다.
    _NOW = _dt.datetime(2026, 9, 5)

    def _win(y, q, end):
        """창의 최신 원소만 보면 되므로 최소 형태 — (연, 분기, 지표, 근거, 분기말)."""
        return [(y, q, {"adj": 1.0}, "w", end)]

    _st = _fa.stale_window(_win(2012, 3, "2012-09-30"), _NOW)
    check("신선도: 14년 묵은 창은 폐기", (_st or {}).get("period"), "2012Q3")
    check("신선도: 경과일을 함께 돌려준다", (_st or {}).get("age_days") > 5000, True)
    check("신선도: 최근 분기는 통과(오탐 없음)",
          _fa.stale_window(_win(2026, 2, "2026-06-30"), _NOW), None)
    # 경계 — 270일 정확히는 통과, 하루 더 낡으면 폐기. 임계가 실제로 그 자리인지.
    _edge = (_NOW - _dt.timedelta(days=_fa.STALE_TTM_DAYS)).strftime("%Y-%m-%d")
    _over = (_NOW - _dt.timedelta(days=_fa.STALE_TTM_DAYS + 1)).strftime("%Y-%m-%d")
    check("신선도: 임계 정확히(270일)는 통과",
          _fa.stale_window(_win(2025, 4, _edge), _NOW), None)
    check("신선도: 임계 하루 초과는 폐기",
          bool(_fa.stale_window(_win(2025, 4, _over), _NOW)), True)
    # 분기말이 없는 응답 — 달력 근사로 폴백하되 **폴백을 탔다는 사실을 남긴다**
    _fb2 = _fa.stale_window(_win(2012, 3, None), _NOW)
    check("신선도: 분기말 없으면 달력 근사로라도 잡는다", (_fb2 or {}).get("period"), "2012Q3")
    check("신선도: 폴백을 탔다는 사실을 남긴다", (_fb2 or {}).get("fallback"), True)
    check("신선도: 근사 폴백이 정상 분기를 막지 않는다",
          _fa.stale_window(_win(2026, 2, None), _NOW), None)
    check("신선도: 창이 없으면 판정하지 않는다", _fa.stale_window([], _NOW), None)
    # 배선 ① — 조립된 분기가 분기말을 싣고 있는가(가드가 잴 자를 실제로 받는가)
    check("신선도 배선: 조립된 분기가 분기말을 싣는다",
          _fa.quarter_incomes(_ytd, _ytd.annual)[0][4], "2026-12-28")
    check("신선도 배선: TTM 창 원소가 가드가 읽는 모양",
          len((_fa.eps_adj_ttm_from(_ytd) or 0) and _fa.eps_adj_ttm_from.last_window[0]), 5)

    # 배선 ② — **build_block 을 실제로 돌린다.** 판정부 단위 테스트가 전부 통과해도
    # 배선이 끊겨 있으면 실전만 틀린다(2026-08-30 교훈). 낡은 픽스처를 넣고
    # 원장·블록·로그까지 흘러오는지 본다.
    _o_fr, _o_rec, _o_gf = _fa.fetch_reports, _fa._record, _fa.fetch_forward_growth
    try:
        _fa._record = lambda *a, **k: None
        _fa.fetch_forward_growth = lambda *a, **k: (None, None)

        def _wire(qs, ann):
            _fa.fetch_reports = (lambda tk, key, kind="quarterly", query=None:
                                 (ann, "ok", 200) if kind == "annual" else (qs, "ok", 200))
            _fa.STALE_DISCARDED.clear()
            return _fa.build_block({"ticker": "ZZZ", "type": "씨즈형"}, "k", _NOW)[0]

        _old_blk = _wire([_rpt(2012, q, 2.0 * q) for q in (3, 2, 1)],
                         [_rpt(2012, 4, 8.0, days=365)])
        check("신선도 배선: 낡은 공시로 실행하면 EPS 가 폐기된다",
              _old_blk["eps_adj_ttm"], None)
        check("신선도 배선: 폐기 사유를 블록에 남긴다",
              "XBRL 신선도 미달: 최신 2012Q4" in (_old_blk.get("notes") or ""), True)
        check("신선도 배선: 같은 창에서 나온 오너어닝·전환율도 함께 비운다",
              (_old_blk["owner_earnings"], _old_blk["conversion"]), (None, None))
        check("신선도 배선: 폐기 원장에 종목·분기가 남는다",
              [(d["ticker"], d["period"]) for d in _fa.STALE_DISCARDED],
              [("ZZZ", "2012Q4")])
        # 오탐 확인 — 같은 배선에 신선한 공시를 넣으면 통과해야 한다
        _new_blk = _wire([_rpt(2026, q, 2.0 * q) for q in (3, 2, 1)],
                         [_rpt(2026, 4, 8.0, days=365)])
        check("신선도 배선: 신선한 공시는 그대로 측정된다",
              round((_new_blk["eps_adj_ttm"] or {}).get("value"), 2), 8.0)
        check("신선도 배선: 정상 회차엔 폐기 원장이 비어 있다", _fa.STALE_DISCARDED, [])
        check("신선도 배선: 기준 시점은 TTM 창의 최신 분기", _new_blk["period"], "2026Q4")
        check("신선도 배선: 창 구성 4분기를 산출물에 남긴다",
              [w["period"] for w in _new_blk["ttm_window"]],
              ["2026Q4", "2026Q3", "2026Q2", "2026Q1"])
    finally:
        _fa.fetch_reports, _fa._record, _fa.fetch_forward_growth = _o_fr, _o_rec, _o_gf
        _fa.STALE_DISCARDED.clear()

    # 누더기 TTM 금지 — 중간 분기가 비면 그 4개를 더하지 않는다(연속 창만 채택).
    _hole = ([_rpt(2026, q, 2.0 * q) for q in (3, 2, 1)]
             + [_rpt(2025, q, 2.0 * q) for q in (2, 1)])      # 2025Q3·Q4 결번
    check("연속성: 결번을 건너뛴 4분기 합은 거부", _fa.eps_adj_ttm_from(_hole)[0], None)
    check("연속성: 거부 사유에 조립된 분기를 남긴다",
          "연속 4분기 없음" in _fa.eps_adj_ttm_from(_hole)[1], True)

    # 투자손익 태그가 하나도 없으면 **0 으로 치지 않고** 무조정임을 밝힌다
    _plain = _fa._attach_annual([_rpt(2026, q, 2.0 * q) for q in (3, 2, 1)],
                                [_rpt(2026, 4, 8.0, days=365)])
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
    _long, _lann = [], []
    for y in (2026, 2025, 2024, 2023, 2022):
        base = 4.0 if y >= 2026 else 2.0 if y >= 2023 else 1.0
        _long += [_rpt(y, q, base * q) for q in (3, 2, 1)]
        _lann.append(_rpt(y, 4, base * 4, days=365))
    _long = _fa._attach_annual(_long, _lann)
    _g3, _why3 = _fa.cagr3y_from(_long)     # 최근 TTM 16 vs 3년 전 8 → 2배/3년
    check("XBRL: 3년 CAGR (12분기 뒤 = 3년)",
          abs(_g3 - (2 ** (1 / 3) - 1)) < 0.01, True)
    check("XBRL: Q4 는 연간 보고에서 복원한다(10-Q 에는 없다)",
          [(y, q) for y, q, *_ in _fa.quarter_incomes(_long, _long.annual)][:4],
          [(2026, 4), (2026, 3), (2026, 2), (2026, 1)])
    _shortl = _fa._attach_annual(_long[:6], _lann[:2])
    check("XBRL: 3년 치가 없으면 null(짧은 이력을 늘려 적지 않는다)",
          _fa.cagr3y_from(_shortl)[0], None)
    check("XBRL: 못 잰 이유를 함께 돌려준다", bool(_fa.cagr3y_from(_shortl)[1]), True)

    # ── g 3중 가드 (2026-09-02 v2) ──────────────────────────────────────────
    # ⓐ 40% 초과 전망은 채택 거부(수집층) · ⓑ 채택분도 20% 캡(측정층) · ⓒ 교차검증
    # 없는 단독 가정은 화면에 '가정 약함'. 셋 다 같은 것을 막는다: **단기 숫자가
    # 10년 복리 자리에 앉는 것.**
    check("가드ⓐ: 전망 45% 는 채택 거부", _fa.vet_forward_growth(0.45, "s")[0], None)
    check("가드ⓑ: 30% 는 20% 로 캡(원값 보존)", _fb.cap_g(0.30), (0.20, 0.30))
    check("가드ⓑ: 15% 는 그대로(캡 흔적 없음)", _fb.cap_g(0.15), (0.15, None))
    check("가드ⓑ: 없는 값은 없는 채로", _fb.cap_g(None), (None, None))
    _capped = {"ticker": "X", "type": "씨즈형",
               "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.30,
                           "g_forward": 0.35}}
    _cb = _fb.measure_bench(_capped, 100.0, {"UST10": 4.75})
    check("가드ⓑ: 캡이 실제 쿠폰에 반영된다", _cb["g_used"], 0.20)
    check("가드ⓑ: 캡 전 값을 화면에 넘긴다", _cb["g_capped_from"], 0.30)
    _weak = {"ticker": "X", "type": "씨즈형",
             "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.10,
                         "g_forward": 0.08}}
    check("가드ⓒ: 과거·전망이 모두 있으면 가정 약함 아님",
          _fb.measure_bench(_weak, 100.0, {"UST10": 4.75})["g_weak"], False)

    # 단위 — 사람은 %로 적고 기계는 소수로 적는다. 추측하지 않고 unit 만 믿는다.
    check("단위: 사람 %표기를 소수로", _fb._rate({"value": 13.0, "unit": "%"}), 0.13)
    check("단위: unit 없는 dict 는 소수 그대로", _fb._rate({"value": 0.13}), 0.13)
    check("단위: 맨숫자는 소수 그대로(자동층 관례)", _fb._rate(0.13), 0.13)
    check("단위: 1 넘는 자동 소수도 그대로(NVDA 1.62 는 정상)", _fb._rate(1.62), 1.62)
    check("단위: 없는 값", _fb._rate(None), None)
    _hp = {"ticker": "X", "type": "씨즈형",
           "buffett": {"eps_adj_ttm": {"value": 16.79}, "g_cagr3y": 0.1985,
                       "g_forward": {"value": 13.0, "unit": "%"}}}
    check("단위: 사람 %값이 g_used 로 정확히 들어간다",
          _fb.measure_bench(_hp, 500.0, {"UST10": 4.75})["g_used"], 0.13)
    # 사람이 %를 안 적어 1300% 가 되는 사고 — 캡이 20% 로 '고쳐' 가리지 않는지
    _bad = {"ticker": "X", "type": "씨즈형",
            "buffett": {"eps_adj_ttm": {"value": 16.79}, "g_cagr3y": 0.1985,
                        "g_forward": {"value": 13.0, "unit": "%"}}}
    check("단위: 캡이 단위 오류를 덮지 않는다(정상값은 캡 흔적 없음)",
          _fb.measure_bench(_bad, 500.0, {"UST10": 4.75})["g_capped_from"], None)

    # ── 과거 다리: 사람이 취재한 3년 CAGR (2026-09-05) ───────────────────────
    # 자동 3년 CAGR 은 공시 12분기 조립이 필요해 해외 상장에는 경로가 없다
    # (TSM·ARM·WMT·PANW). 전망만 취재해도 g 가 서지 않아 영원히 미검정이었다.
    # 사람이 과거 3년을 취재하면 그것을 둘째 다리로 쓴다 — min 규칙은 그대로다.
    check("과거 다리: 자동이 있으면 자동이 이긴다(실측 > 취재)",
          _fb.past_leg(0.12, {"value": 30.0, "unit": "%"}), 0.12)
    check("과거 다리: 자동이 없으면 사람 취재값",
          _fb.past_leg(None, {"value": 30.0, "unit": "%"}), {"value": 30.0, "unit": "%"})
    check("과거 다리: 둘 다 없으면 없음", _fb.past_leg(None, None), None)
    check("과거 다리: 자리표시자는 값이 아니다",
          _fb.past_leg({"value": None}, {"value": 12.0, "unit": "%"}),
          {"value": 12.0, "unit": "%"})
    _hleg = {"ticker": "TSMX", "type": "플라이트세이프티형",
             "buffett": {"eps_adj_ttm": {"value": 13.85}, "g_cagr3y": None,
                         "cagr3y_human": {"value": 14.0, "unit": "%"},
                         "g_forward": {"value": 17.5, "unit": "%"}}}
    _hb = _fb.measure_bench(_hleg, 428.91, {"UST10": 4.77})
    check("과거 다리: 취재 다리로 판정이 선다(더는 미검정 아님)",
          _hb["zone_buffett"] != "untested", True)
    check("과거 다리: min 규칙 불변 — 작은 쪽(14%)이 쓰인다", _hb["g_used"], 0.14)
    check("과거 다리: 취재된 과거는 '가정 약함'이 아니다", _hb["g_weak"], False)
    # 자동이 살아 있으면 사람 취재값이 판정을 바꾸지 못한다
    _hauto = {"ticker": "X", "type": "씨즈형",
              "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.08,
                          "cagr3y_human": {"value": 25.0, "unit": "%"},
                          "g_forward": {"value": 20.0, "unit": "%"}}}
    check("과거 다리: 자동값이 있으면 취재값이 덮지 못한다",
          _fb.measure_bench(_hauto, 100.0, {"UST10": 4.75})["g_used"], 0.08)
    check("과거 다리: 전망이 없으면 취재 다리만으로는 서지 않는다",
          _fb.measure_bench(
              {"ticker": "X", "type": "씨즈형",
               "buffett": {"eps_adj_ttm": {"value": 10.0},
                           "cagr3y_human": {"value": 12.0, "unit": "%"}}},
              100.0, {"UST10": 4.75})["g_used"], None)
    check("과거 다리: 병합 스키마에 등재됐다", "cagr3y_human" in _bl.FIELDS, True)

    # ── EPS 결측 5종 (2026-09-25 legend-audit E) ────────────────────────────
    # 막으려는 것 한 문장: **다른 법인의 옛 공시에 묶인 심볼 때문에 대형주가 영원히 빈칸인 것,
    # 그리고 결측 사유가 전부 '취재 대기' 한 줄로 뭉개지는 것.**
    # 실측: GOOG — Finnhub 'GOOG' 이 분기 4건(최신 2012Q4)만 준다. 2015 지주사 전환 전
    # Google Inc. 공시다. SNDK — 옛 SanDisk(2012Q4)와 새 Sandisk 가 섞여 창이 옛 법인으로
    # 밀렸다. STX·CRWD — 최신 분기 결번으로 창이 546·694일 전으로 밀려 신선도 폐기.
    # BRK-B — 희석주식수 태그 없음(보험·투자 지주 — 투자평가손익이 이익을 지배).
    _att = getattr(_fa, "xbrl_attempts", None)
    if _att is None:
        check("E GOOG: 심볼 대체 조회(xbrl_attempts) 존재", None, "xbrl_attempts")
    else:
        check("E GOOG: GOOG → GOOGL → CIK 직접 지정 순으로 시도",
              _att("GOOG"), [("symbol", "GOOG"), ("symbol", "GOOGL"), ("cik", "1652044")])
        check("E GOOG: 대체가 없는 종목은 자기 심볼만", _att("MSFT"), [("symbol", "MSFT")])
        check("E GOOG: CIK 조회 URL",
              "cik=1652044" in _fa.reports_url(("cik", "1652044"), "quarterly", "k"), True)
        check("E GOOG: 심볼 조회 URL 은 종전 그대로",
              _fa.reports_url(("symbol", "MSFT"), "annual", "k"),
              "https://finnhub.io/api/v1/stock/financials-reported"
              "?symbol=MSFT&freq=annual&token=k")
        _pa = _fa.choose_attempt
        check("E GOOG: 낡은 창은 버리고 신선한 대체 조회를 고른다",
              _pa([{"q": ("symbol", "GOOG"), "eps": 32.4, "stale": {"period": "2012Q4"}},
                   {"q": ("symbol", "GOOGL"), "eps": 9.1, "stale": None}])["q"],
              ("symbol", "GOOGL"))
        check("E GOOG: 전부 실패하면 첫 시도(자기 심볼)의 진단을 남긴다",
              _pa([{"q": ("symbol", "GOOG"), "eps": None, "stale": {"period": "2012Q4"}},
                   {"q": ("symbol", "GOOGL"), "eps": None, "stale": None}])["q"],
              ("symbol", "GOOG"))
    # 배선 — build_block 이 실제로 대체 조회를 타고, 채택한 쪽의 창으로 EPS 를 싣는가.
    # 판정 함수가 다 맞아도 배선이 끊겨 있으면 실전만 틀린다(2026-08-30 교훈).
    if _att is not None:
        import feed_client as _fcw
        _buf0 = dict(_fcw._buffer)
        _q91 = lambda y, q: _rpt(y, q, 2.0, days=91)
        _stale_r = [_q91(2012, q) for q in (4, 3, 2, 1)]
        _fresh_r = [_q91(2026, 2), _q91(2026, 1), _q91(2025, 4), _q91(2025, 3)]
        _calls_w = []

        def _fake_fr(tk, key, freq="quarterly", query=None):
            _calls_w.append((freq, query))
            if freq == "annual":
                return [], "zero", 200
            return ((_stale_r if (query or ("symbol", tk))[1] == "GOOG" else _fresh_r),
                    "ok", 200)
        _o_fr2, _o_fg2 = _fa.fetch_reports, _fa.fetch_forward_growth
        _fa.fetch_reports, _fa.fetch_forward_growth = _fake_fr, (lambda t, k: (None, "x"))
        _fa.STALE_DISCARDED.clear()
        try:
            _gblk, _ = _fa.build_block({"ticker": "GOOG", "type": "씨즈형"}, "k",
                                       _dt.datetime(2026, 9, 25))
        finally:
            _fa.fetch_reports, _fa.fetch_forward_growth = _o_fr2, _o_fg2
            _fcw._buffer.clear()
            _fcw._buffer.update(_buf0)
        check("E 배선: GOOG 는 GOOGL 조회에서 신선한 창을 채택",
              ((_gblk.get("eps_adj_ttm") or {}).get("value"), _gblk.get("period")), (8.0, "2026Q2"))
        check("E 배선: 채택한 대체 조회를 출처에 밝힌다", "GOOGL 조회" in (_gblk.get("source") or ""),
              True)
        check("E 배선: 대체 조회로 살렸으면 신선도 폐기 원장에 올리지 않는다",
              _fa.STALE_DISCARDED, [])
        check("E 배선: CIK 까지 가지 않는다(첫 신선한 조회에서 멈춤)",
              [q for f, q in _calls_w if f == "quarterly"],
              [("symbol", "GOOG"), ("symbol", "GOOGL")])
        _fa.STALE_DISCARDED.clear()

    # 사람 취재 경로 — C 와 같은 규약(자동값이 있으면 자동 우선)
    _eh = {"value": 9.1, "unit": "USD", "basis": "사람 조정", "source": "10-Q",
           "asof": "2026-09-25", "confidence": "중"}
    check("E 취재: 자동이 없으면 사람 EPS",
          _fb.measure_bench({"ticker": "X", "type": "씨즈형",
                             "buffett": {"eps_adj_ttm_human": _eh}},
                            100.0, {"UST10": 4.0})["eps_adj_ttm"], 9.1)
    check("E 취재: 자동이 있으면 자동이 이긴다",
          _fb.measure_bench({"ticker": "X", "type": "씨즈형",
                             "buffett": {"eps_adj_ttm": {"value": 8.0},
                                         "eps_adj_ttm_human": _eh}},
                            100.0, {"UST10": 4.0})["eps_adj_ttm"], 8.0)
    check("E 취재: 병합 스키마에 등재(eps_adj_ttm_human·eps_status)",
          ("eps_adj_ttm_human" in _bl.FIELDS, "eps_status" in _bl.FIELDS), (True, True))
    # BRK-B — 플로트형은 자동 조정을 하지 않는다(공시를 부르지도 않는다)
    _o_fr = _fa.fetch_reports
    _called = []
    _fa.fetch_reports = lambda *a, **k: (_called.append(a), ([], "zero", 200))[1]
    try:
        _bb, _ = _fa.build_block({"ticker": "BRK-B", "type": "플로트형"}, "k",
                                 _dt.datetime(2026, 9, 25))
    finally:
        _fa.fetch_reports = _o_fr
    check("E BRK-B: 플로트형은 공시 조정을 부르지 않는다", _called, [])
    check("E BRK-B: 상태가 남는다", (_bb.get("eps_status") or {}).get("state"), "float_skip")
    _brk = _fb.measure_bench({"ticker": "BRK-B", "type": "플로트형",
                              "buffett": {"eps_status": {"state": "float_skip"}}},
                             500.0, {"UST10": 4.0})
    check("E BRK-B: 비고 — 투자평가손익 지배, 사람 취재 전용",
          _brk.get("eps_note"), "투자평가손익 지배 — 자동 조정 무의미, 사람 취재 전용")
    check("E 비고: 낡은 공시는 그 이름으로",
          _fb.measure_bench({"ticker": "STX", "type": "시클리컬",
                             "buffett": {"eps_status": {"state": "stale", "period": "2025Q3"}}},
                            100.0, {"UST10": 4.0}).get("eps_note"),
          "공시 최신 분기 미확보(창이 2025Q3에 멈춤) — 12개월 조정이익 미확보")
    check("E 비고: 상태가 없으면 종전 문구",
          _fb.measure_bench({"ticker": "X", "type": "씨즈형", "buffett": {}},
                            100.0, {"UST10": 4.0}).get("eps_note"),
          "취재 대기 — 12개월 조정이익 미확보")

    # ── 유형 ROE 결측 (2026-09-25 legend-audit C) ───────────────────────────
    # 막으려는 것 한 문장: **서로 다른 결측 원인이 한 문구로 뭉개지거나, 잴 수 있는 값을
    # 경로가 없어서 못 재는 것.** 실측(9/25): ROE 결측 20종 = 해외 12(계산 경로 자체 없음) ·
    # 유형자본 음수 2(AVGO 자본 996억 vs 영업권+무형 1,241억 · PANW 275억 vs 290억) ·
    # 자기자본 음수 1(DELL −14억) · 이익 폐기 5(E 항목). 예전 비고는 전부
    # "유형자기자본 0 이하" 한 줄이었다(DELL 은 자본 자체가 음수라 원인이 다르다).
    _rs = getattr(_fa, "roe_state", None)
    _rn = getattr(_fb, "roe_note", None)
    if _rs is None or _rn is None:
        check("C ROE: roe_state·roe_note 존재(원인별 분리)", None, "roe_state/roe_note")
    else:
        check("C ROE: AVGO 모양 — 유형자본 음수",
              _rs(99.69e9, 124.126e9, 38.265e9), (None, "tangible_negative"))
        check("C ROE: DELL 모양 — 자기자본 음수",
              _rs(-1.427e9, 23.795e9, 11.378e9), (None, "equity_negative"))
        check("C ROE: 이익 없으면 이익 결측", _rs(10e9, 1e9, None), (None, "income_missing"))
        check("C ROE: 자본 항목 없으면 자본 결측", _rs(None, 1e9, 5e9), (None, "equity_missing"))
        check("C ROE: TSM 모양 — GAAP 미조정 34.6%",
              _rs(6432.518e9, 24.075e9, 2216.808e9), (0.3459, "ok"))
        _avgo_b = {"kind": "xbrl", "status": "tangible_negative", "equity": 99.69e9,
                   "goodwill_intangibles": 124.126e9, "net_income_ttm": 38.265e9}
        check("C 비고: 유형자본 음수는 그 이름으로(영업권·무형 > 자기자본)",
              _rn(_avgo_b), "유형자본 음수 — ROE 산출 불가(영업권·무형자산 > 자기자본)")
        check("C 비고: 자기자본 음수는 다른 문구",
              _rn(dict(_avgo_b, status="equity_negative", equity=-1.4e9)),
              "자기자본 음수 — ROE 산출 불가")
        check("C 비고: 해외 대차대조표 없음은 그 이름으로",
              _rn({"kind": "yfinance", "status": "equity_missing"}),
              "해외 공시 대차대조표 미확보 — ROE 산출 불가")
        check("C 비고: 미국 공시에서 자본 태그가 안 잡히면 미매핑",
              _rn({"kind": "xbrl", "status": "equity_missing"}),
              "자기자본 항목 미매핑 — ROE 산출 불가")
        check("C 비고: 이익 결측", _rn({"kind": "xbrl", "status": "income_missing"}),
              "12개월 이익 미확보 — ROE 산출 불가")
        check("C 비고: 수집 실패", _rn({"kind": "xbrl", "status": "api_fail"}),
              "공시 수집 실패 — ROE 산출 불가")
        check("C 비고: 모르는 상태는 기계 문자열을 내보내지 않는다",
              _rn({"kind": "xbrl", "status": "weird_new_state"}), "ROE 미산출")
    _fr = getattr(_fa, "foreign_roe_from", None)
    if _fr is None:
        check("C ROE: 해외 대차대조표 파서(foreign_roe_from) 존재", None, "foreign_roe_from")
    else:
        # yfinance 행 이름 그대로 — 한국·대만은 영업권 행 없이 '영업권+무형' 합계만 온다
        _roe, _bas = _fr({"Stockholders Equity": 424.19e9,
                          "Goodwill And Other Intangible Assets": 97.509e9}, 96.635e9)
        check("C ROE: 해외 — 합계 행만 있어도 산출", _roe, round(96.635 / (424.19 - 97.509), 4))
        check("C ROE: 해외 — 'GAAP 미조정' 기준을 싣는다", _bas.get("basis"), "GAAP 미조정")
        check("C ROE: 해외 — 자본 행이 없으면 결측(0 치환 금지)",
              _fr({"Goodwill": 1e9}, 5e9)[0], None)
    # 미국 경로 — 희석주식수 태그가 없어도(AVGO·GOOG 모양) ROE 가 조용히 0 이 되지 않는다.
    # 예전엔 ROE 분자를 'EPS × 최신 희석주식수 태그'로 되돌려 만들었는데 태그가 없으면
    # 0 이 되어 `if ni_ttm:` 에서 **비고도 없이** 빠졌다. 분자는 TTM 창 합계 그대로 쓴다.
    _xr = getattr(_fa, "xbrl_roe", None)
    if _xr is None:
        check("C ROE: 미국 경로(xbrl_roe) 존재", None, "xbrl_roe")
    else:
        _flat = _fa.flatten({"bs": [
            {"concept": "us-gaap_StockholdersEquity", "value": 100.0},
            {"concept": "us-gaap_Goodwill", "value": 30.0}]})
        _win4 = [(2026, q, {"adj": 5.0}, "w", None) for q in (4, 3, 2, 1)]
        check("C ROE: 희석주식수 태그 없이도 창 합계로 산출",
              _xr(_flat, _win4)[0], round(20.0 / 70.0, 4))
    # 사람 취재 경로 — cagr3y_human 과 같은 규약(자동값이 있으면 자동 우선)
    _rh = {"value": 25.0, "unit": "%", "basis": "연차보고서 유형자본 기준",
           "source": "사람 취재", "asof": "2026-09-25", "confidence": "중"}
    check("C 취재: 자동이 없으면 사람 값(단위 % 해석)",
          _fb.measure_bench({"ticker": "X", "type": "씨즈형",
                             "buffett": {"roe_tangible_human": _rh}},
                            100.0, {"UST10": 4.0})["roe_tangible"], 0.25)
    check("C 취재: 자동이 있으면 자동이 이긴다",
          _fb.measure_bench({"ticker": "X", "type": "씨즈형",
                             "buffett": {"roe_tangible": 0.31, "roe_tangible_human": _rh}},
                            100.0, {"UST10": 4.0})["roe_tangible"], 0.31)
    check("C 취재: 병합 스키마에 등재(roe_tangible_human·roe_basis)",
          ("roe_tangible_human" in _bl.FIELDS, "roe_basis" in _bl.FIELDS), (True, True))
    _gb2 = _fb.measure_bench({"ticker": "TSMX", "type": "x",
                              "buffett": {"roe_tangible": 0.3459,
                                          "roe_basis": {"kind": "yfinance", "status": "ok",
                                                        "basis": "GAAP 미조정"}}},
                             100.0, {"UST10": 4.0})
    check("C 표시: 해외 자동 ROE 는 'GAAP 미조정' 표식 상태를 싣는다",
          _gb2.get("roe_unadjusted"), True)

    # ── 레전드 측정층은 34종 전원 시세를 부른다 (2026-09-05) ─────────────────
    # 막으려던 것: **시차 관측의 원칙 바구니가 레전드 판정까지 막는 것.**
    # '추정불가 = 시세 미호출' 은 괴리(정당 MAX)를 안 매기겠다는 시차 쪽 규칙인데,
    # 두 자로가 한 루프를 공유해 레전드까지 같이 막혔다 — NVDA·AVGO·AMD·TSLA 는
    # 이익도 성장률도 있는데 주가가 없어 영원히 미검정이었다.
    # 판정부 단위 테스트로는 절대 안 잡힌다(measure_bench 는 멀쩡했다). main() 을 돌린다.
    import tempfile as _tf, pathlib as _pl
    _o_main = (_fb.CFG_PATH, _fb.OUT_PATH, _fb.fetch_price, _fb.collect_rates,
               _fb.load_prev)
    _tmp2 = _pl.Path(_tf.mkdtemp())
    _called = []
    try:
        _cfg2 = {"version": "t", "zones": {}, "items": [
            {"ticker": "HARD", "name": "추정불가별", "type": "추정불가", "fair_max": None,
             "rationale": "r", "buffett": {"eps_adj_ttm": {"value": 5.0},
                                           "g_cagr3y": 0.10, "g_forward": 0.10}},
            {"ticker": "NORM", "name": "보통별", "type": "씨즈형", "fair_max": 25,
             "rationale": "r", "forward_eps": 5.0,
             "buffett": {"eps_adj_ttm": {"value": 5.0},
                         "g_cagr3y": 0.10, "g_forward": 0.10}}]}
        (_tmp2 / "cfg.json").write_text(_json.dumps(_cfg2, ensure_ascii=False),
                                        encoding="utf-8")
        _fb.CFG_PATH = _tmp2 / "cfg.json"
        _fb.OUT_PATH = _tmp2 / "out.json"
        _fb.load_prev = lambda: {}
        _fb.collect_rates = lambda prev: ({"UST10": 4.75, "source": "t", "as_of": "",
                                           "note": ""}, "ok")
        def _fp(tk, tries=3):
            _called.append(tk)
            return (100.0, "USD")
        _fb.fetch_price = _fp
        _fb.main()
        _out = _json.loads(_fb.OUT_PATH.read_text(encoding="utf-8"))
        _rows = {x["ticker"]: x for x in _out["items"]}
        check("전원 시세: 추정불가 종목도 시세를 부른다", sorted(_called), ["HARD", "NORM"])
        # ey 0.05 × 1.1^10 = 쿠폰 12.97% · 국채 4.75% → 3배(14.25%) 미만·1.5배 이상
        check("전원 시세: 추정불가 종목에 주가·쿠폰 경로가 열린다",
              (_rows["HARD"]["bench"]["price"], _rows["HARD"]["bench"]["coupon10y"],
               _rows["HARD"]["bench"]["zone_buffett"]), (100.0, 0.129687, "prove_growth"))
        check("전원 시세: 열린 뒤에는 '시세 미확보' 비고가 사라진다",
              _rows["HARD"]["bench"]["note"], "")
        check("전원 시세: 시세를 한 종목당 한 번만 부른다(중복 호출 없음)",
              len(_called), len(set(_called)))
        # 시차 관측 회귀 0 — 바구니 종목은 여전히 괴리·P/E 를 매기지 않는다
        check("시차 회귀: 원칙 바구니는 여전히 괴리 미산출",
              (_rows["HARD"]["basis"], _rows["HARD"]["gap"], _rows["HARD"]["pe"]),
              ("too_hard", None, None))
        check("시차 회귀: 보통 종목은 그대로 괴리를 매긴다",
              (_rows["NORM"]["basis"], _rows["NORM"]["gap"] is not None), ("forward", True))
    finally:
        (_fb.CFG_PATH, _fb.OUT_PATH, _fb.fetch_price, _fb.collect_rates,
         _fb.load_prev) = _o_main
        import shutil as _sh2
        _sh2.rmtree(_tmp2, ignore_errors=True)

    # ── 비고는 상태에서 생성한다 (2026-09-05 '10년물 없음' 오표기) ────────────
    # 막으려던 것: 결측 원인이 실제와 다른 문구로 나가는 것. 그래서 **원인별로**
    # 검사한다 — 하나의 대표 문구가 아니라 그 상태에서 나와야 할 이름 그대로.
    _UN = _fb.untested_note
    check("비고: 주가만 없으면 시세 미확보",
          _UN(None, 5.0, 0.10, 4.75, "UST10", None), "시세 미확보")
    check("비고: 이익만 없으면 취재 대기",
          _UN(100.0, None, 0.10, 4.75, "UST10", None),
          "취재 대기 — 12개월 조정이익 미확보")
    check("비고: 성장률만 없으면 성장률 가정 미확보",
          _UN(100.0, 5.0, None, 4.75, "UST10", None),
          "성장률 가정 미확보(3y CAGR·전망 중 결측)")
    check("비고: 금리 없을 때만 10년물 없음",
          _UN(100.0, 5.0, 0.10, None, "UST10", None), "10년물 없음")
    check("비고: 미배선 시장은 그 시장 이름으로",
          _UN(100.0, 5.0, 0.10, None, "KTB10", None), "한국 10년물 미배선(v1)")
    check("비고: 여러 칸이 비면 전부 이름을 부른다",
          _UN(None, None, 0.10, 4.75, "UST10", None),
          "시세 미확보 · 취재 대기 — 12개월 조정이익 미확보")
    check("비고: 기계 필드명은 문구에 없다",
          any(k in _UN(None, None, None, None, "UST10", None)
              for k in ("eps_adj_ttm", "coupon10y", "g_used", "guard")), False)
    # 실전 회귀 — NVDA 모양(주가만 없음)에 '10년물 없음'이 다시 붙으면 실패한다
    _nvda = {"ticker": "NVDA", "type": "추정불가",
             "buffett": {"eps_adj_ttm": {"value": 5.73}, "g_cagr3y": 1.62,
                         "g_forward": {"value": 23.7, "unit": "%"}}}
    _nb = _fb.measure_bench(_nvda, None, {"UST10": 4.77})
    check("비고: 금리가 있는데 주가가 없으면 10년물 탓을 하지 않는다",
          _nb["note"], "시세 미확보")
    check("비고: 시클리컬 가드 문구에도 기계 필드명 없음",
          "coupon10y" in _fb.measure_bench(
              {"ticker": "X", "type": "시클리컬",
               "buffett": {"cyclical_peak_guard": True}}, 100.0,
              {"UST10": 4.75})["note"], False)

    # 통과 가격 — 쿠폰이 국채×3 과 같아지는 주가 (수동 대조)
    #   eps 10 · g 10% · 10y 4.75% → 10×1.1^10 ÷ 0.1425 = 25.9374/0.1425 = 182.02
    check("통과가격: 역산 검산", _fb.pass_price(10.0, 0.10, 4.75), 182.02)
    check("통과가격: 그 가격에서 쿠폰이 정확히 허들",
          round(_fb.coupon_10y(10.0 / 181.98, 0.10), 4), round(3 * 0.0475, 4))
    check("통과가격: g 없으면 없음", _fb.pass_price(10.0, None, 4.75), None)
    check("통과가격: 적자면 없음", _fb.pass_price(-1.0, 0.10, 4.75), None)
    check("통과가격: 금리 없으면 없음", _fb.pass_price(10.0, 0.10, None), None)

    # ── 오너어닝·전환율 (2026-09-02) — 빈칸의 원인은 '숫자가 없어서' 였다 ──────
    # 스카우트는 display 만 주고 A/B/C 의 **수치를 주지 않는다**(줄 수도 없다).
    # 그래서 전환율에 넣을 값 자체가 없었다 → 기계가 A안을 계산한다.
    def _w(adj, ni, dna, capex):
        return [(2026, q, {"adj": adj, "ni": ni, "dna": dna, "capex": capex}, "w")
                for q in (4, 3, 2, 1)]
    _oe, _cv = _fa.owner_earnings_from(_w(8.75, 8.0, 1.75, 11.225))
    check("오너어닝: A안 = 순이익(유지캐펙스 = 감가상각)", _oe["variants"]["A"]["value"], 32.0)
    check("오너어닝: C안 = 순이익 + 감가상각 − 캐펙스 전액",
          _oe["variants"]["C"]["value"], -5.9)
    check("오너어닝: 기본 표시는 A안", _oe["display"], "A")
    check("전환율: A ÷ 조정순이익", _cv["value"], round(32.0 / 35.0, 4))
    check("전환율: 근거를 남긴다", "감가상각" in _cv["basis"], True)
    # 감가상각·캐펙스가 없으면 C안은 만들지 않는다 (0 으로 치지 않는다)
    _oe2, _cv2 = _fa.owner_earnings_from(_w(8.75, 8.0, None, None))
    check("오너어닝: 감가상각 없으면 C안 없음", "C" in _oe2["variants"], False)
    check("전환율: A안만으로도 산출된다", _cv2 is not None, True)
    check("오너어닝: 순이익이 없으면 아예 없음",
          _fa.owner_earnings_from(_w(8.75, None, 1.0, 1.0)), (None, None))
    _oe3, _cv3 = _fa.owner_earnings_from(_w(-1.0, 8.0, 1.0, 1.0))
    check("전환율: 조정순이익이 0 이하면 비율은 없음(의미 상실)", _cv3, None)
    # 사람이 지정한 전환율이 자동값을 이긴다 (GOOG C안 유지)
    _mg, _og = _bl.merge_block({"conversion": {"value": -0.17, "basis": "C/조정순이익 35.0"}},
                               {"conversion": {"value": 0.914, "basis": "A ÷ 조정순이익"}})
    check("전환율: 사람 지정이 자동을 이긴다",
          (_mg["conversion"]["value"], _og["conversion"]), (-0.17, "human"))

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
    # 2026-09-02 NaN 사고 회귀 — min(x, NaN) 이 x 를 돌려주는 바람에 금지된 과거
    # CAGR 이 조용히 g 로 쓰였다(AMZN 쿠폰 3381% '통과'). 없는 값은 반드시 None.
    _nan = float("nan")
    check("NaN: to_num 은 NaN 을 숫자로 보지 않는다", _fa.to_num(_nan), None)
    check("NaN: 무한대도 아니다", _fa.to_num(float("inf")), None)
    check("NaN: 문자열 nan 도 막는다", _fa.to_num("nan"), None)
    check("NaN: 전망 표에서 NaN 행은 고르지 않는다",
          _fa.ltg_from_growth_table([("+5y", _nan)]), None)
    check("NaN: pick_g 는 NaN 을 없는 값으로 — 과거 CAGR 단독 사용 금지",
          _fb.pick_g(0.09, _nan), None)
    check("NaN: 정상값은 그대로", _fb.pick_g(0.09, 0.06), 0.06)
    _nanb = {"ticker": "X", "type": "씨즈형",
             "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.64,
                         "g_forward": _nan}}
    check("NaN: 전망이 NaN 이면 존은 미검정",
          _fb.measure_bench(_nanb, 100.0, {"UST10": 4.75})["zone_buffett"], "untested")

    # 상식 가드 — 단기 반등률이 10년 복리 가정 자리에 앉는 것을 막는다
    check("가드: 40% 이하는 채택", _fa.vet_forward_growth(0.40, "src")[0], 0.40)
    check("가드: 40% 초과는 버린다(깎지 않는다)", _fa.vet_forward_growth(0.55, "src")[0], None)
    check("가드: 사유를 note 로 남긴다",
          _fa.vet_forward_growth(0.55, "src")[2], "전망치 이상(55%) — 취재 필요")
    check("가드: 없는 값은 그대로 없음", _fa.vet_forward_growth(None, None)[0], None)
    check("가드: 음수 전망은 막지 않는다(역성장은 정상 관측)",
          _fa.vet_forward_growth(-0.10, "src")[0], -0.10)
    _wild = {"ticker": "X", "type": "씨즈형",
             "buffett": {"eps_adj_ttm": {"value": 10.0}, "g_cagr3y": 0.30,
                         "g_forward": None}}
    check("가드: 전망이 버려지면 존은 미검정(과거 CAGR 단독 금지)",
          _fb.measure_bench(_wild, 100.0, {"UST10": 4.75})["zone_buffett"], "untested")

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

    # 워크플로 YAML 도 같은 규율을 지키는지 — **파이썬만 고치고 YAML 을 놓쳤던**
    # 것이 2026-09-02 누수의 형태다(경성 실패 경보에 공개 채널 폴백이 남아 있었다).
    # 방지선은 코드가 아니라 '발송 경로 전체'를 봐야 한다.
    _wf = (Path(__file__).parent.parent / ".github" / "workflows" / "daily-update.yml")
    if _wf.exists():
        _y = _wf.read_text(encoding="utf-8")
        _pub = [ln.strip() for ln in _y.splitlines()
                if "secrets.TELEGRAM_CHAT_ID" in ln or "@stayhungryasi" in ln]
        _pub = [ln for ln in _pub if not ln.lstrip().startswith("#")]
        check("채널: 워크플로에서 공개 채널을 쓰는 곳은 브리핑 한 곳뿐", len(_pub), 1)
        check("채널: 그 한 곳이 TELEGRAM_CHAT_ID 배선인지",
              _pub[0].startswith("TELEGRAM_CHAT_ID:") if _pub else "", True)
        check("채널: 폴백 연산자(||)로 공개 채널을 끌어오지 않는다",
              any("TELEGRAM_ALERT_CHAT_ID ||" in ln for ln in _y.splitlines()), False)
        # 관문 배선 — 커밋 **앞**에 있고, 실패를 삼키지 않아야 한다.
        # continue-on-error 가 붙으면 관문은 로그만 남기고 커밋은 그대로 나간다
        # (2026-08-21 규칙 ④ '||' 가 실패를 삼키는 것과 같은 형태의 사고다).
        _gi = _y.find("scripts/verify_pages.py")
        _ci = _y.find("- name: Commit if changed")
        check("관문 배선: 워크플로에 등록됐다", _gi >= 0, True)
        check("관문 배선: 커밋 스텝보다 앞에 있다", 0 <= _gi < _ci, True)
        check("관문 배선: 실패를 삼키지 않는다(continue-on-error 없음)",
              "continue-on-error" in _y[_gi:_ci] or "||" in _y[_gi:_ci], False)

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
    # 신선도 폐기는 취재 결과가 아니라 **눈금이 비었다는 사건** — 같은 DM 묶음에 싣는다.
    _stl = [{"ticker": "GOOG", "period": "2012Q3"}]
    check("스카우트: 폐기가 묶음에 종목·분기까지 실린다",
          _bs.digest([], _stl), "XBRL 신선도 미달 1종 폐기: GOOG(2012Q3)")
    check("스카우트: 갱신과 폐기가 한 묶음 두 줄",
          _bs.digest([("AAPL", ["risk5"])], _stl).count(chr(10)), 1)
    # 발송까지 실제로 흘러오는지 — 가짜 송신부를 꽂아 본문과 상태를 함께 본다.
    import sys as _sys
    import types as _ty
    _sent_box = []
    _fake = _ty.ModuleType("send_telegram_briefing")
    _fake.send_telegram = lambda tok, chat, text: _sent_box.append((chat, text))
    _fake.esc = lambda t: t
    _o_mod = _sys.modules.get("send_telegram_briefing")
    _sv2 = {k: _os.environ.get(k) for k in
            ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALERT_CHAT_ID", "TELEGRAM_CHAT_ID")}
    try:
        _sys.modules["send_telegram_briefing"] = _fake
        _os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
        _os.environ["TELEGRAM_ALERT_CHAT_ID"] = "12345"
        _os.environ.pop("TELEGRAM_CHAT_ID", None)
        _st2 = {}
        check("스카우트: 취재 갱신 0 이어도 폐기가 있으면 발송한다",
              _bs.notify([], _st2, "2026-09-05", _stl), True)
        check("스카우트: 폐기 DM 은 소장 DM 으로만 나간다",
              [c for c, _t in _sent_box], ["12345"])
        check("스카우트: 폐기 DM 본문에 종목·분기가 있다",
              "GOOG(2012Q3)" in _sent_box[0][1], True)
        check("스카우트: 같은 날 두 번째는 침묵",
              _bs.notify([], _st2, "2026-09-05", _stl), False)
        check("스카우트: 갱신도 폐기도 없으면 침묵",
              _bs.notify([], {}, "2026-09-05", []), False)
    finally:
        if _o_mod is None:
            _sys.modules.pop("send_telegram_briefing", None)
        else:
            _sys.modules["send_telegram_briefing"] = _o_mod
        for _k, _v in _sv2.items():
            if _v is None:
                _os.environ.pop(_k, None)
            else:
                _os.environ[_k] = _v

    # ── 무인 탐사선 준법 필터 (2026-09-03) ─────────────────────────────────
    # 막으려는 것 한 문장: **"무엇을 얼마나 언제 사라"는 투자 권고가 공개 화면에
    # 실리는 것.** 그래서 낱말이 아니라 **그 정의**로 검사한다 — '저가 경쟁 진입',
    # '매출 내 비중 42%' 는 권고가 아니라 분석이고, 그것까지 자르면 오차단이다
    # (2026-08-22 동행 에세이 오차단 교훈).
    import agent_report as _ag
    _NL = chr(10)          # 생성 코드에 개행 이스케이프를 쓰지 않는다
    _doc = _NL.join(["## PHASE 3", "분석은 남는다", "## PHASE 4 — 투자 판단",
                     "여기서 사라고 말한다", "## PHASE 5", "규칙은 남는다"])
    _kept = _ag.drop_phase4(_doc)[0]
    check("탐사선: PHASE 4 본문 제거", "여기서 사라고" in _kept, False)
    check("탐사선: 앞뒤 절은 남는다",
          ("분석은 남는다" in _kept, "규칙은 남는다" in _kept), (True, True))
    check("탐사선: 잘라낸 자리에 안내를 남긴다(조용히 사라지지 않는다)",
          "제외했습니다" in _kept, True)
    check("탐사선: 권고 문단은 제외", _ag.is_reco("권고: 관망(비중 0%)"), True)
    check("탐사선: 분할 매수 문단은 제외", _ag.is_reco("$80 이하에서만 분할 매수"), True)
    check("탐사선: 최대 비중 문단은 제외", _ag.is_reco("최대 비중 = 2%"), True)
    check("탐사선: 경쟁사 시장 진입은 분석이다(오차단 금지)",
          _ag.is_reco("ARPU $60 미만이면 저가 경쟁 진입 확정"), False)
    check("탐사선: 매출 비중도 분석이다", _ag.is_reco("Connectivity 매출 내 비중 42%"), False)
    check("탐사선: 매수와 붙은 진입은 권고", _ag.is_reco("락업 해제 후 분할 진입 개시"), True)
    # 표는 행 단위로 거른다 — 권고 한 줄 때문에 분석 표를 통째로 지우지 않는다
    _tbl = _NL.join(["| 방식 | 판단 |", "|---|---|",
                     "| 관망 | 현재의 기본 권고 |", "| 지표 | 매출 내 비중 42% |"])
    _t2 = _ag.drop_reco_blocks(_tbl)[0]
    check("탐사선: 표는 권고 행만 빠진다",
          ("기본 권고" in _t2, "비중 42%" in _t2), (False, True))

    # 실제 보고서로 — 산출물에 권고가 남지 않는지
    _rp = Path(__file__).parent.parent / "agent-research" / "reports" / "2026-09.md"
    if _rp.exists():
        _body, _info = _ag.sanitize(_rp.read_text(encoding="utf-8"))
        check("탐사선: 실보고서에서 PHASE 4 제거됨", _info["phase4"], True)
        for _w in ("권고", "분할", "비중 2%", "PHASE 4"):
            check("탐사선: 산출물에 '" + _w + "' 0건", _body.count(_w), 0)
        # 정의 기준 최종 확인 — 남은 어떤 줄도 '권고'가 아니어야 한다
        check("탐사선: 남은 줄 중 권고 형태 0건",
              [ln.strip()[:50] for ln in _body.splitlines() if _ag.is_reco(ln)], [])
        # 오차단 감시 — 필터가 본문을 통째로 삼키지 않았는지
        check("탐사선: 분석은 살아남는다(본문 15k 이상)", len(_body) > 15000, True)

    # ── 준법 분리 ⓑ안 (2026-09-04) — 원본 단계에서 이미 갈라져 있는가 ────────
    # 렌더 필터는 **두 번째 겹**이다. 첫 겹은 원본이 애초에 권고를 담지 않는 것.
    # 필터에만 기대면 렌더 경로가 하나 늘어나는 날(RSS·요약·API) 그리로 샌다.
    _reports = sorted((Path(__file__).parent.parent / "agent-research" / "reports")
                      .glob("*.md")) if (Path(__file__).parent.parent /
                                         "agent-research" / "reports").exists() else []
    _public = [f for f in _reports if not f.stem.endswith("_private")
               and f.stem != "changelog"]
    for _f in _public:
        _t = _f.read_text(encoding="utf-8")
        for _w in ("권고", "분할", "포지션 규모", "진입 방식", "비중 2%"):
            check("분리: 본 보고서 " + _f.stem + " 에 '" + _w + "' 0건", _t.count(_w), 0)
    check("분리: 공개 보고서가 실제로 있다", len(_public) > 0, True)
    # 별지는 저장소에 들어오면 안 된다 — .gitignore 가 아니라 '없음'으로 확인한다
    _ignored = (Path(__file__).parent.parent / ".gitignore")
    check("분리: .gitignore 에 별지 규칙",
          "_private.md" in _ignored.read_text(encoding="utf-8") if _ignored.exists() else False,
          True)
    # 마스터 프롬프트가 다음 회차에도 같은 규칙을 지키게 하는가
    _mp = Path(__file__).parent.parent / "agent-research" / "prompts" / "spacex_master.md"
    if _mp.exists():
        check("분리: 마스터 프롬프트에 준법 분리 규칙",
              "_private.md" in _mp.read_text(encoding="utf-8"), True)

    # ── 리서치 보존 병합 (2026-09-03 사고 재현) ────────────────────────────
    # 사고: 구글뉴스가 33종 중 21종에 비200 을 돌려줬는데, 보호 장치가
    # "전 종목이 0건일 때만" 기존 파일을 지키게 돼 있었다(if ok == 0).
    # 12종이 성공했으므로 파일 전체가 새로 쓰였고 실패한 21종의 기사가 **삭제됐다.**
    # 막으려는 것 한 문장: **수집 실패가 역사를 지우는 것.**
    import fetch_research as _fr

    _prev = {"generated_label": "2026.09.03 20:19", "stocks": [
        {"ticker": "NVDA", "articles": [{"title": "구 기사 A"}]},
        {"ticker": "AAPL", "articles": [{"title": "구 기사 B"}]},
        {"ticker": "NEW1", "articles": []},
    ]}
    # 이번 회차: NVDA 실패(0건) · AAPL 성공 · NEW1 첫 성공
    _fresh = [{"ticker": "NVDA", "articles": []},
              {"ticker": "AAPL", "articles": [{"title": "새 기사"}]},
              {"ticker": "NEW1", "articles": [{"title": "첫 기사"}]}]
    _m, _kept = _fr.merge_preserve(_prev, _fresh, "2026.09.03 22:35")
    _by = {x["ticker"]: x for x in _m}
    check("리서치: 실패한 티커의 기존 기사가 살아남는다",
          _by["NVDA"]["articles"], [{"title": "구 기사 A"}])
    check("리서치: 보존된 티커를 알려준다", _kept, ["NVDA"])
    check("리서치: 성공한 티커는 새 기사로 갱신",
          _by["AAPL"]["articles"], [{"title": "새 기사"}])
    check("리서치: 보존분은 수집 시점을 유지한다(표시층이 낡음을 판정할 근거)",
          _by["NVDA"]["articles_asof"], "2026.09.03 20:19")
    check("리서치: 갱신분은 이번 회차 시점", _by["AAPL"]["articles_asof"], "2026.09.03 22:35")
    check("리서치: 원래 없던 종목은 새로 채워진다",
          _by["NEW1"]["articles"], [{"title": "첫 기사"}])
    # 사고 그대로의 비율 — 21/33 실패해도 한 건도 잃지 않아야 한다
    _prev33 = {"generated_label": "L", "stocks":
               [{"ticker": f"T{i}", "articles": [{"title": f"a{i}"}]} for i in range(33)]}
    _fresh33 = [{"ticker": f"T{i}", "articles": ([] if i < 21 else [{"title": "new"}])}
                for i in range(33)]
    _m33, _k33 = _fr.merge_preserve(_prev33, _fresh33, "N")
    check("리서치: 21/33 실패해도 빈 종목 0",
          sum(1 for x in _m33 if not x.get("articles")), 0)
    check("리서치: 보존 21종", len(_k33), 21)
    # 이전 기록이 아예 없으면 보존할 것도 없다(빈 채로 두고 거짓말하지 않는다)
    check("리서치: 과거가 없으면 빈 채로 둔다",
          _fr.merge_preserve({}, [{"ticker": "X", "articles": []}], "N")[0][0]["articles"], [])

    # 배선 실측 — 함수가 옳아도 main() 이 그것을 부르지 않으면 실전만 틀린다.
    # (2026-08-31 sentinel 에서 배운 것과 같은 함정: 단위 테스트는 전부 통과하는데
    #  배선이 끊겨 있으면 사고는 그대로 난다.)
    import json as _js3, shutil as _sh3
    _tmpd = Path(__file__).parent.parent / "data" / "_selftest_research"
    _o_out, _o_tg = _fr.OUT_PATH, _fr.collect_targets
    _o_tr, _o_sm, _o_parse = _fr.translate_titles_ko, _fr.summarize_articles_ko, _fr.parse_rss
    import feed_client as _fc3
    _o_fetch, _o_rec, _o_flush = _fc3.fetch, _fc3.record, _fc3.flush
    try:
        _tmpd.mkdir(parents=True, exist_ok=True)
        _fr.OUT_PATH = _tmpd / "research.json"
        _fr.OUT_PATH.write_text(_js3.dumps(
            {"generated_label": "2026.09.03 20:19", "stocks": [
                {"ticker": "AAA", "articles": [{"title": "지켜져야 할 기사"}]},
                {"ticker": "BBB", "articles": [{"title": "구 기사"}]}]},
            ensure_ascii=False), encoding="utf-8")
        _fr.collect_targets = lambda: [{"ticker": "AAA", "name": "AAA"},
                                       {"ticker": "BBB", "name": "BBB"}]
        _fr.translate_titles_ko = lambda x: None
        _fr.summarize_articles_ko = lambda x: None
        # AAA 는 실패(본문 없음), BBB 는 성공 — 사고와 같은 부분 실패 상황
        _fc3.fetch = lambda url, label, kind, **k: (
            (None, "http_error", 429) if label == "AAA" else ("<xml/>", "ok", 200))
        _fr.parse_rss = lambda t, limit=None: ([] if not t else [{"title": "새 기사"}])
        _fc3.record = lambda *a, **k: None
        _fc3.flush = lambda: None
        _fr.main()
        _res = _js3.loads(_fr.OUT_PATH.read_text(encoding="utf-8"))
        _rb = {x["ticker"]: x for x in _res["stocks"]}
        check("리서치(배선): 실패 종목의 기사가 파일에 살아남는다",
              _rb["AAA"]["articles"], [{"title": "지켜져야 할 기사"}])
        check("리서치(배선): 성공 종목은 갱신된다",
              _rb["BBB"]["articles"], [{"title": "새 기사"}])
    except SystemExit:
        check("리서치(배선): 부분 실패에서 조기 종료하면 안 된다", "SystemExit", "")
    finally:
        _fr.OUT_PATH, _fr.collect_targets = _o_out, _o_tg
        _fr.translate_titles_ko, _fr.summarize_articles_ko = _o_tr, _o_sm
        _fr.parse_rss = _o_parse
        _fc3.fetch, _fc3.record, _fc3.flush = _o_fetch, _o_rec, _o_flush
        _sh3.rmtree(_tmpd, ignore_errors=True)

    # ── 관측노트(parallax_journal) — 버핏존 전이 기록 규율 ──────────────────
    import parallax_journal as _pj
    check("노트: 괴리존과 서명이 겹치지 않는다",
          _pj.sig("A", "x", "y", "2026-08-30") != _pj.sig("A", "x", "y", "2026-08-30", "buffett"),
          True)

    # ── 버핏존 전이 요동 (2026-09-25 legend-audit A) ────────────────────────
    # 막으려는 것 한 문장: **판정선 위 종목의 하루짜리 흔들림이 사건으로 기록·발송되는 것.**
    # ① 원인 오분류 재현 — TSM 9/24 23:25 → 9/25 10:50 실측값 그대로.
    #    EPS 는 환율로 13.44→13.58 흔들렸을 뿐(통과 쪽으로 +1.0%), 존을 민 것은
    #    금리 4.96→5.11(+15bp)·주가 +1.5% 다. 예전 규칙은 EPS 가 달라졌다는 이유만으로
    #    scale 을 매겨 이 전이를 기록 대상에서 조용히 뺐다.
    _tsm0 = {"eps_adj_ttm": 13.44, "g_used": 0.175, "guard": False,
             "price": 444.33, "rate10y": 4.96}
    _tsm1 = {"eps_adj_ttm": 13.58, "g_used": 0.175, "guard": False,
             "price": 451.15, "rate10y": 5.11}
    check("A 원인: TSM 9/25 는 금리(EPS 환율 흔들림을 scale 로 오분류 금지)",
          _fb.classify_cause(_tsm0, _tsm1), "rate")
    check("A 원인: EPS 가 크게 바뀌면 여전히 scale(재측정)",
          _fb.classify_cause(_tsm0, dict(_tsm1, eps_adj_ttm=9.0)), "scale")
    check("A 원인: g 가 바뀌면 scale",
          _fb.classify_cause(_tsm0, dict(_tsm0, g_used=0.10, price=450.0)), "scale")
    check("A 원인: 이익 결측 전환은 scale(자가 끊김)",
          _fb.classify_cause(_tsm0, dict(_tsm0, eps_adj_ttm=None)), "scale")
    # ② 경계 여유 — TSM 9/25 실측: 현재가가 통과가격보다 1.5% 높다
    _tsm_it = {"ticker": "TSM", "type": "플라이트세이프티형",
               "buffett": {"eps_adj_ttm": {"value": 13.58}, "g_cagr3y": 0.191,
                           "g_forward": 0.175}}
    _tb = _fb.measure_bench(_tsm_it, 451.15, {"UST10": 5.11})
    check("A 경계: TSM 은 통과선 기준", _tb.get("edge_line"), "pass")
    check("A 경계: TSM 여유 +1.5%", round(_tb.get("edge_margin") or 0, 3), 0.015)
    check("A 경계: ±5% 안쪽이면 경계", _tb.get("borderline"), True)
    check("A 경계: 멀리 떨어지면 경계 아님",
          _fb.measure_bench(_tsm_it, 300.0, {"UST10": 5.11}).get("borderline"), False)

    # ③ 2거래일 확정 — 9/21(월)~9/25(금) 평일. 하루짜리 역전은 사건이 아니다.
    def _bi(z, px, r=4.96, eps=13.44, t="TSM", margin=0.01):
        return [{"ticker": t, "bench": {
            "zone_buffett": z, "price": px, "rate10y": r, "eps_adj_ttm": eps,
            "g_used": 0.175, "guard": False, "coupon10y": 0.15,
            "edge_line": "pass", "edge_margin": margin}}]
    _step = getattr(_pj, "step_buffett", None)
    if _step is None:
        check("A 확정: step_buffett 존재(2거래일 확정 규칙)", None, "step_buffett")
    else:
        _s, _e, _l = _step(None, _bi("pass", 440.0), "2026-09-21")
        check("A 확정: 첫 관측은 기준선만", _e, [])
        _s, _e, _l = _step(_s, _bi("prove_growth", 452.0), "2026-09-22")
        check("A 확정: 1일째는 사건 아님(경계 로그)",
              (_e, any("경계" in x and "1/2" in x for x in _l)), ([], True))
        _s2, _e2, _l2 = _step(_s, _bi("pass", 445.0), "2026-09-23")
        check("A 확정: 1일 만의 역전은 기록 없이 복귀 로그",
              (_e2, any("복귀" in x for x in _l2)), ([], True))
        # 같은 날 세 번 돌아도 확인 일수는 1 — 거래일당 1회
        _s3, _e3, _ = _step(_s, _bi("prove_growth", 452.0), "2026-09-22")
        _s3, _e3, _ = _step(_s3, _bi("prove_growth", 453.0), "2026-09-22")
        check("A 확정: 같은 날 재실행은 확인 일수를 늘리지 않는다",
              (_e3, _s3["cand"]["TSM"]["days"]), ([], 1))
        _s4, _e4, _ = _step(_s, _bi("prove_growth", 455.0), "2026-09-23")
        check("A 확정: 2거래일 연속이면 정확히 1건", len(_e4), 1)
        check("A 확정: 확정 후 기준선이 새 존으로", _s4["zones"]["TSM"], "prove_growth")
        _txt = (_e4 or [{}])[0].get("text", "")
        check("A 문구: 여유%와 원인을 함께 적는다",
              ("통과가격보다 1.0% 높음" in _txt, "원인 주가" in _txt), (True, True))
        check("A 문구: 기계 필드명 0건",
              [k for k in ("coupon10y", "10y×3", "prove_growth", "bond_inferior", "edge_",
                           "rate10y", "eps_adj", "g_used", "guard") if k in _txt], [])
        # 주말은 확인 일수를 움직이지 않는다 — 9/25(금) 1일째 → 9/26(토) 동결 → 9/28(월) 확정
        _w, _, _ = _step(None, _bi("pass", 440.0), "2026-09-24")
        _w, _, _ = _step(_w, _bi("prove_growth", 452.0), "2026-09-25")
        _w, _we, _ = _step(_w, _bi("prove_growth", 452.0), "2026-09-26")
        check("A 확정: 주말 회차는 확정하지 않는다", _we, [])
        _w, _we, _ = _step(_w, _bi("prove_growth", 452.0), "2026-09-28")
        check("A 확정: 다음 평일에 확정", len(_we), 1)
        # 금리 원인 · 10bp 미만 → 이틀이 지나도 경계(기록 보류)
        _r, _, _ = _step(None, _bi("pass", 440.0, r=4.95), "2026-09-21")
        _r, _, _ = _step(_r, _bi("prove_growth", 440.0, r=5.00), "2026-09-22")
        _r, _re, _rl = _step(_r, _bi("prove_growth", 440.0, r=5.02), "2026-09-23")
        check("A 확정: 금리 원인 10bp 미만은 이틀째도 보류",
              (_re, any("10bp" in x for x in _rl)), ([], True))
        _r, _re, _ = _step(_r, _bi("prove_growth", 440.0, r=5.06), "2026-09-24")
        check("A 확정: 금리가 10bp 이상 움직이면 확정", len(_re), 1)
        # 눈금 변경(scale)은 재기준만 — 이틀이 지나도 사건 없음
        _c, _, _ = _step(None, _bi("pass", 440.0), "2026-09-21")
        _c, _ce, _ = _step(_c, _bi("prove_growth", 440.0, eps=9.0), "2026-09-22")
        _c, _ce2, _ = _step(_c, _bi("prove_growth", 440.0, eps=9.0), "2026-09-23")
        check("A 확정: 눈금 변경은 사건 아님(재기준)", (_ce, _ce2), ([], []))
        # 미검정이 낀 전이는 사건 아님(종전 규율 유지)
        _u, _, _ = _step(None, _bi("untested", 440.0), "2026-09-21")
        _u, _ue, _ = _step(_u, _bi("pass", 440.0), "2026-09-22")
        _u, _ue2, _ = _step(_u, _bi("pass", 440.0), "2026-09-23")
        check("A 확정: 미검정→판정 은 사건 아님", (_ue, _ue2), ([], []))
        _u, _ue3, _ = _step(_u, _bi("untested", 440.0), "2026-09-24")
        _u, _ue4, _ = _step(_u, _bi("untested", 440.0), "2026-09-25")
        check("A 확정: 판정→미검정 도 사건 아님", (_ue3, _ue4), ([], []))

    # ④ DM — 소장 DM 전용 · 공개 폴백 없음 · 같은 날 1회
    import types as _ty2
    _fk = _ty2.ModuleType("send_telegram_briefing")
    _fk.send_telegram = lambda tok, chat, text: _dm_box.append((chat, text))
    _fk.esc = lambda s: s
    _dm_box = []
    _notify = getattr(_pj, "notify_dm", None)
    if _notify is None:
        check("A DM: notify_dm 존재", None, "notify_dm")
    else:
        import os as _os3
        _o_mod2 = sys.modules.get("send_telegram_briefing")
        _o_env = {k: _os3.environ.get(k) for k in
                  ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALERT_CHAT_ID", "TELEGRAM_CHAT_ID")}
        try:
            sys.modules["send_telegram_briefing"] = _fk
            _ev1 = [{"ticker": "TSM", "before": "pass", "after": "prove_growth", "text": "t"}]
            _os3.environ["TELEGRAM_BOT_TOKEN"] = "tok"
            _os3.environ["TELEGRAM_CHAT_ID"] = "@public"
            _os3.environ.pop("TELEGRAM_ALERT_CHAT_ID", None)
            _st_dm = {}
            check("A DM: 수신처 없으면 보내지 않는다(공개 폴백 없음)",
                  (_notify(_ev1, _st_dm, "2026-09-25"), _dm_box), (0, []))
            _os3.environ["TELEGRAM_ALERT_CHAT_ID"] = "dm-1"
            check("A DM: DM 으로 1건", (_notify(_ev1, _st_dm, "2026-09-25"),
                                         [c for c, _ in _dm_box]), (1, ["dm-1"]))
            check("A DM: 같은 날 재실행은 재발송 안 함",
                  (_notify(_ev1, _st_dm, "2026-09-25"), len(_dm_box)), (0, 1))
        finally:
            for _k, _v in _o_env.items():
                if _v is None:
                    _os3.environ.pop(_k, None)
                else:
                    _os3.environ[_k] = _v
            if _o_mod2 is None:
                sys.modules.pop("send_telegram_briefing", None)
            else:
                sys.modules["send_telegram_briefing"] = _o_mod2

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
                  "inject_header_fix", "inject_aurora_tokens", "inject_macro_badges")

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

    # ── 빌드 중단 사고: 원자적 교체 (2026-09-05) ─────────────────────────────
    # 막으려는 것 한 문장: **빌드가 중간에 죽어 주입이 빠진 페이지가 실제 경로에
    # 남는 것.** 12페이지 생성은 끝났는데 주입 5종 전에 OSError 로 멈추자, 팔레트도
    # 카운터도 없는 산출물이 작업 트리에 남았다. HTML 은 멀쩡하고 페이지도 열려서
    # 눈으로는 안 잡힌다 — 산출물 검사는 주입 **뒤**에 있어 아예 실행되지 않았다.
    # 그래서 검사를 늘리는 대신 **실제 경로를 건드리는 시점 자체를 옮긴다.**
    #
    # 이 테스트는 실제 빌드를 돌린다(약 8초). 판정부 단위 테스트로는 못 잡는
    # 사고이기 때문이다 — 죽는 자리가 함수 안이 아니라 함수들 **사이**에 있다.
    _MARKERS = ("ud-hdr-refine-v4", "uv-presence", "wide-fix",
                "uv-policy-links", "ud-aurora-global-v1")
    import shutil as _sh3, tempfile as _tf3
    from unittest import mock as _mock3
    _stage = Path(_tf3.mkdtemp())
    _orig_write = Path.write_text

    def _boom(self, data, *a, **kw):
        """journal.html 쓰기에서만 터진다 — 실제 사고와 같은 지점."""
        if self.name == "journal.html":
            raise OSError(22, "Invalid argument")
        return _orig_write(self, data, *a, **kw)

    try:
        # 직전 완성본을 실제 경로에 둔다 — 사고가 나도 이것이 그대로여야 한다
        for _pg in _bs.ALL_PAGES:
            _sh3.copy2(_root / _pg, _stage / _pg)
        _before = {p: (_stage / p).read_bytes() for p in _bs.ALL_PAGES}
        with _mock3.patch.object(_bs, "HERE", _stage),              _mock3.patch.object(Path, "write_text", _boom):
            try:
                _bs.main()
            except OSError:
                pass                      # 사고 재현 — 여기서 죽는 것이 정상이다
        _stripped = sorted(p for p in _bs.ALL_PAGES
                           if any(m not in (_stage / p).read_text(encoding="utf-8")
                                  for m in _MARKERS))
        check("원자적 빌드: 중단돼도 주입 빠진 페이지가 남지 않는다", _stripped, [])
        _changed = sorted(p for p in _bs.ALL_PAGES
                          if (_stage / p).read_bytes() != _before[p])
        check("원자적 빌드: 중단 시 실제 경로는 직전 완성본 그대로", _changed, [])
    finally:
        _sh3.rmtree(_stage, ignore_errors=True)

    # publish() 단위 — 교체 자체가 하는 일과 안 하는 일
    _st2 = Path(_tf3.mkdtemp())
    _dst2 = Path(_tf3.mkdtemp())
    try:
        (_dst2 / "a.html").write_text("old", encoding="utf-8")
        (_st2 / "a.html").write_text("new", encoding="utf-8")
        (_st2 / "b.html").write_text("new-b", encoding="utf-8")
        (_st2 / "keep.json").write_text("{}", encoding="utf-8")
        _n = _bs.publish(_st2, _dst2)
        check("원자적 교체: html 만 옮긴다", (_n, sorted(f.name for f in _dst2.glob("*"))),
              (2, ["a.html", "b.html"]))
        check("원자적 교체: 기존 파일을 새 내용으로 갈아끼운다",
              (_dst2 / "a.html").read_text(encoding="utf-8"), "new")
        check("원자적 교체: 옮긴 파일은 스테이징에 남지 않는다",
              sorted(f.name for f in _st2.glob("*")), ["keep.json"])
    finally:
        _sh3.rmtree(_st2, ignore_errors=True)
        _sh3.rmtree(_dst2, ignore_errors=True)
    # 산출물 검사가 죽으면 교체까지 가지 못한다 (검사 → 교체 순서 고정)
    _stage3 = Path(_tf3.mkdtemp())
    try:
        for _pg in _bs.ALL_PAGES:
            _sh3.copy2(_root / _pg, _stage3 / _pg)
        _before3 = (_stage3 / "index.html").read_bytes()
        with _mock3.patch.object(_bs, "HERE", _stage3),              _mock3.patch.object(_bs, "verify_pages",
                                 side_effect=SystemExit(1)):
            try:
                _bs.main()
            except SystemExit:
                pass
        check("원자적 교체: 산출물 검사 실패 시 실제 경로 불변",
              (_stage3 / "index.html").read_bytes(), _before3)
    finally:
        _sh3.rmtree(_stage3, ignore_errors=True)

    # ── 커밋 전 관문 verify_pages.py (2026-09-05) ────────────────────────────
    # 관문은 감시 대상과 **다른 다리로** 서 있어야 한다 — 같은 함수를 공유하면
    # 그 함수가 죽는 날 검사도 같이 죽는다(2026-08-30 배선 교훈).
    import verify_pages as _vp
    _vp_src = _inspect.getsource(_vp)
    check("관문: build_site 를 import 하지 않는다",
          ("import build_site" in _vp_src or "from build_site" in _vp_src), False)
    check("관문: 마커 6종을 스스로 들고 있다(2026-09-26 시장 지표 띠 추가)",
          len(_vp.MARKERS), 6)
    # 시장 지표 띠의 배지 5개도 페이지에 있어야 한다 — 정상 페이지 픽스처에 함께 싣는다
    _badges = "".join(f'data-mk="{k}"' for k in _vp.MACRO_BADGE_KEYS)
    check("관문: 정상 페이지는 통과",
          _vp.check([("ok.html", "x" + "".join(_vp.MARKERS) + _badges + "</hea" + "d>")]), {})
    _one = ("".join(m for m in _vp.MARKERS if m != "uv-presence") + _badges
            + "</hea" + "d>")
    check("관문: 마커 하나만 빠져도 잡는다",
          _vp.check([("bad.html", _one)]), {"bad.html": ["접속자 카운터"]})
    check("관문: head 끝 태그 없는 큰 파일은 실패로 본다",
          "head 끝 태그 없음" in _vp.check([("x.html", "".join(_vp.MARKERS))])["x.html"],
          True)
    check("관문: 실제 저장소가 지금 통과 상태", _vp.main(), 0)
    # 관문이 실제로 막는가 — 마커를 지운 사본으로 확인(실패할 수 없는 검사는 감시자가 아니다)
    _pgs, _stubs = _vp.targets()
    _broken = [(n, r.replace("ud-aurora-global-v1", "x")) for n, r in _pgs[:1]]
    check("관문: 마커를 지우면 반드시 실패한다", bool(_vp.check(_broken)), True)

    # ── 잠재지배자 명단 요동 (2026-09-25) ────────────────────────────────────
    # 막으려는 것 한 문장: **기준 경계에 선 종목이 하루 단위로 명단을 들락거려,
    # 명단과 주간 이력이 실제로 일어나지 않은 편입·제외를 말하는 것.**
    # 9/10~9/25 에 KIOXIA·ARM 이 하루 단위로 편입/제외를 반복했고, 9/19 이력은
    # "ARM 편입·KIOXIA 제외"라 적었지만 9/21 부터 둘 다 명단에 있었다. 9/13 은
    # 파싱 실패 1건이 그대로 명단 탈락이 되어 13종이었다.
    # 판정부 단위가 아니라 **실제 진입점**(run_live·weekly_history.main)을 모의
    # 네트워크로 돌린다 — 배선이 끊겨도 실패해야 하기 때문이다(2026-08-30 교훈).
    import generate_candidates as _gc
    import weekly_history as _wh
    import feed_client as _fcl
    import json as _jl
    import tempfile as _tfl
    import shutil as _shl
    from unittest import mock as _mkl
    from datetime import datetime as _dtl

    def _lat_card(tk, rank):
        return {"rank": rank, "ticker": tk, "name": "Tick" + tk[1:], "country": "🇺🇸",
                "mc": 900 - int(tk[1:]) * 10, "momentum_1y": 150, "theme": "AI 반도체",
                "story": "-", "auto": True}

    def _lat_sim(days, stats_fn, members, tickers, history_on=None):
        """모의 우주에서 run_live 를 날마다 돌린다.

        stats_fn(i, tk) → (rank, 1Y모멘텀) | None(파싱 실패). 반환: 날별 명단,
        이력 항목(history_on 날), 원장의 latent 기록, 상태 파일.
        """
        tmp = Path(_tfl.mkdtemp())
        d = tmp / "data"
        (d / "snapshots").mkdir(parents=True)
        earth = [{"rank": i, "ticker": f"E{i:02d}", "name": f"E{i:02d}", "mc": 6000 - i}
                 for i in range(1, 21)]
        latest = {"meta": {}, "regions": {"earth": {"stocks": earth}},
                  "latent": [_lat_card(tk, 30 + int(tk[1:]) * 8) for tk in members]}
        (d / "latest.json").write_text(_jl.dumps(latest, ensure_ascii=False), encoding="utf-8")
        uni = {tk: {"name": "Tick" + tk[1:], "ticker": tk, "mc": 900 - int(tk[1:]) * 10,
                    "url": f"https://x.invalid/{tk}/marketcap/", "theme": "AI 반도체"}
               for tk in tickers}
        lists, hist, ledger = [], None, None
        try:
            for i, day in enumerate(days):
                now = _dtl.strptime(day + " 12:00", "%Y-%m-%d %H:%M").replace(tzinfo=_gc.KST)
                cur = _jl.loads((d / "latest.json").read_text(encoding="utf-8"))
                for _k in [k for k in _fcl._buffer if k.startswith("latent:")]:
                    _fcl._buffer.pop(_k)   # 원장 버퍼는 프로세스 전역 — 앞 모의일의 기록을 지운다
                (d / "snapshots" / f"{day}.json").write_text(_jl.dumps(
                    {"date": day, "regions": {"earth": earth},
                     "latent": cur.get("latent", [])}, ensure_ascii=False), encoding="utf-8")

                def _stats(row, _i=i):
                    got = stats_fn(_i, row["ticker"])
                    if got is None:
                        return {"rank": None, "momentum": None, "flag": None, "mc": None}
                    return {"rank": got[0], "momentum": got[1], "flag": "🇺🇸", "mc": None}

                with _mkl.patch.object(_gc, "DATA_DIR", d), \
                        _mkl.patch.object(_gc, "LATEST_PATH", d / "latest.json"), \
                        _mkl.patch.object(_gc, "CRITERIA_PATH", d / "_none.json"), \
                        _mkl.patch.object(_gc, "OVERRIDES_PATH", d / "_none.json"), \
                        _mkl.patch.object(_gc, "PREVIEW_PATH", d / "preview.json"), \
                        _mkl.patch.object(_gc, "STATE_PATH", d / "latent_state.json", create=True), \
                        _mkl.patch.object(_gc, "TODAY", now), \
                        _mkl.patch.object(_gc, "scrape_universe", lambda s: dict(uni)), \
                        _mkl.patch.object(_gc, "stock_stats", _stats), \
                        _mkl.patch.object(_gc, "fetch_multi_momentum",
                                          lambda tk: {"m1": None, "m3": None, "m6": None}), \
                        _mkl.patch.object(_fcl, "STATUS_PATH", d / "fetch_status.json"), \
                        _mkl.patch.object(_gc.time, "sleep", lambda s: None):
                    _gc.run_live()
                lat = _jl.loads((d / "latest.json").read_text(encoding="utf-8"))["latent"]
                lists.append(sorted(c["ticker"] for c in lat))
                if history_on == day:
                    with _mkl.patch.object(_wh, "SNAP_DIR", d / "snapshots"), \
                            _mkl.patch.object(_wh, "LATEST_PATH", d / "latest.json"), \
                            _mkl.patch.object(_wh, "HIST_TOP20_PATH", d / "h20.json"), \
                            _mkl.patch.object(_wh, "HIST_LATENT_PATH", d / "hl.json"), \
                            _mkl.patch.object(_wh, "LATENT_STATE_PATH", d / "latent_state.json",
                                              create=True), \
                            _mkl.patch.object(_wh, "TODAY_KST", now):
                        try:
                            _wh.main()
                        except SystemExit:
                            pass
                    hp = d / "hl.json"
                    hist = _jl.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
            sp = d / "fetch_status.json"
            if sp.exists():
                srcs = _jl.loads(sp.read_text(encoding="utf-8")).get("sources", {})
                ledger = {k: v for k, v in srcs.items() if v.get("kind") == "latent"}
            stp = d / "latent_state.json"
            state = _jl.loads(stp.read_text(encoding="utf-8")) if stp.exists() else None
            return lists, hist, ledger, state, lat
        finally:
            _shl.rmtree(tmp, ignore_errors=True)

    _T14 = [f"T{i:02d}" for i in range(1, 15)]
    _WEEK = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
             "2026-09-21", "2026-09-22"]

    # ⓐ 파싱 실패는 탈락이 아니다 — 전일 값으로 승계 (9/13 13종 사고)
    _base_rank = lambda tk: 30 + int(tk[1:]) * 8
    _la, _, _lg, _, _lat = _lat_sim(
        ["2026-09-14"],
        lambda i, tk: None if tk == "T05" else (_base_rank(tk), 150),
        _T14, _T14)
    check("잠재 ⓐ 파싱 실패 1종: 명단 14종 유지", len(_la[0]), 14)
    _t05 = [c for c in _lat if c["ticker"] == "T05"]
    check("잠재 ⓐ 결측 종목은 전일 값 승계 표시",
          bool(_t05) and _t05[0].get("carried") is True and _t05[0].get("carried_days") == 1,
          True)
    # 3거래일 연속 결측이면 그때 제외 (영구 승계 금지)
    _la3, _, _, _, _ = _lat_sim(
        _WEEK[:3], lambda i, tk: None if tk == "T05" else (_base_rank(tk), 150), _T14, _T14)
    check("잠재 ⓐ 결측 1·2일째 유지 → 3일째 제외",
          ["T05" in x for x in _la3], [True, True, False])
    # 원장 배선: 내용 기준 outcome 이 fetch_status 에 kind=latent 로 남는다
    check("잠재 ⓐ fetch_status 에 선정 결과 등재(ok)",
          [v.get("outcome") for v in (_lg or {}).values()], ["ok"])
    # 조건 통과 0개 → 명단 유지 + 원장에 zero (로그로만 남기지 않는다)
    _lz, _, _lgz, _, _ = _lat_sim(["2026-09-14"], lambda i, tk: (_base_rank(tk), 10),
                                  _T14, _T14)
    check("잠재 ⓐ 통과 0개: 명단 유지", len(_lz[0]), 14)
    check("잠재 ⓐ 통과 0개: 원장에 zero",
          [v.get("outcome") for v in (_lgz or {}).values()], ["zero"])
    # 대량 파싱 실패(구조 붕괴) → 개별 결측이 아니라 장애: 명단 동결 + http_error
    _lm, _, _lgm, _, _ = _lat_sim(["2026-09-14"], lambda i, tk: None, _T14, _T14)
    check("잠재 ⓐ 대량 파싱 실패: 명단 동결 + 원장 http_error",
          (len(_lm[0]), [v.get("outcome") for v in (_lgm or {}).values()]),
          (14, ["http_error"]))
    # 변이(mutation): 원장 기록을 끊으면 위 검사가 **반드시** 실패해야 한다
    with _mkl.patch.object(_fcl, "record", lambda *a, **k: None):
        _, _, _lgx, _, _ = _lat_sim(["2026-09-14"], lambda i, tk: (_base_rank(tk), 150),
                                    _T14, _T14)
    check("잠재 ⓐ 변이: 원장 배선을 끊으면 검사가 잡는다", bool(_lgx), False)


    # ⓑ 경계 요동 — 14·15위가 날마다 뒤집혀도 명단은 흔들리지 않는다
    #   T14(기존)와 T15(도전자)의 순위가 150/151 ↔ 152/149 로 매일 교차한다
    def _flip(i, tk):
        if tk == "T14":
            return (150 if i % 2 == 0 else 152, 150)
        if tk == "T15":
            return (151 if i % 2 == 0 else 149, 150)
        return (30 + int(tk[1:]) * 8, 150)
    _lb, _, _, _stb, _ = _lat_sim(_WEEK[:6], _flip, _T14, _T14 + ["T15"])
    _chg = sum(1 for a, b in zip([sorted(_T14)] + _lb, _lb) if a != b)
    check("잠재 ⓑ 경계 요동 6일: 명단 변동 ≤ 1회", _chg <= 1, True)
    check("잠재 ⓑ 도전자는 대기(상태 파일에만)",
          bool(_stb) and "T15" in (_stb.get("candidates") or {}) and "T15" not in _lb[-1],
          True)
    # 빈자리가 있으면 3거래일 연속 충족 후 편입 (1·2일째는 대기)
    _lv, _, _, _, _ = _lat_sim(_WEEK[:4], lambda i, tk: (30 + int(tk[1:]) * 8, 150),
                               _T14[:13], _T14[:13] + ["T15"])
    check("잠재 ⓑ 빈자리 편입은 3거래일째", ["T15" in x for x in _lv],
          [False, False, True, True])
    # 기존 멤버는 기준 미달 5거래일 연속이어야 제외 (4일째까진 유지)
    _lo, _, _, _, _lato = _lat_sim(
        _WEEK[:6], lambda i, tk: (30 + int(tk[1:]) * 8, 40 if tk == "T03" else 150),
        _T14, _T14)
    check("잠재 ⓑ 미달 멤버는 5거래일째 제외", ["T03" in x for x in _lo],
          [True, True, True, True, False, False])
    # 만석이면 10계단 이상 앞선 상태가 5거래일 이어져야 가장 약한 멤버와 교체
    _ls, _, _, _, _ = _lat_sim(
        _WEEK[:6], lambda i, tk: (60, 150) if tk == "T15" else (30 + int(tk[1:]) * 8, 150),
        _T14, _T14 + ["T15"])
    check("잠재 ⓑ 교체는 5거래일째, 가장 약한 멤버와",
          [("T15" in x, "T14" in x) for x in _ls],
          [(False, True)] * 4 + [(True, False)] * 2)
    # 주말은 거래일이 아니다 — 카운트가 움직이지 않는다
    _lw, _, _, _stw, _ = _lat_sim(["2026-09-19", "2026-09-20"],
                                  lambda i, tk: (30 + int(tk[1:]) * 8, 150),
                                  _T14[:13], _T14[:13] + ["T15"])
    check("잠재 ⓑ 주말은 카운트 동결",
          (_lw[-1] == sorted(_T14[:13]),
           ((_stw or {}).get("candidates") or {}).get("T15", {}).get("streak_in", 0)),
          (True, 0))
    # 하루 3회 full — 같은 날 재실행은 카운트를 한 번만 움직인다(그날 출발점에서 재계산)
    _lr, _, _, _str, _ = _lat_sim(["2026-09-14"] * 3 + ["2026-09-15"],
                                  lambda i, tk: (30 + int(tk[1:]) * 8, 150),
                                  _T14[:13], _T14[:13] + ["T15"])
    check("잠재 ⓑ 같은 날 3회 실행 = 1거래일",
          (((_str or {}).get("candidates") or {}).get("T15", {}).get("streak_in"), "T15" in _lr[-1]),
          (2, False))
    # 첫 실행 시드: 현 명단 전원이 멤버로 시작한다(첫 full 에 전원 제외 카운트 사고 방지)
    check("잠재 ⓑ 시드: 기존 14종 전원 멤버",
          sorted((_stb or {}).get("members", {})) == sorted(_T14), True)


    # ⓒ 이력은 확정 전이만 — 하루 빠졌다 돌아온 종목에 '제외'를 적지 않는다
    #   T07 이 9/21(월) 하루만 기준 미달 → 그날 주간 이력 생성 → 9/22 복귀
    _lc, _hc, _, _stc, _ = _lat_sim(
        _WEEK, lambda i, tk: (30 + int(tk[1:]) * 8, 40 if (tk == "T07" and i == 5) else 150),
        _T14, _T14, history_on="2026-09-21")
    _ent = ((_hc or {}).get("entries") or [{}])[0]
    _ev = [it for b in _ent.get("blocks", []) if b.get("type") == "items"
           and "경계" not in (b.get("label") or "") for it in b.get("items", [])]
    check("잠재 ⓒ 하루 이탈 종목에 '제외' 이력 없음",
          [it for it in _ev if "Tick07" in it and "제외" in it], [])
    _watch = [it for b in _ent.get("blocks", []) if "경계" in (b.get("label") or "")
              for it in b.get("items", [])]
    check("잠재 ⓒ 경계 관찰에 제외 카운트 표기",
          any("Tick07" in it and "1/5" in it for it in _watch), True)
    check("잠재 ⓒ 복귀 후 멤버 유지·전이 기록 없음",
          ("T07" in _lc[-1],
           [t for t in (_stc or {}).get("transitions", []) if t.get("ticker") == "T07"]),
          (True, []))
    # 거꾸로, 확정 전이는 반드시 적힌다 — 사건을 통째로 삼키는 이력도 감시자 실격이다
    #   T03 이 첫날부터 미달(9/18 5거래일째 제외) · T15 가 빈자리로 편입(9/16 3거래일째)
    _, _hp, _, _, _ = _lat_sim(
        _WEEK[:6], lambda i, tk: (30 + int(tk[1:]) * 8, 40 if tk == "T03" else 150),
        _T14[:13], _T14[:13] + ["T15"], history_on="2026-09-21")
    _evp = [it for b in ((_hp or {}).get("entries") or [{}])[0].get("blocks", [])
            if b.get("type") == "items" and "경계" not in (b.get("label") or "")
            for it in b.get("items", [])]
    check("잠재 ⓒ 확정 제외는 사유와 함께 기록",
          any("Tick03" in it and "제외" in it and "5거래일" in it for it in _evp), True)
    check("잠재 ⓒ 확정 편입은 사유와 함께 기록",
          any("Tick15" in it and "편입" in it and "3거래일" in it for it in _evp), True)
    check("잠재 ⓒ 화면 문구에 기계 필드명 없음",
          [it for it in _ev + _watch
           if any(k in it for k in ("streak", "carried", "pending", "watch"))], [])

    # ── 헤더 시장 지표 띠 (2026-09-26) ──────────────────────────────────────
    # 막으려는 것 세 가지:
    #   ① 한 회차의 수집 실패가 헤더를 '—' 로 비우거나 0 으로 채우는 것 (이전 값 보존)
    #   ② 헤더와 레전드가 **서로 다른 10년물**을 보이는 것 (측정점은 하나)
    #   ③ 12페이지 중 일부에만 배지가 붙는 것 (공용 주입 + 관문)
    import fetch_data as _fdm
    import feed_client as _fcm
    _cm = getattr(_fdm, "collect_macro", None)
    if _cm is None:
        check("시장지표: collect_macro 존재", None, "collect_macro")
    else:
        _NOWM = _dt.datetime(2026, 9, 26, 8, 20, tzinfo=_fdm.KST)
        _ok = lambda v, src="FRED API", obs="2026-09-25": (lambda: (v, "ok", 200, obs, src))
        _fail = lambda outcome="http_error": (lambda: (None, outcome, None, "", ""))
        _prev_m = {k: {"value": v, "as_of": "2026-09-24", "source": "FRED API",
                       "measured_at": "2026-09-25T18:08", "carried": False, "note": None}
                   for k, v in (("usd_krw", 1390.1), ("usd_jpy", 149.2), ("ust10", 5.11),
                                ("ust30", 5.40), ("wti", 68.4))}
        _buf0m = dict(_fcm._buffer)
        _fcm._buffer.clear()
        _o_flush = _fcm.flush
        _fcm.flush = lambda: None                     # 원장 파일은 건드리지 않는다
        try:
            # ① 전부 실패 — 이전 값 보존 · 그 사실 기록 · 0 치환 없음
            _m1 = _cm(_prev_m, fetchers={k: _fail() for k in _fdm.MACRO_KEYS}, now=_NOWM)
            check("시장지표 ①: FRED 실패 시 이전 값 보존",
                  (_m1["ust10"]["value"], _m1["ust30"]["value"], _m1["wti"]["value"]),
                  (5.11, 5.40, 68.4))
            check("시장지표 ①: 보존 사실을 표시한다(carried)",
                  all(_m1[k]["carried"] for k in _fdm.MACRO_KEYS), True)
            check("시장지표 ①: 보존값의 측정 시각은 원래 시각 그대로",
                  _m1["ust10"]["measured_at"], "2026-09-25T18:08")
            _m0 = _cm({}, fetchers={k: _fail("zero") for k in _fdm.MACRO_KEYS}, now=_NOWM)
            check("시장지표 ①: 이전 값도 없으면 null + 사유(0 아님)",
                  (_m0["wti"]["value"], bool(_m0["wti"]["note"])), (None, True))
            check("시장지표 ①: 원장에 macro 5종 outcome 등재(내용 기준 0건)",
                  sorted((v["source"], v["outcome"], v["items"]) for k, v in _fcm._buffer.items()
                         if v.get("kind") == "macro"),
                  sorted((k, "zero", 0) for k in _fdm.MACRO_KEYS))
            # 성공 회차 — 새 값이 이전 값을 대체하고 측정 시각이 찍힌다
            _m2 = _cm(_prev_m, fetchers={"usd_krw": _ok(1391.5, "frankfurter"),
                                         "usd_jpy": _ok(148.9, "frankfurter"),
                                         "ust10": _ok(5.13), "ust30": _ok(5.42),
                                         "wti": _ok(67.9)}, now=_NOWM)
            check("시장지표: 성공 회차는 새 값 · 측정 시각 · 출처",
                  (_m2["ust10"]["value"], _m2["ust10"]["measured_at"], _m2["ust10"]["source"],
                   _m2["ust10"]["carried"]), (5.13, "2026-09-26T08:20+09:00", "FRED API", False))
            # ② 레전드 = 헤더 — 같은 구현, 같은 측정값
            import fred_client as _frc
            check("시장지표: Treasury 관측일은 ISO 로 맞춘다(출처별 날짜 모양 통일)",
                  (_frc.iso_day("09/25/2026"), _frc.iso_day("2026-09-25")),
                  ("2026-09-25", "2026-09-25"))
            check("시장지표 ②: 레전드와 헤더가 같은 10년물 함수를 쓴다(중복 구현 금지)",
                  _fb.fetch_ust10 is _frc.fetch_ust10, True)
            _o_fu = _fb.fetch_ust10
            _fb.fetch_ust10 = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("레전드가 10년물을 다시 불렀다"))
            _refetched = False
            try:
                _rt, _rout = _fb.collect_rates({"UST10": 4.9, "source": "옛값"}, _m2["ust10"])
                _rtc, _ = _fb.collect_rates({"UST10": 4.9}, _m1["ust10"])
            except AssertionError:
                _refetched = True
                _rt, _rtc = {"UST10": None, "source": None, "as_of": None}, {"UST10": None}
            finally:
                _fb.fetch_ust10 = _o_fu
            check("시장지표 ②: 헤더가 잰 회차엔 레전드가 10년물을 다시 부르지 않는다",
                  _refetched, False)
            check("시장지표 ②: 레전드 UST10 = 헤더 UST10 (값·출처·관측일)",
                  (_rt["UST10"], _rt["source"], _rt["as_of"]),
                  (_m2["ust10"]["value"], _m2["ust10"]["source"], _m2["ust10"]["as_of"]))
            check("시장지표 ②: 헤더가 이전 값을 보존한 회차에도 같은 값",
                  _rtc["UST10"], _m1["ust10"]["value"])
            import build_site as _bsm
            _band = _bsm.macro_badges_html(_m2, "2026-09-26")
            check("시장지표 ②: 헤더 배지에 레전드와 같은 숫자",
                  f'{_rt["UST10"]:.2f}%' in _band, True)
            check("시장지표 ②: 레전드가 승계하면 옛 rates 원장 항목을 지운다(관할 이전)",
                  "rates:ust10" in _fcm._forget, True)
        finally:
            _fcm.flush = _o_flush
            _fcm._buffer.clear()
            _fcm._buffer.update(_buf0m)
            _fcm._forget.discard("rates:ust10")
        # 관제탑 배선 — macro 원장이 judge_feeds 로 흘러가 '정확히 1건' 울린다
        import pipeline_sentinel as _psm
        _stm = {"sources": {"macro:ust10": {"kind": "macro", "source": "ust10",
                                             "outcome": "http_error", "code": 503, "items": 0}}}
        _sst = {"feeds": {}}
        _psm.judge_feeds(_stm, _sst, "2026-09-26", _psm.DEFAULTS)
        _al, _ = _psm.judge_feeds(_stm, _sst, "2026-09-26", _psm.DEFAULTS)
        check("시장지표 ①: 관제탑이 macro 요청 실패 2회에 정확히 1건",
              len([a for a in _al if "시장지표" in str(a)]), 1)
        # 표시층 — WTI 는 관측일이 측정일보다 이르면 '(전일)', 결측은 '—'
        # 2026-09-26 폭 절약: 화면 라벨은 축약(WTI·10Y·30Y·JPY·KRW), 전체 이름·전일 여부는 title
        check("시장지표: 헤더 WTI 는 축약 라벨 + title 에 '전일 종가'(상태에서 생성)",
              ('>WTI <span class="ud-mv">$67.90</span>' in _band,
               'title="WTI 원유 현물 · 전일 종가 · 측정 08:20 · 출처 FRED API"' in _band),
              (True, True))
        check("시장지표: 축약 라벨 4종이 화면에",
              [k for k in (">WTI ", ">10Y ", ">30Y ", ">JPY ") if k not in _band], [])
        _bandn = _bsm.macro_badges_html({}, "2026-09-26")
        check("시장지표: 결측은 — (0 아님)",
              _bandn.count('<span class="ud-mv">—</span>'), 4)
        check("시장지표: title 은 '전체 이름 · 측정 HH:MM · 출처'",
              'title="미국 10년물 국채 금리 · 측정 08:20 · 출처 FRED API"' in _band, True)
        import re as _rem
        _vis = _rem.sub(r"<[^>]+>", " ", _band)        # 보이는 글자만(속성·태그 제외)
        check("시장지표: 화면 문구에 기계 필드명 없음",
              [k for k in ("ust10", "ust30", "usd_jpy", "usd_krw", "wti", "outcome",
                           "carried", "measured_at") if k in _vis], [])
        # ③ 12템플릿 전부 — 공용 주입이 5배지를 만든다 + 관문이 빠진 배지를 잡는다
        _tdir = Path(__file__).parent
        _tpls = sorted(_tdir.glob("*template*.html"))
        _bad3 = []
        for _tp in _tpls:
            _pg, _okw = _bsm.macro_wrap(_tp.read_text(encoding="utf-8"), _m2, "2026-09-26")
            _got = [k for k in ("usd_krw", "wti", "ust10", "ust30", "usd_jpy")
                    if f'data-mk="{k}"' in _pg]
            _krw_lbl = _rem.search(r'data-mk="usd_krw"[^>]*>\s*KRW\s*<span', _pg)
            _krw_ttl = 'title="USD/KRW 환율 · ' in _pg
            if not (_okw and len(_got) == 5 and _pg.count('data-mk="') == 5
                    and _krw_lbl and _krw_ttl):
                _bad3.append(_tp.name)
        check("시장지표 ③: 템플릿 12개 확인", len(_tpls), 12)
        check("시장지표 ③: 12템플릿 전부 5배지(각 1회) · KRW 축약 라벨 · 전체 이름 title", _bad3, [])
        # 날짜/시계 배지 — 초 제거(HH:MM) · 날짜 MM.DD · 30초 갱신
        _hfc = _bsm.HEADER_FIX_CSS
        check("헤더 시계: HH:MM(초 없음) · MM.DD · 30초 갱신",
              ("hour: '2-digit', minute: '2-digit'" in _hfc, ".slice(5).replace('-', '.')" in _hfc,
               "setInterval(tick, 30000)" in _hfc, "setInterval(tick, 1000)" in _hfc),
              (True, True, True, False))
        import verify_pages as _vpm
        _full, _ = _bsm.macro_wrap(_tpls[0].read_text(encoding="utf-8"), _m2, "2026-09-26")
        _full = _full + " ".join(_vpm.MARKERS) + "</hea" + "d>"
        _cut = _full.replace('data-mk="ust30"', "")
        check("시장지표 ③: 관문 — 5배지가 다 있으면 통과", _vpm.check([("a.html", _full)]), {})
        check("시장지표 ③: 관문 — 배지 하나 빠지면 그 이름으로 잡는다",
              _vpm.check([("a.html", _cut)]), {"a.html": ["배지 30Y"]})

    # ── 아침 브리핑 시장 지표 한 줄 (2026-09-26) — 숫자만, 해석 금지 ─────────
    import send_telegram_briefing as _tgm
    _mlf = getattr(_tgm, "macro_line", None)
    if _mlf is None:
        check("브리핑: macro_line 존재", None, "macro_line")
    else:
        _mm = {"fetched_date": "2026-09-26", "usd_krw": 1391.5, "macro": {
            "wti": {"value": 67.9, "as_of": "2026-09-22", "measured_at": "2026-09-26T08:20+09:00"},
            "ust10": {"value": 5.11, "as_of": "2026-09-25", "measured_at": "2026-09-26T08:20+09:00"},
            "ust30": {"value": 5.42, "as_of": "2026-09-25", "measured_at": "2026-09-26T08:20+09:00"},
            "usd_jpy": {"value": None, "note": "취득 실패(http_error) — 이전 값 없음"}}}
        _line = _mlf(_mm)
        check("브리핑: 헤더와 같은 5종·순서·표기(결측 —)", _line,
              "💱 USD/KRW 1,391.50 · WTI(전일) $67.90 · 미10년 5.11% · 미30년 5.42% · USD/JPY —")
        check("브리핑: 숫자만 — 해석·사유 문장 없음",
              [w for w in ("실패", "상승", "하락", "우려", "신호", "http", "note") if w in _line], [])
        check("브리핑: 지표가 없는 구 데이터는 종전 한 줄",
              _mlf({"usd_krw": 1391.5}), "💱 USD/KRW 1,391.50")
        _bl_txt = __import__("inspect").getsource(_tgm.build_briefing)
        check("브리핑: 본문 조립이 macro_line 을 쓴다(배선)", "macro_line(meta)" in _bl_txt, True)

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
