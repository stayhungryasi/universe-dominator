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


def build_items(curr_snap, prev_snap):
    """전 지역을 훑어 변동 사항 items 리스트 생성."""
    items = []
    
    for region_key, label in REGION_LABELS.items():
        prev_stocks = prev_snap.get("regions", {}).get(region_key, [])
        curr_stocks = curr_snap.get("regions", {}).get(region_key, [])
        if not prev_stocks or not curr_stocks:
            continue
        
        d = diff_region(prev_stocks, curr_stocks)
        
        # 지구 지역은 상세히, 나머지는 진입/이탈 위주로 간결하게
        is_earth = (region_key == "earth")
        prefix = "" if is_earth else f"[{label}] "
        
        for s in d["entered"]:
            items.append(
                f"{prefix}<strong>{s['name']}</strong> "
                f"TOP 20 신규 진입 (<span class='em-up'>{s['rank']}위, {fmt_mc(s['mc'])}</span>)"
            )
        for s in d["exited"]:
            items.append(f"{prefix}<strong>{s['name']}</strong> TOP 20 이탈")
        
        if is_earth:
            for cs, old_r, new_r in d["rank_up"]:
                items.append(
                    f"<strong>{cs['name']}</strong> "
                    f"<span class='em-up'>{old_r}위 → {new_r}위 상승</span> ({fmt_mc(cs['mc'])})"
                )
            for cs, old_r, new_r in d["rank_down"]:
                items.append(
                    f"<strong>{cs['name']}</strong> "
                    f"<span class='em-down'>{old_r}위 → {new_r}위 하락</span>"
                )
            for cs, old_mc, new_mc, pct in d["big_mc"][:5]:
                cls = "em-up" if pct >= 0 else "em-down"
                items.append(
                    f"<strong>{cs['name']}</strong> "
                    f"시총 <span class='{cls}'>{fmt_pct(old_mc, new_mc)}</span> "
                    f"({fmt_mc(old_mc)} → {fmt_mc(new_mc)})"
                )
    
    return items



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
    """잠재지배자 변동 items 생성. 이탈 종목이 TOP 20에 있으면 '졸업'으로 구분."""
    items = []
    earth = _earth_tickers(curr_snap)

    for s in d["entered"]:
        mom = s.get("momentum_1y")
        mom_str = f", 1Y +{mom}%" if mom is not None else ""
        items.append(
            f"<strong>{s['name']}</strong> 잠재지배자 신규 편입 "
            f"(<span class='em-up'>{s.get('rank','?')}위, {fmt_mc(s.get('mc',0))}{mom_str}</span>)"
        )

    for s in d["exited"]:
        tk = s.get("ticker")
        if tk in earth:
            s["_grad"] = True
            gr = earth[tk]
            items.append(
                f"<strong>{s['name']}</strong> — "
                f"<span class='em-up'>★ TOP 20 졸업 (글로벌 {gr}위)</span>"
            )
        else:
            items.append(f"<strong>{s['name']}</strong> 잠재지배자 목록에서 제외")

    for cs, pr, cr in sorted(d["rank_moves"], key=lambda x: x[0].get("rank") or 999):
        up = cr < pr
        cls = "em-up" if up else "em-down"
        word = "상승" if up else "하락"
        items.append(
            f"<strong>{cs['name']}</strong> 잠재 순위 "
            f"<span class='{cls}'>{pr}위 → {cr}위 {word}</span>"
        )

    for cs, pm, cm in d["mom_moves"]:
        cls = "em-up" if cm >= pm else "em-down"
        items.append(
            f"<strong>{cs['name']}</strong> 1Y 모멘텀 "
            f"<span class='{cls}'>{pm}% → {cm}%</span>"
        )

    for cs, pmc, cmc, pct in sorted(d["mc_moves"], key=lambda x: -abs(x[3]))[:5]:
        cls = "em-up" if pct >= 0 else "em-down"
        items.append(
            f"<strong>{cs['name']}</strong> 시총 "
            f"<span class='{cls}'>{fmt_pct(pmc, cmc)}</span> "
            f"({fmt_mc(pmc)} → {fmt_mc(cmc)})"
        )

    return items


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
    """확정 전이 → 이력 문구. 사유는 전이에 실린 숫자에서 만든다(내부 문자열을 옮기지 않는다)."""
    items, entered, grads = [], [], []
    earth = _earth_tickers(curr_snap)
    for t in trans:
        name, when = t.get("name") or t.get("ticker", ""), _short_date(t["date"])
        if t.get("dir") == "in":
            entered.append(t)
            facts = [f"{t['rank']}위"] if t.get("rank") else []
            if t.get("mc"):
                facts.append(fmt_mc(t["mc"]))
            if t.get("momentum_1y") is not None:
                facts.append(f"1Y {t['momentum_1y']:+}%")
            if t.get("replacing_name"):
                why = f"{t['replacing_name']} 자리 교체 · 우위 {t.get('swap_streak', '?')}거래일 연속"
            else:
                why = f"기준 충족 {t.get('streak_in', '?')}거래일 연속"
            fact = f"(<span class='em-up'>{', '.join(facts)}</span>) " if facts else ""
            items.append(f"<strong>{name}</strong> 잠재지배자 신규 편입 {fact}— {why}, {when} 확정")
            continue
        tk = t.get("ticker")
        if tk in earth:
            grads.append(t)
            items.append(f"<strong>{name}</strong> — "
                         f"<span class='em-up'>★ TOP 20 졸업 (글로벌 {earth[tk]}위)</span>")
            continue
        if t.get("replaced_by_name"):
            why = f"{t['replaced_by_name']}에 자리 교체"
        elif t.get("carried_days"):
            why = f"데이터 결측 {t['carried_days']}거래일 연속"
        elif t.get("streak_out"):
            why = f"기준 미달 {t['streak_out']}거래일 연속"
        else:
            why = None
        tail = f" — {why}, {when} 확정" if why else f", {when} 확정"
        items.append(f"<strong>{name}</strong> 잠재지배자 제외{tail}")
    return items, entered, grads


