#!/usr/bin/env python3
"""
SpaceX 뉴스 감시관 테스트 (2026-10-27, 외부 검수 G4)
결정은 기계 규칙이다 — 모델은 분류만 한다. '깨웠다'가 아니라 '정확히 무엇을 몇 건' 으로 단언한다.
실행: python scripts/test_spacex_news.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import spacex_news as sn   # noqa: E402


def H(i, title, pub, date="2026-10-27"):
    return {"i": i, "id": sn.headline_id(title), "title": title, "publisher": pub, "date": date,
            "url": f"https://news.google.com/x/{i}"}


def V(i, cluster, sev="major", material=True, known=False, triggers=None):
    return {"i": i, "material": material, "severity": sev, "category": "자금·신용", "cluster": cluster,
            "already_known": known, "triggers": triggers or [], "claim_ko": f"{cluster} 사건"}


class PureTest(unittest.TestCase):
    def test_clean_title_and_id(self):
        self.assertEqual(sn.clean_title("SpaceX signs deal - Reuters", "Reuters"), "SpaceX signs deal")
        self.assertEqual(sn.headline_id("SpaceX signs deal!"), sn.headline_id("spacex  signs deal"))

    def test_query_url_window(self):
        u = sn.query_url('SpaceX (FAA OR mishap)')
        self.assertTrue(u.startswith("https://news.google.com/rss/search?q=SpaceX"))
        self.assertIn("+when:2d&hl=en-US", u)

    def test_decide_rules(self):
        hs = [H(0, "a", "Reuters"), H(1, "b", "Some Blog"), H(2, "c", "Blog A"), H(3, "c2", "Blog B"),
              H(4, "d", "Bloomberg"), H(5, "e", "Reuters")]
        vs = [V(0, "tier1-single"), V(1, "lone-blog"), V(2, "two-pubs"), V(3, "two-pubs"),
              V(4, "known", known=True), V(5, "minor", sev="minor")]
        wake, dm, ign = sn.decide(hs, vs, 0, {}, "2026-10-27")
        self.assertEqual(sorted(c["cluster"] for c in wake), ["tier1-single", "two-pubs"])
        self.assertEqual([c["cluster"] for c in dm], ["lone-blog"])
        self.assertEqual(dm[0]["why_not"], "발행처 1곳(보도 단계)")
        self.assertEqual(ign, 2)

    def test_daily_cap_and_urgent_bypass(self):
        hs = [H(i, f"t{i}", "Reuters") for i in range(4)]
        vs = [V(0, "m1"), V(1, "m2"), V(2, "m3"), V(3, "u1", sev="urgent")]
        wake, dm, _ = sn.decide(hs, vs, 1, {}, "2026-10-27")       # 오늘 이미 1회 깨움
        self.assertEqual([c["cluster"] for c in wake], ["u1", "m1"])  # 긴급은 상한 밖, 중요는 1회만 더
        self.assertEqual(sorted(c["cluster"] for c in dm), ["m2", "m3"])
        self.assertTrue(all(c["why_not"] == "오늘 깨움 상한 도달" for c in dm))

    def test_cooldown(self):
        hs = [H(0, "a", "Reuters")]
        wake, dm, ign = sn.decide(hs, [V(0, "apollo-gpu-lease")], 0, {"apollo-gpu-lease": "2026-10-25"}, "2026-10-27")
        self.assertEqual((wake, dm, ign), ([], [], 1))
        wake, dm, ign = sn.decide(hs, [V(0, "apollo-gpu-lease")], 0, {"apollo-gpu-lease": "2026-10-15"}, "2026-10-27")
        self.assertEqual(len(wake), 1)


class RunTest(unittest.TestCase):
    def setUp(self):
        self._saved = (sn.NEWS_PATH, sn.WATCH_PATH, sn.STATE_PATH)
        self.tmp = Path(tempfile.mkdtemp())
        sn.NEWS_PATH, sn.WATCH_PATH, sn.STATE_PATH = self.tmp / "n.json", self.tmp / "w.json", self.tmp / "s.json"
        sn.WATCH_PATH.write_text(json.dumps({"events": [], "due": [], "dispatch_queue": []}), encoding="utf-8")
        sn.STATE_PATH.write_text(json.dumps({"triggers_next_12m": [{"id": "T6", "name": "Tesla 합병", "status": "대기"}]}),
                                 encoding="utf-8")

    def tearDown(self):
        sn.NEWS_PATH, sn.WATCH_PATH, sn.STATE_PATH = self._saved

    def _run(self, heads, verdicts=None, judge_exc=None, key="k"):
        import feed_client as fc
        dms = []

        def fake_judge(api_key, batch, triggers, known):
            if judge_exc:
                raise judge_exc
            return [v for v in (verdicts or []) if v["i"] < len(batch)], {"input_tokens": 10, "output_tokens": 5}
        with mock.patch.object(sn, "collect", return_value=[dict(h) for h in heads]), \
                mock.patch.object(sn, "judge", side_effect=fake_judge) as j, \
                mock.patch.object(fc, "flush"), mock.patch.object(fc, "record"), \
                mock.patch("spacex_watch.send_dm", side_effect=lambda t: dms.append(t) or True), \
                mock.patch.object(sn, "now_kst", return_value=datetime.fromisoformat("2026-10-27T10:30:00+09:00")), \
                mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": key, "GITHUB_RUN_ID": "77"}):
            rc = sn.run()
        w = json.loads(sn.WATCH_PATH.read_text(encoding="utf-8"))
        n = json.loads(sn.NEWS_PATH.read_text(encoding="utf-8"))
        return rc, dms, w, n, j.call_count

    def test_baseline_then_wake(self):
        old = [H(0, "old story", "Reuters")]
        rc, dms, w, n, calls = self._run(old)
        self.assertEqual((dms, calls, w["dispatch_queue"]), ([], 0, []))           # 기준선 — 판정·DM 없음
        new = old + [H(1, "Tesla and SpaceX special committees agree terms", "Reuters"),
                     H(2, "Tesla SpaceX merger talks advance", "Bloomberg")]
        rc, dms, w, n, calls = self._run(new, [V(0, "tesla-merger", triggers=["T6"]),
                                               V(1, "tesla-merger", triggers=["T6"])])
        self.assertEqual((rc, calls, len(dms)), (0, 1, 1))
        self.assertEqual(len(w["dispatch_queue"]), 1)
        self.assertEqual(w["dispatch_queue"][0]["run_id"], "77")                  # 커밋 뒤 발사 스텝이 쏜다
        self.assertEqual([d["trigger_id"] for d in w["due"]], ["T6"])
        self.assertIn("보도는 판단 근거가 아니라", dms[0])
        self.assertEqual(n["cooldown"], {"tesla-merger": "2026-10-27"})
        # 같은 헤드라인은 다시 판정하지 않는다
        rc, dms, w, n, calls = self._run(new, [])
        self.assertEqual((calls, dms), (0, []))

    def test_judge_failure_retries_and_reports(self):
        self._run([H(0, "old", "Reuters")])
        heads = [H(0, "old", "Reuters"), H(1, "FAA grounds Starship", "Reuters")]
        rc, dms, w, n, calls = self._run(heads, judge_exc=RuntimeError("500"))
        self.assertEqual(rc, 1)
        self.assertEqual(len(dms), 1)
        self.assertIn("판정 모델 호출 실패", dms[0])
        self.assertNotIn(sn.headline_id("FAA grounds Starship"), n["seen"])       # 다음 회차에 다시 판정
        self.assertEqual(w["dispatch_queue"], [])

    def test_report_stage_dm_only(self):
        self._run([H(0, "old", "Reuters")])
        heads = [H(0, "old", "Reuters"), H(1, "Rumor: SpaceX CFO to leave", "Some Blog")]
        rc, dms, w, n, calls = self._run(heads, [V(0, "cfo-exit", sev="urgent")])
        self.assertEqual(w["dispatch_queue"], [])
        self.assertEqual(len(dms), 1)
        self.assertIn("보도 단계", dms[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
