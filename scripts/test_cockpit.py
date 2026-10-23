#!/usr/bin/env python3
"""
관제실 v2 테스트 (2026-10-23) — probe_cockpit(데이터·관제판) + 진척 계산
화면 상호작용(패널·서랍·검색·딥링크·한 화면 단언)은 Playwright 스크립트가 맡는다.
실행: python scripts/test_cockpit.py
"""
import json
import re
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent_report as ar      # noqa: E402
import probe_cockpit as pc     # noqa: E402
import probe_gate as g         # noqa: E402

AXIS_KEY = re.compile(r"[A-E]_[a-z]+")


class ProgressCalcTest(unittest.TestCase):
    """진척 숫자화 — op 별 계산, 복합(AND) = 최솟값, 결측은 None(0 으로 채우지 않는다)."""

    def p(self, cur, tgt, op=">="):
        return ar.part_pct({"current_num": cur, "target_num": tgt, "op": op})

    def test_ge(self):
        self.assertAlmostEqual(self.p(1360, 2000), 0.68)
        self.assertEqual(self.p(69, 60), 1.0)                 # 넘으면 1 로 자른다

    def test_le_is_reverse(self):
        self.assertEqual(self.p(51.1, 50, "<="), 50 / 51.1)    # 목표 이하가 좋은 방향
        self.assertEqual(self.p(40, 50, "<="), 1.0)
        self.assertAlmostEqual(self.p(10.8, 1, "<="), 1 / 10.8)

    def test_threshold_ops(self):
        self.assertEqual(self.p(-7, 0, ">"), 0.0)
        self.assertEqual(self.p(431, 0, ">"), 1.0)
        self.assertEqual(self.p(0, 0, "=="), 1.0)

    def test_missing_is_none(self):
        self.assertIsNone(self.p(None, 30))
        self.assertIsNone(ar.part_pct({"current_num": True, "target_num": 1}))

    def test_and_is_min_and_none_propagates(self):
        m = {"progress": {"parts": [{"current_num": 1360, "target_num": 2000, "op": ">="},
                                    {"current_num": 69, "target_num": 60, "op": ">="}]}}
        self.assertAlmostEqual(ar.milestone_pct(m), 0.68)
        m["progress"]["parts"].append({"current_num": None, "target_num": 1, "op": ">="})
        self.assertIsNone(ar.milestone_pct(m))
        self.assertIsNone(ar.milestone_pct({"progress": None, "progress_parts": []}))


class FormatTest(unittest.TestCase):
    def test_korean_units(self):
        self.assertEqual(pc.usd_m_ko(14575), "$145.8억")
        self.assertEqual(pc.usd_m_ko(-9962), "−$99.6억")
        self.assertEqual(pc.usd_m_ko(None), "—")
        self.assertEqual(pc.man_ko(13.6), "1,360만")

    def test_short_axis(self):
        axes = {"A_starlink": "축 A — Starlink(Connectivity)", "C_ai": "축 C — AI / 컴퓨트(SI)",
                "E_external": "축 E — 외부(규제·궤도환경·지정학·머스크)"}
        self.assertEqual([pc.short_axis(axes, k) for k in axes], ["Starlink", "AI 컴퓨트", "외부"])
        self.assertEqual(pc.short_axis({}, "D_finance"), "재무")


class CockpitRealTest(unittest.TestCase):
    """실제 판단층으로 — 매월 바뀌므로 '항상 참'인 것만 본다."""

    @classmethod
    def setUpClass(cls):
        cls.html, cls.data, cls.reps = pc.render(ar, today=date(2026, 10, 23))
        cls.blob = json.dumps(cls.data, ensure_ascii=False)

    def test_size_budget(self):
        self.assertLessEqual(len(self.blob.encode("utf-8")), 400 * 1024)

    def test_no_machine_keys_anywhere(self):
        everything = self.html + self.blob + json.dumps(self.reps, ensure_ascii=False)
        self.assertEqual(AXIS_KEY.findall(everything), [])

    def test_first_screen_answers_five_questions(self):
        for h in ("① 가격 vs 가정 가치", "② 이번 회차에 바뀐 것", "③ 5축 진척", "④ KPI", "⑤ 다음에 볼 것"):
            self.assertIn(h, self.html)
        self.assertIn(pc.DISCLAIMER, self.html)
        self.assertEqual(self.html.count('class="pc-seg"'), 7)

    def test_no_target_price_language(self):
        plain = re.sub(r"<[^>]+>", "\n", self.html)
        self.assertEqual(g.reco_text_hits(plain), [])
        for w in ("상승여력", "상승 여력", "목표주가"):
            self.assertNotIn(w, plain)
        for r in self.reps:
            for s in r["sections"]:
                self.assertEqual(g.reco_text_hits(re.sub(r"<[^>]+>", "\n", s["html"])), [], s["title"])

    def test_withdrawn_titles_never_rendered(self):
        import companion_essays as ce
        pages = [("x", self.html + self.blob)]
        self.assertEqual(ce.html_leaks(ce.load_essays(), pages), [])

    def test_search_index_covers_all_kinds(self):
        kinds = {s["kind"] for s in self.data["search"]}
        for k in ("이정표", "사건", "보고서", "변화 이력", "출처"):
            self.assertIn(k, kinds)
        self.assertTrue(all(re.match(r"^(milestones|events|report|changelog|sources|triggers)/", s["anchor"])
                            for s in self.data["search"]))

    def test_every_milestone_has_parts_and_history(self):
        for m in self.data["milestones"]:
            self.assertTrue(m["parts"], m["id"])
            self.assertTrue(m["history"], m["id"])
            if m["pct"] is None:      # 측정 불가면 조건별 사유가 있어야 한다
                self.assertTrue(any(p["reason"] for p in m["parts"] if p["cur"] is None), m["id"])

    def test_missing_values_are_dashes(self):
        for r in self.data["fin"]["rows"]:
            for c in r["cells"]:
                if c["v"] is None:
                    self.assertEqual(c["t"], "—")


class SectionSplitTest(unittest.TestCase):
    def test_split_and_toc(self):
        md = chr(10).join(["# 제목", "서문", "## 0. 지난 회차 대비 무엇이 바뀌었는가 (델타)", "본문",
                           "## PHASE 1 — 현재 해부 (3Q26)", "해부"])
        secs = pc.split_sections(md)
        self.assertEqual([t for t, _ in secs], ["개요", "0. 지난 회차 대비 무엇이 바뀌었는가 (델타)",
                                                 "PHASE 1 — 현재 해부 (3Q26)"])
        self.assertEqual(pc.toc_label(secs[2][0]), "PHASE 1")
        self.assertEqual(pc.toc_label(secs[1][0]), "0 지난 회차 대비 무엇이 바뀌었는가")


if __name__ == "__main__":
    unittest.main(verbosity=2)
