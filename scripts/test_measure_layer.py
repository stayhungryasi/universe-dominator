#!/usr/bin/env python3
"""
SpaceX 측정층 테스트 (2026-10-23, M1·M2) — spacex_watch 순수 함수
실행: python scripts/test_measure_layer.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import spacex_watch as sw   # noqa: E402


def _f(start, end, val, filed="2026-10-22", form="10-Q", fp="Q3"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "fp": fp, "fy": 2026,
         "accn": "0001628280-26-066880"}
    if start:
        d["start"] = start
    return d


class FilingTest(unittest.TestCase):
    def test_title_from_state_not_machine_keys(self):
        t = sw.filing_title("8-K", "2.02,9.01")
        self.assertEqual(t, "수시공시 — 실적 발표")
        self.assertNotIn("2.02", t)
        self.assertEqual(sw.filing_title("10-Q", ""), "분기보고서")
        self.assertEqual(sw.filing_title("8-K/A", "1.01"), "수시공시 정정 — 중요 계약 체결")
        self.assertEqual(sw.filing_title("424B4", ""), "증권 발행 설명서")

    def test_kind(self):
        self.assertEqual(sw.event_kind("8-K", "2.02,9.01"), "earnings")
        self.assertEqual(sw.event_kind("8-K", "1.01,3.02"), "contract")
        self.assertEqual(sw.event_kind("8-K", "2.01,3.02,9.01"), "financing")
        self.assertEqual(sw.event_kind("424B4", ""), "financing")
        self.assertEqual(sw.event_kind("10-Q", ""), "report")

    def test_parse_filings_all_forms_insiders_apart(self):
        """2026-10-27 개정: 모든 양식을 싣는다(S-8 = 기록). 내부자 양식(4·144)은 따로 — 상세는 원문 XML."""
        sub = {"filings": {"recent": {
            "form": ["8-K", "4", "10-Q", "S-8", "144"],
            "filingDate": ["2026-10-20", "2026-09-24", "2026-10-22", "2026-10-22", "2026-09-22"],
            "items": ["2.02,9.01", "", "", "", ""],
            "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4", "0001-26-5"],
            "primaryDocument": ["a.htm", "b.xml", "c.htm", "d.htm", "e.xml"]}}}
        ev, ins = sw.parse_filings(sub)
        self.assertEqual([e["form"] for e in ev], ["8-K", "10-Q", "S-8"])
        self.assertEqual([e["grade"] for e in ev], ["major", "major", "record"])
        self.assertEqual([r["form"] for r in ins], ["4", "144"])
        self.assertEqual(ev[0]["url"],
                         "https://www.sec.gov/Archives/edgar/data/1181412/0001261/a.htm")


class KpiTest(unittest.TestCase):
    def test_quarter_flow_direct(self):
        facts = [_f("2026-07-01", "2026-09-30", 14575e6), _f("2026-01-01", "2026-09-30", 27083e6)]
        v, f, how = sw.quarter_flow(facts, "2026-09-30")
        self.assertEqual((v, how), (14575e6, "분기 보고값"))

    def test_quarter_flow_ytd_difference(self):
        """Capex 는 누적만 공시된다 — 9개월 48,069 − 6개월 28,476 = 3분기 19,593."""
        facts = [_f("2026-01-01", "2026-06-30", 28476e6, "2026-08-04", fp="Q2"),
                 _f("2026-01-01", "2026-09-30", 48069e6)]
        v, f, how = sw.quarter_flow(facts, "2026-09-30")
        self.assertEqual(round(v / 1e6), 19593)
        self.assertIn("누적", how)

    def test_quarter_flow_front_difference(self):
        """1분기 = 상반기 누적 − 2분기(3개월). 9월 보고서 공시 표의 1Q26 4,694 와 같아야 한다."""
        facts = [_f("2026-01-01", "2026-06-30", 12508e6, "2026-08-04", fp="Q2"),
                 _f("2026-04-01", "2026-06-30", 7814e6, "2026-08-04", fp="Q2")]
        v, f, how = sw.quarter_flow(facts, "2026-03-31")
        self.assertEqual(round(v / 1e6), 4694)
        self.assertIn("기간", how)

    def test_eight_quarters_with_reasoned_gaps(self):
        cf = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _f("2026-07-01", "2026-09-30", 14575e6)]}}}}}
        rows = sw.build_quarters(cf)
        self.assertEqual([r["quarter"] for r in rows],
                         ["3Q26", "2Q26", "1Q26", "4Q25", "3Q25", "2Q25", "1Q25", "4Q24"])
        self.assertIn("S-1", rows[3]["src"]["revenue_usd_m"]["note"])     # 4분기 빈칸에 사유

    def test_q4_is_annual_minus_nine_months_and_marked(self):
        """결정 4①(2026-10-23): 4분기 = 10-K 연간(FY) − 같은 해 9개월 누적. 계산값은 derived 표식."""
        fy = dict(_f("2026-01-01", "2026-12-31", 45000e6, "2027-02-20", fp="FY"), form="10-K")
        nine = _f("2026-01-01", "2026-09-30", 27083e6)
        v, f, how = sw.quarter_flow([fy, nine], "2026-12-31")
        self.assertEqual(round(v / 1e6), 45000 - 27083)
        self.assertIn("누적 2026-01-01~2026-12-31 − 누적 2026-01-01~2026-09-30", how)
        cf = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [fy, nine]}}}}}
        row = sw.build_quarters(cf, max_q=1)[0]
        self.assertEqual((row["quarter"], row["revenue_usd_m"]), ("4Q26", 17917))
        self.assertTrue(row["src"]["revenue_usd_m"]["derived"])

    def test_quarter_flow_missing_prior_is_none(self):
        """1분기 누적이 없으면 2분기 Capex 는 만들지 않는다(추정으로 채우지 않는다)."""
        facts = [_f("2026-01-01", "2026-06-30", 28476e6, "2026-08-04", fp="Q2")]
        self.assertEqual(sw.quarter_flow(facts, "2026-06-30")[0], None)

    def test_latest_filed_wins(self):
        facts = [_f("2026-04-01", "2026-06-30", 7000e6, "2026-08-04"),
                 _f("2026-04-01", "2026-06-30", 7814e6, "2026-10-22")]
        self.assertEqual(sw.quarter_flow(facts, "2026-06-30")[0], 7814e6)

    def test_cover_shares_sum_classes(self):
        html = ('<ix:nonFraction unitRef="shares" contextRef="c-2" name="dei:EntityCommonStockSharesOutstanding"'
                ' scale="0">8,246,541,218</ix:nonFraction> x '
                '<ix:nonFraction unitRef="shares" contextRef="c-3" name="dei:EntityCommonStockSharesOutstanding"'
                ' scale="0">5,334,522,491</ix:nonFraction>'
                '<xbrli:context id="c-2"><xbrli:entity/><xbrli:period><xbrli:instant>2026-10-16'
                '</xbrli:instant></xbrli:period></xbrli:context>')
        n, asof, k = sw.parse_cover_shares(html)
        self.assertEqual((n, asof, k), (13581063709, "2026-10-16", 2))
        self.assertEqual(sw.parse_cover_shares("<html></html>"), (None, None, 0))


class TriggerTest(unittest.TestCase):
    def test_parse_dates(self):
        p = sw.parse_trigger_date
        self.assertEqual(str(p("2026-11-초(추정)")), "2026-11-01")
        self.assertEqual(str(p("2026-12-08 ~ 2027-01-07")), "2026-12-08")
        self.assertEqual(str(p("2026-4Q(추정)")), "2026-10-01")
        self.assertEqual(str(p("2026-2H ~ 2027-1H")), "2026-07-01")
        self.assertEqual(str(p("2026-11-말")), "2026-11-21")
        self.assertIsNone(p("미정"))

    TRIG = [{"id": "T1", "name": "3Q26 실적의 AI 단위경제", "expected_date": "2026-11-초(추정)",
             "status": "대기"},
            {"id": "T3", "name": "Starship Flight 15", "expected_date": "2027-1Q", "status": "대기"},
            {"id": "T9", "name": "실적 확인 완료분", "expected_date": "2026-01-01", "status": "확인"}]

    def test_event_detection_exactly_once(self):
        ev = [{"kind": "earnings", "accn": "A1", "title": "수시공시 — 실적 발표", "date": "2026-10-20"}]
        d1 = sw.judge_triggers(self.TRIG, ev, "2026-10-23", set())
        self.assertEqual([(d["trigger_id"], d["reason"]) for d in d1], [("T1", "사건 감지")])
        again = sw.judge_triggers(self.TRIG, ev, "2026-10-24", {d["key"] for d in d1})
        self.assertEqual(again, [])            # 같은 사건으로 두 번 울지 않는다

    def test_date_arrival(self):
        d = sw.judge_triggers(self.TRIG, [], "2026-11-02", set())
        self.assertEqual([(x["trigger_id"], x["reason"]) for x in d], [("T1", "예상일 도래")])

    def test_window_already_started_when_written_is_silent(self):
        trig = [{"id": "T5", "name": "$400억 조달", "expected_date": "2026-4Q ~ 2027-1H", "status": "대기"}]
        self.assertEqual(sw.judge_triggers(trig, [], "2026-10-23", set(), written_on="2026-10-23"), [])
        # 작성 이후에 처음 오는 시작일은 운다
        trig2 = [{"id": "T1", "name": "4Q26 실적", "expected_date": "2027-02-초", "status": "대기"}]
        self.assertEqual(len(sw.judge_triggers(trig2, [], "2027-02-02", set(), written_on="2026-10-23")), 1)

    def test_contract_8k_without_trigger(self):
        ev = [{"kind": "contract", "accn": "C1", "title": "수시공시 — 중요 계약 체결", "date": "2026-10-08"}]
        d = sw.judge_triggers([], ev, "2026-10-23", set())
        self.assertEqual([x["reason"] for x in d], ["대형 계약 공시"])

    def test_closed_trigger_ignored(self):
        d = sw.judge_triggers(self.TRIG[2:], [{"kind": "earnings", "accn": "Z"}], "2026-10-23", set())
        self.assertEqual(d, [])


class RunTest(unittest.TestCase):
    """배선 — 기준선 회차는 DM·발사 없음, 다음 회차의 새 실적 공시는 DM 1건 + 대기열 1건."""

    def setUp(self):
        self._saved = (sw.WATCH_PATH, sw.KPI_PATH, sw.STATE_PATH)

    def tearDown(self):        # 모듈 경로 원복 — selftest 같은 프로세스의 뒤쪽 검사를 오염시키지 않게
        sw.WATCH_PATH, sw.KPI_PATH, sw.STATE_PATH = self._saved

    def _run(self, tmp, sub, trig):
        import feed_client as fc
        sw.WATCH_PATH, sw.KPI_PATH = tmp / "w.json", tmp / "k.json"
        sw.STATE_PATH = tmp / "s.json"
        sw.STATE_PATH.write_text(__import__("json").dumps({"triggers_next_12m": trig}),
                                 encoding="utf-8")
        dms = []
        with mock.patch.object(sw, "collect_sec", return_value=(sw.parse_filings(sub)[0], sub)), \
                mock.patch.object(sw, "collect_launches", return_value=[]), \
                mock.patch.object(sw, "collect_ir", return_value=[]), \
                mock.patch.object(sw, "collect_engine"), \
                mock.patch.object(fc, "flush"), \
                mock.patch.object(sw, "send_dm", side_effect=lambda t: dms.append(t)):
            sw.run()
        return dms, __import__("json").loads(sw.WATCH_PATH.read_text(encoding="utf-8"))

    def test_baseline_then_new_event(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        # 기준선 회차에도 과거 실적 공시가 있다 — 그래도 울면 안 된다(양치기 감시자 방지)
        sub1 = {"filings": {"recent": {"form": ["8-K", "10-Q"],
                                       "filingDate": ["2026-08-04", "2026-08-04"],
                                       "items": ["2.02,9.01", ""], "accessionNumber": ["E-0", "A-1"],
                                       "primaryDocument": ["e0.htm", "q.htm"]}}}
        trig = [{"id": "T1", "name": "4Q26 실적", "expected_date": "2027-02", "status": "대기"}]
        dms, w = self._run(tmp, sub1, trig)
        self.assertEqual((dms, w["dispatch_queue"]), ([], []))
        sub2 = {"filings": {"recent": {"form": ["8-K", "8-K", "10-Q"],
                                       "filingDate": ["2027-01-28", "2026-08-04", "2026-08-04"],
                                       "items": ["2.02,9.01", "2.02,9.01", ""],
                                       "accessionNumber": ["B-2", "E-0", "A-1"],
                                       "primaryDocument": ["e.htm", "e0.htm", "q.htm"]}}}
        dms, w = self._run(tmp, sub2, trig)
        self.assertEqual(len(dms), 1)
        self.assertEqual(len(w["dispatch_queue"]), 1)
        self.assertEqual([d["reason"] for d in w["due"] if not d.get("baseline")], ["사건 감지"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
