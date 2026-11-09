#!/usr/bin/env python3
"""
버크셔 탐사선 테스트 (2026-11-09) — 측정층·관문 대상 전환·관제판·배선.
네트워크 없이 돈다(원천은 전부 가짜). 시계는 now_kst 입구 하나만 고정한다(지뢰 ㉑).
변이 감지: 각 테스트는 해당 규칙을 지우면 실패하도록 만들었다(주석의 '변이').
"""
import json
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).parent.parent
KST = timezone(timedelta(hours=9))

import brk_watch as bw  # noqa: E402
import probe_gate as pg  # noqa: E402


def subs(rows):
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "items", "primaryDocument", "primaryDocDescription")
    rec = {k: [] for k in keys}
    for r in rows:
        rec["accessionNumber"].append(r[0]); rec["form"].append(r[1]); rec["filingDate"].append(r[2])
        rec["reportDate"].append(""); rec["items"].append(r[3] if len(r) > 3 else "")
        rec["primaryDocument"].append("doc.htm"); rec["primaryDocDescription"].append("")
    return {"filings": {"recent": rec}}


FACTS = {"facts": {"us-gaap": {
    "PaymentsForRepurchaseOfCommonStock": {"units": {"USD": [
        {"val": 9_600_000_000, "frame": "CY2026Q3"}, {"val": 100_000_000, "frame": "CY2026Q2"},
        {"val": 16_000_000_000, "frame": "CY2026"}]}},
    "StockholdersEquity": {"units": {"USD": [{"val": 700_000_000_000, "frame": "CY2026Q3I"}]}},
}}}


