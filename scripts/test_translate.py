#!/usr/bin/env python3
"""
콘텐츠 번역 엔진 검증 (test_translate) — 단계 3. 네트워크 없이 가짜 모델로 엔진 전체를 돈다.

막으려는 것: 번역이 원문에 없는 숫자·권고를 만들어 다른 언어 화면에 게시되는 것.
그래서 '관문이 막는다'만이 아니라 **막힌 번역이 화면에 끼워지지 않는다**까지 본다(빌드 적용 포함).

  ① 통과 → 캐시 ok · 빌드가 끼운다 · 같은 원문은 다시 부르지 않는다
  ② 원문이 바뀌면 캐시 무효 — 빌드는 원문으로 돌아가고 엔진은 다시 옮긴다
  ③ 관문 실패 → 사유를 주고 정확히 1회 재생성 → 그래도면 미게시(원문 유지)
  ④ 묶음: 짧은 항목 여럿을 한 요청에 · 실패한 항목만 재생성
  ⑤ 회당 상한(I18N_CAP) · 최신순 · 첫 회차 30일
  ⑥ 철회된 에세이 → 모든 언어 캐시 동시 삭제
  ⑦ 보호 블록(차트 svg)은 모델에 보내지 않고 원문 그대로 복원
  ⑧ 원장 kind=i18n 언어별 행 · 키 없으면 아무것도 안 쓴다
  ⑨ 영어 원문 칸(원제)은 en 에서 옮기지 않고 es·ja 는 영어에서 직접
실행: python scripts/test_translate.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import i18n_content as IC        # noqa: E402
import translate_content as TC   # noqa: E402

TODAY = "2026-10-26"

# 가짜 번역 사전 — 원문 → 언어별 번역. 모델 흉내: 프롬프트의 JSON 을 읽어 같은 키로 돌려준다
GOOD = {
    "엔비디아 시총 5조 달러 돌파": {"en": "NVIDIA market cap tops $5 trillion",
                              "es": "La capitalización de NVIDIA supera los 5 billones de dólares",
                              "ja": "NVIDIAの時価総額が5兆ドルを突破"},
    "<p>매출 146억 달러 <a href=\"https://x.com/a\">원문</a></p><div class=\"col-chart\">⟦B0⟧</div>":
        {"en": "<p>Revenue of $14.6 billion <a href=\"https://x.com/a\">source</a></p><div class=\"col-chart\">⟦B0⟧</div>",
         "es": "<p>Ingresos de 14,6 mil millones de dólares <a href=\"https://x.com/a\">fuente</a></p>"
               "<div class=\"col-chart\">⟦B0⟧</div>",
         "ja": "<p>売上高146億ドル <a href=\"https://x.com/a\">原文</a></p><div class=\"col-chart\">⟦B0⟧</div>"},
    "요약 한 줄": {"en": "One-line summary", "es": "Resumen de una línea", "ja": "一行要約"},
    "AI 가속기 패권자": {"en": "AI accelerator leader", "es": "Líder en aceleradores de IA", "ja": "AIアクセラレーターの覇者"},
    "메모리 강자": {"en": "Memory powerhouse", "es": "Potencia de la memoria", "ja": "メモリーの強者"},
    "Why Nvidia Hit 5 Trillion": {"es": "Por qué Nvidia llegó a 5 billones", "ja": "Nvidiaが5兆に達した理由"},
    "기술 의미 설명": {"en": "What it means technically", "es": "Qué significa técnicamente", "ja": "技術的な意味"},
    "매출 146억": {"en": "Revenue 14.6B", "es": "Ingresos 14,6 mil millones", "ja": "売上高146億"},
}


class FakeModel:
    """프롬프트 JSON → 번역. bad: 원문 → 첫 시도에 돌려줄 오염된 번역(언어 무관)."""

    def __init__(self, lang_of, bad=None, bad_always=False, fail=False):
        self.lang_of, self.bad, self.bad_always, self.fail = lang_of, bad or {}, bad_always, fail
        self.calls, self.prompts = 0, []

    def __call__(self, prompt, model, key):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("HTTP 529 overloaded")
        lang = "en" if "into English" in prompt else "es" if "into Spanish" in prompt else "ja"
        js = prompt[prompt.index("JSON:\n") + 6:]
        js = js[:js.index("\n\nYour previous")] if "\n\nYour previous" in js else js
        payload = json.loads(js)
        retry = "rejected by the verification gates" in prompt
        out = {}
        for k, v in payload.items():
            if v in self.bad and (self.bad_always or not retry):
                out[k] = self.bad[v]
            else:
                out[k] = GOOD.get(v, {}).get(lang, "UNTRANSLATED " + v)
        return out, {"input_tokens": 100, "output_tokens": 50}


def fixture(tmp, columns=None, essays=None, latest=None, research=None):
    d = Path(tmp) / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "columns.json").write_text(json.dumps({"columns": columns or []}, ensure_ascii=False), encoding="utf-8")
    (d / "essays.json").write_text(json.dumps(essays or {"essays": [], "corrections": []}, ensure_ascii=False),
                                   encoding="utf-8")
    if latest is not None:
        (d / "latest.json").write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
    if research is not None:
        (d / "research.json").write_text(json.dumps(research, ensure_ascii=False), encoding="utf-8")
    return d, Path(tmp) / "cache"


SVG = '<div class="col-chart"><svg viewBox="0 0 10 10"><text>매출 146억</text></svg></div>'
COL = {"date": "2026.10.25", "url": "https://x.com/a", "title": "엔비디아 시총 5조 달러 돌파",
       "summary": "요약 한 줄", "body": "<p>매출 146억 달러 <a href=\"https://x.com/a\">원문</a></p>" + SVG}


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_engine(self, d, c, model, **env):
        with mock.patch.dict(os.environ, {"I18N_CAP": env.get("cap", "40")}):
            return TC.run(today=TODAY, data_dir=d, cache_dir=c, caller=model, api_key="k", record=False,
                          log=lambda *a: None)

    def test_01_pass_caches_applies_and_is_not_redone(self):
        d, c = fixture(self.tmp, columns=[COL])
        m = FakeModel(None)
        r = self.run_engine(d, c, m)
        self.assertEqual({L: s["published"] for L, s in r["stats"].items()}, {"en": 1, "es": 1, "ja": 1})
        doc = json.loads((d / "columns.json").read_text(encoding="utf-8"))
        out, n = IC.apply("column", "es", doc, c)
        self.assertEqual(n, 1)
        col = out["columns"][0]
        self.assertIn("5 billones", col["title"])
        self.assertIn('<svg viewBox="0 0 10 10"><text>Ingresos 14,6 mil millones</text></svg>', col["body"],
                      "차트는 도형 그대로 · 라벨만 그 언어로")
        self.assertNotIn("<svg", "".join(m.prompts), "보호 블록(도형)은 모델에 보내지 않는다")
        calls = m.calls
        self.run_engine(d, c, m)
        self.assertEqual(m.calls, calls, "같은 원문은 다시 부르지 않는다")

    def test_02_source_change_invalidates(self):
        d, c = fixture(self.tmp, columns=[COL])
        self.run_engine(d, c, FakeModel(None))
        changed = dict(COL, summary="메모리 강자")
        (d / "columns.json").write_text(json.dumps({"columns": [changed]}, ensure_ascii=False), encoding="utf-8")
        out, n = IC.apply("column", "en", json.loads((d / "columns.json").read_text(encoding="utf-8")), c)
        self.assertEqual(n, 0, "원문이 바뀐 캐시는 끼우지 않는다")
        self.assertEqual(out["columns"][0]["title"], COL["title"])
        r = self.run_engine(d, c, FakeModel(None))
        self.assertEqual(r["stats"]["en"]["published"], 1)

    def test_03_gate_failure_retries_once_then_unpublished(self):
        d, c = fixture(self.tmp, columns=[COL])
        bad = {COL["title"]: "NVIDIA market cap tops $6 trillion. We recommend you buy."}
        m = FakeModel(None, bad=bad, bad_always=True)
        r = self.run_engine(d, c, m, cap="1")
        s = r["stats"]["en"]
        self.assertEqual((s["published"], s["gate_failed"], s["requests"]), (0, 1, 2), "정확히 1회 재생성")
        self.assertIn("숫자", m.prompts[1])
        self.assertIn("준법", m.prompts[1])
        out, n = IC.apply("column", "en", json.loads((d / "columns.json").read_text(encoding="utf-8")), c)
        self.assertEqual((n, out["columns"][0]["title"]), (0, COL["title"]), "막힌 번역은 화면에 없다")
        rec = IC.read_cache("en", "column", IC.SPECS["column"]["id"](COL), c)
        self.assertEqual(rec["status"], "failed")
        # 실패한 같은 원문은 7일 동안 다시 사지 않는다 — 다음 회차는 다른 언어(아직 안 한 것)로 넘어간다
        m2 = FakeModel(None)
        self.run_engine(d, c, m2, cap="3")
        self.assertEqual(m2.calls, 2)
        self.assertFalse(any("into English" in p for p in m2.prompts))

    def test_04_retry_with_reasons_can_succeed(self):
        d, c = fixture(self.tmp, columns=[COL])
        m = FakeModel(None, bad={COL["title"]: "NVIDIA market cap tops $6 trillion"})
        r = self.run_engine(d, c, m, cap="1")
        self.assertEqual((r["stats"]["en"]["published"], r["stats"]["en"]["requests"]), (1, 2))
        self.assertIn("6 trillion", m.prompts[1])

    def test_05_batching_and_partial_retry(self):
        latest = {"meta": {"fetched_date": TODAY},
                  "regions": {"earth": {"stocks": [{"ticker": "NVDA", "desc": "AI 가속기 패권자"},
                                                   {"ticker": "MU", "desc": "메모리 강자"}]}}}
        d, c = fixture(self.tmp, latest=latest)
        m = FakeModel(None, bad={"메모리 강자": "메모리 강자"})    # 한글이 남은 번역 → 그 항목만 재생성
        r = self.run_engine(d, c, m)
        self.assertEqual(r["stats"]["en"]["requests"], 2, "묶음 1회 + 실패 항목만 재생성 1회")
        self.assertEqual(r["stats"]["en"]["published"], 2)
        self.assertNotIn("AI 가속기", m.prompts[1], "통과한 항목은 재생성 묶음에 넣지 않는다")

    def test_06_cap_newest_first_and_first_run_window(self):
        old = dict(COL, date="2026.08.01", url="https://x.com/old", title="기술 의미 설명")
        new = dict(COL, date="2026.10.25")
        d, c = fixture(self.tmp, columns=[old, new])
        m = FakeModel(None)
        r = self.run_engine(d, c, m, cap="1")
        self.assertEqual(r["done"], 1)
        self.assertEqual(r["backlog"], 3, "첫 회차는 최근 30일만 — 8월 칼럼은 대기에도 없다")
        self.assertIn("5조", m.prompts[0], "최신 항목부터")

    def test_07_withdrawn_essay_purged_in_all_languages(self):
        e = {"slug": "x-1", "date": "2026-10-24", "title": "엔비디아 시총 5조 달러 돌파", "html": "<p>요약 한 줄</p>"}
        d, c = fixture(self.tmp, essays={"essays": [e], "corrections": []})
        for L in IC.TARGETS:
            IC.write_cache(L, "essay", "x-1", {"status": "ok", "src_hash": "h", "fields": {}}, c)
        (d / "essays.json").write_text(json.dumps({"essays": [dict(e, status="withdrawn")]}, ensure_ascii=False),
                                       encoding="utf-8")
        r = self.run_engine(d, c, FakeModel(None))
        self.assertEqual(r["purged"], 3)
        self.assertFalse(any(IC.cache_path(L, "essay", "x-1", c).exists() for L in IC.TARGETS))

    def test_08_english_source_fields(self):
        research = {"stocks": [{"ticker": "NVDA", "articles": [
            {"link": "https://b.com/1", "date": "2026-10-25T00:00:00", "title": "Why Nvidia Hit 5 Trillion",
             "summary_ko": ["기술 의미 설명"]}]}]}
        d, c = fixture(self.tmp, research=research)
        m = FakeModel(None)
        self.run_engine(d, c, m)
        en_prompts = [p for p in m.prompts if "into English" in p]
        self.assertTrue(en_prompts and all("Why Nvidia" not in p for p in en_prompts), "en 은 영어 원제를 옮기지 않는다")
        es = [p for p in m.prompts if "into Spanish" in p][0]
        self.assertIn("from English and Korean", es.replace("Korean and English", "English and Korean"))
        out, _ = IC.apply("research", "ja", research, c)
        self.assertEqual(out["stocks"][0]["articles"][0]["title"], "Nvidiaが5兆に達した理由")

    def test_09_api_failure_is_counted_not_cached(self):
        d, c = fixture(self.tmp, columns=[COL])
        r = self.run_engine(d, c, FakeModel(None, fail=True))
        self.assertEqual(r["stats"]["en"]["api_errors"], 1)
        self.assertFalse(IC.cache_path("en", "column", IC.SPECS["column"]["id"](COL), c).exists())

    def test_09b_time_budget_stops_new_batches_but_still_reports(self):
        d, c = fixture(self.tmp, columns=[COL])
        m = FakeModel(None)
        with mock.patch.dict(os.environ, {"I18N_CAP": "40", "I18N_BUDGET_S": "-1"}):
            r = TC.run(today=TODAY, data_dir=d, cache_dir=c, caller=m, api_key="k", record=False,
                       log=lambda *a: None)
        self.assertEqual((r["done"], m.calls, r["backlog"]), (0, 0, 3))

    def test_10_no_key_writes_nothing(self):
        d, c = fixture(self.tmp, columns=[COL])
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            r = TC.run(today=TODAY, data_dir=d, cache_dir=c, api_key="", log=lambda *a: None)
        self.assertEqual(r, {"skipped": "no_key"})
        self.assertFalse(Path(c).exists())

    def test_11_ledger_rows(self):
        import feed_client as fc
        d, c = fixture(self.tmp, columns=[COL])
        status = Path(self.tmp) / "fetch_status.json"
        with mock.patch.object(fc, "STATUS_PATH", status), mock.patch.object(fc, "_buffer", {}):
            with mock.patch.dict(os.environ, {"I18N_CAP": "40"}):
                TC.run(today=TODAY, data_dir=d, cache_dir=c, caller=FakeModel(None), api_key="k",
                       log=lambda *a: None)
        rows = json.loads(status.read_text(encoding="utf-8"))["sources"]
        self.assertEqual(sorted(rows), ["i18n:en", "i18n:es", "i18n:ja"])
        self.assertEqual((rows["i18n:en"]["outcome"], rows["i18n:en"]["attempted"], rows["i18n:en"]["items"]),
                         ("ok", 1, 1))


class BuildWiringTest(unittest.TestCase):
    """빌드 배선 — 엔진이 쓴 캐시가 build_site.load_data 를 거쳐 그 언어 화면 데이터에 실제로 닿는가."""

    def test_load_data_applies_cache_only_for_target_language(self):
        import build_site as bs
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        d, c = fixture(tmp, columns=[COL])
        with mock.patch.dict(os.environ, {"I18N_CAP": "40"}):
            TC.run(today=TODAY, data_dir=d, cache_dir=c, caller=FakeModel(None), api_key="k", record=False,
                   log=lambda *a: None)
        with mock.patch.object(bs, "DATA_DIR", d), mock.patch.object(IC, "CACHE", c):
            with mock.patch.object(bs, "LANG", "ja"):
                ja = bs.load_data("columns.json")
            with mock.patch.object(bs, "LANG", "ko"):
                ko = bs.load_data("columns.json")
        self.assertEqual(ja["columns"][0]["title"], "NVIDIAの時価総額が5兆ドルを突破")
        self.assertEqual(ko["columns"][0]["title"], COL["title"], "ko 는 원문 그대로")


class MutationTest(unittest.TestCase):
    """관문이 실제로 막는가 — 변이를 넣으면 실패해야 한다(실패할 수 없는 테스트는 감시자가 아니다)."""

    def test_tampered_number_is_blocked(self):
        f = {"title": ("엔비디아 시총 5조 달러 돌파", "ko", "")}
        ok, reasons = TC.gate(f, {"title": "NVIDIA market cap tops $5 trillion"}, "en")
        self.assertEqual(reasons, [])
        ok, reasons = TC.gate(f, {"title": "NVIDIA market cap tops $5.5 trillion"}, "en")
        self.assertTrue(any("숫자" in r for r in reasons))

    def test_dropped_link_and_tag_blocked(self):
        f = {"body": ('<p>a <a href="https://x.com">b</a></p>', "ko", "html")}
        ok, reasons = TC.gate(f, {"body": "<p>a b</p>"}, "es")
        self.assertTrue(any("링크" in r for r in reasons) and any("마크업" in r for r in reasons))

    def test_hangul_left_blocked(self):
        f = {"t": ("메모리 강자", "ko", "")}
        ok, reasons = TC.gate(f, {"t": "メモリー強者 메모리"}, "ja")
        self.assertTrue(any("한글" in r for r in reasons))

    def test_glossary_enforced(self):
        f = {"t": ("우주지배자 TOP 20 변동", "ko", "")}
        ok, reasons = TC.gate(f, {"t": "Changes in the TOP 20 rulers"}, "en")
        self.assertTrue(any("용어 사전" in r for r in reasons))

    def test_markdown_table_shape(self):
        f = {"md": ("## 표\n| a | b |\n|---|---|\n| 1 | 2 |", "ko", "md")}
        ok, reasons = TC.gate(f, {"md": "## Table\n| a | b |\n|---|---|"}, "en")
        self.assertTrue(any("마크다운" in r for r in reasons))


class F2F3Test(unittest.TestCase):
    """F2 관제판 행 · F3 시차 서술 — 엔진과 빌드가 같은 칸·같은 해시. 번역이 판정 id 를 바꾸지 않는다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ok(self, kind, lang, iid, node, spec, tr):
        f = IC.fields_for(spec, node, lang)
        IC.write_cache(lang, kind, iid, {"src_hash": IC.src_hash(f), "status": "ok",
                                         "fields": {p: tr(v[0]) for p, v in f.items()}}, self.tmp)

    def test_cockpit_rows_localize_only_with_matching_cache(self):
        import probe_cockpit as pc
        ms = [{"id": "A-1", "text": "가입자 2,000만 이상", "status": "진행", "parts": [{"label": "소비자 가입자", "unit": "만 명"}]}]
        trig = [{"id": "T1", "name": "4Q26 실적", "status": "대기", "expected": "2027-02-초(추정)"}]
        delta = [{"id": "2026-10", "kind": "x", "lines": ["판정 변화 없음"]}]
        self._ok("probe_ms", "en", "A-1", ms[0], IC.COCKPIT["probe_ms"], lambda s: "EN:" + s)
        self._ok("probe_delta", "en", "2026-10", delta[0], IC.COCKPIT["probe_delta"], lambda s: "EN:" + s)
        m2, t2, d2 = pc.localize_rows("en", ms, trig, delta, self.tmp)
        self.assertEqual(m2[0]["text"], "EN:가입자 2,000만 이상")
        self.assertEqual(m2[0]["parts"][0]["label"], "EN:소비자 가입자")
        self.assertEqual(m2[0]["status"], "진행", "상태값은 번역 대상이 아니다(기계 id + 카탈로그)")
        self.assertEqual(t2[0]["name"], "4Q26 실적", "캐시 없는 행은 원문")
        self.assertEqual(d2[0]["lines"], ["EN:판정 변화 없음"])
        ms[0]["text"] = "가입자 2,500만 이상"          # 원문이 바뀌면 캐시 무효
        self.assertEqual(pc.localize_rows("en", ms, trig, delta, self.tmp)[0][0]["text"], "가입자 2,500만 이상")
        self.assertIs(pc.localize_rows("ko", ms, trig, delta, self.tmp)[0], ms)

    def test_engine_collects_cockpit_kinds(self):
        nodes = {"probe_ms": [("A-1", "", {"id": "A-1", "text": "이정표 문장"})]}
        with mock.patch.object(TC, "cockpit_nodes", return_value=nodes), \
             mock.patch.object(TC, "probe_nodes", return_value=[]), \
             mock.patch.object(TC, "thesis_nodes", return_value=[]), \
             mock.patch.object(IC, "SPECS", {}):
            jobs, _ = TC.collect(TODAY, data_dir=None, cache_dir=self.tmp, log=lambda *a: None)
        self.assertEqual(sorted({(j[1], j[3]) for j in jobs}), [("probe_ms", L) for L in ("en", "es", "ja")])

    def test_parallax_spec_skips_exact_types_and_keeps_ids(self):
        sp = IC.SPECS["parallax"]
        exact = {"ticker": "NVDA", "type": "추정불가", "rationale": "대형 세입자 이탈", "eps_asof": None}
        mixed = {"ticker": "AMZN", "type": "씨즈형·고자본 혼합", "rationale": "혼합체", "eps_asof": "2026-08 컨센서스 역산"}
        self.assertEqual(sorted(IC.fields_for(sp, exact, "en")), ["rationale"])
        self.assertEqual(sorted(IC.fields_for(sp, mixed, "en")), ["eps_asof", "rationale", "type"])
        self.assertTrue(sp.get("late"), "load_data 가 끼우면 판정 id 를 뽑기 전에 유형 이름이 바뀐다")
        import build_site as bs
        old = bs.LANG
        unc = {"ticker": "ZZZ", "type": "추정불가 경계", "rationale": "근거", "basis": "forward"}
        self._ok("parallax", "en", "ZZZ", unc, sp, lambda s: "EN:" + s)
        try:
            bs.LANG = "en"
            with mock.patch.object(IC, "CACHE", self.tmp):
                v = bs.buffett_view({"items": [unc]})["items"][0]
        finally:
            bs.LANG = old
        self.assertEqual(v["type"], "EN:추정불가 경계")
        self.assertTrue(v["uncertain"], "판정(추정불가 포함)은 원문에서 — 번역 뒤에 재면 사라진다")
        self.assertEqual(v["type_id"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
