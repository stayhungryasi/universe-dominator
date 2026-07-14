#!/usr/bin/env python3
"""
데이터 무결성 검증 (validate_data) — fetch_data 직후 실행
================================================================
파이프라인이 스스로 이상을 신고하게 하는 감시탑.
2026-07 AMD 누락 사고(특정 종목이 매일 조용히 탈락) 이후 도입.

검사 항목:
  ① 각 권역 종목 수 (TOP_N 미달 경고)
  ② 빈 이름 / 시총 0 / 중복 티커
  ③ 권역 내 시총 내림차순 여부
  ④ 전일 스냅샷 대비 "실종" 감시 — 어제 상위 10위 안에 있던 종목이
     오늘 리스트에서 완전히 사라졌으면 경고 (순위 이동이 아닌 증발 탐지)
  ⑤ fetch_data가 남긴 meta.warnings(소스 순위 구멍) 재출력

결과: data/quality_report.json 기록 + Actions 로그에 [무결성] 라인 출력.
경고가 있어도 파이프라인은 계속된다(데이터 보존 원칙) — 단, 로그에 크게 남는다.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SNAP_DIR = DATA_DIR / "snapshots"
KST = timezone(timedelta(hours=9))

TOP_N = 20


def load_prev_snapshot(today_str):
    """오늘이 아닌 가장 최근 스냅샷"""
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(SNAP_DIR.glob("*.json"), reverse=True)
    for s in snaps:
        if today_str not in s.name:
            try:
                return json.loads(s.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def region_stocks(data, key):
    """latest.json({regions:{k:{stocks:[...]}}})과 스냅샷({regions:{k:[...]}}) 두 구조 모두 지원"""
    try:
        v = data["regions"][key]
        if isinstance(v, list):
            return v
        return v.get("stocks", [])
    except Exception:
        return []


def main():
    warns, infos = [], []
    data = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    today = data.get("meta", {}).get("fetched_date", "")

    # ⑤ fetch 단계 경고 재출력 (소스 순위 구멍 등)
    for w in data.get("meta", {}).get("warnings", []) or []:
        warns.append(f"수집단계: {w}")

    for key, region in data.get("regions", {}).items():
        stocks = region.get("stocks", [])

        # ① 종목 수
        if len(stocks) < TOP_N:
            warns.append(f"{key}: 종목 수 {len(stocks)}/{TOP_N} 미달")

        # ② 빈 이름 / 시총 0 / 중복
        for s in stocks:
            if not (s.get("name") or "").strip():
                warns.append(f"{key}: 빈 이름 발견 (ticker={s.get('ticker')})")
            if not s.get("mc"):
                warns.append(f"{key}: 시총 0 — {s.get('name')}")
        tickers = [s.get("ticker") for s in stocks if s.get("ticker")]
        dups = {t for t in tickers if tickers.count(t) > 1}
        if dups:
            warns.append(f"{key}: 중복 티커 {sorted(dups)}")

        # ③ 내림차순
        mcs = [s.get("mc", 0) for s in stocks]
        if any(mcs[i] < mcs[i + 1] for i in range(len(mcs) - 1)):
            warns.append(f"{key}: 시총 정렬 이상 (내림차순 아님)")

    # ④ 전일 대비 실종 감시
    prev = load_prev_snapshot(today)
    if prev:
        for key in data.get("regions", {}):
            prev_top10 = region_stocks(prev, key)[:10]
            cur_all = {(s.get("ticker"), s.get("name"))
                       for s in region_stocks(data, key)}
            cur_tickers = {s.get("ticker") for s in region_stocks(data, key)}
            for s in prev_top10:
                tk, nm = s.get("ticker"), s.get("name")
                if (tk, nm) not in cur_all and tk not in cur_tickers:
                    warns.append(
                        f"{key}: 어제 상위권 '{nm}'({tk})이 오늘 리스트에서 실종 — "
                        f"순위 이동이 아닌 증발이면 파싱 탈락 의심")
    else:
        infos.append("전일 스냅샷 없음 — 실종 감시 생략")

    # ── 결과 기록 & 출력 ──
    report = {
        "checked_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "status": "WARN" if warns else "OK",
        "warnings": warns,
        "infos": infos,
    }
    (DATA_DIR / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    if warns:
        print(f"[무결성] ⚠⚠⚠ 경고 {len(warns)}건 — 확인 필요 ⚠⚠⚠", file=sys.stderr)
        for w in warns:
            print(f"[무결성] ⚠ {w}", file=sys.stderr)
    else:
        print("[무결성] ✅ 전 항목 통과")
    for i in infos:
        print(f"[무결성] ℹ {i}")


if __name__ == "__main__":
    main()
