#!/usr/bin/env python3
"""
4개 언어 관측소 테스트 (2026-10-26 UNIVERTRIX GLOBAL 단계 1)
================================================================================
  ① 카탈로그 — 4개 언어 키 집합 일치 · 코드가 부르는 키 누락 0 · 매개변수 일치 · 동적 접두사 전수
  ② G2 숫자 — 카탈로그 en/es/ja 문장의 숫자 집합 = ko 문장의 숫자 집합(양방향)
  ③ G4 준법 — 4개 언어 카탈로그·관제판에 권고 목적의 문장 0
  ④ 산출물 — 완성 페이지 en/es/ja 에 표식 없는 한글 0 · <html lang> · hreflang 상호 일치 · canonical
  ⑤ 서식 — JS 서식기(UVX)와 파이썬 서식기(i18n)가 같은 값을 같은 글자로
  ⑥ 변이 — 키 1개 삭제 · 번역 숫자 1개 변조 · en 산출물에 한글 문장 주입 → **반드시 실패**
     (실패할 수 없는 테스트는 감시자가 아니다)
실행: python scripts/test_i18n.py
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import i18n                     # noqa: E402
import i18n_compliance as G     # noqa: E402
import i18n_numbers as N        # noqa: E402

ROOT = Path(__file__).parent.parent
SCRIPTS = Path(__file__).parent
CODE = ["template.html", "build_site.py", "probe_cockpit.py", "i18n.py", "weekly_history.py", "fetch_buffett.py"] +     sorted(p.name for p in SCRIPTS.glob("*-template.html"))
# 동적으로 만드는 키 — 접두사 + 상태 id 전수(새 상태가 생기면 여기와 카탈로그에 함께 넣는다)
DYNAMIC = {
    "px.type.": ("sees", "toll", "flight", "cyclical", "float", "too_hard"),
    "px.basis.": ("forward", "trailing", "uncovered", "too_hard", "unlisted", "float"),
    "px.zone.": ("explore", "commit", "watch", "over"),
    "px.reason.": ("too_hard", "float", "unlisted"),
    "px.gloss.": tuple(f"{k}.{p}" for k in ("sees", "toll", "flight", "cyclical", "float", "too_hard", "uncovered")
                       for p in ("dt", "dd")),
    "lg.zone.": ("pass", "prove_growth", "bond_inferior", "untested"),
    "lg.rate.": ("ust10", "ktb10", "jgb10"),
    "lg.line.": ("pass", "prove"),
    "lg.method.": ("xbrl", "gaap"),
    "lg.gloss.": tuple(f"{k}.{p}" for k in ("zone", "pik", "too_hard", "pass_price") for p in ("dt", "dd")),
    "lg.field.": ("eps_adj_ttm", "roe_tangible", "eps_adj_ttm_human", "roe_tangible_human", "g_forward",
                  "g_cagr3y", "cagr3y_human", "conversion", "risk5", "franchise", "capalloc", "owner_earnings"),
    "col.kind.": ("signal", "weekly", "deep"),
    "pc.st.": ("done", "early", "met", "prog", "miss", "wait"),
    "pc.st_short.": ("done", "early", "met", "prog", "miss", "wait"),
    "pc.kind.": ("earnings", "contract", "financing", "report", "filing", "starship", "ir", "essay", "withdraw"),
    "pc.panel.": ("milestones", "events", "fin", "scen", "report", "changelog", "sources"),
    "pc.scen.": ("bull", "base", "bear"),
    "pc.run.": ("success", "failure", "cancelled", "timed_out", "none"),
    "pc.src_state.": ("ok", "zero", "decode_fail", "http_error", "timeout", "none"),
    "pc.axis.": ("A", "B", "C", "D", "E"),
    "pc.row.": ("revenue", "op", "ni", "ocf", "capex", "fcf", "cash", "mkt", "debt", "subs", "arpu", "gw",
                "launch", "conn", "si", "space"),
    "pc.tst.": ("pending",),
    "macro.full.": ("usd_krw", "wti", "ust10", "ust30", "usd_jpy"),
    "nav.": ("home", "latent", "mega", "research", "observatory", "community", "journal", "pioneers"),
    # 단계 2 — 측정층 비고 코드(fetch_buffett *_codes) · 변동 이력 사건 · 잠재 테마 · 일정 · 신호 · 판정
    "nc.": ("price_missing", "eps_wait", "g_missing", "rate_missing", "neg_earnings", "cash_mismatch",
            "guard_ref", "guard_hold", "zone_hold", "rate_unwired.ktb10", "rate_unwired.jgb10",
            "rate_unwired.tw10", "rate_unwired.sa10", "px.no_eps", "px.no_fair", "px.price_fail",
            "item.dna", "item.capex", "div.sign", "div.high", "div.low")
           + tuple("eps." + x for x in ("float_skip", "stale", "api_fail", "no_shares", "short_history",
                                        "foreign_missing", "implausible"))
           + tuple("roe." + x for x in ("none", "income_missing", "api_fail", "api_fail_yf", "equity_missing",
                                        "equity_missing_yf", "equity_negative", "tangible_negative"))
           + tuple("conv." + x for x in ("c_missing", "c_missing_generic", "adj_nonpositive", "income_missing",
                                         "foreign", "float_skip", "api_fail", "none")),
    "hist.watch.": ("miss", "out", "in", "swap", "wait"),
    "hist.lbl.": ("weekly", "latent_weekly", "watch", "sector", "points", "fx"),
    "hist.top20.": ("title", "desc"),
    "hist.latent.": ("title", "desc"),
    "latent.theme.": ("semis", "software", "ai", "tech", "power", "internet"),
    "cal.hour.": ("bmo", "amc", "dmh"),
    "cal.type.": ("macro", "earnings", "event", "other"),
    "sig.tag.": ("essay", "model", "research", "policy", "invest", "other"),
    "ce.v.": ("up", "flat", "down", "hold"),
    "mega.cat.": ("top10", "throne", "themes", "rank"),
}


def vals(v):
    return list(v.values()) if isinstance(v, dict) else [v]


def code_keys():
    keys = set()
    for name in CODE:
        t = (SCRIPTS / name).read_text(encoding="utf-8")
        keys |= set(re.findall(r"\{\{t[:!]([a-z0-9_.\-]+)\}\}", t))
        keys |= set(re.findall(r"""\b_t\(\s*(?:lang|"ko"),\s*['"]([a-z0-9_.\-]+)['"]""", t))
        keys |= set(re.findall(r"""\bT\(\s*['"]([a-z0-9_.\-]+)['"]""", t))
        keys |= set(re.findall(r"""\bL\(\s*lang,\s*['"]([a-z0-9_.\-]+)['"]""", t))
        keys |= set(re.findall(r"""i18n\.t\(\s*(?:LANG|lang|L2),\s*['"]([a-z0-9_.\-]+)['"]""", t))
    return {k for k in keys if not k.endswith(".")}


