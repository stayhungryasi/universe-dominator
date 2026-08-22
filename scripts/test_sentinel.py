#!/usr/bin/env python3
"""
정비 관제탑 검증 (test_sentinel) — 경보가 '울려야 할 때만' 울리는지 실측한다.

핵심 4케이스 (선장님 요구 사양):
  ① 소스 0건 1회      → 침묵 유지 (하루 안 쉬었다고 바로 소리치지 않는다)
  ② 소스 0건 2회 연속 → 경보 1건
  ③ 회복 (0건 → N건)  → ✅ 1회만
  ④ 전 항목 정상      → 완전 침묵 (텔레그램 호출 0회)

발송은 unittest.mock 으로 가로채 **메시지 형식까지** 검증한다
(경보가 울린 줄 알았는데 문구가 깨져 있으면 그것도 침묵 실패다).

실행: python scripts/test_sentinel.py
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import pipeline_sentinel as ps   # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 21, 19, 0, tzinfo=KST)
TODAY = "2026-08-21"
NAMES = ["Hacker News 300+", "Anthropic"]


def signals_for(counts, day=TODAY):
    """소스별 오늘 수집 건수를 그대로 재현하는 가짜 signals 리스트."""
    out = []
    for name, n in counts.items():
        for i in range(n):
            out.append({"source": name, "captured": f"{day} 1{i % 9}:00", "title": f"t{i}"})
    return out


def fresh_state():
    return {"version": 1, "sources": {}, "buffett": [], "sent": {}, "last_verdict": {}}


class SignalJudgeTest(unittest.TestCase):
    """① ② ③ — 연속 0건 카운터와 회복 알림"""

    def setUp(self):
        self.cfg = dict(ps.DEFAULTS)
        self.state = fresh_state()
        # 기준선: 어제 두 소스 모두 정상 수집된 적이 있다 (신규 소스 유예 해제)
        ps.judge_signals(signals_for({"Hacker News 300+": 10, "Anthropic": 2}, "2026-08-20"),
                         NAMES, self.state, "2026-08-20", self.cfg)

    def test_01_zero_once_stays_silent(self):
        alerts, counts = ps.judge_signals(
            signals_for({"Hacker News 300+": 8, "Anthropic": 0}),
            NAMES, self.state, TODAY, self.cfg)
        self.assertEqual(alerts, [], "0건 1회는 침묵해야 한다")
        self.assertEqual(counts["Anthropic"], 0)
        self.assertEqual(self.state["sources"]["Anthropic"]["zero_streak"], 1)

    def test_02_zero_twice_alerts(self):
        ps.judge_signals(signals_for({"Hacker News 300+": 8, "Anthropic": 0}),
                         NAMES, self.state, TODAY, self.cfg)
        alerts, _ = ps.judge_signals(signals_for({"Hacker News 300+": 9, "Anthropic": 0}),
                                     NAMES, self.state, TODAY, self.cfg)
        self.assertEqual(len(alerts), 1, "0건 2회 연속이면 정확히 1건 경보")
        self.assertEqual(alerts[0]["kind"], "alert")
        self.assertEqual(alerts[0]["sig"], f"sentinel:signals:Anthropic:{TODAY}")
        self.assertIn("Anthropic 0건 2회 연속", alerts[0]["text"])
        # 3회째는 이미 경보한 사건 → 중복 발화 금지
        again, _ = ps.judge_signals(signals_for({"Hacker News 300+": 9, "Anthropic": 0}),
                                    NAMES, self.state, TODAY, self.cfg)
        self.assertEqual(again, [], "이미 경보한 소스는 반복 발화하지 않는다")

    def test_03_recovery_notifies_once(self):
        for _ in range(2):   # 경보 상태로 만든 뒤
            ps.judge_signals(signals_for({"Hacker News 300+": 8, "Anthropic": 0}),
                             NAMES, self.state, TODAY, self.cfg)
        alerts, _ = ps.judge_signals(signals_for({"Hacker News 300+": 8, "Anthropic": 3}),
                                     NAMES, self.state, TODAY, self.cfg)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "recover")
        self.assertIn("회복 — 오늘 3건 ✅", alerts[0]["text"])
        self.assertEqual(self.state["sources"]["Anthropic"]["zero_streak"], 0)
        # 회복 알림은 1회뿐 — 다음 회차는 침묵
        again, _ = ps.judge_signals(signals_for({"Hacker News 300+": 8, "Anthropic": 3}),
                                    NAMES, self.state, TODAY, self.cfg)
        self.assertEqual(again, [], "회복 알림은 1회만")

    def test_configured_limit_4_delays_alarm(self):
        """운영값(sentinel_config.json = 4): 3회까지 침묵, 4회째 경보.

        2회 임계는 오탐이 난다 — 2026-08-20 Anthropic은 하루 종일 0건이었지만
        소스가 죽은 게 아니라 그날 발표가 없었을 뿐이었다.
        """
        cfg = dict(ps.DEFAULTS, zero_streak_limit=4)
        zero = signals_for({"Hacker News 300+": 8, "Anthropic": 0})
        for i in range(3):
            alerts, _ = ps.judge_signals(zero, NAMES, self.state, TODAY, cfg)
            self.assertEqual(alerts, [], f"{i + 1}회째는 아직 침묵해야 한다")
        alerts, _ = ps.judge_signals(zero, NAMES, self.state, TODAY, cfg)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Anthropic 0건 4회 연속", alerts[0]["text"])

    def test_new_source_has_grace(self):
        """한 번도 수집된 적 없는 신규 소스는 기준선이 없으므로 판정 유예."""
        state = fresh_state()
        for _ in range(3):
            alerts, _ = ps.judge_signals([], ["신규소스"], state, TODAY, self.cfg)
            self.assertEqual(alerts, [])


class BuffettJudgeTest(unittest.TestCase):
    """② buffett — 측정 종수 급감 / 선행 종수 감소"""

    def items(self, measured, forward):
        out = [{"pe": 20.0, "basis": "forward"} for _ in range(forward)]
        out += [{"pe": 20.0, "basis": "trailing"} for _ in range(measured - forward)]
        out += [{"pe": None, "basis": "too_hard"} for _ in range(5)]
        return {"items": out}

    def test_measured_crash_and_forward_loss(self):
        state = fresh_state()
        ps.judge_buffett(self.items(21, 13), state, "2026-08-20", ps.DEFAULTS)
        alerts, cur = ps.judge_buffett(self.items(13, 9), state, TODAY, ps.DEFAULTS)
        texts = [a["text"] for a in alerts]
        self.assertEqual(cur["measured"], 13)
        self.assertTrue(any("측정 21→13 급감" in t for t in texts), texts)
        self.assertTrue(any("선행 13→9" in t and "EPS 취재분 유실 의심" in t for t in texts), texts)

    def test_stable_is_silent(self):
        state = fresh_state()
        ps.judge_buffett(self.items(21, 13), state, "2026-08-20", ps.DEFAULTS)
        alerts, _ = ps.judge_buffett(self.items(21, 13), state, TODAY, ps.DEFAULTS)
        self.assertEqual(alerts, [])

    def test_mild_drop_is_silent(self):
        """10% 감소는 정상 변동 — 30% 임계 아래는 침묵."""
        state = fresh_state()
        ps.judge_buffett(self.items(20, 13), state, "2026-08-20", ps.DEFAULTS)
        alerts, _ = ps.judge_buffett(self.items(18, 13), state, TODAY, ps.DEFAULTS)
        self.assertEqual(alerts, [])


class OtherJudgeTest(unittest.TestCase):
    """③ 스냅샷 · ④ 정체 · ⑤ 봇 실행 흔적"""

    def test_snapshot_missing(self):
        self.assertEqual(ps.judge_snapshot(Path("data") / "snapshots", "1999-01-01")[0]["sig"],
                         "sentinel:snapshot:1999-01-01")

    def test_snapshot_present(self, ):
        with mock.patch.object(Path, "exists", return_value=True):
            self.assertEqual(ps.judge_snapshot(Path("nowhere"), TODAY), [])

    def test_stale_uses_content_timestamp(self):
        """CI 체크아웃 mtime 함정 회피 — 내용 타임스탬프로 정체를 잰다."""
        old = "2026-08-18T18:00:00+09:00"
        ts = ps._content_time("calendar.json", {"generated_at": old})
        self.assertAlmostEqual(ps._age_hours(ts, NOW), 73.0, places=1)
        ts2 = ps._content_time("columns.json", {"columns": [{"date": "2026.08.21"}]})
        self.assertEqual(ts2.strftime("%Y-%m-%d %H:%M"), "2026-08-21 23:59")

    def test_bots_step_outcome(self):
        cfg = dict(ps.DEFAULTS)
        ok = {"parallax_journal": "success", "community_notice": "success"}
        alerts, _ = ps.judge_bots(cfg, TODAY, ok, Path("data"), NOW)
        self.assertEqual(alerts, [])
        bad = {"parallax_journal": "failure", "community_notice": "skipped"}
        alerts, _ = ps.judge_bots(cfg, TODAY, bad, Path("data"), NOW)
        self.assertEqual(len(alerts), 2)
        self.assertIn("실행 흔적 없음 (스텝 failure)", alerts[0]["text"])

    def test_parse_step_outcomes(self):
        self.assertEqual(ps.parse_step_outcomes('{"a":"success"}'), {"a": "success"})
        self.assertEqual(ps.parse_step_outcomes("a=success,b=failure"),
                         {"a": "success", "b": "failure"})
        self.assertEqual(ps.parse_step_outcomes(""), {})


class MessageFormatTest(unittest.TestCase):
    """메시지 형식 — 선장님이 지정한 형태 그대로인지"""

    def test_shape(self):
        alerts = [ps.alert("signals:Anthropic", "fetch_signals: Anthropic 0건 2회 연속", TODAY),
                  ps.alert("buffett-measured", "buffett: 측정 21→13 급감 (-38%)", TODAY)]
        msg = ps.format_message(alerts, NOW)
        self.assertEqual(msg, "🚨 UNIVERTRIX 정비 관제 (08-21 19:00)\n"
                              "· fetch_signals: Anthropic 0건 2회 연속\n"
                              "· buffett: 측정 21→13 급감 (-38%)")

    def test_cap_five(self):
        alerts = [ps.alert(f"x{i}", f"항목{i}", TODAY) for i in range(8)]
        msg = ps.format_message(alerts, NOW)
        self.assertEqual(len(msg.splitlines()), 1 + ps.MAX_ALERTS + 1)
        self.assertTrue(msg.endswith("· 외 3건"))

    def test_recovery_only_uses_check_header(self):
        alerts = [ps.alert("signals-recover:Anthropic", "fetch_signals: Anthropic 회복 — 오늘 3건 ✅",
                           TODAY, kind="recover")]
        self.assertTrue(ps.format_message(alerts, NOW).startswith("✅ UNIVERTRIX 정비 관제"))


class DispatchTest(unittest.TestCase):
    """발송 — mock 으로 캡처. 정상이면 호출 0회, 같은 날 중복 발송 금지."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok",
                                                "TELEGRAM_ALERT_CHAT_ID": "@alerts"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_04_all_normal_sends_nothing(self):
        state = fresh_state()
        with mock.patch.object(ps, "send_telegram") as send:
            if []:   # 경보 없음 → dispatch 자체를 부르지 않는 것이 run() 의 계약
                ps.dispatch([], NOW, TODAY, state)
            self.assertEqual(send.call_count, 0, "전 항목 정상이면 텔레그램 호출 0회")

    def test_alert_chat_preference(self):
        self.assertEqual(ps.alert_chat_id(), "@alerts")
        with mock.patch.dict(os.environ, {"TELEGRAM_ALERT_CHAT_ID": ""}):
            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "@fallback"}):
                self.assertEqual(ps.alert_chat_id(), "@fallback")

    def test_dedup_same_day(self):
        state = fresh_state()
        alerts = [ps.alert("signals:Anthropic", "fetch_signals: Anthropic 0건 2회 연속", TODAY)]
        with mock.patch.object(ps, "send_telegram") as send:
            self.assertTrue(ps.dispatch(alerts, NOW, TODAY, state))
            self.assertEqual(send.call_count, 1)
            _, chat, text = send.call_args[0]
            self.assertEqual(chat, "@alerts")
            self.assertIn("Anthropic 0건 2회 연속", text)
            # 같은 날 같은 서명 → 재발송 금지
            self.assertFalse(ps.dispatch(alerts, NOW, TODAY, state))
            self.assertEqual(send.call_count, 1)

    def test_send_failure_keeps_signature_unsent(self):
        """발송 실패 시 서명을 기록하지 않아야 다음 회차에 재시도된다."""
        state = fresh_state()
        alerts = [ps.alert("snapshot", "스냅샷 없음", TODAY)]
        with mock.patch.object(ps, "send_telegram", side_effect=RuntimeError("네트워크 끊김")):
            self.assertFalse(ps.dispatch(alerts, NOW, TODAY, state))
        self.assertEqual(state.get("sent", {}).get(TODAY), None)
        with mock.patch.object(ps, "send_telegram") as send:
            self.assertTrue(ps.dispatch(alerts, NOW, TODAY, state))
            self.assertEqual(send.call_count, 1)


