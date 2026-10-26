"""
i18n.py — 4개 언어 관측소의 단일 진실 공급원 (2026-10-26 UNIVERTRIX GLOBAL 단계 1)
================================================================================
번역이 아니라 **관측 위치**를 바꾼다. 같은 데이터·같은 숫자를 네 행성(ko·en·es·ja)에서 본다.

  LANGS        언어 목록 — 이 상수 하나만 읽는다(ALL_PAGES 와 같은 원칙)
  catalog()    data/i18n/{lang}.json 메시지 카탈로그 (키 = 의미 단위, ko 가 정본)
  t()          키 → 문장 ({n}·{date} 매개변수, 복수형 {"one":…, "other":…})
  render()     템플릿의 {{t:키}} → 그 언어 문장(HTML 이스케이프)
  runtime_js() 페이지 head 첫머리에 심는 T()·UVX 서식기 — 그 페이지가 쓰는 키만. **클라이언트 fetch 없음**
  서식기        mc·local·pct·num·date — 파이썬(빌드 시점)과 JS(화면) 두 벌이 **같은 규칙**을 쓴다

헌법: 화면 문구는 상태에서 생성한다(작업 규칙 8) — 한국어 문장을 번역 매핑하지 않는다.
카탈로그는 '의미 단위 키 → 언어별 문장'이고, 한국어 문장을 키로 쓰지 않는다.
"""
import html as _html
import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent.parent
I18N_DIR = HERE / "data" / "i18n"

# ── 언어 목록 — 단일 진실 공급원 ───────────────────────────────────────────────
LANGS = ("ko", "en", "es", "ja")
DEFAULT = "ko"          # 루트(지금 주소 그대로 — G1)
X_DEFAULT = "en"        # hreflang x-default
SITE = "https://univertrix.com"
HTML_LANG = {"ko": "ko", "en": "en", "es": "es", "ja": "ja"}
OG_LOCALE = {"ko": "ko_KR", "en": "en_US", "es": "es_LA", "ja": "ja_JP"}
INTL = {"ko": "ko-KR", "en": "en-US", "es": "es-ES", "ja": "ja-JP"}
# 언어 전환기의 자기 이름(각 언어로) — 고유명사 허용 목록이기도 하다(ko 링크 라벨 '한국어')
SELF_NAME = {"ko": "한국어", "en": "English", "es": "Español", "ja": "日本語"}
SHORT = {"ko": "KO", "en": "EN", "es": "ES", "ja": "JA"}
# 관측 위치 렌즈(단계 4)의 기본 통화 — 결정 대기: es 기본 통화(기본안 USD)
DEFAULT_CCY = {"ko": "KRW", "en": "USD", "es": "USD", "ja": "JPY"}

# 화면 문구 100% 완료 페이지 — 한글 0·hreflang·sitemap 검사 대상. 단계마다 늘어난다.
# (나머지 페이지도 4개 언어로 빌드되지만 본문 번역 전이라 noindex + '번역 준비 중' 띠)
DONE_PAGES = ("index.html", "latent.html", "megatrend.html", "research.html", "community.html",
              "observatory.html", "journal.html", "pioneers.html", "history-top20.html",
              "history-latent.html", "about.html", "policies.html")

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")


def subdir(lang):
    """산출물 폴더 — ko 는 루트, 나머지는 /en/ 같은 하위 폴더."""
    return "" if lang == DEFAULT else lang + "/"


def up(lang):
    """그 언어 페이지에서 루트로 가는 상대 경로."""
    return "" if lang == DEFAULT else "../"


def href_to(from_lang, to_lang, page):
    """from_lang 페이지에서 to_lang 의 같은 페이지로 가는 상대 링크."""
    return up(from_lang) + subdir(to_lang) + page


def canonical(lang, page):
    """확장자 없는 정식 주소(Cloudflare 가 *.html 을 308 로 확장자 없는 주소로 보낸다)."""
    stem = "" if page == "index.html" else page[:-5] if page.endswith(".html") else page
    return SITE + "/" + subdir(lang) + stem


# ── 카탈로그 ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def catalog(lang):
    p = I18N_DIR / f"{lang}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def reload():
    catalog.cache_clear()
    names.cache_clear()


def _pick(v, n):
    if isinstance(v, dict):
        if n is not None and n == 1 and "one" in v:
            return v["one"]
        return v.get("other", next(iter(v.values())))
    return v


MISSING = []            # 빌드 중 발견한 키 누락 — selftest 가 0 을 요구한다


