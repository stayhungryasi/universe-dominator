#!/usr/bin/env python3
"""
판단층 병합 (buffett_layers) — 기계가 채우고 사람이 덮어쓴다
================================================================================
헌법 개정(2026-08-31): 판단층은 이제 두 겹이다.

  data/buffett_auto.json    기계 판단층 — 매일 갱신된다. 사람은 여기 손대지 않는다.
  data/buffett_config.json  사람 판단층 — `buffett` 블록은 **오버라이드 전용**.
                            기계는 이 파일을 절대 쓰지 않는다(읽기만).

병합은 **필드 단위**다. 블록 통째로 고르지 않는다 — 사람이 risk5 하나만 취재했다고
기계가 잰 eps_adj_ttm 까지 버리면 취재가 늘수록 화면이 비는 역설이 생긴다.

  사람 값이 null 이 아니면 → 사람 값 (origin "human")
  아니면 자동 값이 있으면  → 자동 값 (origin "auto")
  둘 다 없으면             → null   (origin None)

origin 은 화면이 "이 숫자는 누가 적었나"를 표시하기 위한 것이다. 자동값에 사람의
권위를 씌우면 안 된다 — 공시 추출·AI 판정은 틀릴 수 있고, 그 사실을 화면이 말해야 한다.

잠재지배자 overrides(generate_candidates.load_overrides)와 같은 뼈대다: 기계가 만든
목록에 사람이 고른 칸만 덮어쓴다. 다른 점은 **어느 칸을 덮었는지 되돌려준다**는 것.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
CFG_PATH = DATA_DIR / "buffett_config.json"
AUTO_PATH = DATA_DIR / "buffett_auto.json"

# 병합 대상 필드 — 여기 없는 키는 병합되지 않는다(스키마를 한 곳에서만 늘린다)
FIELDS = [
    "as_of", "period", "cyclical_peak_guard",
    "eps_adj", "eps_adj_ttm", "roe_tangible",
    "g_cagr3y", "g_forward", "g_forward_source",
    "owner_earnings", "conversion", "franchise", "risk5", "capalloc",
    "retention_test", "method", "source", "confidence", "notes",
]


def is_null(v):
    """'값이 없다'의 정의 — 한 곳에만 둔다.

    주의 ① False·0 은 값이다. 가드가 False 인 것과 안 적힌 것은 다르다.
    주의 ② {"value": null, "note": "미취재"} 는 **없는 값**이다. 취재 전 자리표시자를
           값으로 치면 기계가 잰 숫자를 영원히 덮어버린다(사람 값이 우선이므로).
    """
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, dict):
        if "value" in v:
            return v.get("value") is None
        return not v
    if isinstance(v, (list, str)):
        return len(v) == 0
    return False


def merge_block(human, auto):
    """한 종목의 판단 블록 병합 → (병합값, origin 맵)."""
    human = human if isinstance(human, dict) else {}
    auto = auto if isinstance(auto, dict) else {}
    out, origin = {}, {}
    for f in FIELDS:
        h, a = human.get(f), auto.get(f)
        if not is_null(h):
            out[f], origin[f] = h, "human"
        elif not is_null(a):
            out[f], origin[f] = a, "auto"
        else:
            out[f], origin[f] = None, None
    return out, origin


def load_auto(path=None):
    """기계 판단층 — 없으면 빈 dict (첫 도입 회차·수집 실패 모두 조용히 견딘다)."""
    p = Path(path) if path else AUTO_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        items = doc.get("items")
        return items if isinstance(items, dict) else {}
    except Exception:
        return {}


def merged_items(cfg, auto=None):
    """config 의 items 를 **병합된 buffett 블록으로 바꿔** 돌려준다.

    원본 dict 를 손대지 않고 얕은 복사본을 만든다 — 판단층 파일은 사람의 것이라
    프로세스 안에서도 오염시키지 않는다. 각 항목에 `buffett_origin` 을 함께 싣는다.
    """
    auto = load_auto() if auto is None else auto
    out = []
    for it in (cfg.get("items") or []):
        row = dict(it)
        block, origin = merge_block(it.get("buffett"), auto.get(it.get("ticker")))
        row["buffett"] = block
        row["buffett_origin"] = origin
        out.append(row)
    return out


def origin_tally(items):
    """origin 집계 — 로그에 '몇 칸을 기계가 채웠나'를 남기기 위한 것."""
    t = {"human": 0, "auto": 0, "none": 0}
    for it in items:
        for v in (it.get("buffett_origin") or {}).values():
            t[v if v in ("human", "auto") else "none"] += 1
    return t