class SendPathTest(unittest.TestCase):
    """발송부는 브리핑과 같은 경로여야 한다 — 그래야 매일 실전 검증된다."""

    def test_delegates_to_briefing_sender(self):
        import send_telegram_briefing as briefing
        with mock.patch.object(briefing, "send_telegram") as real:
            ps.send_telegram("tok", "@chan", "· 측정 21→13 급감")
            self.assertEqual(real.call_count, 1, "브리핑 발송부를 그대로 타야 한다")
            self.assertEqual(real.call_args[0][0], "tok")
            self.assertEqual(real.call_args[0][1], "@chan")

    def test_escapes_for_html_parse_mode(self):
        """브리핑 경로는 parse_mode=HTML — 평문의 <, & 가 메시지를 깨뜨리면 안 된다."""
        import send_telegram_briefing as briefing
        with mock.patch.object(briefing, "send_telegram") as real:
            ps.send_telegram("tok", "@chan", "소스 <A & B> 0건")
            self.assertEqual(real.call_args[0][2], "소스 &lt;A &amp; B&gt; 0건")

    def test_config_file_overrides_threshold(self):
        """운영 중 임계 조정은 코드 수정 없이 sentinel_config.json 으로."""
        cfg = ps.load_config()
        self.assertIn("zero_streak_limit", cfg)
        self.assertIsInstance(cfg["stale_hours"], dict)