def t(lang, key, **params):
    """키 → 문장. 누락이면 ko 로 대신하고 MISSING 에 기록한다(화면에 키 이름을 내보내지 않는다)."""
    cat = catalog(lang)
    if key in cat:
        v = cat[key]
    else:
        MISSING.append((lang, key))
        v = catalog(DEFAULT).get(key, "")
    s = _pick(v, params.get("n"))
    if params:
        s = re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params
                   else m.group(0), s)
    return s


_T_RE = re.compile(r"\{\{t:([a-z0-9_.\-]+)\}\}")
# 강조 태그가 섞인 문단(소개·정책) — 키가 _html 로 끝날 때만 무이스케이프. 카탈로그는 저장소의 신뢰 원본이다
_TH_RE = re.compile(r"\{\{t!([a-z0-9_.\-]+_html)\}\}")


def render(html, lang):
    """템플릿의 {{t:키}} → 그 언어 문장(HTML 이스케이프) · {{t!키_html}} → 그대로. 매개변수 문장은 JS·파이썬에서."""
    html = _TH_RE.sub(lambda m: t(lang, m.group(1)), html)
    return _T_RE.sub(lambda m: _html.escape(t(lang, m.group(1)), quote=True), html)


# JS 가 부르는 키 — T('키') · T("키") · TP('접두사.') (접두사로 묶인 키 전부)
_JS_KEY = re.compile(r"""\bT\(\s*['"]([a-z0-9_.\-]+)['"]""")
_JS_PREFIX = re.compile(r"""\bTP\(\s*['"]([a-z0-9_.\-]+\.)['"]""")
_HINT = re.compile(r"i18n-keys:\s*([a-z0-9_.\-* ]+)")


def js_keys(html):
    """페이지가 쓰는 키 — 직접 호출 + 접두사 호출 + 'i18n-keys: a.* b.c' 힌트 주석."""
    keys = set(_JS_KEY.findall(html))
    prefixes = set(_JS_PREFIX.findall(html))
    # T('mega.cat.' + id) — 점으로 끝나는 키는 동적 키의 접두사다(2026-10-26 렌더 실측: 접두사를
    # 키로 싣자 런타임에 null 이 들어가 분류 칩이 한국어 상태값으로 떨어졌다)
    prefixes |= {k for k in keys if k.endswith(".")}
    keys = {k for k in keys if not k.endswith(".")}
    for h in _HINT.findall(html):
        for k in h.split():
            if k.endswith("*"):
                prefixes.add(k[:-1])
            else:
                keys.add(k)
    cat = catalog(DEFAULT)
    for p in prefixes:
        keys.update(k for k in cat if k.startswith(p))
    return sorted(keys)


# ── 이름 사전 — 한국어 종목명 등 고유명사 ────────────────────────────────────

@lru_cache(maxsize=None)
def names():
    p = I18N_DIR / "names.json"
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def stock_name(lang, ticker, name):
    """종목명 — ko 는 데이터 그대로. 다른 언어는 이름 사전, 없으면 원래 이름(한글이면 표식 대상)."""
    if lang == DEFAULT:
        return name
    ent = names().get(ticker or "") or {}
    if not ent and has_hangul(name):           # 티커가 없거나 다른 표기면 한국어 이름으로 역조회
        ent = next((e for e in names().values() if e.get("ko") == name), {})
    return ent.get(lang) or ent.get("en") or name


def short_names(lang):
    """{티커: 짧은 이름} — 3D 이름표·우주 지도용(언어별 고유명사만. 라틴 이름은 템플릿이 들고 있다)."""
    out = {}
    for tk, ent in names().items():
        v = short_name(lang, tk)
        if v:
            out[tk] = v
    return out


def short_name(lang, ticker):
    ent = names().get(ticker) or {}
    if lang == DEFAULT:
        return ent.get("ko_short")
    return ent.get(lang + "_short") or ent.get("en_short")


# ── 서식기 (파이썬 · 빌드 시점) — JS 판(UVX_JS)과 같은 규칙 ────────────────────

def _dec(lang, s):
    """소수점 — es 는 쉼표(천 단위 구분 없는 짧은 수에만 쓴다)."""
    return s.replace(".", ",") if lang == "es" else s


def mc(lang, b):
    """시가총액(십억 달러) — ko·en $5.09T/$812B · es US$5,09T · ja 5.09兆ドル/8,120億ドル."""
    if not isinstance(b, (int, float)) or isinstance(b, bool):
        return "—"
    if lang == "ja":
        return f"{b / 1000:.2f}兆ドル" if b >= 1000 else f"{round(b * 10):,}億ドル"
    pre = "US$" if lang == "es" else "$"
    return (pre + _dec(lang, f"{b / 1000:.2f}") + "T") if b >= 1000 else f"{pre}{b:.0f}B"


