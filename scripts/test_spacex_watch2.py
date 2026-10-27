#!/usr/bin/env python3
"""
SpaceX 감시 개정 테스트 (2026-10-27, 외부 검수 G1~G3·G8·G9·G11 재현)
================================================================================
재현의 원본: 2026-10-26 SpaceX 가 $400억 GPU 금융리스 약정 8-K 를 냈다
(accn 0001104659-26-118614, Item 8.01). 옛 분류표는 8.01 을 'filing' 으로 보고
DM 도 엔진 발사도 하지 않았다 — 01:22 KST 회차가 기록만 남겼다(실측).

이 테스트는 '경보가 났다'가 아니라 '정확히 몇 건, 무엇'으로 단언한다.
실행: python scripts/test_spacex_watch2.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import spacex_watch as sw   # noqa: E402

ACCN_LEASE = "0001104659-26-118614"
LEASE_TEXT = ("<html><body><p>Item 8.01. Other Events.</p><p>On October 24, 2026, Space Exploration "
              "Technologies Corp. entered into a binding commitment letter to enter into a master finance "
              "lease with an entity managed by Apollo Global Management, Inc.</p></body></html>")
T5 = {"id": "T5", "name": "$400억 조달 확정 조건(8-K) — 금리·담보·만기·증자 포함 여부",
      "expected_date": "2026-10-31 ~ 2026-11-06", "status": "대기"}


def sub_of(rows):
    """rows: (form, date, items, accn, doc)"""
    return {"filings": {"recent": {
        "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows], "items": [r[2] for r in rows],
        "accessionNumber": [r[3] for r in rows], "primaryDocument": [r[4] for r in rows]}}}


FORM4_SALE = """<?xml version="1.0"?>
<ownershipDocument>
 <aff10b5One>1</aff10b5One>
 <reportingOwner><reportingOwnerId><rptOwnerName>Shotwell Gwynne</rptOwnerName></reportingOwnerId>
  <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>President and COO</officerTitle></reportingOwnerRelationship>
 </reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction><transactionDate><value>2026-09-22</value></transactionDate>
   <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>342170</value></transactionShares>
    <transactionPricePerShare><value>3.5</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>2814205</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction><transactionDate><value>2026-09-22</value></transactionDate>
   <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>342170</value></transactionShares>
    <transactionPricePerShare><value>153.43</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>2472035</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
  </nonDerivativeTransaction>
 </nonDerivativeTable>
