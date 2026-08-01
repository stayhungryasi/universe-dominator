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

    entry = {"date": now.strftime("%Y.%m.%d"), "title": col["title"][:80],
             "summary": (col.get("summary") or "")[:120], "body": col["body"],
             "auto": True}
    cols_data["columns"] = [entry] + cols_data.get("columns", [])
    COLS_PATH.write_text(json.dumps(cols_data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"[주간칼럼] 발행 완료 — 「{entry['title']}」")


if __name__ == "__main__":
    main()
