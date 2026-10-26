#!/usr/bin/env python3
"""
translate_content.py — 콘텐츠 번역 엔진 (단계 3 · daily full 회차)
================================================================================
막으려는 것 한 문장: **번역이 원문에 없는 사실·숫자·권고를 만들어 다른 언어 화면에 게시되는 것.**
그래서 번역은 관문(i18n_gates: 숫자·용어·준법·마크업·링크)을 통과해야만 캐시에 'ok' 로 들어가고,
빌드는 ok 캐시만 화면에 끼운다. 통과 못 하면 원문 + '번역 준비 중' 표식 — 침묵이 기본값이다.

흐름 (회차마다)
  1. 수집: i18n_content.SPECS 의 모든 kind × en·es·ja → 옮길 칸이 있고 캐시가 없거나 원문이 바뀐 항목
     · 철회된 에세이는 모든 언어 캐시를 **동시에** 지운다
     · 최근 실패한 항목(같은 원문)은 FAIL_COOLDOWN_DAYS 동안 다시 시도하지 않는다(같은 돈을 두 번 쓰지 않게)
     · 그 언어의 첫 회차(캐시가 비었음)는 최근 FIRST_RUN_DAYS 일만
  2. 최신순 정렬 → 회당 상한 I18N_CAP(기본 40, 항목×언어 단위)
  3. 번역: 사실 중심(실적·계약·조달 에세이, 탐사 보고서)은 I18N_MODEL_FACT, 나머지는 I18N_MODEL
  4. 관문 → 위반이면 사유를 주고 **1회** 재생성 → 그래도면 미게시(status=failed, 사유 기록)
  5. 원장: fetch_status.json 에 kind="i18n" 언어별 1행(시도·게시·관문 실패·API 실패·토큰)
     관제탑(judge_i18n)이 관문 실패율 >30% 가 2회 연속이면 소장 DM 1건(7일 재알림)

번역 원천: ko 칸은 한국어 원문, en 칸(원제·헤드라인)은 영어 원문에서 es·ja 로 **직접** — 한국어를 거치지 않는다.
API 키가 없으면(로컬) 아무것도 쓰지 않고 끝난다 — 원장에도 쓰지 않는다(로컬 실행이 CI 원장을 오염시키지 않게).
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import i18n                    # noqa: E402
import i18n_content as IC      # noqa: E402
import i18n_gates as Q         # noqa: E402

KST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5"          # I18N_MODEL 로 덮어쓴다
MODEL_FACT = "claude-sonnet-5-5"    # I18N_MODEL_FACT 로 덮어쓴다
CAP = 40                            # I18N_CAP — 회당 요청 묶음 상한(재생성 제외)
BUDGET_S = 900                      # I18N_BUDGET_S — 새 묶음을 시작하지 않는 경과 시간(초). 스텝 제한(25분) 안에서
                                    # 원장을 반드시 쓰고 끝나게(원장 없이 죽으면 관제탑이 '못 나간 회차'로 센다)
WORKERS = 4                         # 동시 요청 수
FIRST_RUN_DAYS = 30
FAIL_COOLDOWN_DAYS = 7
LANG_NAME = {"en": "English", "es": "Spanish (neutral, for Spain and Latin America)", "ja": "Japanese"}
UNIT_RULE = {
    "en": "Korean 조 = trillion, 억 = 100 million (e.g. 5,094조원 → KRW 5,094 trillion; 146억 달러 → $14.6 billion). "
          "Use T/B/M only where the source uses abbreviated amounts.",
    "es": "Korean 조 = billón (10^12), 억 = 100 millones. Write 'billones' for 10^12 and 'mil millones' for 10^9 "
          "(146억 달러 → 14.600 millones de dólares or 14,6 mil millones de dólares). Decimal comma.",
    "ja": "Keep 兆 and 億 (조 → 兆, 억 → 億, 만 → 万). Numbers stay identical.",
}


def models():
    return (os.environ.get("I18N_MODEL", "").strip() or MODEL,
            os.environ.get("I18N_MODEL_FACT", "").strip() or MODEL_FACT)


def cap():
    try:
        return max(0, int(os.environ.get("I18N_CAP", "").strip() or CAP))
    except ValueError:
        return CAP


def glossary_lines(lang):
    out = []
    for t in Q.glossary_terms():
        if t.get("ko") and t.get(lang):
            out.append(f"- {t['ko']} / {t.get('en', '')} → {t[lang]}")
    return "\n".join(out)


def build_prompt(lang, kind, fields, reasons=None):
    """fields: {경로: (원문, 원문 언어, html)} → 프롬프트. 응답은 같은 키의 JSON 하나."""
    payload = {k: v[0] for k, v in fields.items()}
    src_langs = sorted({v[1] for v in fields.values()})
    src_name = " and ".join({"ko": "Korean", "en": "English"}[s] for s in src_langs)
    p = f"""You translate content for UNIVERTRIX (univertrix.com), a global market-cap observatory.