def missing_keys(cats):
    """카탈로그 묶음 → [(언어, 키)] — 코드가 부르는 키·동적 키·ko 정본 키 중 빠진 것."""
    need = code_keys() | {p + s for p, ss in DYNAMIC.items() for s in ss}
    need |= {"region." + k for k in json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
             .get("regions", {})}
    need |= set(cats["ko"])
    return sorted((L, k) for L, c in cats.items() for k in need if k not in c)


def strip_payloads(html):
    """산출물 → 화면 문구(chrome)만 남긴 텍스트. 데이터 덩어리(const DATA = {...})는 JSON 으로 정확히 잘라 낸다.

    데이터에는 번역 전 원문(한국어)이 들어 있다 — 그것은 화면에서 lang=ko 표식으로 감싸지고
    렌더 검증(check_i18n_render)이 따로 본다. 여기서 보는 것은 템플릿·카탈로그·주입이 만든 글자다.
    """
    out, i = [], 0
    dec = json.JSONDecoder()
    for m in re.finditer(r"(?:const|let|var)\s+[A-Z][A-Z_0-9]*\s*=\s*(?=[\[{])", html):
        if m.start() < i:
            continue
        try:
            _, end = dec.raw_decode(html, m.end())
        except ValueError:
            continue
        out.append(html[i:m.end()])
        i = end
    out.append(html[i:])
    t = "".join(out)
    t = re.sub(r"<!--[\s\S]*?-->", " ", t)
    t = re.sub(r"/\*[\s\S]*?\*/", " ", t)
    t = re.sub(r"(?<![:'\"\w\\])//[^\n]*", " ", t)
    # 허용 목록 — 전환기의 '한국어' 링크(lang=ko 표식이 붙은 자기 이름)
    t = re.sub(r'<a [^>]*lang="ko"[^>]*>한국어<small>', " ", t)
    t = re.sub(r'<span lang="ko" data-uvx-pending="1">[^<]*</span>', " ", t)
    return t


