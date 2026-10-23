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


if __name__ == "__main__":
    unittest.main(verbosity=2)
