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
import math
import os
import re
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


FETCH_LIMIT = 600000      # 긴 보고서(정렬 평가·PDF)를 자르지 않는다 — 8만 자에서 잘린 원문 뒤쪽 숫자를 불일치로 오판했다


def fetch_full(url):
    hdr = None
    if "sec.gov" in url:
        ua = os.environ.get("SEC_USER_AGENT", "").strip()
        hdr = {"User-Agent": ua} if ua else None
    try:
        return ce.fetch_article_text(url, limit=FETCH_LIMIT, headers=hdr) or ""
    except Exception as e:
        print(f"  · 읽기 실패 {url} ({e})", file=sys.stderr)
        return ""


# ── ① 원문 진위 — 글자 수만으로 '전문'이라 판정하지 않는다 (2026-10-23 정밀 분류) ──
# 사고: Webull 출처가 지금은 블로그 목록 페이지(8,965자)를 돌려줬다. 1,500자 기준은 통과했고, 기사에 없는
# 숫자가 '명백한 불일치'로 잡혔다 — 원문이 사라진 것을 원문과 다르다고 판정한 셈이다.
# 제목 핵심어는 URL 슬러그에서 뽑는다(원문 제목은 기록돼 있지 않고, 지금 페이지의 제목은 목록 페이지면 목록 제목이다).
# 고유명사 = 슬러그 낱말 중 에세이 본문에 라틴 문자로 등장하는 것(한국어 에세이 속 라틴 낱말은 이름이다).
SLUG_STOP = set((
    "the and for with from that this into over after before about will your our its are was were has have had "
    "not but you how why what when who new news article articles blog blogs story html htm aspx php amp www "
    "com default details releases release updates update index content files press").split())
TITLE_MIN = 2             # 제목 핵심어가 이만큼은 원문에 있어야 같은 기사다
TITLE_COVER = 0.6         # 고유명사가 모자랄 때 — 슬러그 핵심어 중 이 비율 이상


def slug_words(url):
    from urllib.parse import urlparse
    path = urlparse(url or "").path
    toks = [t for t in re.split(r"[-_/.?=+%]+", path) if t]
    return [t.lower() for t in toks if len(t) >= 3 and not re.fullmatch(r"\d+[a-z]{0,2}", t.lower())
            and t.lower() not in SLUG_STOP]


def latin_names(body_html):
    plain = re.sub(r"<[^>]+>", " ", body_html or "")
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", plain)}


def is_article(url, text, body_html):
    """다시 읽은 텍스트가 그 출처의 기사인가 → (참/거짓/None=판정 불가, 사유).

    SEC EDGAR 아카이브는 접수번호로 고정된 주소라 내용이 바뀌지 않는다 — 슬러그가 제목이 아니므로
    제목 대조 대신 '공시 원문'으로 통과시킨다(파일명 'spaceexplorationtechnologi.htm' 은 제목이 아니다).
    """
    if re.search(r"(?:^|\.)sec\.gov/Archives/edgar/data/\d+/\d{18}/", re.sub(r"^https?://(www\.)?", "", url or "")):
        return True, "SEC 공시 원문(접수번호 고정 주소)"
    words = list(dict.fromkeys(slug_words(url)))
    low = (text or "").lower()
    has = [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)]
    names = [w for w in words if w in latin_names(body_html)]
    if len(names) >= TITLE_MIN:
        hit = [w for w in names if w in has]
        return len(hit) >= TITLE_MIN, f"제목 고유명사 {len(hit)}/{len(names)}({', '.join(names[:4])})"
    if len(words) >= TITLE_MIN:
        need = max(TITLE_MIN, math.ceil(TITLE_COVER * len(words)))
        return len(has) >= need, f"제목 핵심어 {len(has)}/{len(words)}(필요 {need})"
    return None, "제목 핵심어 부족 — 진위 판정 불가"


# ── ② 걸린 숫자 분류 ──
EXEMPT, DERIVED, TOLERANCE, TRUE_BAD = "순번·명칭(제외)", "파생 일치", "허용 오차 일치", "진짜 불일치"
_ID_BEFORE = re.compile(r"(?:(?<![A-Za-z])[A-Z][A-Za-z]+|포춘|플라이트|팰컨|버전|시리즈|모델)\s*$")
_UNIT_AFTER = re.compile(r"^\s*(?:(?:%|퍼센트|배|명|개|건|기|대|달러|원|억|조|만|천|년|일|분|시간)(?![가-힣])"
                         r"|(?:B|M|K|bn|billion|million|x)\b)", re.I)
REL_TOL = 0.01            # 허용 오차 재판정 — 반올림 폭 또는 1% 중 큰 쪽


