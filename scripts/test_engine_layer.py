#!/usr/bin/env python3
"""
판단층 무정지 엔진 테스트 (2026-10-23, E3·E6) — probe_gate 관문 + 관제탑 감시견
spacex-probe.yml 이 Claude 를 부르기 전에 이 파일을 먼저 돌린다(관문이 망가진 채 게시하지 않게).
실행: python scripts/test_engine_layer.py
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import probe_gate as g            # noqa: E402
import pipeline_sentinel as ps    # noqa: E402

NL = chr(10)


def good_state():
    ms = {"id": "D-2029-1", "axis": "D_finance", "year": 2029, "statement": "GAAP 순이익 흑자",
          "status": "진행", "progress": {"current": 4404, "target": 0, "unit": "$M(분기)",
                                        "asof": "2026-09-30", "source": "https://www.sec.gov/x"}}
    return {
        "schema_version": 2, "ticker": "SPCX",
        "run": {"run_id": "2026-10", "run_date": "2026-10-24", "run_number": 2,
                "previous_run_id": "2026-09", "report_path": "reports/2026-10.md"},
        "axes": {"D_finance": "재무"},
        "scenarios": {"bull": {"probability": 0.25}, "base": {"probability": 0.5},
                      "bear": {"probability": 0.25}},
        "valuation": {"fair_value_band_usd": [30, 80]},
        "milestones": [ms, dict(ms, id="B-2027-1", status="미판정", progress=None,
                                progress_reason="회수 시도 전 — 측정 대상 없음")],
        "triggers_next_12m": [], "probability_adjustment_rules": [],
        "open_questions_for_next_run": [],
    }


PREV = {"run": {"run_id": "2026-09", "run_number": 1}}


class RunIdTest(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(g.run_id_for("monthly", "2026-10-24", "2026-09", ["2026-09"]), "2026-10")
        self.assertEqual(g.run_id_for("monthly", "2026-10-30", "2026-10", ["2026-10"]), "2026-10-r2")
        self.assertEqual(g.run_id_for("event", "2026-10-24", "2026-10", []), "2026-10-24-event")
        self.assertEqual(g.run_id_for("event", "2026-10-24", "x", ["2026-10-24-event"]),
                         "2026-10-24-event-r2")


class SchemaTest(unittest.TestCase):
    def test_good(self):
        st = good_state()
        self.assertEqual(g.check_schema(st) + g.check_probabilities(st) + g.check_statuses(st)
                         + g.check_chain(st, PREV), [])

    def test_missing_progress_and_reason(self):
        st = good_state()
        st["milestones"][0].pop("progress")
        st["milestones"][1]["progress_reason"] = ""
        bad = g.check_schema(st)
        self.assertEqual(len(bad), 2, bad)

    def test_probability_sum(self):
        st = good_state()
        st["scenarios"]["bear"]["probability"] = 0.26
        self.assertEqual(len(g.check_probabilities(st)), 1)
        st["scenarios"]["bear"]["probability"] = 0.2505        # ±0.001 안
        self.assertEqual(g.check_probabilities(st), [])

    def test_status_set(self):
        st = good_state()
        st["milestones"][0]["status"] = "보류"
        self.assertEqual(len(g.check_statuses(st)), 1)
        for ok in g.STATUSES:
            st["milestones"][0]["status"] = ok
            self.assertEqual(g.check_statuses(st), [])

    def test_chain(self):
        st = good_state()
        st["run"]["run_number"] = 1
        st["run"]["previous_run_id"] = None
        self.assertEqual(len(g.check_chain(st, PREV)), 2)


class SourceTest(unittest.TestCase):
    def test_table_inherits_caption_source(self):
        md = NL.join(["단위 $M. (출처: [10-Q](https://www.sec.gov/x), 2026-10-22)", "",
                      "| 항목 | 3Q26 |", "|---|---|", "| 매출 | 14,575 |", "| 영업이익 | 2,791 |"])
        n, bad = g.unsourced_lines(md)
        self.assertEqual((n, bad), (3, []))       # 캡션 줄도 숫자 줄(출처 보유)

    def test_table_without_source_counts(self):
        md = NL.join(["설명 문단", "", "| 항목 | 값 |", "|---|---|", "| 매출 | 14,575 |"])
        self.assertEqual(len(g.unsourced_lines(md)[1]), 1)

    def test_calc_line_is_derived(self):
        self.assertEqual(g.unsourced_lines("회수기간 = $396억 ÷ $63억 = 6.3년")[1], [])

    def test_definition_table_exempt(self):
        md = NL.join(["| 연도 | 이정표 | 달성 시 의미 |", "|---|---|---|",
                      "| 2027 | 가입자 ≥ 2,000만 | 물량 방어 |"])
        self.assertEqual(g.unsourced_lines(md)[1], [])

    def test_korean_dates_and_counters_not_numbers(self):
        self.assertFalse(g._has_number("2027년 이정표 3개를 다시 본다"))
        self.assertFalse(g._has_number("9/28 Flight 14 결과"))
        self.assertTrue(g._has_number("가입자 1,360만"))

    def test_threshold(self):
        lines = [f"- 값 {i}억 ([원천](https://a.com/{i}))" for i in range(1, 40)] + ["- 값 99억"]
        self.assertEqual(g.check_sources(NL.join(lines))[0], [])         # 1/40 = 2.5%
        lines += ["- 값 98억", "- 값 97억"]
        self.assertEqual(len(g.check_sources(NL.join(lines))[0]), 1)     # 3/42 = 7.1%


class VerifyTest(unittest.TestCase):
    ITEMS = [{"claim": f"c{i}", "value": i, "source_url": "https://x", "source_date": "2026-10-20",
              "where_in_report": "1-1"} for i in range(20)]

    def test_ok(self):
        self.assertEqual(g.check_verify(self.ITEMS, {"checked": 20, "match": 20, "mismatch": [],
                                                     "verified_at": "2026-10-24T10:00+09:00"}), [])

    def test_mismatch_and_missing(self):
        self.assertEqual(len(g.check_verify(self.ITEMS, {"mismatch": [{"claim": "c1"}],
                                                         "verified_at": "x"})), 1)
        self.assertIn("verify_result 없음", g.check_verify(self.ITEMS, None)[0])
        self.assertEqual(len(g.check_verify(self.ITEMS[:5], {"mismatch": [], "verified_at": "x"})), 1)

    def test_only_verify_failures(self):
        self.assertTrue(g.only_verify_failures({"failures": ["검증관 불일치 2건"]}))
        self.assertFalse(g.only_verify_failures({"failures": ["검증관 불일치 2건", "시나리오 확률 합"]}))
        self.assertFalse(g.only_verify_failures({"failures": []}))


class ComplianceTest(unittest.TestCase):
    def _files(self, state, md):
        d = Path(tempfile.mkdtemp())
        (d / "reports").mkdir()
        (d / "state").mkdir()
        (d / "state" / "spacex_state.json").write_text(json.dumps(state, ensure_ascii=False),
                                                       encoding="utf-8")
        (d / "reports" / "2026-10.md").write_text(md, encoding="utf-8")
        (d / "reports" / "2026-10_private.md").write_text("권고: 관망(비중 0%)", encoding="utf-8")
        return d

    def test_clean_passes_and_private_ignored(self):
        d = self._files(good_state(), "ARPU $60 미만이면 저가 경쟁 진입 확정. 매출 내 비중 42%.")
        self.assertEqual(g.check_compliance(g.public_files(d)), [])

    def test_forbidden_keys(self):
        for k in ("recommendation", "max_position_weight", "entry_trigger_price_usd",
                  "consensus_price_target_usd", "portfolio_weight"):
            st = good_state()
            st["valuation"][k] = 1
            d = self._files(st, "분석")
            self.assertEqual(len(g.check_compliance(g.public_files(d))), 1, k)

    def test_forbidden_sentences(self):
        for t in ("투자 판단: 관망(비중 0%)", "$80 이하 분할 진입", "월가 목표주가 $219",
                  "지금은 매수 의견을 유지한다", "포트폴리오 비중은 2% 이내", "비중 확대 구간"):
            d = self._files(good_state(), t)
            self.assertEqual(len(g.check_compliance(g.public_files(d))), 1, t)

    def test_negated_disclaimer_is_not_reco(self):
        self.assertEqual(g.reco_text_hits("기계 생성 가정 · 투자 권유 아님 · 목표가 아님"), [])
        self.assertEqual(len(g.reco_text_hits("목표가 아님 — 그래도 $80 이하 분할 매수")), 1)
        self.assertEqual(len(g.reco_text_hits("월가 목표주가 $219")), 1)

    def test_private_in_ci(self):
        d = self._files(good_state(), "분석")
        self.assertEqual(len(g.check_private(d, ci=True)), 1)
        self.assertEqual(g.check_private(d, ci=False), [])


class RunChecksTest(unittest.TestCase):
    def test_full_pass_then_each_failure(self):
        st, md = good_state(), "- 매출 $14,575M ([10-Q](https://www.sec.gov/x), 2026-10-22)"
        items = VerifyTest.ITEMS
        vres = {"mismatch": [], "verified_at": "2026-10-24T10:00+09:00", "checked": 20}
        args = dict(prev=PREV, md=md, vlist=items, vres=vres, files=[], ci=False)
        self.assertTrue(g.run_checks(st=st, **args)["ok"])
        bad = copy.deepcopy(st)
        bad["scenarios"]["bull"]["probability"] = 0.9
        self.assertFalse(g.run_checks(st=bad, **args)["ok"])
        self.assertFalse(g.run_checks(st=st, **dict(args, vres={"mismatch": [1], "verified_at": "x"}))["ok"])
        # 작성자 단계는 검증 결과를 아직 보지 않는다
        self.assertTrue(g.run_checks(stage="author", st=st, **dict(args, vres=None))["ok"])


class WatchdogTest(unittest.TestCase):
    """E6 — 탐사선 정지 35일 초과는 매일 1건, 엔진 실패는 7일 재알림, 회복 1회."""

    def _days(self, n, st_doc, watch, start="2026-10-24"):
        state, log = {}, []
        d0 = datetime.strptime(start, "%Y-%m-%d")
        for i in range(n):
            t = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
            log.append((t, ps.judge_probe(st_doc, watch, state, t)))
        return log, state

    def test_stale_daily(self):
        log, _ = self._days(3, {"run": {"run_date": "2026-09-02"}}, {})
        a = [x for _, al in log for x in al]
        self.assertEqual(len(a), 3)
        self.assertEqual(len({x["sig"] for x in a}), 3)          # 날짜별 서명 → 하루 1건
        self.assertIn("52일 경과", a[0]["text"])

    def test_within_window_silent(self):
        log, _ = self._days(3, {"run": {"run_date": "2026-10-20"}}, {})
        self.assertEqual([x for _, al in log for x in al], [])

    def test_stale_recovery_once(self):
        state = {}
        ps.judge_probe({"run": {"run_date": "2026-09-02"}}, {}, state, "2026-10-24")
        a = ps.judge_probe({"run": {"run_date": "2026-10-24"}}, {}, state, "2026-10-25")
        b = ps.judge_probe({"run": {"run_date": "2026-10-24"}}, {}, state, "2026-10-26")
        self.assertEqual(([x["kind"] for x in a], b), (["recover"], []))

    def test_engine_failure_realert(self):
        watch = {"engine": {"last_run": {"conclusion": "failure", "created_at": "2026-11-01T00:13Z",
                                         "url": "https://github.com/x/runs/1"}}}
        log, state = self._days(21, {"run": {"run_date": "2026-10-24"}}, watch)
        hits = [t for t, al in log for x in al if x["sig"].startswith("sentinel:probe-fail:")]
        self.assertEqual(hits, ["2026-10-24", "2026-10-31", "2026-11-07"])
        ok = {"engine": {"last_run": {"conclusion": "success"}}}
        r = ps.judge_probe({"run": {"run_date": "2026-10-24"}}, ok, state, "2026-11-15")
        self.assertEqual([x["kind"] for x in r], ["recover"])


class WorkflowWiringTest(unittest.TestCase):
    """spacex-probe.yml 이 헌법대로 묶여 있는가 — 도구 제한·관문·DM 전용."""

    def setUp(self):
        self.y = (Path(__file__).parent.parent / ".github" / "workflows" /
                  "spacex-probe.yml").read_text(encoding="utf-8")

    def test_triggers_and_concurrency(self):
        for k in ("cron: '13 0 1 * *'", "workflow_dispatch:", "types: [spacex-event]",
                  "group: spacex-probe"):
            self.assertIn(k, self.y)

    def test_bash_only_gate(self):
        self.assertIn("Bash(python scripts/probe_gate.py *)", self.y)
        allow = [ln for ln in self.y.splitlines() if "AUTHOR_ALLOW:" in ln][0]
        self.assertNotIn("Bash,", allow.replace("Bash(python", ""))   # 맨 Bash 허용 금지

    def test_no_public_channel(self):
        self.assertNotIn("TELEGRAM_CHAT_ID:", self.y)

    def test_daily_never_writes_agent_research(self):
        d = (Path(__file__).parent.parent / ".github" / "workflows" /
             "daily-update.yml").read_text(encoding="utf-8")
        self.assertNotIn("probe_gate.py publish", d)
        self.assertNotIn("claude -p", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