def mc1(lang, b):
    """합계용 소수 1자리 T(빌드 시점 TOP 20 합계)."""
    if not isinstance(b, (int, float)):
        return "—"
    if lang == "ja":
        return f"{b / 1000:.1f}兆ドル" if b >= 1000 else f"{round(b * 10):,}億ドル"
    pre = "US$" if lang == "es" else "$"
    return (pre + _dec(lang, f"{b / 1000:.1f}") + "T") if b >= 1000 else f"{pre}{b:.0f}B"


def usd_m(lang, v, digits=1):
    """백만 달러 → ko '$145.8억'·'$1.2조' / en '$14.6B'·'$1.2T' / es 'US$14,6B' / ja '145.8億ドル'."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    sign = "−" if v < 0 else ""
    a = abs(v)
    if lang == "ko":
        if a >= 1_000_000:
            return f"{sign}${a / 1_000_000:,.{digits}f}조"
        return f"{sign}${a / 100:,.{digits}f}억"
    if lang == "ja":
        if a >= 1_000_000:
            return f"{sign}{a / 1_000_000:,.{digits}f}兆ドル"
        return f"{sign}{a / 100:,.{digits}f}億ドル"
    pre = "US$" if lang == "es" else "$"
    if a >= 1_000_000:
        return f"{sign}{pre}" + _dec(lang, f"{a / 1_000_000:.{digits}f}") + "T"
    if a >= 1000:
        return f"{sign}{pre}" + _dec(lang, f"{a / 1000:.{digits}f}") + "B"
    return f"{sign}{pre}" + _dec(lang, f"{a:.{digits}f}") + "M"


def usd_bn(lang, v):
    """십억 달러(시나리오 2036) → ko '$7,050억'·'$1.2조' / en '$705B'·'$1.2T' / ja '7,050億ドル'."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    sign = "−" if v < 0 else ""
    a = abs(v)
    if lang == "ko":
        return f"{sign}${a / 1000:.1f}조" if a >= 1000 else f"{sign}${round(a * 10):,}억"
    if lang == "ja":
        return f"{sign}{a / 1000:.1f}兆ドル" if a >= 1000 else f"{sign}{round(a * 10):,}億ドル"
    pre = "US$" if lang == "es" else "$"
    return (f"{sign}{pre}" + _dec(lang, f"{a / 1000:.1f}") + "T") if a >= 1000 else f"{sign}{pre}{a:,.0f}B"


def count_m(lang, million):
    """백만 단위 개수(가입자) → ko '1,360만' / en '13.6M' / es '13,6 M' / ja '1,360万'."""
    if not isinstance(million, (int, float)) or isinstance(million, bool):
        return "—"
    if lang == "ko":
        return f"{million * 100:,.0f}만"
    if lang == "ja":
        return f"{million * 100:,.0f}万"
    return _dec(lang, f"{million:.1f}") + ("M" if lang == "en" else " M")


def num(lang, v, digits=None):
    """일반 수 — 천 단위 구분(ko·en·ja 쉼표 / es 마침표), es 소수점 쉼표."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    if digits is None:
        digits = 0 if abs(v) >= 100 or float(v).is_integer() else 1
    s = f"{v:,.{digits}f}"
    if lang == "es":
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return s


def pct(lang, v, digits=0):
    """비율(0.12) → '12%' · es '12 %' 가 아니라 '12%'(좁은 칸 우선) · 소수점은 언어 규칙."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    return _dec(lang, f"{v * 100:.{digits}f}") + "%"


_MON_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MON_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic")


def date(lang, iso):
    """'2026-10-26' → ko '2026.10.26' · en 'Oct 26, 2026' · es '26 oct 2026' · ja '2026年10月26日'."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return "—" if not iso else str(iso)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if lang == "ko":
        return f"{y}.{mo:02d}.{d:02d}"
    if lang == "en":
        return f"{_MON_EN[mo - 1]} {d}, {y}"
    if lang == "es":
        return f"{d} {_MON_ES[mo - 1]} {y}"
    return f"{y}年{mo}月{d}日"


def md(lang, iso):
    """월/일 — ko·ja·es '10/26'(es 는 '26/10') · en 'Oct 26'."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return "—"
    mo, d = int(m.group(2)), int(m.group(3))
    if lang == "en":
        return f"{_MON_EN[mo - 1]} {d}"
    if lang == "es":
        return f"{d}/{mo}"
    return f"{mo}/{d}"


