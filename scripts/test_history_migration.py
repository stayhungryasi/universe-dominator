#!/usr/bin/env python3
"""
변동 이력 이관 검증 (test_history_migration) — UNIVERTRIX GLOBAL F1 (2026-10-26)

막으려는 것: 옛 기록을 사건 코드로 되돌리다 **ko 화면 글자가 바뀌는 것**, 그리고
'이관했다'고 믿었는데 실제로는 원문으로 남아 다른 언어 화면이 여전히 한국어인 것.

  ① 회귀: 저장소의 이관본에서 상태(사건·환율·라벨 id·기간)를 벗겨 이관 전 모양을 되살리고,
     이관기를 다시 돌려 → ko 화면 칸이 글자 단위로 같고, 사건에서 다시 그린 ko 도 같다
  ② 범위: 이관 대상 기간(≤ 2026.10.24)에서 원문으로 남는 항목은 **06.03 수기 기록뿐**(top20 9 · latent 8).
     패턴 하나가 무력화되면 원문 유지 개수가 늘어 실패한다(변이 감지)
  ③ 저장소 이관본 자체: 사건에서 다시 그린 ko == 저장된 문장
  ④ 다른 언어: 원문으로 남는 항목(raw)·사람이 지은 테마 조각 밖에는 한글이 없다
  ⑤ 빌드 배선: 다국어 빌드(history_view)가 이관된 기록을 그 언어로 다시 그린다
     (단계 2 의 조건은 기록 단위 'events' 를 찾아 늘 거짓이었다 — 2026-10-26 수리)
실행: python scripts/test_history_migration.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import migrate_history_events as M   # noqa: E402
import weekly_history as wh          # noqa: E402

DATA = Path(__file__).parent.parent / "data"
CUTOFF = "2026.10.24"                   # 이관 대상(그 뒤 기록은 처음부터 사건 코드로 저장된다)
EXPECTED_RAW = {"history-top20.json": 9, "history-latent.json": 8}
HAN = re.compile(r"[가-힣]")
MARKED = re.compile(r'<span lang="ko"[^>]*>[^<]*</span>(<sup class="uvx-ko"[^>]*>KO</sup>)?')


def load(fn):
    return json.loads((DATA / fn).read_text(encoding="utf-8"))


def strip_state(doc):
    """이관본 → 이관 전 모양(이관 대상 기간만). 원문 칸은 그대로 남기고 이관이 붙인 상태만 벗긴다."""
    doc = json.loads(json.dumps(doc, ensure_ascii=False))
    for e in doc.get("entries") or []:
        if (e.get("date") or "") > CUTOFF:
            continue
        e.pop("prev", None)
        e.pop("curr", None)
        for b in e.get("blocks") or []:
            for k in ("events", "fx", "label_id", "label_p"):
                b.pop(k, None)
    return doc


def scoped(doc):
    return {"entries": [e for e in doc.get("entries") or [] if (e.get("date") or "") <= CUTOFF]}


def regression(fn, disabled=()):
    """→ (ko 칸 동일, 다시 그린 ko 동일, 원문 유지 개수)."""
    idx = M.name_index()
    before = scoped(strip_state(load(fn)))
    after, st = M.migrate_doc(before, idx, disabled)
    return (M.visible_ko(after) == M.visible_ko(before),
            M.visible_ko(M.rerender_ko(after)) == M.visible_ko(before), st["raw"])


class MigrationTest(unittest.TestCase):
    def test_01_regression_ko_identical_and_scope(self):
        for fn, want in EXPECTED_RAW.items():
            same, same2, raw = regression(fn)
            self.assertTrue(same, f"{fn}: 이관 후 ko 원문 칸이 달라짐")
            self.assertTrue(same2, f"{fn}: 사건에서 다시 그린 ko 가 원문과 다름")
            self.assertEqual(raw, want, f"{fn}: 원문 유지 {raw}건(기대 {want} — 06.03 수기 기록만)")

    def test_02_raw_only_in_manual_entry(self):
        for fn in EXPECTED_RAW:
            for e in scoped(load(fn))["entries"]:
                raws = [x for b in e.get("blocks") or [] for x in b.get("events") or [] if x.get("k") == "raw"]
                if e.get("auto"):
                    self.assertEqual(raws, [], f"{fn} {e.get('date')}: 자동 기록에 원문 유지 항목")

    def test_03_repo_state_rerenders_to_stored_text(self):
        for fn in EXPECTED_RAW:
            doc = load(fn)
            self.assertEqual(M.visible_ko(M.rerender_ko(doc)), M.visible_ko(doc), fn)

    def test_04_other_languages_hangul_only_inside_marks(self):
        for fn in EXPECTED_RAW:
            for e in load(fn)["entries"]:
                for L in ("en", "es", "ja"):
                    r = wh.render_entry(e, L)
                    for b0, b in zip(e.get("blocks") or [], r.get("blocks") or []):
                        if b0.get("events") is None:
                            continue
                        for ev, it in zip(b0["events"], b["items"]):
                            if ev.get("k") == "raw":
                                continue
                            self.assertFalse(HAN.search(MARKED.sub("", it)),
                                             f"{fn} {e.get('date')} {L}: 표식 밖 한글 — {it[:90]}")
                        if b0.get("label_id"):
                            self.assertFalse(HAN.search(b.get("label") or ""), f"{L} 라벨: {b.get('label')}")
                    if e.get("prev"):
                        self.assertFalse(HAN.search(r.get("period") or ""), f"{L} 기간: {r.get('period')}")

    def test_05_build_rerenders_migrated_entries(self):
        import build_site as bs
        old = bs.LANG
        try:
            bs.LANG = "en"
            v = bs.history_view(load("history-top20.json"), "top20")
        finally:
            bs.LANG = old
        e = next(x for x in v["entries"] if x.get("date") == "2026.09.19")
        self.assertIn("(weekly)", e["period"])
        self.assertFalse(HAN.search(" ".join(e["blocks"][0]["items"])), e["blocks"][0]["items"][:2])

    def test_06_numbers_localized_not_reinterpreted(self):
        """es 에서 '1,384' 는 소수로 읽힌다 — 환율 칸은 그 언어 숫자 표기로(값은 같다)."""
        e = next(x for x in load("history-top20.json")["entries"] if any(b.get("fx") for b in x["blocks"]))
        fx = next(b for b in wh.render_entry(e, "es")["blocks"] if b.get("fx"))
        rate = float(next(b for b in e["blocks"] if b.get("fx"))["fx"]["rate"].replace(",", ""))
        self.assertIn(f"{rate:,.0f}".replace(",", "."), fx["items"][0]["v"])


class MutationTest(unittest.TestCase):
    """회귀 자가 실패할 수 있는가 — 패턴 하나를 끄면 원문 유지 개수가 늘어 ①이 실패해야 한다."""

    def test_disabled_pattern_is_detected(self):
        for name, fn in (("l_rank", "history-latent.json"), ("enter", "history-top20.json")):
            same, same2, raw = regression(fn, disabled={name})
            self.assertTrue(same and same2, "패턴을 꺼도 ko 는 원문 그대로여야 한다(원문 유지로 떨어진다)")
            self.assertGreater(raw, EXPECTED_RAW[fn], f"{name} 패턴을 꺼도 원문 유지 개수가 그대로 — 자가 무디다")

    def test_wrong_value_is_rejected_by_rerender(self):
        """정규식이 값을 잘못 읽으면(순위 a·b 뒤바뀜) 다시 그린 문장이 달라져 원문으로 남는다."""
        item = "<strong>CrowdStrike</strong> 잠재 순위 <span class='em-up'>74위 → 60위 상승</span>"
        ev, kind = M.to_event(item, {})
        self.assertEqual(kind, "l_rank")
        bad = dict(ev, a=ev["b"], b=ev["a"])
        self.assertNotEqual(wh.render_event(bad, "ko"), item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
