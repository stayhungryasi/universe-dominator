#!/usr/bin/env python3
"""
신호 관측소 (fetch_signals) — 실리콘밸리의 중요 담론·발표를 놓치지 않는 그물
================================================================
목적: Situational Awareness 같은 에세이·중대 발표를 발표 당일 포착한다.

수집망 (data/signal_sources.json — 소스는 설정으로 추가/제거):
  - Hacker News 고득점 (기본 300점↑): 실리콘밸리의 '집단 주목' 필터
  - AI 랩 공식 발표 RSS (OpenAI·Anthropic·DeepMind 등)

가공: Claude Haiku가 한글 제목 + '왜 중요한가' 요약 + 태그 분류.
저장: data/signals.json (최신 60건 유지, 중복 URL 제거)
원칙: 소스 하나가 죽어도 나머지는 정상 (우아한 저하) · API 키 없으면
      번역 없이 원문 그대로 저장 (수집 자체는 멈추지 않는다)
"""
import json
import os
import re
import sys
import html as html_mod
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SRC_PATH = DATA_DIR / "signal_sources.json"
OUT_PATH = DATA_DIR / "signals.json"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "UNIVERTRIX signal-observatory (univertrix.com)"}

DEFAULT_SOURCES = {
    "sources": [
        {"name": "Hacker News 300+", "type": "hn",
         "url": "https://hnrss.org/frontpage?points=300&count=30"},
        {"name": "OpenAI", "type": "rss",
         "url": "https://openai.com/news/rss.xml"},
        {"name": "Anthropic", "type": "rss",
         "url": "https://www.anthropic.com/rss.xml"},
        {"name": "Google DeepMind", "type": "rss",
         "url": "https://deepmind.google/blog/rss.xml"},
    ],
    "min_points": 300,
    "keep": 60,
}


