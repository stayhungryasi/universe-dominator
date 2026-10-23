#!/usr/bin/env python3
"""
동행 관측 (companion_essays) — 3사 10년 논제를 관찰로 쌓는 에세이 엔진
================================================================================
이것은 뉴스 스크랩이 아니다. 한 기업의 10년 항로에 대한 **확신을 관찰로 쌓는**
기록이며, 시차 관측과 같은 '판단층 / 기계층' 분리 원칙을 따른다.

  판단층 (기계가 절대 건드리지 않는다 — 채팅 취재로만 갱신)
    data/thesis/{slug}.md          논제 원장. 핵심 가설과 반증 조건.
    scripts/prompts/companion_essayist.txt   에세이 헌법 프롬프트.
  기계층 (파이프라인이 쓴다)
    data/essays.json               발행 에세이 원장 (columns.json 규칙 — 원격 최신 기준)
    data/companion_state.json      발행 상한·소재 처리 이력

핵심 규율 (프롬프트 헌법과 같은 뿌리):
  · **기본값은 침묵.** 5축(실적변곡·기술임계·해자변화·자본배분과인물·규제구조)에
    실질적으로 닿지 않으면 쓰지 않는다. 이 노트의 가치는 편수가 아니라 밀도다.
  · **논제 원장이 플레이스홀더면 그 회사는 집필하지 않는다.**
    논제 없는 에세이는 스크랩이다 — 관찰 대기 로그만 남긴다.
  · **인용 규율(원문 대조)**: 막으려는 것은 따옴표가 아니라 스크랩이다. 수집된
    텍스트에 실제로 있는 구절만 인용으로 세고(영문 15단어·한국어 60자·출처당 1회),
    따옴표 없이 원문을 통째 옮긴 것도 잡는다. 위반하면 사유를 주고 1회 재생성,
    그래도 걸리면 보류한다.
  · 회사당 하루 1편 (직접 게재는 상한 제외) · signal_column 식 이중 잠금.

소재함(Firestore materials): 소장이 관측노트에서 던진 링크·메모를 우선 처리한다.
  mode="essay" → 원문 추출 후 집필 · mode="direct" → 소장 글 그대로 발행(AI 무개입)
  처리 후 consumed=true 로 마킹한다. **삭제하지 않는다** — 소급 삭제 금지 원칙.
"""
import json
import os
import random
import re
import string
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
THESIS_DIR = DATA_DIR / "thesis"
SRC_PATH = DATA_DIR / "companion_sources.json"
ESSAYS_PATH = DATA_DIR / "essays.json"
STATE_PATH = DATA_DIR / "companion_state.json"
FB_CFG_PATH = DATA_DIR / "firebase_config.json"
PROMPT_PATH = Path(__file__).parent / "prompts" / "companion_essayist.txt"

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (UNIVERTRIX companion-observatory; univertrix.com)"}
MODEL = "claude-haiku-4-5"            # 기본 집필 모델 — COMPANION_MODEL 로 덮어쓴다
MODEL_FACT = "claude-sonnet-5-5"      # 숫자 중심 사건(실적·계약·조달) — COMPANION_MODEL_FACT 로 덮어쓴다
MATERIALS = "materials"
VERDICTS = ("강화", "불변", "약화", "판단 보류")
DISCLAIMER = ("본 동행 관측은 정보 제공 목적의 기록이며 특정 종목의 매수·매도 "
              "권유가 아닙니다. 판단과 손익은 독자 본인에게 귀속됩니다.")
ANTHROPIC_COI = ("이해관계 고지: 이 사이트의 자동 분석은 Anthropic 의 모델로 작성됩니다. "
                 "Anthropic 에세이는 공식 발표와 제3자 보도만을 근거로 쓰며, "
                 "칭찬과 비판에 같은 증거 기준을 적용합니다.")


# ────────────────────────────────────────────────────────────────
# 판단층 읽기 — 절대 쓰지 않는다
# ────────────────────────────────────────────────────────────────

# 원장 머리글 — 소장이 쓰는 형식이 곧 표준이다. 기계가 형식에 맞추지,
# 판단층을 기계 편의로 고치지 않는다.
#   인용줄 형식(v1 이후):  > v1.0 · 2026-08-22 · 승인: 소장 · status: active
#   YAML 형식(플레이스홀더 세대):  --- \n version: 0 \n status: placeholder \n ---
_META_LINE = re.compile(r"^>\s*v([0-9][0-9.]*)\s*[·|]\s*(\d{4}-\d{2}-\d{2})", re.M)
_STATUS_IN_LINE = re.compile(r"status\s*:\s*([A-Za-z]+)")


def read_thesis(slug):
    """논제 원장 → {"raw","body","version","updated","placeholder"}.

    파이프라인은 이 파일을 **읽기만** 한다. 쓰기 경로는 존재하지 않는다.
    """
    p = THESIS_DIR / f"{slug}.md"
    if not p.exists():
        return {"raw": "", "body": "", "version": "0", "updated": "", "placeholder": True}
    raw = p.read_text(encoding="utf-8")
    meta, body = {}, raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
    if m:                                     # 구 YAML 머리글
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    else:                                     # 인용줄 머리글
        hm = _META_LINE.search(raw)
        if hm:
            meta["version"], meta["updated"] = hm.group(1), hm.group(2)
            sm = _STATUS_IN_LINE.search(hm.group(0) + raw[hm.end():hm.end() + 120])
            if sm:
                meta["status"] = sm.group(1)

    status = meta.get("status", "").lower()
    # 판정 순서가 중요하다: status 가 명시돼 있으면 그것이 진실이고,
    # 없을 때만 본문의 취재중 표식을 본다 (v1 본문에 '논제 취재 중'이라는
    # 말이 인용으로 섞여도 원장이 잠기지 않게).
    if status:
        placeholder = (status == "placeholder")
    else:
        placeholder = "논제 취재 중" in raw
    return {"raw": raw, "body": body.strip(), "version": meta.get("version", "0"),
            "updated": meta.get("updated", ""), "placeholder": placeholder}


def load_prompt():
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    print("[동행] 에세이 헌법 프롬프트 없음 — 집필 불가", file=sys.stderr)
    return ""


# ────────────────────────────────────────────────────────────────
# 기계층 원장
# ────────────────────────────────────────────────────────────────

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_essays():
    d = load_json(ESSAYS_PATH, {"essays": []})
    d.setdefault("essays", [])
    return d


def save_essays(d):
    now = datetime.now(KST)
    d["generated_label"] = now.strftime("%Y.%m.%d %H:%M")
    ESSAYS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")


def load_state():
    st = load_json(STATE_PATH, {})
    st.setdefault("published", {})     # {slug: "YYYY-MM-DD"}
    st.setdefault("materials", [])     # 처리한 소재 문서 id (재처리 방지)
    st.setdefault("materials_pending", 0)   # 이번 회차에 소비하지 못한 소재 수 (-1=접근 실패)
    st.setdefault("materials_checked", "")
    return st


def save_state(st):
    st["materials"] = st.get("materials", [])[-300:]
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")


def make_slug(company, title, date):
    """에세이 영구 슬러그 — 제목·날짜가 같으면 언제 계산해도 같은 값(멱등)."""
    seed = f"{company}|{title}|{date}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"{company}-{h:08x}"


def is_public(e):
    """공개 대상인가 — 철회(withdrawn)된 에세이는 원장에 남되 어디에도 렌더하지 않는다.

    2026-10-23: 회수 에세이 2편이 원문에 없는 숫자로 게시돼 철회했다. 삭제하지 않는 이유는
    소급 삭제 금지 원칙 — 무엇을 왜 언제 거뒀는지가 기록으로 남아야 한다.
    """
    return isinstance(e, dict) and e.get("status") != "withdrawn"


def withdraw(doc, slugs, reason, when, correction=None):
    """에세이 철회 → 철회한 슬러그 목록. 정정 줄(correction)은 doc.corrections 에 1회만 쌓는다."""
    done = []
    for e in doc.get("essays", []):
        if e.get("slug") in slugs and e.get("status") != "withdrawn":
            e["status"] = "withdrawn"
            e["withdrawn_at"] = when
            e["withdrawn_reason"] = reason
            done.append(e["slug"])
    if correction:
        cs = doc.setdefault("corrections", [])
        if not any(c.get("text") == correction.get("text") for c in cs):
            cs.insert(0, correction)
    if done:
        # 배포 필요 표식 — 데이터만 고치면 루트 HTML(라이브)은 옛 빌드 그대로다(2026-10-23:
        # Anthropic 에세이가 데이터에서만 철회되고 본문이 라이브에 남았다). 표식은 라이브에서
        # 제목 0회를 확인한 뒤에만 지운다(scripts/withdraw_essay.py).
        titles = [e.get("title", "") for e in doc.get("essays", []) if e.get("slug") in done]
        doc.setdefault("pending_deploy", []).append({"slugs": done, "titles": titles, "since": when})
    return done


def withdrawn_titles(doc):
    """철회된 에세이의 (slug, 제목) — 루트 HTML 에 남아 있으면 안 되는 문자열들."""
    return [(e.get("slug", ""), e.get("title", "")) for e in (doc or {}).get("essays", [])
            if isinstance(e, dict) and e.get("status") == "withdrawn" and e.get("title")]


def html_leaks(doc, pages):
    """루트 HTML 에 남은 철회 제목 → [(파일명, slug)]. JSON 이스케이프 형태도 본다."""
    import json as _json
    out = []
    for slug, title in withdrawn_titles(doc):
        forms = {title, _json.dumps(title, ensure_ascii=False)[1:-1],
                 title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}
        for name, raw in pages:
            if any(f and f in raw for f in forms):
                out.append((name, slug))
    return out


def clear_pending(doc, slugs):
    """라이브 확인을 마친 슬러그를 배포 필요 표식에서 지운다 → 남은 표식 수."""
    keep = []
    for p in doc.get("pending_deploy", []) or []:
        rest = [s for s in p.get("slugs", []) if s not in slugs]
        if rest:
            keep.append(dict(p, slugs=rest))
    doc["pending_deploy"] = keep
    return len(keep)


def published_today(essays, slug, today):
    return any(e.get("company") == slug and e.get("date") == today
               and e.get("origin") != "직접" and is_public(e) for e in essays)


# ────────────────────────────────────────────────────────────────
# 인용 규율 — 스크랩 방지선
# ────────────────────────────────────────────────────────────────

_QUOTE_RE = re.compile(r"[\"“「『]([^\"”」』]{4,})[\"”」』]")

EN_WORD_LIMIT = 15      # 영문 원문 인용 상한(단어)
KO_CHAR_LIMIT = 60      # 한국어 원문 인용 상한(글자)
BULK_WINDOW = 60        # 따옴표 밖에서 이만큼 연속으로 원문과 같으면 '통째 옮겨쓰기'
MATCH_RATIO = 0.90      # 수집 텍스트와 이 비율 이상 겹치면 원문 인용으로 본다


