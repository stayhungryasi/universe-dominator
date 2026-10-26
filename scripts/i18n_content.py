#!/usr/bin/env python3
"""
i18n_content.py — 콘텐츠 번역의 '무엇을' (단계 3) — 수집(collect)과 적용(apply)
================================================================================
번역 엔진(translate_content.py)과 빌드(build_site.py)가 **같은 표**를 본다. 두 벌이 되면
'번역한 칸'과 '화면에 끼우는 칸'이 갈라진다 — 그래서 종류(kind)별 명세는 여기 한 곳(SPECS).

명세 한 줄 = 문서 · 항목 목록 경로 · id · 날짜 · 옮길 칸
  ko 칸  : 한국어로 쓰인 우리 글(칼럼·에세이·해부 문단·요약·설명) → en·es·ja
  en 칸  : 영어 원문(신호·리서치·해부 원제, 개척자 헤드라인) → es·ja 만(en 은 원문 그대로).
           한국어를 거치지 않는다(지시: 영어 원문에서 직접)
칸 경로는 점 표기, '*' 는 목록 전체('sections.*.h').

캐시: data/i18n_cache/{lang}/{kind}/{id}.json
  {"src_hash", "status": "ok"|"failed", "fields": {칸 경로: 번역}, "model", "at", "reasons", "attempts"}
  src_hash 는 **그 언어가 옮기는 칸들의 원문**으로 잰다 — 원문이 바뀌면 캐시는 무효(원문 표식으로 돌아간다)
적용(apply)은 status=ok 이고 src_hash 가 지금 원문과 같을 때만. 아니면 원문 그대로(+ 화면의 '번역 준비 중' 표식).
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
CACHE = DATA / "i18n_cache"
KST = timezone(timedelta(hours=9))
TARGETS = ("en", "es", "ja")


def _h(*parts):
    return hashlib.sha1("|".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()[:12]


def _date(s):
    """'2026.10.26' · '2026-10-26T…' · RFC822 → 'YYYY-MM-DD'. 못 읽으면 ''."""
    s = str(s or "")
    m = re.search(r"(20\d{2})[-.](\d{1,2})[-.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _essay_public(e):
    try:
        import companion_essays as ce
        return ce.is_public(e)
    except Exception:
        return e.get("status") != "withdrawn"


def _doc_date(doc):
    for k in ("generated_at", "generated_label", "updated", "fetched_date", "asof"):
        d = _date((doc or {}).get(k) if isinstance(doc, dict) else "")
        if d:
            return d
    return ""


# kind → 명세. list: 항목 목록 경로('' = 문서 자체가 한 항목) · ko/en: 옮길 칸 · fact: 사실 모델
# date: 최신순 우선순위의 근거. 빈 날짜 = 정적 문서(메가트렌드) — 엔진이 오늘로 친다(늘 보이는 페이지)
SPECS = {
    "column":     {"file": "columns.json", "list": "columns", "id": lambda x: _h(x.get("url"), x.get("title")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("title", "summary", "body"), "html": ("body",)},
    "essay":      {"file": "essays.json", "list": "essays", "id": lambda x: x.get("slug") or _h(x.get("title")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("title", "html"), "html": ("html",),
                   "keep": _essay_public,
                   "fact": lambda x: x.get("event_type") in ("실적", "계약", "조달")},
    "correction": {"file": "essays.json", "list": "corrections", "id": lambda x: _h(x.get("slug"), x.get("text")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("text",)},
    "dissect":    {"file": "translations.json", "list": "items", "id": lambda x: x.get("id") or _h(x.get("url")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("sections.*.h", "sections.*.ko", "caveat"),
                   "en": ("title",)},
    "research":   {"file": "research.json", "list": "stocks.*.articles", "id": lambda x: _h(x.get("link")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("summary_ko.*",), "en": ("title",)},
    "signal":     {"file": "signals.json", "list": "signals", "id": lambda x: _h(x.get("url"), x.get("title")),
                   "date": lambda x, d: _date(x.get("captured") or x.get("pub")), "ko": ("why",), "en": ("title",)},
    "pioneer":    {"file": "pioneers.json", "list": "people", "id": lambda x: x.get("id") or _h(x.get("en")),
                   "date": lambda x, d: _doc_date(d), "ko": ("role", "note")},
    "pioneer_news": {"file": "pioneers.json", "list": "people.*.items", "id": lambda x: _h(x.get("url"), x.get("title")),
                     "date": lambda x, d: _date(x.get("date") or x.get("pub")), "en": ("title",)},
    "mega_theme": {"file": "megatrend.json", "list": "themes", "id": lambda x: "t" + str(x.get("num")),
                   "date": lambda x, d: "", "ko": ("title", "subtitle", "stats.*", "insight")},
    "mega_throne": {"file": "megatrend.json", "list": "throne", "id": lambda x: "y" + str(x.get("year")),
                    "date": lambda x, d: "", "ko": ("narrative",)},
    "guru":       {"file": "gurus.json", "list": "gurus", "id": lambda x: str(x.get("cik") or _h(x.get("entity"))),
                   "date": lambda x, d: _doc_date(d), "ko": ("name_ko", "person", "tagline")},
    "desc":       {"file": "latest.json", "list": "regions.*.stocks", "id": lambda x: str(x.get("ticker")),
                   "date": lambda x, d: _date((d.get("meta") or {}).get("fetched_date")), "ko": ("desc",)},
    "latent":     {"file": "latest.json", "list": "latent", "id": lambda x: str(x.get("ticker")),
                   "date": lambda x, d: _date((d.get("meta") or {}).get("fetched_date")), "ko": ("desc", "story"),
                   "keep_field": lambda x, f: f != "story" or x.get("story_auto") is False},
    "calendar":   {"file": "calendar.json", "list": "events", "id": lambda x: _h(x.get("date"), x.get("title")),
                   "date": lambda x, d: _date(x.get("date")), "ko": ("title",),
                   "keep": lambda x: x.get("type") != "earnings"},
}


# ── 경로 ───────────────────────────────────────────────────────────────────
def walk(node, pattern):
    """점 표기 경로('a.*.b') → [(상대 경로 문자열, 값)]. '*' 는 목록·사전 전체."""
    parts = [p for p in pattern.split(".") if p] if pattern else []
    out = []

    def go(n, i, acc):
        if i == len(parts):
            out.append((".".join(acc), n))
            return
        p = parts[i]
        if p == "*":
            if isinstance(n, list):
                for j, v in enumerate(n):
                    go(v, i + 1, acc + [str(j)])
            elif isinstance(n, dict):
                for k, v in n.items():
                    go(v, i + 1, acc + [k])
        elif isinstance(n, dict) and p in n:
            go(n[p], i + 1, acc + [p])
        elif isinstance(n, list) and p.isdigit() and int(p) < len(n):
            go(n[int(p)], i + 1, acc + [p])
    go(node, 0, [])
    return out


def set_path(node, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _has_hangul(s):
    return bool(re.search(r"[가-힣]", s or ""))


def fields_for(spec, item, lang):
    """그 언어가 옮길 칸 → {경로: (원문, 원문 언어, 형식 'html'|'md'|'')}. 빈 칸·이미 그 언어인 칸은 뺀다."""
    out = {}
    keepf = spec.get("keep_field") or (lambda x, f: True)
    for src_lang in ("ko", "en"):
        if src_lang == "en" and lang == "en":
            continue                      # 영어 원문은 en 화면에 그대로
        for pat in spec.get(src_lang, ()):
            if not keepf(item, pat.split(".")[0]):
                continue
            for path, v in walk(item, pat):
                if not isinstance(v, str) or not v.strip():
                    continue
                if src_lang == "ko" and not _has_hangul(v):
                    continue              # 한국어 칸인데 한글이 없으면(영문 이름 등) 옮길 것이 없다
                if src_lang == "en" and _has_hangul(v):
                    continue
                top = pat.split(".")[0]
                mode = "html" if top in spec.get("html", ()) else "md" if top in spec.get("md", ()) else ""
                out[path] = (v, src_lang, mode)
    return out


def src_hash(fields):
    return _h(json.dumps({k: v[0] for k, v in sorted(fields.items())}, ensure_ascii=False))


def items(kind, doc):
    """문서 → [(id, 날짜, 항목 노드)]. keep 이 거른 항목(철회 등)은 빠진다."""
    spec = SPECS[kind]
    keep = spec.get("keep") or (lambda x: True)
    nodes = [v for _, v in walk(doc, spec["list"])] if spec["list"] else [doc]
    out = []
    for lst in nodes:
        for x in (lst if isinstance(lst, list) else []):
            if isinstance(x, dict) and keep(x):
                out.append((spec["id"](x), spec["date"](x, doc), x))
    return out


def load_doc(spec, data_dir=None):
    p = Path(data_dir or DATA) / spec["file"]
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── 캐시 ───────────────────────────────────────────────────────────────────
def cache_path(lang, kind, iid, cache_dir=None):
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(iid))[:80]
    return Path(cache_dir or CACHE) / lang / kind / f"{safe}.json"


def read_cache(lang, kind, iid, cache_dir=None):
    try:
        return json.loads(cache_path(lang, kind, iid, cache_dir).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_cache(lang, kind, iid, rec, cache_dir=None):
    p = cache_path(lang, kind, iid, cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def purge(kind, iid, cache_dir=None):
    """모든 언어에서 **동시에** 지운다(철회는 한 언어만 남으면 안 된다). 지운 수."""
    n = 0
    for lang in TARGETS:
        p = cache_path(lang, kind, iid, cache_dir)
        if p.exists():
            p.unlink()
            n += 1
    return n


# ── 적용(빌드) ──────────────────────────────────────────────────────────────
def apply(kind, lang, doc, cache_dir=None):
    """문서 사본에 그 언어 번역을 끼운다(원문 해시가 같은 ok 캐시만). → (문서, 적용 항목 수)."""
    if lang not in TARGETS or doc is None:
        return doc, 0
    doc = json.loads(json.dumps(doc, ensure_ascii=False))
    spec, n = SPECS[kind], 0
    for iid, _, node in items(kind, doc):
        f = fields_for(spec, node, lang)
        if not f:
            continue
        rec = read_cache(lang, kind, iid, cache_dir)
        if not rec or rec.get("status") != "ok" or rec.get("src_hash") != src_hash(f):
            continue
        for path, val in (rec.get("fields") or {}).items():
            if path in f and isinstance(val, str):
                set_path(node, path, val)
        n += 1
    return doc, n


# 탐사 보고서 공개분 — 데이터 파일이 아니라 probe_cockpit.report_nodes 가 항목을 준다(절 단위)
PROBE = {"ko": ("title", "md"), "md": ("md",), "fact": lambda x: True}
# 동행 논제 원장(data/thesis/*.md, 판단층 — 읽기만) — build_site.thesis_node 가 항목을 준다
THESIS = {"ko": ("one", "body"), "md": ("body",), "fact": lambda x: True}


def apply_node(kind, lang, iid, node, spec, cache_dir=None):
    """항목 하나에 번역을 끼운 사본(ok 캐시·원문 해시 일치일 때만). 없으면 원본 그대로."""
    if lang not in TARGETS:
        return node
    f = fields_for(spec, node, lang)
    rec = read_cache(lang, kind, iid, cache_dir) if f else None
    if not rec or rec.get("status") != "ok" or rec.get("src_hash") != src_hash(f):
        return node
    node = json.loads(json.dumps(node, ensure_ascii=False))
    for path, val in (rec.get("fields") or {}).items():
        if path in f and isinstance(val, str):
            set_path(node, path, val)
    return node


def apply_all(lang, docs, cache_dir=None):
    """{파일명: 문서} 를 받아 그 파일을 쓰는 모든 kind 를 적용한다."""
    out = dict(docs)
    for kind, spec in SPECS.items():
        if spec["file"] in out:
            out[spec["file"]], _ = apply(kind, lang, out[spec["file"]], cache_dir)
    return out