def hangul_lines(text):
    return [ln.strip()[:100] for ln in text.splitlines() if i18n.HANGUL.search(ln)]


class CatalogTest(unittest.TestCase):
    def setUp(self):
        i18n.reload()
        self.cats = {L: i18n.catalog(L) for L in i18n.LANGS}

    def test_key_sets_equal_and_nothing_missing(self):
        self.assertEqual(missing_keys(self.cats), [])
        for L in i18n.LANGS:
            self.assertEqual(sorted(set(self.cats[L]) ^ set(self.cats["ko"])), [], L)

    def test_params_match(self):
        bad = []
        for k, v in self.cats["ko"].items():
            want = set(re.findall(r"\{(\w+)\}", " ".join(vals(v))))
            for L in ("en", "es", "ja"):
                got = set(re.findall(r"\{(\w+)\}", " ".join(vals(self.cats[L][k]))))
                if got != want:
                    bad.append((L, k, sorted(want), sorted(got)))
        self.assertEqual(bad, [])

    def test_translations_have_no_hangul(self):
        bad = [(L, k) for L in ("en", "es", "ja") for k, v in self.cats[L].items()
               if any(i18n.HANGUL.search(s) for s in vals(v))]
        self.assertEqual(bad, [])

    def test_numbers_match_korean(self):
        """G2 — 카탈로그 번역 문장의 숫자 집합 = ko 문장의 숫자 집합."""
        bad = []
        for k, v in self.cats["ko"].items():
            for L in ("en", "es", "ja"):
                for sv in vals(v):
                    for tv in vals(self.cats[L][k]):
                        e, m = N.check(sv, tv, L)
                        if e or m:
                            bad.append((L, k, e, m))
        self.assertEqual(bad, [])

    def test_compliance_all_languages(self):
        """G4 — 권고 목적의 문장 0(부정형 고지는 통과)."""
        bad = [(L, k, G.violations(s, L)) for L in i18n.LANGS for k, v in self.cats[L].items()
               for s in vals(v) if G.violations(s, L)]
        self.assertEqual(bad, [])

    def test_glossary_terms(self):
        """용어 사전 — 화면 문구도 번역 관문과 같은 함수로 잰다(사이트 고유명은 한 이름)."""
        import i18n_gates as Q
        bad = []
        for k, v in self.cats["ko"].items():
            if k.startswith("_") or not isinstance(v, str):
                continue
            src = re.sub(r"\s*·\s*우주지배자$", "", v)      # 탭 제목 끝의 브랜드 = UNIVERTRIX
            for L in ("en", "es", "ja"):
                for s in vals(self.cats[L].get(k, "")):
                    bad += [(L, k, why) for why in Q.glossary(src, s, L)]
        self.assertEqual(bad, [])

    def test_names_cover_korean_stock_names(self):
        latest = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
        names = [(s.get("ticker"), s.get("name")) for r in latest.get("regions", {}).values()
                 for s in r.get("stocks", [])] + [(x.get("ticker"), x.get("name")) for x in latest.get("latent", [])]
        for L in ("en", "es", "ja"):
            left = sorted({n for t, n in names if i18n.has_hangul(i18n.stock_name(L, t, n))})
            self.assertEqual(left, [], L)


class ComplianceTest(unittest.TestCase):
    """판정 함수 — 정상 문장으로 오탐을 먼저 잰다(방지선 규칙 ②)."""

    def test_advice_is_caught_in_every_language(self):
        for L, s in (("ko", "지금 분할 매수하세요."), ("ko", "지금 신규 진입하세요."), ("en", "We recommend you buy NVDA now."),
                     ("es", "Recomendamos comprar ahora."), ("ja", "この銘柄は買い推奨です。")):
            self.assertEqual(G.purpose(s, L), "advice", (L, s))

    def test_disclaimers_and_analysis_pass(self):
        for L, s in (("ko", "어떤 항목도 매수·매도 신호가 아닙니다."), ("ko", "STAGE 2 · ORBIT — 궤도 진입"),
                     ("ko", "매출 비중 42%"), ("ko", "엔비디아 TOP 20 신규 진입 (3위)"),
                     ("ko", "시점별 변동 기록 — 신규 진입·이탈, 권역별 흐름"), ("en", "Nothing here is a buy or sell signal."),
                     ("en", "The buy-back continued."), ("en", "not a price target"),
                     ("es", "no un precio objetivo."), ("ja", "目標株価ではありません。"),
                     ("ja", "売上高が伸びた。"), ("ja", "売買シグナルではありません。")):
            self.assertNotEqual(G.purpose(s, L), "advice", (L, s))

    def test_cockpit_chrome_complies(self):
        import agent_report as ar
        import probe_cockpit as pc
        for L in i18n.LANGS:
            h, d, _ = pc.render(ar, today=date(2026, 10, 26), lang=L)
            plain = re.sub(r'<span lang="ko" data-uvx-pending="1">[^<]*</span>', " ", h)
            self.assertEqual(G.violations(plain, L), [], L)
            if L != "ko":
                # 관제판의 화면 문구는 그 언어 — 남는 한글은 원문 표식(탐사 원장 문장) 안에만
                self.assertEqual(hangul_lines(re.sub(r"<[^>]+>", "\n", plain)), [], L)
                self.assertTrue(all(not i18n.has_hangul(p["ko"]) for p in d["panels"]), L)


