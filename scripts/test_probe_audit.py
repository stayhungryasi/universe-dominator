#!/usr/bin/env python3
"""
SpaceX 숫자 감사관 테스트 (2026-10-27, 외부 검수 S5) — 정확 일치 · 숫자 경계 · 교차 산식
실행: python scripts/test_probe_audit.py
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import probe_audit as pa   # noqa: E402

ROOT = Path(__file__).parent.parent
# 3Q26 Ex.99.1 표의 칸들(원문 숫자 그대로 — 채팅 측 원문 대조로 확인한 값)
EX99 = """<table><tr><td>Total revenues</td><td>$</td><td>14,575</td><td>5,576</td></tr>
<tr><td>Income (loss) from operations</td><td>2,791</td><td>(273)</td></tr>
<tr><td>Net income (loss)</td><td>4,404</td><td>(1,054)</td></tr>
<tr><td>Purchases of property and equipment</td><td>19,593</td></tr>
<tr><td>Cash and cash equivalents</td><td>51,294</td><td>Marketable securities</td><td>38,561</td></tr>
<tr><td>Space</td><td>1,767</td><td>Connectivity</td><td>5,248</td><td>SI</td><td>7,560</td></tr>
<tr><td>Starlink consumer subscribers (# in millions)</td><td>13.6</td><td>12.0</td><td>7.2</td></tr>
<tr><td>Starlink consumer ARPU ($ per month)</td><td>$69</td><td>$66</td><td>$83</td></tr>
<tr><td>Nameplate compute (in GW)</td><td>1.7</td><td>1.4</td><td>0.6</td></tr>
<tr><td>Total launches (#)</td><td>35</td><td>38</td><td>44</td></tr></table>"""

KPI = {"auto": {"shares_outstanding_m": 13581, "quarters": [
    {"quarter": "3Q26", "revenue_usd_m": 14575, "operating_income_usd_m": 2791, "net_income_usd_m": 4404,
     "capex_usd_m": 19593, "ocf_usd_m": 9631, "fcf_usd_m": -9962, "cash_usd_m": 51294,
     "marketable_securities_usd_m": 38561}]}}
STATE = {"kpi_history": [{"quarter": "3Q26", "subscribers_m": 13.6, "arpu_usd": 69, "ai_nameplate_gw": 1.7,
                          "launches": 35, "rev_connectivity_usd_m": 5248, "rev_si_usd_m": 7560, "rev_space_usd_m": 1767}],
         "market": {"price_usd": 147.49, "price_asof": "2026-10-23", "market_cap_usd_bn": 2003}}


class AuditTest(unittest.TestCase):
    def test_all_match(self):
        checks, q = pa.audit(KPI, STATE, EX99, latest_mc=2026.0)
        n, ok, bad, unv = pa.summary(checks)
        self.assertEqual(q, "3Q26")
        self.assertEqual((n, ok, bad, unv), (16, 16, [], []))
        self.assertTrue(any(c["id"] == "info:mcap_fresh" and c["verdict"] == "ok" for c in checks))

    def test_wrong_probe_value_caught(self):
        st = copy.deepcopy(STATE)
        st["kpi_history"][0]["arpu_usd"] = 70
        bad = pa.summary(pa.audit(KPI, st, EX99)[0])[2]
        self.assertEqual([c["id"] for c in bad], ["probe:arpu_usd"])

    def test_number_boundary(self):
        self.assertFalse(pa.present("total 114,575 units", ["14,575"]))
        self.assertTrue(pa.present("revenues $ 14,575 and", ["14,575"]))
        self.assertTrue(pa.present("net loss (1,054)", pa.number_forms(-1054, "m")))
        self.assertFalse(pa.present("13.65 million", ["13.6"]))

    def test_cross_checks(self):
        k = copy.deepcopy(KPI)
        k["auto"]["quarters"][0]["fcf_usd_m"] = -9000
        bad = pa.summary(pa.audit(k, STATE, EX99)[0])[2]
        self.assertIn("cross:fcf", [c["id"] for c in bad])
        st = copy.deepcopy(STATE)
        st["kpi_history"][0]["rev_si_usd_m"] = 7000
        ids = [c["id"] for c in pa.summary(pa.audit(KPI, st, EX99)[0])[2]]
        self.assertEqual(sorted(ids), ["cross:segments", "probe:rev_si_usd_m"])

    def test_no_text_is_unverifiable_not_ok(self):
        n, ok, bad, unv = pa.summary(pa.audit(KPI, STATE, None)[0])
        self.assertEqual((ok, len(bad)), (3, 0))            # 교차 산식 3개만 통과, 원문 대조 13개는 '대조 불가'
        self.assertEqual(len(unv), 13)

    def test_stale_engine_price_flagged(self):
        c = [x for x in pa.audit(KPI, STATE, EX99, latest_mc=2400.0)[0] if x["id"] == "info:mcap_fresh"][0]
        self.assertEqual(c["verdict"], "unverifiable")
        self.assertIn("+19.8%", c["why"])

    def test_repo_data_shape(self):
        """저장소 실제 파일로도 돌아간다 — 키 이름이 바뀌면 여기서 운다."""
        kpi = json.loads((ROOT / "data" / "spacex_kpi.json").read_text(encoding="utf-8"))
        st = json.loads((ROOT / "agent-research" / "state" / "spacex_state.json").read_text(encoding="utf-8"))
        checks, q = pa.audit(kpi, st, EX99)
        n, ok, bad, unv = pa.summary(checks)
        self.assertGreaterEqual(n, 14)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
