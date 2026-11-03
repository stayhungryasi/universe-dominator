#!/usr/bin/env python3
"""
관측 공백 뒤의 '전일' 표기 테스트 (2026-11-03)
================================================================
막으려는 것 한 문장: **바로 전날이 아닌 스냅샷과 비교한 증감을 '전일'·'어제'라고 부르는 것.**
데일리가 5일 멈췄다 풀린 첫 회차가 10/29 대비 증감을 공개 채널 아침 브리핑에 '어제보다'로 낼 뻔했다.
기준 스냅샷을 고르는 규칙은 build_site.prev_snapshot 하나 — 화면(TOP 20·우주 지도)과 브리핑이 같이 쓴다.
실행: python scripts/test_snapshot_gap.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import build_site as bs                 # noqa: E402
import send_telegram_briefing as tb     # noqa: E402

ROOT = Path(__file__).parent.parent
EARTH = [("NVDA", "엔비디아", 5000), ("MSFT", "마이크로소프트", 4000), ("AAPL", "애플", 3900),
         ("GOOGL", "알파벳", 3000), ("AMZN", "아마존", 2500)]


def _stocks(scale=1.0):
    return [{"ticker": t, "name": n, "mc": mc * scale, "rank": i} for i, (t, n, mc) in enumerate(EARTH, 1)]


class GapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "snapshots").mkdir()
        (self.tmp / "latest.json").write_text(json.dumps(
            {"meta": {"fetched_date": "2026-11-03"}, "regions": {"earth": {"stocks": _stocks(1.05)}},
             "latent": []}), encoding="utf-8")
        self.patches = [mock.patch.object(bs, "DATA_DIR", self.tmp),
                        mock.patch.object(tb, "DATA_DIR", self.tmp),
                        mock.patch.object(tb, "SNAP_DIR", self.tmp / "snapshots")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def snap(self, d, scale=1.0):
        (self.tmp / "snapshots" / f"{d}.json").write_text(json.dumps(
            {"date": d, "regions": {"earth": _stocks(scale)}, "latent": []}), encoding="utf-8")

    def test_prev_snapshot_rule(self):
        self.snap("2026-10-28")
        self.snap("2026-10-29")
        self.snap("2026-11-03")                                     # 오늘 것은 기준이 아니다
        f, d, gap = bs.prev_snapshot("2026-11-03")
        self.assertEqual((f.name, d, gap), ("2026-10-29.json", "2026-10-29", 5))
        self.assertEqual(bs.prev_base("2026-11-03"), "2026-10-29")
        self.snap("2026-11-02")
        self.assertEqual(bs.prev_base("2026-11-03"), "")            # 바로 전날이면 '전일 대비'
        self.assertEqual(bs.prev_snapshot("2026-10-01"), (None, None, None))

    def test_briefing_names_base_after_gap(self):
        self.snap("2026-10-29")
        text = tb.build_briefing()
        self.assertIn("(순위·증감 10.29 대비)", text)
        self.assertIn("🔺5.0%", text)
        self.assertNotIn("어제", text)                               # 5일 전을 '어제'라고 부르지 않는다
        self.assertIn("10.29보다 벌어짐", text)

    def test_briefing_plain_on_consecutive_day(self):
        self.snap("2026-11-02")
        text = tb.build_briefing()
        self.assertNotIn("대비)", text)
        self.assertIn("어제보다 벌어짐", text)

    def test_screens_wired(self):
        tpl = (ROOT / "scripts" / "template.html").read_text(encoding="utf-8")
        self.assertIn("const PREV_BASE = {{PREV_BASE_JSON}};", tpl)
        self.assertIn("PREV_BASE ? T('idx.vs_date'", tpl)
        ob = (ROOT / "scripts" / "observatory-template.html").read_text(encoding="utf-8")
        self.assertIn("DATA.chgBase ? T('map.vs_date'", ob)
        src = Path(bs.__file__).read_text(encoding="utf-8")
        self.assertIn('"chgBase": prev_base(', src)
        self.assertIn('replace("{{PREV_BASE_JSON}}"', src)
        for lang in ("ko", "en", "es", "ja"):
            cat = json.loads((ROOT / "data" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertIn("{d}", cat["idx.vs_date"])
            self.assertIn("{d}", cat["map.vs_date"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
