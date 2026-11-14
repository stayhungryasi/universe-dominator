#!/usr/bin/env python3
"""
API 비용 계량기·일일 보고 테스트 (2026-11-14 ㉗)
① 사용량 × 단가(캐시·웹 검색 포함) ② Claude Messages 호출만 센다 · 계량기 오류가 호출을 망치지 않는다
③ CI 밖에서는 파일을 쓰지 않는다 ④ 단가표에 없는 모델은 '미등록'(틀린 숫자 대신 빈칸)
⑤ 보고는 하루 1회 · 08시 전엔 안 보냄 · DM 실패면 날짜를 찍지 않음 ⑥ 경보(예산 초과 전망·급증·실패율)
⑦ 배선 — API 를 부르는 스크립트는 전부 계량기를 단다 · 보고 스텝은 소장 DM 전용
실행: python scripts/test_api_meter.py
"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import api_cost_report as rp   # noqa: E402
import api_meter as am         # noqa: E402

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 11, 20, 9, 30, tzinfo=am.KST)
URL = "https://api.anthropic.com/v1/messages"


class Resp:
    def __init__(self, status=200, body=None, broken=False):
        self.status_code, self._body, self._broken = status, body, broken

    def json(self):
        if self._broken:
            raise ValueError("not json")
        return self._body


def body(model="claude-haiku-4-5", **usage):
    return {"model": model, "usage": usage, "content": []}


class MeterBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "api_cost.json"      # 진짜 data/api_cost.json 은 절대 건드리지 않는다(㉔)
        self.env = mock.patch.dict(os.environ, {"API_METER": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def days(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["days"]


class PriceTest(unittest.TestCase):
    def test_tokens_times_price(self):
        usd, tok = am.cost_usd("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000})
        self.assertAlmostEqual(usd, 2.0 + 1.0)
        usd, _ = am.cost_usd("claude-haiku-4-5-20251001", {"input_tokens": 200_000, "output_tokens": 100_000,
                                                           "cache_creation_input_tokens": 400_000,
                                                           "cache_read_input_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 0.2 + 0.5 + 0.5 + 0.1)       # 날짜 꼬리가 붙은 id 도 앞머리로 맞춘다
        usd, tok = am.cost_usd("claude-sonnet-5-5", {"input_tokens": 0, "output_tokens": 0,
                                                     "server_tool_use": {"web_search_requests": 3}})
        self.assertAlmostEqual(usd, 0.03)
        self.assertEqual(tok["ws"], 3)

    def test_longest_name_wins_and_unknown_is_none(self):
        self.assertEqual(am.price_of("claude-sonnet-5-5")[3], 0.10)   # 'claude-sonnet-5' 가 먼저 맞으면 0.20 이 된다
        self.assertEqual(am.price_of("claude-sonnet-5")[3], 0.20)
        usd, tok = am.cost_usd("claude-future-9", {"input_tokens": 10, "output_tokens": 5})
        self.assertIsNone(usd)                                         # 모르는 모델에 값을 지어내지 않는다
        self.assertEqual((tok["in"], tok["out"]), (10, 5))


class ObserveTest(MeterBase):
    def test_counts_only_claude_messages(self):
        with mock.patch.object(am, "script_name", return_value="translate_content"):
            am.observe(URL, Resp(200, body(input_tokens=1000, output_tokens=500)), path=self.path, now=NOW)
            am.observe(URL, Resp(200, body("claude-sonnet-5-5", input_tokens=2000, output_tokens=100)),
                       path=self.path, now=NOW)
            self.assertIsNone(am.observe("https://api.telegram.org/botX/sendMessage", Resp(200, {}),
                                         path=self.path, now=NOW))
            self.assertIsNone(am.observe(URL + "/batches", Resp(200, {}), path=self.path, now=NOW))
        row = self.days()["2026-11-20"]["translate_content"]
        self.assertEqual((row["calls"], row["fail"], row["in"], row["out"]), (2, 0, 3000, 600))
        self.assertAlmostEqual(row["usd"], (1000 * 1 + 500 * 5 + 2000 * 2 + 100 * 10) / 1e6)
        self.assertEqual(set(row["models"]), {"claude-haiku-4-5", "claude-sonnet-5-5"})

    def test_failures_and_unpriced_are_counted_not_priced(self):
        with mock.patch.object(am, "script_name", return_value="spacex_news"):
            am.observe(URL, Resp(529, {}), path=self.path, now=NOW)
            am.observe(URL, Resp(200, {"type": "error"}), path=self.path, now=NOW)
            am.observe(URL, Resp(200, body("claude-future-9", input_tokens=10, output_tokens=5)),
                       path=self.path, now=NOW)
        row = self.days()["2026-11-20"]["spacex_news"]
        self.assertEqual((row["calls"], row["fail"], row["unpriced"], row["usd"]), (3, 2, 1, 0.0))

    def test_disabled_outside_ci(self):
        with mock.patch.dict(os.environ, {"API_METER": "", "GITHUB_ACTIONS": ""}):
            self.assertIsNone(am.observe(URL, Resp(200, body(input_tokens=1, output_tokens=1)), path=self.path, now=NOW))
        with mock.patch.dict(os.environ, {"API_METER": "0", "GITHUB_ACTIONS": "true"}):
            self.assertIsNone(am.observe(URL, Resp(200, body(input_tokens=1, output_tokens=1)), path=self.path, now=NOW))
        self.assertFalse(self.path.exists())

    def test_old_days_pruned(self):
        self.path.write_text(json.dumps({"schema": 1, "days": {"2026-09-01": {"x": {"usd": 1.0}},
                                                               "2026-11-19": {"x": {"usd": 2.0}}}}), encoding="utf-8")
        am.record("x", "claude-haiku-4-5", {"input_tokens": 1, "output_tokens": 1}, path=self.path, now=NOW)
        self.assertEqual(sorted(self.days()), ["2026-11-19", "2026-11-20"])


class InstallTest(unittest.TestCase):
    """관찰자는 호출을 바꾸지 않는다 — 응답은 그대로, 예외는 그대로, 계량기 자신의 오류는 삼킨다."""

    def _session_cls(self, orig):
        return type("S", (), {"request": orig})

    def _install(self, orig):
        fake = type("R", (), {})()
        fake.sessions = type("M", (), {})()
        fake.sessions.Session = self._session_cls(orig)
        with mock.patch.dict(sys.modules, {"requests": fake}):
            self.assertTrue(am.install())
            self.assertTrue(am.install())                              # 두 번 불러도 한 겹
        return fake.sessions.Session

    def test_response_passes_through_and_meter_errors_are_swallowed(self):
        want = Resp(200, broken=True)                                  # json() 이 터지는 응답 — 계량기만 실패해야 한다
        seen = []
        cls = self._install(lambda self, method, url, *a, **k: seen.append((method, url, k)) or want)
        with mock.patch.dict(os.environ, {"API_METER": "1"}), \
                mock.patch.object(am, "record", side_effect=AssertionError("기록되면 안 된다")):
            got = cls().request("POST", URL, json={"x": 1}, timeout=30)
        self.assertIs(got, want)
        self.assertEqual(seen, [("POST", URL, {"json": {"x": 1}, "timeout": 30})])
        self.assertEqual(getattr(cls.request, "_api_meter", False), True)

    def test_call_errors_are_not_hidden(self):
        def boom(self, method, url, *a, **k):
            raise TimeoutError("read timed out")
        cls = self._install(boom)
        with self.assertRaises(TimeoutError):
            cls().request("POST", URL)


class RealRequestsTest(MeterBase):
    """진짜 requests 로 한 바퀴 — 스크립트가 쓰는 requests.post 가 관찰자를 지나 원장에 닿는다(네트워크 없음)."""

    def test_requests_post_is_metered_end_to_end(self):
        import requests
        am.install()

        def fake_send(adapter, request, **kw):
            r = requests.Response()
            r.status_code, r.url, r.request = 200, request.url, request
            r._content = json.dumps(body("claude-sonnet-5-5", input_tokens=4000, output_tokens=1000)).encode()
            return r

        with mock.patch.object(requests.adapters.HTTPAdapter, "send", fake_send), \
                mock.patch.object(am, "LEDGER", self.path), \
                mock.patch.object(am, "now_kst", return_value=NOW), \
                mock.patch.object(am, "script_name", return_value="companion_essays"):
            r = requests.post(URL, headers={"x-api-key": "k"}, json={"model": "m"}, timeout=5)
            requests.post("https://api.telegram.org/botX/sendMessage", json={}, timeout=5)
        self.assertEqual(r.json()["model"], "claude-sonnet-5-5")       # 호출자가 받는 응답은 그대로
        row = self.days()["2026-11-20"]["companion_essays"]
        self.assertEqual((row["calls"], row["in"], row["out"]), (1, 4000, 1000))
        self.assertAlmostEqual(row["usd"], 0.018)


class ReportTest(MeterBase):
    def _seed(self, days):
        self.path.write_text(json.dumps({"schema": 1, "days": days}), encoding="utf-8")

    def _row(self, usd, calls=10, fail=0, unpriced=0):
        return {"calls": calls, "fail": fail, "in": 1000, "out": 500, "cw": 0, "cr": 0, "ws": 0,
                "usd": usd, "unpriced": unpriced, "models": {}}

    def test_once_a_day_after_eight_and_failed_dm_is_not_stamped(self):
        self._seed({"2026-11-19": {"translate_content": self._row(2.5), "spacex_news": self._row(1.5)}})
        sent = []
        with mock.patch.object(rp, "send", side_effect=lambda t: sent.append(t) or True):
            self.assertEqual(rp.run(self.path, NOW.replace(hour=7)), 0)          # 08시 전
            self.assertEqual(sent, [])
            self.assertEqual(rp.run(self.path, NOW), 0)
            self.assertEqual(rp.run(self.path, NOW.replace(hour=18)), 0)         # 같은 날 둘째 회차
        self.assertEqual(len(sent), 1)
        self.assertIn("어제 $4.00", sent[0])
        self.assertIn("translate_content $2.50", sent[0])
        self.assertIn("범위:", sent[0])                                           # 무엇을 세지 않는지 밝힌다
        self._seed({"2026-11-19": {"x": self._row(1.0)}})
        with mock.patch.object(rp, "send", side_effect=RuntimeError("telegram down")):
            self.assertEqual(rp.run(self.path, NOW), 1)
        self.assertNotIn("report", json.loads(self.path.read_text(encoding="utf-8")))   # 다음 회차가 다시 보낸다
        with mock.patch.object(rp, "send", return_value=False):                  # 수신처 미등록 — 보낸 것이 아니다
            self.assertEqual(rp.run(self.path, NOW), 0)
        self.assertNotIn("report", json.loads(self.path.read_text(encoding="utf-8")))

    def test_alerts(self):
        days = {f"2026-11-{d:02d}": {"translate_content": self._row(1.0)} for d in range(10, 19)}
        days["2026-11-19"] = {"translate_content": self._row(9.0),               # 중앙값 1.0 의 9배
                              "spacex_news": self._row(0.5, calls=10, fail=4),   # 실패율 40%
                              "signal_column": self._row(0.0, unpriced=2)}
        al = rp.alerts(days, NOW, budget=20)
        self.assertEqual(len(al), 4, al)
        self.assertTrue(any("예산 $20 초과" in a for a in al))
        self.assertTrue(any(a.startswith("translate_content 어제 $9.00") and "9.0배" in a for a in al))
        self.assertTrue(any("실패 4/10" in a for a in al))
        self.assertTrue(any("단가표에 없는 모델 2건" in a for a in al))
        self.assertTrue(rp.build_message(days, NOW, 20).startswith("⚠️ "))
        calm = {f"2026-11-{d:02d}": {"translate_content": self._row(1.0)} for d in range(10, 20)}
        self.assertEqual(rp.alerts(calm, NOW, budget=200), [])                   # 조용한 날은 울지 않는다
        self.assertFalse(rp.build_message(calm, NOW, 200).startswith("⚠️"))

    def test_projection_uses_measured_days_only(self):
        days = {"2026-11-18": {"x": self._row(4.0)}, "2026-11-19": {"x": self._row(6.0)},
                "2026-11-20": {"x": self._row(99.0)}}                             # 오늘(진행 중)은 평균에서 뺀다
        self.assertEqual(rp.projection(days, NOW), 150.0)                         # 하루 $5 × 30일 — 계량 전의 빈 날로 나누지 않는다
        self.assertIsNone(rp.projection({}, NOW))


class WiringTest(unittest.TestCase):
    def test_every_api_script_carries_the_meter(self):
        missing = []
        for p in sorted((ROOT / "scripts").glob("*.py")):
            if p.name.startswith("test_") or p.name in ("api_meter.py", "selftest.py"):
                continue
            s = p.read_text(encoding="utf-8")
            if "api.anthropic.com/v1/messages" in s and not re.search(r"(?m)^import api_meter\b", s):
                missing.append(p.name)
        self.assertEqual(missing, [])          # 새로 API 를 부르는 스크립트는 머리에 'import api_meter' 한 줄

    def test_daily_reports_to_owner_dm_only(self):
        y = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        steps = [s for s in re.split(r"(?m)^(?=      - )", y) if "python scripts/api_cost_report.py" in s]
        self.assertEqual(len(steps), 1)
        s = steps[0]
        self.assertIn("continue-on-error: true", s)
        self.assertIn("TELEGRAM_ALERT_CHAT_ID", s)
        self.assertNotIn("TELEGRAM_CHAT_ID:", s)                       # 공개 채널 변수는 넘기지 않는다
        self.assertIn("API_BUDGET_USD", s)
        self.assertLess(y.rindex("ANTHROPIC_API_KEY"), y.index("api_cost_report.py"))   # 마지막 API 스텝 뒤
        self.assertLess(y.index("api_cost_report.py"), y.index("git add -A"))            # 커밋 앞 — 원장이 함께 실린다


if __name__ == "__main__":
    unittest.main(verbosity=2)