class ClassifyTest(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(bw.classify("8-K", "5.02,9.01"), (bw.URGENT, "officer"))
        self.assertEqual(bw.classify("8-K", "2.02"), (bw.MAJOR, "results"))
        # 변이: 성격 모르는 8-K 를 기록으로 낮추면 실패(⑳ — 8.01 금융리스 사고)
        self.assertEqual(bw.classify("8-K", "")[0], bw.MAJOR)
        self.assertEqual(bw.classify("10-Q"), (bw.MAJOR, "quarterly"))
        self.assertEqual(bw.classify("13F-HR"), (bw.MAJOR, "holdings"))
        self.assertEqual(bw.classify("13F-HR/A"), (bw.MAJOR, "holdings"))
        self.assertEqual(bw.classify("NT 10-Q"), (bw.URGENT, "late"))
        self.assertEqual(bw.classify("4"), (bw.RECORD, "insider"))
        # 표에 없는 양식도 버리지 않는다 — 최소 '기록'
        self.assertEqual(bw.classify("WEIRD-X"), (bw.RECORD, "other"))

    def test_every_kind_has_titles_in_all_catalogs(self):
        import i18n
        kinds = set(bw.KIND_KO) | {"trigger"}
        for L in i18n.LANGS:
            cat = i18n.catalog(L)
            self.assertEqual(sorted(k for k in kinds if "bk.ev." + k not in cat), [], L)


class WatchRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = (bw.WATCH_PATH, bw.STATE_PATH, bw.now_kst, bw.send_dm)
        bw.WATCH_PATH = self.tmp / "brk_watch.json"
        bw.STATE_PATH = self.tmp / "state.json"
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=KST)
        bw.now_kst = lambda: self.now
        self.dms = []
        bw.send_dm = lambda t: self.dms.append(t) or True
        bw.STATE_PATH.write_text(json.dumps({"run": {"run_date": "2026-11-09"}, "triggers_next_12m": []}), encoding="utf-8")
        import os
        os.environ["GITHUB_RUN_ID"] = "777"

    def tearDown(self):
        bw.WATCH_PATH, bw.STATE_PATH, bw.now_kst, bw.send_dm = self.old

    def go(self, rows, facts=FACTS, runs=None):
        return bw.run(fetch_subs=lambda: subs(rows), fetch_facts=lambda: facts, fetch_runs=lambda: runs)

    def w(self):
        return json.loads(bw.WATCH_PATH.read_text(encoding="utf-8"))

    def test_first_run_is_baseline_no_wake(self):
        self.go([("A1", "10-Q", "2026-11-07")])
        w = self.w()
        self.assertTrue(w["baselined"])
        self.assertEqual(w.get("dispatch_queue") or [], [])
        self.assertEqual(self.dms, [])

    def test_new_major_filing_wakes_engine_once(self):
        self.go([("A1", "10-Q", "2026-11-07")])
        self.go([("A2", "8-K", "2026-11-09", "5.02"), ("A1", "10-Q", "2026-11-07")])
        q = self.w()["dispatch_queue"]
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["mode"], "event")
        self.assertIn("임원", q[0]["reason"])
        self.assertTrue(any("긴급" in d for d in self.dms))
        # 같은 공시로 두 번 울지 않는다(변이: seen 삭제 → 실패)
        self.dms.clear()
        self.go([("A2", "8-K", "2026-11-09", "5.02"), ("A1", "10-Q", "2026-11-07")])
        self.assertEqual(len(self.w()["dispatch_queue"]), 1)
        self.assertEqual(self.dms, [])

    def test_record_grade_dm_only(self):
        self.go([("A1", "10-Q", "2026-11-07")])
        self.go([("A3", "4", "2026-11-09"), ("A1", "10-Q", "2026-11-07")])
        self.assertEqual(self.w().get("dispatch_queue") or [], [])
        self.assertTrue(any("기록" in d for d in self.dms))

    def test_zero_filings_counts_as_failure_and_alerts_at_3(self):
        self.go([("A1", "10-Q", "2026-11-07")])
        for i in range(3):
            rc = self.go([])
            self.assertEqual(rc, 1)          # 침묵은 통과가 아니다
        self.assertEqual(self.w()["sources"]["sec_submissions"]["streak"], 3)
        self.assertTrue(any("3회 연속 실패" in d for d in self.dms))
        self.dms.clear()
        self.go([])                          # 4회째 — 7일 안에는 재알림 없음
        self.assertFalse(any("연속 실패" in d for d in self.dms))
        self.now = self.now + timedelta(days=8)
        self.go([])
        self.assertTrue(any("연속 실패" in d for d in self.dms))   # 7일 재알림
        self.dms.clear()
        self.go([("A1", "10-Q", "2026-11-07")])
        self.assertTrue(any("회복" in d for d in self.dms))

    def test_facts_by_quarter_frames_only(self):
        self.go([("A1", "10-Q", "2026-11-07")])
        f = self.w()["facts"]
        self.assertEqual(f["quarters"]["3Q26"]["buyback_usd_m"], 9600.0)
        self.assertEqual(f["quarters"]["3Q26"]["equity_usd_m"], 700000.0)
        # 연간 프레임(CY2026)은 분기로 섞지 않는다
        self.assertNotIn("26", "".join(k for k in f["quarters"] if not re.fullmatch(r"[1-4]Q\d\d", k)))
        self.assertTrue(f["status"]["net_income_attrib_usd_m"].startswith("missing"))   # 못 잰 사유(㉒)

    def test_backstop_dispatches_monthly_when_stale(self):
        bw.STATE_PATH.write_text(json.dumps({"run": {"run_date": "2026-10-01"}}), encoding="utf-8")
        self.go([("A1", "10-Q", "2026-11-07")])
        q = self.w()["dispatch_queue"]
        self.assertEqual([x["mode"] for x in q], ["monthly"])
        self.assertTrue(any("관측 공백" in d for d in self.dms))
        # 쿨다운 3일 — 같은 날 다시 쏘지 않는다
        self.go([("A1", "10-Q", "2026-11-07")])
        self.assertEqual(len(self.w()["dispatch_queue"]), 1)

    def test_trigger_due_wakes_once(self):
        bw.STATE_PATH.write_text(json.dumps({"run": {"run_date": "2026-11-09"}, "triggers_next_12m": [
            {"id": "T1", "name": "13F", "expected_date": "2026-11-09(추정)", "status": "대기"},
            {"id": "T2", "name": "미래", "expected_date": "2027-02-28", "status": "대기"},
            {"id": "T3", "name": "끝남", "expected_date": "2026-11-01", "status": "확인(2026-11-02)"}]}), encoding="utf-8")
        self.go([("A1", "10-Q", "2026-11-07")])
        self.assertEqual([d["id"] for d in self.w()["due"]], ["T1"])
        self.assertEqual(len(self.w()["dispatch_queue"]), 1)
        self.go([("A1", "10-Q", "2026-11-07")])
        self.assertEqual(len(self.w()["dispatch_queue"]), 1)

    def test_engine_failure_alerts(self):
        self.go([("A1", "10-Q", "2026-11-07")], runs=[{"id": 5, "status": "completed", "conclusion": "failure",
                                                     "event": "schedule", "created_at": "2026-11-02T00:43:00Z"}])
        self.assertTrue(any("엔진 실행 failure" in d for d in self.dms))
        self.dms.clear()
        self.go([("A1", "10-Q", "2026-11-07")], runs=[{"id": 5, "status": "completed", "conclusion": "failure"}])
        self.assertFalse(any("엔진" in d for d in self.dms))

    def test_dispatch_only_this_run(self):
        import os
        bw.WATCH_PATH.write_text(json.dumps({"dispatch_queue": [
            {"state": "queued", "run_id": "1", "mode": "event", "reason": "옛 회차"},
            {"state": "queued", "run_id": "777", "mode": "monthly", "reason": "이번 회차"}]}), encoding="utf-8")
        os.environ["GITHUB_REPOSITORY"] = "x/y"
        os.environ["GITHUB_TOKEN"] = "t"
        sent = []

        class R:
            status_code = 204
        rc = bw.dispatch(post=lambda url, **kw: sent.append(kw["json"]) or R())
        self.assertEqual(rc, 0)
        self.assertEqual(sent[0]["event_type"], "brk-event")
        self.assertEqual(sent[0]["client_payload"]["mode"], "monthly")
        self.assertNotIn("옛 회차", sent[0]["client_payload"]["reason"])