def _norm(s):
    """공백만 정규화 — 대조는 문자 그대로 해야 오탐이 줄어든다."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _is_latin(s):
    """영문 인용인가 — 라틴 문자 비중으로 판별해 길이 기준을 고른다."""
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return True
    return sum(1 for c in letters if ord(c) < 0x250) / len(letters) >= 0.6


def _from_source(q, src):
    """이 따옴표 구간이 **수집된 텍스트에서 실제로 옮겨온 것**인가.

    핵심: 따옴표가 있다는 표면 특징이 아니라 '원문에서 가져왔는가'라는 정의로
    검사한다. 한국어 글의 "검색 사망론" 같은 강조·용어 표기는 수집 텍스트에
    없으므로 인용이 아니다 — 규율 대상에서 빠진다.
    """
    if not src:
        return False
    if q in src:
        return True
    # 부분 변형(조사·말줄임)까지 잡되, 겹치는 덩어리가 인용의 대부분일 때만.
    try:
        from difflib import SequenceMatcher
        m = SequenceMatcher(None, q, src, autojunk=False).find_longest_match(
            0, len(q), 0, len(src))
        return m.size >= MATCH_RATIO * len(q)
    except Exception:
        return False


def check_quotes(html, source_text, n_sources):
    """인용 규율 — **원문 대조 방식**. 위반 목록을 돌려준다(빈 목록 = 통과).

    막으려는 것은 '따옴표'가 아니라 '스크랩'이다. 그래서 검사도 그 정의로 한다:
      · 수집된 텍스트(전문을 못 읽었으면 제목·요약)에 실제로 있는 구절만 인용으로 센다
      · 원문 인용은 출처당 1회 · 영문 15단어 / 한국어 60자 미만
      · 따옴표가 없어도 원문을 80자 연속으로 옮겼으면 통째 옮겨쓰기로 본다
    수집 텍스트가 아예 없으면 대조할 근거가 없다 — 그때는 인용 판정을 하지 않는다
    (원문을 못 읽었으면 모델도 인용할 재료가 없었다는 뜻이라 오탐만 남는다).
    """
    body = _norm(html)
    src = _norm(source_text)
    bad = []
    if not src:
        return bad

    quoted = [q.strip() for q in _QUOTE_RE.findall(body) if q.strip()]
    real = [q for q in quoted if _from_source(q, src)]

    for q in real:
        if _is_latin(q):
            n = len(q.split())
            if n >= EN_WORD_LIMIT:
                bad.append(f"긴 인용({n}단어): {q[:40]}…")
        elif len(q) >= KO_CHAR_LIMIT:
            bad.append(f"긴 인용({len(q)}자): {q[:40]}…")

    limit = max(1, int(n_sources or 1))
    if len(real) > limit:
        bad.append(f"원문 인용 {len(real)}회 > 출처 {limit}건 (출처당 1회)")

    # 따옴표 없이 옮겨 적는 쪽이 오히려 스크랩에 가깝다 — 그것도 본다.
    # 인용 구간은 위에서 이미 규율을 통과했으므로 걷어내고 본다. 그러지 않으면
    # 허용된 영문 14단어 인용(≈80자)이 여기서 다시 걸려 오탐이 된다.
    lifted = _bulk_lift(_QUOTE_RE.sub(" ", body), src)
    if lifted:
        bad.append(f"원문 통째 옮겨쓰기({len(lifted)}자 연속): {lifted[:40]}…")
    return bad


# ────────────────────────────────────────────────────────────────
# 숫자 규율 — 원문에 없는 숫자 방지선 (2026-10-23 ⑱)
# ────────────────────────────────────────────────────────────────
# 막으려는 것 한 문장: **수집한 원문에 없는 숫자가 에세이·차트에 실리는 것.**
# 그래서 검사도 그 정의로 한다 — 본문·차트의 숫자 하나하나를 수집 원문 텍스트의 숫자와
# 대조한다(인용 규율과 같은 원문 대조 방식). 표면 특징(숫자 개수·길이)으로 재지 않는다.
#   · 단위 환산: 조·억·만·천 / trillion·billion·million·thousand / T·B·M·K 를 절대값으로
#   · 허용 오차: 표기 자릿수의 반올림 폭(예: 146억 → ±0.5억) 또는 0.5% 중 큰 쪽
#   · 원문 표는 흔히 백만·천 단위로 적힌다 → 단위 없는 원문 숫자는 ×1e6·×1e3 도 후보
#   · 면제: 연도·날짜·분기 표기(3Q26·3분기·Q3), 10 이하 맨 정수(2편·3가지 같은 세기)
_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3,
         "trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6,
         "thousand": 1e3, "t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}
# 숫자 경계(2026-10-23 오탐·구멍 수리): ① '800MHz' 를 '80' 으로 잘라 읽던 것 — 단위는 '뒤에 다른
# 글자가 붙지 않을 때만' 취하는 선택 묶음으로 둔다(MHz 같은 남의 단위면 맨 숫자 800 으로 읽는다).
# ② 한국어에 바로 붙은 숫자('매출145억')를 건너뛰던 것 — 앞 경계에서 한글은 막지 않는다.
# ③ 한국어 단위 글자가 낱말의 첫 글자일 때('Opus 4.6 조기 버전'·'Falcon 9조차')를 단위로 읽던 오탐 —
# 과거 에세이 재검사(2026-10-23 결정 5)에서 정상 문장으로 실측. 단위 뒤에 흔한 낱말 꼬리가 붙으면 단위가 아니다.
_KO_JO = r"조(?![차기건직사정합치성화선약항업작달언금각류심절종례])"
_KO_EOK = r"억(?![제압측울])"
_KO_MAN = r"만(?![큼족약])"
_KO_CHEON = r"천(?![천히문체국재하])"
_NUM_RE = re.compile(
    r"(?<![\d.,A-Za-z])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
    r"(?:\s*(" + _KO_JO + "|" + _KO_EOK + "|" + _KO_MAN + "|" + _KO_CHEON +
    r"|trillion|billion|bn|million|mn|thousand|T|B|M|K)(?![A-Za-z0-9]))?", re.I)
# 한국어 복합 단위 — '1조 4,575억'·'2억 8천만'·'1,360만' 을 하나의 값으로(2026-10-23 오탐 실측:
# '2억 8천만'(= 280 million)을 '2억'과 '8천'으로 쪼개 원문과 못 맞췄다)
_KO_PART = r"(\d[\d,]*(?:\.\d+)?)\s*(" + _KO_JO + "|" + _KO_EOK + "|천만|백만|" + _KO_MAN + ")"
_COMPOUND = re.compile(_KO_PART + r"(?:\s*" + _KO_PART + r")*")
_KO_MULT = {"조": 1e12, "억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4}
# ISO 날짜(2026-10-20)는 맨 앞에 둔다 — 연도 갈래가 '2026' 만 먼저 먹으면 '-10-20' 의 '20' 이
# 숫자로 남아 대조 대상이 됐다(2026-10-23 숫자 목록 테스트에서 발각)
# 점 날짜(2026.9.18 · 2026.10)와 분기+4자리 연도(Q1 2027 · 3Q 2026)도 같은 재검사에서 실측한 구멍 —
# 'Q1 20' 까지만 먹고 '27' 을 숫자로 남겼다. 연도 자리는 4자리를 먼저 시도한다.
_EXEMPT = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}|(?<![\d.,])(?:19|20)\d{2}\.(?:1[0-2]|0?[1-9])(?:\.(?:3[01]|[12]\d|0?[1-9]))?(?![\d.])"
    r"|(?<!\d)(?:19|20)\d{2}\s*년|(?<![\d,.])(?:19|20)\d{2}(?![\d,.]|\s*(?:억|만|조|천|명|건|달러))"
    r"|\d{1,2}/\d{1,2}(?!\d)|\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}\s*월"
    r"|\d{1,2}\s*일(?!\s*(?:만|억))|(?<![A-Za-z])[1-4]Q(?:\s*'?(?:\d{4}|\d{2}))?(?!\d)|Q[1-4](?:\s*'?(?:\d{4}|\d{2}))?(?!\d)|[1-4]\s*분기|[1-4]H\d{2}"
    r"|[①-⑳]|(?<![A-Za-z])V\d\b")


def _num_spans(text):
    """텍스트 → [(값, 허용오차, 원문표기, 시작, 끝)] — 면제 표기는 빼고, 복합 단위(1조 4,575억)는 합친다.

    지운 자리는 같은 길이의 공백으로 채워 위치를 보존한다(숫자 목록의 앞뒤 문맥을 잘라내려고).
    """
    t = _EXEMPT.sub(lambda m: " " * len(m.group(0)), text or "")
    out = []

    def comp(m):
        parts = re.findall(_KO_PART, m.group(0))
        total = sum(float(n.replace(",", "")) * _KO_MULT[u] for n, u in parts)
        n_last, u_last = parts[-1]
        dec = len(n_last.split(".")[1]) if "." in n_last else 0
        out.append((total, 0.5 * 10 ** (-dec) * _KO_MULT[u_last], m.group(0), m.start(), m.end()))
        return " " * len(m.group(0))
    t = _COMPOUND.sub(comp, t)
    plain = []
    for m in _NUM_RE.finditer(t):
        whole, frac, unit = m.group(1), m.group(2), m.group(3)
        v = float(whole.replace(",", "") + ("." + frac if frac else ""))
        mult = _UNIT.get((unit or "").lower(), 1.0) if unit else 1.0
        dec = len(frac) if frac else 0
        raw = m.group(0).strip()
        plain.append([v, dec, mult, raw, m.start(), m.start() + len(raw)])
    # 범위 표기('$175~185B' · '$175 to $185 billion')의 앞 숫자는 뒤 숫자의 단위를 물려받는다 —
    # 과거 에세이 재검사(2026-10-23)에서 '175' 를 맨 숫자로 읽어 원문 '$175 billion' 과 못 맞추던 오탐
    for a, b in zip(plain, plain[1:]):
        if a[2] == 1.0 and b[2] != 1.0 and _RANGE_GAP.match(t[a[5]:b[4]]):
            a[2] = b[2]
    for v, dec, mult, raw, s, e in plain:
        out.append((v * mult, 0.5 * 10 ** (-dec) * mult, raw, s, e))
    return out


_RANGE_GAP = re.compile(r"^\s*(?:[~∼〜–—-]|to)\s*\$?\s*$", re.I)


def _num_tokens(text):
    """텍스트 → [(값, 허용오차, 원문표기)]."""
    return [x[:3] for x in _num_spans(text)]


def _small_count(v, tol):
    """10 이하 맨 정수(2편·3가지 같은 세기) — 대조 면제."""
    return tol == 0.5 and v <= 10 and float(v).is_integer()


NUM_LIST_MAX = 220      # 프롬프트에 넣는 '사용 가능한 숫자' 상한(SEC 원문 표는 숫자가 수백 개)
NUM_CTX = 36            # 숫자 앞뒤로 붙이는 문맥 글자 수


def number_inventory(items, limit=NUM_LIST_MAX, ctx=NUM_CTX):
    """수집 원문 → '사용 가능한 숫자' 목록 [{숫자, 문맥, 원천}] (2026-10-23 E2).

    숫자 규율(check_numbers)이 대조하는 바로 그 텍스트(collected_text 의 재료)에서 뽑는다 —
    목록에 있는 표기는 규율을 통과하고, 목록 밖은 통과하지 못한다. 1순위 원천(SEC)부터, 같은 표기는 한 번.
    """
    out, seen = [], set()
    order = sorted(range(len(items or [])), key=lambda i: not (items[i] or {}).get("primary"))
    for i in order:
        x = items[i] or {}
        text = _norm(" ".join(str(x.get(k) or "") for k in ("title", "article", "raw_desc")))
        for v, tol, raw, s, e in _num_spans(text):
            if _small_count(v, tol) or raw in seen:
                continue
            seen.add(raw)
            left = text[max(0, s - ctx):s].lstrip()
            right = text[e:e + ctx].rstrip()
            out.append({"숫자": raw, "문맥": f"…{left}[{raw}]{right}…", "원천": i + 1})
            if len(out) >= limit:
                return out
    return out


def number_context(body, toks, width=50):
    """위반 숫자 → 원고 속 앞뒤 문맥(재생성 사유·보류 로그용). 토크나이저가 읽은 자리 그대로 찾는다
    ('26' 이 '2026' 안에서 먼저 잡히는 식의 엉뚱한 문맥을 피한다)."""
    plain = _norm(re.sub(r"<[^>]+>", " ", body or ""))
    spans = {}
    for _, _, raw, s, e in _num_spans(plain):
        spans.setdefault(raw, (s, e))
    out = []
    for tok in toks:
        if tok.startswith("차트 "):
            out.append((tok, "(차트)"))
            continue
        s, e = spans.get(tok, (plain.find(tok), plain.find(tok) + len(tok)))
        out.append((tok, plain[max(0, s - width):e + 30] if s >= 0 else ""))
    return out


# 표의 단위 선언 — '$, in millions' · '(# in millions)' · '(단위: 백만원)' 같은 머리말. 그 아래 표 칸만
# 단위 환산 후보가 된다(2026-10-23 구멍 수리: 단위 없는 정수를 무조건 백만·천으로도 읽었더니
# 원문의 'Flight 14' 가 에세이의 '13.6백만' 을 통과시켰다 — 반올림 폭 ±0.5백만이 그대로 따라갔다).
_SCALE_HDR = re.compile(
    r"in\s+(millions|thousands|billions)|\((millions|thousands)\b|"
    r"단위\s*[:：]?\s*(?:US\$|\$|USD\s*)?(백만|천|억)", re.I)
_SCALE_MULT = {"millions": 1e6, "thousands": 1e3, "billions": 1e9, "백만": 1e6, "천": 1e3, "억": 1e8}
SCALE_WIN = 8000        # 단위 선언이 미치는 거리(글자) — 표 하나 길이
# 표 칸 = 이웃 숫자와 사이에 기호·공백만 있는 숫자('5,248 4,291 3,097' · '$(143) $(273)').
# 문장 속 숫자('Flight 14, our first')는 표 칸이 아니다 — 머리말 아래에 있어도 환산하지 않는다.
_CELL_GAP = re.compile(r"^[\s$()%\-–—]*$")


def _scale_at(src, pos, hdrs):
    """pos 위쪽 SCALE_WIN 안의 가장 가까운 단위 선언의 배수(없으면 None)."""
    best = None
    for s, mult in hdrs:
        if s < pos and pos - s <= SCALE_WIN:
            best = mult
    return best


def _source_values(src):
    """원문 숫자 → [(값, 그 표기의 반올림 폭)].

    단위 없는 숫자는 **단위 선언 아래의 표 칸일 때만** 그 단위로 환산한 후보를 더한다.
    단위 문맥이 없는 맨 정수(비행 번호·모델 번호·문장 속 개수)는 적힌 값 그대로만 대조한다.
    """
    hdrs = []
    for m in _SCALE_HDR.finditer(src or ""):
        word = next(g for g in m.groups() if g).lower()
        hdrs.append((m.start(), _SCALE_MULT[word]))
    spans = _num_spans(src)
    vals = []
    for i, (v, tol, raw, s, e) in enumerate(spans):
        vals.append((v, tol))
        if v >= 1e6 or not hdrs:
            continue
        mult = _scale_at(src, s, hdrs)
        if not mult:
            continue
        prev_gap = src[spans[i - 1][4]:s] if i > 0 else None
        next_gap = src[e:spans[i + 1][3]] if i + 1 < len(spans) else None
        if any(g is not None and _CELL_GAP.match(g) for g in (prev_gap, next_gap)):
            vals.append((v * mult, tol * mult))
    return vals


def _matches(v, tol, pool):
    """허용 오차 = 에세이 표기와 원문 표기의 반올림 폭 중 큰 쪽.

    2026-10-23: 처음엔 여기에 '0.5% 상대 오차'를 더 얹었다가, 원문에 없는 'Google $920M'이
    감가상각 922 와 우연히 맞아 통과했다. 반올림 폭이면 단위 환산(146억 ↔ 14,575M)과
    원문 쪽 반올림($14.6 billion ↔ 14,575M) 양쪽이 다 설명된다 — 그 이상은 우연을 허용할 뿐이다.
    """
    for s, stol in pool:
        if abs(abs(v) - abs(s)) <= max(tol, stol):
            return True
    return False


def check_numbers(html, charts, source_text):
    """숫자 규율 — 위반 목록(빈 목록 = 통과). 원문 대조만으로 판정한다."""
    body = _norm(re.sub(r"<[^>]+>", " ", html or ""))
    pool = _source_values(_norm(source_text))
    bad = []
    for v, tol, raw in _num_tokens(body):
        if _small_count(v, tol):
            continue                    # 10 이하 맨 정수 — 세기·순서(2편·3가지)
        if not _matches(v, tol, pool):
            bad.append(raw)
    for ch in charts or []:
        unit = str((ch or {}).get("unit") or "")
        mult = _UNIT.get(unit.strip().lower(), 1.0)
        for row in (ch or {}).get("series") or []:
            try:
                val = float(row.get("value"))
            except (TypeError, ValueError, AttributeError):
                continue
            dec = len(str(row.get("value")).split(".")[1]) if "." in str(row.get("value")) else 0
            if not _matches(val * mult, 0.5 * 10 ** (-dec) * mult, pool):
                bad.append(f"차트 {row.get('label', '')} {row.get('value')}{unit}")
    seen, uniq = set(), []
    for b in bad:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return uniq


def has_numbers(html, charts):
    """원문 대조 대상 숫자가 하나라도 있는가(면제 표기 제외)."""
    body = _norm(re.sub(r"<[^>]+>", " ", html or ""))
    toks = [t for t in _num_tokens(body) if not _small_count(t[0], t[1])]
    return bool(toks) or any((c or {}).get("series") for c in charts or [])


_HARD_EVENT = re.compile(r"실적|분기\s*결과|earnings|results|계약|contract|agreement|8-K", re.I)
# 숫자 중심 사건 — 실적(8-K 2.02)·계약(1.01)·조달. 이 소재는 사실 모델(MODEL_FACT)로 쓴다(2026-10-23 E1:
# 같은 날 Haiku 에세이 3편이 모두 원문에 없는 숫자를 실었다 — 'ARR $65B'·'Google 20M'·166억 차트)
_FACT_EVENT = re.compile(
    r"실적|분기\s*결과|매출|영업이익|earnings|results|revenue|quarter|계약|수주|contract|agreement|award"
    r"|조달|차입|채권|대출|financing|offering|notes|bonds?|loan|credit\s+facility|8-K", re.I)
FACT_KINDS = ("earnings", "contract", "financing")


def models():
    """(기본 모델, 사실 모델) — 저장소 변수 COMPANION_MODEL · COMPANION_MODEL_FACT 로 덮어쓴다."""
    return (os.environ.get("COMPANION_MODEL", "").strip() or MODEL,
            os.environ.get("COMPANION_MODEL_FACT", "").strip() or MODEL_FACT)


def is_fact_event(items):
    """집필 전에 고른다 — SEC 1순위 원천(실적·계약·조달 8-K)이 있거나 후보 제목이 숫자 중심 사건이면 참."""
    for x in items or []:
        if x.get("primary") and x.get("event_kind") in FACT_KINDS:
            return True
    return any(_FACT_EVENT.search(str(x.get("title", ""))) for x in items or [])


def pick_model(items):
    base, fact = models()
    return fact if is_fact_event(items) else base


def is_hard_event(res, items):
    """실적·계약 사건인가 — 숫자가 핵심인 사건은 전문 없이 쓰지 않는다."""
    if str((res or {}).get("event_type", "")).strip() in ("실적", "계약"):
        return True
    text = " ".join([str((res or {}).get("title", ""))] + [str(x.get("title", "")) for x in items or []])
    return bool(_HARD_EVENT.search(text))


def _bulk_lift(body, src, window=BULK_WINDOW):
    """본문에 원문이 window 자 이상 연속으로 그대로 박혀 있으면 그 구간을 돌려준다."""
    if len(body) < window or not src:
        return ""
    step = max(10, window // 3)
    for i in range(0, len(body) - window + 1, step):
        chunk = body[i:i + window]
        if chunk in src:
            return chunk
    return ""


# ────────────────────────────────────────────────────────────────
# 수집 — 신호 관측소 규칙 이식 (우아한 저하)
# ────────────────────────────────────────────────────────────────

def collect_candidates(company, cfg, widen_days=0):
    """회사별 후보 뉴스. 소스 하나가 죽어도 나머지는 살아 있다.

    widen_days > 0 이면 **회수 모드** — 구글뉴스 쿼리의 when:Nd 를 넓히고 상한을
    키운다(놓친 사건 회수 1회 한정, S4). 평시에는 0.
    """
    try:
        from fetch_signals import parse_rss, resolve_gnews, classify_outcome
    except Exception as e:
        print(f"[동행] 수집 유틸 로드 실패 → 자동 수집 생략 ({e})", file=sys.stderr)
        return []
    import feed_client as fc
    from urllib.parse import urljoin
    cap = int(cfg.get("rss_cap", 4))
    scan = int(cfg.get("scan_limit", 12))
    if widen_days:
        cap, scan = cap * 2, scan * 2
    rows = []
    # 라벨에 회사를 담아야 관제탑이 어느 항로의 소스가 죽었는지 말할 수 있다
    for s in fc.interleave_by_host(company.get("sources", [])):
        label = f"{company['slug']}:{s['name']}"
        url = s["url"]
        if widen_days and s.get("type") == "gnews":
            url = widen_query(url, widen_days)
        text, outcome, code = fc.fetch(url, label, "companion", headers=UA)
        if text is None:
            print(f"[동행] {label} 수집 실패 → 건너뜀 ({outcome} {code})", file=sys.stderr)
            continue
        try:
            got = parse_rss(text, s["name"])
            items_raw = len(got)
            stats = {}
            if s.get("type") == "gnews":
                got = resolve_gnews(got, s.get("domain", ""), cap, scan_limit=scan,
                                    stats=stats)
            else:
                age = s.get("max_age_days")
                if age:
                    got = [x for x in got
                           if within_days(x.get("pub", ""), max(int(age), widen_days or 0))]
                got = got[:cap]
                # IR 피드(Q4)는 링크가 상대경로로 섞여 온다 — 기준 주소로 절대화한다
                base = s.get("base") or ""
                for x in got:
                    if base and x.get("url", "").startswith("/"):
                        x["url"] = urljoin(base, x["url"])
            for x in got:
                x.pop("feed_source", None)
            rows.extend(got)
            oc = classify_outcome(items_raw, got, stats)
            fc.record("companion", label, oc, code, len(got), items_raw=items_raw)
            print(f"[동행] {label}: {len(got)}건 (원본 {items_raw}건)")
            if oc == "decode_fail":
                print(f"[동행] {label}: 원본 {items_raw}건인데 디코딩 전멸 — 고장",
                      file=sys.stderr)
            elif not got:
                print(f"[동행] {label}: 0건 — 피드 점검 대상", file=sys.stderr)
        except Exception as e:
            fc.record("companion", label, "http_error", code, 0)
            print(f"[동행] {label} 파싱 실패 → 건너뜀 ({e})", file=sys.stderr)
    fc.flush()
    seen, out = set(), []
    for x in rows:
        if x["url"] in seen:
            continue
        seen.add(x["url"])
        out.append(x)
    return out


def within_days(pub, days, now=None):
    """발행일(RFC822/ISO 혼재)이 최근 days 일 안인가. 못 읽으면 True(버리지 않는다)."""
    from email.utils import parsedate_to_datetime
    now = now or datetime.now(timezone.utc)
    dt = None
    try:
        dt = parsedate_to_datetime(pub)
    except Exception:
        try:
            dt = datetime.fromisoformat((pub or "").replace("Z", "+00:00"))
        except Exception:
            return True
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days <= int(days)


def pub_date(pub):
    """발행일(RFC822/ISO/YYYY-MM-DD 혼재) → date. 못 읽으면 None."""
    from email.utils import parsedate_to_datetime
    s = str(pub or "").strip()
    if not s:
        return None
    for f in (lambda x: parsedate_to_datetime(x), lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            return f(s).date()
        except Exception:
            continue
    return None


def in_focus(item, day, before=2, after=10):
    """회수 초점 사건일(day, YYYY-MM-DD) 근처의 후보인가 — 사건일 −before ~ +after 일. 날짜를 못 읽으면 제외."""
    d = pub_date(item.get("pub"))
    if d is None:
        return False
    c = datetime.strptime(day, "%Y-%m-%d").date()
    return (c - timedelta(days=before)) <= d <= (c + timedelta(days=after))


def widen_query(url, days):
    """구글뉴스 검색 URL 의 when:Nd 를 days 로 넓힌다(없으면 붙인다). 순수 함수."""
    if re.search(r"when(?::|%3A)\d+d", url):
        return re.sub(r"when(?::|%3A)\d+d", f"when:{int(days)}d", url)
    if "q=" in url:
        return re.sub(r"(q=[^&]*)", lambda m: m.group(1) + f"+when:{int(days)}d", url, count=1)
    return url


def pdf_text(data, limit):
    """PDF 바이트 → 텍스트(pypdf). 실패하면 빈 문자열 — 호출자가 SEC HTML 로 대체한다."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        out = []
        for page in reader.pages:
            out.append(page.extract_text() or "")
            if sum(len(x) for x in out) >= limit:
                break
        return re.sub(r"\s+", " ", " ".join(out)).strip()[:limit]
    except Exception as e:
        print(f"[동행] PDF 추출 실패({e}) — SEC HTML 원문으로 대체 필요", file=sys.stderr)
        return ""


