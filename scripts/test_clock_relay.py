#!/usr/bin/env python3
"""
시계 중계 · 아침 브리핑 창 테스트 (2026-11-04)
막으려는 것: 데일리 수집과 아침 브리핑이 GitHub 예약의 지연(실측 3~7시간)에 끌려다니는 것 —
새벽 2:26 에 미국 장중 가격으로 '모닝브리핑'이 나갔다.
실행: python scripts/test_clock_relay.py
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import clock_relay as cr               # noqa: E402
import pipeline_sentinel as ps          # noqa: E402
import send_telegram_briefing as tb     # noqa: E402

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent


def at(hm, day=4):
    h, m = (int(x) for x in hm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=KST)


def run(hm, status="completed", conclusion="success", day=4):
    return {"created_at": at(hm, day).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status, "conclusion": conclusion if status == "completed" else None}


class RelayTest(unittest.TestCase):
    def test_morning_slot_fires_once(self):
        prev = [run("18:40", day=3), run("01:59")]                     # 어제 저녁·새벽(늦게 울린 예약) 회차
        self.assertEqual(cr.decide(at("08:15"), prev)[0], "08:10")
        self.assertIsNone(cr.decide(at("08:30"), prev + [run("08:16", "in_progress")])[0])   # 진행 중 — 기다린다
        self.assertIsNone(cr.decide(at("08:45"), prev + [run("08:16")])[0])                  # 이미 쐈다

    def test_never_steals_a_running_daily(self):
        """cancel-in-progress 로 진행 중 회차를 끊지 않는다 — 수동 실행이 돌고 있으면 기다린다."""
        label, why = cr.decide(at("12:15"), [run("12:05", "in_progress")])
        self.assertIsNone(label)
        self.assertIn("기다린다", why)

    def test_retry_only_without_success(self):
        failed = [run("08:16", conclusion="failure")]
        self.assertEqual(cr.decide(at("09:15"), failed)[0], "09:10")
        self.assertIsNone(cr.decide(at("09:15"), [run("08:16")])[0])

    def test_outside_slots_and_grace(self):
        self.assertIsNone(cr.decide(at("02:00"), [])[0])                 # 미국 장중 — 절대 안 쏜다
        self.assertIsNone(cr.decide(at("07:59"), [])[0])
        self.assertIsNone(cr.decide(at("11:05"), [])[0])                 # 아침 유예(170분) 지남
        self.assertEqual(cr.decide(at("12:20"), [run("08:16")])[0], "12:10")
        self.assertEqual(cr.decide(at("18:15"), [run("12:16")])[0], "18:10")
        self.assertIsNone(cr.decide(at("23:40"), [])[0])                 # 미국 개장 뒤엔 안 쏜다

    def test_slots_never_in_us_session(self):
        """모든 슬롯 + 유예가 미국 정규장(23:30~06:00 KST) 밖이다 — 증감이 늘 종가 대 종가가 되게."""
        for _l, _k, t, end in cr.slot_times(at("00:00")):
            self.assertGreaterEqual(t.hour, 6)
            self.assertLessEqual(end, at("23:30"))


class BriefingWindowTest(unittest.TestCase):
    def test_window(self):
        today = "2026-11-04"
        self.assertFalse(tb.in_window(at("02:26"), "", today)[0])        # 실제 사고 시각
        self.assertTrue(tb.in_window(at("08:35"), "", today)[0])
        self.assertFalse(tb.in_window(at("08:35"), "", "2026-11-03")[0])  # 어제 수집한 숫자로는 안 보낸다
        self.assertFalse(tb.in_window(at("11:00"), "", today)[0])
        self.assertTrue(tb.in_window(at("07:40"), "07:30-10:00", today)[0])
        self.assertTrue(tb.in_window(at("09:00"), "엉터리", today)[0])     # 잘못된 설정은 기본 창으로

    def test_missed_briefing_alerts_after_window(self):
        self.assertEqual(ps.judge_briefing({}, "2026-11-04", at("10:30")), [])
        a = ps.judge_briefing({"last_sent": "2026-11-03"}, "2026-11-04", at("12:40"))
        self.assertEqual(len(a), 1)
        self.assertIn("미발송", a[0]["text"])
        self.assertEqual(ps.judge_briefing({"last_sent": "2026-11-04"}, "2026-11-04", at("12:40")), [])


class WiringTest(unittest.TestCase):
    def test_workflows(self):
        d = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        self.assertNotIn("- cron:", d)                                     # 늦게 우는 GitHub 예약은 없다
        self.assertIn("BRIEFING_WINDOW:", d)
        w = (ROOT / ".github" / "workflows" / "spacex-watch.yml").read_text(encoding="utf-8")
        i = w.index("python scripts/clock_relay.py")
        block = w[w.rindex("- name:", 0, i):w.index("- name:", i)]
        self.assertIn("continue-on-error: true", block)                   # 중계가 실패해도 감시는 돈다
        self.assertNotIn("TELEGRAM_CHAT_ID:", block)                        # 공개 채널 금지
        self.assertIn("actions: write", w)
        self.assertLess(i, w.index("python scripts/spacex_watch.py --hourly"))
        src = (ROOT / "scripts" / "pipeline_sentinel.py").read_text(encoding="utf-8")
        self.assertIn("+ brief_alerts)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
