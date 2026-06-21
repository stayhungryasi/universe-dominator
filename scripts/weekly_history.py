"""
weekly_history.py — 주간 변동 자동 감지 → History entry 생성

동작:
  1. 오늘 스냅샷과 7일 전 스냅샷을 비교
  2. 지구(earth) 지역 기준으로 진입/이탈/순위변동/시총변동 감지
  3. history-top20.json 의 entries 맨 앞에 새 entry 추가
  4. 잠재지배자(latent)는 큐레이션 영역이라 자동 비교 대상에서 제외
     → 대신 latest.json의 latent 목록 변동만 가볍게 기록

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


# ───────────────────────── 공통 헬퍼 ─────────────────────────

def _make_entry(items, prev_date, curr_date, label):
    def short(dstr):
        dt = datetime.strptime(dstr, "%Y-%m-%d")
        return f"{dt.month}.{dt.day}"
    return {
        "date": curr_date.replace("-", "."),
        "period": f"{short(prev_date)} → {short(curr_date)} (주간)",
        "auto": True,
        "blocks": [{"type": "items", "label": label, "items": items}],
    }


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
        _write_history(HIST_TOP20_PATH, {
            "page_title": "우주지배자 변동 이력",
            "page_desc": "글로벌 시가총액 TOP 20의 시점별 변동 기록",
            "entries": [],
        }, entry)
        _preview("우주지배자 History", items)
    else:
        print("[정보] 우주지배자: 이번 주 유의미한 변동 없음")

    # ── 잠재지배자 (latent) ──
    prev_latent = prev_snap.get("latent", [])
    curr_latent = curr_snap.get("latent", [])
    if not prev_latent:
        print("[정보] 잠재지배자: 지난주 스냅샷에 latent 없음 — 다음 주부터 자동 기록 시작")
    elif not curr_latent:
        print("[정보] 잠재지배자: 현재 목록 비어있음 — 건너뜀")
    else:
        dl = diff_latent(prev_latent, curr_latent)
        litems = build_latent_items(dl, curr_snap)
        if litems:
            lentry = _make_entry(litems, prev_date, curr_date, "잠재지배자 주간 변동 (자동 감지)")
            _write_history(HIST_LATENT_PATH, {
                "page_title": "잠재지배자 변동 이력",
                "page_desc": "차세대 우주지배자 후보의 시점별 변동 기록",
                "entries": [],
            }, lentry)
            _preview("잠재지배자 History", litems)
        else:
            print("[정보] 잠재지배자: 이번 주 변동 없음")


if __name__ == "__main__":
    main()