def fetch_article_text(url, limit=6000, headers=None):
    """원문 본문 대강 추출 — 실패해도 빈 문자열로 우아하게 (signal_column 과 같은 문법)

    2026-10-23 ⑱: 예전엔 PDF 를 빈 문자열로 넘겼다(바이너리 쓰레기 방지). 그 결과 IR 실적
    PDF 를 못 읽은 채 제목만으로 에세이를 쓰고 차트 숫자까지 지어냈다. 이제 pypdf 로 읽는다.
    """
    try:
        r = requests.get(url, headers=headers or UA, timeout=30)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "pdf" in ctype or re.search(r"\.pdf(\?|$)", url or "", re.I):
            return pdf_text(r.content, limit)
        html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z#0-9]+;", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:limit]
    except Exception as e:
        print(f"[동행] 원문 수집 실패({e}) — 제목·요약만으로 진행", file=sys.stderr)
        return ""


# ────────────────────────────────────────────────────────────────
# 소재함 (Firestore materials) — 소장이 던진 소재가 최우선
# ────────────────────────────────────────────────────────────────

def firestore_token(sa_info):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/datastore"])
    creds.refresh(Request())
    return creds.token


def _fv(field):
    """Firestore 값 → 파이썬 값 (필요한 타입만)"""
    if not isinstance(field, dict):
        return None
    for k in ("stringValue", "booleanValue", "integerValue", "timestampValue"):
        if k in field:
            return field[k]
    return None


