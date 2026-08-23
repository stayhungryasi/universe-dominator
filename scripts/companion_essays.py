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
MODEL = "claude-haiku-4-5"
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


def published_today(essays, slug, today):
    return any(e.get("company") == slug and e.get("date") == today
               and e.get("origin") != "직접" for e in essays)


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

def collect_candidates(company, cfg):
    """회사별 후보 뉴스. 소스 하나가 죽어도 나머지는 살아 있다."""
    try:
        from fetch_signals import parse_rss, resolve_gnews
    except Exception as e:
        print(f"[동행] 수집 유틸 로드 실패 → 자동 수집 생략 ({e})", file=sys.stderr)
        return []
    import feed_client as fc
    cap = int(cfg.get("rss_cap", 4))
    scan = int(cfg.get("scan_limit", 12))
    rows = []
    # 라벨에 회사를 담아야 관제탑이 어느 항로의 소스가 죽었는지 말할 수 있다
    for s in fc.interleave_by_host(company.get("sources", [])):
        label = f"{company['slug']}:{s['name']}"
        text, outcome, code = fc.fetch(s["url"], label, "companion", headers=UA)
        if text is None:
            print(f"[동행] {label} 수집 실패 → 건너뜀 ({outcome} {code})", file=sys.stderr)
            continue
        try:
            got = parse_rss(text, s["name"])
            if s.get("type") == "gnews":
                got = resolve_gnews(got, s.get("domain", ""), cap, scan_limit=scan)
            else:
                got = got[:cap]
            for x in got:
                x.pop("feed_source", None)
            rows.extend(got)
            fc.record("companion", label, "ok" if got else "zero", code, len(got))
            print(f"[동행] {label}: {len(got)}건")
            if not got:
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


def fetch_article_text(url, limit=6000):
    """원문 본문 대강 추출 — 실패해도 빈 문자열로 우아하게 (signal_column 과 같은 문법)"""
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
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
               feedback=""):
    """헌법 프롬프트 + 논제 원장 + 후보를 주고 판정·집필을 함께 시킨다.

    5축에 닿지 않으면 모델이 {"publish": false} 로 침묵을 택한다 — 그것이 정답이다.
    """
    ctx = {
        "회사": company["ko"],
        "논제원장": thesis["body"][:6000],
        "최근에세이제목": recent_titles[:8],
        "소장메모": memo,
        "직전_원고_반려사유": feedback,
        "후보": [{"제목": x.get("title", ""), "URL": x.get("url", ""),
                  "발행": x.get("pub", ""), "발췌": (x.get("article") or "")[:2500]}
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
 "charts": [{{"title": "차트 제목", "subtitle": "부제·단위 설명", "unit": "단위(%·B·건 등, 없으면 빈 문자열)", "series": [{{"label": "항목", "value": 숫자}}]}}]}}""")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 4000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
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


def build_entry(slug, title, verdict, body_html, sources, origin, today, charts=None):
    verdict = verdict if verdict in VERDICTS else "판단 보류"
    charts_html = render_charts(charts)
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


def write_with_discipline(api_key, constitution, comp, thesis, items, recent, memo=""):
    """집필 → 인용 규율 검사 → (위반이면) 사유를 주고 **1회 재생성** → 재검사.

    한 번 걸렸다고 바로 보류하면, 모델이 규율을 몰라서 걸린 경우까지 버리게 된다.
    반려 사유를 알려주고 다시 쓰게 하는 것이 사람 편집자가 하는 일이다.
    두 번째도 걸리면 그때는 보류한다 — 방지선은 끝내 지켜야 하니까.
    """
    src = collected_text(items)
    feedback = ""
    for attempt in (1, 2):
        res = ask_claude(api_key, constitution, comp, thesis, items, recent,
                         memo=memo, feedback=feedback)
        if not res.get("publish"):
            return res, None
        bad = check_quotes(res.get("body", ""), src, len(res.get("sources") or [1]))
        if not bad:
            return res, None
        reason = " / ".join(bad)
        if attempt == 1:
            print(f"[동행] {comp['ko']}: 인용 규율 위반 — 사유를 주고 1회 재생성 ({reason})",
                  file=sys.stderr)
            feedback = ("직전 원고가 인용 규율에 걸렸다. 아래를 고쳐 다시 써라: "
                        + reason +
                        " — 원문을 옮기지 말고 해석을 써라. 강조가 필요하면 따옴표 대신 "
                        "네 문장으로 설명하라.")
            continue
        return res, reason
    return res, "재생성 후에도 인용 규율 위반"


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
        return None, "인용 규율 위반(재생성 후에도) — 발행 보류 :: " + violation
    return build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                       res.get("body", ""),
                       source_registry([item], res.get("sources")), "소재", today,
                       charts=res.get("charts")), "소재 에세이화"


def run_auto(comp, api_key, constitution, essays, today):
    """자동 수집분 처리 → 에세이 항목 또는 None."""
    thesis = read_thesis(comp["slug"])
    if thesis["placeholder"]:
        print(f"[동행] {comp['ko']}: 논제 원장 플레이스홀더 — 관찰 대기 🔭 (집필 스킵)")
        return None
    if not api_key or not constitution:
        print(f"[동행] {comp['ko']}: API 키/헌법 없음 — 건너뜀", file=sys.stderr)
        return None

    cfg = load_json(SRC_PATH, {})
    cands = collect_candidates(comp, cfg)
    seen_urls = {r["url"] for e in essays if e.get("company") == comp["slug"]
                 for r in normalize_sources(e.get("sources"))}
    cands = [c for c in cands if c.get("url") not in seen_urls]
    if not cands:
        print(f"[동행] {comp['ko']}: 후보 0 · 발행 0 — 침묵")
        return None
    for c in cands[:3]:
        c["article_attempted"] = True        # 유료벽 라벨은 '시도했는데 실패'일 때만
        c["article"] = fetch_article_text(c["url"])
    recent = [e["title"] for e in essays if e.get("company") == comp["slug"]][:8]
    try:
        res, violation = write_with_discipline(api_key, constitution, comp, thesis,
                                               cands[:5], recent)
    except Exception as e:
        print(f"[동행] {comp['ko']} 집필 실패: {e} — 다음 회차 재시도", file=sys.stderr)
        return None
    if not res.get("publish"):
        print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 0 — 5축 미달로 침묵 "
              f"({res.get('reason','')[:60]})")
        return None
    if violation:
        # 사유 전문을 남긴다 — 잘라 쓰면 왜 막혔는지 영영 모른다
        print(f"[동행] {comp['ko']}: 인용 규율 위반(재생성 후에도) — 발행 보류 :: {violation}",
              file=sys.stderr)
        return None
    print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 1 — 축 {res.get('axis','')}")
    return build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                       res.get("body", ""),
                       source_registry(cands[:5], res.get("sources")), "auto", today,
                       charts=res.get("charts"))


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
