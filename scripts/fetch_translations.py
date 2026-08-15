#!/usr/bin/env python3
"""
필독 해부실 (fetch_translations) — 필독 신호 원문을 문단별 한국어 브리핑으로 해부
================================================================
목적: 선장님이 영어 원문을 열지 않고도 "그 글이 실제로 무엇을 말했는지"를
      문단 단위 밀도로 파악하게 한다. 요약(why 한 줄)과 칼럼(해석) 사이의 빈칸.

대상: data/signals.json 의 pin=true 신호
저장: data/translations.json (항목 키 = 원문 URL의 SHA-1 앞 12자)
가공: Claude Haiku — 문단별 상세 재서술 (전문 번역이 아님, 아래 저작권 원칙 참조)

⚠️ 저작권 원칙 (프롬프트에 명시 · 산출물 검수 기준)
  - 문장 단위 번역 금지. 원문 문장을 한국어로 옮기는 방식은 하지 않는다.
  - 각 문단·섹션이 '말하는 바'를 편집자의 문장으로 다시 설명한다(재서술).
  - 직접 인용은 글 전체에서 1~2회, 각 15단어 미만.
  - 결과물이 원문의 대체재가 아니라 원문으로 가는 안내판이 되어야 한다.

원칙:
  - 멱등: 이미 해부된 id는 건너뛴다 (재실행해도 중복 생성 없음)
  - 항목별 continue-on-error: 유료벽·404·추출 실패는 failures에 기록만 하고 진행
  - 실패는 최대 MAX_ATTEMPTS 회까지 재시도 (그 뒤로는 영구 건너뜀)
  - API 키 없으면 조용히 종료 (수집 파이프라인을 막지 않는다)

사용:
  python scripts/fetch_translations.py              # 신규 필독 전부
  python scripts/fetch_translations.py --limit 1    # 1건만 (검증용)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SIG_PATH = DATA_DIR / "signals.json"
OUT_PATH = DATA_DIR / "translations.json"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (UNIVERTRIX dissection-room; univertrix.com)"}

MAX_ATTEMPTS = 3        # 실패 항목 재시도 상한
MIN_ARTICLE_CHARS = 500  # 이보다 짧으면 유료벽·자바스크립트 렌더로 간주
ARTICLE_LIMIT = 12000    # Haiku에 넣을 본문 상한


def sig_id(url):
    """항목 키 — 원문 URL 해시 (제목이 바뀌어도 같은 글은 같은 id)"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def extract_article(url):
    """원문 본문 추출 → (텍스트, 실패사유). 실패해도 예외를 올리지 않는다."""
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except Exception as e:
        return "", f"접속 실패: {str(e)[:80]}"
    if r.status_code in (401, 402, 403):
        return "", f"유료벽·접근 거부 (HTTP {r.status_code})"
    if r.status_code == 404:
        return "", "원문 없음 (HTTP 404)"
    if r.status_code >= 400:
        return "", f"HTTP {r.status_code}"

    html = r.text
    # 본문이 아닌 영역 제거
    html = re.sub(r"<(script|style|nav|header|footer|aside)[\s\S]*?</\1>", " ", html, flags=re.I)
    # 문단 경계 보존 — 재서술의 단위가 문단이므로 구조를 잃으면 안 된다
    html = re.sub(r"</(p|div|section|article|li|h[1-6]|br)\s*>", "\n\n", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    paras = [p.strip() for p in text.split("\n\n")]
    # 메뉴·버튼 같은 짧은 조각 제거, 실제 문단만 남긴다
    paras = [p for p in paras if len(p) >= 60]
    body = "\n\n".join(paras).strip()
    if len(body) < MIN_ARTICLE_CHARS:
        return "", f"본문 추출 실패 (확보 {len(body)}자 — 유료벽·JS 렌더 추정)"
    return body[:ARTICLE_LIMIT], ""


# ── 생성 프롬프트: 저작권 제약을 본문에 명시한다 (요구사항) ──────────────
COPYRIGHT_RULES = """■ 저작권 원칙 — 반드시 지키십시오
1. 문장 단위 번역을 하지 마십시오. 원문의 문장을 한국어 문장으로 대응시켜 옮기는 방식은 금지입니다.
2. 각 문단·섹션이 '말하는 바'를 당신의 문장으로 다시 설명하십시오(재서술).
   무엇을 주장하는지, 어떤 근거·수치를 드는지, 앞 문단과 어떻게 이어지는지를 풀어 쓰십시오.
3. 직접 인용은 글 전체에서 1~2회만, 각 15단어 미만으로만 허용합니다.
   인용 부호는 반드시 「 」를 쓰십시오. 큰따옴표(")는 JSON 문자열을 깨뜨리므로
   본문 어디에도 쓰지 마십시오 (강조·제목 표기에도 마찬가지입니다).
4. 결과물이 원문을 읽지 않아도 되는 '대체재'가 아니라, 원문으로 가도록 안내하는 '해설'이어야 합니다.
5. 원문에 없는 사실을 지어내지 마십시오. 원문이 추정으로 말한 것은 추정이라고 표기하십시오."""


def build_prompt(sig, article):
    ko_title = sig.get("ko") or sig["title"]
    ctx = {"원제": sig["title"], "한글제목": ko_title,
           "출처": sig.get("source", ""), "URL": sig["url"],
           "한줄평": sig.get("why", "")}
    ctx_json = json.dumps(ctx, ensure_ascii=False)
    return f"""당신은 글로벌 시가총액 추적·AI 시대 대비 사이트 UNIVERTRIX(우주지배자)의
'필독 해부실' 편집자입니다. 한국 독자가 영어 원문을 열지 않고도 그 글의 논지 전개를
문단 단위로 따라갈 수 있도록, **문단별 상세 브리핑**을 작성하십시오.

{COPYRIGHT_RULES}

신호 정보: {ctx_json}

원문 본문:
\"\"\"
{article}
\"\"\"

작성 지침
- sections: 원문의 문단·섹션 흐름을 따라 5~10개로 나눕니다.
  · h  = 그 대목이 다루는 내용을 드러내는 한국어 소제목 (30자 이내)
  · ko = 그 대목의 재서술 3~6문장. 주장·근거·수치·전환을 담되 당신의 문장으로 쓸 것
- ko_title: 글 전체를 대표하는 한국어 제목 (60자 이내)
- caveat: 이 해부문을 읽을 때 유의할 점 한 문장
  (원문의 한계·저자의 이해관계·검증되지 않은 주장 등. 없으면 원문 대조 권유)

■ 출력 전 자체 검수 (반드시 수행)
JSON을 출력하기 전에 작성한 한국어 전문을 처음부터 끝까지 다시 읽고 아래를 교정하십시오.
- 맞춤법·오탈자 (예: '빠우며' → '빠르며' 같은 자모 누락·오변환)
- 조사·어미의 호응, 띄어쓰기, 문장 종결의 일관성
- 주어와 서술어가 어긋난 비문, 중간에 끊긴 문장
검수를 마친 문장만 출력하십시오. 검수 과정이나 수정 내역은 출력하지 마십시오.

반드시 아래 JSON만 응답하십시오. 코드펜스·설명·인사말 금지:
{{"ko_title": "...", "sections": [{{"h": "소제목", "ko": "재서술 3~6문장"}}], "caveat": "..."}}"""


def ask_claude(api_key, sig, article, attempts=2):
    """Haiku 호출 → 파싱된 dict. 실패 시 예외를 올린다(호출부가 항목 단위로 처리).

    JSON 파싱 실패는 재현되지 않는 경우가 많아(따옴표 이스케이프 누락 등)
    fetch_signals와 같은 방식으로 재시도한다. 마지막 실패에는 응답 앞부분을
    붙여 원인 규명이 가능하게 한다.
    """
    prompt = build_prompt(sig, article)
    last = None
    for i in range(attempts):
        text = ""
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 6000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=180)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            text = re.sub(r"```(?:json)?", "", text).strip()
            out = json.loads(text[text.find("{"):text.rfind("}") + 1])
            secs = [{"h": str(s.get("h", ""))[:60], "ko": str(s.get("ko", "")).strip()}
                    for s in (out.get("sections") or [])
                    if isinstance(s, dict) and str(s.get("ko", "")).strip()]
            if len(secs) < 2:
                raise ValueError(f"섹션 부족({len(secs)}개) — 재서술 실패로 간주")
            return {"ko_title": str(out.get("ko_title") or "")[:120],
                    "sections": secs,
                    "caveat": str(out.get("caveat") or "")[:300]}
        except Exception as e:
            last = e
            if i + 1 < attempts:
                print(f"    (재시도 {i + 1}/{attempts - 1}: {str(e)[:70]})", file=sys.stderr)
            else:
                snippet = re.sub(r"\s+", " ", text)[:160]
                raise ValueError(f"{str(e)[:80]} | 응답앞부분: {snippet}") from last