def fetch_materials(sa_info, token):
    """미소비 소재 목록 — 오래된 것부터. 실패해도 자동 수집은 계속된다."""
    pid = sa_info["project_id"]
    body = {"structuredQuery": {
        "from": [{"collectionId": MATERIALS}],
        "where": {"fieldFilter": {"field": {"fieldPath": "consumed"},
                                  "op": "EQUAL", "value": {"booleanValue": False}}},
        "limit": 20}}
    r = requests.post(
        f"https://firestore.googleapis.com/v1/projects/{pid}/databases/(default)/documents:runQuery",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=30)
    r.raise_for_status()
    out = []
    for row in r.json():
        doc = row.get("document")
        if not doc:
            continue
        f = doc.get("fields", {})
        out.append({
            "name": doc["name"],
            "id": doc["name"].rsplit("/", 1)[-1],
            "company": _fv(f.get("company")) or "",
            "mode": _fv(f.get("mode")) or "essay",
            "url": _fv(f.get("url")) or "",
            "memo": _fv(f.get("memo")) or "",
            "title": _fv(f.get("title")) or "",
            "created": _fv(f.get("created")) or "",
        })
    out.sort(key=lambda m: m.get("created") or "")
    return out


def mark_consumed(token, doc_name, note):
    """처리 완료 표시 — 삭제하지 않는다(소급 삭제 금지). consumed 플래그만 세운다."""
    url = (f"https://firestore.googleapis.com/v1/{doc_name}"
           "?updateMask.fieldPaths=consumed&updateMask.fieldPaths=consumed_note")
    r = requests.patch(url, headers={"Authorization": f"Bearer {token}"},
                       json={"fields": {"consumed": {"booleanValue": True},
                                        "consumed_note": {"stringValue": note[:300]}}},
                       timeout=30)
    r.raise_for_status()


# ────────────────────────────────────────────────────────────────
# 집필
# ────────────────────────────────────────────────────────────────

