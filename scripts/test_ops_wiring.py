#!/usr/bin/env python3
"""
운영 배선 회귀 테스트 (2026-11-09) — 데일리 3.5일·27회 정지에서 드러난 구멍들
① 측정층 배선 테스트가 진짜 매시간 감시 파일을 읽었다(격리 누수) → 오염된 파일을 깔아 두고도 통과해야 한다
② 자가 수리는 workflow_run 으로만 불렸는데 시계 중계가 띄운 회차엔 그 트리거가 울리지 않았다 → 직접 호출
③ 다른 워크플로(매시간 감시)의 데이터 커밋이 다음 데일리를 세웠다 → 커밋 직후 관문 미리보기
④ 시계 중계 재시도 슬롯이 15분마다 다시 쐈다 → 한 번만
실행: python scripts/test_ops_wiring.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import clock_relay as cr        # noqa: E402
import gate_preview as gp       # noqa: E402
import repair_dispatch as rd    # noqa: E402
import spacex_watch as sw       # noqa: E402

ROOT = Path(__file__).parent.parent
KST = timezone(timedelta(hours=9))


class IsolationTest(unittest.TestCase):
    def test_measure_run_ignores_real_hourly_file(self):
        """진짜 자리에 오염된 매시간 기록(예상일 도래·중요 사건)을 깔아도 측정층 배선 테스트는 통과해야 한다."""
        import test_measure_layer as tml
        poison = Path(tempfile.mkdtemp()) / "spacex_hourly.json"
        poison.write_text(json.dumps({"fired": ["X"], "due": [{"reason": "예상일 도래"}, {"reason": "중요 사건"}],
                                      "events": [{"accn": "P-1", "kind": "earnings", "grade": "중요",
                                                  "date": "2027-01-27", "title": "오염"}]}), encoding="utf-8")
        saved = sw.HOURLY_PATH
        try:
            sw.HOURLY_PATH = poison
            r = unittest.TextTestRunner(stream=open("/dev/null", "w"), verbosity=0).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(tml.RunTest))
            self.assertTrue(r.wasSuccessful(), [str(f[1])[-300:] for f in r.failures + r.errors])
            self.assertEqual(sw.HOURLY_PATH, poison)                 # 테스트가 끝나면 원래 경로로 돌려놓는다
        finally:
            sw.HOURLY_PATH = saved


class RepairCallTest(unittest.TestCase):
    def test_dispatch_needs_repo_and_token(self):
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}):
            self.assertFalse(rd.dispatch_repair("now"))

    def test_dispatch_posts_mode(self):
        sent = {}

        class R:
            status_code = 204

        def post(url, headers=None, json=None, timeout=None):
            sent.update(url=url, body=json)
            return R()
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch("requests.post", side_effect=post):
            self.assertTrue(rd.dispatch_repair("future"))
        self.assertIn("self-repair.yml/dispatches", sent["url"])
        self.assertEqual(sent["body"]["inputs"]["mode"], "future")

    def test_gate_preview(self):
        sent, calls = [], []
        rc = gp.main("매시간 감시", runner=lambda: (False, [{"label": "test_measure_layer: 전 케이스 통과", "got": "['x']"}]),
                     send=sent.append, dispatch=calls.append)
        self.assertEqual((rc, calls), (1, ["now"]))
        self.assertIn("다음 데일리 정지 예고", sent[0])
        self.assertIn("test_measure_layer", sent[0])
        sent.clear()
        self.assertEqual(gp.main("매시간 감시", runner=lambda: (True, []), send=sent.append, dispatch=calls.append), 0)
        self.assertEqual(sent, [])


class RelayRetryOnceTest(unittest.TestCase):
    def test_retry_fires_once(self):
        def run(hm, concl):
            t = datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST).astimezone(timezone.utc)
            return {"created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "completed", "conclusion": concl}
        failed = [run("08:16", "failure")]
        self.assertEqual(cr.decide(datetime(2026, 11, 9, 9, 15, tzinfo=KST), failed)[0], "09:10")
        again = failed + [run("09:16", "failure")]
        for hm in ("09:30", "09:45", "10:00"):                       # 실측: 이 세 시각에 또 쐈다
            self.assertIsNone(cr.decide(datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST), again)[0])


class WorkflowWiringTest(unittest.TestCase):
    def _block(self, y, needle):
        i = y.index(needle)
        return y[y.rindex("- name:", 0, i):y.index("- name:", i)] if "- name:" in y[i:] else y[y.rindex("- name:", 0, i):]

    def test_daily_calls_repair_directly(self):
        y = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        b = self._block(y, "python scripts/repair_dispatch.py now")
        self.assertIn("if: failure() && steps.selftest.outcome == 'failure'", b)
        self.assertIn("actions: write", y)
        self.assertLess(y.index("repair_dispatch.py now"), y.index("Hard failure alarm"))
        self.assertIn("GITHUB_TOKEN", self._block(y, "python scripts/timebomb_probe.py"))

    def test_watch_previews_gate_after_commit(self):
        y = (ROOT / ".github" / "workflows" / "spacex-watch.yml").read_text(encoding="utf-8")
        self.assertIn('echo "committed=true" >> "$GITHUB_OUTPUT"', y)
        b = self._block(y, "python scripts/gate_preview.py")
        self.assertIn("if: steps.commit.outputs.committed == 'true'", b)
        self.assertIn("continue-on-error: true", b)
        self.assertNotIn("TELEGRAM_CHAT_ID:", b)                      # 소장 DM 전용
        self.assertLess(y.index("id: commit"), y.index("gate_preview.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
