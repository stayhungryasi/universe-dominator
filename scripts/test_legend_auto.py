#!/usr/bin/env python3
"""
레전드 자동층 수리 테스트 (2026-11-03)
================================================================
① 원천 라벨이 기간과 어긋난 회사(F7 CRWD)는 기간으로 라벨을 맞춰 TTM 창을 되살린다 — 정상 회사는 그대로.
② 전망 성장률 — 원천이 장기 성장률을 내지 않으면(NaN) yfinance 연간 EPS 추정으로 잰다(무료 · Finnhub 경로와 같은 뜻).
③ 자동층 축별 커버리지를 산출물에 싣는다(관제탑이 0 인 축을 본다).
실행: python scripts/test_legend_auto.py
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import fetch_buffett_auto as fa   # noqa: E402

SH = 1_000_000_000.0


def rep(label_y, label_q, start, end, ytd_ni):
    rows = [{"concept": "us-gaap_NetIncomeLoss", "value": ytd_ni * SH},
            {"concept": "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding", "value": SH}]
    return {"year": label_y, "quarter": label_q, "startDate": start, "endDate": end, "report": {"ic": rows}}


def crwd_like(mislabel=True):
    """1월 결산 · 누적(YTD) 10-Q · 분기 순이익 1.0/주. FY2026 1분기 10-Q 가 '2025Q1' 로 실린다(실측 F7)."""
    q = [rep(2027, 2, "2026-02-01", "2026-07-31", 2.0),
         rep(2027, 1, "2026-02-01", "2026-04-30", 1.0),
         rep(2026, 3, "2025-02-01", "2025-10-31", 3.0),
         rep(2026, 2, "2025-02-01", "2025-07-31", 2.0),
         rep(2025 if mislabel else 2026, 1, "2025-02-01", "2025-04-30", 1.0),
         rep(2025, 3, "2024-02-01", "2024-10-31", 3.0),
         rep(2025, 2, "2024-02-01", "2024-07-31", 2.0),
         rep(2025, 1, "2024-02-01", "2024-04-30", 1.0)]
    a = [rep(2026, 4, "2025-02-01", "2026-01-31", 4.0), rep(2025, 4, "2024-02-01", "2025-01-31", 4.0)]
    return fa._attach_annual(q, a)


def calendar_like():
    """12월 결산 · 라벨 정상(GOOG 꼴) — 교정이 아무것도 바꾸면 안 된다."""
    q, a = [], []
    for y in (2026, 2025):
        for k in (3, 2, 1):
            end = {1: f"{y}-03-31", 2: f"{y}-06-30", 3: f"{y}-09-30"}[k]
            q.append(rep(y, k, f"{y}-01-01", end, float(k)))
        a.append(rep(y - 1, 4, f"{y - 1}-01-01", f"{y - 1}-12-31", 4.0))
    return fa._attach_annual(q, a)


class RelabelTest(unittest.TestCase):
    def test_crwd_window_revived(self):
        bad = crwd_like()
        # 수리 전 재현 — 같은 라벨 충돌로 사슬이 끊겨 최신 창이 FY2027 에 닿지 못한다
        e0, _m0, _ = fa.eps_adj_ttm_from(bad)
        w0 = getattr(fa.eps_adj_ttm_from, "last_window", None) or []
        self.assertFalse(e0 is not None and w0 and w0[0][4] == "2026-07-31")
        fixed, changes = fa.relabel_by_dates(bad)
        self.assertEqual(changes, ["2025Q1→2026Q1(종료 2025-04-30)"])
        eps, method, _ = fa.eps_adj_ttm_from(fixed)
        win = fa.eps_adj_ttm_from.last_window
        self.assertEqual([(y, q) for y, q, *_ in win], [(2027, 2), (2027, 1), (2026, 4), (2026, 3)])
        self.assertAlmostEqual(eps, 4.0, places=4)
        self.assertEqual(win[0][4], "2026-07-31")

    def test_consistent_sources_untouched(self):
        for src in (calendar_like(), crwd_like(mislabel=False)):
            fixed, changes = fa.relabel_by_dates(src)
            self.assertEqual(changes, [])
            self.assertIs(fixed, src)                          # 정상 회사는 객체째 그대로

    def test_no_annual_no_change(self):
        q = fa._attach_annual([rep(2025, 1, "2025-02-01", "2025-04-30", 1.0)], [])
        self.assertEqual(fa.relabel_by_dates(q), (q, []))

    def test_wired_into_build_block(self):
        """배선 — build_block 이 교정한 라벨로 창을 만들고, 교정 사실을 원장에 남긴다."""
        bad = crwd_like()
        fa.LABEL_FIXED.clear()
        fa.STALE_DISCARDED.clear()
        with mock.patch.object(fa, "fetch_reports",
                               side_effect=lambda tk, key, kind="quarterly", query=None:
                               (list(bad.annual), "ok", 200) if kind == "annual" else (list(bad), "ok", 200)), \
                mock.patch.object(fa, "_record"), \
                mock.patch.object(fa, "fetch_forward_growth", return_value=(None, "없음")):
            blk = fa.build_block({"ticker": "CRWD", "type": "추정불가"}, "k",
                                 fa.datetime(2026, 11, 3))[0]
        self.assertEqual(blk["period"], "2027Q2")
        self.assertAlmostEqual(blk["eps_adj_ttm"]["value"], 4.0, places=3)
        self.assertEqual([d["ticker"] for d in fa.LABEL_FIXED], ["CRWD"])
        self.assertEqual(blk.get("g_forward_status"), "없음")         # 왜 없는가를 남긴다
        fa.LABEL_FIXED.clear()


class ForwardGrowthTest(unittest.TestCase):
    def test_two_year_cagr_from_actual(self):
        g = fa.growth_from_estimate_table({"0y": {"avg": 5.5, "yearAgoEps": 5.0}, "+1y": {"avg": 6.05}})
        self.assertAlmostEqual(g, (6.05 / 5.0) ** 0.5 - 1, places=6)

    def test_one_year_when_no_actual(self):
        g = fa.growth_from_estimate_table({"0y": {"avg": 2.0, "yearAgoEps": float("nan")}, "+1y": {"avg": 2.4}})
        self.assertAlmostEqual(g, 0.2, places=6)

    def test_losses_and_nan_are_not_values(self):
        self.assertIsNone(fa.growth_from_estimate_table({"0y": {"avg": -1.0, "yearAgoEps": -2.0}, "+1y": {"avg": 0.5}}))
        self.assertIsNone(fa.growth_from_estimate_table({"0y": {"avg": 2.0}, "+1y": {"avg": float("nan")}}))
        self.assertIsNone(fa.growth_from_estimate_table({}))

    def test_vet_drops_both_tails(self):
        self.assertEqual(fa.vet_forward_growth(0.14, "s")[:2], (0.14, "s"))
        self.assertIsNone(fa.vet_forward_growth(0.83, "s")[0])          # NVDA 꼴 반등률
        self.assertIsNone(fa.vet_forward_growth(-0.24, "s")[0])         # PANW 꼴 기준 섞임
        self.assertIn("-24%", fa.vet_forward_growth(-0.24, "s")[2])

    def test_falls_back_when_ltg_is_nan(self):
        """2026-09-02 실측: 장기 성장률 행이 NaN — 연간 추정으로 넘어간다(Finnhub 키 없이도)."""
        import pandas as pd

        class T:
            growth_estimates = pd.DataFrame({"stockTrend": [float("nan")]}, index=["+5y"])
            earnings_estimate = pd.DataFrame({"avg": [5.5, 6.05], "yearAgoEps": [5.0, 5.5]}, index=["0y", "+1y"])
        with mock.patch("yfinance.Ticker", return_value=T()):
            g, src = fa.fetch_forward_growth("ZZZ", "")
        self.assertEqual(src, "yfinance 연간 EPS 추정 CAGR")
        self.assertAlmostEqual(g, (6.05 / 5.0) ** 0.5 - 1, places=6)

    def test_reason_kept_when_all_sources_fail(self):
        class T:
            growth_estimates = None
            earnings_estimate = None
        with mock.patch("yfinance.Ticker", return_value=T()):
            g, why = fa.fetch_forward_growth("ZZZ", "")
        self.assertIsNone(g)
        self.assertIn("yfinance 장기 없음", why)
        self.assertIn("Finnhub 키 없음", why)


class ContractTest(unittest.TestCase):
    """외부 라이브러리 계약(⑰) — 설치된 실물이 우리가 읽는 속성을 갖고 있는가 · 버전 고정."""

    def test_yfinance_contract_and_pin(self):
        import yfinance
        for attr in ("growth_estimates", "earnings_estimate"):
            self.assertTrue(hasattr(yfinance.Ticker, attr), attr)
        req = (Path(__file__).parent.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(req, r"(?m)^yfinance==")


class CoverageTest(unittest.TestCase):
    def test_auto_coverage_counts_axes(self):
        items = {"A": {"g_forward": 0.1, "g_cagr3y": None, "eps_adj_ttm": {"value": 1}},
                 "B": {"g_forward": None, "g_cagr3y": 0.2, "eps_adj_ttm": None},
                 "BRK": {"conversion_status": {"state": "float_skip"}, "g_forward": None}}
        self.assertEqual(fa.auto_coverage(items), {"g_forward": [1, 2], "g_cagr3y": [1, 2], "eps": [1, 2]})

    def test_payload_carries_coverage(self):
        src = Path(fa.__file__).read_text(encoding="utf-8")
        self.assertIn('"coverage": auto_coverage(items)', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