def ask_claude(api_key, constitution, company, thesis, items, recent_titles, memo="",
               feedback="", full_any=True, numbers=None, model=None):
    """헌법 프롬프트 + 논제 원장 + 후보를 주고 판정·집필을 함께 시킨다.

    5축에 닿지 않으면 모델이 {"publish": false} 로 침묵을 택한다 — 그것이 정답이다.
    """
    ctx = {
        "회사": company["ko"],
        "논제원장": thesis["body"][:6000],
        "최근에세이제목": recent_titles[:8],
        "소장메모": memo,
        "직전_원고_반려사유": feedback,
        "숫자_규율": ("본문·차트의 숫자는 아래 '사용_가능한_숫자' 목록에 있는 것만 쓴다 — 목록 밖 숫자 금지. "
                    "필요하면 숫자 없이 서술하라. 단위 표기만 바꿀 수 있다(5,248 백만 달러 → 52.5억 달러). "
                    "합·차·비율·연환산처럼 직접 계산한 숫자, 기억으로 채운 숫자는 쓰지 않는다 — "
                    "엔진이 원문과 대조해 걸러내고, 걸리면 그 숫자가 사유로 되돌아온다."
                    if full_any else
                    "전문을 읽은 원천이 없다 — 숫자와 차트를 쓰지 마라. 실적·계약 사건이면 "
                    "publish=false 로 답하라."),
        "사용_가능한_숫자": (numbers or []) if full_any else [],
        "후보": [{"제목": x.get("title", ""), "URL": x.get("url", ""),
                  "발행": x.get("pub", ""),
                  "원천등급": x.get("grade") or ("SEC 공시 원문(1순위)" if x.get("primary") else "보도·기타"),
                  "발췌": (x.get("article") or "")[:(20000 if x.get("primary") else 2500)]}
                 for x in items],
    }
    prompt = (constitution + "\n\n" + f"""
[이번 회차 입력]
{json.dumps(ctx, ensure_ascii=False)}

위 헌법에 따라 판정하고, 쓸 가치가 있을 때만 집필하라.
반드시 아래 JSON 하나만 응답하라 (코드펜스·설명 금지):
{{"publish": true 또는 false,
 "reason": "판정 근거 한 문장 (침묵이면 어느 축에도 닿지 않은 이유)",
 "axis": "닿은 축 번호와 이름 (침묵이면 빈 문자열)",
 "title": "에세이 제목 (60자 이내, 낚시 금지)",
 "verdict": "강화|불변|약화|판단 보류",
 "body": "<h3>·<p>·<ul>·<li> 만 쓴 HTML. 헌법의 6단 구조를 지키되 6번 출처·면책은 넣지 마라(엔진이 붙인다)",
 "sources": ["실제로 근거로 삼은 URL", "..."],
 "watch": ["다음 관측 포인트", "..."],
 "event_type": "실적|계약|발사|규제|기타 중 하나",
 "event_date": "다루는 사건이 일어난 날 YYYY-MM-DD",
 "event_key": ["사건 핵심어 2~3개(예: 3Q26 실적, Flight 14)"],
 "charts": [{{"title": "차트 제목", "subtitle": "부제·단위 설명", "unit": "단위(%·B·건 등, 없으면 빈 문자열)", "series": [{{"label": "항목", "value": 숫자}}]}}]}}""")
    model = model or models()[0]
    fact = model != models()[0]
    # 사실 모델(Sonnet 5.5)은 사고가 기본으로 켜진다 — 사고가 응답 몫을 먹지 않게 상한을 넉넉히,
    # 노력은 medium(실적 표 대조에는 충분하고 비용이 반쯤)
    body = {"model": model, "max_tokens": 16000 if fact else 4000,
            "messages": [{"role": "user", "content": prompt}]}
    if fact:
        body["output_config"] = {"effort": "medium"}
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=300 if fact else 180)
    r.raise_for_status()
    data = r.json()
    use = data.get("usage") or {}
    print(f"[동행] 집필 모델 {model} · 입력 {use.get('input_tokens')} · 출력 {use.get('output_tokens')} 토큰"
          f" · 종료 {data.get('stop_reason')}")
    if data.get("stop_reason") == "refusal":
        return {"publish": False, "reason": "모델이 응답을 거절(refusal) — 침묵"}
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```(?:json)?", "", text).strip()
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def render_charts(charts):
    """수치 차트 — signal_column 의 기계를 **재사용**한다(복사하지 않는다).

    두 벌이 되면 반드시 갈라진다: 항목<2 방어·오염값(NaN/inf) 방어·연도 계열
    시간선 전환·다크 패널(#10141f)·UNIVERTRIX 각인이 관측일지 칼럼과 한 문법이어야
    같은 사이트의 그림으로 읽힌다. 여기서는 그 함수를 부르기만 한다.
    """
    try:
        from signal_column import render_chart
    except Exception as e:
        print(f"[동행] 차트 기계 로드 실패 → 차트 없이 발행 ({e})", file=sys.stderr)
        return ""
    out = ""
    for ch in (charts or [])[:2]:
        try:
            out += render_chart(ch)
        except Exception as e:
            print(f"[동행] 차트 렌더 실패 → 건너뜀 ({e})", file=sys.stderr)
    return out


# 꼬리(출처·이해관계·면책)를 감싸는 표식 — 소급 재렌더의 경계가 된다
TAIL_OPEN = '<div class="ce-tail">'
TAIL_CLOSE = "</div>"
# 모델이 헌법 6번을 스스로 써 버린 경우(엔진이 붙인다고 일렀는데도) 그 꼬리를 걷어낸다.
#
# 제목 변형에 주의: 처음엔 <h3>출처</h3> 하나만 잡았는데, 모델은 회차마다
# "참고 자료" · "Sources" · "출처:" · <h4> · <p><strong>면책:</strong> 등으로 바꿔 쓴다
# (2026-08-23 실측 — 10개 변형 중 9개가 새어 나갔다).
# 반대로 느슨하게 풀면 "출처의 신뢰도는 어떠한가" 같은 **분석 절**을 먹는다.
# 그래서 라벨을 **전체 일치**로만 잡는다: 제목 텍스트가 목록의 라벨 그 자체일 때만.
_TAIL_LABEL = (
    r"(?:출처\s*목록|인용\s*출처|자료\s*출처|출처"
    r"|참고\s*자료|참고\s*문헌"
    r"|투자\s*면책|면책\s*고지|면책"
    r"|고지\s*사항"
    r"|sources?|references?|bibliography|disclaimer|disclosure)"
)
# '출처 및 면책' 처럼 둘을 붙여 쓴 제목까지 (연결어는 한 번만 허용)
_TAIL_HEAD_TEXT = (r"\s*" + _TAIL_LABEL +
                   r"(?:\s*(?:및|와|과|·|/|,|&|and)\s*" + _TAIL_LABEL + r")?"
                   r"\s*[:：.]?\s*")
_MODEL_TAIL = re.compile(
    r"(?:<h[2-6]>" + _TAIL_HEAD_TEXT + r"</h[2-6]>"
    r"|<p>\s*<(?:strong|b)>" + _TAIL_HEAD_TEXT + r"</(?:strong|b)>)"
    r"(?:(?!<h[2-6]>).)*$",
    re.S | re.I)


def valid_url(u):
    """href 로 쓸 수 있는 값인가 — scheme + host 가 실제로 파싱되는가.

    모델이 'https://www.reuters.com (White House ... memo, 2026.08.20)' 같은
    설명 섞인 문자열을 출처라며 내놓은 적이 있다(2026-08-23 실측). 그대로 href 에
    넣으면 클릭 불능 링크가 된다 — 링크로 보이는데 아무 데도 가지 않는 쪽이
    링크가 없는 것보다 나쁘다.
    """
    if not isinstance(u, str):
        return False
    u = u.strip()
    if " " in u or len(u) > 500:
        return False
    try:
        from urllib.parse import urlparse
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc) and "." in p.netloc
    except Exception:
        return False


def normalize_sources(raw):
    """출처 레코드 정규화 — 구 스키마(문자열 목록)도 그대로 읽는다.

    → [{"url": str, "full": bool, "attempted": bool}]
    """
    out = []
    for s in raw or []:
        if isinstance(s, str):
            out.append({"url": s, "full": True, "attempted": True})
        elif isinstance(s, dict) and s.get("url"):
            out.append({"url": str(s["url"]),
                        "full": bool(s.get("full", True)),
                        "attempted": bool(s.get("attempted", True))})
    return out[:6]


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def tail_html(slug, sources):
    """에세이 말미 고정 블록 — 출처 · (anthropic 만) 이해관계 · 면책.

    출처는 **모델 출력이 아니라 수집 단계가 실제로 요청한 URL**로만 만든다.
    모델은 본문을 쓰고, 출처는 기계가 안다. URL 로 성립하지 않는 항목은
    링크를 걸지 않고 텍스트로만 남긴다(정직하게 보이되 속이지는 않게).
    """
    parts = []
    recs = normalize_sources(sources)
    if recs:
        lis = []
        for r in recs:
            u = r["url"].strip()
            # 전문을 못 읽었으면 그 사실을 적는다 — 근거의 두께를 숨기지 않는다
            if not r["full"]:
                note = ("<span class='ce-src-note'> (유료벽 — 제목·요약 기반)</span>"
                        if r["attempted"]
                        else "<span class='ce-src-note'> (제목·요약 기반)</span>")
            else:
                note = ""
            if valid_url(u):
                lis.append(f'<li><a href="{_esc(u)}" target="_blank" rel="noopener">'
                           f'{_esc(u[:90])}</a>{note}</li>')
            else:
                lis.append(f"<li>{_esc(u[:120])}<span class='ce-src-note'> "
                           f"(링크 불가 — 주소 형식 아님)</span></li>")
        parts.append("<h3>출처</h3><ul class='ce-src'>" + "".join(lis) + "</ul>")
    if slug == "anthropic":
        parts.append(f"<p class='ce-coi'>{ANTHROPIC_COI}</p>")
    parts.append(f"<p class='ce-disc'>{DISCLAIMER}</p>")
    return TAIL_OPEN + "".join(parts) + TAIL_CLOSE


def strip_model_tail(body):
    """모델이 스스로 붙인 출처·면책 꼬리를 걷어낸다.

    헌법과 응답 스키마 둘 다 "6번 출처·면책은 넣지 마라(엔진이 붙인다)"고 이르지만
    모델이 지키지 않는 회차가 있다. 그대로 두면 한 에세이에 출처 절이 두 개가 되고,
    그중 하나는 검증되지 않은 모델의 기억이다 — 그쪽이 더 위험하다.
    본문 **끝**의 출처·면책 제목 이후만 잘라낸다(분석 문단은 건드리지 않는다).
    출처 블록과 면책 블록이 따로 쌓여 있을 수 있어, 더 잘릴 것이 없을 때까지 반복한다.
    끝에 붙은 것만 지우므로 본문 중간의 문단은 어떤 경우에도 살아남는다.
    """
    out = (body or "").rstrip()
    for _ in range(6):                      # 무한 루프 방지 — 꼬리가 6겹일 리는 없다
        nxt = _MODEL_TAIL.sub("", out).rstrip()
        if nxt == out:
            break
        out = nxt
    return out


def split_tail(html):
    """기존 에세이 html 을 (본문, 꼬리) 로 가른다 — 소급 재렌더용."""
    i = (html or "").find(TAIL_OPEN)
    if i >= 0:
        return html[:i], html[i:]
    # 표식이 없는 구세대: 엔진이 붙인 출처/이해관계/면책의 시작점을 찾는다
    for marker in ("<h3>출처</h3><ul class='ce-src'>", "<p class='ce-coi'>",
                   "<p class='ce-disc'>"):
        j = (html or "").find(marker)
        if j >= 0:
            return html[:j], html[j:]
    return html or "", ""


def build_entry(slug, title, verdict, body_html, sources, origin, today, charts=None,
                numbers_verified=False, event=None):
    verdict = verdict if verdict in VERDICTS else "판단 보류"
    # 차트의 '자료: 원문 기사에서 추출' 각인은 숫자 대조를 통과했을 때만 붙는다
    charts_html = render_charts(charts) if numbers_verified else ""
    if charts_html:
        print(f"[동행] {slug}: 원문 수치 차트 {charts_html.count('<svg')}개 동봉")
    recs = normalize_sources(sources)
    return {
        "company": slug,
        "date": today,
        "title": title[:80],
        "verdict": verdict,
        "html": strip_model_tail(body_html) + charts_html + tail_html(slug, recs),
        "sources": recs,
        "origin": origin,
        "slug": make_slug(slug, title, today),
        "event_type": (event or {}).get("event_type", ""),
        "event_date": (event or {}).get("event_date", ""),
        "event_key": list((event or {}).get("event_key") or [])[:4],
        "numbers_verified": bool(numbers_verified),
    }


def rerender_tails(doc):
    """기발행 에세이의 출처 절을 sources[] 기준으로 다시 그린다 (멱등).

    본문(분석)은 건드리지 않는다. 바뀌는 것은 '출처 표기'뿐 — 다만 모델이 본문 끝에
    스스로 적어 둔 출처·면책 꼬리도 출처 표기라서 함께 기계 원장으로 교체한다.
    """
    n = 0
    for e in doc.get("essays", []):
        body, _old = split_tail(e.get("html", ""))
        body = strip_model_tail(body)
        recs = normalize_sources(e.get("sources"))
        new = body + tail_html(e.get("company", ""), recs)
        if new != e.get("html"):
            e["html"] = new
            e["sources"] = recs
            n += 1
    return n


# ────────────────────────────────────────────────────────────────
# 회차 실행
# ────────────────────────────────────────────────────────────────

def collected_text(items):
    """이번 집필에 실제로 들어간 '수집된 텍스트' 전부 — 인용 대조의 유일한 근거.

    전문 수집에 성공했으면 전문, 유료벽으로 막혔으면 제목·요약만 담긴다.
    그래서 유료벽 대응이 따로 필요 없다: 못 읽은 원문은 대조 대상이 아니고,
    읽은 제목·요약을 통째 옮기면 그건 여전히 잡힌다.
    """
    parts = []
    for x in items or []:
        for k in ("title", "pub", "article", "raw_desc"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return " ".join(parts)


def source_registry(items, cited=None):
    """이번 집필의 출처 원장 — **수집 단계가 실제로 요청한 URL**만 담는다.

    모델의 sources 는 '어느 것을 근거로 썼는가'의 힌트로만 쓴다(cited). 원장에
    없는 항목은 버린다 — 모델이 기억으로 지어낸 주소가 링크로 올라가는 것을
    막는 유일한 방법이 이것이다.
    cited 와 겹치는 게 하나도 없으면 원장 전체를 근거로 본다(그게 사실이므로).
    """
    reg = []
    for x in items or []:
        u = (x.get("url") or "").strip()
        if not u:
            continue
        reg.append({"url": u, "full": bool(x.get("article")),
                    "attempted": bool(x.get("article_attempted"))})
    if not cited:
        return reg[:6]
    keys = [str(c).strip() for c in cited if isinstance(c, str)]
    hit = [r for r in reg if any(r["url"] == k or r["url"] in k or k in r["url"]
                                 for k in keys if k)]
    return (hit or reg)[:6]


# ────────────────────────────────────────────────────────────────
# 사실 규율 (2026-10-23 ⑲) — 숫자 밖의 거짓을 막는다
# ────────────────────────────────────────────────────────────────
# 사고: Flight 14 회수 에세이가 숫자 규율은 통과했다(숫자가 '14' 하나뿐). 그런데 "부스터가 궤도에 진입"
# (실제: 분리 후 멕시코만 연착수) · "여섯 번의 실패" · "재사용 가능성을 갖춘 채 도달"(선체는 태평양 착수 후 폭발)
# 을 썼다. 원천은 발사 **전** 예고 기사와 유료벽 제목뿐이었다. 막아야 할 것은 '원문이 말하지 않은 사실'이다 —
# 검사도 그 정의 그대로: 본문의 사실 주장을 뽑아 수집 원문과 대조한다(지뢰 '방지선은 정의로 검사하라').
EVENT_TYPES = ("실적", "계약", "발사", "규제")       # '기타'(추세·논평)는 사건 에세이가 아니다
PRIMARY_HOSTS = ("sec.gov", "ir.spacex.com", "spacex.com", "ll.thespacedevs.com", "anthropic.com",
                 "abc.xyz", "blog.google", "deepmind.google", "googleblog.com")
PRIMARY_MIN = 400        # 공식 도메인 원천을 '읽었다'고 칠 최소 글자(동의 화면·목록 페이지 배제)
PRIMARY_EXCERPT_MIN = 80  # 엔진이 직접 넣은 1차 원천(SEC 발췌·발사 기록)의 최소 글자
FACT_SRC_PRIMARY = 20000
FACT_SRC_OTHER = 6000
FACT_PROMPT = """너는 독립 사실 검증관이다. 원고를 쓴 사람의 의도·논제·지시는 모른다. 아래 '원고'에서 사실 주장
(누가·무엇을·언제·결과, 수량, 사건의 성패, 인용·출처 귀속)을 문장 단위로 모두 뽑아, '자료'의 텍스트만으로 판정하라.
네 기억·상식·외부 지식으로 채우지 마라.
판정: 지지 = 자료 텍스트가 그 주장을 직접 말한다 / 불지지 = 자료에 근거가 없다 / 반박 = 자료가 반대 사실을 말한다.
규칙:
- 사건일 이전 자료의 예정·계획·기대 문장(expected, targeting, will, aims, 예정)은 사건 결과를 지지하지 않는다.
  발사 기록의 '임무 설명'도 발사 전에 쓴 계획 문구다 — 결과는 '결과'·'착륙'·'선체'·'진행 기록' 줄에서만 확인한다.
- 제목만 있는 자료(전문_여부 false)는 제목에 적힌 사실까지만 지지한다.
- 주장의 일부라도 자료를 넘어서면(예: '모두'·'처음'·'회수'·'재사용'처럼 범위·순서·결과를 넓히면) 불지지다.
- 해석·전망·의견·가정('~를 의미한다', '~일 수 있다', 다음 관측 포인트)과 논제 원장·가설·판정에 대한 서술은 뽑지 않는다.
  단 그 문장 안에 세계에 대한 사실(사건 결과·날짜·인물 행위·과거 사례)이 섞여 있으면 그 사실만 뽑는다.
- 다른 회사·다른 기체의 과거 사례도 사실 주장이다 — 자료에 없으면 불지지.
반드시 아래 JSON 하나만 응답하라 (코드펜스·설명 금지):
{"claims": [{"주장": "원고 문장 요지(원고 표현에 가깝게)", "판정": "지지|불지지|반박", "자료": 번호 또는 null,
 "근거": "자료 속 근거 구절(20단어 이내) 또는 빈 문자열", "사유": "불지지·반박이면 무엇이 자료와 다른가"}]}
"""


def _host(url):
    from urllib.parse import urlparse
    try:
        h = urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def is_primary_source(x):
    """1차 원천인가 — 엔진이 넣은 원천(SEC·발사 기록) 또는 회사·규제기관 공식 도메인."""
    if (x or {}).get("primary"):
        return True
    h = _host((x or {}).get("url"))
    return bool(h) and (any(h == d or h.endswith("." + d) for d in PRIMARY_HOSTS) or h.endswith(".gov"))


def primary_after_event(items, event_date):
    """사건 이후의 1차 원천 중 실제로 읽은 것. 사건 전 예고·유료벽 제목은 여기 들지 못한다."""
    try:
        ev = datetime.strptime(str(event_date or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        ev = None
    out = []
    for x in items or []:
        if not is_primary_source(x):
            continue
        need = PRIMARY_EXCERPT_MIN if x.get("primary") else PRIMARY_MIN
        if len((x.get("article") or "").strip()) < need:
            continue
        d = pub_date(x.get("pub"))
        if ev is not None and (d is None or d < ev):
            continue
        out.append(x)
    return out


def is_event_essay(res, required=False):
    return bool(required) or str((res or {}).get("event_type", "")).strip() in EVENT_TYPES


def fact_sources(items):
    """검증관에게 주는 자료 — 수집 원문만(논제 원장·헌법·메모는 넣지 않는다)."""
    out = []
    for i, x in enumerate(items or []):
        art = (x.get("article") or "").strip()
        lim = FACT_SRC_PRIMARY if x.get("primary") else FACT_SRC_OTHER
        out.append({"번호": i + 1, "URL": x.get("url", ""), "발행": x.get("pub", ""),
                    "원천등급": x.get("grade") or ("1차 원천" if is_primary_source(x) else "보도·기타"),
                    "전문_여부": bool(art), "제목": x.get("title", ""),
                    "텍스트": (art or str(x.get("raw_desc") or ""))[:lim]})
    return out


def verify_facts(api_key, body_html, items, event_date="", model=None):
    """독립 검증 호출 — 원고 글자와 수집 원문만 준다(작성 맥락 미전달: 헌법·논제 원장·소장 메모·
    반려 사유·사용 가능한 숫자 전부 빠진다). → (원문이 지지하지 않는 주장 목록, 뽑은 주장 수).

    검증 자체가 실패하면 예외 — 호출자는 미게시로 처리한다(침묵은 통과가 아니다).
    판정이 '지지'가 아닌 것(불지지·반박·알 수 없는 값)은 전부 위반으로 센다.
    """
    plain = _norm(re.sub(r"<[^>]+>", " ", body_html or ""))
    payload = {"사건일": event_date or "", "원고": plain, "자료": fact_sources(items)}
    body = {"model": model or models()[1], "max_tokens": 16000, "output_config": {"effort": "medium"},
            "messages": [{"role": "user", "content": FACT_PROMPT + "\n" + json.dumps(payload, ensure_ascii=False)}]}
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=300)
    r.raise_for_status()
    data = r.json()
    use = data.get("usage") or {}
    print(f"[동행] 사실 검증관 {body['model']} · 입력 {use.get('input_tokens')} · 출력 {use.get('output_tokens')} 토큰")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```(?:json)?", "", text).strip()
    out = json.loads(text[text.find("{"):text.rfind("}") + 1])
    claims = out.get("claims")
    if not isinstance(claims, list):
        raise ValueError("검증관 응답에 claims 목록이 없다")
    return _unsupported(claims), len(claims)


