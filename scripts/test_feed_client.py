#!/usr/bin/env python3
"""
피드 공용 클라이언트 검증 (test_feed_client)

2026-08-22 사고 재발 방지: 동행 구글뉴스 7개가 한꺼번에 503을 맞았다.
여기서 보는 것은 세 가지다 —
  ① 503 을 만나면 정말로 백오프하며 다시 두드리는가 (한 번 던지고 포기하지 않는가)
  ② 항구적 오류(404)는 헛되이 다시 두드리지 않는가
  ③ 같은 호스트를 연타하지 않는가 (페이싱·순서 섞기)
  ④ 결과가 원장에 남는가 — 관제탑이 '조용한 날'과 '죽은 소스'를 가르는 근거

실행: python scripts/test_feed_client.py
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import feed_client as fc   # noqa: E402


class Resp:
    def __init__(self, code, text="<rss/>"):
        self.status_code, self.text = code, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Server Error")


class RetryTest(unittest.TestCase):
    def setUp(self):
        fc._last_hit.clear()
        fc._buffer.clear()
        self.sleep = mock.patch.object(fc.time, "sleep")   # 테스트는 실제로 기다리지 않는다
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_503_then_success(self):
        seq = [Resp(503), Resp(503), Resp(200, "<rss>ok</rss>")]
        with mock.patch.object(fc.requests, "get", side_effect=seq) as g:
            text, outcome, code = fc.fetch("https://news.google.com/x", "src", "signals")
        self.assertEqual(g.call_count, 3, "503 은 재시도해야 한다")
        self.assertEqual((outcome, code), ("ok", 200))
        self.assertIn("ok", text)

    def test_503_all_the_way_gives_up_cleanly(self):
        with mock.patch.object(fc.requests, "get", return_value=Resp(503)) as g:
            text, outcome, code = fc.fetch("https://news.google.com/x", "src", "companion")
        self.assertIsNone(text)
        self.assertEqual(outcome, "http_error")
        self.assertEqual(code, 503)
        self.assertEqual(g.call_count, fc.RETRIES + 1, "재시도 횟수만큼 두드린다")
        rec = fc._buffer["companion:src"]
        self.assertEqual((rec["outcome"], rec["code"], rec["kind"]),
                         ("http_error", 503, "companion"))

    def test_backoff_is_exponential(self):
        waits = []
        with mock.patch.object(fc.time, "sleep", side_effect=lambda s: waits.append(s)):
            with mock.patch.object(fc.requests, "get", return_value=Resp(503)):
                fc.fetch("https://news.google.com/x", "src", "signals")
        big = [w for w in waits if w in fc.BACKOFF]
        self.assertEqual(big, list(fc.BACKOFF), "백오프가 4→10→20 으로 커져야 한다")

    def test_404_is_not_retried(self):
        """항구적 오류를 세 번 더 두드리는 것은 무례하고 느리기만 하다."""
        with mock.patch.object(fc.requests, "get", return_value=Resp(404)) as g:
            _, outcome, code = fc.fetch("https://example.com/x", "src", "signals")
        self.assertEqual(g.call_count, 1)
        self.assertEqual((outcome, code), ("http_error", 404))

    def test_timeout_reported(self):
        with mock.patch.object(fc.requests, "get",
                               side_effect=fc.requests.exceptions.Timeout()):
            _, outcome, code = fc.fetch("https://example.com/x", "src", "signals")
        self.assertEqual(outcome, "timeout")
        self.assertIsNone(code)
        self.assertEqual(fc._buffer["signals:src"]["outcome"], "timeout")


class PacingTest(unittest.TestCase):
    def setUp(self):
        fc._last_hit.clear()

    def test_same_host_is_paced(self):
        waits = []
        with mock.patch.object(fc.time, "sleep", side_effect=lambda s: waits.append(s)):
            with mock.patch.object(fc.requests, "get", return_value=Resp(200)):
                fc.fetch("https://news.google.com/a", "a", "signals")
                fc.fetch("https://news.google.com/b", "b", "signals")
        self.assertTrue(any(w >= fc.MIN_GAP - 0.01 for w in waits),
                        f"같은 호스트 연타를 쉬지 않았다: {waits}")

    def test_different_hosts_not_paced(self):
        waits = []
        with mock.patch.object(fc.time, "sleep", side_effect=lambda s: waits.append(s)):
            with mock.patch.object(fc.requests, "get", return_value=Resp(200)):
                fc.fetch("https://news.google.com/a", "a", "signals")
                fc.fetch("https://openai.com/rss.xml", "b", "signals")
        self.assertEqual(waits, [], "호스트가 다르면 기다릴 이유가 없다")

    def test_interleave_by_host(self):
        srcs = [{"url": "https://news.google.com/1"}, {"url": "https://news.google.com/2"},
                {"url": "https://openai.com/a"}, {"url": "https://deepmind.google/b"}]
        hosts = [fc._host(s["url"]) for s in fc.interleave_by_host(srcs)]
        self.assertEqual(len(hosts), 4)
        pairs = sum(1 for i in range(1, len(hosts)) if hosts[i] == hosts[i - 1])
        self.assertEqual(pairs, 0, f"같은 호스트가 연달아 있다: {hosts}")

    def test_interleave_single_host_keeps_order(self):
        srcs = [{"url": f"https://news.google.com/{i}"} for i in range(3)]
        self.assertEqual(fc.interleave_by_host(srcs), srcs)


class LedgerTest(unittest.TestCase):
    """상태 원장 — 이 파일이 있어야 관제탑이 0건과 장애를 구분한다."""

    def setUp(self):
        fc._buffer.clear()

    def test_flush_merges_other_processes(self):
        """fetch_signals 와 companion 은 다른 프로세스다 — 서로의 기록을 지우면 안 된다."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fetch_status.json"
            path.write_text(json.dumps(
                {"sources": {"signals:OpenAI": {"kind": "signals", "outcome": "ok"}}},
                ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(fc, "STATUS_PATH", path):
                fc.record("companion", "spacex:발사", "zero", 200, 0)
                fc.flush()
                doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("signals:OpenAI", doc["sources"], "남의 기록을 지웠다")
        self.assertEqual(doc["sources"]["companion:spacex:발사"]["outcome"], "zero")
        self.assertIn("generated_label", doc)

    def test_record_shape(self):
        fc.record("signals", "Anthropic", "ok", 200, 3)
        r = fc._buffer["signals:Anthropic"]
        self.assertEqual((r["source"], r["items"], r["kind"]), ("Anthropic", 3, "signals"))
        self.assertRegex(r["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_empty_buffer_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fetch_status.json"
            with mock.patch.object(fc, "STATUS_PATH", path):
                fc.flush()
            self.assertFalse(path.exists(), "쓸 것이 없으면 파일을 만들지 않는다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