class NumberGateTest(unittest.TestCase):
    def test_units_across_languages(self):
        ko = "씨즈캔디는 이익을 16배 불리는 동안 3,200만 달러를 썼다 — 4억 달러짜리 공사. 4.8배."
        self.assertTrue(N.ok(ko, "grew 16-fold, spending $32 million — a $400 million job. 4.8x.", "en"))
        self.assertTrue(N.ok(ko, "por 16, gastó 32 millones de dólares; una obra de 400 millones de dólares. 4,8x.", "es"))
        self.assertTrue(N.ok(ko, "16倍、3,200万ドル — 4億ドルの工事。4.8倍。", "ja"))

    def test_tampered_number_is_blocked(self):
        ko = "증축비는 3,200만 달러"
        self.assertFalse(N.ok(ko, "expansion cost $33 million", "en"))
        self.assertFalse(N.ok(ko, "3,300万ドル", "ja"))
        self.assertFalse(N.ok(ko, "33 millones de dólares", "es"))

    def test_translation_must_keep_the_value_exactly(self):
        # 에세이 규율의 '큰 쪽 반올림 폭'이면 5조 → 5.5 trillion 이 통과한다 — 번역은 더 정밀한 쪽 폭으로
        self.assertFalse(N.ok("시총 5조 달러", "market cap of $5.5 trillion", "en"))
        self.assertFalse(N.ok("매출 146.2억 달러", "revenue of $14.6 billion", "en"))
        self.assertTrue(N.ok("매출 146억 달러", "revenue of $14.6 billion", "en"))

    def test_article_number_is_not_money(self):
        # '제1조'(조문)는 1조(10^12)가 아니다 — 약관 번역에서 실제로 났던 오탐(2026-10-26)
        self.assertTrue(N.ok("제1조 (목적)", "Article 1 (Purpose)", "en"))
        self.assertTrue(N.ok("제12조 (준거법)", "第12条(準拠法)", "ja"))
        self.assertFalse(N.ok("시가총액 1조 원", "market cap of KRW 1", "en"))

    def test_missing_number_is_blocked(self):
        self.assertFalse(N.ok("매출 146억 달러, 영업이익 23억 달러", "revenue $14.6 billion", "en"))


class FormatterTest(unittest.TestCase):
    """JS 서식기와 파이썬 서식기 — 같은 규칙(두 벌의 판이 갈라지면 빌드 숫자와 화면 숫자가 달라진다)."""

    def test_js_matches_python(self):
        if shutil.which("node") is None:
            self.skipTest("node 없음")
        cases = [5094.0, 812.4, 39600.0, 0.4]
        for L in i18n.LANGS:
            js = (i18n.runtime_js(L, [], {"USD": 1, "KRW": 1324.45, "JPY": 156.8})
                  .replace('<script id="uvx-i18n-v1">', "").replace("</script>", "").replace("<\\/", "</"))
            js = "var window = {}; var localStorage = {getItem: function(){ return null; }};" + js + \
                 ";console.log(JSON.stringify({mc: " + json.dumps(cases) + ".map(window.UVX.mc), " \
                 "bn: [705, 1200, -5].map(window.UVX.bn), d: window.UVX.date('2026-10-26'), " \
                 "md: window.UVX.md('2026-10-26')}))"
            p = subprocess.run(["node"], input=js, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(p.returncode, 0, p.stderr[:300])
            got = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got["mc"], [i18n.mc(L, v) for v in cases], L)
            self.assertEqual(got["bn"], [i18n.usd_bn(L, v) for v in (705, 1200, -5)], L)
            self.assertEqual((got["d"], got["md"]), (i18n.date(L, "2026-10-26"), i18n.md(L, "2026-10-26")), L)

    def test_korean_formats_unchanged(self):
        self.assertEqual((i18n.mc("ko", 5094.0), i18n.mc("ko", 812.4), i18n.mc1("ko", 39600)),
                         ("$5.09T", "$812B", "$39.6T"))
        self.assertEqual((i18n.mc("es", 5094.0), i18n.mc("ja", 812.4)), ("US$5,09T", "8,124億ドル"))