def load_sources():
    cfg = dict(DEFAULT_SOURCES)
    if SRC_PATH.exists():
        try:
            cfg.update(json.loads(SRC_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[신호] 소스 설정 읽기 실패 → 기본값 ({e})", file=sys.stderr)
    return cfg


def parse_rss(xml_text, source_name):
    """RSS/Atom 관용 파서 — item/entry 공통 처리"""
    out = []
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8")
                                      if isinstance(xml_text, str) else xml_text)
    except Exception:
        return out
    ns_atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{ns_atom}entry")
    for it in items:
        def g(tag):
            el = it.find(tag) if it.find(tag) is not None else it.find(ns_atom + tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = html_mod.unescape(g("title"))
        link = g("link")
        if not link:  # Atom: <link href=...>
            el = it.find(ns_atom + "link")
            link = el.get("href", "") if el is not None else ""
        pub = g("pubDate") or g("published") or g("updated")
        desc = html_mod.unescape(re.sub(r"<[^>]+>", " ", g("description") or g("summary")))
        # HN 포인트 추출 (description에 "Points: 412")
        m = re.search(r"Points:\s*(\d+)", desc)
        points = int(m.group(1)) if m else None
        # HN은 기사 원문 링크가 <link>, 토론은 comments — 원문 우선
        if title and link:
            out.append({"title": title[:300], "url": link, "pub": pub[:40],
                        "points": points, "source": source_name,
                        "raw_desc": desc[:400]})
    return out


def collect(cfg):
    rows = []
    for s in cfg["sources"]:
        try:
            r = requests.get(s["url"], headers=UA, timeout=25)
            r.raise_for_status()
            got = parse_rss(r.text, s["name"])
            if s.get("type") == "hn":
                got = [x for x in got
                       if (x["points"] or 0) >= cfg.get("min_points", 300)]
            else:
                got = got[:cfg.get("rss_cap", 5)]  # 한 소스가 관측소를 도배하지 못하게
            rows.extend(got)
            print(f"[신호] {s['name']}: {len(got)}건")
        except Exception as e:
            print(f"[신호] {s['name']} 수집 실패 → 건너뜀 ({e})", file=sys.stderr)
    return rows


def _claude_chunk(api_key, chunk):
    """묶음 하나 번역 — 2회 재시도, 관용 파싱"""
    payload = [{"i": i, "title": x["title"], "source": x["source"],
                "hint": (x.get("raw_desc") or "")[:150]} for i, x in enumerate(chunk)]
    prompt = f"""글로벌 시총 추적·AI 시대 대비 사이트 UNIVERTRIX의 '신호 관측소' 편집자로서,
실리콘밸리 고신호 소식을 한국 독자용으로 가공하세요.

목록: {json.dumps(payload, ensure_ascii=False)}

각 항목:
- ko: 한글 제목 (자연스러운 번역, 60자 이내)
- why: 왜 중요한가 — 투자자·AI 시대를 준비하는 개인 관점 1~2문장 (과장 금지)
- tag: 에세이 | 모델 발표 | 연구 | 정책·규제 | 투자·산업 | 기타 중 하나 ('모델 발표'는 실제 모델·제품 출시에만, 비전·전략 글은 '에세이')
- grade: 일반 | 주목 | 필독 중 하나 — '필독'은 극히 드뭅니다. 산업 판도를 바꿀 발표,
  AGI·초지능 담론의 기준이 될 에세이, 프런티어 모델 출시급 사건에만 부여하고,
  해당 사항이 없으면 목록 전체에 필독이 0개여도 정상입니다.
  다음은 필독이 아닙니다(주목 이하): 기능 업데이트, 고객 도입 사례, 파트너십·인수 보도,
  교육 과정·행사 안내, 점진적 연구 성과

JSON 배열로만 응답. 마크다운 코드펜스·설명 금지: [{{"i":0,"ko":"...","why":"...","tag":"...","grade":"..."}}]"""
    for attempt in range(2):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 2500,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            text = re.sub(r"```(?:json)?", "", text).strip()
            arr = json.loads(text[text.find("["):text.rfind("]") + 1])
            n = 0
            for row in arr:
                i = row.get("i")
                if isinstance(i, int) and 0 <= i < len(chunk) and row.get("ko"):
                    chunk[i]["ko"] = row["ko"][:120]
                    chunk[i]["why"] = (row.get("why") or "")[:300]
                    chunk[i]["tag"] = row.get("tag") or "기타"
                    chunk[i]["grade"] = row.get("grade") or "일반"
                    n += 1
            return n
        except Exception as e:
            if attempt == 1:
                print(f"[신호] 묶음 가공 실패({len(chunk)}건): {e}", file=sys.stderr)
    return 0


def enrich_with_claude(api_key, items):
    """8건씩 묶음 처리 — 일부 실패해도 나머지는 한글화"""
    if not api_key or not items:
        return items
    done = 0
    for s in range(0, len(items), 8):
        done += _claude_chunk(api_key, items[s:s + 8])
    print(f"[신호] 한글 가공: {done}/{len(items)}건")
    return items


def arbitrate_pins(api_key, signals):
    """등급 기반 필독(고득점 예외 제외)이 2건 이상이면 진짜 필독 1건만 남기고 강등.
    실패 시 최신 1건만 유지하는 보수적 폴백 — 어떤 경우에도 과잉 고정은 남기지 않는다."""
    cands = [x for x in signals
             if x.get("pin") and x.get("grade") == "필독"
             and (x.get("points") or 0) < 700]
    if len(cands) <= 1:
        return
    keep_idx = None
    if api_key:
        payload = [{"i": i, "title": x.get("ko") or x["title"],
                    "why": x.get("why", ""), "source": x.get("source", "")}
                   for i, x in enumerate(cands)]
        prompt = f"""당신은 '신호 관측소'의 최종 편집장입니다. 아래는 하위 편집자들이 '필독'으로 올린 후보입니다.
필독 기준: 산업 판도를 바꿀 발표 / AGI·초지능 담론의 기준이 될 에세이 / 프런티어 모델 출시급 사건.
기능 업데이트·고객 사례·인수 보도·행사·점진적 연구는 필독이 아닙니다.

후보: {json.dumps(payload, ensure_ascii=False)}

가장 중요한 1개의 i만 선택하세요. 모두 기준 미달이면 -1.
JSON만 응답: {{"keep": 숫자}}"""
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            keep_idx = json.loads(text[text.find("{"):text.rfind("}") + 1]).get("keep")
        except Exception as e:
            print(f"[신호] 결선 판정 실패 → 최신 1건 폴백 ({e})", file=sys.stderr)
    if not isinstance(keep_idx, int) or not (-1 <= keep_idx < len(cands)):
        keep_idx = 0  # 폴백: 목록 최신(맨 앞) 1건만 유지
    demoted = 0
    for i, x in enumerate(cands):
        if i != keep_idx:
            x["grade"] = "주목"
            x["pin"] = False
            demoted += 1
    print(f"[신호] 결선 판정: 필독 후보 {len(cands)}건 → 유지 {0 if keep_idx == -1 else 1}건 · 강등 {demoted}건")


def main():
    cfg = load_sources()
    prev = {"signals": []}
    if OUT_PATH.exists():
        try:
            prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    known_urls = {x.get("url") for x in prev.get("signals", [])}

    fresh = [x for x in collect(cfg) if x["url"] not in known_urls]
    # 소스 간 중복 URL 제거
    seen, dedup = set(), []
    for x in fresh:
        if x["url"] in seen:
            continue
        seen.add(x["url"]); dedup.append(x)
    fresh = dedup

    has_backlog = any(not x.get("ko") for x in prev.get("signals", []))
    if not fresh and not has_backlog:
        print("[신호] 새 신호 없음 — 기존 유지")
        return

    fresh = enrich_with_claude(os.environ.get("ANTHROPIC_API_KEY", "").strip(),
                               fresh)
    now_label = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    for x in fresh:
        x["captured"] = now_label
        x.pop("raw_desc", None)

    merged = fresh + prev.get("signals", [])
    merged = merged[:cfg.get("keep", 60)]
    # 📌 필독 자동 고정: HN 700점↑ 또는 Claude 필독 판정
    for x in merged:
        if x.get("pin") is None:
            x["pin"] = bool((x.get("points") or 0) >= 700 or x.get("grade") == "필독")
    # 결선 판정: 등급 기반 필독은 하루 최대 1건 (점수 무사통과분 제외) — 과잉 고정 자동 교정
    arbitrate_pins(os.environ.get("ANTHROPIC_API_KEY", "").strip(), merged)
    # 소급 번역: 과거 미번역 글도 매 실행 최대 24건씩 한글화
    backlog = [x for x in merged if not x.get("ko")][:24]
    if backlog:
        print(f"[신호] 미번역 백로그 {len(backlog)}건 소급 가공")
        enrich_with_claude(os.environ.get("ANTHROPIC_API_KEY", "").strip(), backlog)
        for x in backlog:
            x.pop("raw_desc", None)
    OUT_PATH.write_text(json.dumps(
        {"generated_label": now_label, "signals": merged},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[신호] 신규 {len(fresh)}건 포착 → 총 {len(merged)}건 보관")


if __name__ == "__main__":
    main()