# ── 화면 서식기 (JS) — 위 파이썬 판과 같은 규칙. head 첫머리에 한 번 심는다 ──────
# ⚠️ 이 문자열은 파이썬이 JS 를 생성한다 — 개행 이스케이프를 쓰지 않는다(지뢰: 프로브 침묵).
UVX_JS = r"""
(function(){
var L = __LANG__, M = __MSGS__, R = __RATES__, P = __PEND__, U = __UNITS__;
function pick(v, n){ if (v && typeof v === 'object') return (n === 1 && v.one != null) ? v.one : (v.other != null ? v.other : ''); return v; }
function T(k, p){ var s = M.hasOwnProperty(k) ? M[k] : null; if (s == null) return '';
  s = pick(s, p ? p.n : undefined);
  if (p) s = String(s).replace(/\{(\w+)\}/g, function(m, x){ return p[x] != null ? p[x] : m; });
  return s; }
function TP(pre){ var o = {}; for (var k in M) if (k.indexOf(pre) === 0) o[k.slice(pre.length)] = M[k]; return o; }
var es = L === 'es', ja = L === 'ja', ko = L === 'ko';
function dec(s){ return es ? String(s).replace('.', ',') : String(s); }
function num(v, d){ if (typeof v !== 'number' || !isFinite(v)) return '—';
  if (d == null) d = (Math.abs(v) >= 100 || v % 1 === 0) ? 0 : 1;
  return v.toLocaleString(__INTL__, { minimumFractionDigits: d, maximumFractionDigits: d }); }
function mc(b){ if (typeof b !== 'number' || !isFinite(b)) return '—';
  if (ja) return b >= 1000 ? (b / 1000).toFixed(2) + '兆ドル' : Math.round(b * 10).toLocaleString('ja-JP') + '億ドル';
  var pre = es ? 'US$' : '$';
  return b >= 1000 ? pre + dec((b / 1000).toFixed(2)) + 'T' : pre + Math.round(b) + 'B'; }
var CCY = (function(){ var d = __CCY__; try { var s = localStorage.getItem('uvx-ccy'); if (s && R[s]) d = s; } catch (e) {} return d; })();
function local(b){ if (typeof b !== 'number' || !isFinite(b)) return '';
  var r = R[CCY]; if (CCY === 'USD' || !r) return '';
  var v = b * r;                                 // 십억 단위 현지 통화
  if (CCY === 'KRW') { var jo = v / 1000; return ko ? (jo >= 1000 ? (jo / 1000).toFixed(1) + U.cheonjo_won : jo.toFixed(0) + U.jo_won)
      : ja ? Math.round(v * 10).toLocaleString('ja-JP') + '億ウォン' : '₩' + dec((v / 1000).toFixed(0)) + 'T'; }
  if (CCY === 'JPY') { var cho = v / 1000; return ja ? (cho >= 100 ? cho.toFixed(0) : cho.toFixed(1)) + '兆円'
      : ko ? Math.round(cho).toLocaleString('ko-KR') + U.jo_yen : '¥' + dec(cho.toFixed(cho >= 100 ? 0 : 1)) + 'T'; }
  var sym = CCY === 'EUR' ? '€' : CCY === 'MXN' ? 'MX$' : CCY + ' ';
  if (ja) return (v >= 1000 ? (v / 1000).toFixed(2) + '兆' : Math.round(v * 10).toLocaleString('ja-JP') + '億') + (CCY === 'EUR' ? 'ユーロ' : 'ペソ');
  if (ko) return sym + (v >= 1000 ? (v / 1000).toFixed(2) + 'T' : Math.round(v) + 'B');
  return sym + (v >= 1000 ? dec((v / 1000).toFixed(2)) + 'T' : Math.round(v) + 'B'); }
function bn(v){ if (typeof v !== 'number' || !isFinite(v)) return '—';   /* 십억 달러 — i18n.usd_bn 과 같은 규칙 */
  var sg = v < 0 ? '−' : '', a = Math.abs(v);
  if (ko) return a >= 1000 ? sg + '$' + (a / 1000).toFixed(1) + U.jo : sg + '$' + Math.round(a * 10).toLocaleString('ko-KR') + U.eok;
  if (ja) return a >= 1000 ? sg + (a / 1000).toFixed(1) + '兆ドル' : sg + Math.round(a * 10).toLocaleString('ja-JP') + '億ドル';
  var pre = es ? 'US$' : '$';
  return a >= 1000 ? sg + pre + dec((a / 1000).toFixed(1)) + 'T' : sg + pre + Math.round(a).toLocaleString(__INTL__) + 'B'; }
function pct(v, d){ return (typeof v !== 'number' || !isFinite(v)) ? '—' : dec((v * 100).toFixed(d == null ? 0 : d)) + '%'; }
var MON = { en: ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],
            es: ['ene','feb','mar','abr','may','jun','jul','ago','sept','oct','nov','dic'] };
function date(iso){ var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || '')); if (!m) return iso ? String(iso) : '—';
  var y = +m[1], mo = +m[2], d = +m[3];
  if (ko) return m[1] + '.' + m[2] + '.' + m[3];
  if (L === 'en') return MON.en[mo - 1] + ' ' + d + ', ' + y;
  if (es) return d + ' ' + MON.es[mo - 1] + ' ' + y;
  return y + '年' + mo + '月' + d + '日'; }
function md(iso){ var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || '')); if (!m) return '—';
  var mo = +m[2], d = +m[3]; return L === 'en' ? MON.en[mo - 1] + ' ' + d : es ? d + '/' + mo : mo + '/' + d; }
var HAN = /[\uac00-\ud7a3]/;
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
/* 콘텐츠 문자열 — 번역이 아직 없는 원문(한국어)이면 lang=ko 표식으로 감싼다. 화면 문구(chrome)에는 쓰지 않는다 */
function TX(s){ var h = esc(s); return (!ko && HAN.test(String(s || ''))) ? '<span lang="ko" data-uvx-pending="1">' + h + '</span>' : h; }
/* 이미 HTML 인 콘텐츠 덩어리(칼럼 본문 등) — 원문이면 블록 표식 + '번역 준비 중' 한 줄 */
function TXB(html, tag){ tag = tag || 'div';
  if (ko || !HAN.test(String(html || '').replace(/<[^>]*>/g, ''))) return html;
  return '<' + tag + ' lang="ko" data-uvx-pending="1"><span class="uvx-pending" lang="' + L + '">' + esc(P) + '</span>' + html + '</' + tag + '>'; }
/* 사용자 작성 글(관제센터·관측노트) — 번역하지 않는다. 페이지 언어와 다른 언어면 lang 표식 + 작은 언어 칩 */
var KANA = /[぀-ヿ]/;
function UT(s){ var str = String(s == null ? '' : s), h = esc(str), lg = HAN.test(str) ? 'ko' : KANA.test(str) ? 'ja' : '';
  if (!lg || lg === L) return h;
  return '<span lang="' + lg + '" data-uvx-user="1">' + h + '</span><sup class="uvx-ut">' + lg.toUpperCase() + '</sup>'; }
/* 한국어 고유명사(언론사·매체 이름) — 옮기지 않는다. 번역 대기가 아니므로 표식은 lang 만 */
function PN(s){ var h = esc(s); return (!ko && HAN.test(String(s || ''))) ? '<span lang="ko">' + h + '</span>' : h; }
window.T = T; window.TP = TP; window.TX = TX; window.TXB = TXB; window.UT = UT; window.PN = PN;
window.UVX = { lang: L, T: T, num: num, mc: mc, bn: bn, local: local, pct: pct, dec: dec, date: date, md: md, short: __SHORT__,
  esc: esc, rates: R, ccy: CCY, intl: __INTL__ };
})();
"""