def is_ordinal(plain, s, e, raw):
    """'Flight 14'·'포춘 500'·'Gemini 3' 같은 이름 속 번호 — 정수이고, 앞이 이름이고, 뒤에 단위가 없다."""
    if not re.fullmatch(r"\d+", raw):
        return False
    return bool(_ID_BEFORE.search(plain[max(0, s - 24):s])) and not _UNIT_AFTER.match(plain[e:e + 8])


def derived_from(v, tol, operands):
    """에세이가 쓴 원문 숫자 두 개로 재현되는가(합·차·비·백분율·증감률) → 식 문자열 또는 None.

    피연산자는 '에세이 본문에 있고 원문과도 맞은 숫자'로 한정한다 — 원문 숫자 전부로 조합하면
    숫자가 수백 개인 표에서 아무 값이나 우연히 재현된다.
    """
    for i, a in enumerate(operands):
        for j, b in enumerate(operands):
            if i == j or not b:
                continue
            for val, op in ((a + b, "합"), (abs(a - b), "차"), (a / b, "비"), (a / b * 100, "백분율"),
                            ((a / b - 1) * 100, "증감률")):
                if abs(abs(v) - abs(val)) <= tol:
                    return f"{op}({fmt(a)}, {fmt(b)})"
    return None


def fmt(x):
    for d, u in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(x) >= d:
            return f"{x / d:g}{u}"
    return f"{x:g}"


def nearest_source(v, src_texts):
    """원문에서 가장 가까운 숫자(사람이 읽는 참고용) — 적힌 값(단위 포함) 그대로 비교한다."""
    best = None
    for url, t in src_texts:
        nt = ce._norm(t)
        for sv, _, raw, a, b in ce._num_spans(nt):
            if not sv:
                continue
            d = abs(abs(v) - abs(sv)) / max(abs(v), 1e-9)
            if best is None or d < best[0]:
                best = (d, {"숫자": raw, "문맥": nt[max(0, a - 50):b + 40], "url": url, "차이": f"{d:.1%}"})
    return best[1] if best else None


def classify_numbers(body_html, src_texts):
    """본문 숫자 중 원문에 없는 것을 자리마다 분류 → [{숫자, 문장, 분류, 근거, 가장_가까운_원문_숫자}]."""
    src = " ".join(t for _, t in src_texts)
    pool = ce._source_values(ce._norm(src))
    plain = ce._norm(re.sub(r"<[^>]+>", " ", body_html or ""))
    spans = [x for x in ce._num_spans(plain) if not ce._small_count(x[0], x[1])]
    operands = sorted({v for v, tol, _, _, _ in spans if ce._matches(v, tol, pool)})
    out = []
    for v, tol, raw, s, e in spans:
        if ce._matches(v, tol, pool):
            continue
        rec = {"숫자": raw, "문장": plain[max(0, s - 60):e + 40]}
        if is_ordinal(plain, s, e, raw):
            rec.update(분류=EXEMPT, 근거="이름 속 번호")
        else:
            expr = derived_from(v, tol, operands)
            if expr:
                rec.update(분류=DERIVED, 근거=expr)
            elif ce._matches(v, max(tol, REL_TOL * abs(v)), pool):
                rec.update(분류=TOLERANCE, 근거=f"반올림 폭·{REL_TOL:.0%} 안의 원문 숫자")
            else:
                rec.update(분류=TRUE_BAD, 근거="원문에도 계산에도 없음")
        if rec["분류"] in (TRUE_BAD, TOLERANCE):
            rec["가장_가까운_원문_숫자"] = nearest_source(v, src_texts)
        out.append(rec)
    return out


def classify(body_html, texts):
    """본문 + [(url, 원문 텍스트)] → (분류, 진짜 불일치 숫자, 못 읽은·기사 아닌 URL, 숫자별 분류, 진위 판정).

    못 읽은 출처(FULL_MIN 미만)와 '기사 아님'·'판정 불가' 출처는 대조 텍스트에서 빼고,
    그런 출처가 하나라도 있으면 철회하지 않는다(대조 불가) — 사라진 원문은 원문과 다를 수 없다.
    """
    genuine, unread, verdicts = [], [], []
    for u, t in texts:
        if len(t) < FULL_MIN:
            unread.append(u)
            verdicts.append({"url": u, "chars": len(t), "기사": None, "사유": "못 읽음(글자 수 미달)"})
            continue
        if len(t) >= FETCH_LIMIT:
            unread.append(u)
            verdicts.append({"url": u, "chars": len(t), "기사": None, "사유": "잘림(상한 도달) — 뒤쪽 미확인"})
            continue
        ok, why = is_article(u, t, body_html)
        verdicts.append({"url": u, "chars": len(t), "기사": ok, "사유": why})
        if ok:
            genuine.append((u, t))
        else:
            unread.append(u)
    if not ce.has_numbers(body_html, []):
        return MATCH, [], unread, [], verdicts
    nums = classify_numbers(body_html, genuine)
    true_bad = list(dict.fromkeys(n["숫자"] for n in nums if n["분류"] == TRUE_BAD))
    if not true_bad:
        return MATCH, [], unread, nums, verdicts
    if texts and not unread:
        return MISMATCH, true_bad, [], nums, verdicts
    return UNVERIFIABLE, true_bad, unread, nums, verdicts


