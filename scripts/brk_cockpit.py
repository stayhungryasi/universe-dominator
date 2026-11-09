#!/usr/bin/env python3
"""
버크셔 탐사선 관제판 (brk_cockpit) — 판단층을 읽어 공개용 정적 HTML 로 · 2026-11-09
================================================================================
`agent-research/berkshire/` 는 탐사 에이전트 본인만 쓰는 판단층이다. 여기서는 **읽기만** 하고
빌드 시점에 정적 HTML 을 만든다(클라이언트 fetch 금지 — 무인 탐사선과 같은 이유: 공개 페이지가
판단층 파일을 직접 긁을 수 있으면 준법 필터를 우회할 길이 생긴다).

규율(무인 탐사선에서 물려받은 것):
  · 준법 — 보고서는 agent_report.sanitize(PHASE 4 절·권고 블록 제외)를 거친다. state 의 권고·포지션 계열
    키는 애초에 관문이 막는다(probe_gate.check_compliance).
  · 화면 문구는 카탈로그(bk.*)에서 그 언어로 만든다 — 판단층의 한국어 상태값은 기계 id 로 바꾼 뒤 카탈로그로.
    축 기계 키(A_insurance 등)는 화면에 내보내지 않는다(작업 규칙 8).
  · 콘텐츠(탐사 원장 문장)는 번역 전 원문 — 다른 언어 화면에서는 원문 표식(lang=ko + KO)을 단다.
  · 시계는 today 인자 하나(테스트가 고정한다 — 지뢰 ㉑).
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent.parent
AR = HERE / "agent-research" / "berkshire"
DATA_DIR = HERE / "data"
KST = timezone(timedelta(hours=9))
MARK = "univertrix.com"

STATUS_ID = {"달성": "done", "달성(조기)": "early", "진행": "prog", "미달": "miss", "미판정": "wait"}
AXIS_LETTERS = ("A", "B", "C", "D", "E")
KPI_TILES = [("op_earnings_usd_m", "op", "money"), ("buyback_usd_m", "buyback", "money"),
             ("cash_tbill_usd_m", "cash", "money"), ("bvps_a_usd", "bvps", "usd"),
             ("float_usd_m", "float", "money"), ("equity_net_usd_m", "eqnet", "money")]
GRADE_ID = ("urgent", "major", "record")


def esc(t):
    return (str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def L(lang, key, **p):
    import i18n
    return i18n.t(lang, key, **p)


def C(lang, s):
    """콘텐츠 원문 조각 — 다른 언어 화면에서는 원문 표식."""
    import i18n
    return i18n.pending_inline(lang, esc(s))


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def read_state(root=None):
    root = root or AR
    return read_json(root / "state" / "berkshire_state.json", {}) or {}


def report_files(root=None):
    root = root or AR
    try:
        return sorted((p for p in (root / "reports").glob("*.md")
                       if re.fullmatch(r"\d{4}-\d{2}(?:-r\d+|-\d{2}-event(?:-r\d+)?)?", p.stem)),
                      key=lambda p: p.stem, reverse=True)
    except Exception:
        return []


def kst_today():
    return datetime.now(KST).date()


# ────────────────────────────────────────────────────────────────
# 순수 함수 — 테스트가 직접 두드린다
# ────────────────────────────────────────────────────────────────

def axis_letter(key):
    k = (key or "?")[:1].upper()
    return k if k in AXIS_LETTERS else "E"


def axis_name(lang, axes, key):
    """축 화면 이름 — ko 는 원장 이름('축 A — 보험·플로트' → '보험·플로트'), 다른 언어는 카탈로그."""
    if lang == "ko":
        v = (axes or {}).get(key) if isinstance(axes, dict) else None
        if isinstance(v, str) and v.strip():
            v = re.sub(r"^축\s*[A-E]\s*[—\-:]\s*", "", v.strip())
            return re.split(r"[(（]", v)[0].strip() or v
    return L(lang, "bk.axis." + axis_letter(key))


def ms_pct(m):
    import agent_report
    return agent_report.milestone_pct(m)


def axis_rows(st, lang="ko"):
    """축별 진척 — 측정된 이정표 평균 · 측정 수/전체 · 판정 분포."""
    axes = st.get("axes") or {}
    keys = list(axes.keys()) if isinstance(axes, dict) else []
    for m in st.get("milestones") or []:
        if isinstance(m, dict) and m.get("axis") and m["axis"] not in keys:
            keys.append(m["axis"])
    out = []
    for k in keys:
        ms = [m for m in st.get("milestones") or [] if isinstance(m, dict) and m.get("axis") == k]
        if not ms:
            continue
        ps = [p for p in (ms_pct(m) for m in ms) if p is not None]
        cnt = {}
        for m in ms:
            sid = STATUS_ID.get(m.get("status"), "wait")
            cnt[sid] = cnt.get(sid, 0) + 1
        out.append({"key": k, "name": axis_name(lang, axes, k), "n": len(ms), "measured": len(ps),
                    "pct": (sum(ps) / len(ps)) if ps else None, "counts": cnt})
    return out


def kpi_series(st):
    """분기 KPI — period_end 순. 같은 분기는 마지막 줄."""
    rows = {}
    for h in st.get("kpi_history") or []:
        if isinstance(h, dict) and h.get("quarter"):
            rows[h["quarter"]] = h
    return sorted(rows.values(), key=lambda h: str(h.get("period_end") or ""))


def tile(series, key):
    """KPI 타일 — 최신값 · 바로 앞 분기 대비 방향(값이 있는 직전 점이 아니라 **바로 앞 분기**) · 스파크라인."""
    pts = [(h.get("quarter"), h.get(key)) for h in series]
    vals = [(q, v) for q, v in pts if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    q, v = vals[-1]
    idx = [p[0] for p in pts].index(q)
    prev = pts[idx - 1][1] if idx > 0 else None
    arrow = None
    if isinstance(prev, (int, float)) and not isinstance(prev, bool):
        arrow = "up" if v > prev else ("down" if v < prev else "flat")
    yoy = None
    if idx >= 4:
        y = pts[idx - 4][1]
        if isinstance(y, (int, float)) and y > 0 and v > 0:
            yoy = v / y - 1
    src = next((h.get("source") for h in series if h.get("quarter") == q), "")
    origin = next((h.get("origin") for h in series if h.get("quarter") == q), "") or "disc"
    return {"quarter": q, "value": v, "arrow": arrow, "yoy": yoy, "spark": [x for _, x in vals][-6:],
            "source": src, "origin": origin}


def spark_svg(vals, w=64, h=18):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(vals))
    return (f'<svg class="bk-spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def fmt(lang, v, kind):
    import i18n
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    if kind == "money":
        return i18n.usd_m(lang, v)
    if kind == "usd":
        pre = "US$" if lang == "es" else "$"
        return pre + i18n.num(lang, v, 0)
    return i18n.num(lang, v)


def usd(lang, v, digits=0):
    import i18n
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    pre = "US$" if lang == "es" else "$"
    return pre + i18n.num(lang, v, digits)


def next_triggers(st, today, n=3):
    """다음에 볼 것 — ISO 예상일이 있는 '대기' 트리거를 가까운 순으로. 날짜 없는 것은 뒤에."""
    out = []
    for t in st.get("triggers_next_12m") or []:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "")
        if status and not status.startswith("대기"):
            continue
        ds = re.findall(r"20\d{2}-\d{2}-\d{2}", str(t.get("expected_date") or ""))
        d = None
        if ds:
            try:
                d = date.fromisoformat(ds[0])
            except ValueError:
                d = None
        out.append({"id": t.get("id"), "name": t.get("name"), "date": d, "expected": t.get("expected_date"),
                    "check": t.get("check"), "dd": (d - today).days if d else None})
    out.sort(key=lambda x: (x["date"] is None, x["date"] or date.max))
    return out[:n]


def next_run_date(run_date):
    """다음 정기 회차 — 매월 2일."""
    try:
        d = date.fromisoformat(str(run_date)[:10])
    except Exception:
        return None
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, 2)


def event_title(lang, ev):
    kind = ev.get("kind") or "other"
    return L(lang, "bk.ev." + kind) + (f" ({ev.get('form')})" if ev.get("form") else "")


# ────────────────────────────────────────────────────────────────
# 렌더
# ────────────────────────────────────────────────────────────────

def _chip(lang, status):
    sid = STATUS_ID.get(status, "wait")
    cls = {"done": "done", "early": "done", "prog": "prog", "miss": "miss"}.get(sid, "")
    return f'<span class="ag-chip{" ag-chip-" + cls if cls else ""}">{esc(L(lang, "pc.st." + sid))}</span>'


def _bar(p):
    if p is None:
        return ""
    return f'<span class="ag-bar"><i style="width:{max(0, min(100, round(p * 100)))}%"></i></span>'


def render_value(st, lang):
    v = st.get("valuation") or {}
    mk = st.get("market") or {}
    sc = st.get("scenarios") or {}
    band = v.get("band") or []
    price = mk.get("price_usd")
    lo, hi = (band + [None, None])[:2]
    mid, pw = v.get("mid"), v.get("pw_value")
    if not all(isinstance(x, (int, float)) for x in (lo, hi, price)):
        return ""
    vals = [x for x in (lo, hi, price, mid, pw) if isinstance(x, (int, float))]
    vmin, vmax = min(vals) * 0.85, max(vals) * 1.1

    def pos(x):
        return max(0.0, min(100.0, (x - vmin) / (vmax - vmin) * 100))
    h = ['<section class="bk-card bk-value"><h4 class="bk-h">' + esc(L(lang, "bk.h.value")) + "</h4>",
         '<div class="bk-band" role="img" aria-label="' + esc(L(lang, "bk.value.band", lo=usd(lang, lo), hi=usd(lang, hi),
                                                                 mid=usd(lang, mid), p=usd(lang, price, 2),
                                                                 d=mk.get("price_asof", ""))) + '">',
         f'<div class="bk-band-rng" style="left:{pos(lo):.1f}%;width:{pos(hi) - pos(lo):.1f}%"></div>']
    if isinstance(mid, (int, float)):
        h.append(f'<div class="bk-band-mid" style="left:{pos(mid):.1f}%"><span>{esc(usd(lang, mid))}</span></div>')
    h.append(f'<div class="bk-band-lo" style="left:{pos(lo):.1f}%">{esc(usd(lang, lo))}</div>')
    h.append(f'<div class="bk-band-hi" style="left:{pos(hi):.1f}%">{esc(usd(lang, hi))}</div>')
    h.append(f'<div class="bk-band-px" style="left:{pos(price):.1f}%"><i></i><span>{esc(L(lang, "bk.value.price"))} '
             f'{esc(usd(lang, price, 2))}</span></div>')
    h.append("</div>")
    h.append('<p class="bk-sub">' + esc(L(lang, "bk.value.band", lo=usd(lang, lo), hi=usd(lang, hi), mid=usd(lang, mid),
                                           p=usd(lang, price, 2), d=mk.get("price_asof", ""))))
    if isinstance(pw, (int, float)):
        h.append(" · " + esc(L(lang, "bk.value.pw", v=usd(lang, pw))))
    h.append("</p><div class='bk-probs'>")
    for k in ("bull", "base", "bear"):
        p = (sc.get(k) or {}).get("probability")
        if isinstance(p, (int, float)):
            h.append(f'<span class="bk-prob bk-prob-{k}" style="flex:{max(p, 0.04):.2f}">'
                     f'{esc(L(lang, "bk.sc." + k))} {round(p * 100)}</span>')
    h.append("</div>")
    if v.get("method"):
        h.append('<p class="bk-fine">' + C(lang, v.get("method")) + "</p>")
    h.append("</section>")
    return "".join(h)


def render_delta(st, lang, changelog_md):
    run = st.get("run") or {}
    h = ['<section class="bk-card"><h4 class="bk-h">' + esc(L(lang, "bk.h.delta")) + "</h4>"]
    summ = run.get("summary") or []
    if isinstance(summ, str):
        summ = [summ]
    h.append('<div class="bk-delta"><b>' + esc(run.get("run_id", "")) + "</b> · "
             + esc(L(lang, "bk.mode." + ("event" if run.get("mode") == "event" else "monthly"))) + "<ul>")
    for s in summ[:4]:
        h.append("<li>" + C(lang, s) + "</li>")
    h.append("</ul></div>")
    prev = [r for r in st.get("run_history") or [] if isinstance(r, dict) and r.get("run_id") != run.get("run_id")]
    if prev:
        r = prev[-1]
        h.append('<div class="bk-delta bk-delta-prev"><b>' + esc(r.get("run_id")) + "</b><ul>")
        for s in (r.get("summary") or [])[:2]:
            h.append("<li>" + C(lang, s) + "</li>")
        h.append("</ul></div>")
    h.append("</section>")
    return "".join(h)


def render_axes(st, lang):
    rows = axis_rows(st, lang)
    if not rows:
        return ""
    h = ['<section class="bk-card"><h4 class="bk-h">' + esc(L(lang, "bk.h.axes"))
         + '<small>' + esc(L(lang, "bk.axes.meas")) + "</small></h4>"]
    for r in rows:
        pct = r["pct"]
        cnt = " · ".join(f'{L(lang, "pc.st." + k)} {n}' for k, n in sorted(r["counts"].items()))
        pct_s = f"{round(pct * 100)}%" if pct is not None else "—"
        h.append('<div class="bk-axis"><span class="bk-axis-n">' + esc(r["name"]) + "</span>"
                 f'<span class="bk-axis-b"><i style="width:{round((pct or 0) * 100)}%"></i></span>'
                 '<span class="bk-axis-t">' + esc(L(lang, "bk.axes.row", pct=pct_s, m=r["measured"], n=r["n"]))
                 + " · " + esc(cnt) + "</span></div>")
    h.append("</section>")
    return "".join(h)


def render_kpis(st, lang):
    series = kpi_series(st)
    tiles = []
    for key, tid, kind in KPI_TILES:
        t = tile(series, key)
        if not t:
            continue
        arrow = {"up": "↗", "down": "↘", "flat": "→"}.get(t["arrow"] or "", "")
        sub = []
        if t["yoy"] is not None:
            import i18n
            sign = "+" if t["yoy"] >= 0 else "−"
            sub.append(L(lang, "bk.kpi.yoy", v=sign + i18n.pct(lang, abs(t["yoy"]))))
        tag = L(lang, "bk.kpi.o." + ("calc" if t["origin"] == "calc" else "disc"))
        link = f' href="{esc(t["source"])}" target="_blank" rel="noopener"' if str(t["source"]).startswith("http") else ""
        tiles.append(f'<a class="bk-tile"{link}><span class="bk-tile-k">{esc(L(lang, "bk.kpi." + tid))} {esc(t["quarter"])}'
                     f'<em>{esc(tag)}</em></span><span class="bk-tile-v">{esc(fmt(lang, t["value"], kind))}'
                     f'<b class="bk-ar bk-ar-{t["arrow"] or "na"}">{arrow}</b></span>'
                     f'<span class="bk-tile-s">{esc(" · ".join(sub))}{spark_svg(t["spark"])}</span></a>')
    if not tiles:
        return ""
    return ('<section class="bk-card"><h4 class="bk-h">' + esc(L(lang, "bk.h.kpi")) + "<small>"
            + esc(L(lang, "bk.kpi.sub")) + '</small></h4><div class="bk-tiles">' + "".join(tiles) + "</div></section>")


def render_next(st, lang, today):
    nx = next_triggers(st, today)
    if not nx:
        return ""
    h = ['<section class="bk-card bk-wide"><h4 class="bk-h">' + esc(L(lang, "bk.h.next")) + "</h4><div class='bk-next'>"]
    for t in nx:
        dd = t["dd"]
        badge = (L(lang, "bk.dd.future", n=dd) if dd is not None and dd > 0 else
                 L(lang, "bk.dd.today") if dd == 0 else
                 L(lang, "bk.dd.past", n=-dd) if dd is not None else L(lang, "bk.dd.tbd"))
        h.append('<div class="bk-nx"><span class="bk-dd">' + esc(badge) + "</span><b>" + esc(t["id"]) + " "
                 + C(lang, t["name"]) + '</b><span class="bk-fine">' + C(lang, t["expected"] or "") + "</span></div>")
    h.append("</div></section>")
    return "".join(h)


def render_milestones(st, lang):
    ms = [m for m in st.get("milestones") or [] if isinstance(m, dict)]
    if not ms:
        return ""
    axes = st.get("axes") or {}
    h = ['<h4 class="ag-h">' + esc(L(lang, "bk.h.ms")) + '</h4><div class="ag-tw"><table class="ag-table"><thead><tr>']
    for c in ("id", "axis", "year", "statement", "status", "progress"):
        h.append("<th>" + esc(L(lang, "bk.col." + c)) + "</th>")
    h.append("</tr></thead><tbody>")
    for m in sorted(ms, key=lambda m: (str(m.get("year")), str(m.get("id")))):
        pg = m.get("progress")
        cur = pg.get("current") if isinstance(pg, dict) else (m.get("progress_reason") or "")
        tgt = pg.get("target") if isinstance(pg, dict) else ""
        p = ms_pct(m)
        prog = _bar(p) + C(lang, f"{cur} / {tgt}" if tgt else cur)
        h.append(f"<tr><td>{esc(m.get('id'))}</td><td>{esc(axis_name(lang, axes, m.get('axis')))}</td>"
                 f"<td>{esc(m.get('year'))}</td><td>{C(lang, m.get('statement'))}</td>"
                 f"<td>{_chip(lang, m.get('status'))}</td><td class='ag-np'>{prog}</td></tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def render_disruption(st, lang):
    rows = [d for d in st.get("disruption_watch") or [] if isinstance(d, dict)]
    if not rows:
        return ""
    h = ['<h4 class="ag-h">' + esc(L(lang, "bk.h.disrupt")) + '</h4><p class="bk-fine">' + esc(L(lang, "bk.disrupt.note"))
         + '</p><div class="ag-tw"><table class="ag-table"><thead><tr>']
    for c in ("dname", "dtarget", "dsignal", "dstate"):
        h.append("<th>" + esc(L(lang, "bk.col." + c)) + "</th>")
    h.append("</tr></thead><tbody>")
    for d in rows:
        h.append(f"<tr><td><b>{esc(d.get('id'))}</b> {C(lang, d.get('name'))}</td><td>{C(lang, d.get('threat_to'))}</td>"
                 f"<td>{C(lang, d.get('signal'))}</td><td>{C(lang, d.get('status'))}</td></tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def render_scenarios(st, lang):
    sc = st.get("scenarios") or {}
    rows = [(k, sc.get(k)) for k in ("bull", "base", "bear") if isinstance(sc.get(k), dict)]
    if not rows:
        return ""
    h = ['<h4 class="ag-h">' + esc(L(lang, "bk.h.sc")) + '</h4><div class="ag-tw"><table class="ag-table"><thead><tr>']
    for c in ("sc", "prob", "value", "story"):
        h.append("<th>" + esc(L(lang, "bk.col." + c)) + "</th>")
    h.append("</tr></thead><tbody>")
    for k, s in rows:
        p = s.get("probability")
        h.append(f"<tr><td><b>{esc(L(lang, 'bk.sc.' + k))}</b></td><td>{round(p * 100) if isinstance(p, (int, float)) else '—'}%</td>"
                 f"<td>{esc(usd(lang, s.get('value_per_b_share')))}</td><td>{C(lang, s.get('description', ''))}</td></tr>")
    h.append("</tbody></table></div>")
    rules = [r for r in st.get("probability_adjustment_rules") or [] if isinstance(r, dict)]
    if rules:
        h.append('<h4 class="ag-h">' + esc(L(lang, "bk.h.rules")) + '</h4><div class="ag-tw"><table class="ag-table"><thead><tr>')
        for c in ("rid", "cond", "adj", "rstate"):
            h.append("<th>" + esc(L(lang, "bk.col." + c)) + "</th>")
        h.append("</tr></thead><tbody>")
        for r in rules:
            adj = " · ".join(f"{L(lang, 'bk.sc.' + k)} {'+' if r[k] >= 0 else '−'}{abs(round(r[k] * 100))}%p"
                             for k in ("bull", "base", "bear") if isinstance(r.get(k), (int, float)))
            h.append(f"<tr><td>{esc(r.get('id'))}</td><td>{C(lang, r.get('condition'))}</td><td>{esc(adj)}</td>"
                     f"<td>{C(lang, r.get('status', ''))}</td></tr>")
        h.append("</tbody></table></div>")
    return "".join(h)


def render_events(watch, lang, n=12):
    evs = [e for e in (watch or {}).get("events") or [] if isinstance(e, dict)][:n]
    if not evs:
        return ""
    h = ['<h4 class="ag-h">' + esc(L(lang, "bk.h.events")) + '</h4><ul class="ag-tl">']
    for e in evs:
        g = e.get("grade") if e.get("grade") in GRADE_ID else "record"
        title = esc(event_title(lang, e))
        if str(e.get("url", "")).startswith("http"):
            title = f'<a href="{esc(e["url"])}" target="_blank" rel="noopener">{title}</a>'
        h.append(f'<li><span class="ag-tl-d">{esc(e.get("date"))}</span><span class="ag-tl-s">'
                 f'{esc(L(lang, "bk.grade." + g))}</span>{title}</li>')
    h.append("</ul>")
    return "".join(h)


def render_reports(lang, root=None, n=3):
    import agent_report
    out, cut = [], {"phase4": 0, "blocks": 0}
    for i, p in enumerate(report_files(root)[:n]):
        body, info = agent_report.sanitize(p.read_text(encoding="utf-8"))
        cut["phase4"] += int(bool(info.get("phase4")))
        cut["blocks"] += info.get("blocks", 0)
        html = agent_report.md_to_html(body, base_level=3)
        attr = ' lang="ko" data-uvx-pending="1"' if lang != "ko" else ""
        out.append(f'<details class="ag-run" id="brk-{esc(p.stem)}"{" open" if i == 0 else ""}><summary>'
                   + esc(L(lang, "bk.report.title", id=p.stem)) + f'</summary><div class="ag-body"{attr}>' + html + "</div></details>")
    if not out:
        return "", cut
    return '<h4 class="ag-h">' + esc(L(lang, "bk.h.report")) + "</h4>" + "".join(out), cut


def status_line(st, lang, today, vres=None):
    run = st.get("run") or {}
    bits = [L(lang, "bk.st.run", id=run.get("run_id", "—"), n=run.get("run_number", "—"))]
    try:
        import i18n
        bits.append(L(lang, "bk.st.obs", d=i18n.date(lang, run.get("run_date"))))
    except Exception:
        pass
    if isinstance(vres, dict) and vres.get("checked"):
        ok = int(vres.get("match") or 0) + int(vres.get("rounding") or 0)
        bits.append(L(lang, "bk.st.verify", a=ok, b=vres.get("checked")))
    nr = next_run_date(run.get("run_date"))
    if nr:
        import i18n
        bits.append(L(lang, "bk.st.next", d=i18n.date(lang, nr.isoformat())))
    return " · ".join(bits)


def render(root=None, today=None, watch=None, lang="ko"):
    """관제판 HTML(빈 문자열이면 '준비 중'). 판단층은 읽기만 한다."""
    root = root or AR
    today = today or kst_today()
    st = read_state(root)
    if not st:
        return ""
    watch = read_json(DATA_DIR / "brk_watch.json", {}) if watch is None else watch
    vres = read_json(root / "state" / "verify_result.json", None)
    try:
        changelog = (root / "reports" / "changelog.md").read_text(encoding="utf-8")
    except Exception:
        changelog = ""
    run = st.get("run") or {}
    h = ['<div class="bk">',
         '<p class="pc-note">' + esc(L(lang, "bk.note")) + "</p>",
         '<div class="pc-status"><span class="pc-dot"></span><span class="pc-status-t">'
         + esc(status_line(st, lang, today, vres)) + "</span></div>"]
    try:
        age = (today - date.fromisoformat(str(run.get("run_date"))[:10])).days
    except Exception:
        age = None
    if age is not None and age > 35:
        h.append('<div class="ag-delay"><b>' + esc(L(lang, "bk.delay", n=age)) + "</b></div>")
    h.append('<div class="bk-grid">')
    h.append(render_value(st, lang))
    h.append(render_delta(st, lang, changelog))
    h.append(render_axes(st, lang))
    h.append(render_kpis(st, lang))
    h.append(render_next(st, lang, today))
    h.append("</div>")
    h.append(render_milestones(st, lang))
    h.append(render_disruption(st, lang))
    h.append(render_scenarios(st, lang))
    h.append(render_events(watch, lang))
    rep, cut = render_reports(lang, root)
    h.append(rep)
    h.append('<p class="ag-link">' + esc(L(lang, "bk.disclaimer")) + " · " + MARK + "</p></div>")
    html = "".join(h)
    print(f"[OK] 버크셔 탐사선({lang}): {len(html) // 1024}KB · PHASE4 제외 {cut['phase4']} · 권고 블록 제외 {cut['blocks']}")
    return html
