#!/usr/bin/env python3
"""
과거 에세이 재검사 정밀 분류 테스트 (2026-10-23)
================================================================================
첫 드라이런이 41편 중 15편(37%)을 '명백한 불일치'로 잡았다. 그중
  · Webull 출처는 지금 블로그 목록 페이지(8,965자)를 돌려준다 — 글자 수만 보고 '전문'으로 쳤다
  · 'Flight 14' 같은 이름 속 번호가 숫자 불일치로 잡혔다
① 원문 진위: 제목 핵심어(슬러그의 고유명사 2개 이상)가 없으면 '기사 아님' → 대조 불가
② 숫자 분류: 순번·명칭 제외 / 파생 일치 / 허용 오차 일치 / 진짜 불일치
각 규칙은 변이(규칙을 끈 판)에서 결과가 실제로 뒤집히는지 확인한다.
실행: python scripts/test_recheck.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import recheck_essays as rc   # noqa: E402

WEBULL = "https://www.webull.com/blog/294-Alphabet-Q2-2026-Earnings-Revenue-Beats-Cloud-Revenue-Surges-82"
LIST_PAGE = ("Individuals Trading Stocks ETFs Options Futures Crypto Market Insights Explore the latest market "
             "trends, investing ideas, and expert insights. Top Gainers Pre-market After-hours. ") * 30
ARTICLE = ("Alphabet reported Q2 revenue of $119.8 billion, beating estimates of $116.93 billion. "
           "Google Cloud revenue rose to $24.77 billion as growth accelerated. ") * 20
BODY = "<p>Alphabet이 Q2 매출 $119.8B를 보고했다. Google Cloud는 $24.77B였다.</p>"


class ArticleCheckTest(unittest.TestCase):
    def test_list_page_is_not_the_article(self):
        ok, why = rc.is_article(WEBULL, LIST_PAGE, BODY)
        self.assertIs(ok, False)
        self.assertIn("고유명사", why)

    def test_real_article_passes(self):
        self.assertIs(rc.is_article(WEBULL, ARTICLE, BODY)[0], True)

    def test_slug_without_names_uses_keyword_cover(self):
        url = "https://www.anthropic.com/news/expanding-support-for-scientists"
        txt = "We are expanding our support for scientists with free seats. " * 40
        self.assertIs(rc.is_article(url, txt, "<p>과학자 지원</p>")[0], True)
        self.assertIs(rc.is_article(url, LIST_PAGE, "<p>과학자 지원</p>")[0], False)

    def test_sec_archive_is_fixed_filing(self):
        self.assertIs(rc.is_article(SEC_URL, "Revenue Space $1,767 " * 100, "<p>SpaceX 실적</p>")[0], True)
        self.assertIs(rc.is_article("https://www.sec.gov/cgi-bin/browse-edgar?company=x", LIST_PAGE, BODY)[0] is True,
                      False)                                   # 검색 화면은 고정 공시가 아니다

    def test_no_slug_is_undecidable(self):
        self.assertIsNone(rc.is_article("https://www.ft.com/content/4564e6a5", ARTICLE, BODY)[0])

    def test_list_page_essay_becomes_unverifiable_not_mismatch(self):
        cls, bad, unread, _, _ = rc.classify(BODY, [(WEBULL, LIST_PAGE)])
        self.assertEqual(cls, rc.UNVERIFIABLE)
        self.assertEqual(unread, [WEBULL])

    def test_mutant_length_only_calls_it_mismatch(self):
        with mock.patch.object(rc, "is_article", return_value=(True, "변이")):
            cls, *_ = rc.classify(BODY, [(WEBULL, LIST_PAGE)])
        self.assertEqual(cls, rc.MISMATCH)


SEC = ("Space Segment $, in millions Revenue Space $1,767 $962 $1,666 Connectivity 5,248 4,291 3,097 "
       "Completed Starship Flight 14, our first orbital Starship flight. Revenue of $14,575 million. ") * 10
SEC_URL = "https://www.sec.gov/Archives/edgar/data/1181412/000162828026066482/spaceexplorationtechnologi.htm"


def kinds(body, src=SEC):
    return {n["숫자"]: n["분류"] for n in rc.classify_numbers(body, [(SEC_URL, src)])}


class NumberClassTest(unittest.TestCase):
    def test_names_numbers_are_exempt(self):
        k = kinds("<p>Starship Flight 21에서 포춘 500 기업이 지켜봤다.</p>")
        self.assertEqual(k, {"21": rc.EXEMPT, "500": rc.EXEMPT})

    def test_quantities_are_not_exempt(self):
        k = kinds("<p>검색 광고 +17% 성장, 위성 33기</p>")
        self.assertEqual(k["17"], rc.TRUE_BAD)
        self.assertEqual(k["33"], rc.TRUE_BAD)

    def test_derived_values(self):
        k = kinds("<p>Space 1,767백만과 Connectivity 5,248백만을 합친 7,015백만. Connectivity는 전년 3,097백만에서 69% 늘었다.</p>")
        self.assertEqual(k["7,015백만"], rc.DERIVED)
        self.assertEqual(k["69"], rc.DERIVED)

    def test_tolerance_recheck(self):
        k = kinds("<p>매출 $14.5B</p>")            # 14,575M 과 0.5% 차 — 반올림 폭(0.05B)은 넘고 1% 안
        self.assertEqual(k["$14.5B"] if "$14.5B" in k else k.get("14.5B"), rc.TOLERANCE)

    def test_true_mismatch_carries_nearest_source(self):
        n = [x for x in rc.classify_numbers("<p>Starlink 영업이익 166억 달러</p>", [(SEC_URL, SEC)])
             if x["분류"] == rc.TRUE_BAD][0]
        self.assertEqual(n["숫자"], "166억")
        self.assertIn("url", n["가장_가까운_원문_숫자"])
        self.assertIn("166억", n["문장"])

    def test_mutant_no_ordinal_rule_flags_flight_number(self):
        with mock.patch.object(rc, "is_ordinal", return_value=False):
            self.assertEqual(kinds("<p>Starship Flight 21</p>")["21"], rc.TRUE_BAD)

    def test_mutant_no_derivation_flags_sum(self):
        with mock.patch.object(rc, "derived_from", return_value=None):
            self.assertEqual(kinds("<p>1,767백만과 5,248백만을 합친 7,015백만</p>")["7,015백만"], rc.TRUE_BAD)

    def test_mutant_derivation_over_all_source_numbers_is_too_loose(self):
        # 피연산자를 원문 숫자 전부로 넓히면 아무 비율이나 '파생'이 된다 — 에세이가 쓴 숫자만 피연산자
        self.assertEqual(kinds("<p>점유율 54%</p>")["54"], rc.TRUE_BAD)


class ReportTest(unittest.TestCase):
    def test_report_lists_only_confirmed_with_fields(self):
        res = {"checked_at": "t", "total": 2, "counts": {}, "withdraw_ratio": 0.5, "stop": True, "rows": [
            {"slug": "a", "title": "A", "date": "d", "class": rc.MISMATCH, "in_thesis": ["166억"],
             "sources": [{"url": SEC_URL}],
             "numbers": rc.classify_numbers("<p>Flight 21 · 영업이익 166억</p>", [(SEC_URL, SEC)])},
            {"slug": "b", "title": "B", "date": "d", "class": rc.UNVERIFIABLE, "sources": [], "numbers": []}]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            rc.write_report(res, p)
            rep = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual([c["slug"] for c in rep["confirmed"]], ["a"])
        c = rep["confirmed"][0]
        self.assertEqual([x["숫자"] for x in c["진짜_불일치"]], ["166억"])
        self.assertEqual(c["그_밖의_걸린_숫자"][0]["분류"], rc.EXEMPT)
        self.assertEqual(c["원문"], [SEC_URL])


if __name__ == "__main__":
    unittest.main(verbosity=2)
