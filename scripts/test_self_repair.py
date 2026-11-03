#!/usr/bin/env python3
"""
자가 수리 에이전트 테스트 (2026-11-03)
막으려는 것: selftest 가 멈춘 채로 사람이 볼 때까지 시간이 흐르는 것 — 그리고 그 반대편의 위험,
'테스트를 약하게 만들어 통과시키는 수리'가 main 에 들어가는 것.
실행: python scripts/test_self_repair.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import self_repair as sr   # noqa: E402

ROOT = Path(__file__).parent.parent
TODAY = "2026-11-03"


class DecideTest(unittest.TestCase):
    def test_daily_selftest_failure_is_now(self):
        d = sr.decide("workflow_run", "failure", True, {}, TODAY)
        self.assertEqual(d["mode"], "now")

    def test_other_daily_failures_are_not_repaired(self):
        """네트워크 등 selftest 밖의 실패는 수리 대상이 아니다(코드가 아니라 세상이 문제)."""
        self.assertEqual(sr.decide("workflow_run", "failure", False, {}, TODAY)["mode"], "none")

    def test_timebomb_future_failure(self):
        tb = {"checked": TODAY, "found": [{"offset": 14, "date": "2026-11-17", "labels": ["x"]},
                                          {"offset": 3, "date": "2026-11-06", "labels": ["y"]}]}
        d = sr.decide("workflow_run", "success", False, tb, TODAY)
        self.assertEqual((d["mode"], d["date"]), ("future", "2026-11-06"))

    def test_next_run_failure_is_now(self):
        tb = {"checked": TODAY, "found": [{"offset": 0, "date": TODAY, "labels": ["names"]}]}
        self.assertEqual(sr.decide("workflow_run", "success", False, tb, TODAY)["mode"], "now")

    def test_old_timebomb_record_ignored(self):
        tb = {"checked": "2026-11-01", "found": [{"date": "2026-11-06", "labels": ["y"]}]}
        self.assertEqual(sr.decide("workflow_run", "success", False, tb, TODAY)["mode"], "none")

    def test_forced(self):
        self.assertEqual(sr.decide("workflow_dispatch", "", False, {}, TODAY, forced="now")["mode"], "now")
        self.assertEqual(sr.decide("workflow_dispatch", "", False, {}, TODAY, forced="future")["mode"], "none")


class GuardTest(unittest.TestCase):
    B = {"scripts/test_a.py": {"tests": 5, "asserts": 20, "checks": 0, "skips": 0},
         "scripts/selftest.py": {"tests": 0, "asserts": 0, "checks": 300, "skips": 0}}

    def test_clean_repair_passes(self):
        a = {"scripts/test_a.py": {"tests": 6, "asserts": 22, "checks": 0, "skips": 0},
             "scripts/selftest.py": {"tests": 0, "asserts": 0, "checks": 300, "skips": 0}}
        g = sr.guard(self.B, a, ["scripts/spacex_watch.py", "scripts/test_a.py", "CLAUDE.md"], [])
        self.assertEqual((g["ok"], g["violations"], g["flags"]), (True, [], []))
        self.assertTrue(sr.can_auto_merge("auto", g))
        self.assertFalse(sr.can_auto_merge("pr", g))

    def test_weakened_tests_rejected(self):
        a = {"scripts/test_a.py": {"tests": 4, "asserts": 20, "checks": 0, "skips": 1},
             "scripts/selftest.py": {"tests": 0, "asserts": 0, "checks": 299, "skips": 0}}
        g = sr.guard(self.B, a, ["scripts/test_a.py"], ["        self.assertEqual(x, 1)"])
        self.assertFalse(g["ok"])
        joined = " ".join(g["violations"])
        for k in ("tests 5 → 4", "checks 300 → 299", "건너뛰기 추가"):
            self.assertIn(k, joined)

    def test_forbidden_and_outside_paths(self):
        g = sr.guard(self.B, self.B, [".github/workflows/daily-update.yml", "agent-research/state/x.json",
                                      "data/buffett_config.json", "data/latest.json"], [])
        self.assertFalse(g["ok"])
        self.assertEqual(len(g["violations"]), 4)

    def test_edited_assertions_flagged_and_block_auto_merge(self):
        g = sr.guard(self.B, self.B, ["scripts/test_a.py"], ['        self.assertIn("a", b)'])
        self.assertTrue(g["ok"])
        self.assertTrue(g["flags"])
        self.assertFalse(sr.can_auto_merge("auto", g))                # 검사 줄을 고친 수리는 사람이 본다

    def test_no_change_is_not_a_repair(self):
        self.assertFalse(sr.guard(self.B, self.B, [], [])["ok"])

    def test_deleted_test_file_rejected(self):
        a = {"scripts/selftest.py": self.B["scripts/selftest.py"]}
        self.assertFalse(sr.guard(self.B, a, ["scripts/test_a.py"], [])["ok"])

    def test_inventory_counts_real_repo(self):
        inv = sr.inventory(ROOT)
        self.assertGreater(inv["scripts/selftest.py"]["checks"], 100)
        self.assertGreater(inv["scripts/test_self_repair.py"]["tests"], 10)

    def test_inventory_counts_fixture(self):
        d = Path(tempfile.mkdtemp())
        (d / "scripts").mkdir()
        (d / "scripts" / "test_x.py").write_text(
            "class T:\n    def test_a(self):\n        self.assertEqual(1, 1)\n        assert True\n"
            "    @unittest.skip('x')\n    def test_b(self):\n        pass\n", encoding="utf-8")
        (d / "scripts" / "selftest.py").write_text("check('a', 1, 1)\ncheck('b', 2, 2)\n_x.check(1)\n",
                                                   encoding="utf-8")
        inv = sr.inventory(d)
        self.assertEqual(inv["scripts/test_x.py"], {"tests": 2, "asserts": 2, "checks": 0, "skips": 1})
        self.assertEqual(inv["scripts/selftest.py"]["checks"], 2)


class MessageTest(unittest.TestCase):
    def test_signature_stable(self):
        a = sr.signature([{"label": "x"}, {"label": "y"}])
        self.assertEqual(a, sr.signature([{"label": "y"}, {"label": "x"}]))
        self.assertEqual(len(a), 8)

    def test_prompt_carries_rules_and_failures(self):
        p = sr.build_prompt("future", "2026-11-17", [{"label": "test_x: 전 케이스 통과", "got": "['t1']"}])
        for k in ("테스트를 약하게 만들어 통과시키지 마라", "timebomb_probe.py --at 2026-11-17", "test_x",
                  "agent-research/", "_repair/report.md", "CLAUDE.md"):
            self.assertIn(k, p)
        self.assertNotIn("timebomb_probe.py --at", sr.build_prompt("now", "", []))

    def test_messages(self):
        m = sr.fail_message("now", "", [{"label": "t"}], "검사 감소", "https://x/runs/1")
        self.assertIn("사람 개입 필요", m)
        self.assertIn("검사 감소", m)
        ok = sr.ok_message("future", "2026-11-17", "시계 입구 수리", {"flags": ["검사 줄 1개 수정 — 사람이 반드시 확인"]},
                           "https://x/pull/1", False)
        self.assertIn("2026-11-17 부터 실패할 예정", ok)
        self.assertIn("⚠️ 검사 줄", ok)
        self.assertIn("Merge", ok)


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.y = (ROOT / ".github" / "workflows" / "self-repair.yml").read_text(encoding="utf-8")

    def test_triggers_and_safety(self):
        self.assertIn('workflows: ["Daily Universe Dominator Update"]', self.y)
        self.assertNotIn("TELEGRAM_CHAT_ID:", self.y)                          # 공개 채널 금지
        allow = [ln for ln in self.y.splitlines() if "REPAIR_ALLOW:" in ln][0]
        self.assertNotIn("WebFetch", allow)                                    # 네트워크 도구 없음
        self.assertNotIn("Bash,", allow)                                       # 맨 Bash 허용 금지
        self.assertIn("steps.guard.outputs.ok == 'true'", self.y)              # 검사 통과해야 게시
        self.assertLess(self.y.index("Guard ("), self.y.index("Publish repair"))
        self.assertIn("REPAIR_MERGE: ${{ vars.REPAIR_MERGE || 'pr' }}", self.y)  # 기본은 사람이 Merge

    def test_daily_alarm_mentions_repair(self):
        d = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertIn("자가 수리 에이전트", d)

    def test_timebomb_at_command(self):
        import timebomb_probe as tb
        self.assertTrue(callable(getattr(tb, "at_date", None)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
