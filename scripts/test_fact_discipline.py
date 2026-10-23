#!/usr/bin/env python3
"""
사실 규율 테스트 (2026-10-23, 알려진 지뢰 ⑲) — 숫자 밖의 거짓
================================================================================
Flight 14 회수 에세이가 숫자 규율을 통과했다(숫자가 '14' 하나뿐). 그런데 본문은
"부스터가 궤도에 진입"(실제: 분리 후 멕시코만 연착수) · "재사용 가능성을 갖춘 채 도달"(선체는 태평양 착수 후 폭발)
을 썼다. 원천은 발사 전 예고 기사와 유료벽 제목뿐이었고, 측정층의 발사 기록과 8-K 문장은 쓰이지 않았다.
  ① 원문이 지지하지 않는 주장 → 사유 주고 1회 재생성 → 그래도면 미게시
  ② 검증관은 작성 맥락을 모른다(헌법·논제 원장·메모·반려 사유·숫자 목록 미전달)
  ③ 검증이 실패하면 미게시(침묵은 통과가 아니다)
  ④ 사건 에세이는 사건 이후의 1차 원천이 없으면 쓰지 않는다
  ⑤ SpaceX 사건은 발사 기록·8-K 언급 문장이 자료에 자동으로 들어간다
각 규칙마다 변이(수리를 되돌린 판)에서 테스트가 실제로 뒤집히는지 확인한다.
실행: python scripts/test_fact_discipline.py
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import companion_essays as ce   # noqa: E402

COMP = {"slug": "spacex", "ko": "SpaceX", "emoji": "🚀", "sources": []}
CONSTITUTION = "헌법-표식-ZQX"
THESIS = {"body": "논제원장-표식-ZQX", "placeholder": False}
MEMO = "소장메모-표식-ZQX"

# 실제 Launch Library 2 기록(Flight 14) 모양 — 계획 문구와 결과 줄이 함께 있다
LL2 = {
    "name": "Starship | Starlink Group 31-1 (Starship Flight 14)", "net": "2026-09-28T12:48:59Z",
    "last_updated": "2026-09-29T15:33:32Z",
    "status": {"name": "Launch Successful",
               "description": "The launch vehicle successfully inserted its payload(s) into the target orbit(s)."},
    "rocket": {
        "launcher_stage": [{"launcher": {"serial_number": "Booster 21", "status": {"name": "lost"},
                                         "details": "Lost to the ocean after a successful soft splashdown."},
                            "landing": {"attempt": True, "success": True,
                                        "description": "The Super Heavy Booster 21 has performed a soft splashdown in the Gulf of Mexico.",
                                        "landing_location": {"name": "Gulf of Mexico"}}}],
        "spacecraft_stage": [{"spacecraft": {"name": "Ship 41", "status": {"name": "Destroyed"},
                                             "time_in_space": "PT3H8M30S",
                                             "description": "Lost in an explosion just after the soft splashdown."},
                              "landing": {"attempt": True, "success": True,
                                          "description": "Ship 41 has performed a soft splashdown in the Pacific Ocean after deploying its payload.",
                                          "landing_location": {"name": "Pacific Ocean"}}}]},
    "updates": [{"created_on": "2026-09-28T14:08:00Z",
                 "comment": "All Starlink satellites deployed and in contact with ground stations."}],
    "mission": {"description": "The booster's primary test objective on Flight 14 will be executing a "
                               "successful launch, ascent, stage separation, boostback burn, and landing burn."},
}
LL2_ITEM = {"title": "발사 기록(Launch Library 2) — Flight 14", "url": "https://ll.thespacedevs.com/2.4.0/launches/x/",
            "pub": "2026-09-29", "article": ce.ll2_text(LL2), "article_attempted": True, "primary": True,
            "event_kind": "launch", "grade": "발사 기록(Launch Library 2, 사건 후 갱신)", "focus_ok": True}
PREVIEW = {"title": "Will SpaceX's Starship finally make it to orbit? We're about to find out",
           "url": "https://www.space.com/preview", "pub": "Fri, 25 Sep 2026 12:00:00 GMT",
           "article": "SpaceX is targeting Sept. 28 for Starship's Flight 14. " * 20, "article_attempted": True}
PAYWALL = {"title": "SpaceX's Starship launches 14th flight, first headed to orbit",
           "url": "https://www.reuters.com/x", "pub": "Mon, 28 Sep 2026 14:00:00 GMT",
           "article": "", "article_attempted": True}

BOOSTER_CLAIM = "<p>9월 28일 Flight 14에서 슈퍼 헤비 부스터와 선체가 모두 궤도에 진입했다.</p>"
GOOD_CLAIM = "<p>9월 28일 Flight 14에서 선체가 Starlink 위성을 배치한 뒤 태평양에 착수했다.</p>"


def draft(body, **kw):
    d = {"publish": True, "title": "Flight 14", "verdict": "불변", "body": body, "sources": [],
         "event_type": "발사", "event_date": "2026-09-28"}
    d.update(kw)
    return d


def fake_judge(claims_by_body):
    """검증관 응답을 흉내 낸다 — 원고 글자에 따라 판정을 돌려준다(요청 본문은 기록해 둔다)."""
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append(json)
        content = json["messages"][0]["content"]
        payload = __import__("json").loads(content[content.index("{\"사건일\""):])
        claims = claims_by_body(payload["원고"], payload)
        r = mock.Mock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"content": [{"type": "text", "text": __import__("json").dumps(
            {"claims": claims}, ensure_ascii=False)}], "usage": {}, "stop_reason": "end_turn"}
        return r
    return post, seen


def booster_judge(text, payload):
    """최소 판정기: 자료가 '부스터 멕시코만 착수'를 말하는데 원고가 '부스터 궤도 진입'이면 반박."""
    src = " ".join(s["텍스트"] for s in payload["자료"])
    if "부스터" in text and "궤도에 진입" in text and "Gulf of Mexico" in src:
        return [{"주장": "부스터가 궤도에 진입했다", "판정": "반박", "자료": 1,
                 "근거": "soft splashdown in the Gulf of Mexico", "사유": "부스터는 멕시코만에 착수했다"}]
    return [{"주장": "선체가 위성을 배치한 뒤 태평양에 착수했다", "판정": "지지", "자료": 1,
             "근거": "soft splashdown in the Pacific Ocean after deploying its payload", "사유": ""}]


class FactFlowTest(unittest.TestCase):
    def _write(self, drafts, judge=booster_judge, items=None, required=True):
        post, seen = fake_judge(judge)
        with mock.patch.object(ce, "ask_claude", side_effect=drafts) as ask, \
             mock.patch.object(ce.requests, "post", side_effect=post):
            res, viol = ce.write_with_discipline("k", CONSTITUTION, COMP, THESIS, items or [LL2_ITEM, PREVIEW],
                                                 [], memo=MEMO, event_required=required,
                                                 focus_date="2026-09-28")
        return res, viol, ask, seen

    def test_repro_booster_orbit_claim_is_blocked(self):
        res, viol, ask, seen = self._write([draft(BOOSTER_CLAIM), draft(BOOSTER_CLAIM)])
        self.assertIn("원문이 지지하지 않는 주장", viol)
        self.assertEqual(ask.call_count, 2, "1회 재생성 기회")
        self.assertEqual(len(seen), 2, "재생성 원고도 다시 검증한다")
        fb = ask.call_args.kwargs["feedback"]
        self.assertIn("부스터가 궤도에 진입", fb)
        self.assertIn("멕시코만", fb)

    def test_corrected_rewrite_publishes(self):
        res, viol, ask, _ = self._write([draft(BOOSTER_CLAIM), draft(GOOD_CLAIM)])
        self.assertIsNone(viol)
        self.assertTrue(res["facts_verified"])

    def test_verifier_gets_no_writing_context(self):
        _, _, _, seen = self._write([draft(BOOSTER_CLAIM), draft(GOOD_CLAIM)])
        for req in seen:
            self.assertEqual(leaked(req), [])
            content = req["messages"][0]["content"]
            self.assertIn("Gulf of Mexico", content)              # 수집 원문은 간다
            self.assertIn("궤도에 진입", seen[0]["messages"][0]["content"])   # 원고 글자는 간다
        self.assertEqual(seen[0]["model"], ce.models()[1])

    def test_verifier_failure_holds(self):
        def broken(text, payload):
            raise ValueError("깨진 응답")
        res, viol, _, _ = self._write([draft(GOOD_CLAIM)], judge=broken)
        self.assertIn("사실 검증 실행 실패 — 미게시", viol)

    def test_unknown_or_unsupported_verdict_counts(self):
        self.assertEqual(len(ce._unsupported([{"판정": "불지지"}, {"판정": "반박"}, {"판정": ""},
                                              {"판정": "부분 지지"}, {"판정": "지지"}])), 4)

    def test_pre_event_preview_and_paywall_title_only_is_held_before_writing(self):
        res, viol, ask, seen = self._write([draft(GOOD_CLAIM)], items=[PREVIEW, PAYWALL])
        self.assertIn("사건 이후의 1차 원천 없음", viol)
        self.assertEqual(ask.call_count, 0, "원천이 없으면 쓰지도 않는다")
        self.assertEqual(seen, [])

    def test_event_type_essay_needs_primary_even_outside_focus(self):
        res, viol, ask, _ = self._write([draft(GOOD_CLAIM)], items=[PREVIEW, PAYWALL], required=False)
        self.assertIn("사건 이후의 1차 원천 없음", viol)
        self.assertEqual(ask.call_count, 1)

    def test_non_event_essay_still_fact_checked(self):
        res, viol, _, seen = self._write([draft(BOOSTER_CLAIM, event_type="기타"),
                                          draft(BOOSTER_CLAIM, event_type="기타")], required=False)
        self.assertIn("원문이 지지하지 않는 주장", viol)
        self.assertEqual(len(seen), 2)


def leaked(req):
    """검증관 요청에 작성 맥락이 섞였는가 — 표식 문자열·작성용 입력 키를 찾는다."""
    blob = json.dumps(req, ensure_ascii=False)
    return [m for m in ("헌법-표식-ZQX", "논제원장-표식-ZQX", "소장메모-표식-ZQX",
                        "사용_가능한_숫자", "직전_원고_반려사유", "논제원장") if m in blob]


class PrimarySourceTest(unittest.TestCase):
    def test_official_pre_event_article_does_not_count(self):
        pre = {"url": "https://ir.spacex.com/x", "pub": "2026-09-20", "article": "x" * 2000}
        post = dict(pre, pub="2026-10-20")
        self.assertEqual(ce.primary_after_event([pre], "2026-09-28"), [])
        self.assertEqual(len(ce.primary_after_event([post], "2026-09-28")), 1)

    def test_third_party_full_article_is_not_primary(self):
        self.assertEqual(ce.primary_after_event([dict(PREVIEW, pub="2026-09-29")], "2026-09-28"), [])

    def test_ll2_record_counts(self):
        self.assertEqual(len(ce.primary_after_event([LL2_ITEM], "2026-09-28")), 1)

    def test_ll2_text_separates_plan_from_result(self):
        t = LL2_ITEM["article"]
        self.assertIn("soft splashdown in the Gulf of Mexico", t)
        self.assertIn("Lost in an explosion", t)
        self.assertIn("임무 설명(발사 전에 쓴 계획 문구 — 결과가 아니다)", t)


class LaunchRecordWiringTest(unittest.TestCase):
    """측정층 원장(spacex_watch.json)의 발사 기록이 자료로 들어온다 — 도구가 있어도 배선해야 있는 것이다."""
    WATCH = {"events": [
        {"date": "2026-09-28", "title": "Starship Flight 14 — 발사 성공", "source": "Launch Library 2",
         "url": "https://ll.thespacedevs.com/2.4.0/launches/x/", "kind": "starship"},
        {"date": "2026-07-24", "title": "Flight 13 — 발사 성공", "source": "Launch Library 2",
         "url": "https://ll.thespacedevs.com/2.4.0/launches/y/", "kind": "starship"},
        {"date": "2026-10-20", "title": "실적", "source": "SEC EDGAR", "url": "https://sec/x", "kind": "earnings"}]}

    def _items(self, comp=COMP, focus="기본"):
        focus = FOCUS if focus == "기본" else focus
        r = mock.Mock()
        r.raise_for_status = lambda: None
        r.json = lambda: LL2
        with mock.patch.object(ce, "load_json", return_value=self.WATCH),              mock.patch.object(ce.requests, "get", return_value=r) as get:
            return ce.spacex_launch_items(comp, 30, today="2026-10-23", focus=focus), get

    def test_focus_pulls_only_that_launch(self):
        items, get = self._items()
        self.assertEqual(len(items), 1)
        self.assertEqual(get.call_args.args[0], "https://ll.thespacedevs.com/2.4.0/launches/x/")
        self.assertTrue(items[0]["primary"] and items[0]["focus_ok"])
        self.assertIn("Gulf of Mexico", items[0]["article"])
        self.assertEqual(len(ce.primary_after_event(items, "2026-09-28")), 1)

    def test_other_companies_get_nothing(self):
        items, get = self._items(comp={"slug": "alphabet", "ko": "Alphabet"})
        self.assertEqual(items, [])
        self.assertEqual(get.call_count, 0)


SEC_TEXT = ("SpaceX Reports Third Quarter 2026 Results. Delivered revenue of $14.6 billion. "
            "Completed Starship Flight 14, our first orbital Starship flight, delivering 26 Starlink V3 "
            "satellites to our broadband constellation. Entered into an agreement to acquire FCC licenses.")
SEC_ITEM = {"title": "SEC 8-K 2026-10-20 — 실적 (원문)", "url": "https://www.sec.gov/Archives/x.htm",
            "pub": "2026-10-20", "article": SEC_TEXT, "article_attempted": True, "primary": True,
            "event_kind": "earnings"}
FOCUS = {"date": "2026-09-28", "label": "Starship Flight 14 첫 궤도 비행"}


class FocusWiringTest(unittest.TestCase):
    def test_excerpt_keeps_only_event_sentences(self):
        ex = ce.focus_excerpt(SEC_ITEM, FOCUS)
        self.assertIn("Completed Starship Flight 14, our first orbital Starship flight", ex["article"])
        self.assertNotIn("14.6 billion", ex["article"])
        self.assertIn("발췌", ex["grade"])
        self.assertIsNone(ce.focus_excerpt(SEC_ITEM, {"date": "2026-09-28", "label": "Flight 15"}))

    def _run_auto(self, essays=()):
        got = {}

        def capture(api_key, constitution, comp, thesis, items, recent, memo="", **kw):
            got["items"], got["kw"] = items, kw
            return {"publish": False, "reason": "테스트"}, None
        with mock.patch.object(ce, "read_thesis", return_value=THESIS), \
             mock.patch.object(ce, "collect_candidates", return_value=[PREVIEW, PAYWALL]), \
             mock.patch.object(ce, "sec_primary_items", return_value=[SEC_ITEM]), \
             mock.patch.object(ce, "spacex_launch_items", return_value=[LL2_ITEM]), \
             mock.patch.object(ce, "fetch_article_text", return_value=""), \
             mock.patch.object(ce, "write_with_discipline", side_effect=capture):
            ce.run_auto(COMP, "k", CONSTITUTION, list(essays), "2026-10-23", widen_days=30, focus=FOCUS)
        return got

    def test_focus_run_feeds_ll2_and_8k_sentence(self):
        # 8-K 원문은 이미 3Q 에세이가 출처로 썼다 — 그래도 Flight 14 문장 발췌는 들어가야 한다
        used = [{"company": "spacex", "title": "3Q", "date": "2026-10-23", "html": "",
                 "sources": [{"url": SEC_ITEM["url"], "full": True}]}]
        got = self._run_auto(used)
        urls = [x["url"] for x in got["items"]]
        self.assertIn(LL2_ITEM["url"], urls)
        ex = [x for x in got["items"] if x["url"] == SEC_ITEM["url"]]
        self.assertEqual(len(ex), 1)
        self.assertIn("first orbital Starship flight", ex[0]["article"])
        self.assertTrue(got["kw"]["event_required"])
        self.assertEqual(got["kw"]["focus_date"], "2026-09-28")


class MutationTest(unittest.TestCase):
    """변이 — 수리를 되돌리면 위 테스트가 **반드시** 뒤집힌다(실패할 수 없는 테스트는 감시자가 아니다)."""

    def test_m1_ignoring_verifier_lets_booster_claim_through(self):
        with mock.patch.object(ce, "verify_facts", return_value=([], 1)):
            with mock.patch.object(ce, "ask_claude", side_effect=[draft(BOOSTER_CLAIM)]):
                _, viol = ce.write_with_discipline("k", CONSTITUTION, COMP, THESIS, [LL2_ITEM], [],
                                                   event_required=True, focus_date="2026-09-28")
        self.assertIsNone(viol)

    def test_m2_leak_detector_catches_context(self):
        req = {"messages": [{"content": ce.FACT_PROMPT + json.dumps({"원고": "x", "메모": MEMO},
                                                                      ensure_ascii=False)}]}
        self.assertEqual(leaked(req), ["소장메모-표식-ZQX"])

    def test_m3_dropping_primary_rule_writes_from_preview(self):
        with mock.patch.object(ce, "primary_after_event", return_value=[PREVIEW]), \
             mock.patch.object(ce, "verify_facts", return_value=([], 1)), \
             mock.patch.object(ce, "ask_claude", side_effect=[draft(GOOD_CLAIM)]) as ask:
            _, viol = ce.write_with_discipline("k", CONSTITUTION, COMP, THESIS, [PREVIEW, PAYWALL], [],
                                               event_required=True, focus_date="2026-09-28")
        self.assertIsNone(viol)
        self.assertEqual(ask.call_count, 1)

    def test_m4_counting_only_rebuttals_misses_unsupported(self):
        def no_basis(text, payload):            # '근거 없음' 판정 — 반박은 아니다
            return [{"주장": "여섯 번의 실패 끝에 도달했다", "판정": "불지지", "사유": "자료에 없다"}]
        flow = FactFlowTest()
        _, viol, _, _ = flow._write([draft(GOOD_CLAIM), draft(GOOD_CLAIM)], judge=no_basis)
        self.assertIn("원문이 지지하지 않는 주장", viol)          # 정상: 불지지도 막는다
        with mock.patch.object(ce, "_unsupported", lambda cs: [c for c in cs if c.get("판정") == "반박"]):
            _, viol, _, _ = flow._write([draft(GOOD_CLAIM)], judge=no_basis)
        self.assertIsNone(viol)                                   # 변이: 반박만 세면 새어 나간다

    def test_m5_without_excerpt_8k_sentence_is_lost(self):
        with mock.patch.object(ce, "focus_excerpt", return_value=None):
            got = FocusWiringTest._run_auto(self)
        self.assertNotIn(SEC_ITEM["url"], [x["url"] for x in got["items"]])

    def test_m6_without_launch_record_focus_is_held(self):
        with mock.patch.object(ce, "ask_claude", side_effect=[draft(GOOD_CLAIM)]) as ask:
            _, viol = ce.write_with_discipline("k", CONSTITUTION, COMP, THESIS, [PREVIEW, PAYWALL], [],
                                               event_required=True, focus_date="2026-09-28")
        self.assertIn("사건 이후의 1차 원천 없음", viol)
        self.assertEqual(ask.call_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
