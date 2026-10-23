#!/usr/bin/env python3
"""
과거 에세이 숫자 규율 전수 재검사 (2026-10-23 대표님 결정 5)
================================================================================
숫자 규율(지뢰 ⑱, check_numbers)이 생기기 전에 공개된 에세이를 오늘 규칙 그대로 다시 잰다.
출처 URL 을 지금 다시 열어 전문을 읽고, 본문(차트 글자 포함)의 숫자가 원문에 있는지 본다.

분류 (판정은 상태에서 — 사람 손 안 탐):
  일치        본문 숫자가 모두 원문에 있다(또는 대조할 숫자가 없다) → 그대로
  명백한 불일치  출처를 전부 전문으로 읽었는데 숫자가 원문에 없다 → 철회(withdraw_essay.py — 라이브 0회 확인까지)
  대조 불가    출처 중 하나라도 못 읽었다(유료벽·차단·삭제) → 철회 금지, "숫자 규율 도입 이전 기록 — 원문 대조 불가" 표식

안전장치: 철회 후보가 전체의 20% 를 넘으면 --apply 는 아무것도 하지 않는다(오탐 의심 — 목록만 보고).

사용:
  python scripts/recheck_essays.py --dry-run [--out PATH]     # 목록만(데이터 안 바꿈)
  python scripts/recheck_essays.py --apply --from PATH       # 드라이런 결과로 처리(표식 기록 + 철회 도구 호출)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import companion_essays as ce   # noqa: E402

HERE = Path(__file__).parent.parent
FULL_MIN = 1500          # 이만큼 못 읽었으면 '전문'이 아니다(유료벽·동의 화면·차단)
WITHDRAW_CAP = 0.20      # 철회 후보 비율 상한 — 넘으면 오탐 의심으로 멈춘다
LABEL = "숫자 규율 도입 이전 기록 — 원문 대조 불가"
MATCH, MISMATCH, UNVERIFIABLE = "일치", "명백한 불일치", "대조 불가"


def fetch_full(url):
    hdr = None
    if "sec.gov" in url:
        ua = os.environ.get("SEC_USER_AGENT", "").strip()
        hdr = {"User-Agent": ua} if ua else None
    try:
        return ce.fetch_article_text(url, limit=80000, headers=hdr) or ""
    except Exception as e:
        print(f"  · 읽기 실패 {url} ({e})", file=sys.stderr)
        return ""


def classify(body_html, texts):
    """본문 + [(url, 원문 텍스트)] → (분류, 위반 숫자 목록, 못 읽은 URL 목록)."""
    if not ce.has_numbers(body_html, []):
        return MATCH, [], []
    src = " ".join(t for _, t in texts)
    bad = ce.check_numbers(body_html, [], src)
    unread = [u for u, t in texts if len(t) < FULL_MIN]
    if not bad:
        return MATCH, [], unread
    if texts and not unread:
        return MISMATCH, bad, []
    return UNVERIFIABLE, bad, unread


def dry_run(out):
    doc = ce.load_essays()
    rows = []
    pub = [e for e in doc.get("essays", []) if ce.is_public(e) and e.get("origin") != "직접"]
    for n, e in enumerate(pub, 1):
        body, _ = ce.split_tail(e.get("html", ""))
        urls = [s["url"] for s in ce.normalize_sources(e.get("sources"))]
        print(f"[{n}/{len(pub)}] {e['slug']} · 출처 {len(urls)}")
        texts = []
        for u in urls:
            texts.append((u, fetch_full(u)))
            time.sleep(0.4)
        cls, bad, unread = classify(body, texts)
        ctx = ce.number_context(body, bad[:10]) if bad else []
        # 참고: 걸린 숫자가 그 회사 논제 원장(집필 프롬프트에 들어가는 문서)에는 있는가 — 분류는 바꾸지 않는다
        th = ce.read_thesis(e.get("company", "")).get("body", "") if bad else ""
        in_thesis = [b for b in bad if th and not ce.check_numbers(f"<p>{b}</p>", [], th)]
        rows.append({"in_thesis": in_thesis,"slug": e["slug"], "title": e.get("title", ""), "company": e.get("company"),
                     "date": e.get("date"), "class": cls, "bad": bad, "context": ctx,
                     "unread": unread, "sources": [{"url": u, "chars": len(t)} for u, t in texts]})
        print(f"    → {cls}" + (f" · 원문 밖 {bad[:6]}" if bad else "") + (f" · 못 읽음 {len(unread)}" if unread else ""))
    total = len(rows)
    cnt = {c: sum(1 for r in rows if r["class"] == c) for c in (MATCH, MISMATCH, UNVERIFIABLE)}
    res = {"checked_at": datetime.now().isoformat(timespec="minutes"), "total": total, "counts": cnt,
           "withdraw_ratio": round(cnt[MISMATCH] / total, 3) if total else 0,
           "stop": bool(total) and cnt[MISMATCH] / total > WITHDRAW_CAP, "rows": rows}
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== 재검사 {total}편 · " + " · ".join(f"{k} {v}" for k, v in cnt.items()) +
          f" · 철회 비율 {res['withdraw_ratio']:.0%}" + (" — 20% 초과: 처리 중단(오탐 의심)" if res["stop"] else ""))
    for r in rows:
        if r["class"] == MISMATCH:
            print(f"  철회 후보: {r['slug']} 「{r['title']}」 — {', '.join(r['bad'][:6])}")
    return res


def apply(path):
    res = json.loads(Path(path).read_text(encoding="utf-8"))
    if res.get("stop"):
        print("[재검사] 철회 후보가 20% 를 넘는다 — 처리하지 않는다(목록만 보고).")
        return 2
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=HERE, check=True)
    doc = ce.load_essays()
    by = {e.get("slug"): e for e in doc.get("essays", [])}
    now = datetime.now().strftime("%Y-%m-%d")
    marked = 0
    for r in res["rows"]:
        e = by.get(r["slug"])
        if not e or not ce.is_public(e):
            continue
        rec = {"status": {MATCH: "match", MISMATCH: "mismatch", UNVERIFIABLE: "unverifiable"}[r["class"]],
               "checked_at": now}
        if r["class"] == UNVERIFIABLE:
            rec["label"] = LABEL
            marked += 1
        e["number_check"] = rec
    ce.save_essays(doc)
    print(f"[재검사] 표식 기록 — 대조 불가 {marked}편")
    slugs = [r["slug"] for r in res["rows"] if r["class"] == MISMATCH]
    if not slugs:
        return 0
    reason = "숫자 규율 재검사(2026-10-23) — 원문 전문을 읽었으나 본문 숫자가 원문에 없음"
    cmd = [sys.executable, str(HERE / "scripts" / "withdraw_essay.py"), "--reason", reason]
    for s in slugs:
        cmd += ["--slug", s]
    print("[재검사] 철회 도구 실행:", " ".join(slugs))
    return subprocess.run(cmd, cwd=HERE).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default=str(HERE / "_probe" / "essay_recheck.json"))
    ap.add_argument("--from", dest="src", default="")
    a = ap.parse_args()
    if a.apply:
        return apply(a.src or a.out)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    dry_run(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
