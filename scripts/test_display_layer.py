#!/usr/bin/env python3
"""
표시층 테스트 (2026-10-23, D1~D5) — 정체가 보이지 않던 탐사선 탭
실행: python scripts/test_display_layer.py
"""
import re
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent_report as ar   # noqa: E402
import build_site as bs     # noqa: E402

AXIS_KEY = re.compile(r"[A-E]_[a-z]+")
TRIG = [{"id": "T1", "name": "3Q26 실적의 AI 단위경제", "expected_date": "2026-11-초(추정)",
         "status": "대기"},
        {"id": "T2", "name": "락업 해제", "expected_date": "2026-12-08 ~ 2027-01-07", "status": "대기"}]


class AxisTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(bs.axis_name("A_starlink", {"A_starlink": "스타링크"}), "스타링크")
        self.assertEqual(bs.axis_name("C_ai", [{"id": "C_ai", "ko": "AI 컴퓨트(xAI)"}]), "AI 컴퓨트(xAI)")
        self.assertEqual(bs.axis_name("E_external", None), "외부 변수")      # 고정 사전
        self.assertEqual(bs.axis_name("D_finance", {"D_finance": {"ko": "재무"}}), "재무")

    def test_body_keys_replaced(self):
        self.assertEqual(bs.ko_axes("A_starlink 축과 B_starship 축", None), "Starlink 축과 Starship 축")
        self.assertEqual(bs.ko_axes("A-2027-1 이정표", None), "A-2027-1 이정표")   # 이정표 id 는 그대로


class ScheduleTest(unittest.TestCase):
    def test_overdue(self):
        s = bs.probe_schedule("2026-09-02", TRIG, date(2026, 10, 23))
        self.assertEqual((s["next"], s["overdue_days"], s["age_days"], s["stale"]),
                         ("2026-10-01", 22, 51, True))
        html = bs.schedule_html(s, {"run_id": "2026-09", "run_date": "2026-09-02"}, ar.esc)
        self.assertIn("예정일 경과 22일 — 엔진 점검 중", html)
        self.assertIn("ag-delay", html)

    def test_normal_with_trigger_dday(self):
        s = bs.probe_schedule("2026-10-24", TRIG, date(2026, 10, 25))
        html = bs.schedule_html(s, {"run_id": "2026-10", "run_date": "2026-10-24"}, ar.esc)
        self.assertEqual(s["overdue_days"], 0)
        self.assertIn("다음 관측 예정 2026-11-01", html)
        self.assertIn("다음 트리거 T1 3Q26 실적의 AI 단위경제 D-7", html)
        self.assertNotIn("ag-delay", html)

    def test_trigger_past(self):
        s = bs.probe_schedule("2026-11-01", TRIG[:1], date(2026, 11, 3))
        html = bs.schedule_html(s, {"run_id": "2026-11", "run_date": "2026-11-01"}, ar.esc)
        self.assertIn("예상일 경과 2일", html)


class MilestoneTest(unittest.TestCase):
    def test_progress_states(self):
        self.assertIn("진척 미기록", bs.progress_cell({}, ar.esc)[0])
        self.assertIn("회수 시도 전", bs.progress_cell({"progress": None,
                                                    "progress_reason": "회수 시도 전"}, ar.esc)[0])
        txt, bar = bs.progress_cell({"progress": {"current": 13.6, "target": 20, "unit": "백만 명",
                                                  "asof": "2026-09-30", "source": "x"}}, ar.esc)
        self.assertIn("13.6 / 20 백만 명", txt)
        self.assertIn("width:68%", bar)
        _, bar2 = bs.progress_cell({"progress": {"current": 4.0, "target": 3.0, "unit": "배",
                                                 "direction": "down", "asof": "", "source": ""}}, ar.esc)
        self.assertIn("width:75%", bar2)

    def test_chips(self):
        for st, cls in (("달성", "done"), ("달성(조기)", "done"), ("진행", "prog"),
                        ("미달", "miss"), ("미판정", "wait")):
            self.assertIn("ag-chip-" + cls, bs.status_chip(st, ar.esc))


class TimelineEngineTest(unittest.TestCase):
    WATCH = {"events": [{"date": f"2026-10-{i:02d}", "source": "SEC EDGAR", "form": "8-K",
                         "title": "수시공시 — 실적 발표", "url": "https://www.sec.gov/x"}
                        for i in range(1, 15)],
             "engine": {"last_run": {"conclusion": "failure", "created_at": "2026-11-01T00:13:00Z"}}}

    def test_timeline_ten(self):
        h = bs.timeline_html(self.WATCH, ar.esc)
        self.assertEqual(h.count("<li>"), 10)

    def test_engine_line(self):
        st = {"sources": {"companion:spacex:SpaceX 발사·로드맵": {
            "kind": "companion", "source": "spacex:SpaceX 발사·로드맵", "outcome": "decode_fail"}}}
        h = bs.engine_html(self.WATCH, {"checked": 20, "match": 17, "rounding": 2}, st, ar.esc)
        self.assertIn("2026-11-01 · 실패", h)
        self.assertIn("검증 일치 19/20", h)
        self.assertIn("SpaceX 발사·로드맵 디코딩 실패", h)
        self.assertNotIn("decode_fail", h)
        self.assertNotIn("spacex:", h)

    def test_engine_unknown(self):
        self.assertIn("상태 미확인", bs.engine_html({}, {}, {}, ar.esc))


class RenderRealTest(unittest.TestCase):
    """실제 판단층 파일로 렌더 — 기계 키 0 · 금지 키 0 · 지연 배너(10월 회차 전)."""

    def test_render(self):
        h = bs.render_agent_probe(ar, today=date(2026, 10, 23), watch=TimelineEngineTest.WATCH,
                                  status={}, vres={})
        self.assertEqual(AXIS_KEY.findall(h), [])
        for k in ("recommendation", "max_position", "entry_trigger", "decode_fail"):
            self.assertNotIn(k, h)
        self.assertIn("엔진 점검 중", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
