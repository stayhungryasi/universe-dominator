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
  · **인용 규율**: 출처당 1회·15단어 미만. 위반하면 발행을 보류하고 다음 회차에
    다시 쓴다 (스크랩 방지선이라 경고로 넘기지 않는다).
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


def check_quotes(html, n_sources):
    """원문 인용은 출처당 1회·15단어 미만. 위반 목록을 돌려준다(빈 목록 = 통과).

    뉴스 전문 요약을 복제하면 이 노트는 스크랩이 된다. 헌법이 금지한 선이라
    경고로 넘기지 않고 발행을 보류시키는 근거로 쓴다.
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    quotes = [q.strip() for q in _QUOTE_RE.findall(text) if q.strip()]
    bad = []
    for q in quotes:
        if len(q.split()) >= 15 or len(q) >= 80:
            bad.append(f"긴 인용({len(q.split())}단어): {q[:40]}…")
    limit = max(1, int(n_sources or 1))
    if len(quotes) > limit:
        bad.append(f"인용 {len(quotes)}회 > 출처 {limit}건 (출처당 1회)")
    return bad


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
    cap = int(cfg.get("rss_cap", 4))
    scan = int(cfg.get("scan_limit", 12))
    rows = []
    for s in company.get("sources", []):
        try:
            r = requests.get(s["url"], headers=UA, timeout=25)
            r.raise_for_status()
            got = parse_rss(r.text, s["name"])
            if s.get("type") == "gnews":
                got = resolve_gnews(got, s.get("domain", ""), cap, scan_limit=scan)
            else:
                got = got[:cap]
            for x in got:
                x.pop("feed_source", None)
            rows.extend(got)
            print(f"[동행] {company['slug']} · {s['name']}: {len(got)}건")
            if not got:
                print(f"[동행] {company['slug']} · {s['name']}: 0건 — 피드 점검 대상",
                      file=sys.stderr)
        except Exception as e:
            print(f"[동행] {company['slug']} · {s.get('name','?')} 수집 실패 → 건너뜀 ({e})",
                  file=sys.stderr)
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

def ask_claude(api_key, constitution, company, thesis, items, recent_titles, memo=""):
    """헌법 프롬프트 + 논제 원장 + 후보를 주고 판정·집필을 함께 시킨다.

    5축에 닿지 않으면 모델이 {"publish": false} 로 침묵을 택한다 — 그것이 정답이다.
    """
    ctx = {
        "회사": company["ko"],
        "논제원장": thesis["body"][:6000],
        "최근에세이제목": recent_titles[:8],
        "소장메모": memo,
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


def tail_html(slug, sources):
    """에세이 말미 고정 블록 — 출처 · 면책 · (anthropic 만) 이해관계."""
    parts = []
    links = [s for s in (sources or []) if isinstance(s, str) and s.startswith("http")]
    if links:
        lis = "".join(f'<li><a href="{u}" target="_blank" rel="noopener">{u[:90]}</a></li>'
                      for u in links[:6])
        parts.append(f"<h3>출처</h3><ul class='ce-src'>{lis}</ul>")
    if slug == "anthropic":
        parts.append(f"<p class='ce-coi'>{ANTHROPIC_COI}</p>")
    parts.append(f"<p class='ce-disc'>{DISCLAIMER}</p>")
    return "".join(parts)


def build_entry(slug, title, verdict, body_html, sources, origin, today, charts=None):
    verdict = verdict if verdict in VERDICTS else "판단 보류"
    charts_html = render_charts(charts)
    if charts_html:
        print(f"[동행] {slug}: 원문 수치 차트 {charts_html.count('<svg')}개 동봉")
    return {
        "company": slug,
        "date": today,
        "title": title[:80],
        "verdict": verdict,
        "html": body_html + charts_html + tail_html(slug, sources),
        "sources": [s for s in (sources or []) if isinstance(s, str)][:6],
        "origin": origin,
        "slug": make_slug(slug, title, today),
    }


# ────────────────────────────────────────────────────────────────
# 회차 실행
# ────────────────────────────────────────────────────────────────

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
        sources = [mat["url"]] if mat.get("url") else []
        return build_entry(comp["slug"], title, "불변", paras, sources, "직접", today), "직접 게재"

    thesis = read_thesis(comp["slug"])
    if thesis["placeholder"]:
        return None, "논제 원장이 플레이스홀더 — 집필 스킵"
    if not api_key or not constitution:
        return None, "API 키/헌법 프롬프트 없음"

    item = {"title": mat.get("title") or mat.get("url", ""), "url": mat.get("url", ""),
            "pub": "", "article": fetch_article_text(mat["url"]) if mat.get("url") else ""}
    recent = [e["title"] for e in essays if e.get("company") == comp["slug"]][:8]
    res = ask_claude(api_key, constitution, comp, thesis, [item], recent,
                     memo=mat.get("memo", ""))
    if not res.get("publish"):
        return None, f"소재 판정 침묵 — {res.get('reason','')[:60]}"
    bad = check_quotes(res.get("body", ""), len(res.get("sources") or [1]))
    if bad:
        return None, "인용 규율 위반 — 발행 보류 (" + " / ".join(bad[:2]) + ")"
    srcs = res.get("sources") or ([mat["url"]] if mat.get("url") else [])
    return build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                       res.get("body", ""), srcs, "소재", today,
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
    seen_urls = {u for e in essays if e.get("company") == comp["slug"]
                 for u in (e.get("sources") or [])}
    cands = [c for c in cands if c.get("url") not in seen_urls]
    if not cands:
        print(f"[동행] {comp['ko']}: 후보 0 · 발행 0 — 침묵")
        return None
    for c in cands[:3]:
        c["article"] = fetch_article_text(c["url"])
    recent = [e["title"] for e in essays if e.get("company") == comp["slug"]][:8]
    try:
        res = ask_claude(api_key, constitution, comp, thesis, cands[:5], recent)
    except Exception as e:
        print(f"[동행] {comp['ko']} 집필 실패: {e} — 다음 회차 재시도", file=sys.stderr)
        return None
    if not res.get("publish"):
        print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 0 — 5축 미달로 침묵 "
              f"({res.get('reason','')[:60]})")
        return None
    bad = check_quotes(res.get("body", ""), len(res.get("sources") or [1]))
    if bad:
        print(f"[동행] {comp['ko']}: 인용 규율 위반 — 발행 보류 ({bad[0]})", file=sys.stderr)
        return None
    print(f"[동행] {comp['ko']}: 후보 {len(cands)} · 발행 1 — 축 {res.get('axis','')}")
    return build_entry(comp["slug"], res.get("title", "무제"), res.get("verdict", ""),
                       res.get("body", ""), res.get("sources"), "auto", today,
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