def _unsupported(claims):
    """'지지'가 아닌 판정은 전부 위반 — 불지지·반박·빈 값·알 수 없는 값(검증관이 형식을 어기면 통과시키지 않는다)."""
    return [c for c in claims or [] if str((c or {}).get("판정", "")).strip() != "지지"]


def _fact_feedback(bad):
    return ("독립 검증관이 원문과 대조해 아래 주장을 지지하지 않았다: " +
            " / ".join(f"'{c.get('주장', '')}' ({c.get('판정', '')}: {c.get('사유', '')})" for c in bad[:8]) +
            " — 이 주장들을 빼거나 자료가 말하는 그대로 고쳐라. 결과·범위·순서를 자료보다 넓히지 마라. "
            "사건의 결과는 사건 이후 자료(1차 원천)에서만 가져오고, 예고 기사의 계획 문장을 결과로 쓰지 마라.")


def write_with_discipline(api_key, constitution, comp, thesis, items, recent, memo="",
                          event_required=False, focus_date=""):
    """집필 → 인용·숫자·사실 규율 검사 → (위반이면) 사유를 주고 **1회 재생성** → 재검사.

    한 번 걸렸다고 바로 보류하면, 모델이 규율을 몰라서 걸린 경우까지 버리게 된다.
    반려 사유를 알려주고 다시 쓰게 하는 것이 사람 편집자가 하는 일이다.
    두 번째도 걸리면 그때는 보류한다 — 방지선은 끝내 지켜야 하니까.
    사건 에세이(실적·계약·발사·규제, 또는 초점 회수)는 사건 이후의 1차 원천이 없으면 쓰지 않는다 —
    재생성으로 원천을 만들 수는 없으므로 이 조건은 바로 보류다.
    """
    src = collected_text(items)
    full_any = any((x.get("article") or "").strip() for x in items or [])
    numbers = number_inventory(items) if full_any else []
    model = pick_model(items)
    print(f"[동행] {comp['ko']}: 집필 모델 {model} · 사용 가능한 숫자 {len(numbers)}개")
    if event_required and not primary_after_event(items, focus_date):
        return {"publish": True}, ("사건 이후의 1차 원천 없음 — 예고 기사·유료벽 제목만으로 사건 에세이를"
                                   " 쓰지 않는다(보류)")
    feedback = ""
    for attempt in (1, 2):
        res = ask_claude(api_key, constitution, comp, thesis, items, recent,
                         memo=memo, feedback=feedback, full_any=full_any,
                         numbers=numbers, model=model)
        if not res.get("publish"):
            return res, None
        ev_date = res.get("event_date") or focus_date
        if is_event_essay(res, event_required) and not primary_after_event(items, ev_date):
            return res, (f"사건 이후의 1차 원천 없음(사건일 {ev_date or '미상'}) — 예고 기사·유료벽 제목만으로"
                         " 사건 에세이를 쓰지 않는다(보류)")
        body, charts = res.get("body", ""), res.get("charts") or []
        bad = check_quotes(body, src, len(res.get("sources") or [1]))
        num_bad, num_ctx = [], []
        if not full_any:
            # 전문이 하나도 없다 — 숫자가 핵심인 사건이면 쓰지 않고, 아니면 숫자·차트 금지
            if is_hard_event(res, items):
                return res, "전문 원천 없음 — 실적·계약 사건은 원문 없이 게시하지 않는다(보류)"
            if has_numbers(body, charts):
                num_bad = ["전문 원천 없음 — 숫자·차트 금지"]
        else:
            nb = check_numbers(body, charts, src)
            if nb:
                num_bad = ["원문에 없는 숫자: " + ", ".join(nb[:8])]
                # 보류 판단의 근거를 남긴다 — 걸린 숫자의 앞뒤 문맥(오탐인지 진짜 위반인지 가릴 수 있게)
                num_ctx = number_context(body, nb[:8])
                for tok, ctx in num_ctx:
                    print(f"[동행] 숫자 규율 근거 — '{tok}' :: …{ctx}…", file=sys.stderr)
        bad += num_bad
        fact_bad = []
        if not bad:
            # 인용·숫자를 통과한 원고만 검증관에게 보낸다(비용) — 검증관은 작성 맥락을 모른다
            try:
                fact_bad, n_claims = verify_facts(api_key, body, items, event_date=ev_date)
            except Exception as e:
                return res, f"사실 검증 실행 실패 — 미게시 ({type(e).__name__}: {str(e)[:120]})"
            print(f"[동행] {comp['ko']}: 사실 검증 — 주장 {n_claims}건 · 원문 미지지 {len(fact_bad)}건")
            for c in fact_bad[:8]:
                print(f"[동행] 사실 규율 근거 — '{c.get('주장', '')}' :: {c.get('판정', '')} · "
                      f"{c.get('사유', '')}", file=sys.stderr)
            if not fact_bad:
                res["numbers_verified"] = True
                res["facts_verified"] = True
                res["fact_claims"] = n_claims
                return res, None
            bad = ["원문이 지지하지 않는 주장: " + " / ".join(str(c.get("주장", ""))[:80] for c in fact_bad[:6])]
        reason = " / ".join(bad)
        if attempt == 1:
            print(f"[동행] {comp['ko']}: 규율 위반 — 사유를 주고 1회 재생성 ({reason})",
                  file=sys.stderr)
            feedback = ("직전 원고가 규율에 걸렸다. 아래를 고쳐 다시 써라: " + reason +
                        " — 원문을 옮기지 말고 해석을 써라. 숫자는 후보의 발췌에 **그대로 있는 것만**"
                        " 쓰고, 직접 계산·추정한 숫자와 차트는 빼라. 강조는 따옴표 대신 네 문장으로.")
            if num_ctx:
                # 어느 숫자가 원고의 어느 자리에서 걸렸는지 그대로 돌려준다 — 같은 숫자를 또 쓰지 않게
                feedback += (" 걸린 숫자와 원고 속 자리: " +
                             " / ".join(f"'{tok}' ← …{ctx}…" for tok, ctx in num_ctx) +
                             " — 이 숫자들은 '사용_가능한_숫자' 목록 밖이다. 목록의 숫자로 바꾸거나 숫자 없이 써라.")
            if fact_bad:
                feedback = "직전 원고가 사실 규율에 걸렸다. " + _fact_feedback(fact_bad)
            continue
        return res, reason
    return res, "재생성 후에도 규율 위반"


_STRONG = re.compile(r"(?<![A-Za-z])[1-4]Q\d{2}|[1-4]\s*분기|Q[1-4]\s*20\d{2}|"
                     r"Flight\s*\d+|플라이트\s*\d+|\d+\s*차\s*비행|비행\s*\d+", re.I)
_STOP = {"spacex", "그리고", "에세이", "관측", "논제", "the", "and", "of", "for"}


def _event_tokens(e):
    txt = " ".join([str(e.get("title", ""))] + [str(k) for k in (e.get("event_key") or [])])
    strong = {re.sub(r"\s+", "", m.group(0)).lower().replace("분기", "q")
              for m in _STRONG.finditer(txt)}
    strong = {re.sub(r"^([1-4])q$", r"\1q", x) for x in strong}
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}|[가-힣]{2,}", txt)} - _STOP
    return strong, words


def _norm_strong(tokens):
    """'3q26'·'3q' 를 같은 분기로 — 연도 꼬리를 떼어 비교한다."""
    return {re.sub(r"^([1-4]q)\d{2}$", r"\1", t) for t in tokens}


def same_event(a, b, days=3):
    """같은 회사 · 사건일 ±days · (분기·비행 번호 같은 강한 핵심어 공유 또는 핵심어 절반 이상 겹침)."""
    if a.get("company") != b.get("company"):
        return False
    da = a.get("event_date") or a.get("date") or ""
    db = b.get("event_date") or b.get("date") or ""
    try:
        gap = abs((datetime.strptime(da[:10], "%Y-%m-%d") - datetime.strptime(db[:10], "%Y-%m-%d")).days)
    except ValueError:
        return False
    if gap > days:
        return False
    sa, wa = _event_tokens(a)
    sb, wb = _event_tokens(b)
    if _norm_strong(sa) & _norm_strong(sb):
        return True
    if wa and wb and len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.5:
        return True
    return False


def duplicate_of(entry, essays):
    """공개 에세이 중 같은 사건을 이미 다룬 것 → 그 에세이(없으면 None)."""
    for e in essays or []:
        if is_public(e) and e is not entry and same_event(entry, e):
            return e
    return None


def run_material(mat, companies, api_key, constitution, essays, today):
    """소재 1건 처리 → 에세이 항목 또는 None. (직접 게재는 AI 를 부르지 않는다)"""
    comp = companies.get(mat.get("company"))
    if not comp:
        print(f"[동행] 소재 {mat['id']}: 대상 기업 불명({mat.get('company')}) — 건너뜀",
              file=sys.stderr)
        return None, "대상 기업 불명"

    if mat.get("mode") == "direct":
        # 소장이 쓴 글 그대로 — AI 개입 금지
        body = mat.get("memo", "").strip()
        if not body:
            return None, "직접 게재인데 본문이 비어 있음"
        paras = "".join(f"<p>{line}</p>" for line in body.split("\n") if line.strip())
        title = (mat.get("title") or body.strip().splitlines()[0])[:80]
        sources = ([{"url": mat["url"], "full": True, "attempted": False}]
                   if mat.get("url") else [])
        return build_entry(comp["slug"], title, "불변", paras, sources, "직접", today), "직접 게재"

    thesis = read_thesis(comp["slug"])
    if thesis["placeholder"]:
        return None, "논제 원장이 플레이스홀더 — 집필 스킵"
    if not api_key or not constitution:
        return None, "API 키/헌법 프롬프트 없음"

    item = {"title": mat.get("title") or mat.get("url", ""), "url": mat.get("url", ""),
            "pub": "", "article_attempted": bool(mat.get("url")),
            "article": fetch_article_text(mat["url"]) if mat.get("url") else ""}
    recent = [e["title"] for e in essays if e.get("company") == comp["slug"]][:8]
    res, violation = write_with_discipline(api_key, constitution, comp, thesis, [item],
                                           recent, memo=mat.get("memo", ""))
    if not res.get("publish"):
        return None, f"소재 판정 침묵 — {res.get('reason','')[:60]}"
    if violation:
        return None, "규율 위반(재생성 후에도) — 발행 보류 :: " + violation
    return build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                       res.get("body", ""),
                       source_registry([item], res.get("sources")), "소재", today,
                       charts=res.get("charts"), numbers_verified=res.get("numbers_verified", False),
                       event=res), "소재 에세이화"


SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/"


def pick_ex99(index_html):
    """공시 색인 페이지 → EX-99.x 문서 경로(없으면 None). 파일명이 아니라 '문서 유형' 열로 고른다.

    SpaceX 의 실적 보도자료(Ex.99.1)는 'spaceexplorationtechnologi.htm' 같은 이름이라
    'ex99' 파일명 규칙으로는 못 찾는다(2026-10-23 실측).
    """
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_html or "", re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if any(re.fullmatch(r"EX-99(\.\d+)?", c) for c in cells):
            href = re.findall(r'href="([^"]+\.html?)"', row)
            if href:
                return href[0].replace("/ix?doc=", "")
    return None


def sec_exhibit_url(event, headers):
    """8-K 사건 → Ex.99.1(보도자료 HTML) 주소. 못 찾으면 8-K 본문 주소."""
    url = event.get("url") or ""
    accn = (event.get("accn") or "").strip()
    m = re.search(r"/data/(\d+)/(\d{18})/", url)
    if not m or not accn:
        return url
    idx = f"{SEC_ARCHIVE}{m.group(1)}/{m.group(2)}/{accn}-index.html"
    try:
        r = requests.get(idx, headers=headers, timeout=25)
        r.raise_for_status()
        path = pick_ex99(r.text)
        if path:
            return "https://www.sec.gov" + path if path.startswith("/") else path
    except Exception as e:
        print(f"[동행] SEC 공시 색인 실패({e}) — 8-K 본문으로", file=sys.stderr)
    return url


def sec_primary_items(comp, days, today=None):
    """실적·계약 사건의 1순위 원천 — 측정층(spacex_watch.json)이 잡은 SEC 8-K 원문.

    2026-10-23 ⑱: IR 실적 PDF 를 못 읽고 제목만으로 쓴 에세이가 숫자를 지어냈다.
    실적·계약은 숫자가 핵심이므로 SEC 공시 원문(Ex.99.1 HTML)을 후보 맨 앞에 넣는다.
    SEC_USER_AGENT 가 없으면 SEC 가 거절하므로 로그만 남기고 빈 목록.
    """
    if comp.get("slug") != "spacex":
        return []
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        print("[동행] SEC_USER_AGENT 없음 — SEC 1순위 원천 생략", file=sys.stderr)
        return []
    watch = load_json(DATA_DIR / "spacex_watch.json", {}) or {}
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=int(days))).strftime("%Y-%m-%d")
    hdr = {"User-Agent": ua}
    out = []
    for e in watch.get("events") or []:
        if e.get("source") != "SEC EDGAR" or e.get("kind") not in FACT_KINDS:
            continue
        if (e.get("date") or "") < cut:
            continue
        ex = sec_exhibit_url(e, hdr)
        text = fetch_article_text(ex, limit=40000, headers=hdr)
        out.append({"title": f"SEC 8-K {e.get('date')} — {e.get('title')} (원문)", "url": ex,
                    "pub": e.get("date", ""), "article": text, "article_attempted": True,
                    "primary": True, "event_kind": e.get("kind")})
        if len(out) >= 2:
            break
    if out:
        print(f"[동행] {comp['ko']}: SEC 1순위 원천 {len(out)}건 — "
              + ", ".join(f"{x['pub']} {len(x['article'])}자" for x in out))
    return out


def ll2_text(d):
    """Launch Library 2 발사 기록(JSON) → 대조용 텍스트. 계획(임무 설명)과 결과(착륙·선체·진행 기록)를 줄로 가른다."""
    d = d or {}
    st = d.get("status") or {}
    lines = [f"Launch Library 2 발사 기록 — 마지막 갱신 {d.get('last_updated', '')}",
             f"이름: {d.get('name', '')} · 발사 시각(UTC): {d.get('net', '')}",
             f"결과: {st.get('name', '')} — {st.get('description', '')}"]
    if d.get("failreason"):
        lines.append(f"결과(실패 사유): {d['failreason']}")
    rk = d.get("rocket") or {}
    for s in rk.get("launcher_stage") or []:
        ln = s.get("launcher") or {}
        land = s.get("landing") or {}
        loc = (land.get("landing_location") or {}).get("name", "")
        lines.append(f"착륙(부스터 {ln.get('serial_number', '')}): 시도 {land.get('attempt')} · 성공 {land.get('success')}"
                     f" · {loc} — {land.get('description', '')}")
        if ln.get("details"):
            lines.append(f"부스터 기록: {ln['details']} (상태: {(ln.get('status') or {}).get('name', '')})")
    for s in rk.get("spacecraft_stage") or []:
        sc = s.get("spacecraft") or {}
        land = s.get("landing") or {}
        lines.append(f"선체({sc.get('name', '')}): 상태 {(sc.get('status') or {}).get('name', '')} · 우주 체류"
                     f" {sc.get('time_in_space', '')} · {sc.get('description', '')}")
        if land:
            loc = (land.get("landing_location") or {}).get("name", "")
            lines.append(f"착륙(선체): 시도 {land.get('attempt')} · 성공 {land.get('success')} · {loc} — "
                         f"{land.get('description', '')}")
    ups = sorted(d.get("updates") or [], key=lambda u: u.get("created_on") or "")
    for u in ups:
        lines.append(f"진행 기록 {u.get('created_on', '')}: {u.get('comment', '')}")
    m = d.get("mission") or {}
    if m.get("description"):
        lines.append(f"임무 설명(발사 전에 쓴 계획 문구 — 결과가 아니다): {m['description']}")
    return "\n".join(lines)


def spacex_launch_items(comp, days, today=None, focus=None):
    """SpaceX 발사 사건의 1차 원천 — 측정층(spacex_watch.json)이 잡은 Launch Library 2 기록을 자료에 넣는다.

    2026-10-23 ⑲: Flight 14 에세이가 발사 전 예고 기사와 유료벽 제목만으로 결과를 지어냈다.
    측정층은 같은 날 '발사 성공' 기록을 이미 갖고 있었다 — 도구가 있어도 배선하지 않으면 없는 것과 같다.
    """
    if comp.get("slug") != "spacex":
        return []
    watch = load_json(DATA_DIR / "spacex_watch.json", {}) or {}
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=int(days))).strftime("%Y-%m-%d")
    out = []
    for e in watch.get("events") or []:
        if e.get("source") != "Launch Library 2" or not e.get("url"):
            continue
        if focus:
            if not in_focus({"pub": e.get("date")}, focus["date"]):
                continue
        elif (e.get("date") or "") < cut:
            continue
        try:
            r = requests.get(e["url"], params={"mode": "detailed"}, timeout=25,
                             headers={"User-Agent": "UNIVERTRIX univertrix.com"})
            r.raise_for_status()
            d = r.json()
        except Exception as ex:
            print(f"[동행] 발사 기록 수집 실패({e.get('title', '')}: {ex}) — 생략", file=sys.stderr)
            continue
        out.append({"title": f"발사 기록(Launch Library 2) — {d.get('name') or e.get('title', '')}",
                    "url": e["url"], "pub": (d.get("last_updated") or e.get("date") or "")[:10],
                    "article": ll2_text(d), "article_attempted": True, "primary": True,
                    "event_kind": "launch", "grade": "발사 기록(Launch Library 2, 사건 후 갱신)",
                    "focus_ok": bool(focus)})      # 초점 판정은 사건일로 이미 했다(갱신일이 아니라)
        if len(out) >= 2:
            break
    if out:
        print(f"[동행] {comp['ko']}: 발사 기록 {len(out)}건 — " + ", ".join(x["pub"] for x in out))
    return out


def focus_excerpt(item, focus):
    """초점 창 밖의 1차 원천 → 초점 사건의 강한 핵심어(Flight 14·3Q26)가 든 문장만 발췌. 없으면 None.

    10/20 실적 8-K 에는 "Completed Starship Flight 14, our first orbital Starship flight…" 가 있다 —
    날짜 창만으로 거르면 이 문장이 9/28 사건의 자료에서 빠진다(2026-10-23 실측).
    """
    pats = [re.escape(re.sub(r"\s+", " ", m.group(0))).replace(r"\ ", r"\s*")
            for m in _STRONG.finditer(str((focus or {}).get("label", "")))]
    if not pats:
        return None
    rx = re.compile("|".join(pats), re.I)
    sents = re.split(r"(?<=[.!?])\s+|\s*•\s*|\n+", item.get("article") or "")
    hit = [s.strip() for s in sents if rx.search(s)]
    if not hit:
        return None
    d = dict(item)
    d["article"] = " … ".join(hit[:6])
    d["title"] = f"{item.get('title', '')} — '{focus['label']}' 언급 문장 발췌"
    d["grade"] = "SEC 공시 원문 발췌(사건 후)" if "sec.gov" in str(item.get("url")) else "1차 원천 발췌(사건 후)"
    return d


def run_auto(comp, api_key, constitution, essays, today, widen_days=0, exclude=None,
             memo="", focus=None):
    """자동 수집분 처리 → 에세이 항목 또는 None.

    widen_days/exclude/memo 는 회수 모드(S4) 전용 — 평시 호출은 기본값 그대로다.
    """
    thesis = read_thesis(comp["slug"])
    if thesis["placeholder"]:
        print(f"[동행] {comp['ko']}: 논제 원장 플레이스홀더 — 관찰 대기 🔭 (집필 스킵)")
        return None
    if not api_key or not constitution:
        print(f"[동행] {comp['ko']}: API 키/헌법 없음 — 건너뜀", file=sys.stderr)
        return None

    cfg = load_json(SRC_PATH, {})
    cands = collect_candidates(comp, cfg, widen_days=widen_days)
    # 철회된 에세이의 출처는 막지 않는다 — 같은 사건을 원문 기반으로 다시 쓸 수 있어야 한다
    seen_urls = {r["url"] for e in essays if e.get("company") == comp["slug"] and is_public(e)
                 for r in normalize_sources(e.get("sources"))}
    seen_urls |= set(exclude or ())
    # 1차 원천이 맨 앞 — 실적·계약은 SEC 원문, 발사는 측정층의 발사 기록(⑲)
    primary = (sec_primary_items(comp, widen_days or 7) +
               spacex_launch_items(comp, widen_days or 7, today=today, focus=focus))
    p_urls = {x["url"] for x in primary}
    others = [c for c in cands if c.get("url") not in p_urls and c.get("url") not in seen_urls]
    if focus:
        # 회수 초점(사건일·이름) — 그 사건 근처 자료만 근거로 삼는다. 창 밖의 1차 원천은
        # 그 사건을 언급한 문장만 발췌해 넣는다(다른 사건 에세이가 이미 쓴 원천이어도 — 문장이 다르다)
        prim = []
        for c in primary:
            if c.get("focus_ok") or in_focus(c, focus["date"]):
                if c.get("url") not in seen_urls:
                    prim.append(c)
            else:
                ex = focus_excerpt(c, focus)
                if ex:
                    prim.append(ex)
        cands = prim + [c for c in others if in_focus(c, focus["date"])]
        print(f"[동행] {comp['ko']}: 회수 초점 {focus['date']} {focus['label']} — 후보 {len(cands)}"
              f" (1차 원천 {len(prim)})")
    else:
        cands = [c for c in primary if c.get("url") not in seen_urls] + others
    if not cands:
        print(f"[동행] {comp['ko']}: 후보 0 · 발행 0 — 침묵")
        return None
    for c in cands[:3]:
        if c.get("primary"):
            continue                          # 이미 SEC 원문 전문을 담았다
        c["article_attempted"] = True        # 유료벽 라벨은 '시도했는데 실패'일 때만
        c["article"] = fetch_article_text(c["url"])
    recent = [e["title"] for e in essays if e.get("company") == comp["slug"] and is_public(e)][:8]
    try:
        res, violation = write_with_discipline(api_key, constitution, comp, thesis,
                                               cands[:5], recent, memo=memo,
                                               event_required=bool(focus),
                                               focus_date=(focus or {}).get("date", ""))
    except Exception as e:
        print(f"[동행] {comp['ko']} 집필 실패: {e} — 다음 회차 재시도", file=sys.stderr)
        return None
    if not res.get("publish"):
        print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 0 — 5축 미달로 침묵 "
              f"({res.get('reason','')[:60]})")
        return None
    if violation:
        # 사유 전문을 남긴다 — 잘라 쓰면 왜 막혔는지 영영 모른다
        print(f"[동행] {comp['ko']}: 규율 위반(인용·숫자·사실·원천) — 발행 보류 :: {violation}",
              file=sys.stderr)
        return None
    entry = build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                        res.get("body", ""),
                        source_registry(cands[:5], res.get("sources")), "auto", today,
                        charts=res.get("charts"),
                        numbers_verified=res.get("numbers_verified", False), event=res)
    dup = duplicate_of(entry, essays)
    if dup:
        print(f"[동행] {comp['ko']}: 같은 사건 중복 — 「{dup.get('title','')}」 이미 있음, 발행 안 함")
        return None
    print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 1 — 축 {res.get('axis','')}")
    return entry


