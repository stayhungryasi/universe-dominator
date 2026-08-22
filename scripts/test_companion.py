#!/usr/bin/env python3
"""
동행 관측 엔진 검증 (test_companion) — 침묵이 정답인 경우를 특히 본다.

핵심 4케이스 (선장님 요구 사양):
  ① 소재 에세이화   — 링크+메모 → 집필 → 원장 등재
  ② 직접 게재       — 소장 글 그대로, AI 호출 0회 (origin="직접")
  ③ 중요도 미달     — 5축 미달 판정 시 침묵 (원장 불변)
  ④ 논제 부재       — 원장이 플레이스홀더면 집필 스킵 ("논제 없는 에세이는 스크랩")
추가: 인용 규율(출처당 1회·15단어 미만) 출력 검사.

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


class QuoteDisciplineTest(unittest.TestCase):
    """인용 규율 — 스크랩 방지선. 위반이면 발행을 보류한다."""

    def test_clean_passes(self):
        html = '<p>회사는 "reusability is the key" 라고 밝혔다.</p>'
        self.assertEqual(ce.check_quotes(html, 1), [])

    def test_long_quote_flagged(self):
        long_q = " ".join(["word"] * 20)
        self.assertTrue(any("긴 인용" in b for b in ce.check_quotes(f'<p>"{long_q}"</p>', 1)))

    def test_too_many_quotes_flagged(self):
        html = '<p>"first quote here" 그리고 "second quote here" 또 "third quote here"</p>'
        bad = ce.check_quotes(html, 1)
        self.assertTrue(any("출처당 1회" in b for b in bad), bad)

    def test_violation_blocks_publication(self):
        long_q = " ".join(["word"] * 20)
        bad_res = dict(GOOD, body=f'<p>"{long_q}"</p>')
        with mock.patch.object(ce, "read_thesis", return_value=LIVE_THESIS), \
             mock.patch.object(ce, "collect_candidates",
                               return_value=[{"title": "t", "url": "https://x/9"}]), \
             mock.patch.object(ce, "fetch_article_text", return_value=""), \
             mock.patch.object(ce, "ask_claude", return_value=bad_res):
            self.assertIsNone(ce.run_auto(COMP, "key", "헌법", [], TODAY),
                              "인용 규율 위반은 발행되지 않는다")


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
