#!/usr/bin/env python3
"""
i18n_gates.py — 번역 관문 묶음 (단계 3 · 콘텐츠 번역이 게시되기 전 마지막 다리)
================================================================================
막으려는 것 한 문장: **번역이 원문에 없는 것을 만들거나, 원문에 있는 것을 바꾸는 것.**
그래서 관문은 전부 '원문 ↔ 번역' 대조다(표면 특징 단독 판정 금지 — 방지선 지뢰).

  numbers    — 숫자 규율(i18n_numbers → companion_essays.check_numbers)을 양방향으로. 단위 환산 포함
  glossary   — 원문에 사이트 고유 용어(용어 사전)가 있으면 번역에 그 언어 대응어가 있어야 한다
  compliance — 번역이 **권고 문장을 새로 만들지 않는다**: 번역의 권고 목적 문장 수 ≤ 원문의 수
               (원문이 제3자의 'Sell' 헤드라인을 전하는 것까지 막으면 정상이 막힌다)
  structure  — 마크업 골격(태그 이름 순서)이 같다 · 보호 블록 자리표시자가 정확히 한 번씩
  links      — href 집합이 같다(링크를 지어내거나 빠뜨리지 않는다)

check(src, dst, lang, src_lang) → 위반 사유 목록(빈 목록 = 통과). 사유 문장은 재생성 프롬프트에
그대로 들어간다 — 모델이 무엇을 고쳐야 하는지 알 수 있게 구체적으로.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import i18n_compliance as G
import i18n_numbers as N

GLOSSARY = Path(__file__).parent.parent / "data" / "i18n" / "glossary.json"
# 관문이 강제하는 것은 사이트 고유명(페이지·지표 이름)뿐 — '태양'·'리서치'·'통과' 같은 일반 낱말은
# 문맥마다 자연스러운 말이 달라 강제하면 오탐이 난다(정상 문장으로 먼저 잼, 2026-10-26)
GLOSSARY_SKIP = {"sun", "research", "zone_pass"}

_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_MDLINK = re.compile(r"\]\((https?://[^\s)]+)\)")
PLACEHOLDER = re.compile(r"⟦B(\d+)⟧")
# 번역에 보내지 않는 블록 — 차트·그림·코드. 자리표시자로 바꿔 보내고 돌아오면 원문 그대로 되돌린다
PROTECT = re.compile(r"<svg\b[\s\S]*?</svg>|<figure\b[\s\S]*?</figure>|<pre\b[\s\S]*?</pre>|"
                     r"<div class=\"(?:ud-chart|col-chart|ce-chart)[^\"]*\"[\s\S]*?</div>\s*</div>", re.I)


@lru_cache(maxsize=1)
def glossary_terms():
    try:
        doc = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [t for t in doc.get("terms", []) if t.get("id") not in GLOSSARY_SKIP]


def _stem(word, lang):
    """en·es 복수형을 단수와 같이 본다(Soberanos ↔ Soberano)."""
    w = word.lower()
    if lang in ("en", "es") and len(w) > 4 and w.endswith("s"):
        return w[:-1]
    return w


def glossary(src, dst, lang, src_lang="ko"):
    out = []
    low = (dst or "").lower()
    for t in glossary_terms():
        a, b = t.get(src_lang), t.get(lang)
        if not a or not b or a not in (src or ""):
            continue
        if _stem(b, lang) not in low:
            out.append(f"용어 사전: 원문의 '{a}' 는 '{b}' 로 옮겨야 한다")
    return out


def numbers(src, dst, lang, src_lang="ko"):
    extra, missing = N.check(src, dst, lang, src_lang=src_lang)
    out = []
    if extra:
        out.append("숫자: 원문에 없는 숫자 " + ", ".join(map(str, extra[:6])))
    if missing:
        out.append("숫자: 원문의 숫자가 빠짐 " + ", ".join(map(str, missing[:6])))
    return out


def compliance(src, dst, lang, src_lang="ko"):
    adv_src = G.violations(src, src_lang)
    adv_dst = G.violations(dst, lang)
    if len(adv_dst) > len(adv_src):
        return ["준법: 원문에 없는 권고 표현(매매·비중·목표가) — " + " / ".join(adv_dst[:2])]
    return []


def tags(html):
    return [("/" if m.group(1) else "") + m.group(2).lower() for m in _TAG.finditer(html or "")]


def structure(src, dst, lang=None, src_lang=None):
    out = []
    if tags(src) != tags(dst):
        out.append("마크업: 태그 골격이 원문과 다르다(태그를 더하거나 빼지 말 것) — 원문 "
                   + " ".join(tags(src)[:12]))
    ps, pd = PLACEHOLDER.findall(src or ""), PLACEHOLDER.findall(dst or "")
    if sorted(ps) != sorted(pd):
        out.append("마크업: 보호 블록 자리표시자(⟦B숫자⟧)를 원문 그대로 한 번씩 둘 것")
    return out


def links(src, dst, lang=None, src_lang=None):
    a = set(_HREF.findall(src or "")) | set(_MDLINK.findall(src or ""))
    b = set(_HREF.findall(dst or "")) | set(_MDLINK.findall(dst or ""))
    if a != b:
        return ["링크: href 가 원문과 다르다 — 원문 링크를 그대로 둘 것"]
    return []


GATES = (("numbers", numbers), ("glossary", glossary), ("compliance", compliance),
         ("structure", structure), ("links", links))


def check(src, dst, lang, src_lang="ko"):
    """→ [(관문 이름, 사유)] — 빈 목록이면 통과."""
    out = []
    for name, fn in GATES:
        for why in fn(src, dst, lang, src_lang):
            out.append((name, why))
    return out


def _md_kinds(md):
    out = []
    for ln in (md or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        m = re.match(r"(#{1,6})\s", t)
        out.append("h" + str(len(m.group(1))) if m else "|" if t.startswith("|") else
                   "-" if re.match(r"[-*+]\s", t) else "1." if re.match(r"\d+[.)]\s", t) else
                   ">" if t.startswith(">") else "p")
    return out


def md_shape(src, dst):
    """마크다운 골격(머리글 단계·목록·표·인용 줄의 순서)이 같아야 한다 — 표가 깨지면 화면이 깨진다."""
    a, b = _md_kinds(src), _md_kinds(dst)
    if [k for k in a if k != "p"] != [k for k in b if k != "p"]:
        return ["마크다운: 머리글·목록·표 줄 구성이 원문과 다르다(줄을 합치거나 나누지 말 것)"]
    return []


_SVG_TEXT = re.compile(r"(<(?:text|tspan|title)\b[^>]*>)([^<]*[가-힣][^<]*)(?=</(?:text|tspan|title)>)")


def svg_texts(block):
    """보호 블록 속 한글 글자 노드(차트 라벨·제목) — 도형·좌표는 그대로 두고 글자만 번역에 보낸다."""
    return [m.group(2) for m in _SVG_TEXT.finditer(block or "")]


def svg_fill(block, texts):
    """svg_texts 순서대로 번역을 끼운다. 번역이 없으면(None) 원문 그대로 — 관문이 한글 잔존으로 잡는다."""
    it = iter(texts)

    def sub(m):
        t = next(it, None)
        return m.group(1) + (_html_esc(t) if isinstance(t, str) and t.strip() else m.group(2))
    return _SVG_TEXT.sub(sub, block or "")


def _html_esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def protect(html):
    """보호 블록 → 자리표시자. (보낼 문자열, 블록 목록)."""
    blocks = []

    def sub(m):
        blocks.append(m.group(0))
        return f"⟦B{len(blocks) - 1}⟧"
    return PROTECT.sub(sub, html or ""), blocks


def restore(text, blocks):
    return PLACEHOLDER.sub(lambda m: blocks[int(m.group(1))] if int(m.group(1)) < len(blocks) else "", text or "")
