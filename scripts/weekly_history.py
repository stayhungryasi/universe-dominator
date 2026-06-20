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


def main():
    curr_date = TODAY_KST.strftime("%Y-%m-%d")
    curr_snap = load_snapshot(curr_date)
    if not curr_snap:
        # 오늘 스냅샷이 아직이면 latest.json에서 즉석 생성
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
    
    items = build_items(curr_snap, prev_snap)
    
    if not items:
        print("[정보] 이번 주 유의미한 변동 없음 — entry 생성 안 함")
        sys.exit(0)
    
    # 기간 라벨 (6.13 → 6.20 형식)
    def short(d):
        dt = datetime.strptime(d, "%Y-%m-%d")
        return f"{dt.month}.{dt.day}"
    period = f"{short(prev_date)} → {short(curr_date)} (주간)"
    date_label = curr_date.replace("-", ".")
    
    new_entry = {
        "date": date_label,
        "period": period,
        "auto": True,
        "blocks": [
            {"type": "items", "label": "주간 변동 사항 (자동 감지)", "items": items}
        ],
    }
    
    # history-top20.json 로드 후 맨 앞에 추가
    if HIST_TOP20_PATH.exists():
        hist = json.loads(HIST_TOP20_PATH.read_text(encoding="utf-8"))
    else:
        hist = {
            "page_title": "우주지배자 변동 이력",
            "page_desc": "글로벌 시가총액 TOP 20의 시점별 변동 기록",
            "entries": [],
        }
    
    # 같은 날짜 auto entry가 이미 있으면 교체 (중복 방지)
    entries = hist.get("entries", [])
    entries = [e for e in entries if not (e.get("date") == date_label and e.get("auto"))]
    entries.insert(0, new_entry)
    hist["entries"] = entries
    
    HIST_TOP20_PATH.write_text(
        json.dumps(hist, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] History entry 추가: {date_label} ({len(items)}개 변동)")
    for it in items:
        # 태그 제거하고 콘솔 미리보기
        import re
        clean = re.sub(r"<[^>]+>", "", it)
        print(f"   • {clean}")


if __name__ == "__main__":
    main()