class ClockTest(unittest.TestCase):
    def test_single_clock_entry(self):
        src = (Path(__file__).parent / "brk_watch.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("datetime.now("), 1)
        self.assertIn("def now_kst", src)
        csrc = (Path(__file__).parent / "brk_cockpit.py").read_text(encoding="utf-8")
        self.assertEqual(csrc.count("datetime.now("), 1)     # kst_today 하나 — render 는 today 인자


class GateSubjectTest(unittest.TestCase):
    def tearDown(self):
        pg.configure("spacex")

    def test_default_is_spacex_and_switch_is_reversible(self):
        self.assertEqual(pg.STATE.name, "spacex_state.json")
        self.assertEqual(pg.ar_rel(), "agent-research")
        pg.configure("berkshire")
        self.assertEqual(pg.STATE, ROOT / "agent-research" / "berkshire" / "state" / "berkshire_state.json")
        self.assertEqual(pg.WORK.name, "_probe_brk")
        self.assertEqual(pg.gate_cmd(), "python scripts/probe_gate.py --subject berkshire")
        self.assertEqual(pg.public_files()[0].parent.parent, ROOT / "agent-research" / "berkshire")
        pg.configure("spacex")
        self.assertEqual(pg.STATE.name, "spacex_state.json")
        self.assertEqual(pg.gate_cmd(), "python scripts/probe_gate.py")

    def test_spacex_public_files_do_not_include_berkshire(self):
        self.assertFalse(any("berkshire" in str(p) for p in pg.public_files()))

    def test_berkshire_published_files_pass_gate(self):
        pg.configure("berkshire")
        if not pg.STATE.exists():
            self.skipTest("버크셔 판단층 없음")
        g = pg.run_checks(stage="final", prev={})
        self.assertEqual(g["failures"], [])
        self.assertEqual(pg.check_compliance(pg.public_files()), [])

    def test_ci_allow_pattern_covers_author_gate_command(self):
        wf = (ROOT / ".github" / "workflows" / "brk-probe.yml").read_text(encoding="utf-8")
        pg.configure("berkshire")
        self.assertIn(f"Bash({pg.gate_cmd()} *)", wf)
        self.assertIn("PROBE_SUBJECT: berkshire", wf)
        self.assertIn("types: [brk-event]", wf)
        self.assertIn("client_payload.mode", wf)          # 월간 자가 발사가 monthly 로 돈다


class WiringTest(unittest.TestCase):
    def test_daily_runs_watch_then_dispatches_after_commit(self):
        d = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        i_watch, i_commit, i_disp = (d.index("scripts/brk_watch.py\n"), d.index("- name: Commit if changed"),
                                     d.index("brk_watch.py --dispatch"))
        self.assertLess(i_watch, i_commit)
        self.assertLess(i_commit, i_disp)
        blk = d[d.index("- name: Berkshire watch"):d.index("- name: Berkshire watch") + 900]
        self.assertNotIn("TELEGRAM_CHAT_ID:", blk.replace("TELEGRAM_ALERT_CHAT_ID", ""))   # 공개 채널 변수 금지

    def test_watch_never_writes_judgment_layer(self):
        src = (Path(__file__).parent / "brk_watch.py").read_text(encoding="utf-8")
        self.assertEqual([w for w in ("write_json(STATE_PATH", "STATE_PATH.write") if w in src], [])


class CockpitTest(unittest.TestCase):
    def test_render_all_languages(self):
        import brk_cockpit as bc
        import i18n
        if not (bc.AR / "state" / "berkshire_state.json").exists():
            self.skipTest("버크셔 판단층 없음")
        i18n.MISSING.clear()
        for L in i18n.LANGS:
            h = bc.render(lang=L, today=date(2026, 11, 9), watch={"events": [
                {"date": "2026-11-07", "form": "10-Q", "kind": "quarterly", "grade": "major", "url": "https://www.sec.gov/x"}]})
            self.assertTrue(h)
            self.assertEqual(re.findall(r"\b[A-E]_[a-z]+\b", h), [], L)       # 축 기계 키 0(규칙 8)
            for k in ("recommendation", "max_position", "entry_trigger", "op_earnings_usd_m", "http_error"):
                self.assertNotIn(k, h, L)
            self.assertNotIn("PHASE 4 —", h)
        self.assertEqual(i18n.MISSING, [])

    def test_tile_compares_with_immediately_previous_quarter(self):
        import brk_cockpit as bc
        s = [{"quarter": "1Q26", "x": 10}, {"quarter": "2Q26"}, {"quarter": "3Q26", "x": 5}]
        self.assertIsNone(bc.tile(s, "x")["arrow"])      # 2Q26 공란을 건너뛰어 1Q26 과 비교하지 않는다
        s[1]["x"] = 4
        self.assertEqual(bc.tile(s, "x")["arrow"], "up")

    def test_empty_state_renders_nothing(self):
        import brk_cockpit as bc
        self.assertEqual(bc.render(root=Path(tempfile.mkdtemp()), today=date(2026, 11, 9), watch={}), "")


class TranslationAndExportTest(unittest.TestCase):
    """버크셔 서술은 번역 엔진과 화면·허브가 같은 행·같은 칸을 본다 — 캐시가 있으면 그 언어, 없으면 원문."""

    def test_nodes_cover_every_kind_in_spec(self):
        import brk_cockpit as bc
        import i18n_content as IC
        if not (bc.AR / "state" / "berkshire_state.json").exists():
            self.skipTest("버크셔 판단층 없음")
        nodes = bc.translate_nodes()
        self.assertEqual(sorted(nodes), sorted(IC.BERKSHIRE))
        self.assertTrue(all(nodes[k] for k in nodes), {k: len(v) for k, v in nodes.items()})
        ids = [i for v in nodes.values() for i, _, _ in v]
        rep = [i for i, _, _ in nodes["brk_report"]]
        self.assertTrue(all(i.startswith("brk-") for i in rep))       # SpaceX 보고서 캐시(2026-11-s0)와 겹치지 않는다
        import translate_content
        self.assertIn("berkshire_nodes", translate_content.collect.__code__.co_names)   # 변이: 배선 삭제 → 실패

    def test_cached_translation_flows_to_screen_and_hub(self):
        import brk_cockpit as bc
        import i18n_content as IC
        st = {"run": {"run_id": "R", "summary": ["한국어 요약"]},
              "milestones": [{"id": "A-2027-1", "axis": "A_insurance", "year": 2027, "statement": "보험 인수이익 흑자",
                              "status": "진행", "progress": None, "progress_reason": "사유"}],
              "scenarios": {"bull": {"probability": 1.0, "description": "강세 그림"}}}
        tmp = Path(tempfile.mkdtemp())
        row = bc.rows(st)["brk_ms"][0]
        f = IC.fields_for(IC.BERKSHIRE["brk_ms"], row, "en")
        IC.write_cache("en", "brk_ms", "A-2027-1", {"status": "ok", "src_hash": IC.src_hash(f),
                                                     "fields": {"text": "Insurance underwriting profit", "reason": "why"}}, tmp)
        en = bc.localized_state(st, "en", cache_dir=tmp)
        self.assertEqual(en["milestones"][0]["statement"], "Insurance underwriting profit")
        self.assertEqual(en["milestones"][0]["progress_reason"], "why")
        self.assertEqual(en["run"]["summary"], ["한국어 요약"])          # 캐시 없는 칸은 원문(표식은 화면이)
        self.assertEqual(st["milestones"][0]["statement"], "보험 인수이익 흑자")   # 원본 불변
        row["text"] = "바뀐 문장"
        st["milestones"][0]["statement"] = "바뀐 문장"
        self.assertEqual(bc.localized_state(st, "en", cache_dir=tmp)["milestones"][0]["statement"], "바뀐 문장")  # 원문이 바뀌면 캐시 무효

    def test_export_shape_for_hub(self):
        import brk_cockpit as bc
        if not (bc.AR / "state" / "berkshire_state.json").exists():
            self.skipTest("버크셔 판단층 없음")
        for L in ("ko", "en", "es", "ja"):
            d = bc.export(L, today=date(2026, 11, 9), watch={"events": []})
            for k in ("run", "delta", "price", "band", "scen", "rules", "axes", "milestones", "triggers", "tiles",
                      "disruption", "report", "labels", "links"):
                self.assertIn(k, d, (L, k))
            self.assertTrue(d["run"]["id"] and d["milestones"])
            self.assertTrue(all(a["key"] in "ABCDE" for a in d["axes"]))
            blob = json.dumps(d, ensure_ascii=False)
            self.assertEqual(re.findall(r"\b[A-E]_[a-z]+\b", blob), [], L)      # 기계 키 0
            if L != "ko":
                import i18n
                self.assertFalse(i18n.has_hangul(json.dumps(d["labels"], ensure_ascii=False)), L)
                self.assertFalse(any(i18n.has_hangul(t["label"]) for t in d["tiles"]), L)


if __name__ == "__main__":
    unittest.main(verbosity=1)
