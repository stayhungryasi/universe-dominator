#!/usr/bin/env python3
"""
사건층 복구 테스트 (2026-10-23, 알려진 지뢰 ⑰) — 동행 관측 32일 정지
================================================================================
세 겹이 겹쳐 32일을 침묵했다:
  ① 버전 미고정 — googlenewsdecoder 0.2.1 이 성공 키를 status → success 로 바꿨다
  ② 실패를 조용한 날로 분류 — 원본 피드는 있는데 디코딩이 전멸해도 'zero'
  ③ 한 번 울고 침묵 — zero_alerted/err_alerted 가 1회성이었다

각 겹마다 '수정 전 실패 → 수리 → 되돌리면 실패' 를 확인한 케이스를 둔다.
실행: python scripts/test_event_layer.py
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import fetch_signals as fs          # noqa: E402
import pipeline_sentinel as ps      # noqa: E402


GN = "https://news.google.com/rss/articles/CBMiABC"


def _rows(n, url=GN):
    return [{"title": f"t{i} - Pub", "url": f"{url}{i}", "pub": "", "points": None,
             "source": "s", "feed_source": "Pub", "raw_desc": ""} for i in range(n)]


class DecoderAdapterTest(unittest.TestCase):
    """① 겹 — 반환 형식이 바뀌어도 원문 URL 을 꺼낸다."""

    def test_new_format_success_key(self):
        self.assertEqual(fs.gnews_decoded({"success": True, "decoded_url": "https://a.com/x"}),
                         "https://a.com/x")

    def test_old_format_status_key(self):
        self.assertEqual(fs.gnews_decoded({"status": True, "decoded_url": "https://a.com/x"}),
                         "https://a.com/x")

    def test_failure_dicts(self):
        self.assertIsNone(fs.gnews_decoded({"success": False, "message": "x"}))
        self.assertIsNone(fs.gnews_decoded({"status": False, "message": "x"}))
        self.assertIsNone(fs.gnews_decoded({"success": True}))       # URL 없는 성공
        self.assertIsNone(fs.gnews_decoded(None))

    def test_list_return(self):
        self.assertEqual(fs.gnews_decoded([{"success": False}, {"success": True,
                                                                "decoded_url": "https://b.com"}]),
                         "https://b.com")
        self.assertIsNone(fs.gnews_decoded([]))

    def test_resolve_with_new_format_keeps_articles(self):
        """재현: 0.2.x 형식에서 옛 코드는 0건을 돌려줬다(32일 정지의 직접 원인)."""
        fake = mock.Mock(side_effect=lambda u, interval=None:
                         {"success": True, "decoded_url": "https://www.anthropic.com/news/" + u[-1]})
        with mock.patch.object(fs, "gnewsdecoder", fake):
            st = {}
            got = fs.resolve_gnews(_rows(3), "anthropic.com", cap=5, stats=st)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0]["url"], "https://www.anthropic.com/news/0")
        self.assertEqual(got[0]["title"], "t0")           # 꼬리 제거는 그대로
        self.assertEqual(st, {"scanned": 3, "decoded": 3, "decode_fail": 0})


class ContractTest(unittest.TestCase):
    """설치된 **실물** 패키지가 어댑터가 아는 형식을 돌려주는가 — 네트워크 없이.

    다음 형식 변경(예: success → ok)이 오면 수집이 0건으로 위장하기 전에 여기서 운다.
    """

    def test_installed_package_shape(self):
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder("https://example.com/not-a-google-news-url")
        self.assertIsInstance(res, dict)
        self.assertTrue({"success", "status"} & set(res),
                        f"반환 키가 바뀌었다: {sorted(res)}")
        self.assertIsNone(fs.gnews_decoded(res))          # 실패는 실패로 읽는다

    def test_requirements_pinned(self):
        req = (Path(__file__).parent.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("googlenewsdecoder==0.2.2", req)


class OutcomeClassifyTest(unittest.TestCase):
    """② 겹 — 원본이 있는데 디코딩 전멸이면 '조용한 날'이 아니다."""

    def test_raw_present_decode_dead_is_failure(self):
        self.assertEqual(fs.classify_outcome(12, [], {"scanned": 12, "decoded": 0}),
                         "decode_fail")

    def test_raw_empty_is_zero(self):
        self.assertEqual(fs.classify_outcome(0, [], {}), "zero")

    def test_decoded_but_filtered_is_zero(self):
        """디코딩은 됐는데 도메인 필터가 다 걸렀다 — 그건 정상적인 0건."""
        self.assertEqual(fs.classify_outcome(5, [], {"scanned": 5, "decoded": 5}), "zero")

    def test_hn_points_filter_is_zero(self):
        self.assertEqual(fs.classify_outcome(30, [], {}), "zero")

    def test_ok(self):
        self.assertEqual(fs.classify_outcome(3, [{"url": "x"}], {}), "ok")

    def _collect_with(self, decoder_result):
        import feed_client as fc
        xml = ("<rss><channel>" + "".join(
            f"<item><title>a{i}</title><link>{GN}{i}</link></item>" for i in range(4))
            + "</channel></rss>")
        cfg = {"sources": [{"name": "Anthropic", "type": "gnews", "domain": "",
                            "url": "https://news.google.com/rss/search?q=x"}], "rss_cap": 5}
        fc._buffer.clear()
        with mock.patch.object(fc, "fetch", return_value=(xml, "ok", 200)), \
                mock.patch.object(fc, "flush"), \
                mock.patch.object(fs, "gnewsdecoder",
                                  mock.Mock(return_value=decoder_result)):
            fs.collect(cfg)
        return dict(fc._buffer["signals:Anthropic"])

    def test_collect_records_decode_fail_with_raw(self):
        rec = self._collect_with({"success": False, "message": "boom"})
        self.assertEqual((rec["outcome"], rec["items"], rec["items_raw"]), ("decode_fail", 0, 4))

    def test_collect_records_ok(self):
        rec = self._collect_with({"success": True, "decoded_url": "https://x.com/a"})
        self.assertEqual((rec["outcome"], rec["items_raw"]), ("ok", 4))

    def test_companion_collect_records_decode_fail(self):
        import companion_essays as ce
        import feed_client as fc
        xml = ("<rss><channel>" + "".join(
            f"<item><title>a{i}</title><link>{GN}{i}</link></item>" for i in range(3))
            + "</channel></rss>")
        comp = {"slug": "spacex", "sources": [{"name": "발사", "type": "gnews", "domain": "",
                                               "url": "https://news.google.com/rss/search?q=x"}]}
        fc._buffer.clear()
        with mock.patch.object(fc, "fetch", return_value=(xml, "ok", 200)), \
                mock.patch.object(fc, "flush"), \
                mock.patch.object(fs, "gnewsdecoder",
                                  mock.Mock(return_value={"success": False})):
            ce.collect_candidates(comp, {})
        rec = fc._buffer["companion:spacex:발사"]
        self.assertEqual((rec["outcome"], rec["items_raw"]), ("decode_fail", 3))


def _status(outcome, kind="companion", label="spacex:발사", raw=12):
    key = f"{kind}:{label}"
    return {"sources": {key: {"kind": kind, "source": label, "outcome": outcome,
                              "code": 200, "items": 0 if outcome != "ok" else 3,
                              "items_raw": raw}}}


CFG = {"feed_error_limit": 2, "zero_streak_limit": 4}


def _run_days(outcomes, kind="companion", label="spacex:발사", names=()):
    """하루 1회차씩 판정을 돌려 경보 목록(일자별)을 모은다 — judge_signals+judge_feeds 합산."""
    state = {"sources": {}, "feeds": {}}
    day0 = datetime(2026, 9, 22)
    log = []
    for i, oc in enumerate(outcomes):
        today = (day0 + timedelta(days=i)).strftime("%Y-%m-%d")
        st = _status(oc, kind, label)
        a1, _ = ps.judge_signals(st, list(names), state, today, CFG)
        a2, _ = ps.judge_feeds(st, state, today, CFG)
        log.append((today, a1 + a2))
    return log, state


class DecodeFailAlertTest(unittest.TestCase):
    """② 겹 — decode_fail 은 오류 계열: 2회 연속에 **정확히 1건**."""

    def test_companion_two_runs_exactly_one(self):
        log, _ = _run_days(["decode_fail", "decode_fail"])
        alerts = [a for _, al in log for a in al]
        self.assertEqual(len(alerts), 1)
        self.assertIn("디코딩 실패(원본 12건 → 0건) 2회 연속", alerts[0]["text"])

    def test_signals_source_exactly_one(self):
        """신호 소스는 judge_signals·judge_feeds 둘 다 보지만 우는 곳은 한 곳뿐."""
        log, _ = _run_days(["decode_fail", "decode_fail"], kind="signals",
                           label="Anthropic", names=["Anthropic"])
        alerts = [a for _, al in log for a in al]
        self.assertEqual(len(alerts), 1)

    def test_one_run_is_not_enough(self):
        log, _ = _run_days(["decode_fail"])
        self.assertEqual([a for _, al in log for a in al], [])


class RealertTest(unittest.TestCase):
    """③ 겹 — 미해결이면 7일마다 다시 운다. 회복은 1회."""

    def test_21_days_failure_exactly_three(self):
        log, _ = _run_days(["decode_fail"] * 21)
        hits = [(d, a["text"]) for d, al in log for a in al]
        self.assertEqual(len(hits), 3, hits)
        self.assertEqual([d for d, _ in hits], ["2026-09-23", "2026-09-30", "2026-10-07"])
        self.assertIn("첫 경보 후 7일", hits[1][1])
        self.assertIn("첫 경보 후 14일", hits[2][1])

    def test_recovery_once(self):
        log, state = _run_days(["http_error"] * 10 + ["ok", "ok", "ok"])
        rec = [a for _, al in log for a in al if a["kind"] == "recover"]
        self.assertEqual(len(rec), 1)
        self.assertFalse(state["feeds"]["companion:spacex:발사"].get("err_alerted"))

    def test_zero_realert_and_recovery(self):
        log, _ = _run_days(["zero"] * 18 + ["ok"])
        hard = [d for d, al in log for a in al if a["kind"] == "alert"]
        soft = [d for d, al in log for a in al if a["kind"] == "recover"]
        self.assertEqual(hard, ["2026-09-25", "2026-10-02", "2026-10-09"])
        self.assertEqual(soft, ["2026-10-10"])

    def test_signals_zero_realert(self):
        state = {"sources": {"Anthropic": {"zero_streak": 0, "alerted": False,
                                           "ever_seen": True}}, "feeds": {}}
        day0, hits = datetime(2026, 9, 22), []
        for i in range(15):
            today = (day0 + timedelta(days=i)).strftime("%Y-%m-%d")
            st = _status("zero", "signals", "Anthropic")
            a1, _ = ps.judge_signals(st, ["Anthropic"], state, today, CFG)
            a2, _ = ps.judge_feeds(st, state, today, CFG)
            hits += [today for _ in a1 + a2]
        self.assertEqual(hits, ["2026-09-25", "2026-10-02"])

    def test_legacy_alerted_without_date_realerts_once_now(self):
        """옛 상태(alerted:True, 날짜 없음)는 이관 회차에 한 번 울고 다시 7일 주기로."""
        state = {"sources": {}, "feeds": {"companion:spacex:발사": {
            "err_streak": 0, "zero_streak": 35, "err_alerted": False, "zero_alerted": True}}}
        hits = []
        for i in range(8):
            today = (datetime(2026, 10, 24) + timedelta(days=i)).strftime("%Y-%m-%d")
            a, _ = ps.judge_feeds(_status("zero"), state, today, CFG)
            hits += [(today, x["text"]) for x in a]
        self.assertEqual([d for d, _ in hits], ["2026-10-24", "2026-10-31"])
        self.assertIn("첫 경보 일자 미상", hits[0][1])


class BackfillTest(unittest.TestCase):
    """S4 — 놓친 사건 회수: spacex 만 · when:30d · 상한 2편 · 1회 한정."""

    def setUp(self):
        import companion_essays as ce
        self.ce = ce

    def test_widen_query(self):
        u = "https://news.google.com/rss/search?q=SpaceX+OR+Starship+when:7d&hl=en-US"
        self.assertIn("when:30d", self.ce.widen_query(u, 30))
        self.assertNotIn("when:7d", self.ce.widen_query(u, 30))
        self.assertIn("q=Anthropic+when:30d",
                      self.ce.widen_query("https://news.google.com/rss/search?q=Anthropic&hl=en", 30))

    def _entry(self, n):
        return {"company": "spacex", "title": f"e{n}", "sources": [{"url": f"https://u/{n}"}]}

    def test_cap_two_and_once_only(self):
        calls = []

        def fake_run_auto(comp, *a, **kw):
            calls.append(kw)
            return self._entry(len(calls))
        state = {"published": {}}
        comps = {"spacex": {"slug": "spacex", "ko": "SpaceX"}}
        with mock.patch.object(self.ce, "run_auto", side_effect=fake_run_auto):
            out = self.ce.run_backfill(comps, "k", "c", [], "2026-10-24", state, days=30)
            again = self.ce.run_backfill(comps, "k", "c", [], "2026-10-25", state, days=30)
        self.assertEqual(len(out), 2)                  # 상한 2편
        self.assertEqual(again, [])                    # 1회 한정
        self.assertEqual(calls[0]["widen_days"], 30)
        self.assertEqual(calls[1]["exclude"], {"https://u/1"})   # 같은 기사 재사용 금지
        self.assertNotIn("이미 쓴 에세이", calls[0]["memo"])
        self.assertIn("「e1」", calls[1]["memo"])                 # 같은 사건 재집필 금지(제목으로)
        self.assertTrue(all(e.get("backfill") for e in out))
        self.assertEqual(state["backfill"]["spacex"]["published"], 2)

    def test_focused_backfill_one_per_event_and_lock_per_focus(self):
        """E3(2026-10-23) — 초점 회수: 사건마다 1편, 초점이 다르면 새 회수, 같은 초점은 잠금."""
        calls = []

        def fake_run_auto(comp, *a, **kw):
            calls.append(kw)
            return {"company": "spacex", "title": f"f{len(calls)}", "event_type": "기타",
                    "event_date": kw["focus"]["date"], "event_key": [kw["focus"]["label"]],
                    "sources": [{"url": f"https://f/{len(calls)}"}]}
        state = {"published": {}, "backfill": {"spacex": {"date": "2026-10-23", "days": 30, "published": 1}}}
        comps = {"spacex": {"slug": "spacex", "ko": "SpaceX"}}
        focus = [{"date": "2026-09-28", "label": "Flight 14"}, {"date": "2026-10-20", "label": "3Q26 실적"}]
        with mock.patch.object(self.ce, "run_auto", side_effect=fake_run_auto):
            self.assertEqual(self.ce.run_backfill(comps, "k", "c", [], "d", state, days=30, focus=[]), [])  # 옛 잠금
            out = self.ce.run_backfill(comps, "k", "c", [], "2026-10-24", state, days=30, focus=focus)
            again = self.ce.run_backfill(comps, "k", "c", [], "2026-10-24", state, days=30, focus=focus)
        self.assertEqual([c["focus"]["label"] for c in calls], ["Flight 14", "3Q26 실적"])
        self.assertIn("'Flight 14' 하나만", calls[0]["memo"])
        self.assertEqual(len(out), 2)
        self.assertEqual(again, [])                                   # 같은 초점은 1회
        self.assertEqual(state["backfill"]["spacex"]["key"], "2026-09-28|Flight 14;2026-10-20|3Q26 실적")

    def test_focus_parse_and_window(self):
        with mock.patch.dict(self.ce.os.environ, {"COMPANION_BACKFILL_FOCUS": "2026-09-28|Flight 14;bad|x;2026-10-20|3Q"}):
            self.assertEqual([f["date"] for f in self.ce.backfill_focus()], ["2026-09-28", "2026-10-20"])
        self.assertTrue(self.ce.in_focus({"pub": "Mon, 28 Sep 2026 23:10:00 GMT"}, "2026-09-28"))
        self.assertTrue(self.ce.in_focus({"pub": "2026-10-03"}, "2026-09-28"))
        self.assertFalse(self.ce.in_focus({"pub": "2026-10-20"}, "2026-09-28"))
        self.assertFalse(self.ce.in_focus({"pub": ""}, "2026-09-28"))

    def test_zero_days_is_noop(self):
        with mock.patch.object(self.ce, "run_auto") as ra:
            self.assertEqual(self.ce.run_backfill({"spacex": {}}, "k", "c", [], "d",
                                                  {"published": {}}, days=0), [])
        ra.assert_not_called()

    def test_within_days(self):
        now = datetime(2026, 10, 23, tzinfo=__import__("datetime").timezone.utc)
        self.assertTrue(self.ce.within_days("Tue, 20 Oct 2026 00:00:00 -0400", 14, now))
        self.assertFalse(self.ce.within_days("Tue, 04 Aug 2026 16:05:00 -0400", 14, now))
        self.assertTrue(self.ce.within_days("알 수 없음", 14, now))     # 못 읽으면 버리지 않는다


class StaleRowTest(unittest.TestCase):
    """유령 행 — 키가 바뀐 뒤 남은 낡은 실패 행을 매 회차 새 실패로 세지 않는다."""

    def test_stale_error_row_never_alerts(self):
        state = {"sources": {}, "feeds": {}}
        st = {"sources": {"scout:scout:AAPL": {"kind": "scout", "source": "scout:AAPL",
                                               "outcome": "http_error", "code": 503, "items": 0,
                                               "ts": "2026-10-11 11:49"}}}
        hits = []
        for i in range(21):
            today = (datetime(2026, 10, 23) + timedelta(days=i)).strftime("%Y-%m-%d")
            a, seen = ps.judge_feeds(st, state, today, CFG)
            hits += a
        self.assertEqual(hits, [])
        self.assertEqual(seen["stale"], 1)

    def test_fresh_row_still_counts(self):
        state = {"sources": {}, "feeds": {}}
        hits = []
        for i in range(2):
            today = (datetime(2026, 10, 23) + timedelta(days=i)).strftime("%Y-%m-%d")
            st = {"sources": {"scout:AAPL": {"kind": "scout", "source": "AAPL", "outcome": "http_error",
                                             "code": 503, "items": 0, "ts": today + " 09:00"}}}
            hits += ps.judge_feeds(st, state, today, CFG)[0]
        self.assertEqual(len(hits), 1)

    def test_stale_signal_row_is_unrecorded(self):
        state = {"sources": {"Anthropic": {"zero_streak": 0, "alerted": False, "ever_seen": True}}}
        st = {"sources": {"signals:Anthropic": {"kind": "signals", "source": "Anthropic",
                                                "outcome": "zero", "items": 0, "ts": "2026-10-01 09:00"}}}
        a, obs = ps.judge_signals(st, ["Anthropic"], state, "2026-10-23", CFG)
        self.assertEqual((a, obs["Anthropic"]["outcome"]), ([], "미기록"))


class WithdrawTest(unittest.TestCase):
    """철회 — 삭제가 아니라 status=withdrawn. 렌더·공지·브리핑·하루 1편 잠금에서 빠진다."""

    def setUp(self):
        import companion_essays as ce
        self.ce = ce
        self.doc = {"essays": [{"slug": "spacex-a", "company": "spacex", "date": "2026-10-23",
                                "origin": "auto", "title": "A", "sources": [{"url": "https://u/1"}]},
                               {"slug": "spacex-b", "company": "spacex", "date": "2026-10-20",
                                "origin": "auto", "title": "B", "sources": []}]}

    def test_withdraw_idempotent_with_one_correction(self):
        c = {"date": "2026-10-23", "company": "spacex", "text": "정정"}
        self.assertEqual(self.ce.withdraw(self.doc, {"spacex-a"}, "사유", "t", c), ["spacex-a"])
        self.assertEqual(self.ce.withdraw(self.doc, {"spacex-a"}, "사유", "t", c), [])
        self.assertEqual(len(self.doc["corrections"]), 1)
        self.assertEqual(len(self.doc["essays"]), 2)                    # 삭제하지 않는다
        self.assertEqual(self.doc["essays"][0]["withdrawn_reason"], "사유")

    def test_withdrawn_frees_daily_lock(self):
        self.assertTrue(self.ce.published_today(self.doc["essays"], "spacex", "2026-10-23"))
        self.ce.withdraw(self.doc, {"spacex-a"}, "r", "t")
        self.assertFalse(self.ce.published_today(self.doc["essays"], "spacex", "2026-10-23"))

    def test_render_excludes_withdrawn(self):
        import build_site as bs
        import json
        import tempfile
        self.ce.withdraw(self.doc, {"spacex-a"}, "r", "t", {"date": "d", "company": "spacex", "text": "정정"})
        d = Path(tempfile.mkdtemp())
        (d / "essays.json").write_text(json.dumps(self.doc, ensure_ascii=False), encoding="utf-8")
        (d / "companion_sources.json").write_text('{"companies": []}', encoding="utf-8")
        saved = bs.DATA_DIR
        try:
            bs.DATA_DIR = d
            data = bs.companion_data()
        finally:
            bs.DATA_DIR = saved
        self.assertEqual([e["slug"] for e in data["essays"]], ["spacex-b"])
        self.assertEqual(data["corrections"][0]["text"], "정정")


class WithdrawDeployTest(unittest.TestCase):
    """철회는 라이브 0회 확인까지가 한 동작 — 표식·누출 검사·관문·관제탑."""

    DOC = {"essays": [{"slug": "anthropic-x", "company": "anthropic", "date": "2026-10-23",
                       "title": "증명이 무기가 된다 — \"형식검증\"", "sources": []}]}

    def setUp(self):
        import companion_essays as ce
        import copy
        self.ce = ce
        self.doc = copy.deepcopy(self.DOC)

    def test_withdraw_sets_pending_marker(self):
        self.ce.withdraw(self.doc, {"anthropic-x"}, "r", "2026-10-23 11:00 KST")
        self.assertEqual(self.doc["pending_deploy"][0]["slugs"], ["anthropic-x"])
        self.assertEqual(self.ce.clear_pending(self.doc, {"anthropic-x"}), 0)

    def test_leak_detected_in_raw_and_json_forms(self):
        import json
        self.ce.withdraw(self.doc, {"anthropic-x"}, "r", "t")
        title = self.doc["essays"][0]["title"]
        stale = [("journal.html", "const CE = " + json.dumps({"essays": [{"title": title}]},
                                                             ensure_ascii=False))]
        self.assertEqual(self.ce.html_leaks(self.doc, stale), [("journal.html", "anthropic-x")])
        self.assertEqual(self.ce.html_leaks(self.doc, [("journal.html", "<p>정정 한 줄</p>")]), [])

    def test_verify_pages_gate_blocks_leak(self):
        import json
        import tempfile
        import verify_pages as vp
        self.ce.withdraw(self.doc, {"anthropic-x"}, "r", "t")
        d = Path(tempfile.mkdtemp()) / "essays.json"
        d.write_text(json.dumps(self.doc, ensure_ascii=False), encoding="utf-8")
        page = ("journal.html", json.dumps(self.doc["essays"][0]["title"], ensure_ascii=False))
        self.assertEqual(vp.withdrawn_leaks([page], d), {"journal.html": ["anthropic-x"]})
        self.assertEqual(vp.withdrawn_leaks([("journal.html", "깨끗")], d), {})

    def test_verify_pages_main_wired(self):
        """관문 main() 이 철회 누출에서 실제로 1을 돌려준다(배선) — DM 은 막는다."""
        import verify_pages as vp
        pages = [(n, "".join(vp.MARKERS) + "".join(f'data-mk="{k}"' for k in vp.MACRO_BADGE_KEYS)
                  + "</hea" + "d>")for n in ("journal.html",)]
        with mock.patch.object(vp, "targets", return_value=(pages, [])),                 mock.patch.object(vp, "notify"),                 mock.patch.object(vp, "withdrawn_leaks", return_value={"journal.html": ["x"]}):
            self.assertEqual(vp.main(), 1)
        with mock.patch.object(vp, "targets", return_value=(pages, [])),                 mock.patch.object(vp, "withdrawn_leaks", return_value={}):
            self.assertEqual(vp.main(), 0)

    def test_sentinel_alerts_on_old_marker(self):
        doc = {"pending_deploy": [{"slugs": ["anthropic-x"], "since": "2026-10-23 11:00 KST"}]}
        self.assertEqual(ps.judge_withdrawals(doc, "2026-10-23"), [])
        self.assertEqual(len(ps.judge_withdrawals(doc, "2026-10-24")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
