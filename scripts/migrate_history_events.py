#!/usr/bin/env python3
"""
migrate_history_events.py — 변동 이력 아카이브 이관(1회성 · AI 없음) — UNIVERTRIX GLOBAL F1
================================================================================
막으려는 것 한 문장: **옛 기록이 한국어 문장(HTML)으로만 남아 다른 언어 화면에서 영영 원문으로 보이는 것.**

단계 2 이후의 기록은 사건(events) 코드로 저장되고 화면이 그 언어로 문장을 만든다
(weekly_history.render_event). 그 전 19주치(history-top20 · history-latent)는 문장만 있다.
이 도구는 그 문장을 정규식으로 사건 코드로 되돌린다.

수용 기준(지시서 F1):
  ① 이관 후 ko 화면이 이관 전과 **글자 단위로 같다** — 그래서 판정은 '정규식이 맞았다'가 아니라
     **되돌린 사건을 같은 렌더러로 다시 그린 ko 문장 == 원문** 일 때만 이관한다.
     정규식이 틀리면(값을 잘못 읽으면) 다시 그린 문장이 원문과 달라져 저절로 원문으로 남는다.
  ② 못 읽은 항목은 원문 그대로(사건 kind='raw') — 개수·예시를 보고한다.
  ③ 드라이런이 기본이다. --apply 일 때만 파일을 쓴다.

무엇을 되돌리나
  items 블록의 항목 → events (enter·exit·rank_up·rank_down·mc_move·l_enter·l_exit·l_rank·l_mom·
                      grad·t_in·t_out·sector·pt_hot·pt_new·pt_grad·watch) · 라벨 → label_id
  stats 블록(환율·환산) → fx 상태 {rate, date, mult}
  기간 '10.17 → 10.24 (주간)' → prev/curr
  prose 블록(자유 서술)은 건드리지 않는다 — 라벨만 id 로(있으면)

종목명은 원문 그대로 이름 칸에 둔다(ko 렌더러는 이름을 그대로 쓴다). 티커는 스냅샷·이름 사전에서
이름으로 찾는다 — 다른 언어 화면의 이름 현지화(삼성전자 → Samsung Electronics)에 쓰인다.

실행: python scripts/migrate_history_events.py            # 드라이런(보고만)
      python scripts/migrate_history_events.py --apply    # 파일 갱신
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import i18n                 # noqa: E402
import weekly_history as wh  # noqa: E402

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
FILES = ("history-top20.json", "history-latent.json")

S = r"<strong>(?P<name>[^<]+)</strong>"
MC = r"\$[\d.]+[TB]"


def _ko(key, **p):
    return i18n.t("ko", key, **p)


def parse_mc(s):
    """'$1.27T' → 1270.0 · '$111B' → 111.0 (fmt_mc 의 역)."""
    v = float(s[1:-1])
    return v * 1000 if s.endswith("T") else v


def _int(s):
    return int(s) if s not in (None, "?") else "?"


# ── 이름 → 티커 ────────────────────────────────────────────────────────────
def name_index(data_dir=None):
    d = Path(data_dir or DATA)
    idx = {}
    for snap in sorted((d / "snapshots").glob("*.json")):
        try:
            doc = json.loads(snap.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = [s for v in (doc.get("regions") or {}).values() for s in (v or [])] + list(doc.get("latent") or [])
        for s in rows:
            if isinstance(s, dict) and s.get("name") and s.get("ticker"):
                idx.setdefault(s["name"], s["ticker"])
    for tk, ent in i18n.names().items():
        if isinstance(ent, dict) and ent.get("ko"):
            idx.setdefault(ent["ko"], tk)
    return idx


# ── 문장 → 사건 후보 ────────────────────────────────────────────────────────
def _region_key(label):
    for k in wh.REGION_LABELS:
        if _ko("region." + k) == label:
            return k
    return None


def p_enter(s, g):
    reg = _region_key(g["reg"]) if g.get("reg") else None
    if g.get("reg") and not reg:
        return None
    return {"k": "enter", "region": reg, "name": g["name"], "rank": int(g["rank"]), "mc": parse_mc(g["mc"])}


def p_exit(s, g):
    reg = _region_key(g["reg"]) if g.get("reg") else None
    if g.get("reg") and not reg:
        return None
    return {"k": "exit", "region": reg, "name": g["name"]}


def p_facts(f):
    """'21위, $700B, 1Y +181%' → {rank, mc, mom} (hist.ev.facts 의 역)."""
    out = {}
    for part in f.split(", "):
        m = re.fullmatch(r"(\d+)위", part)
        if m:
            out["rank"] = int(m.group(1))
            continue
        if re.fullmatch(MC, part):
            out["mc"] = parse_mc(part)
            continue
        m = re.fullmatch(r"1Y ([+-]\d+)%", part)
        if m:
            out["mom"] = int(m.group(1))
            continue
        return None
    return out


def p_why_in(why):
    m = re.fullmatch(r"기준 충족 (\d+)거래일 연속", why)
    if m:
        return {"streak": int(m.group(1))}
    m = re.fullmatch(r"(.+) 자리 교체 · 우위 (\d+)거래일 연속", why)
    if m:
        return {"replacing": m.group(1), "swap": int(m.group(2))}
    return None


def p_t_in(s, g):
    ev = {"k": "t_in", "name": g["name"], "when": g["when"]}
    if g.get("f"):
        f = p_facts(g["f"])
        if f is None:
            return None
        ev.update(f)
    w = p_why_in(g["why"])
    if w is None:
        return None
    ev.update(w)
    return ev


def p_t_out(s, g):
    ev = {"k": "t_out", "name": g["name"], "when": g["when"]}
    why = g.get("why")
    if why:
        m = re.fullmatch(r"(.+)에 자리 교체", why)
        if m:
            ev["by"] = m.group(1)
        elif re.fullmatch(r"데이터 결측 \d+거래일 연속", why):
            ev["carried"] = int(re.search(r"\d+", why).group())
        elif re.fullmatch(r"기준 미달 \d+거래일 연속", why):
            ev["out"] = int(re.search(r"\d+", why).group())
        else:
            return None
    return ev


def p_sector(s, g, idx):
    names = g["names"].split(", ")
    if int(g["n"]) != len(names):
        return None
    th = g["theme"]
    try:
        import generate_candidates as gc
        tid = gc.THEME_ID.get(th, "")
    except Exception:
        tid = ""
    return {"k": "sector", "theme": th, "tid": tid, "names": [{"name": n, "tk": idx.get(n)} for n in names]}


def p_watch(s, g, idx):
    one = re.compile(S + r" (?:(?P<w>편입|제외|교체) 카운트 (?P<a>\d+)/(?P<b>\d+|\?)"
                         r"|데이터 결측 (?P<ma>\d+)/(?P<mb>\d+|\?)|기준 충족 (?P<wa>\d+)거래일 · 빈자리 대기)")
    parts = []
    for m in one.finditer(s):
        d = m.groupdict()
        nm = d["name"]
        if d["w"]:
            parts.append({"w": {"편입": "in", "제외": "out", "교체": "swap"}[d["w"]], "name": nm,
                          "tk": idx.get(nm), "a": int(d["a"]), "b": _int(d["b"])})
        elif d["ma"]:
            parts.append({"w": "miss", "name": nm, "tk": idx.get(nm), "a": int(d["ma"]), "b": _int(d["mb"])})
        else:
            parts.append({"w": "wait", "name": nm, "tk": idx.get(nm), "a": int(d["wa"])})
    return {"k": "watch", "parts": parts} if parts else None


# (이름, 정규식, 변환기) — 순서대로 시도한다. 변환 결과는 다시 그려 원문과 같을 때만 받는다.
PATTERNS = [
    ("enter", r"(?:\[(?P<reg>[^\]]+)\] )?" + S + r" TOP 20 신규 진입 \(<span class='em-up'>(?P<rank>\d+)위, (?P<mc>" + MC + r")</span>\)",
     p_enter),
    ("exit", r"(?:\[(?P<reg>[^\]]+)\] )?" + S + r" TOP 20 이탈", p_exit),
    ("rank_up", S + r" <span class='em-up'>(?P<a>\d+)위 → (?P<b>\d+)위 상승</span> \((?P<mc>" + MC + r")\)",
     lambda s, g: {"k": "rank_up", "name": g["name"], "a": int(g["a"]), "b": int(g["b"]), "mc": parse_mc(g["mc"])}),
    ("rank_down", S + r" <span class='em-down'>(?P<a>\d+)위 → (?P<b>\d+)위 하락</span>",
     lambda s, g: {"k": "rank_down", "name": g["name"], "a": int(g["a"]), "b": int(g["b"])}),
    ("mc_move", S + r" 시총 <span class='em-(?:up|down)'>(?P<pct>[+-]?\d+)%</span> \((?P<old>" + MC + r") → (?P<new>" + MC + r")\)",
     lambda s, g: {"k": "mc_move", "name": g["name"], "pct": int(g["pct"]),
                   "old": parse_mc(g["old"]), "new": parse_mc(g["new"])}),
    ("t_in", S + r" 잠재지배자 신규 편입 (?:\(<span class='em-up'>(?P<f>[^<]*)</span>\) )?— (?P<why>.+), (?P<when>[\d.]+) 확정",
     p_t_in),
    ("l_enter", S + r" 잠재지배자 신규 편입 \(<span class='em-up'>(?P<rank>\d+|\?)위, (?P<mc>" + MC + r")(?:, 1Y \+(?P<mom>-?\d+)%)?</span>\)",
     lambda s, g: {"k": "l_enter", "name": g["name"], "rank": _int(g["rank"]), "mc": parse_mc(g["mc"]),
                   "mom": None if g.get("mom") is None else int(g["mom"])}),
    ("t_out", S + r" 잠재지배자 제외(?: — (?P<why>.+))?, (?P<when>[\d.]+) 확정", p_t_out),
    ("l_exit", S + r" 잠재지배자 목록에서 제외", lambda s, g: {"k": "l_exit", "name": g["name"]}),
    ("grad", S + r" — <span class='em-up'>★ TOP 20 졸업 \(글로벌 (?P<rank>\d+)위\)</span>",
     lambda s, g: {"k": "grad", "name": g["name"], "rank": int(g["rank"])}),
    ("l_rank", S + r" 잠재 순위 <span class='em-(?:up|down)'>(?P<a>\d+)위 → (?P<b>\d+)위 (?:상승|하락)</span>",
     lambda s, g: {"k": "l_rank", "name": g["name"], "a": int(g["a"]), "b": int(g["b"])}),
    ("l_mom", S + r" 1Y 모멘텀 <span class='em-(?:up|down)'>(?P<a>-?\d+)% → (?P<b>-?\d+)%</span>",
     lambda s, g: {"k": "l_mom", "name": g["name"], "a": int(g["a"]), "b": int(g["b"])}),
    ("pt_hot", r"<strong>가장 폭발적</strong>: (?P<name>.+) — 1Y <span class='em-up'>\+(?P<m>-?\d+)%</span>",
     lambda s, g: {"k": "pt_hot", "name": g["name"], "m": int(g["m"])}),
    ("pt_new", r"<strong>신규 중 최고 순위</strong>: (?P<name>.+) — 글로벌 (?P<rank>\d+)위 데뷔",
     lambda s, g: {"k": "pt_new", "name": g["name"], "rank": int(g["rank"])}),
    ("pt_grad", r"<strong>졸업</strong>: (?P<names>.+) — TOP 20 진입",
     lambda s, g: {"k": "pt_grad", "names": [{"name": n} for n in g["names"].split(", ")]}),
    ("sector", r"<strong>(?P<theme>.+) \((?P<n>\d+)\)</strong>: (?P<names>.+)", p_sector),
    ("watch", r"<strong>[^<]+</strong> (?:편입 카운트|제외 카운트|교체 카운트|데이터 결측|기준 충족) .*", p_watch),
]
_COMPILED = [(n, re.compile(r), f) for n, r, f in PATTERNS]


def _with_tk(ev, idx):
    if ev.get("name") and not ev.get("tk"):
        ev["tk"] = idx.get(ev["name"])
    for x in ev.get("names") or []:
        if x.get("name") and not x.get("tk"):
            x["tk"] = idx.get(x["name"])
    return ev


def to_event(item, idx, disabled=()):
    """항목 문장 → (사건, 패턴 이름). 다시 그린 ko 문장이 원문과 글자 단위로 같을 때만. 아니면 raw."""
    for name, rx, conv in _COMPILED:
        if name in disabled:
            continue
        m = rx.fullmatch(item)
        if not m:
            continue
        g = {k: v for k, v in m.groupdict().items()}
        try:
            ev = conv(item, g, idx) if conv in (p_sector, p_watch) else conv(item, g)
        except Exception:
            ev = None
        if not ev:
            continue
        ev = _with_tk(ev, idx)
        if wh.render_event(ev, "ko") == item:
            return ev, name
    return {"k": "raw", "html": item}, None


# ── 라벨·기간·환율 ──────────────────────────────────────────────────────────
LABEL_IDS = ("weekly", "latent_weekly", "points", "watch", "major", "grad_manual", "new_manual", "data_note")


def to_label(label):
    if not label:
        return None, None
    for lid in LABEL_IDS:
        if _ko("hist.lbl." + lid) == label:
            return lid, {}
    m = re.fullmatch(r"섹터 분포 \((\d+)종목\)", label)
    if m and _ko("hist.lbl.sector", n=int(m.group(1))) == label:
        return "sector", {"n": int(m.group(1))}
    return None, None


def to_period(entry):
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2}) → (\d{1,2})\.(\d{1,2}) \(주간\)", entry.get("period") or "")
    y = re.match(r"(20\d{2})", entry.get("date") or "")
    if not m or not y:
        return None
    a_m, a_d, b_m, b_d = map(int, m.groups())
    yb = int(y.group(1))
    ya = yb - 1 if a_m > b_m else yb
    prev, curr = f"{ya}-{a_m:02d}-{a_d:02d}", f"{yb}-{b_m:02d}-{b_d:02d}"
    try:
        ok = _ko("hist.period", a=wh._short_date(prev), b=wh._short_date(curr)) == entry["period"]
    except ValueError:
        ok = False
    return (prev, curr) if ok else None


def to_fx(block):
    its = block.get("items") or []
    if len(its) != 2 or block.get("label") != _ko("hist.lbl.fx"):
        return None
    m1 = re.fullmatch(r"([\d,]+) \((\d{1,2}/\d{1,2}) 기준\)", str(its[0].get("v")))
    m2 = re.fullmatch(r"시총\(조원\) = 시총\(USD bn\) × ([\d.]+)", str(its[1].get("v")))
    if its[0].get("k") != "USD/KRW" or not m1 or not m2:
        return None
    fx = {"rate": m1.group(1), "date": m1.group(2), "mult": m2.group(1)}
    again = [{"k": "USD/KRW", "v": _ko("hist.fx.rate", r=fx["rate"], d=fx["date"])},
             {"k": _ko("hist.fx.formula_k"), "v": _ko("hist.fx.formula", m=fx["mult"])}]
    return fx if again == its else None


# ── 이관 ───────────────────────────────────────────────────────────────────
def migrate_doc(doc, idx, disabled=()):
    """문서 사본 → (이관본, 통계). 원문 칸(items·label·period·v)은 **한 글자도 바꾸지 않는다** — 상태만 곁에 붙인다."""
    doc = json.loads(json.dumps(doc, ensure_ascii=False))
    st = {"items": 0, "migrated": 0, "raw": 0, "by_kind": {}, "raw_examples": [], "fx": 0, "fx_raw": 0,
          "period": 0, "period_raw": 0, "labels": 0, "labels_raw": [], "prose": 0}
    for e in doc.get("entries") or []:
        if not isinstance(e, dict):
            continue
        if not (e.get("prev") and e.get("curr")):
            pc = to_period(e)
            if pc:
                e["prev"], e["curr"] = pc
                st["period"] += 1
            elif e.get("period"):
                st["period_raw"] += 1
        for b in e.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            if b.get("label") and not b.get("label_id") and b.get("type") != "stats":
                lid, lp = to_label(b["label"])
                if lid:
                    b["label_id"], b["label_p"] = lid, lp
                    st["labels"] += 1
                else:
                    st["labels_raw"].append(b["label"])
            if b.get("type") == "items" and b.get("events") is None:
                evs = []
                for it in b.get("items") or []:
                    st["items"] += 1
                    ev, kind = to_event(it, idx, disabled)
                    evs.append(ev)
                    if kind:
                        st["migrated"] += 1
                        st["by_kind"][kind] = st["by_kind"].get(kind, 0) + 1
                    else:
                        st["raw"] += 1
                        if len(st["raw_examples"]) < 12:
                            st["raw_examples"].append(f"{e.get('date')} | {it[:110]}")
                b["events"] = evs
            elif b.get("type") == "stats" and not b.get("fx"):
                fx = to_fx(b)
                if fx:
                    b["fx"] = fx
                    st["fx"] += 1
                else:
                    st["fx_raw"] += 1
            elif b.get("type") == "prose":
                st["prose"] += 1
    return doc, st


def visible_ko(doc):
    """ko 화면이 그리는 칸만(날짜·자동 배지·기간·라벨·항목·서술·환율 k/v) — 회귀 비교의 자."""
    out = []
    for e in doc.get("entries") or []:
        out.append(("E", e.get("date"), bool(e.get("auto")), e.get("period")))
        for b in e.get("blocks") or []:
            out.append(("B", b.get("type"), b.get("label"), b.get("text"),
                        tuple(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in b.get("items") or [])))
    return out


def rerender_ko(doc):
    """이관본을 렌더러로 다시 그린 ko — 사건에서 만든 문장이 원문과 같은지 보는 두 번째 자."""
    return {"entries": [wh.render_entry(e, "ko") for e in doc.get("entries") or []]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--data", default="")
    a = ap.parse_args()
    data_dir = Path(a.data) if a.data else DATA
    idx = name_index(data_dir)
    bad = 0
    for fn in FILES:
        p = data_dir / fn
        doc = json.loads(p.read_text(encoding="utf-8"))
        new, st = migrate_doc(doc, idx)
        same = visible_ko(new) == visible_ko(doc)
        same2 = visible_ko(rerender_ko(new)) == visible_ko(doc)
        print(f"== {fn}: 항목 {st['items']} · 이관 {st['migrated']} · 원문 유지 {st['raw']} "
              f"· 환율 {st['fx']}/{st['fx'] + st['fx_raw']} · 기간 {st['period']}/{st['period'] + st['period_raw']} "
              f"· 라벨 {st['labels']} · 서술 블록 {st['prose']}(손대지 않음)")
        print(f"   유형별: {json.dumps(st['by_kind'], ensure_ascii=False)}")
        print(f"   ko 회귀: 원문 칸 {'동일' if same else '달라짐 ✗'} · 사건에서 다시 그린 ko {'동일' if same2 else '달라짐 ✗'}")
        if st["labels_raw"]:
            print(f"   id 없는 라벨: {sorted(set(st['labels_raw']))}")
        for x in st["raw_examples"]:
            print(f"   원문 유지 예: {x}")
        if not (same and same2):
            bad += 1
            continue
        if a.apply:
            # weekly_history._write_history 와 같은 형식(indent 2 · 끝 개행 없음) — 자동 커밋 diff 를 키우지 않게
            p.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   → {fn} 갱신")
    if not a.apply:
        print("(드라이런 — 파일을 쓰지 않았다. --apply 로 갱신)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