# 화면 서식기의 언어별 단위 낱말 — 그 언어 페이지에는 그 언어 글자만 싣는다(en 산출물에 '조원'이 남지 않게)
UNITS = {"ko": {"cheonjo_won": "천조원", "jo_won": "조원", "jo_yen": "조 엔", "jo": "조", "eok": "억"}}


def runtime_js(lang, keys, rates=None):
    """head 첫머리에 심는 <script id="uvx-i18n-v1"> — 그 페이지가 쓰는 키만 싣는다."""
    msgs = {k: catalog(lang).get(k, catalog(DEFAULT).get(k)) for k in keys}
    js = (UVX_JS.replace("__LANG__", json.dumps(lang))
          .replace("__MSGS__", json.dumps(msgs, ensure_ascii=False))
          .replace("__RATES__", json.dumps(rates or {}, sort_keys=True))
          .replace("__PEND__", json.dumps(t(lang, "common.pending"), ensure_ascii=False))
          .replace("__CCY__", json.dumps(DEFAULT_CCY[lang]))
          .replace("__INTL__", json.dumps(INTL[lang]))
          .replace("__SHORT__", json.dumps(short_names(lang), ensure_ascii=False, sort_keys=True))
          .replace("__UNITS__", json.dumps(UNITS.get(lang, {}), ensure_ascii=False)))
    # 스크립트 블록 안 — 문자열 속 종료 태그가 블록을 닫지 못하게
    js = js.replace("</", "<\\/")
    return '<script id="uvx-i18n-v1">' + js + "</script>"


def has_hangul(s):
    return bool(HANGUL.search(s or ""))
