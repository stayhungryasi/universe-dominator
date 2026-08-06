#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_pioneers.py — 개척자 관측: 세상을 바꾸는 인물들의 최신 신호 추적
─────────────────────────────────────────────────────────────
- data/pioneers_config.json 명단의 인물별 Google News RSS 검색
- 인물당 최신 3건 (fresh_days 이내), URL 기준 중복 제거
- 기존 pioneers.json의 한글 번역은 보존(소급 재번역 안 함)
- 새 항목만 Haiku 8건 묶음 한글 헤드라인 가공 (실패 시 원문 유지 — 우아한 저하)
- 인물 단위 수집 실패 시 이전 데이터 유지 (한 명 실패가 전체를 무너뜨리지 않음)
출력: data/pioneers.json
"""
import html as html_mod
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import requests

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
CONFIG_PATH = DATA_DIR / "pioneers_config.json"
OUT_PATH = DATA_DIR / "pioneers.json"
UA = {"User-Agent": "Mozilla/5.0 (UNIVERTRIX pioneers observer)"}

GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def parse_rss(xml_text):
    """RSS 관용 파서 (fetch_signals와 동일 계열)"""
    out = []
    try:
        root = ElementTree.fromstring(
            xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except Exception:
        return out
    for it in root.findall(".//item"):
        def g(tag):
            el = it.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = html_mod.unescape(g("title"))
        link = g("link")
        pub = g("pubDate")
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if title and link:
            out.append({"title": title[:300], "url": link,
                        "pub": pub[:40], "source": source[:60]})
    return out


def parse_when(pub):
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def norm_title(t):
    """구글뉴스 제목 말미 ' - 매체명' 제거 + 소문자 정규화 (중복 판정용)"""
    t = re.sub(r"\s+-\s+[^-]{2,40}$", "", t)
    return re.sub(r"\W+", "", t.lower())[:80]


def collect_person(p, cfg):
    url = GNEWS.format(q=quote(p["query"]))
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    items = parse_rss(r.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("fresh_days", 21))
    fresh, seen = [], set()
    for it in items:
        dt = parse_when(it["pub"])
        if not dt or dt < cutoff:
            continue
        key = norm_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        it["date"] = dt.strftime("%Y-%m-%d")
        # 표시용 제목에서 ' - 매체명' 꼬리 정리 (source 필드가 따로 있으므로)
        it["title"] = re.sub(r"\s+-\s+[^-]{2,40}$", "", it["title"]).strip()
        fresh.append(it)
    fresh.sort(key=lambda x: x["date"], reverse=True)
    return fresh[: cfg.get("max_items_per_person", 3)]


def _claude_chunk(api_key, chunk):
    """새 항목 묶음 한글 헤드라인 가공 — 2회 재시도, 실패 시 원문 유지"""
    payload = [{"i": i, "person": x["_person"], "title": x["title"]}
               for i, x in enumerate(chunk)]
    prompt = f"""글로벌 시총 추적 사이트 UNIVERTRIX의 '개척자 관측' 편집자로서,
세상을 바꾸는 인물들의 최신 뉴스 헤드라인을 한국 독자용으로 번역하세요.

목록: {json.dumps(payload, ensure_ascii=False)}

각 항목 ko: 자연스러운 한글 헤드라인 (60자 이내, 과장·이모지 금지, 인물명은 한글 표기)
JSON 배열로만 응답. 코드펜스·설명 금지: [{{"i":0,"ko":"..."}}]"""
    for attempt in range(2):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 1500,
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
                    n += 1
            return n
        except Exception as e:
            if attempt == 1:
                print(f"[개척자] 번역 묶음 실패({len(chunk)}건): {e}", file=sys.stderr)
    return 0


def translate_new(api_key, items):
    if not api_key:
        print("[개척자] ANTHROPIC_API_KEY 없음 — 원문 제목 유지", file=sys.stderr)
        return
    todo = [x for x in items if not x.get("ko")]
    for i in range(0, len(todo), 8):
        _claude_chunk(api_key, todo[i:i + 8])
        time.sleep(1)


def main():
    import os
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    prev = {}
    if OUT_PATH.exists():
        try:
            for pp in json.loads(OUT_PATH.read_text(encoding="utf-8")).get("people", []):
                prev[pp["id"]] = pp
        except Exception:
            pass
    prev_ko = {}  # url → 기존 번역 보존
    for pp in prev.values():
        for it in pp.get("items", []):
            if it.get("ko"):
                prev_ko[it.get("url", "")] = it["ko"]

    people_out, new_items = [], []
    for p in cfg["people"]:
        try:
            items = collect_person(p, cfg)
            for it in items:
                if it["url"] in prev_ko:
                    it["ko"] = prev_ko[it["url"]]
                else:
                    it["_person"] = p["ko"]
                    new_items.append(it)
            print(f"[개척자] {p['ko']}: {len(items)}건 (신규 {sum(1 for i in items if '_person' in i)}건)")
        except Exception as e:
            print(f"[개척자] {p['ko']} 수집 실패 → 이전 데이터 유지 ({e})", file=sys.stderr)
            items = prev.get(p["id"], {}).get("items", [])
        people_out.append({"id": p["id"], "ko": p["ko"], "en": p["en"],
                           "role": p["role"], "note": p.get("note", ""),
                           "items": items})
        time.sleep(1)

    translate_new(os.environ.get("ANTHROPIC_API_KEY", "").strip(), new_items)
    for it in new_items:
        it.pop("_person", None)

    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "people": people_out}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    total = sum(len(pp["items"]) for pp in people_out)
    print(f"[개척자] 저장 완료 — {len(people_out)}인 {total}건 "
          f"(신규 번역 {len(new_items)}건)")


if __name__ == "__main__":
    main()