def load_out():
    if OUT_PATH.exists():
        try:
            d = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            d.setdefault("items", [])
            d.setdefault("failures", [])
            return d
        except Exception as e:
            print(f"[해부실] 기존 파일 손상({e}) — 새로 시작", file=sys.stderr)
    return {"generated_label": "", "items": [], "failures": []}


def main():
    ap = argparse.ArgumentParser(description="필독 신호 원문 → 문단별 한국어 브리핑")
    ap.add_argument("--limit", type=int, default=0,
                    help="이번 실행에서 새로 해부할 최대 건수 (0=제한 없음)")
    ap.add_argument("--force", action="store_true",
                    help="이미 해부된 항목도 다시 생성 (프롬프트 개선 후 재생성용). "
                         "같은 id는 중복 추가하지 않고 교체한다")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[해부실] ANTHROPIC_API_KEY 미설정 — 건너뜀")
        return
    if not SIG_PATH.exists():
        print("[해부실] signals.json 없음 — 건너뜀")
        return

    signals = json.loads(SIG_PATH.read_text(encoding="utf-8")).get("signals", [])
    pinned = [s for s in signals if s.get("pin") and s.get("url")]
    if not pinned:
        print("[해부실] 필독 신호 없음 — 침묵")
        return

    out = load_out()
    done_ids = {it.get("id") for it in out["items"]}
    fail_by_id = {f.get("id"): f for f in out["failures"]}

    todo = []
    for s in pinned:
        sid = sig_id(s["url"])
        if sid in done_ids and not args.force:
            continue
        prev = fail_by_id.get(sid)
        if prev and prev.get("attempts", 0) >= MAX_ATTEMPTS and not args.force:
            continue  # 반복 실패 — 영구 건너뜀 (유료벽 등)
        todo.append((sid, s))
    if args.limit > 0:
        todo = todo[:args.limit]

    if not todo:
        print(f"[해부실] 새로 해부할 필독 없음 — 보관 {len(out['items'])}건 유지")
        return

    print(f"[해부실] 대상 {len(todo)}건 (필독 {len(pinned)}건 중 신규)")
    new_ok = new_fail = 0
    for sid, s in todo:
        title = (s.get("ko") or s["title"])[:40]
        article, err = extract_article(s["url"])
        if not err:
            try:
                got = ask_claude(api_key, s, article)
            except Exception as e:
                err = f"생성 실패: {str(e)[:100]}"
        if err:
            # 항목별 continue-on-error — 실패 기록만 남기고 다음 항목으로
            rec = fail_by_id.get(sid) or {"id": sid, "url": s["url"]}
            rec["title"] = title
            rec["error"] = err
            rec["attempts"] = rec.get("attempts", 0) + 1
            rec["last_try"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
            fail_by_id[sid] = rec
            new_fail += 1
            print(f"  [–] {title} — {err}", file=sys.stderr)
            continue

        # 재생성(--force) 시 같은 id가 두 번 실리지 않도록 기존 항목을 걷어낸다
        out["items"] = [x for x in out["items"] if x.get("id") != sid]
        out["items"].insert(0, {
            "id": sid,
            "date": (s.get("captured") or "")[:10],
            "title": s["title"],
            "ko_title": got["ko_title"] or (s.get("ko") or s["title"]),
            "source": s.get("source", ""),
            "url": s["url"],
            "sections": got["sections"],
            "caveat": got["caveat"],
        })
        fail_by_id.pop(sid, None)  # 재시도 성공 — 실패 기록 해제
        new_ok += 1
        print(f"  [OK] {title} — {len(got['sections'])}개 대목")

    out["failures"] = list(fail_by_id.values())
    out["generated_label"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[해부실] 신규 {new_ok}건 · 실패 {new_fail}건 → 총 {len(out['items'])}건 보관")


if __name__ == "__main__":
    main()
