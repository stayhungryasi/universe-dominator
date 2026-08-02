#!/usr/bin/env python3
"""
주간 자동 칼럼 (weekly_column) — 관측일지 하이브리드의 자동 축
================================================================
매주 토요일, 지난 7일의 우주 변동(왕좌·TOP20·시총 증감·잠재)을 집계해
Claude가 관측일지 문체로 칼럼을 작성 → data/columns.json 맨 앞에 추가.

원칙:
  - 토요일(KST)에만 작동 (workflow 게이트와 이중 안전)
  - 같은 주에 이미 발행했으면 침묵 (title의 주차 표기로 중복 방지)
  - API 키 없거나 실패하면 조용히 건너뜀 — columns.json 원본 보존
  - 발행되면 관제탑 봇이 자동으로 커뮤니티에 공지 (기존 배선 재사용)
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SNAP_DIR = DATA_DIR / "snapshots"
COLS_PATH = DATA_DIR / "columns.json"
KST = timezone(timedelta(hours=9))


def load_snapshot_before(days):
    """약 N일 전 스냅샷 (없으면 그보다 오래된 것 중 최신)"""
    if not SNAP_DIR.exists():
        return None
    target = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    snaps = sorted(SNAP_DIR.glob("*.json"))
    older = [s for s in snaps if s.stem <= target]
    pick = older[-1] if older else (snaps[0] if snaps else None)
    if not pick:
        return None
    try:
        return json.loads(pick.read_text(encoding="utf-8"))
    except Exception:
        return None


def region_list(data, key):
    v = data.get("regions", {}).get(key, [])
    return v if isinstance(v, list) else v.get("stocks", [])


def weekly_digest():
    """지난 7일 변동 원자료 (Claude 입력용)"""
    latest = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    cur = latest["regions"]["earth"]["stocks"]
    cur_latent = latest.get("latent", [])
    prev = load_snapshot_before(7)
    d = {"today": latest.get("meta", {}).get("fetched_date", ""),
         "king": {"name": cur[0]["name"], "mc_t": round(cur[0]["mc"] / 1000, 2)},
         "top5": [{"rank": i, "name": s["name"], "mc_t": round(s["mc"] / 1000, 2)}
                  for i, s in enumerate(cur[:5], 1)]}
    if prev:
        pe = region_list(prev, "earth")
        if pe:
            d["prev_king"] = pe[0]["name"]
            d["king_changed"] = pe[0].get("ticker") != cur[0].get("ticker")
            pm = {r["ticker"]: r["mc"] for r in pe if r.get("ticker") and r.get("mc")}
            movers = []
            for s in cur:
                if s["ticker"] in pm:
                    movers.append({"name": s["name"],
                                   "chg": round((s["mc"] - pm[s["ticker"]]) / pm[s["ticker"]] * 100, 1)})
            movers.sort(key=lambda x: -x["chg"])
            d["week_hot"] = movers[:3]
            d["week_cold"] = movers[-3:][::-1]
            pv = {r["ticker"] for r in pe if r.get("ticker")}
            cv = {s["ticker"]: s["name"] for s in cur}
            d["entered"] = [cv[t] for t in cv if t not in pv]
            d["exited"] = [r["name"] for r in pe if r.get("ticker") not in cv]
        pl = {r.get("ticker") for r in prev.get("latent", [])}
        d["latent_new"] = [x["name"] for x in cur_latent if x.get("ticker") not in pl]
        d["latent_top"] = [{"name": x["name"], "momentum": x.get("momentum_1y")}
                           for x in sorted(cur_latent, key=lambda x: -(x.get("momentum_1y") or 0))[:3]]
    return d


def ask_claude(api_key, digest, week_label):
    prompt = f"""당신은 글로벌 시가총액 추적 사이트 UNIVERTRIX(우주지배자)의 관측일지 필자입니다.
사이트 세계관: 기업 = 행성, 시총 1위 = 태양(왕좌), 순위표 = 우주의 질서. 모토는 "우주는 돌고, 기록은 남는다".
아래 지난 7일 관측 데이터로 주간 칼럼을 작성하세요.

데이터: {json.dumps(digest, ensure_ascii=False)}

