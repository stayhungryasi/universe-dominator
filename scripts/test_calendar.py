#!/usr/bin/env python3
"""
실적 발표일 뉴스 추출의 대조 테스트 (2026-11-03)
막으려는 것: 헤드라인 제목에 없는 날짜가 실적 발표일로 공개 브리핑에 실리는 것
(10/28 경 이미 실적을 낸 Microsoft 가 '11/3 실적 발표 D-0'으로 나갔다 — 발행 시각을 발표일로 읽은 것으로 보인다).
실행: python scripts/test_calendar.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import fetch_calendar as fc   # noqa: E402


class StatedTest(unittest.TestCase):
    def test_pubdate_is_not_a_stated_date(self):
        h = ["[Tue, 03 Nov 2026] Microsoft earnings: what Wall Street expects from Azure"]
        self.assertFalse(fc.stated_in_titles("2026-11-03", h))

    def test_formats_in_title(self):
        for t in ("Microsoft to report Q1 results on Oct. 28", "AMD earnings date set for November 4th",
                  "Results due 4 Nov after the bell", "Earnings on 11/4 after close", "삼성전자 11월 4일 실적 발표",
                  "Earnings call scheduled 2026-11-04"):
            d = "2026-10-28" if "Oct" in t else "2026-11-04"
            self.assertTrue(fc.stated_in_titles(d, ["[Mon, 02 Nov 2026] " + t]), t)

    def test_near_miss_dates_rejected(self):
        self.assertFalse(fc.stated_in_titles("2026-11-03", ["Earnings on Nov 30 after close"]))
        self.assertFalse(fc.stated_in_titles("2026-11-03", ["Earnings on 11/30"]))
        self.assertFalse(fc.stated_in_titles("2026-11-03", ["실적 발표 11월 13일"]))
        self.assertFalse(fc.stated_in_titles("2026-11-03", None))

    def test_wired_into_extraction(self):
        """배선 — 모델이 헤드라인에 없는 날짜를 돌려주면 버린다(있는 날짜는 싣는다)."""
        corpus_titles = {"Microsoft": ["[Tue, 03 Nov 2026] Microsoft earnings: what to expect"],
                         "AMD": ["[Mon, 02 Nov 2026] AMD to report earnings on Nov. 4"]}

        class R:
            status_code = 200

            def json(self):
                return {"content": [{"type": "text", "text":
                                     '[{"date":"2026-11-03","stock":"Microsoft","note":""},'
                                     '{"date":"2026-11-04","stock":"AMD","note":"장후"}]'}]}
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(fc, "earnings_targets", return_value=list(corpus_titles)), \
                mock.patch.object(fc, "_rss_titles", side_effect=lambda q, lang="en", limit=4:
                                  corpus_titles["Microsoft" if "Microsoft" in q else "AMD"]), \
                mock.patch.object(fc, "in_horizon", return_value=True), \
                mock.patch.object(fc.time, "sleep"), \
                mock.patch.object(fc.requests, "post", return_value=R()):
            out = fc.fetch_earnings_via_news()
        self.assertEqual([e["title"] for e in out], ["AMD 실적 발표 · 장후"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
