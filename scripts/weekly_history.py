"""
weekly_history.py — 주간 변동 자동 감지 → History entry 생성

동작:
  1. 오늘 스냅샷과 7일 전 스냅샷을 비교
  2. 지구(earth) 지역 기준으로 진입/이탈/순위변동/시총변동 감지
  3. history-top20.json 의 entries 맨 앞에 새 entry 추가
  4. 잠재지배자(latent): 편입·제외는 data/latent_state.json 의 **확정 전이**만 사건으로
     기록한다(2026-09-25). 스냅샷 두 장의 차이는 그날의 요동일 수 있기 때문이다.
     순위·모멘텀·시총 변동은 스냅샷 비교, 문턱에 걸린 종목은 '경계 관찰' 한 줄.

실행: 토요일에만 (워크플로우에서 요일 체크)

자동 생성 = 사실(fact)만. 해설(narrative)은 비워둠 → 추후 수동 보완 가능.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
TODAY_KST = datetime.now(KST)

HERE = Path(__file__).parent.parent
SNAP_DIR = HERE / "data" / "snapshots"
LATEST_PATH = HERE / "data" / "latest.json"
HIST_TOP20_PATH = HERE / "data" / "history-top20.json"
HIST_LATENT_PATH = HERE / "data" / "history-latent.json"
LATENT_STATE_PATH = HERE / "data" / "latent_state.json"

REGION_LABELS = {
    "earth": "지구", "us": "미국", "korea": "한국", "japan": "일본",
    "europe": "유럽", "china": "중국", "hk": "홍콩",
}

# 시총 포맷 ($1.09T / $842B)
def fmt_mc(mc):
    if mc >= 1000:
        return f"${mc/1000:.2f}T"
    return f"${mc:.0f}B"

def fmt_pct(old, new):
    if not old:
        return ""
    pct = (new - old) / old * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def load_snapshot(date_str):
    p = SNAP_DIR / f"{date_str}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_prev_snapshot(target_date, max_back=10):
    """target_date(7일 전)에 가장 가까운 스냅샷을 찾음 (정확히 그 날짜가 없을 수 있으니 ±며칠 탐색)."""
    base = datetime.strptime(target_date, "%Y-%m-%d")
    # 정확한 날짜 우선, 없으면 하루씩 더 과거로
    for delta in range(0, max_back + 1):
        for sign in ([0] if delta == 0 else [-1, 1]):
            cand = (base + timedelta(days=delta * sign)).strftime("%Y-%m-%d")
            snap = load_snapshot(cand)
            if snap:
                return snap, cand
    return None, None


def diff_region(prev_stocks, curr_stocks):
    """
    한 지역의 이전/현재 스톡 리스트를 비교.
    반환: {entered, exited, rank_up, rank_down, mc_moves}
    """
    prev_by_ticker = {s["ticker"]: s for s in prev_stocks if s.get("ticker")}
    curr_by_ticker = {s["ticker"]: s for s in curr_stocks if s.get("ticker")}
    
    prev_tickers = set(prev_by_ticker)
    curr_tickers = set(curr_by_ticker)
    
    entered = []  # 신규 진입
    for t in curr_tickers - prev_tickers:
        s = curr_by_ticker[t]
        entered.append(s)
    entered.sort(key=lambda s: s["rank"])
    
    exited = []  # 이탈
    for t in prev_tickers - curr_tickers:
        s = prev_by_ticker[t]
        exited.append(s)
    exited.sort(key=lambda s: s["rank"])
    
    rank_up = []    # 순위 상승 (숫자 작아짐)
    rank_down = []  # 순위 하락
    big_mc = []     # 큰 시총 변동 (±15% 이상)
    for t in curr_tickers & prev_tickers:
        ps, cs = prev_by_ticker[t], curr_by_ticker[t]
        rank_change = ps["rank"] - cs["rank"]  # +면 상승
        if rank_change >= 3:
            rank_up.append((cs, ps["rank"], cs["rank"]))
        elif rank_change <= -3:
            rank_down.append((cs, ps["rank"], cs["rank"]))
        # 시총 변동
        if ps.get("mc") and cs.get("mc"):
            pct = (cs["mc"] - ps["mc"]) / ps["mc"] * 100
            if abs(pct) >= 15:
                big_mc.append((cs, ps["mc"], cs["mc"], pct))
    
    rank_up.sort(key=lambda x: x[0]["rank"])
    rank_down.sort(key=lambda x: x[0]["rank"])
    big_mc.sort(key=lambda x: -abs(x[3]))
    
    return {
        "entered": entered, "exited": exited,
        "rank_up": rank_up, "rank_down": rank_down, "big_mc": big_mc,
    }


def _t(lang, key, **p):
    import i18n
    return i18n.t(lang, key, **p)


def _mc(lang, mc):
    import i18n
    return fmt_mc(mc) if lang == "ko" else i18n.mc(lang, mc)


def _nm(lang, ev):
    import i18n
    return i18n.stock_name(lang, ev.get("tk"), ev.get("name") or ev.get("tk") or "")


# ── 사건(event) → 문장 — 문장은 사건(상태)에서 언어별로 만든다(CLAUDE.md 규칙 8).
#    ko 문장도 같은 렌더러(ko 카탈로그 hist.ev.*)로 만든다 — 규칙이 두 벌이 되지 않게.
#    다국어 빌드(build_site.history_view)가 render_entry 로 같은 사건을 그 언어로 다시 그린다.
def render_event(ev, lang="ko"):
    k = ev.get("k")
    nm = _nm(lang, ev)
    if k in ("enter", "exit"):
        pre = "" if ev.get("region") in (None, "earth") else \
            "[" + _t(lang, "region." + ev["region"]) + "] "
        if k == "enter":
            return pre + _t(lang, "hist.ev.enter", name=nm, rank=ev.get("rank"), mc=_mc(lang, ev.get("mc") or 0))
        return pre + _t(lang, "hist.ev.exit", name=nm)
    if k == "rank_up":
        return _t(lang, "hist.ev.rank_up", name=nm, a=ev["a"], b=ev["b"], mc=_mc(lang, ev.get("mc") or 0))
    if k == "rank_down":
        return _t(lang, "hist.ev.rank_down", name=nm, a=ev["a"], b=ev["b"])
    if k == "mc_move":
        return _t(lang, "hist.ev.mc_move", name=nm, cls="em-up" if ev["pct"] >= 0 else "em-down",
                  pct=f"{'+' if ev['pct'] >= 0 else ''}{ev['pct']:.0f}%",
                  a=_mc(lang, ev["old"]), b=_mc(lang, ev["new"]))
    if k == "l_enter":
        mom = "" if ev.get("mom") is None else _t(lang, "hist.ev.mom_tail", m=ev["mom"])
        return _t(lang, "hist.ev.l_enter", name=nm, rank=ev.get("rank", "?"), mc=_mc(lang, ev.get("mc") or 0), mom=mom)
    if k == "grad":
        return _t(lang, "hist.ev.grad", name=nm, rank=ev.get("rank"))
    if k == "l_exit":
        return _t(lang, "hist.ev.l_exit", name=nm)
    if k == "l_rank":
        return _t(lang, "hist.ev.l_rank_up" if ev["b"] < ev["a"] else "hist.ev.l_rank_down", name=nm, a=ev["a"], b=ev["b"])
    if k == "l_mom":
        return _t(lang, "hist.ev.l_mom", name=nm, cls="em-up" if ev["b"] >= ev["a"] else "em-down", a=ev["a"], b=ev["b"])
    if k == "t_in":
        facts = []
        if ev.get("rank"):
            facts.append(_t(lang, "hist.ev.rank_fact", n=ev["rank"]))
        if ev.get("mc"):
            facts.append(_mc(lang, ev["mc"]))
        if ev.get("mom") is not None:
            facts.append(f"1Y {ev['mom']:+}%")
        fact = _t(lang, "hist.ev.facts", f=", ".join(facts)) if facts else ""
        why = (_t(lang, "hist.ev.why_swap", who=ev["replacing"], n=ev.get("swap", "?")) if ev.get("replacing")
               else _t(lang, "hist.ev.why_in", n=ev.get("streak", "?")))
        return _t(lang, "hist.ev.t_in", name=nm, fact=fact, why=why, when=ev["when"])
    if k == "t_out":
        if ev.get("by"):
            why = _t(lang, "hist.ev.why_replaced", who=ev["by"])
        elif ev.get("carried"):
            why = _t(lang, "hist.ev.why_carried", n=ev["carried"])
        elif ev.get("out"):
            why = _t(lang, "hist.ev.why_out", n=ev["out"])
        else:
            why = None
        tail = _t(lang, "hist.ev.tail_why", why=why, when=ev["when"]) if why else _t(lang, "hist.ev.tail", when=ev["when"])
        return _t(lang, "hist.ev.t_out", name=nm, tail=tail)
    if k == "watch":
        return " · ".join(_t(lang, "hist.watch." + p["w"], name=_nm(lang, p), a=p.get("a"), b=p.get("b"))
                          for p in ev.get("parts") or [])
    if k == "sector":
        th = _t(lang, "latent.theme." + ev["tid"]) if ev.get("tid") and lang != "ko" else ev.get("theme", "")
        names = ", ".join(_nm(lang, x) for x in ev.get("names") or [])
        return _t(lang, "hist.ev.sector", theme=th, n=len(ev.get("names") or []), names=names)
    if k == "pt_hot":
        return _t(lang, "hist.ev.pt_hot", name=nm, m=ev["m"])
    if k == "pt_new":
        return _t(lang, "hist.ev.pt_new", name=nm, rank=ev.get("rank", "?"))
    if k == "pt_grad":
        return _t(lang, "hist.ev.pt_grad", names=", ".join(_nm(lang, x) for x in ev.get("names") or []))
    return ""


def _block(label_id, events, lang="ko", **lp):
    return {"type": "items", "label_id": label_id, "label_p": lp, "label": _t(lang, "hist.lbl." + label_id, **lp),
            "events": events, "items": [render_event(e, lang) for e in events]}


def render_entry(entry, lang):
    """저장된 기록(사건 구조가 있는 것) → 그 언어로 다시 그린 사본. 사건이 없는 블록은 그대로."""
    e = dict(entry)
    if e.get("prev") and e.get("curr"):
        e["period"] = _t(lang, "hist.period", a=_short_date(e["prev"]), b=_short_date(e["curr"]))
    blocks = []
    for b in e.get("blocks") or []:
        b = dict(b)
        if b.get("type") == "items" and b.get("events") is not None:
            b["items"] = [render_event(x, lang) for x in b["events"]]
            if b.get("label_id"):
                b["label"] = _t(lang, "hist.lbl." + b["label_id"], **(b.get("label_p") or {}))
        elif b.get("type") == "stats" and b.get("fx"):
            fx = b["fx"]
            b["label"] = _t(lang, "hist.lbl.fx")
            b["items"] = [{"k": "USD/KRW", "v": _t(lang, "hist.fx.rate", r=fx["rate"], d=fx["date"])},
                          {"k": _t(lang, "hist.fx.formula_k"), "v": _t(lang, "hist.fx.formula", m=fx["mult"])}]
        blocks.append(b)
    e["blocks"] = blocks
    return e


def build_items(curr_snap, prev_snap):
    """전 지역을 훑어 변동 사건 리스트 생성(문장은 render_event 가 만든다)."""
    events = []

    for region_key, label in REGION_LABELS.items():
        prev_stocks = prev_snap.get("regions", {}).get(region_key, [])
        curr_stocks = curr_snap.get("regions", {}).get(region_key, [])
        if not prev_stocks or not curr_stocks:
            continue

        d = diff_region(prev_stocks, curr_stocks)

        # 지구 지역은 상세히, 나머지는 진입/이탈 위주로 간결하게
        is_earth = (region_key == "earth")

        for s in d["entered"]:
            events.append({"k": "enter", "region": region_key, "name": s["name"], "tk": s.get("ticker"),
                           "rank": s["rank"], "mc": s["mc"]})
        for s in d["exited"]:
            events.append({"k": "exit", "region": region_key, "name": s["name"], "tk": s.get("ticker")})

        if is_earth:
            for cs, old_r, new_r in d["rank_up"]:
                events.append({"k": "rank_up", "name": cs["name"], "tk": cs.get("ticker"),
                               "a": old_r, "b": new_r, "mc": cs["mc"]})
            for cs, old_r, new_r in d["rank_down"]:
                events.append({"k": "rank_down", "name": cs["name"], "tk": cs.get("ticker"), "a": old_r, "b": new_r})
            for cs, old_mc, new_mc, pct in d["big_mc"][:5]:
                events.append({"k": "mc_move", "name": cs["name"], "tk": cs.get("ticker"),
                               "pct": (new_mc - old_mc) / old_mc * 100, "old": old_mc, "new": new_mc})

    return events



# ───────────────────────── 잠재지배자(latent) 자동 비교 ─────────────────────────

def _earth_tickers(snap):
    return {s.get("ticker"): s.get("rank")
            for s in snap.get("regions", {}).get("earth", []) if s.get("ticker")}


def diff_latent(prev_latent, curr_latent):
    """잠재지배자 목록 이전/현재 비교 (ticker 기준)."""
    prev_by = {s["ticker"]: s for s in prev_latent if s.get("ticker")}
    curr_by = {s["ticker"]: s for s in curr_latent if s.get("ticker")}
    prev_t, curr_t = set(prev_by), set(curr_by)

    entered = [curr_by[x] for x in curr_t - prev_t]
    entered.sort(key=lambda s: s.get("rank") or 999)
    exited = [prev_by[x] for x in prev_t - curr_t]
    exited.sort(key=lambda s: s.get("rank") or 999)

    rank_moves, mom_moves, mc_moves = [], [], []
    for x in curr_t & prev_t:
        ps, cs = prev_by[x], curr_by[x]
        pr, cr = ps.get("rank"), cs.get("rank")
        if pr and cr and abs(pr - cr) >= 3:
            rank_moves.append((cs, pr, cr))
        pm, cm = ps.get("momentum_1y"), cs.get("momentum_1y")
        if pm is not None and cm is not None and abs(cm - pm) >= 15:
            mom_moves.append((cs, pm, cm))
        pmc, cmc = ps.get("mc"), cs.get("mc")
        if pmc and cmc:
            pct = (cmc - pmc) / pmc * 100
            if abs(pct) >= 15:
                mc_moves.append((cs, pmc, cmc, pct))

    return {"entered": entered, "exited": exited, "rank_moves": rank_moves,
            "mom_moves": mom_moves, "mc_moves": mc_moves}


def build_latent_items(d, curr_snap):
    """잠재지배자 변동 사건 생성. 이탈 종목이 TOP 20에 있으면 '졸업'으로 구분."""
    events = []
    earth = _earth_tickers(curr_snap)

    for s in d["entered"]:
        events.append({"k": "l_enter", "name": s["name"], "tk": s.get("ticker"), "rank": s.get("rank", "?"),
                       "mc": s.get("mc", 0), "mom": s.get("momentum_1y")})

    for s in d["exited"]:
        tk = s.get("ticker")
        if tk in earth:
            s["_grad"] = True
            events.append({"k": "grad", "name": s["name"], "tk": tk, "rank": earth[tk]})
        else:
            events.append({"k": "l_exit", "name": s["name"], "tk": tk})

    for cs, pr, cr in sorted(d["rank_moves"], key=lambda x: x[0].get("rank") or 999):
        events.append({"k": "l_rank", "name": cs["name"], "tk": cs.get("ticker"), "a": pr, "b": cr})

    for cs, pm, cm in d["mom_moves"]:
        events.append({"k": "l_mom", "name": cs["name"], "tk": cs.get("ticker"), "a": pm, "b": cm})

    for cs, pmc, cmc, pct in sorted(d["mc_moves"], key=lambda x: -abs(x[3]))[:5]:
        events.append({"k": "mc_move", "name": cs["name"], "tk": cs.get("ticker"),
                       "pct": (cmc - pmc) / pmc * 100, "old": pmc, "new": cmc})

    return events


def load_latent_state():
    """명단 관성 상태(generate_candidates 가 씀). 없거나 깨졌으면 None — 조용히 넘기지 않는다."""
    if not LATENT_STATE_PATH.exists():
        print("[정보] 잠재지배자: latent_state.json 없음 — 편입·제외 사건은 기록하지 않음"
              "(스냅샷 두 장 비교는 폐지)")
        return None
    try:
        st = json.loads(LATENT_STATE_PATH.read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else None
    except Exception as e:
        print(f"[warn] latent_state.json 읽기 실패({e}) — 편입·제외 사건 기록 생략")
        return None


def confirmed_transitions(state, prev_date, curr_date):
    """이번 주 구간(prev_date, curr_date]에 확정된 전이만. 스냅샷 차이는 사건이 아니다."""
    return [t for t in (state or {}).get("transitions", [])
            if prev_date < (t.get("date") or "") <= curr_date]


def _short_date(dstr):
    dt = datetime.strptime(dstr, "%Y-%m-%d")
    return f"{dt.month}.{dt.day}"


def build_transition_items(trans, curr_snap):
    """확정 전이 → 이력 사건. 사유는 전이에 실린 숫자에서 만든다(내부 문자열을 옮기지 않는다)."""
    events, entered, grads = [], [], []
    earth = _earth_tickers(curr_snap)
    for t in trans:
        name, when = t.get("name") or t.get("ticker", ""), _short_date(t["date"])
        if t.get("dir") == "in":
            entered.append(t)
            events.append({"k": "t_in", "name": name, "tk": t.get("ticker"), "rank": t.get("rank"),
                           "mc": t.get("mc"), "mom": t.get("momentum_1y"), "when": when,
                           "replacing": t.get("replacing_name"), "swap": t.get("swap_streak", "?"),
                           "streak": t.get("streak_in", "?")})
            continue
        tk = t.get("ticker")
        if tk in earth:
            grads.append(t)
            events.append({"k": "grad", "name": name, "tk": tk, "rank": earth[tk]})
            continue
        events.append({"k": "t_out", "name": name, "tk": tk, "when": when, "by": t.get("replaced_by_name"),
                       "carried": t.get("carried_days"), "out": t.get("streak_out")})
    return events, entered, grads


def build_watch_line(state):
    """'경계 관찰' 한 줄의 사건 — 제외 카운트가 도는 멤버와 대기 중인 후보. 낱말은 상태 숫자에서 만든다."""
    if not state:
        return None
    lim = state.get("limits") or {}
    members = state.get("members") or {}
    parts = []
    for tk, m in sorted(members.items(), key=lambda kv: -max(kv[1].get("streak_out", 0),
                                                               kv[1].get("carried_days", 0))):
        nm = m.get("name") or tk
        if m.get("carried_days"):
            parts.append({"w": "miss", "name": nm, "tk": tk, "a": m["carried_days"], "b": lim.get("miss", "?")})
        elif m.get("streak_out"):
            parts.append({"w": "out", "name": nm, "tk": tk, "a": m["streak_out"], "b": lim.get("out", "?")})
    full = len(members) >= int(lim.get("cap", 14))
    for tk, c in sorted((state.get("candidates") or {}).items(),
                        key=lambda kv: kv[1].get("rank") or 999):
        nm = c.get("name") or tk
        if c.get("streak_in", 0) < int(lim.get("in", 3)):
            parts.append({"w": "in", "name": nm, "tk": tk, "a": c.get("streak_in", 0), "b": lim.get("in", "?")})
        elif full and c.get("swap_streak"):
            parts.append({"w": "swap", "name": nm, "tk": tk, "a": c["swap_streak"], "b": lim.get("swap_days", "?")})
        elif full:
            parts.append({"w": "wait", "name": nm, "tk": tk, "a": c.get("streak_in")})
    return {"k": "watch", "parts": parts} if parts else None


def build_latent_extra_blocks(curr_latent, d):
    """잠재지배자 부가 블록: 섹터 분포 + 관전 포인트 (모두 데이터 기반 자동)."""
    blocks = []
    # 섹터 분포 (theme 집계)
    themes = {}
    for s in curr_latent:
        th = s.get("theme") or "기타"
        themes.setdefault(th, []).append(s)
    if themes:
        evs = [{"k": "sector", "theme": th, "tid": next((x.get("theme_id") for x in rows if x.get("theme_id")), ""),
                "names": [{"name": x.get("name", ""), "tk": x.get("ticker")} for x in rows]}
               for th, rows in sorted(themes.items(), key=lambda kv: -len(kv[1]))]
        blocks.append(_block("sector", evs, n=len(curr_latent)))
    # 관전 포인트 (숫자 기반)
    pts = []
    moms = [s for s in curr_latent if s.get("momentum_1y") is not None]
    if moms:
        hot = max(moms, key=lambda s: s["momentum_1y"])
        pts.append({"k": "pt_hot", "name": hot["name"], "tk": hot.get("ticker"), "m": hot["momentum_1y"]})
    if d.get("entered"):
        top_new = min(d["entered"], key=lambda s: s.get("rank") or 999)
        pts.append({"k": "pt_new", "name": top_new["name"], "tk": top_new.get("ticker"), "rank": top_new.get("rank", "?")})
    grads = [s for s in d.get("exited", []) if s.get("_grad")]
    if grads:
        pts.append({"k": "pt_grad", "names": [{"name": s["name"], "tk": s.get("ticker")} for s in grads]})
    if pts:
        blocks.append(_block("points", pts))
    return blocks


# ───────────────────────── 공통 헬퍼 ─────────────────────────

def _fx_block():
    """환율·환산 블록 (latest.json meta 기준). 없으면 None. 상태(fx)를 함께 남겨 언어별로 다시 그린다."""
    try:
        m = json.loads(LATEST_PATH.read_text(encoding="utf-8")).get("meta", {})
        fx = m.get("usd_krw")
        if not fx:
            return None
        date_label = m.get("fetched_date", "")[-5:].replace("-", "/")
        rate_disp = f"{fx:,.0f}"
        mult = f"{fx/1000:.2f}"
        st = {"rate": rate_disp, "date": date_label, "mult": mult}
        return {"type": "stats", "label": _t("ko", "hist.lbl.fx"), "fx": st,
                "items": [{"k": "USD/KRW", "v": _t("ko", "hist.fx.rate", r=rate_disp, d=date_label)},
                          {"k": _t("ko", "hist.fx.formula_k"), "v": _t("ko", "hist.fx.formula", m=mult)}]}
    except Exception:
        return None


def _make_entry(events, prev_date, curr_date, label_id):
    def short(dstr):
        dt = datetime.strptime(dstr, "%Y-%m-%d")
        return f"{dt.month}.{dt.day}"
    entry = {
        "date": curr_date.replace("-", "."),
        "period": _t("ko", "hist.period", a=short(prev_date), b=short(curr_date)),
        "prev": prev_date, "curr": curr_date,
        "auto": True,
        "blocks": [_block(label_id, events)],
    }
    return entry


def _write_history(path, default, new_entry):
    if path.exists():
        hist = json.loads(path.read_text(encoding="utf-8"))
    else:
        hist = default
    date_label = new_entry["date"]
    entries = [e for e in hist.get("entries", [])
               if not (e.get("date") == date_label and e.get("auto"))]
    entries.insert(0, new_entry)
    hist["entries"] = entries
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def _preview(tag, items):
    import re
    print(f"[OK] {tag} entry 추가 ({len(items)}개 변동)")
    for it in items:
        print(f"   • {re.sub(r'<[^>]+>', '', it)}")


def main():
    curr_date = TODAY_KST.strftime("%Y-%m-%d")
    curr_snap = load_snapshot(curr_date)
    if not curr_snap:
        # 오늘 스냅샷이 아직이면 latest.json에서 즉석 생성 (latent 포함)
        if LATEST_PATH.exists():
            latest = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
            curr_snap = {
                "date": curr_date,
                "regions": {
                    rk: [
                        {"rank": i+1, "ticker": s.get("ticker",""),
                         "name": s.get("name",""), "mc": s.get("mc",0)}
                        for i, s in enumerate(r.get("stocks", []))
                    ]
                    for rk, r in latest.get("regions", {}).items()
                },
                "latent": latest.get("latent", []),
            }
        else:
            print("[중단] 오늘 스냅샷도 latest.json도 없음")
            sys.exit(0)

    # 7일 전 스냅샷 찾기
    target = (TODAY_KST - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_snap, prev_date = find_prev_snapshot(target)
    if not prev_snap:
        print(f"[중단] 비교할 과거 스냅샷 없음 (7일 전 ≈ {target}). 첫 주는 건너뜀.")
        sys.exit(0)

    print(f"[비교] {prev_date} → {curr_date}")

    # ── 우주지배자 (TOP 20) ──
    events = build_items(curr_snap, prev_snap)
    items = [render_event(e) for e in events]
    if items:
        entry = _make_entry(events, prev_date, curr_date, "weekly")
        fx = _fx_block()
        if fx:
            entry["blocks"].append(fx)
        _write_history(HIST_TOP20_PATH, {
            "page_title": "우주지배자 변동 이력",
            "page_desc": "글로벌 시가총액 TOP 20의 시점별 변동 기록",
            "entries": [],
        }, entry)
        _preview("우주지배자 History", items)
    else:
        print("[정보] 우주지배자: 이번 주 유의미한 변동 없음")

    # ── 잠재지배자 (latent) ──
    # 편입·제외는 명단 상태의 확정 전이만 적는다. 9/19 이력은 스냅샷 두 장을 비교해
    # "ARM 편입·KIOXIA 제외"라 적었지만 9/21 부터 둘 다 명단에 있었다(경계 요동).
    prev_latent = prev_snap.get("latent", [])
    curr_latent = curr_snap.get("latent", [])
    lstate = load_latent_state()
    trans = confirmed_transitions(lstate, prev_date, curr_date)
    litems, entered, grads = build_transition_items(trans, curr_snap)
    if prev_latent and curr_latent:
        dl = diff_latent(prev_latent, curr_latent)
        dl["entered"], dl["exited"] = [], []      # 명단 출입은 위의 확정 전이만
        litems += build_latent_items(dl, curr_snap)
    else:
        print("[정보] 잠재지배자: 비교할 스냅샷 latent 없음 — 순위·모멘텀 변동 생략")
    watch_ev = build_watch_line(lstate)
    watch = render_event(watch_ev) if watch_ev else None
    if litems or watch:
        lentry = _make_entry(litems, prev_date, curr_date, "latent_weekly")
        if not litems:
            lentry["blocks"] = []
        if watch_ev:
            lentry["blocks"].append(_block("watch", [watch_ev]))
        if curr_latent:
            lentry["blocks"].extend(build_latent_extra_blocks(
                curr_latent, {"entered": entered,
                              "exited": [dict(g, _grad=True) for g in grads]}))
        fx = _fx_block()
        if fx:
            lentry["blocks"].append(fx)
        _write_history(HIST_LATENT_PATH, {
            "page_title": "잠재지배자 변동 이력",
            "page_desc": "차세대 우주지배자 후보의 시점별 변동 기록",
            "entries": [],
        }, lentry)
        _preview("잠재지배자 History", [render_event(e) for e in litems] + ([watch] if watch else []))
    else:
        print("[정보] 잠재지배자: 이번 주 변동 없음")

if __name__ == "__main__":
    main()