Translate every value of the JSON object below from {src_name} into {LANG_NAME[lang]}.
Return ONLY one JSON object with exactly the same keys.

Rules (all mandatory):
1. Translate faithfully. Do not add, drop, summarize or reinterpret facts. No new numbers, names or claims.
2. Every number in the source must appear in the translation with the same value. Units: {UNIT_RULE[lang]}
   Dates, years and quarters keep their values.
3. HTML/markdown: keep every tag, attribute, link URL and markdown link target exactly as in the source,
   in the same order. Translate only human-readable text. Keep placeholders like ⟦B0⟧ unchanged, once each.
4. Never turn description into advice: no buy/sell/overweight/target-price wording that the source does not have.
   Keep disclaimers as disclaimers.
5. Site terms must use this glossary (source term → {lang} term):
{glossary_lines(lang)}
6. Proper nouns (companies, people, products) use their usual {LANG_NAME[lang].split(' ')[0]} form.
7. Style: concise, natural, neutral observatory tone.

Content type: {kind}
JSON:
{json.dumps(payload, ensure_ascii=False)}"""
    if reasons:
        p += ("\n\nYour previous translation was rejected by the verification gates for these reasons "
              "(fix every one, change nothing else):\n- " + "\n- ".join(reasons))
    return p


def call_model(prompt, model, api_key):
    """→ (응답 사전, 사용량). 실패는 예외(호출자가 API 실패로 센다)."""
    import requests
    fact = model == models()[1]
    body = {"model": model, "max_tokens": 16000 if fact else 8000,
            "messages": [{"role": "user", "content": prompt}]}
    if fact:
        body["output_config"] = {"effort": "low"}
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=300 if fact else 180)
    r.raise_for_status()
    data = r.json()
    if data.get("stop_reason") == "refusal":
        raise RuntimeError("refusal")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```(?:json)?", "", text).strip()
    out = json.loads(text[text.find("{"):text.rfind("}") + 1])
    if not isinstance(out, dict):
        raise ValueError("JSON 객체가 아님")
    return out, data.get("usage") or {}


def gate(fields, out, lang, prefix=""):
    """→ (통과한 번역 {경로: 문장}, 사유 목록). 보호 블록 복원 후 원문과 대조한다."""
    reasons, ok = [], {}
    for path, (src, src_lang, mode) in fields.items():
        tag = f"[{prefix}{path}]"
        dst = out.get(prefix + path)
        if not isinstance(dst, str) or not dst.strip():
            reasons.append(f"{tag} 번역이 비었다")
            continue
        if i18n.has_hangul(dst):
            reasons.append(f"{tag} 한글이 남았다 — 전부 옮길 것")
        for name, why in Q.check(src, dst, lang, src_lang):
            reasons.append(f"{tag} {why}")
        if mode == "md":
            reasons += [f"{tag} {why}" for why in Q.md_shape(src, dst)]
        ok[path] = dst
    return ok, reasons


SEP = "§"


def translate_batch(kind, lang, jobs, model, api_key, caller=None):
    """같은 언어·종류의 항목 묶음 하나 → 요청 1회(+관문 실패분만 사유를 주고 1회 재생성).

    jobs: [(id, 칸)] → ([(id, 결과)], 사용량, 요청 수). 결과 = {status: ok|failed|api_error, fields, reasons, attempts}
    짧은 항목(종목 설명 한 줄 등)을 한 요청에 묶는다 — 항목마다 부르면 같은 지시문 값을 수백 번 낸다.
    키는 '번호§칸 경로' — 관문은 항목별로 따로 잰다(한 항목의 실패가 묶음 전체를 버리지 않게).
    """
    caller = caller or call_model
    multi = len(jobs) > 1
    prefix = [f"{n}{SEP}" if multi else "" for n in range(len(jobs))]
    prot, blocks = [], []
    for iid, f in jobs:
        pj, bj = {}, {}
        for k, (src, sl, mode) in f.items():
            if mode == "html":
                t, b = Q.protect(src)
                pj[k], bj[k] = (t, sl, mode), b
                # 차트 속 한글 라벨은 따로 옮긴다(도형·좌표는 보호) — 키 '칸~블록.글자'
                for bi, blk in enumerate(b):
                    for ti, txt in enumerate(Q.svg_texts(blk)):
                        pj[f"{k}~{bi}.{ti}"] = (txt, sl, "")
            else:
                pj[k] = (src, sl, mode)
        prot.append(pj)
        blocks.append(bj)
    usage = {"input_tokens": 0, "output_tokens": 0}
    results = {n: None for n in range(len(jobs))}
    pending, reasons, requests_n = list(range(len(jobs))), {}, 0
    for attempt in (1, 2):
        payload = {prefix[n] + k: v for n in pending for k, v in prot[n].items()}
        why = [r for n in pending for r in reasons.get(n, [])] if attempt == 2 else None
        requests_n += 1
        try:
            out, use = caller(build_prompt(lang, kind, payload, why), model, api_key)
        except Exception as e:
            for n in pending:
                results[n] = {"status": "api_error", "fields": {}, "reasons": [], "attempts": attempt,
                              "error": str(e)[:200]}
            break
        for u in usage:
            usage[u] += int(use.get(u) or 0)
        still = []
        for n in pending:
            o = {}
            for k in jobs[n][1]:
                v = out.get(prefix[n] + k)
                if k in blocks[n] and isinstance(v, str):
                    filled = [Q.svg_fill(blk, [out.get(f"{prefix[n]}{k}~{bi}.{ti}")
                                               for ti in range(len(Q.svg_texts(blk)))])
                              for bi, blk in enumerate(blocks[n][k])]
                    v = Q.restore(v, filled)
                o[prefix[n] + k] = v
            ok, rs = gate(jobs[n][1], o, lang, prefix[n])
            if rs:
                reasons[n] = rs
                still.append(n)
                results[n] = {"status": "failed", "fields": {}, "reasons": rs, "attempts": attempt}
            else:
                results[n] = {"status": "ok", "fields": ok, "reasons": [], "attempts": attempt}
        pending = still
        if not pending:
            break
    return [(jobs[n][0], results[n]) for n in range(len(jobs))], usage, requests_n


def translate_item(kind, lang, fields, model, api_key, caller=None):
    """항목 하나(테스트·수동 실행용) — 묶음 하나짜리 translate_batch."""
    res, usage, _ = translate_batch(kind, lang, [("one", fields)], model, api_key, caller)
    r = dict(res[0][1])
    r["usage"] = usage
    return r


SMALL = 1500          # 이보다 짧은 항목은 묶는다(글자)
BATCH_CHARS = 5000    # 묶음 하나의 원문 글자 상한
BATCH_ITEMS = 20


def batches(jobs):
    """최신순 작업 → 요청 묶음 목록(최신 묶음 먼저). 같은 언어·종류·모델끼리만 묶는다."""
    out, open_ = [], {}
    for job in jobs:
        date, kind, iid, L, f, fact = job
        size = sum(len(v[0]) for v in f.values())
        key = (L, kind, fact)
        if size > SMALL:
            out.append([job])
            continue
        b = open_.get(key)
        if b is None or len(b) >= BATCH_ITEMS or sum(sum(len(v[0]) for v in j[4].values()) for j in b) + size > BATCH_CHARS:
            b = []
            open_[key] = b
            out.append(b)
        b.append(job)
    return out


# ── 수집 ───────────────────────────────────────────────────────────────────
def probe_nodes():
    """탐사 보고서 공개분(준법 필터 통과본) — probe_cockpit 이 화면에 싣는 것과 같은 절."""
    try:
        import agent_report as ar
        import probe_cockpit as pc
        axes = (ar.read_state() or {}).get("axes") or {}
        return [(f"{stem}-s{i}", date, node) for stem, i, date, node in pc.report_nodes(ar, axes)]
    except Exception as e:
        print(f"[번역] 탐사 보고서 수집 실패 — 이번 회차 제외 ({e})", file=sys.stderr)
        return []


def thesis_nodes():
    """동행 논제 원장 — 화면(build_site.companion_data)과 같은 칸."""
    try:
        import companion_essays as ce
        from build_site import thesis_node
        cfg = json.loads((IC.DATA / "companion_sources.json").read_text(encoding="utf-8"))
        out = []
        for c in cfg.get("companies", []):
            t = ce.read_thesis(c["slug"])
            if not t.get("placeholder"):
                out.append((c["slug"], "", thesis_node(t)))    # 논제는 늘 걸려 있는 문서 — 정적 문서처럼 오늘로
        return out
    except Exception as e:
        print(f"[번역] 논제 원장 수집 실패 — 이번 회차 제외 ({e})", file=sys.stderr)
        return []


def collect(today, data_dir=None, cache_dir=None, log=print):
    """→ (작업 목록, 정리 기록). 작업 = (날짜, kind, id, 언어, 칸, 사실 여부)."""
    jobs, purged = [], 0
    cutoff_first = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=FIRST_RUN_DAYS)).strftime("%Y-%m-%d")
    first = {L: not any((Path(cache_dir or IC.CACHE) / L).glob("*/*.json")) for L in IC.TARGETS}
    docs = {}
    sources = []
    for kind, spec in IC.SPECS.items():
        doc = docs.setdefault(spec["file"], IC.load_doc(spec, data_dir))
        if doc is None:
            continue
        sources.append((kind, spec, IC.items(kind, doc)))
        if kind == "essay":
            # 철회: 원장에 있지만 공개가 아닌 에세이 → 모든 언어 캐시 동시 삭제
            for e in doc.get("essays") or []:
                if isinstance(e, dict) and not spec["keep"](e):
                    purged += IC.purge("essay", spec["id"](e), cache_dir)
    if data_dir is None:          # 판단층 원장은 실제 저장소에서만(테스트는 data_dir 로 격리)
        sources.append(("probe", IC.PROBE, probe_nodes()))
        sources.append(("thesis", IC.THESIS, thesis_nodes()))
    for kind, spec, its in sources:
        for iid, date, node in its:
            for L in IC.TARGETS:
                f = IC.fields_for(spec, node, L)
                if not f:
                    continue
                if first[L] and date and date < cutoff_first:
                    continue
                h = IC.src_hash(f)
                rec = IC.read_cache(L, kind, iid, cache_dir)
                if rec and rec.get("src_hash") == h:
                    if rec.get("status") == "ok":
                        continue
                    last = rec.get("at", "")[:10]
                    if last and last >= (datetime.strptime(today, "%Y-%m-%d")
                                         - timedelta(days=FAIL_COOLDOWN_DAYS)).strftime("%Y-%m-%d"):
                        continue
                fact = bool(spec.get("fact") and spec["fact"](node))
                jobs.append((date or today, kind, iid, L, f, fact))
    jobs.sort(key=lambda j: (j[0], j[1], j[2]), reverse=True)
    if purged:
        log(f"[번역] 철회 에세이 캐시 {purged}건 삭제(모든 언어 동시)")
    return jobs, purged


def run(today=None, data_dir=None, cache_dir=None, caller=None, api_key=None, record=True, log=print):
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key and caller is None:
        log("[번역] ANTHROPIC_API_KEY 없음 — 이번 실행은 번역하지 않는다(로컬). 원장 미기록")
        return {"skipped": "no_key"}
    all_jobs, purged = collect(today, data_dir, cache_dir, log)
    all_batches = batches(all_jobs)
    todo = all_batches[:cap()]          # 회당 상한 = 요청 묶음 수(재생성 제외)
    m_base, m_fact = models()
    stats = {L: {"attempted": 0, "published": 0, "gate_failed": 0, "api_errors": 0, "requests": 0,
                 "tokens_in": 0, "tokens_out": 0, "backlog": 0, "reasons": {}} for L in IC.TARGETS}
    for j in all_jobs:
        stats[j[3]]["backlog"] += 1
    t0 = time.time()
    try:
        budget = float(os.environ.get("I18N_BUDGET_S", "").strip() or BUDGET_S)
    except ValueError:
        budget = BUDGET_S
    from concurrent.futures import ThreadPoolExecutor

    def work(b):
        _, kind, _, L, _, fact = b[0]
        model = m_fact if fact else m_base
        return b, model, translate_batch(kind, L, [(j[2], j[4]) for j in b], model, api_key, caller)

    done_batches, stopped = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i in range(0, len(todo), WORKERS):
            if time.time() - t0 > budget:
                stopped = len(todo) - i
                log(f"[번역] 시간 예산 {budget:.0f}s 초과 — 남은 {stopped}묶음은 다음 회차로")
                break
            done_batches += list(ex.map(work, todo[i:i + WORKERS]))
    for b, model, (res, usage, nreq) in done_batches:
        _, kind, _, L, _, fact = b[0]
        s = stats[L]
        s["requests"] += nreq
        s["tokens_in"] += usage["input_tokens"]
        s["tokens_out"] += usage["output_tokens"]
        at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        for job, (iid, r) in zip(b, res):
            f = job[4]
            s["attempted"] += 1
            rec = {"kind": kind, "id": iid, "lang": L, "src_hash": IC.src_hash(f), "model": model,
                   "at": at, "attempts": r["attempts"]}
            if r["status"] == "ok":
                s["published"] += 1
                IC.write_cache(L, kind, iid, dict(rec, status="ok", fields=r["fields"]), cache_dir)
            elif r["status"] == "failed":
                s["gate_failed"] += 1
                for why in r["reasons"]:
                    g = re.sub(r"^\[[^\]]*\]\s*", "", why).split(":")[0].split(" — ")[0]
                    s["reasons"][g] = s["reasons"].get(g, 0) + 1
                IC.write_cache(L, kind, iid, dict(rec, status="failed", fields={}, reasons=r["reasons"][:8]),
                               cache_dir)
                log(f"[번역] {L} {kind}/{iid} 관문 미통과 — 원문 유지: {r['reasons'][:2]}")
            else:
                s["api_errors"] += 1
                log(f"[번역] {L} {kind}/{iid} API 실패 — {r.get('error')}")
    for L, s in stats.items():
        s["backlog"] = max(0, s["backlog"] - s["published"])
        log(f"[번역] {L}: 시도 {s['attempted']} · 게시 {s['published']} · 관문 실패 {s['gate_failed']}"
            f" · API 실패 {s['api_errors']} · 요청 {s['requests']} · 토큰 {s['tokens_in']}/{s['tokens_out']}"
            f" · 남은 대기 {s['backlog']}")
    log(f"[번역] 전체 대기 {len(all_jobs)}건({len(all_batches)}묶음) · 이번 회차 {len(done_batches)}묶음"
        f" · 모델 {m_base}/{m_fact} · {time.time() - t0:.0f}s")
    if record:
        ledger(stats)
    return {"stats": stats, "backlog": len(all_jobs), "batches": len(all_batches), "done": len(done_batches),
            "purged": purged}


def ledger(stats):
    """fetch_status.json 에 kind='i18n' 언어별 1행. 판정은 관제탑 judge_i18n 단독 관할."""
    import feed_client as fc
    for L, s in stats.items():
        if s["api_errors"] and not s["published"] and not s["gate_failed"]:
            outcome = "api_error"
        elif s["gate_failed"]:
            outcome = "gate_fail"
        elif s["attempted"]:
            outcome = "ok"
        else:
            outcome = "idle"
        fc.record("i18n", L, outcome, None, s["published"])
        fc._buffer[f"i18n:{L}"].update({k: s[k] for k in ("attempted", "gate_failed", "api_errors", "requests",
                                                           "tokens_in", "tokens_out", "backlog")},
                                       reasons=dict(sorted(s["reasons"].items(), key=lambda kv: -kv[1])[:5]))
    fc.flush()


def main():
    try:
        r = run()
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    st = (r or {}).get("stats") or {}
    # API 가 전부 실패한 회차는 실패로 보고한다(침묵은 통과가 아니다) — 워크플로 outcome 이 failure 가 된다
    tried = sum(s["attempted"] for s in st.values())
    if tried and all(s["api_errors"] == s["attempted"] for s in st.values() if s["attempted"]):
        print("[번역] 시도 전부 API 실패 — 실패로 보고", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