BACKFILL_SLUG = "spacex"     # 회수 대상 — 사건층 정지 기간에 가장 많이 놓친 항로
BACKFILL_MAX = 2            # 회수 에세이 상한
BACKFILL_MEMO = ("회수 모드: 수집 정지 기간(2026-09-22~) 동안 놓친 사건을 되짚는다. "
                 "후보 가운데 실제로 5축에 닿는 사건만 고르고, 이미 쓴 사건은 다시 쓰지 마라. "
                 "근거는 후보로 주어진 자료뿐이다.")


def backfill_focus():
    """워크플로 입력 companion_backfill_focus → [{date, label}].

    형식: '2026-09-28|Starship Flight 14 첫 궤도;2026-10-20|3Q26 실적'. 비우면 기존 방식(모델이 고름).
    """
    out = []
    for part in (os.environ.get("COMPANION_BACKFILL_FOCUS", "") or "").split(";"):
        if "|" not in part:
            continue
        d, label = (x.strip() for x in part.split("|", 1))
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            print(f"[동행] 회수 초점 날짜 형식 오류 — 무시: {part}", file=sys.stderr)
            continue
        out.append({"date": d, "label": label[:60]})
    return out[:BACKFILL_MAX]


def backfill_days():
    """워크플로 입력 companion_backfill_days → 정수(기본 0 = 회수 안 함)."""
    try:
        return max(0, min(60, int(os.environ.get("COMPANION_BACKFILL_DAYS", "0") or 0)))
    except ValueError:
        return 0


def run_backfill(by_slug, api_key, constitution, essays, today, state, days=None, focus=None):
    """수리 배포 후 1회 한정 — spacex 만 쿼리 창을 넓혀 최대 2편.

    1회 한정은 상태로 지킨다: state.backfill[slug] 가 있으면 다시 돌지 않는다
    (입력값을 실수로 남겨 둔 채 재실행해도 회수가 반복되지 않는다).
    인용·준법 규율은 평시 경로(write_with_discipline)를 그대로 탄다.
    """
    days = backfill_days() if days is None else days
    if not days:
        return []
    focus = backfill_focus() if focus is None else focus
    key = ";".join(f"{f['date']}|{f['label']}" for f in focus)
    comp = by_slug.get(BACKFILL_SLUG)
    done = (state.setdefault("backfill", {}) or {}).get(BACKFILL_SLUG)
    if not comp:
        print(f"[동행] 회수: 대상 {BACKFILL_SLUG} 설정 없음 — 생략", file=sys.stderr)
        return []
    # 1회 한정은 '같은 회수'에 대해서다 — 초점(사건 목록)이 다르면 새 회수로 본다(2026-10-23 E3).
    # 같은 초점을 실수로 다시 넣으면 잠금이 막는다. 초점 없는 회수는 예전처럼 한 번뿐이다.
    if done and (done.get("key", "") == key or not key):
        print(f"[동행] 회수: {BACKFILL_SLUG} 은 {done.get('date')} 에 이미 회수함 — 1회 한정, 생략")
        return []
    if focus:
        return _run_focused(comp, api_key, constitution, essays, today, state, days, focus, key)
    out, used = [], set()
    for k in range(BACKFILL_MAX):
        # 2026-10-23 첫 회수에서 2편이 모두 3Q 실적이었다 — URL 만 빼서는 같은 사건의 다른
        # 기사가 다시 후보가 된다. 이미 쓴 사건을 제목으로 못 박고, 같은 사건이면 침묵시킨다.
        memo = BACKFILL_MEMO + "".join(
            f" 이번 회차에 이미 쓴 에세이: 「{e['title']}」 — 같은 사건(같은 실적 발표·같은 비행)을"
            " 다른 기사로 다시 쓰지 마라. 남은 후보에 다른 사건이 없으면 publish=false." for e in out)
        entry = run_auto(comp, api_key, constitution, essays + out, today,
                         widen_days=days, exclude=set(used), memo=memo)
        if not entry:
            break
        if duplicate_of(entry, essays + out):
            print(f"[동행] 회수 {k + 1}: 같은 사건 중복 — 발행 안 함")
            break
        entry["backfill"] = True
        out.append(entry)
        used |= {r["url"] for r in entry.get("sources", [])}
        print(f"[동행] 회수 {k + 1}/{BACKFILL_MAX} — 「{entry['title']}」")
    state["backfill"][BACKFILL_SLUG] = {"date": today, "days": days, "published": len(out)}
    if out:
        state["published"][BACKFILL_SLUG] = today   # 같은 회차 평시 경로는 침묵
    print(f"[동행] 회수 종료 — when:{days}d · 발행 {len(out)}편")
    return out


def _run_focused(comp, api_key, constitution, essays, today, state, days, focus, key):
    """초점 회수 — 사건마다 1편씩, 그 사건 근처 자료만으로. 규율 경로는 평시와 같다."""
    out, used = [], set()
    for k, f in enumerate(focus):
        memo = (BACKFILL_MEMO + f" 이번 편은 {f['date']} 의 사건 '{f['label']}' 하나만 다룬다. "
                "후보 중 이 사건의 자료만 근거로 삼고, 이 사건이 5축에 닿지 않으면 publish=false." +
                "".join(f" 이미 쓴 에세이: 「{e['title']}」 — 같은 사건이면 publish=false." for e in out))
        entry = run_auto(comp, api_key, constitution, essays + out, today,
                         widen_days=days, exclude=set(used), memo=memo, focus=f)
        if not entry:
            print(f"[동행] 회수 {k + 1}/{len(focus)} ({f['label']}): 발행 없음")
            continue
        if duplicate_of(entry, essays + out):
            print(f"[동행] 회수 {k + 1} ({f['label']}): 같은 사건 중복 — 발행 안 함")
            continue
        entry["backfill"] = True
        out.append(entry)
        used |= {r["url"] for r in entry.get("sources", [])}
        print(f"[동행] 회수 {k + 1}/{len(focus)} ({f['label']}) — 「{entry['title']}」")
    state["backfill"][BACKFILL_SLUG] = {"date": today, "days": days, "published": len(out), "key": key}
    if out:
        state["published"][BACKFILL_SLUG] = today
    print(f"[동행] 초점 회수 종료 — when:{days}d · 초점 {len(focus)} · 발행 {len(out)}편")
    return out


def main():
    cfg = load_json(SRC_PATH, {})
    comps = cfg.get("companies", [])
    if not comps:
        print("[동행] companion_sources.json 없음/비어 있음 — 건너뜀", file=sys.stderr)
        return 0
    by_slug = {c["slug"]: c for c in comps}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    constitution = load_prompt()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    doc = load_essays()
    essays = doc["essays"]
    fixed = rerender_tails(doc)          # 기발행분 출처 절을 기계 원장 기준으로 (멱등)
    if fixed:
        save_essays(doc)
        print(f"[동행] 기발행 에세이 {fixed}편의 출처 절을 수집 원장 기준으로 재렌더")
    state = load_state()
    published = []

    # ── ① 소재함 우선 ──
    sa_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if sa_raw:
        try:
            stuck = 0
            sa_info = json.loads(sa_raw)
            token = firestore_token(sa_info)
            mats = [m for m in fetch_materials(sa_info, token)
                    if m["id"] not in set(state.get("materials", []))]
            print(f"[동행] 소재함: 미소비 {len(mats)}건")
            for m in mats:
                entry, note = run_material(m, by_slug, api_key, constitution, essays, today)
                if entry:
                    essays.insert(0, entry)
                    published.append(entry)
                    print(f"[동행] 소재 발행 — {entry['company']} 「{entry['title']}」({note})")
                else:
                    print(f"[동행] 소재 {m['id']} 미발행 — {note}")
                # 발행되지 않아도 '판정된' 소재는 소비 처리한다. 그래야 같은 소재를
                # 매 회차 다시 물고 늘어지지 않는다 (소장은 목록에서 사유를 본다).
                try:
                    mark_consumed(token, m["name"], note)
                    state.setdefault("materials", []).append(m["id"])
                except Exception as e:
                    stuck += 1
                    print(f"[동행] 소재 {m['id']} 소비 표시 실패 ({e}) — 다음 회차 재시도",
                          file=sys.stderr)
            # 정비 관제탑이 읽는 관측치 — 소장이 던진 소재가 침묵 속에 썩지 않도록
            # '남은 미소비 건수'를 산출물에 남긴다 (sentinel 은 이 값만 본다).
            state["materials_pending"] = stuck
            state["materials_checked"] = today
        except Exception as e:
            state["materials_pending"] = -1     # -1 = 소재함 접근 자체 실패(알 수 없음)
            state["materials_checked"] = today
            print(f"[동행] 소재함 접근 실패 → 자동 수집만 진행 ({e})", file=sys.stderr)
    else:
        print("[동행] FIREBASE_SERVICE_ACCOUNT 없음 — 소재함 생략")

    # ── ①-b 놓친 사건 회수 (S4, 1회 한정) ──
    for entry in run_backfill(by_slug, api_key, constitution, essays, today, state):
        essays.insert(0, entry)
        published.append(entry)

    # ── ② 자동 수집분 (회사당 하루 1편) ──
    for comp in comps:
        slug = comp["slug"]
        if state["published"].get(slug) == today:
            print(f"[동행] {comp['ko']}: 오늘 이미 발행 — 침묵 (하루 1편)")
            continue
        # 이중 잠금 — 상태 파일이 유실돼도 원장에 오늘 자 자동 에세이가 있으면 침묵
        if published_today(essays, slug, today):
            print(f"[동행] {comp['ko']}: 원장에 오늘 자 에세이 존재 — 침묵 (이중 잠금)")
            state["published"][slug] = today
            continue
        entry = run_auto(comp, api_key, constitution, essays, today)
        if entry:
            essays.insert(0, entry)
            published.append(entry)
            state["published"][slug] = today

    if published:
        doc["essays"] = essays
        save_essays(doc)
    save_state(state)
    print(f"[동행] 회차 종료 — 발행 {len(published)}건 / 원장 {len(essays)}편")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        print("[동행] 엔진 실패 — 파이프라인은 계속 진행합니다", file=sys.stderr)
        sys.exit(0)