요구사항:
- 한국어, 우주 은유를 절제 있게 사용한 담백하고 격조 있는 문체
- 과장·투자권유 금지, 데이터에 있는 사실만 사용
- 분량: 문단 4~6개
- 반드시 아래 JSON 형식으로만 응답 (다른 텍스트·마크다운 금지):
{{"title": "{week_label} 주간 관측 — (부제목)", "summary": "(한 줄 요약, 80자 이내)", "body": "(<p>와 <h3>만 사용한 HTML 본문)"}}"""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5", "max_tokens": 3000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    i, j = text.find("{"), text.rfind("}")
    col = json.loads(text[i:j + 1])
    assert col.get("title") and col.get("body")
    return col


# ─────────────────── 주간 차트 자동 생성 ───────────────────

def _bar_chart(title, subtitle, series, unit="", W=700, H=300):
    """세로 막대 — series: [(label, value)]"""
    maxv = max(v for _, v in series) or 1
    n = len(series)
    pad_l, pad_r, pad_t, pad_b = 40, 40, 78, 56
    cw = (W - pad_l - pad_r) / n
    bw = min(74, cw * 0.56)
    base_y = H - pad_b
    o = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    o.append('<defs><linearGradient id="wg" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0%" stop-color="#f2ba3c"/><stop offset="100%" stop-color="#9c7a3a"/></linearGradient></defs>')
    o.append(f'<rect width="{W}" height="{H}" fill="#10141f"/>')
    o.append(f'<text x="26" y="34" fill="#f2ba3c" font-size="19" font-weight="800">{title}</text>')
    o.append(f'<text x="26" y="56" fill="#8a94b8" font-size="12.5" font-weight="600">{subtitle}</text>')
    o.append(f'<line x1="{pad_l-14}" y1="{base_y}" x2="{W-pad_r+14}" y2="{base_y}" stroke="#3a4568"/>')
    for i, (label, v) in enumerate(series):
        x = pad_l + cw * i + (cw - bw) / 2
        h = (v / maxv) * (base_y - pad_t)
        y = base_y - h
        o.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw:.0f}" height="{h:.0f}" rx="7" fill="url(#wg)"/>')
        o.append(f'<text x="{x+bw/2:.0f}" y="{y-10:.0f}" text-anchor="middle" fill="#fff" font-size="16" font-weight="800">{v:g}{unit}</text>')
        o.append(f'<text x="{x+bw/2:.0f}" y="{base_y+22:.0f}" text-anchor="middle" fill="#c8d0ea" font-size="12" font-weight="700">{label}</text>')
    o.append(f'<text x="{W-26}" y="{H-14}" text-anchor="end" fill="#9c7a3a" font-size="11.5" font-weight="800">UNIVERTRIX · univertrix.com</text>')
    o.append('</svg>')
    return '<div class="col-chart">' + ''.join(o) + '</div>'


def _mover_chart(hot, cold, W=700, H=310):
    """주간 등락 — 상승 금빛 / 하락 코발트 가로 막대"""
    rows = [(x["name"], x["chg"], True) for x in hot] + [(x["name"], x["chg"], False) for x in cold]
    if not rows:
        return ""
    maxa = max(abs(c) for _, c, _ in rows) or 1
    pad_t = 74
    rh = (H - pad_t - 40) / len(rows)
    bh = min(26, rh * 0.62)
    o = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    o.append(f'<rect width="{W}" height="{H}" fill="#10141f"/>')
    o.append('<text x="26" y="34" fill="#f2ba3c" font-size="19" font-weight="800">이번 주의 별 — 타오른 별과 식은 별</text>')
    o.append('<text x="26" y="56" fill="#8a94b8" font-size="12.5" font-weight="600">지구 TOP 20 주간 시총 증감률 상·하위</text>')
    for i, (name, chg, up) in enumerate(rows):
        y = pad_t + rh * i + (rh - bh) / 2
        w = abs(chg) / maxa * (W - 320)
        color = 'url(#wg2)' if up else '#386ee1'
        o.append(f'<text x="26" y="{y+bh/2+5:.0f}" fill="#e6ebfa" font-size="13" font-weight="800">{name[:16]}</text>')
        o.append(f'<rect x="200" y="{y:.0f}" width="{max(w,4):.0f}" height="{bh:.0f}" rx="6" fill="{color}"/>')
        sign = "+" if chg > 0 else ""
        o.append(f'<text x="{200+max(w,4)+10:.0f}" y="{y+bh/2+5:.0f}" fill="#fff" font-size="14.5" font-weight="800">{sign}{chg:g}%</text>')
    o.insert(1, '<defs><linearGradient id="wg2" x1="0" y1="0" x2="1" y2="0">'
                '<stop offset="0%" stop-color="#9c7a3a"/><stop offset="100%" stop-color="#f2ba3c"/></linearGradient></defs>')
    o.append(f'<text x="{W-26}" y="{H-14}" text-anchor="end" fill="#9c7a3a" font-size="11.5" font-weight="800">UNIVERTRIX · univertrix.com</text>')
    o.append('</svg>')
    return '<div class="col-chart">' + ''.join(o) + '</div>'


def build_charts(digest):
    charts = ""
    hot = digest.get("week_hot") or []
    cold = digest.get("week_cold") or []
    charts += _mover_chart(hot[:3], cold[:3])
    top5 = digest.get("top5") or []
    if top5:
        charts += _bar_chart("이번 주의 태양계 — TOP 5 시가총액",
                             "조 달러($T) 기준 · " + digest.get("today", ""),
                             [(t["name"].split(" ")[0][:10], t["mc_t"]) for t in top5], unit="")
    return charts


def main():
    now = datetime.now(KST)
    if now.weekday() != 5:  # 5 = 토요일
        print("[주간칼럼] 토요일 아님 — 건너뜀")
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[주간칼럼] ANTHROPIC_API_KEY 미설정 — 건너뜀")
        return

    week_label = now.strftime("%m월 %d일")
    cols_data = {"columns": []}
    if COLS_PATH.exists():
        try:
            cols_data = json.loads(COLS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if any(week_label in (c.get("title") or "") for c in cols_data.get("columns", [])):
        print(f"[주간칼럼] {week_label} 주간 이미 발행 — 침묵")
        return

    digest = weekly_digest()
    try:
        col = ask_claude(api_key, digest, week_label)
    except Exception as e:
        print(f"[주간칼럼] 생성 실패: {e} — columns.json 보존", file=sys.stderr)
        sys.exit(0)

    charts = ""
    try:
        charts = build_charts(digest)
    except Exception as e:
        print(f"[주간칼럼] 차트 생성 실패(본문만 발행): {e}", file=sys.stderr)
    entry = {"date": now.strftime("%Y.%m.%d"), "title": col["title"][:80],
             "summary": (col.get("summary") or "")[:120], "body": col["body"] + charts,
             "auto": True,
             "viz": {"type": "solar",
                     "planets": [{"n": t["name"], "mc": t["mc_t"]}
                                 for t in digest.get("top5", [])]}}
    cols_data["columns"] = [entry] + cols_data.get("columns", [])
    COLS_PATH.write_text(json.dumps(cols_data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"[주간칼럼] 발행 완료 — 「{entry['title']}」")


if __name__ == "__main__":
    main()