def build_watch_line(state):
    """'경계 관찰' 한 줄 — 제외 카운트가 도는 멤버와 대기 중인 후보. 낱말은 상태 숫자에서 만든다."""
    if not state:
        return None
    lim = state.get("limits") or {}
    members = state.get("members") or {}
    parts = []
    for tk, m in sorted(members.items(), key=lambda kv: -max(kv[1].get("streak_out", 0),
                                                               kv[1].get("carried_days", 0))):
        nm = m.get("name") or tk
        if m.get("carried_days"):
            parts.append(f"<strong>{nm}</strong> 데이터 결측 {m['carried_days']}/{lim.get('miss', '?')}")
        elif m.get("streak_out"):
            parts.append(f"<strong>{nm}</strong> 제외 카운트 {m['streak_out']}/{lim.get('out', '?')}")
    full = len(members) >= int(lim.get("cap", 14))
    for tk, c in sorted((state.get("candidates") or {}).items(),
                        key=lambda kv: kv[1].get("rank") or 999):
        nm = c.get("name") or tk
        if c.get("streak_in", 0) < int(lim.get("in", 3)):
            parts.append(f"<strong>{nm}</strong> 편입 카운트 {c.get('streak_in', 0)}/{lim.get('in', '?')}")
        elif full and c.get("swap_streak"):
            parts.append(f"<strong>{nm}</strong> 교체 카운트 {c['swap_streak']}/{lim.get('swap_days', '?')}")
        elif full:
            parts.append(f"<strong>{nm}</strong> 기준 충족 {c.get('streak_in')}거래일 · 빈자리 대기")
    return " · ".join(parts) if parts else None


def build_latent_extra_blocks(curr_latent, d):
    """잠재지배자 부가 블록: 섹터 분포 + 관전 포인트 (모두 데이터 기반 자동)."""
    blocks = []
    # 섹터 분포 (theme 집계)
    themes = {}
    for s in curr_latent:
        th = s.get("theme") or "기타"
        themes.setdefault(th, []).append(s.get("name", ""))
    if themes:
        items = [f"<strong>{th} ({len(names)})</strong>: {', '.join(names)}"
                 for th, names in sorted(themes.items(), key=lambda kv: -len(kv[1]))]
        blocks.append({"type": "items", "label": f"섹터 분포 ({len(curr_latent)}종목)", "items": items})
    # 관전 포인트 (숫자 기반)
    pts = []
    moms = [s for s in curr_latent if s.get("momentum_1y") is not None]
    if moms:
        hot = max(moms, key=lambda s: s["momentum_1y"])
        pts.append(f"<strong>가장 폭발적</strong>: {hot['name']} — 1Y <span class='em-up'>+{hot['momentum_1y']}%</span>")
    if d.get("entered"):
        top_new = min(d["entered"], key=lambda s: s.get("rank") or 999)
        pts.append(f"<strong>신규 중 최고 순위</strong>: {top_new['name']} — 글로벌 {top_new.get('rank','?')}위 데뷔")
    grads = [s for s in d.get("exited", []) if s.get("_grad")]
    if grads:
        pts.append(f"<strong>졸업</strong>: {', '.join(s['name'] for s in grads)} — TOP 20 진입")
    if pts:
        blocks.append({"type": "items", "label": "관전 포인트", "items": pts})
    return blocks


# ───────────────────────── 공통 헬퍼 ─────────────────────────

def _fx_block():
    """환율·환산 블록 (latest.json meta 기준). 없으면 None."""
    try:
        m = json.loads(LATEST_PATH.read_text(encoding="utf-8")).get("meta", {})
        fx = m.get("usd_krw")
        if not fx:
            return None
        date_label = m.get("fetched_date", "")[-5:].replace("-", "/")
        rate_disp = f"{fx:,.0f}"
        mult = f"{fx/1000:.2f}"
        return {"type": "stats", "label": "환율 · 환산",
                "items": [{"k": "USD/KRW", "v": f"{rate_disp} ({date_label} 기준)"},
                          {"k": "환산식", "v": f"시총(조원) = 시총(USD bn) × {mult}"}]}
    except Exception:
        return None


def _make_entry(items, prev_date, curr_date, label):
    def short(dstr):
        dt = datetime.strptime(dstr, "%Y-%m-%d")
        return f"{dt.month}.{dt.day}"
    entry = {
        "date": curr_date.replace("-", "."),
        "period": f"{short(prev_date)} → {short(curr_date)} (주간)",
        "auto": True,
        "blocks": [{"type": "items", "label": label, "items": items}],
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
    items = build_items(curr_snap, prev_snap)
    if items:
        entry = _make_entry(items, prev_date, curr_date, "주간 변동 사항 (자동 감지)")
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
    watch = build_watch_line(lstate)
    if litems or watch:
        lentry = _make_entry(litems, prev_date, curr_date, "잠재지배자 주간 변동 (자동 감지)")
        if not litems:
            lentry["blocks"] = []
        if watch:
            lentry["blocks"].append({"type": "items", "label": "경계 관찰", "items": [watch]})
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
        _preview("잠재지배자 History", litems + ([watch] if watch else []))
    else:
        print("[정보] 잠재지배자: 이번 주 변동 없음")

if __name__ == "__main__":
    main()
