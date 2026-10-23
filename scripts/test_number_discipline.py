#!/usr/bin/env python3
"""
숫자 규율 테스트 (2026-10-23, 알려진 지뢰 ⑱) — 원문 없이 숫자를 만든다
================================================================================
회수 에세이가 IR 실적 PDF 를 못 읽고(PDF→빈 문자열) 제목만으로 차트 숫자까지 지어냈다.
인용은 원문과 대조했지만 숫자는 대조하지 않았다.
  ① 원문에 없는 숫자 1개가 섞인 본문 → 차단
  ② 전문 없는 원천(PDF 만) → 숫자·차트 금지, 실적·계약이면 보류
  ③ 같은 사건 2편 → 1편
실행: python scripts/test_number_discipline.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import companion_essays as ce   # noqa: E402

SEC = ("Third Quarter 2026 Highlights. Revenue was $14,575 million. Operating income $2,791 million. "
       "Net income $4,404 million. Connectivity operating income $2,367 million. Space Infrastructure "
       "operating income $431 million. Subscribers 13.6 million, ARPU $69 across 174 markets. "
       "AI nameplate capacity 1.7 GW. Capital expenditures $19,593 million.")


class NumberCheckTest(unittest.TestCase):
    def test_all_from_source_pass(self):
        body = ("<p>3분기 매출은 $14,575M(약 146억 달러), 순이익 $4,404M. Connectivity 영업이익 2,367M, "
                "가입자 1,360만 명, ARPU $69, 174개 시장. 2026년 10월 20일 발표.</p>")
        self.assertEqual(ce.check_numbers(body, [], SEC), [])

    def test_one_foreign_number_blocked(self):
        body = "<p>매출 $14,575M. Starlink 영업이익 166억 USD.</p>"
        self.assertEqual(ce.check_numbers(body, [], SEC), ["166억"])

    def test_chart_values_checked(self):
        ok = [{"title": "t", "unit": "M", "series": [{"label": "매출", "value": 14575}]}]
        bad = [{"title": "t", "unit": "억", "series": [{"label": "xAI 영업손실", "value": -126}]}]
        self.assertEqual(ce.check_numbers("", ok, SEC), [])
        self.assertEqual(len(ce.check_numbers("", bad, SEC)), 1)

    def test_rounding_tolerance(self):
        # 14,575M = 145.75억 → '146억'(표기 자릿수 반올림) 통과, '150억' 차단
        self.assertEqual(ce.check_numbers("<p>146억 달러</p>", [], SEC), [])
        self.assertEqual(ce.check_numbers("<p>150억 달러</p>", [], SEC), ["150억"])

    def test_korean_compound_units(self):
        src = "So far, 1 million skills have been published and nearly 280 million installed."
        self.assertEqual(ce.check_numbers("<p>공개 스킬 1백만 개, 설치 2억 8천만 건</p>", [], src), [])
        self.assertEqual(ce.check_numbers("<p>1조 4,575억</p>", [], "revenue $1,457.5 billion"), [])
        self.assertEqual(ce.check_numbers("<p>설치 3억 8천만 건</p>", [], src), ["3억 8천만"])
        self.assertEqual(ce.check_numbers("<p>설치 2억8천만 · 스킬 100만 · 가입 1,360만</p>", [],
                                          src + " 13.6 million subscribers"), [])

    def test_number_boundaries(self):
        src = "800MHz spectrum. Revenue was $14,575 million."
        self.assertEqual(ce.check_numbers("<p>800MHz 스펙트럼 인수</p>", [], src), [])      # '80' 으로 자르지 않는다
        self.assertEqual(ce.check_numbers("<p>900MHz 스펙트럼</p>", [], src), ["900"])
        self.assertEqual(ce.check_numbers("<p>매출146억 달러, 순이익999억</p>", [], src), ["999억"])
        # 한글 바로 뒤의 맨 숫자(예전엔 검사를 건너뛰었다)
        self.assertEqual(ce.check_numbers("<p>ARPU 하락률17% 기록</p>", [], src), ["17"])

    def test_no_coincidental_match(self):
        """원문에 없는 'Google $920M' 이 감가상각 922 와 우연히 맞으면 안 된다(실측 사고)."""
        src = "Depreciation amortization 2,473 1,885 922. Revenue was $14.6 billion."
        self.assertEqual(ce.check_numbers("<p>Google $920M 월간 거래</p>", [], src), ["920M"])
        # 원문이 더 거칠게 반올림했으면 원문 폭으로 맞춘다
        self.assertEqual(ce.check_numbers("<p>매출 $14,575M</p>", [], src), [])

    def test_exempt_tokens(self):
        body = "<p>2026년 3분기(3Q26) Flight 14 이후 2편의 관측, 9/28 발사, 10월 20일 실적, 차트 Q3'25 대비.</p>"
        self.assertEqual(ce.check_numbers(body, [], SEC + " Flight 14"), [])


class DisciplineFlowTest(unittest.TestCase):
    """집필 경로 — 위반이면 사유 주고 1회 재생성, 그래도면 보류. 전문 없으면 숫자 금지."""

    COMP = {"slug": "spacex", "ko": "SpaceX"}
    TH = {"body": "논제"}

    def _run(self, items, replies):
        with mock.patch.object(ce, "ask_claude", side_effect=replies) as ask:
            res, viol = ce.write_with_discipline("k", "헌법", self.COMP, self.TH, items, [])
        return res, viol, ask

    def test_foreign_number_regenerates_then_holds(self):
        items = [{"title": "SEC 8-K", "url": "https://sec/x", "article": SEC, "primary": True}]
        bad = {"publish": True, "title": "실적", "body": "<p>Starlink 영업이익 166억 USD</p>",
               "sources": ["https://sec/x"]}
        res, viol, ask = self._run(items, [bad, bad])
        self.assertIn("원문에 없는 숫자", viol)
        self.assertEqual(ask.call_count, 2)
        self.assertIn("발췌에 **그대로 있는 것만**", ask.call_args.kwargs["feedback"])

    def test_clean_rewrite_publishes(self):
        items = [{"title": "SEC 8-K", "url": "https://sec/x", "article": SEC}]
        bad = {"publish": True, "body": "<p>166억</p>", "sources": []}
        good = {"publish": True, "body": "<p>순이익 $4,404M</p>", "sources": []}
        res, viol, _ = self._run(items, [bad, good])
        self.assertIsNone(viol)
        self.assertTrue(res["numbers_verified"])

    def test_pdf_only_source_forbids_numbers(self):
        items = [{"title": "SpaceX Launches Starship Flight 14", "url": "https://ir/x.pdf",
                  "article": "", "article_attempted": True}]
        withnum = {"publish": True, "title": "궤도 비행", "event_type": "발사",
                   "body": "<p>엔진 33기</p>", "sources": []}
        res, viol, _ = self._run(items, [withnum, withnum])
        self.assertIn("전문 원천 없음 — 숫자·차트 금지", viol)
        nonum = {"publish": True, "title": "궤도 비행", "event_type": "발사",
                 "body": "<p>첫 궤도 비행이 남긴 질문</p>", "sources": []}
        res, viol, ask = self._run(items, [nonum])
        self.assertIsNone(viol)
        self.assertFalse(ask.call_args.kwargs["full_any"])

    def test_pdf_only_earnings_is_held(self):
        items = [{"title": "SpaceX Reports Third Quarter 2026 Results", "url": "https://ir/q3.pdf",
                  "article": ""}]
        res, viol, ask = self._run(items, [{"publish": True, "title": "3Q 실적", "body": "<p>해석</p>",
                                            "sources": []}])
        self.assertIn("보류", viol)
        self.assertEqual(ask.call_count, 1)

    def test_chart_mark_only_when_verified(self):
        ch = [{"title": "t", "unit": "M", "series": [{"label": "a", "value": 1}, {"label": "b", "value": 2}]}]
        e1 = ce.build_entry("spacex", "t", "불변", "<p>x</p>", [], "auto", "2026-10-23", charts=ch)
        self.assertNotIn("원문 기사에서 추출", e1["html"])
        with mock.patch.object(ce, "render_charts", return_value="<svg>자료: 원문 기사에서 추출 (자동)</svg>"):
            e2 = ce.build_entry("spacex", "t", "불변", "<p>x</p>", [], "auto", "2026-10-23",
                                charts=ch, numbers_verified=True)
        self.assertIn("원문 기사에서 추출", e2["html"])


class PdfAndSecTest(unittest.TestCase):
    def test_pdf_goes_to_pypdf(self):
        resp = mock.Mock(status_code=200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        resp.raise_for_status = mock.Mock()
        with mock.patch.object(ce.requests, "get", return_value=resp), \
                mock.patch.object(ce, "pdf_text", return_value="Revenue $14,575 million") as pt:
            self.assertEqual(ce.fetch_article_text("https://ir/q3.pdf"), "Revenue $14,575 million")
        pt.assert_called_once()

    def test_pick_ex99_by_type_column(self):
        html = ('<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/x/a.htm">a.htm</a></td>'
                '<td>8-K</td></tr><tr><td>2</td><td>EX-99.1</td><td><a href="/Archives/x/'
                'spaceexplorationtechnologi.htm">spaceexplorationtechnologi.htm</a></td><td>EX-99.1</td></tr>')
        self.assertEqual(ce.pick_ex99(html), "/Archives/x/spaceexplorationtechnologi.htm")
        self.assertIsNone(ce.pick_ex99("<tr><td>8-K</td></tr>"))


class SameEventTest(unittest.TestCase):
    A = {"company": "spacex", "date": "2026-10-23", "event_date": "2026-10-20",
         "title": "Starlink 현금이 AI 손실을 견딜 때까지 — 3Q 실적의 단위 경제 검증",
         "event_key": ["3Q26 실적"]}
    B = {"company": "spacex", "date": "2026-10-23", "event_date": "2026-10-20",
         "title": "Starlink 현금흐름과 AI 손실의 충돌 — 3Q 실적의 수직 통합 명제",
         "event_key": ["3분기 실적"]}
    F = {"company": "spacex", "date": "2026-10-23", "event_date": "2026-09-28",
         "title": "첫 궤도 비행이 증명한 것과 남긴 것", "event_key": ["Flight 14", "궤도"]}

    def test_same(self):
        self.assertTrue(ce.same_event(self.A, self.B))

    def test_different_events(self):
        self.assertFalse(ce.same_event(self.A, self.F))
        self.assertFalse(ce.same_event(self.A, dict(self.B, company="anthropic")))
        self.assertFalse(ce.same_event(self.A, dict(self.B, event_date="2026-07-28")))

    def test_backfill_keeps_one(self):
        outs = iter([dict(self.A, slug="a", sources=[{"url": "u1"}]),
                     dict(self.B, slug="b", sources=[{"url": "u2"}])])
        st = {"published": {}}
        with mock.patch.object(ce, "run_auto", side_effect=lambda *a, **k: next(outs)):
            out = ce.run_backfill({"spacex": {"slug": "spacex", "ko": "SpaceX"}}, "k", "c", [],
                                  "2026-10-23", st, days=30)
        self.assertEqual([e["slug"] for e in out], ["a"])

    def test_withdrawn_does_not_block(self):
        self.assertIsNone(ce.duplicate_of(self.A, [dict(self.B, status="withdrawn")]))
        self.assertIsNotNone(ce.duplicate_of(self.A, [self.B]))


class NumberListTest(unittest.TestCase):
    """E2(2026-10-23) — 집필 전에 '사용 가능한 숫자' 목록을 프롬프트에 넣고, 위반은 숫자+문맥을 사유로 돌려준다."""

    ITEMS = [{"title": "SEC 8-K", "url": "https://sec/x", "article": SEC, "primary": True,
              "event_kind": "earnings"}]

    def test_list_values_pass_and_outside_values_fail(self):
        inv = ce.number_inventory(self.ITEMS)
        raws = [x["숫자"] for x in inv]
        self.assertIn("14,575 million", raws)
        self.assertIn("13.6 million", raws)
        self.assertTrue(all("[" + x["숫자"] + "]" in x["문맥"] for x in inv))
        src = ce.collected_text(self.ITEMS)
        for r in raws:                       # 목록의 숫자는 규율을 통과한다(목록 ⊂ 허용)
            self.assertEqual(ce.check_numbers(f"<p>{r}</p>", [], src), [], r)
        self.assertNotIn("920M", " ".join(raws))
        self.assertEqual(ce.check_numbers("<p>Google $920M 월간</p>", [], src), ["920M"])   # 목록 밖 → 차단

    def test_small_counts_and_dates_not_listed(self):
        inv = ce.number_inventory([{"title": "x", "article": "On 2026-10-20 the 3 segments in Q3 grew to $431 million."}])
        self.assertEqual([x["숫자"] for x in inv], ["431 million"])

    def _flow(self, replies):
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            r = mock.Mock()
            r.raise_for_status = lambda: None
            r.json = lambda: {"content": [{"type": "text", "text": ce.json.dumps(replies[len(calls) - 1],
                                                                              ensure_ascii=False)}],
                              "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "end_turn"}
            return r
        with mock.patch.object(ce.requests, "post", side_effect=fake_post):
            res, viol = ce.write_with_discipline("k", "헌법", {"slug": "spacex", "ko": "SpaceX"},
                                                 {"body": "논제"}, self.ITEMS, [])
        return res, viol, calls

    def test_prompt_carries_list_and_retry_carries_number_with_context(self):
        bad = {"publish": True, "title": "t", "body": "<p>광고 계약으로 Google $920M 월간 지급</p>", "sources": []}
        good = {"publish": True, "title": "t", "body": "<p>매출 $14,575 million</p>", "sources": []}
        res, viol, calls = self._flow([bad, good])
        self.assertIsNone(viol)
        p1 = calls[0]["messages"][0]["content"]
        self.assertIn("사용_가능한_숫자", p1)
        self.assertIn("[14,575 million]", p1)          # 목록 항목(문맥 속 [숫자])이 실제로 실렸다 — 발췌만으로는 안 생긴다
        self.assertIn("목록 밖 숫자 금지", p1)
        p2 = calls[1]["messages"][0]["content"]
        self.assertIn("920M", p2)
        self.assertIn("Google $920M 월간", p2)          # 위반 숫자의 원고 속 문맥이 사유로 간다

    def test_fact_event_uses_fact_model(self):
        good = {"publish": True, "title": "t", "body": "<p>해석</p>", "sources": []}
        _, _, calls = self._flow([good])
        self.assertEqual(calls[0]["model"], "claude-sonnet-5-5")
        self.assertEqual(calls[0]["output_config"], {"effort": "medium"})
        self.assertGreaterEqual(calls[0]["max_tokens"], 16000)

    def test_model_routing(self):
        launch = [{"title": "Starship Flight 15 slips to November", "article": "x"}]
        self.assertEqual(ce.pick_model(launch), "claude-haiku-4-5")
        self.assertEqual(ce.pick_model([{"title": "SpaceX prices $40 billion notes offering"}]), "claude-sonnet-5-5")
        self.assertEqual(ce.pick_model([{"title": "보도", "primary": True, "event_kind": "contract"}]), "claude-sonnet-5-5")
        with mock.patch.dict(ce.os.environ, {"COMPANION_MODEL": "m-base", "COMPANION_MODEL_FACT": "m-fact"}):
            self.assertEqual(ce.pick_model(launch), "m-base")
            self.assertEqual(ce.pick_model(self.ITEMS), "m-fact")


class RecheckFalsePositiveTest(unittest.TestCase):
    """과거 에세이 재검사(2026-10-23 결정 5)에서 정상 문장으로 실측한 오탐 — 규율 폭은 그대로, 읽기만 고친다."""

    def toks(self, s):
        return [x[2] for x in ce._num_spans(s)]

    def test_dotted_dates_exempt(self):
        self.assertEqual(self.toks("파트너십을 발표했다(2026.9.18)."), [])
        self.assertEqual(self.toks("2026.10 기준"), [])

    def test_quarter_with_four_digit_year_exempt(self):
        self.assertEqual(self.toks("Q4 2026·Q1 2027 실적"), [])
        self.assertEqual(self.toks("3Q 2026 실적 · 3Q26"), [])

    def test_korean_word_starting_with_unit_letter_is_not_unit(self):
        self.assertEqual(self.toks("Claude Opus 4.6 조기 버전"), ["4.6"])
        self.assertEqual(self.toks("Falcon 9조차 상단 회수에"), ["9"])

    def test_real_units_still_read(self):
        self.assertEqual(ce._num_tokens("9조원")[0][0], 9e12)
        self.assertEqual(ce._num_tokens("1조 4,575억원")[0][0], 1.4575e12)
        self.assertEqual(ce._num_tokens("2억 8천만")[0][0], 2.8e8)

    def test_context_points_at_the_flagged_token(self):
        ctx = dict(ce.number_context("<p>2026년 8월에 26개 조직이 표적이 됐다</p>", ["26"]))
        self.assertIn("26개", ctx["26"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