def dry_run(out, report=None, fetch=None):
    fetch = fetch or fetch_full
    doc = ce.load_essays()
    rows = []
    pub = [e for e in doc.get("essays", []) if ce.is_public(e) and e.get("origin") != "직접"]
    for n, e in enumerate(pub, 1):
        body, _ = ce.split_tail(e.get("html", ""))
        urls = [s["url"] for s in ce.normalize_sources(e.get("sources"))]
        print(f"[{n}/{len(pub)}] {e['slug']} · 출처 {len(urls)}")
        texts = []
        for u in urls:
            texts.append((u, fetch(u)))
            time.sleep(0.4 if fetch is fetch_full else 0)
        cls, bad, unread, nums, verdicts = classify(body, texts)
        # 참고: 걸린 숫자가 그 회사 논제 원장(집필 프롬프트에 들어가는 문서)에는 있는가 — 분류는 바꾸지 않는다
        th = ce.read_thesis(e.get("company", "")).get("body", "") if bad else ""
        in_thesis = [b for b in bad if th and not ce.check_numbers(f"<p>{b}</p>", [], th)]
        rows.append({"in_thesis": in_thesis, "slug": e["slug"], "title": e.get("title", ""),
                     "company": e.get("company"), "date": e.get("date"), "class": cls, "bad": bad,
                     "numbers": nums, "unread": unread, "sources": verdicts})
        print(f"    → {cls}" + (f" · 진짜 불일치 {bad[:6]}" if bad else "") +
              (f" · 못 읽음/기사 아님 {len(unread)}" if unread else ""))
    total = len(rows)
    cnt = {c: sum(1 for r in rows if r["class"] == c) for c in (MATCH, MISMATCH, UNVERIFIABLE)}
    res = {"checked_at": datetime.now().isoformat(timespec="minutes"), "total": total, "counts": cnt,
           "withdraw_ratio": round(cnt[MISMATCH] / total, 3) if total else 0,
           "stop": bool(total) and cnt[MISMATCH] / total > WITHDRAW_CAP, "rows": rows}
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if report:
        write_report(res, report)
    print(f"\n== 재검사 {total}편 · " + " · ".join(f"{k} {v}" for k, v in cnt.items()) +
          f" · 철회 비율 {res['withdraw_ratio']:.0%}" + (" — 20% 초과: 처리 중단(오탐 의심)" if res["stop"] else ""))
    for r in rows:
        if r["class"] == MISMATCH:
            print(f"  철회 후보 확정: {r['slug']} 「{r['title']}」 — {', '.join(r['bad'][:6])}")
    return res


def write_report(res, path):
    """철회 후보 확정분만 — 제목·숫자·그 문장·원문 URL·원문에서 가장 가까운 숫자. 철회는 하지 않는다."""
    conf = []
    for r in res["rows"]:
        if r["class"] != MISMATCH:
            continue
        conf.append({"slug": r["slug"], "title": r["title"], "date": r["date"],
                     "원문": [s["url"] for s in r["sources"]],
                     "진짜_불일치": [{k: n.get(k) for k in ("숫자", "문장", "가장_가까운_원문_숫자")}
                                for n in r["numbers"] if n["분류"] == TRUE_BAD],
                     "그_밖의_걸린_숫자": [{k: n.get(k) for k in ("숫자", "분류", "근거")}
                                     for n in r["numbers"] if n["분류"] != TRUE_BAD],
                     "논제_원장에_있는_숫자": r.get("in_thesis", [])})
    rep = {"generated_at": res["checked_at"], "total": res["total"], "counts": res["counts"],
           "withdraw_ratio": res["withdraw_ratio"], "stop": res["stop"],
           "rules": {"원문_진위": "다시 읽은 텍스트에 URL 슬러그의 제목 고유명사 2개 이상(고유명사가 모자라면 "
                               "슬러그 핵심어 60%·최소 2개) — 아니면 '기사 아님' → 대조 불가",
                     "숫자_분류": [EXEMPT, DERIVED, TOLERANCE, TRUE_BAD],
                     "확정": "모든 출처가 기사로 확인되고 진짜 불일치가 1개 이상"},
           "confirmed": conf}
    Path(path).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[재검사] 보고서 저장 — {path} (확정 {len(conf)}편)")


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
    ap.add_argument("--report", default=str(HERE / "data" / "recheck_report.json"))
    a = ap.parse_args()
    if a.apply:
        return apply(a.src or a.out)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    dry_run(a.out, a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
