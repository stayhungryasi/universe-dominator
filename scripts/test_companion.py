#!/usr/bin/env python3
"""
동행 관측 엔진 검증 (test_companion) — 침묵이 정답인 경우를 특히 본다.

핵심 4케이스 (선장님 요구 사양):
  ① 소재 에세이화   — 링크+메모 → 집필 → 원장 등재
  ② 직접 게재       — 소장 글 그대로, AI 호출 0회 (origin="직접")
  ③ 중요도 미달     — 5축 미달 판정 시 침묵 (원장 불변)
  ④ 논제 부재       — 원장이 플레이스홀더면 집필 스킵 ("논제 없는 에세이는 스크랩")
추가: 인용 규율(원문 대조 방식) — 오탐 2례 통과·진짜 위반 적발·재생성 1회.

실행: python scripts/test_companion.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import companion_essays as ce   # noqa: E402

TODAY = "2026-08-22"
COMP = {"slug": "spacex", "ko": "SpaceX", "emoji": "🚀", "sources": []}
COMPANIES = {"spacex": COMP}
LIVE_THESIS = {"raw": "x", "body": "핵심 가설: 재사용 발사 비용 곡선이 10년을 가른다.",
               "version": 3, "updated": "2026-08-20", "placeholder": False}
PLACEHOLDER = {"raw": "", "body": "", "version": 0, "updated": "", "placeholder": True}

GOOD = {
    "publish": True, "reason": "재사용 로드맵의 실물 증거", "axis": "② 기술 임계",
    "title": "스타십 9호기 — 재사용 곡선의 첫 실물 증거",
    "verdict": "강화",
    "body": "<h3>무슨 일이 있었나</h3><p>9호기가 궤도 재진입 후 회수됐다.</p>"
            "<h3>10년 논제와의 연결</h3><p>비용 곡선 가설에 직접 닿는다.</p>"
            "<h3>무엇이 틀릴 수 있나</h3><p>1회 성공은 신뢰성 곡선이 아니다.</p>",
    "sources": ["https://example.com/a"],
    "watch": ["10호기 일정"],
}


class MaterialTest(unittest.TestCase):
    """① 소재 에세이화 · ② 직접 게재"""

    def test_01_material_to_essay(self):
        mat = {"id": "m1", "name": "n/m1", "company": "spacex", "mode": "essay",
               "url": "https://example.com/a", "memo": "이건 꼭 보세요", "title": "Starship 9"}
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "fetch_article_text", return_value="본문"), \
             mock.patch.object(ce, "ask_claude", return_value=GOOD) as ask:
            entry, note = ce.run_material(mat, COMPANIES, "key", "헌법", [], TODAY)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["origin"], "소재")
        self.assertEqual(entry["company"], "spacex")
        self.assertEqual(entry["verdict"], "강화")
        self.assertIn("스타십 9호기", entry["title"])
        self.assertEqual(ask.call_count, 1)
        # 소장 메모가 집필 입력으로 실제로 전달돼야 한다
        self.assertEqual(ask.call_args.kwargs.get("memo"), "이건 꼭 보세요")
        self.assertIn("출처", entry["html"])
        self.assertIn("매수·매도", entry["html"])

    def test_02_direct_post_never_calls_ai(self):
        mat = {"id": "m2", "name": "n/m2", "company": "spacex", "mode": "direct",
               "url": "", "memo": "오늘의 관측\n두 번째 문단", "title": "소장 노트"}
        with mock.patch.object(ce, "ask_claude") as ask, \
             mock.patch.object(ce, "read_thesis", return_value=PLACEHOLDER):
            entry, note = ce.run_material(mat, COMPANIES, "key", "헌법", [], TODAY)
        self.assertEqual(ask.call_count, 0, "직접 게재에 AI 가 개입하면 안 된다")
        self.assertEqual(entry["origin"], "직접")
        self.assertIn("<p>오늘의 관측</p>", entry["html"])
        self.assertIn("<p>두 번째 문단</p>", entry["html"])
        # 논제가 플레이스홀더여도 직접 게재는 막지 않는다 (소장의 글이므로)
        self.assertEqual(entry["title"], "소장 노트")

    def test_direct_empty_body_is_refused(self):
        mat = {"id": "m3", "name": "n/m3", "company": "spacex", "mode": "direct",
               "url": "", "memo": "   ", "title": ""}
        entry, note = ce.run_material(mat, COMPANIES, "key", "헌법", [], TODAY)
        self.assertIsNone(entry)
        self.assertIn("본문이 비어", note)


class SilenceTest(unittest.TestCase):
    """③ 중요도 미달 침묵 · ④ 논제 부재 스킵"""

    def test_03_below_threshold_is_silent(self):
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "collect_candidates",
                               return_value=[{"title": "주가 3% 상승", "url": "https://x/1"}]), \
             mock.patch.object(ce, "fetch_article_text", return_value="본문"), \
             mock.patch.object(ce, "ask_claude",
                               return_value={"publish": False, "reason": "단순 주가 변동"}):
            entry = ce.run_auto(COMP, "key", "헌법", [], TODAY)
        self.assertIsNone(entry, "5축에 닿지 않으면 침묵이 정답")

    def test_04_placeholder_thesis_skips_writing(self):
        with mock.patch.object(ce, "read_thesis", return_value=PLACEHOLDER), \
             mock.patch.object(ce, "collect_candidates") as collect, \
             mock.patch.object(ce, "ask_claude") as ask:
            entry = ce.run_auto(COMP, "key", "헌법", [], TODAY)
        self.assertIsNone(entry)
        self.assertEqual(ask.call_count, 0, "논제 없는 에세이는 스크랩이다 — 집필 금지")
        self.assertEqual(collect.call_count, 0, "수집조차 하지 않는다 (비용 낭비 방지)")

    def test_no_candidates_is_silent(self):
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "collect_candidates", return_value=[]), \
             mock.patch.object(ce, "ask_claude") as ask:
            self.assertIsNone(ce.run_auto(COMP, "key", "헌법", [], TODAY))
            self.assertEqual(ask.call_count, 0)

    def test_already_sourced_urls_are_skipped(self):
        """이미 근거로 쓴 URL 은 후보에서 빠진다 — 반복 보도로 같은 글을 두 번 쓰지 않는다."""
        essays = [{"company": "spacex", "title": "t", "sources": ["https://x/1"]}]
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "collect_candidates",
                               return_value=[{"title": "같은 건", "url": "https://x/1"}]), \
             mock.patch.object(ce, "ask_claude") as ask:
            self.assertIsNone(ce.run_auto(COMP, "key", "헌법", essays, TODAY))
            self.assertEqual(ask.call_count, 0)


SRC_EN = ("SpaceX said reusability is the key to lowering launch cost, and the "
          "company reported Starlink revenue of 4.3 billion dollars in the quarter.")
SRC_KO = ("스페이스X는 재사용이 발사 비용을 낮추는 핵심이라고 밝혔으며 이번 분기 "
          "스타링크 매출은 43억 달러로 전체의 55퍼센트를 차지했다고 설명했다.")


class QuoteDisciplineTest(unittest.TestCase):
    """인용 규율 — **원문 대조 방식**.

    2026-08-22 사고: 표면 특징(따옴표·어절 수)으로 검사했더니 한국어 강조 표기와
    한국어 서술문 길이가 인용으로 오인돼 정상 에세이 2편이 막혔다.
    이제는 '수집된 텍스트에 실제로 있는가'라는 정의로 검사한다.
    """

    def test_korean_emphasis_is_not_a_quote(self):
        """오탐 ①: 한국어 강조·용어 표기 — 수집 텍스트에 없으므로 인용이 아니다."""
        html = ('<p>이른바 "검색 사망론"은 아직 실적에 없다. '
                '우리는 이 회사를 "궤도의 건물주"라 부른다. '
                '"통행료형" 사업의 정의가 여기서 갈린다.</p>')
        self.assertEqual(ce.check_quotes(html, SRC_KO, 1), [],
                         "원문에 없는 따옴표는 규율 대상이 아니다")

    def test_korean_sentence_length_is_not_a_long_quote(self):
        """오탐 ②: 한국어 15어절은 짧다 — 어절 수가 아니라 글자 수로 잰다."""
        ko = "환경 특정 타겟 최적 프롬프트 에서만 작동 하며 실제 제약사 공정 에는 아직 못 미친다"
        self.assertGreaterEqual(len(ko.split()), 15)
        self.assertLess(len(ko), ce.KO_CHAR_LIMIT)
        self.assertEqual(ce.check_quotes(f'<p>"{ko}"</p>', SRC_KO, 1), [])

    def test_real_english_quote_within_limit_passes(self):
        html = '<p>회사는 "reusability is the key" 라고 밝혔다.</p>'
        self.assertEqual(ce.check_quotes(html, SRC_EN, 1), [])

    def test_long_english_quote_from_source_flagged(self):
        q = ("SpaceX said reusability is the key to lowering launch cost, "
             "and the company reported Starlink revenue")
        self.assertGreaterEqual(len(q.split()), ce.EN_WORD_LIMIT)
        bad = ce.check_quotes(f'<p>"{q}"</p>', SRC_EN, 1)
        self.assertTrue(any("긴 인용" in b and "단어" in b for b in bad), bad)

    def test_long_korean_quote_from_source_flagged(self):
        """진짜 위반: 수집 텍스트를 60자 이상 그대로 옮긴 인용."""
        q = SRC_KO[:70]
        bad = ce.check_quotes(f'<p>"{q}"</p>', SRC_KO, 1)
        self.assertTrue(any("긴 인용" in b and "자" in b for b in bad), bad)

    def test_too_many_real_quotes_flagged(self):
        html = ('<p>"reusability is the key" 그리고 "lowering launch cost" 또 '
                '"Starlink revenue of 4.3" 이라고 한다.</p>')
        bad = ce.check_quotes(html, SRC_EN, 1)
        self.assertTrue(any("출처당 1회" in b for b in bad), bad)

    def test_bulk_lift_without_quotes_flagged(self):
        """따옴표를 지워도 원문을 통째 옮기면 그게 더 스크랩에 가깝다."""
        bad = ce.check_quotes(f"<p>{SRC_KO}</p>", SRC_KO, 3)
        self.assertTrue(any("통째 옮겨쓰기" in b for b in bad), bad)

    def test_no_collected_text_means_no_quote_judgment(self):
        """수집 텍스트가 없으면 대조할 근거가 없다 — 인용 판정을 하지 않는다."""
        self.assertEqual(ce.check_quotes('<p>"무엇이든"</p>', "", 1), [])

    def test_paywalled_falls_back_to_title_and_summary(self):
        """유료벽: 전문을 못 읽어도 제목·요약은 수집됐다 — 그것만 대조하면 된다."""
        items = [{"title": "Starlink revenue hits record", "article": "",
                  "raw_desc": "Quarterly revenue reached 4.3 billion dollars."}]
        src = ce.collected_text(items)
        self.assertIn("Starlink revenue hits record", src)
        self.assertEqual(ce.check_quotes('<p>"궤도의 건물주"</p>', src, 1), [])


class RegenerateTest(unittest.TestCase):
    """위반 시 즉시 보류가 아니라 사유를 주고 1회 재생성한다."""

    def _auto(self, side_effect):
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "collect_candidates",
                               return_value=[{"title": "t", "url": "https://x/9"}]), \
             mock.patch.object(ce, "fetch_article_text", return_value=SRC_KO), \
             mock.patch.object(ce, "ask_claude", side_effect=side_effect) as ask:
            return ce.run_auto(COMP, "key", "헌법", [], TODAY), ask

    def test_regenerates_once_then_publishes(self):
        bad_res = dict(GOOD, body=f'<p>{SRC_KO}</p>')
        entry, ask = self._auto([bad_res, GOOD])
        self.assertEqual(ask.call_count, 2, "위반 1회는 재생성 기회를 준다")
        self.assertIsNotNone(entry, "재생성이 통과하면 발행된다")
        self.assertIn("인용 규율에 걸렸다", str(ask.call_args.kwargs.get("feedback", "")),
                      "재생성 요청에 반려 사유가 실려야 한다")

    def test_second_violation_holds(self):
        bad_res = dict(GOOD, body=f'<p>{SRC_KO}</p>')
        entry, ask = self._auto([bad_res, bad_res])
        self.assertEqual(ask.call_count, 2, "재생성은 1회뿐 — 무한 반복하지 않는다")
        self.assertIsNone(entry, "두 번째도 걸리면 보류한다")

    def test_silence_does_not_trigger_regeneration(self):
        entry, ask = self._auto([{"publish": False, "reason": "5축 미달"}])
        self.assertEqual(ask.call_count, 1, "침묵은 위반이 아니다")
        self.assertIsNone(entry)


class SourceIntegrityTest(unittest.TestCase):
    """출처 무결성 — 2026-08-23 사고: 모델이 지어낸 문자열이 href 로 올라갔다.

    "https://www.reuters.com (White House ... memo, 2026.08.20" 이 통째로 링크돼
    클릭 불능이었다. 링크로 보이는데 아무 데도 가지 않는 쪽이 링크가 없는 것보다 나쁘다.
    """

    def test_valid_url(self):
        for good in ("https://a.com/x", "http://b.co.kr/p?q=1"):
            self.assertTrue(ce.valid_url(good), good)
        for bad in ("https://www.reuters.com (White House memo, 2026.08.20",
                    "로이터 (원문 기반)", "", None, "ftp://a.com/x", "https://nohost"):
            self.assertFalse(ce.valid_url(bad), repr(bad))

    def test_broken_source_rendered_as_text_not_link(self):
        broken = "https://www.reuters.com (White House memo, 2026.08.20"
        html = ce.tail_html("spacex", [{"url": broken, "full": True, "attempted": True}])
        self.assertNotIn("<a href", html, "URL 이 아닌 값에 링크를 걸면 안 된다")
        self.assertIn("링크 불가", html)

    def test_valid_source_is_linked(self):
        u = "https://www.hellot.net/news/article.html?no=114491"
        html = ce.tail_html("spacex", [{"url": u, "full": True, "attempted": True}])
        self.assertIn(f'<a href="{u}"', html)
        self.assertIn('rel="noopener"', html)

    def test_paywalled_source_is_labelled(self):
        u = "https://www.wsj.com/x"
        html = ce.tail_html("spacex", [{"url": u, "full": False, "attempted": True}])
        self.assertIn(f'<a href="{u}"', html, "전문을 못 읽어도 그 URL 은 실재한다")
        self.assertIn("유료벽 — 제목·요약 기반", html)
        # 시도조차 안 한 소스에 유료벽이라 적으면 그건 거짓말이다
        html2 = ce.tail_html("spacex", [{"url": u, "full": False, "attempted": False}])
        self.assertNotIn("유료벽", html2)
        self.assertIn("제목·요약 기반", html2)

    def test_registry_drops_model_inventions(self):
        """출처의 유일한 원천은 수집 원장 — 모델이 덧붙인 주소는 버린다."""
        items = [{"url": "https://real.com/a", "article": "본문", "article_attempted": True}]
        cited = ["https://real.com/a",
                 "https://www.reuters.com (White House memo, 2026.08.20"]
        reg = ce.source_registry(items, cited)
        self.assertEqual([r["url"] for r in reg], ["https://real.com/a"])

    def test_registry_falls_back_when_nothing_matches(self):
        items = [{"url": "https://real.com/a", "article": "", "article_attempted": True}]
        reg = ce.source_registry(items, ["https://made-up.example/z"])
        self.assertEqual([r["url"] for r in reg], ["https://real.com/a"])
        self.assertFalse(reg[0]["full"])

    def test_model_written_tail_is_stripped(self):
        body = ("<h3>다음 관측 포인트</h3><ul><li>3Q 실적</li></ul>"
                "<h3>출처</h3><ul><li>로이터 (원문 기반)</li></ul>"
                "<p><strong>면책:</strong> …</p>")
        out = ce.strip_model_tail(body)
        self.assertIn("다음 관측 포인트", out, "분석 문단은 건드리지 않는다")
        self.assertNotIn("로이터", out)
        self.assertNotIn("면책", out)

    def test_build_entry_has_single_source_section(self):
        e = ce.build_entry(
            "spacex", "t", "강화",
            "<p>분석</p><h3>출처</h3><ul><li>로이터</li></ul>",
            [{"url": "https://real.com/a", "full": True, "attempted": True}],
            "auto", TODAY)
        self.assertEqual(e["html"].count("<h3>출처</h3>"), 1, "출처 절은 하나뿐이어야 한다")
        self.assertIn("https://real.com/a", e["html"])
        self.assertNotIn("로이터", e["html"])

    def test_rerender_is_idempotent_and_fixes_old(self):
        doc = {"essays": [{
            "company": "spacex", "sources": ["https://ok.com/a", "https://x.com (설명)"],
            "html": ("<p>분석</p><h3>출처</h3><ul class='ce-src'>"
                     "<li><a href=\"https://x.com (설명)\">깨진 링크</a></li></ul>"
                     "<p class='ce-disc'>면책</p>")}]}
        n = ce.rerender_tails(doc)
        self.assertEqual(n, 1)
        h = doc["essays"][0]["html"]
        self.assertIn("<p>분석</p>", h, "본문은 그대로")
        self.assertIn('<a href="https://ok.com/a"', h)
        self.assertNotIn('href="https://x.com (설명)"', h, "깨진 href 가 남으면 안 된다")
        self.assertIn("링크 불가", h)
        self.assertEqual(ce.rerender_tails(doc), 0, "두 번째 실행은 변경 없음(멱등)")

    def test_normalize_accepts_old_string_schema(self):
        recs = ce.normalize_sources(["https://a.com/x"])
        self.assertEqual(recs, [{"url": "https://a.com/x", "full": True, "attempted": True}])


class LedgerTest(unittest.TestCase):
    """원장·판단층 규율"""

    def test_slug_is_idempotent(self):
        a = ce.make_slug("spacex", "제목", TODAY)
        b = ce.make_slug("spacex", "제목", TODAY)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("spacex-"))
        self.assertNotEqual(a, ce.make_slug("spacex", "다른 제목", TODAY))

    def test_real_thesis_files_are_active_v1(self):
        """2026-08-22 원장 v1 반영 — 3사 모두 살아 있어야 엔진이 집필한다.

        머리글 형식은 소장이 정한다(인용줄 `> v1.0 · 날짜 · status: active`).
        기계가 그 형식을 읽지 못하면 심장이 뛰어도 맥을 못 짚는 것이므로
        버전·갱신일까지 실제 파일에서 확인한다.
        """
        for slug in ("spacex", "alphabet", "anthropic"):
            t = ce.read_thesis(slug)
            self.assertFalse(t["placeholder"], f"{slug} 논제가 아직 잠겨 있다")
            self.assertEqual(t["version"], "1.0", slug)
            self.assertEqual(t["updated"], "2026-08-22", slug)
            self.assertIn("핵심 가설", t["body"], slug)
            self.assertIn("반증 조건", t["body"], slug)

    def test_header_formats_both_parse(self):
        """구 YAML 머리글과 신 인용줄 머리글 둘 다 읽는다 (형식 이행 중 사고 방지)."""
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            old_dir = ce.THESIS_DIR
            ce.THESIS_DIR = pathlib.Path(d)
            try:
                nl = chr(10)      # 지뢰 회피: 생성 코드에 개행 이스케이프를 쓰지 않는다
                (ce.THESIS_DIR / "yaml.md").write_text(
                    nl.join(["---", "version: 0", "updated: 2026-08-01",
                             "status: placeholder", "---", "# x", ""]),
                    encoding="utf-8")
                y = ce.read_thesis("yaml")
                self.assertTrue(y["placeholder"])
                self.assertEqual(y["updated"], "2026-08-01")
                (ce.THESIS_DIR / "quote.md").write_text(
                    nl.join(["# T",
                             "> v2.1 · 2026-09-09 · 승인: 소장 · status: active",
                             "", "본문", ""]),
                    encoding="utf-8")
                q = ce.read_thesis("quote")
                self.assertFalse(q["placeholder"])
                self.assertEqual((q["version"], q["updated"]), ("2.1", "2026-09-09"))
            finally:
                ce.THESIS_DIR = old_dir

    def test_anthropic_tail_has_conflict_disclosure(self):
        html = ce.tail_html("anthropic", ["https://example.com/x"])
        self.assertIn("이해관계 고지", html)
        self.assertIn("매수·매도", html)
        self.assertNotIn("이해관계 고지", ce.tail_html("spacex", []))

    def test_verdict_falls_back_safely(self):
        e = ce.build_entry("spacex", "t", "이상한값", "<p>x</p>", [], "auto", TODAY)
        self.assertEqual(e["verdict"], "판단 보류")

    def test_published_today_ignores_direct(self):
        """직접 게재는 하루 1편 상한에서 제외 — 소장이 여러 편 올릴 수 있다."""
        essays = [{"company": "spacex", "date": TODAY, "origin": "직접"}]
        self.assertFalse(ce.published_today(essays, "spacex", TODAY))
        essays.append({"company": "spacex", "date": TODAY, "origin": "auto"})
        self.assertTrue(ce.published_today(essays, "spacex", TODAY))


if __name__ == "__main__":
    unittest.main(verbosity=2)