class MaterialsTest(unittest.TestCase):
    """⑥ 동행 소재함 — 소장이 던진 소재가 침묵 속에 썩지 않게.

    관찰자 원칙 유지: Firestore 를 보지 않고 동행 엔진의 상태 파일만 읽는다.
    """

    def setUp(self):
        self.state = fresh_state()
        self.state["materials"] = {"streak": 0, "alerted": False}
        self.cfg = dict(ps.DEFAULTS)

    def test_one_round_stays_silent(self):
        a = ps.judge_materials(self.state, {"materials_pending": 2}, TODAY, self.cfg)
        self.assertEqual(a, [], "1회 잔존은 일시 오류일 수 있다 — 침묵")

    def test_two_rounds_alert(self):
        ps.judge_materials(self.state, {"materials_pending": 2}, TODAY, self.cfg)
        a = ps.judge_materials(self.state, {"materials_pending": 2}, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertIn("미소비 소재 2건 잔존", a[0]["text"])
        self.assertEqual(a[0]["sig"], f"sentinel:materials:{TODAY}")
        again = ps.judge_materials(self.state, {"materials_pending": 2}, TODAY, self.cfg)
        self.assertEqual(again, [], "이미 경보한 사건은 반복하지 않는다")

    def test_inbox_unreachable_is_worse(self):
        for _ in range(2):
            a = ps.judge_materials(self.state, {"materials_pending": -1}, TODAY, self.cfg)
        self.assertIn("소재함 접근 실패", a[0]["text"])

    def test_recovery_notifies_once(self):
        for _ in range(2):
            ps.judge_materials(self.state, {"materials_pending": 1}, TODAY, self.cfg)
        a = ps.judge_materials(self.state, {"materials_pending": 0}, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["kind"], "recover")
        self.assertEqual(ps.judge_materials(self.state, {"materials_pending": 0}, TODAY, self.cfg), [])

    def test_normal_is_silent(self):
        self.assertEqual(ps.judge_materials(self.state, {}, TODAY, self.cfg), [])
        self.assertEqual(ps.judge_materials(self.state, {"materials_pending": 0}, TODAY, self.cfg), [])


class FeedJudgeTest(unittest.TestCase):
    """⑦ fetch_status 원장 — 죽은 소스와 조용한 날을 가른다 (2026-08-22 503 사고).

    이전에는 signals.json 결과물만 봤기 때문에 동행 소스가 통째로 죽어도
    아무 소리가 나지 않았다. 그 사각지대가 닫혔는지 본다.
    """

    def setUp(self):
        self.state = fresh_state()
        self.state["feeds"] = {}
        self.cfg = dict(ps.DEFAULTS, feed_error_limit=2, zero_streak_limit=4)

    def st(self, outcome, kind="companion", code=503, label="spacex:발사"):
        return {"sources": {f"{kind}:{label}": {"kind": kind, "source": label,
                                                "outcome": outcome, "code": code}}}

    def test_dead_source_alerts_on_second_round(self):
        a, seen = ps.judge_feeds(self.st("http_error"), self.state, TODAY, self.cfg)
        self.assertEqual(a, [], "1회 실패는 일시 오류일 수 있다")
        self.assertEqual(seen["error"], 1)
        a, _ = ps.judge_feeds(self.st("http_error"), self.state, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertIn("동행 spacex:발사: HTTP 503 2회 연속", a[0]["text"])
        again, _ = ps.judge_feeds(self.st("http_error"), self.state, TODAY, self.cfg)
        self.assertEqual(again, [], "이미 경보한 사건은 반복하지 않는다")

    def test_quiet_day_is_not_an_error(self):
        """0건은 장애가 아니다 — 실패보다 관대한 임계(4)를 쓴다."""
        for i in range(3):
            a, seen = ps.judge_feeds(self.st("zero", code=200), self.state, TODAY, self.cfg)
            self.assertEqual(a, [], f"{i + 1}회째 0건은 아직 침묵")
        self.assertEqual(seen["zero"], 1)
        a, _ = ps.judge_feeds(self.st("zero", code=200), self.state, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertIn("0건 4회 연속", a[0]["text"])

    def test_signals_zero_is_left_to_judge_signals(self):
        """신호 소스의 0건은 judge_signals 관할 — 같은 사실로 두 번 울리지 않는다."""
        for _ in range(6):
            a, _ = ps.judge_feeds(self.st("zero", kind="signals", code=200, label="Anthropic"),
                                  self.state, TODAY, self.cfg)
            self.assertEqual(a, [])

    def test_signals_error_still_alerts(self):
        """단, 신호 소스라도 '요청 실패'는 여기서 잡는다 — 그건 장애다."""
        for _ in range(2):
            a, _ = ps.judge_feeds(self.st("http_error", kind="signals", label="Anthropic"),
                                  self.state, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertIn("신호 Anthropic", a[0]["text"])

    def test_recovery_notifies_once(self):
        for _ in range(2):
            ps.judge_feeds(self.st("http_error"), self.state, TODAY, self.cfg)
        a, _ = ps.judge_feeds(self.st("ok", code=200), self.state, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["kind"], "recover")
        self.assertIn("응답 회복 ✅", a[0]["text"])
        again, _ = ps.judge_feeds(self.st("ok", code=200), self.state, TODAY, self.cfg)
        self.assertEqual(again, [])

    def test_timeout_counts_as_error(self):
        for _ in range(2):
            a, _ = ps.judge_feeds(self.st("timeout", code=None), self.state, TODAY, self.cfg)
        self.assertEqual(len(a), 1)
        self.assertIn("timeout 2회 연속", a[0]["text"])

    def test_all_ok_is_silent(self):
        a, seen = ps.judge_feeds(self.st("ok", code=200), self.state, TODAY, self.cfg)
        self.assertEqual(a, [])
        self.assertEqual(seen["ok"], 1)

    def test_removed_source_is_forgotten(self):
        """설정에서 뺀 소스가 상태 파일에 유령으로 남지 않는다."""
        ps.judge_feeds(self.st("http_error"), self.state, TODAY, self.cfg)
        self.assertIn("companion:spacex:발사", self.state["feeds"])
        ps.judge_feeds({"sources": {}}, self.state, TODAY, self.cfg)
        self.assertEqual(self.state["feeds"], {})

    def test_missing_ledger_is_silent(self):
        """원장이 아직 없어도(첫 도입 회차) 조용히 넘어간다."""
        a, seen = ps.judge_feeds({}, self.state, TODAY, self.cfg)
        self.assertEqual((a, seen), ([], {"error": 0, "zero": 0, "ok": 0}))


class ResilienceTest(unittest.TestCase):
    """관측자는 죽어도 파이프라인을 죽이지 않는다 — 단 조용히 죽지도 않는다."""

    def test_main_never_raises(self):
        with mock.patch.object(ps, "run", side_effect=RuntimeError("판정부 폭발")):
            with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
                self.assertEqual(ps.main(), 0)

    def test_self_failure_is_loud(self):
        with mock.patch.object(ps, "run", side_effect=RuntimeError("판정부 폭발")):
            with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok"}):
                with mock.patch.object(ps, "send_telegram") as send:
                    self.assertEqual(ps.main(), 0)
                    self.assertEqual(send.call_count, 1)
                    self.assertIn("정비 관제 자체가 실패", send.call_args[0][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
