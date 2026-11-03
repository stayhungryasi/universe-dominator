#!/usr/bin/env python3
"""
시한폭탄 조기경보 테스트 (2026-11-03) — 미래 날짜로 돌린 selftest 가 실패를 '그날 전에' 잡는가
실행: python scripts/test_timebomb.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import timebomb_probe as tb   # noqa: E402

KST = timezone(timedelta(hours=9))
# 가짜 selftest — 2026-11-20 이 지나면 실패한다(실제 시한폭탄과 같은 꼴: 실제 시계를 읽는다)
FAKE = (
    "import json, os, sys\n"
    "from datetime import datetime, timezone\n"
    "fails = []\n"
    "if datetime.now(timezone.utc) > datetime(2026, 11, 20, tzinfo=timezone.utc):\n"
    "    fails.append({'label': 'test_fake: 전 케이스 통과', 'got': \"['test_launch_wiring']\"})\n"
    "out = os.environ.get('SELFTEST_FAILS_OUT')\n"
    "open(out, 'w').write(json.dumps({'fails': [f['label'] for f in fails], 'detail': fails}))\n"
    "sys.exit(1 if fails else 0)\n"
)


class TimebombTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = self.tmp / "selftest.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self._saved = tb.STATE_PATH
        tb.STATE_PATH = self.tmp / "state.json"

    def tearDown(self):
        tb.STATE_PATH = self._saved

    def test_future_failure_caught_before_the_day(self):
        now = datetime(2026, 11, 10, 9, 0, tzinfo=KST)
        found = tb.probe((3, 14, 45), now, self.fake)
        self.assertEqual([x["offset"] for x in found], [14, 45])         # +3일(11/13)은 통과, +14일(11/24)부터 실패
        self.assertIn("2026-11-24(+14일)", tb.message(found))
        self.assertIn("test_launch_wiring", tb.message(found))

    def test_output_failure_is_next_run_warning(self):
        """+0일 = 다음 회차 관문 미리보기 — 시계와 무관하게 실패하면 '다음 회차 정지 예고'로 운다."""
        bad = self.tmp / "out.py"
        bad.write_text("import os,json;open(os.environ['SELFTEST_FAILS_OUT'],'w').write(json.dumps("
                       "{'fails':['산출물'],'detail':[{'label':'산출물 예산','got':'407KB'}]}))\n", encoding="utf-8")
        found = tb.probe((0, 3), datetime(2026, 11, 10, 9, 0, tzinfo=KST), bad)
        self.assertEqual([x["offset"] for x in found], [0, 3])
        msg = tb.message(found)
        self.assertIn("다음 회차 정지 예고", msg)
        self.assertNotIn("시한폭탄", msg.splitlines()[0])
        self.assertIn(0, tb.OFFSETS)

    def test_clock_really_moves(self):
        """시계가 정말 옮겨지는가 — 오늘 날짜로는 통과해야 한다(가짜 selftest 는 실제 시계를 읽는다)."""
        fails, err = tb.run_at(datetime(2026, 11, 1, tzinfo=KST), self.fake)
        self.assertEqual((fails, err), ([], None))
        fails, err = tb.run_at(datetime(2026, 12, 1, tzinfo=KST), self.fake)
        self.assertEqual(len(fails), 1)

    def test_crash_is_unverifiable_not_pass(self):
        bad = self.tmp / "bad.py"
        bad.write_text("raise SystemExit(3)\n", encoding="utf-8")
        fails, err = tb.run_at(datetime(2026, 11, 1, tzinfo=KST), bad)
        self.assertEqual(fails, [])
        self.assertIn("실행 실패", err)                                  # 침묵은 통과가 아니다

    def test_once_a_day_and_realert(self):
        sent = []
        with mock.patch.object(tb, "now_kst", return_value=datetime(2026, 11, 10, 9, 0, tzinfo=KST)):
            tb.run(offsets=(14,), selftest=self.fake, send=sent.append)
            tb.run(offsets=(14,), selftest=self.fake, send=sent.append)     # 같은 날 두 번째 — 건너뜀
        self.assertEqual(len(sent), 1)
        with mock.patch.object(tb, "now_kst", return_value=datetime(2026, 11, 11, 9, 0, tzinfo=KST)):
            tb.run(offsets=(14,), selftest=self.fake, send=sent.append)     # 다음 날, 같은 실패 — 침묵(7일 재알림)
        self.assertEqual(len(sent), 1)
        with mock.patch.object(tb, "now_kst", return_value=datetime(2026, 11, 17, 9, 0, tzinfo=KST)):
            tb.run(offsets=(14,), selftest=self.fake, send=sent.append)     # 7일 뒤 — 재알림
        self.assertEqual(len(sent), 2)

    def test_recovery_once(self):
        sent = []
        with mock.patch.object(tb, "now_kst", return_value=datetime(2026, 11, 10, 9, 0, tzinfo=KST)):
            tb.run(offsets=(14,), selftest=self.fake, send=sent.append)
        ok = self.tmp / "ok.py"
        ok.write_text("import os,json;open(os.environ['SELFTEST_FAILS_OUT'],'w').write(json.dumps({'fails':[],'detail':[]}))\n",
                      encoding="utf-8")
        with mock.patch.object(tb, "now_kst", return_value=datetime(2026, 11, 12, 9, 0, tzinfo=KST)):
            tb.run(offsets=(14,), selftest=ok, send=sent.append)
            tb.run(offsets=(14,), selftest=ok, send=sent.append, force=True)
        self.assertEqual(len(sent), 2)
        self.assertIn("해소", sent[1])

    def test_wired_non_blocking(self):
        y = (Path(__file__).parent.parent / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        i = y.index("python scripts/timebomb_probe.py")
        block = y[y.rindex("- name:", 0, i):y.index("- name:", i)]
        self.assertIn("continue-on-error: true", block)                   # 오늘의 수집은 막지 않는다
        self.assertIn("TELEGRAM_ALERT_CHAT_ID", block)
        self.assertNotIn("TELEGRAM_CHAT_ID:", block)                       # 공개 채널 금지
        req = (Path(__file__).parent.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(req, r"(?m)^time-machine==")                      # 외부 라이브러리는 == 고정(⑰)


if __name__ == "__main__":
    unittest.main(verbosity=2)
