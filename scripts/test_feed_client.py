#!/usr/bin/env python3
"""
피드 공용 클라이언트 검증 (test_feed_client)

2026-08-22 사고 재발 방지: 동행 구글뉴스 7개가 한꺼번에 503을 맞았다.
여기서 보는 것은 세 가지다 —
  ① 503 을 만나면 정말로 백오프하며 다시 두드리는가 (한 번 던지고 포기하지 않는가)
  ② 항구적 오류(404)는 헛되이 다시 두드리지 않는가
  ③ 같은 호스트를 연타하지 않는가 (페이싱·순서 섞기)

실행: python scripts/test_feed_client.py
"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
