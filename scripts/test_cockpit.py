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


class DecisionsTest(unittest.TestCase):
    """대표님 결정(2026-10-23 12:30) — F1 화면 상태 생성 · ② 두 블록 · KPI 계산/S-1 표식."""

    T = date(2026, 10, 23)

    def test_met_before_judgment_year_is_generated_from_state(self):
        self.assertEqual(pc.shown_status("진행", 1.0, 2027, self.T), pc.MET)
        self.assertEqual(pc.shown_status("진행", 0.99, 2027, self.T), "진행")     # 100% 미만은 진행
        self.assertEqual(pc.shown_status("진행", 1.0, 2026, self.T), "진행")      # 판정 연도면 진행
        self.assertEqual(pc.shown_status("달성", 1.0, 2027, self.T), "달성")      # 판정이 난 것은 그대로
        self.assertEqual(pc.shown_status("진행", None, 2027, self.T), "진행")     # 측정 불가는 충족 아님

    def test_axis_met_only_when_every_measured_milestone_met(self):
        ms = [{"axis": "E", "pct": 1.0, "status": "진행", "shown": pc.MET},
              {"axis": "E", "pct": None, "status": "미판정", "shown": "미판정"}]
        a = pc.axis_rows({"axes": {"E_external": "축 E — 외부"}}, ms)[0]
        self.assertTrue(a["met"])
        ms.append({"axis": "E", "pct": 0.5, "status": "진행", "shown": "진행"})
        self.assertFalse(pc.axis_rows({"axes": {"E_external": "축 E — 외부"}}, ms)[0]["met"])

    def test_real_state_marks_met_milestones(self):
        _, data, _ = pc.render(ar, today=self.T)
        for m in data["milestones"]:
            exp = m["pct"] is not None and m["pct"] >= 1 and m["status"] == "진행" and int(m["year"]) > 2026
            self.assertEqual(m["shown"] == pc.MET, exp, m["id"])
            self.assertEqual(m["cls"] == "met", exp, m["id"])

    def test_delta_two_blocks_for_event_run(self):
        st = {"run": {"run_id": "2026-10-23-event", "mode": "event", "summary": ["a", "b", "c", "d"],
                      "previous_monthly_run_id": "2026-10"},
              "run_history": [{"run_id": "2026-10", "summary": ["m1", "m2"]}]}
        bl = pc.delta_blocks(st, ar)
        self.assertEqual([(b["id"], b["kind"]) for b in bl], [("2026-10-23-event", "사건 회차"), ("2026-10", "직전 월간 회차")])
        self.assertEqual(bl[0]["lines"], ["a", "b", "c"])            # 최대 3줄
        self.assertEqual(bl[1]["lines"], ["m1", "m2"])
        st["run"]["mode"] = "monthly"
        self.assertEqual(len(pc.delta_blocks(st, ar)), 1)            # 월간 회차면 하나

    def test_kpi_marks_derived_and_s1(self):
        kpi = {"auto": {"quarters": [
            {"quarter": "1Q26", "end": "2026-03-31", "revenue_usd_m": 4694,
             "src": {"revenue_usd_m": {"how": "누적 2026-01-01~2026-06-30 − 기간 2026-04-01~2026-06-30", "derived": True}}},
            {"quarter": "4Q25", "end": "2025-12-31", "revenue_usd_m": None, "src": {"revenue_usd_m": {"note": "없음"}}},
            {"quarter": "2Q26", "end": "2026-06-30", "revenue_usd_m": 7814, "src": {"revenue_usd_m": {"how": "분기 보고값"}}}]}}
        st = {"kpi_history": [{"quarter": "4Q25", "period_end": "2025-12-31", "revenue_usd_m": 4100,
                               "fin_source": "https://www.sec.gov/s1", "fin_note": "FY2025 − 9M2025"}]}
        rev = {r["id"]: r for r in pc.kpi_series(kpi, st)["rows"]}["revenue"]
        cells = dict(zip(pc.kpi_series(kpi, st)["cols"], rev["cells"]))
        self.assertEqual((cells["1Q26"]["mk"], cells["2Q26"]["mk"]), ("계산", ""))
        self.assertEqual((cells["4Q25"]["v"], cells["4Q25"]["mk"]), (4100, "S-1"))
        self.assertEqual(cells["4Q25"]["src"]["url"], "https://www.sec.gov/s1")
        st["kpi_history"][0].pop("fin_source")                     # 출처 없는 기입값은 쓰지 않는다
        rev = {r["id"]: r for r in pc.kpi_series(kpi, st)["rows"]}["revenue"]
        self.assertIsNone(dict(zip(pc.kpi_series(kpi, st)["cols"], rev["cells"]))["4Q25"]["v"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