class OutputTest(unittest.TestCase):
    """산출물(빌드가 끝난 저장소) — 완성 페이지 en/es/ja 의 화면 문구 한글 0 · hreflang 상호성."""

    @classmethod
    def setUpClass(cls):
        cls.pages = {}
        for L in i18n.LANGS:
            for p in i18n.DONE_PAGES:
                f = ROOT / i18n.subdir(L) / p
                if f.exists():
                    cls.pages[(L, p)] = f.read_text(encoding="utf-8")

    def setUp(self):
        if len(self.pages) < len(i18n.LANGS) * len(i18n.DONE_PAGES):
            self.skipTest("산출물 없음 — 빌드 후 실행")

    def test_no_hangul_in_chrome(self):
        bad = {}
        for (L, p), h in self.pages.items():
            if L == i18n.DEFAULT:
                continue
            left = hangul_lines(strip_payloads(h))
            if left:
                bad[f"{L}/{p}"] = left[:5]
        self.assertEqual(bad, {})

    def test_html_lang_and_hreflang_reciprocal(self):
        for (L, p), h in self.pages.items():
            self.assertIn(f'<html lang="{L}"', h[:300], (L, p))
            alts = dict(re.findall(r'<link rel="alternate" hreflang="([a-z\-]+)" href="([^"]+)">', h))
            self.assertEqual(sorted(alts), sorted(list(i18n.LANGS) + ["x-default"]), (L, p))
            self.assertEqual(alts["x-default"], i18n.canonical(i18n.X_DEFAULT, p))
            self.assertIn(f'<link rel="canonical" href="{i18n.canonical(L, p)}">', h)
            for L2, url in alts.items():
                if L2 != "x-default":
                    other = self.pages[(L2, p)]
                    self.assertIn(f'<link rel="canonical" href="{url}">', other, (L, L2, p))

    def test_runtime_and_compliance(self):
        for (L, p), h in self.pages.items():
            self.assertEqual(h.count('id="uvx-i18n-v1"'), 1, (L, p))
            self.assertNotIn("{{t:", h, (L, p))
            chrome = re.sub(r"<[^>]+>", "\n", strip_payloads(h))
            self.assertEqual(G.violations(chrome, L), [], (L, p))
            # 런타임 사전에 빈 값(null) 0 — 페이지가 부르는데 카탈로그에 없는 키·접두사를 키로 실은 것
            m = re.search(r'var L = "[a-z]+", M = (\{.*?\}), R = ', h, re.S)
            self.assertIsNotNone(m, (L, p))
            nulls = sorted(k for k, v in json.loads(m.group(1)).items() if v is None)
            self.assertEqual(nulls, [], (L, p))


class MutationTest(unittest.TestCase):
    """변이 3종 — 관문이 실제로 막는가."""

    def test_deleting_a_key_fails(self):
        cats = {L: dict(i18n.catalog(L)) for L in i18n.LANGS}
        self.assertEqual(missing_keys(cats), [])
        del cats["es"]["obs.tab.legend"]
        self.assertEqual(missing_keys(cats), [("es", "obs.tab.legend")])

    def test_tampering_a_number_fails(self):
        ko, en = i18n.catalog("ko")["px.gloss.sees.dd"], i18n.catalog("en")["px.gloss.sees.dd"]
        self.assertTrue(N.ok(ko, en, "en"))
        self.assertFalse(N.ok(ko, en.replace("$32 million", "$35 million"), "en"))

    def test_injected_hangul_in_en_output_fails(self):
        f = ROOT / "en" / "index.html"
        if not f.exists():
            self.skipTest("산출물 없음")
        h = f.read_text(encoding="utf-8")
        self.assertEqual(hangul_lines(strip_payloads(h)), [])
        bad = h.replace("</main>", "<p>이 문장은 번역되지 않았다</p></main>", 1)
        self.assertTrue(hangul_lines(strip_payloads(bad)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