</ownershipDocument>"""

FORM4_SMALL = FORM4_SALE.replace("<aff10b5One>1</aff10b5One>", "").replace("342170", "1000") \
    .replace("2472035", "2000000").replace("2814205", "2001000").replace("Shotwell Gwynne", "Doe Jane")

FORM4_MUSK = FORM4_SMALL.replace("Doe Jane", "Musk Elon")

FORM144 = """<edgarSubmission><formData>
 <issuerInfo><nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold>Gwynne Shotwell</nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold></issuerInfo>
 <securitiesInformation><noOfUnitsSold>342170</noOfUnitsSold><aggregateMarketValue>52500000</aggregateMarketValue></securitiesInformation>
 <remarks>Rule 10b5-1 plan adopted June 23, 2026</remarks></formData></edgarSubmission>"""


class GradeTest(unittest.TestCase):
    def test_grade_table(self):
        g = sw.filing_grade
        self.assertEqual(g("8-K", "8.01,9.01"), "major")          # G1 원본 — 옛 코드는 'filing'(침묵)
        self.assertEqual(g("8-K", "4.02"), "urgent")
        self.assertEqual(g("8-K", "5.02"), "major")
        self.assertEqual(g("8-K", "2.01,9.01"), "major")
        self.assertEqual(g("8-K", "7.01,9.01"), "record")
        self.assertEqual(g("8-K", "9.01"), "record")
        self.assertEqual(g("8-K/A", "3.01"), "urgent")
        self.assertEqual(g("NT 10-Q", ""), "urgent")
        self.assertEqual(g("10-K", ""), "major")
        self.assertEqual(g("424B2", ""), "major")
        self.assertEqual(g("DEF 14A", ""), "major")
        self.assertEqual(g("425", ""), "major")
        self.assertEqual(g("S-8", ""), "record")
        self.assertEqual(g("CORRESP", ""), "record")
        self.assertEqual(g("SC 13G/A", ""), "record")
        self.assertEqual(g("NEWFORM-X", ""), "record")             # 표에 없는 양식도 최소 기록

    def test_refine_kind_from_text(self):
        self.assertEqual(sw.refine_kind("filing", LEASE_TEXT), "financing")
        self.assertEqual(sw.refine_kind("filing", "<p>entered into a definitive Merger Agreement</p>"), "contract")
        self.assertEqual(sw.refine_kind("filing", "<p>annual meeting date</p>"), "filing")
        self.assertEqual(sw.refine_kind("earnings", LEASE_TEXT), "earnings")     # 항목번호가 1순위


class InsiderTest(unittest.TestCase):
    def test_form4_sale_detail(self):
        info = sw.parse_form4(FORM4_SALE)
        self.assertEqual(info["owners"][0]["name"], "Shotwell Gwynne")
        self.assertIn("President and COO", info["owners"][0]["roles"])
        self.assertEqual(info["sold_shares"], 342170)
        self.assertAlmostEqual(info["sold_usd"] / 1e6, 52.50, places=1)
        self.assertTrue(info["plan_10b5_1"])
        self.assertEqual(info["after"], 2472035)
        self.assertAlmostEqual(info["pct_disposed"], 342170 / (2472035 + 342170), places=4)
        self.assertEqual(sw.insider_grade("4", info), "major")       # 보유 12% 처분 ≥ 5%
        t = sw.insider_title("4", info)
        self.assertIn("매도 342,170주", t)
        self.assertIn("$52.5M", t)
        self.assertIn("10b5-1", t)

    def test_small_record_musk_major(self):
        self.assertEqual(sw.insider_grade("4", sw.parse_form4(FORM4_SMALL)), "record")
        self.assertEqual(sw.insider_grade("4", sw.parse_form4(FORM4_MUSK)), "major")

    def test_form144(self):
        info = sw.parse_form144(FORM144)
        self.assertEqual(info["owners"][0]["name"], "Gwynne Shotwell")
        self.assertEqual(info["planned_usd"], 52500000)
        self.assertIn("매도 예정 신고", sw.insider_title("144", info))

    def test_bad_xml_is_none_not_guess(self):
        self.assertIsNone(sw.parse_form4("<not-xml"))
        ev = sw.insider_event({"form": "4", "accn": "X-1", "date": "2026-10-27", "doc": "x.xml"}, "<bad")
        self.assertEqual(ev["grade"], "record")
        self.assertIn("원문 해석 실패", ev["title"])

    def test_xml_url_strips_xsl(self):
        self.assertEqual(sw.insider_xml_url("0001-26-9", "xslF345X05/wk-form4_1.xml"),
                         "https://www.sec.gov/Archives/edgar/data/1181412/0001269/wk-form4_1.xml")


class LaunchTest(unittest.TestCase):
    def test_launch_grades(self):
        f9_fail = sw.launch_event({"id": "a", "name": "Falcon 9 Block 5 | Starlink 12-3", "net": "2026-11-02T01:00Z",
                                   "status": {"abbrev": "Failure"}}, "실패")
        self.assertEqual((f9_fail["kind"], f9_fail["grade"]), ("launch_fail", "urgent"))
        ss = sw.launch_event({"id": "b", "name": "Starship | Flight 15", "net": "2026-11-08T12:00Z",
                              "status": {"abbrev": "Success"}}, "Starship")
        self.assertEqual((ss["kind"], ss["grade"]), ("starship", "major"))


class LockupTest(unittest.TestCase):
    PROSPECTUS = ("<p>Our executive officers, directors and holders of substantially all of our capital stock have "
                  "agreed with the underwriters, subject to certain exceptions, not to sell any shares for 180 days "
                  "after the date of this prospectus (the &ldquo;lock-up period&rdquo;).</p><p>Notwithstanding the "
                  "foregoing, 25% of the shares subject to the lock-up agreements will be released 90 days after the "
                  "date of this prospectus.</p><p>We expect to use the net proceeds for general corporate purposes. "
                  "The lock-up restrictions also apply to options.</p>")

    def test_sentences_quoted_with_numbers_only(self):
        out = sw.lockup_sentences(self.PROSPECTUS)
        self.assertEqual(len(out), 2)                                  # 숫자 없는 락업 문장·무관 문장 제외
        self.assertIn("180 days", out[0])
        self.assertIn('"lock-up period"', out[0])
        self.assertIn("25% of the shares", out[1])

    def test_collect_once_per_accn(self):
        import feed_client as fc
        watch = {}
        ev = [{"form": "424B4", "date": "2026-06-12", "accn": "P-1", "url": "https://x/p.htm"}]
        with mock.patch.object(sw, "sec_headers", return_value={"User-Agent": "t"}), \
                mock.patch.object(fc, "fetch", return_value=(self.PROSPECTUS, "ok", 200)) as f, \
                mock.patch.object(fc, "record"):
            logs = []
            sw.collect_prospectus_facts(fc, watch, ev, logs)
            sw.collect_prospectus_facts(fc, watch, ev, logs)
        self.assertEqual(f.call_count, 1)                              # 같은 공시는 다시 받지 않는다
        self.assertEqual((watch["facts"]["lockup"]["accn"], len(watch["facts"]["lockup"]["sentences"])), ("P-1", 2))


class CockpitTileTest(unittest.TestCase):
    """G6: 화살표는 바로 앞 분기와만 — 빈 분기를 건너뛰어 전년과 비교하지 않는다. FCF 는 '계산' 표식."""

    def _fin(self):
        import probe_cockpit as pc
        q = [{"end": "2026-09-30", "quarter": "3Q26", "revenue_usd_m": 14575, "ocf_usd_m": 9631, "capex_usd_m": 19593,
              "fcf_usd_m": -9962, "src": {"fcf_usd_m": {"how": "영업현금흐름 − 유형자산 취득(같은 분기 도출값)"}}},
             {"end": "2026-06-30", "quarter": "2Q26", "revenue_usd_m": 7814, "fcf_usd_m": None, "src": {}},
             {"end": "2026-03-31", "quarter": "1Q26", "revenue_usd_m": 4694, "src": {}},
             {"end": "2025-12-31", "quarter": "4Q25", "src": {}},
             {"end": "2025-09-30", "quarter": "3Q25", "revenue_usd_m": 5576, "fcf_usd_m": -3112, "src": {}}]
        st = {"kpi_history": [{"quarter": "3Q26", "period_end": "2026-09-30", "arpu_usd": 69, "subscribers_m": 13.6},
                              {"quarter": "2Q26", "period_end": "2026-06-30", "arpu_usd": 66, "subscribers_m": 12.0},
                              {"quarter": "3Q25", "period_end": "2025-09-30", "arpu_usd": 83, "subscribers_m": 7.2}]}
        fin = pc.kpi_series({"auto": {"quarters": q}}, st)
        return {t["id"]: t for t in pc.kpi_tiles(fin)}

    def test_arrow_basis_fixed(self):
        t = self._fin()
        self.assertEqual(t["fcf"]["dir"], "none")            # 2Q26 비어 있음 → 화살표 없음(옛 코드: 3Q25 와 비교해 ↘)
        self.assertEqual(t["arpu"]["dir"], "up")             # 66 → 69 (QoQ)
        self.assertAlmostEqual(t["arpu"]["yoy"], 69 / 83 - 1, places=4)    # 전년 −17% 는 따로
        self.assertIsNone(t["fcf"]["yoy"])                   # 음수 기준의 % 는 싣지 않는다
        self.assertEqual(t["revenue"]["dir"], "up")

    def test_fcf_marked_calc(self):
        self.assertEqual(self._fin()["fcf"]["mk"], "계산")

    def test_qshift(self):
        import probe_cockpit as pc
        self.assertEqual((pc.qshift("1Q26", -1), pc.qshift("3Q26", -4), pc.qshift("4Q25", 1)), ("4Q25", "3Q25", "1Q26"))


class RunWiringTest(unittest.TestCase):
    """run() 을 실제로 돌린다 — 수집만 가짜. DM·대기열·due 를 '정확히' 센다."""

    def setUp(self):
        self._saved = (sw.WATCH_PATH, sw.KPI_PATH, sw.STATE_PATH, sw.HOURLY_PATH)
        self.tmp = Path(tempfile.mkdtemp())
        sw.WATCH_PATH, sw.KPI_PATH = self.tmp / "w.json", self.tmp / "k.json"
        sw.STATE_PATH, sw.HOURLY_PATH = self.tmp / "s.json", self.tmp / "h.json"
        sw.STATE_PATH.write_text(json.dumps({"triggers_next_12m": [T5], "run": {"run_date": "2026-10-26"}}),
                                 encoding="utf-8")

    def tearDown(self):
        sw.WATCH_PATH, sw.KPI_PATH, sw.STATE_PATH, sw.HOURLY_PATH = self._saved

    def _run(self, sub, mode="daily", texts=None, now="2026-10-27T09:30:00+09:00", dispatch_ok=True, env=None):
        import feed_client as fc
        dms, fired = [], []
        texts = texts or {}

        def fake_fetch(url, label, kind, headers=None, timeout=25):
            for k, v in texts.items():
                if k in url:
                    return v, "ok", 200
            return None, "http_error", 404

        def fake_dispatch(q):
            fired.append(q)
            return dispatch_ok, 204 if dispatch_ok else 403
        coll = "collect_sec_light" if mode == "hourly" else "collect_sec"
        patches = [mock.patch.object(sw, coll, return_value=(sw.parse_filings(sub)[0], sub)),
                   mock.patch.object(sw, "collect_launches", return_value=[]),
                   mock.patch.object(sw, "collect_ir", return_value=[]),
                   mock.patch.object(sw, "collect_engine"),
                   mock.patch.object(sw, "collect_hourly_runs"),
                   mock.patch.object(fc, "flush"),
                   mock.patch.object(fc, "fetch", side_effect=fake_fetch),
                   mock.patch.object(sw, "sec_headers", return_value={"User-Agent": "t"}),
                   mock.patch.object(sw, "now_kst", return_value=datetime.fromisoformat(now)),
                   mock.patch.object(sw, "send_dm", side_effect=lambda t: dms.append(t) or True),
                   mock.patch.object(sw, "_dispatch", side_effect=fake_dispatch),
                   mock.patch.object(sw.time, "sleep"),
                   mock.patch.dict("os.environ", env or {}, clear=False)]
        for p in patches:
            p.start()
        try:
            sw.run(mode)
        finally:
            for p in reversed(patches):
                p.stop()
        w = json.loads(sw.WATCH_PATH.read_text(encoding="utf-8")) if sw.WATCH_PATH.exists() else {}
        h = json.loads(sw.HOURLY_PATH.read_text(encoding="utf-8")) if sw.HOURLY_PATH.exists() else {}
        return dms, fired, w, h

    BASE = [("10-Q", "2026-10-22", "", "0001628280-26-066880", "spcx-20260930.htm")]
    LEASE = ("8-K", "2026-10-26", "8.01", ACCN_LEASE, "tm2628247d1_8k.htm")

    def _baseline(self):
        dms, fired, w, h = self._run(sub_of(self.BASE), now="2026-10-25T10:30:00+09:00")
        self.assertEqual((dms, fired), ([], []))
        self.assertEqual(w["schema"], sw.SCHEMA)

    def test_g1_lease_8k_wakes_engine_via_t5(self):
        """G1 재현: 8.01 금융리스 8-K → 본문 핵심어로 '조달' → T5 사건 감지 · DM 1통 · 대기열 1건."""
        self._baseline()
        dms, fired, w, h = self._run(sub_of([self.LEASE] + self.BASE), texts={"tm2628247d1_8k": LEASE_TEXT})
        new = [d for d in w["due"] if not d.get("baseline")]
        self.assertEqual([(d["trigger_id"], d["reason"]) for d in new], [("T5", "사건 감지")])
        self.assertEqual(len(dms), 1)
        self.assertIn(ACCN_LEASE.replace("-", ""), dms[0])          # 원문 링크가 DM 에
        self.assertEqual(len(w["dispatch_queue"]), 1)
        self.assertEqual(fired, [])                                   # 데일리는 커밋 뒤에 쏜다

    def test_g1_without_text_still_wakes(self):
        """본문을 못 읽어도(SEC 오류) 8.01 은 '중요 사건'으로 엔진을 깨운다 — 조용히 기록만 하지 않는다."""
        self._baseline()
        dms, fired, w, h = self._run(sub_of([self.LEASE] + self.BASE))
        new = [d for d in w["due"] if not d.get("baseline")]
        self.assertEqual([(d["trigger_id"], d["reason"]) for d in new], [(None, "중요 사건")])
        self.assertEqual((len(dms), len(w["dispatch_queue"])), (1, 1))

    def test_record_grade_dm_only(self):
        self._baseline()
        dms, fired, w, h = self._run(sub_of([("S-8", "2026-10-27", "", "0001-26-77", "s8.htm")] + self.BASE))
        self.assertEqual([d for d in w["due"] if not d.get("baseline")], [])
        self.assertEqual(len(dms), 1)
        self.assertIn("[기록]", dms[0])
        self.assertEqual(w["dispatch_queue"], [])

    def test_urgent_402(self):
        self._baseline()
        dms, fired, w, h = self._run(sub_of([("8-K", "2026-10-27", "4.02", "0001-26-88", "nr.htm")] + self.BASE))
        self.assertTrue(dms[0].startswith("🚨"))
        self.assertEqual([d["grade"] for d in w["due"] if not d.get("baseline")], ["urgent"])

    def test_insider_detail_event(self):
        self._baseline()
        rows = [("4", "2026-10-27", "", "0001-26-44", "xslF345X05/f4.xml")] + self.BASE
        dms, fired, w, h = self._run(sub_of(rows), texts={"/f4.xml": FORM4_SALE})
        ev = next(e for e in w["events"] if e.get("kind") == "insider")
        self.assertEqual(ev["grade"], "major")
        self.assertIn("Shotwell", ev["title"])
        self.assertEqual(len(w["dispatch_queue"]), 1)
        self.assertIn("0001-26-44", w["seen"])

    def test_insider_fetch_fail_retries_next_run(self):
        self._baseline()
        rows = [("4", "2026-10-27", "", "0001-26-45", "xslF345X05/f5.xml")] + self.BASE
        dms, fired, w, h = self._run(sub_of(rows))                   # 원문 못 받음
        self.assertNotIn("0001-26-45", w["seen"])                     # 다음 회차에 다시 읽는다
        dms, fired, w, h = self._run(sub_of(rows), texts={"/f5.xml": FORM4_SMALL})
        self.assertIn("0001-26-45", w["seen"])

    def test_migration_quiet_old_and_catchup_recent(self):
        """이관 회차(schema 1): 새로 감시하는 양식의 옛 공시는 조용히, 옛 분류표가 놓친 최근 8.01 은 한 번 다시 판정."""
        old_watch = {"seen": ["0001628280-26-066880", ACCN_LEASE], "due": [],
                     "events": [{"date": "2026-10-26", "form": "8-K", "items": "8.01", "accn": ACCN_LEASE,
                                 "kind": "filing", "title": "수시공시 — 기타 중요 사건", "source": "SEC EDGAR",
                                 "url": "https://www.sec.gov/Archives/edgar/data/1181412/000110465926118614/tm2628247d1_8k.htm"}]}
        sw.WATCH_PATH.write_text(json.dumps(old_watch), encoding="utf-8")
        rows = [self.LEASE, ("S-8", "2026-10-22", "", "0001-26-31", "s8.htm")] + self.BASE
        dms, fired, w, h = self._run(sub_of(rows), texts={"tm2628247d1_8k": LEASE_TEXT})
        new = [d for d in w["due"] if not d.get("baseline")]
        self.assertEqual([(d["trigger_id"], d["reason"]) for d in new], [("T5", "사건 감지")])
        self.assertEqual(len(dms), 1)
        self.assertIn("이관 회차", dms[0])
        self.assertNotIn("[기록] 임직원 주식보상 등록", dms[0])        # 옛 S-8 은 기준선
        self.assertEqual(w["schema"], sw.SCHEMA)
        lease = next(e for e in w["events"] if e.get("accn") == ACCN_LEASE)
        self.assertEqual((lease["kind"], lease["grade"]), ("financing", "major"))
        # 같은 사건으로 다시 울지 않는다
        dms, fired, w, h = self._run(sub_of(rows), texts={"tm2628247d1_8k": LEASE_TEXT})
        self.assertEqual(dms, [])

    def test_hourly_fires_now_and_daily_does_not_repeat(self):
        self._baseline()
        rows = [("8-K", "2026-10-27", "4.02", "0001-26-90", "nr.htm")] + self.BASE
        before = sw.WATCH_PATH.read_text(encoding="utf-8")
        dms, fired, w, h = self._run(sub_of(rows), mode="hourly")
        self.assertEqual((len(dms), len(fired)), (1, 1))             # 즉시 발사
        self.assertIn("즉시 발사", dms[0])
        self.assertEqual(sw.WATCH_PATH.read_text(encoding="utf-8"), before)   # 데일리 파일은 건드리지 않는다
        self.assertIn("0001-26-90", h["alerted"])
        self.assertEqual(h["dispatches"][0]["state"], "sent")
        # 데일리가 같은 공시를 봐도 다시 울지 않고, due 는 화면 목록에 합친다
        dms, fired, w, h2 = self._run(sub_of(rows))
        self.assertEqual(dms, [])
        self.assertEqual([d["grade"] for d in w["due"] if not d.get("baseline")], ["urgent"])

    def test_hourly_never_writes_daily_files(self):
        """매시간 감시는 spacex_hourly.json 하나만 쓴다 — 데일리 파일을 쓰면 두 워크플로의 커밋이 충돌한다."""
        self._baseline()
        written = []
        real = sw.write_json
        with mock.patch.object(sw, "write_json", side_effect=lambda p, d: written.append(Path(p).name) or real(p, d)):
            self._run(sub_of([("8-K", "2026-10-27", "5.02", "0001-26-91", "o.htm")] + self.BASE), mode="hourly")
        self.assertEqual(written, ["h.json"])

    def test_hourly_waits_for_daily_migration(self):
        sw.WATCH_PATH.write_text(json.dumps({"seen": ["x"]}), encoding="utf-8")       # schema 1
        dms, fired, w, h = self._run(sub_of(self.BASE), mode="hourly")
        self.assertEqual((dms, fired, h), ([], [], {}))

    def test_hourly_no_change_no_write_diff(self):
        """바뀐 것이 없으면 파일 내용이 그대로 — 매시간 커밋(Cloudflare 빌드)이 생기지 않는다."""
        self._baseline()
        self._run(sub_of(self.BASE), mode="hourly")
        a = sw.HOURLY_PATH.read_text(encoding="utf-8")
        self._run(sub_of(self.BASE), mode="hourly", now="2026-10-27T11:47:00+09:00")
        self.assertEqual(sw.HOURLY_PATH.read_text(encoding="utf-8"), a)

    def test_heartbeat_once_a_day(self):
        self._baseline()
        env = {"SPACEX_HEARTBEAT": "1"}
        dms, *_ = self._run(sub_of(self.BASE), mode="hourly", env=env, now="2026-10-27T07:47:00+09:00")
        self.assertEqual(dms, [])                                     # 08시 전
        dms, *_ = self._run(sub_of(self.BASE), mode="hourly", env=env, now="2026-10-27T08:47:00+09:00")
        self.assertEqual(len(dms), 1)
        self.assertIn("일일 점검", dms[0])
        self.assertIn("T5 10/31(D-4)", dms[0])
        self.assertIn("오지 않으면 감시가 멈춘 것", dms[0])
        dms, *_ = self._run(sub_of(self.BASE), env=env, now="2026-10-27T10:30:00+09:00")   # 데일리도 같은 날
        self.assertEqual(dms, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
