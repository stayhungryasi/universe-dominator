#!/usr/bin/env python3
"""
API 비용 계량기 (2026-11-14 ㉗) — Claude API 를 부르는 스크립트가 13개인데 비용을 재는 자가 없었다.
11월은 13일 동안 아무도 모르는 사이 조직 월 지출 한도를 채워 API 전체가 멈췄다(㉕). 재지 않으면 줄일 수도 없다.

하는 일: Claude Messages API 응답을 **관찰만** 한다 — 응답에 실려 오는 사용량(usage)에 단가를 곱해
스크립트별·날짜별(KST) 비용을 `data/api_cost.json` 에 쌓는다. 호출의 내용·결과·예외는 건드리지 않는다.

쓰는 법: API 를 부르는 스크립트 머리에 한 줄 —  import api_meter
  · requests 의 요청 한 곳(Session.request)에 관찰자를 건다 → 그 스크립트의 모든 호출이 저절로 잡힌다(새 호출 포함)
  · 관찰자 안의 어떤 오류도 호출을 망치지 않는다(삼키고 지나간다) — 계량기가 측정 대상을 세우면 안 된다
  · 기록은 CI(GITHUB_ACTIONS=true) 또는 API_METER=1 일 때만. API_METER=0 이면 끈다. 로컬·테스트는 파일을 쓰지 않는다
  · 단가표에 없는 모델은 토큰만 세고 비용은 '미등록'으로 남긴다 — 틀린 숫자보다 빈칸이 낫다

보고는 api_cost_report.py(데일리 full · 하루 1회 소장 DM).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
LEDGER = Path(__file__).parent.parent / "data" / "api_cost.json"
KEEP_DAYS = 40
MESSAGES_URL = "api.anthropic.com/v1/messages"

# USD / 100만 토큰 — (입력, 출력, 캐시 쓰기 5분, 캐시 읽기). 출처: platform.claude.com/docs/en/about-claude/pricing
# (2026-11-14 확인). 모델 id 는 앞머리로 맞춘다(날짜 꼬리 허용). 긴 이름이 먼저 맞도록 정렬해 쓴다.
PRICES = {
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
}
WEB_SEARCH_USD = 10.0 / 1000        # 서버 웹 검색 1회


def now_kst():
    """시계는 이 입구 하나(㉑) — 테스트는 여기를 고정한다."""
    return datetime.now(KST)


def enabled():
    flag = os.environ.get("API_METER", "").strip()
    if flag == "0":
        return False
    return flag == "1" or os.environ.get("GITHUB_ACTIONS") == "true"


def price_of(model):
    for name in sorted(PRICES, key=len, reverse=True):
        if str(model or "").startswith(name):
            return PRICES[name]
    return None


def cost_usd(model, usage):
    """→ (USD 또는 None, 토큰 사전). 단가표에 없는 모델은 None."""
    u = usage if isinstance(usage, dict) else {}
    tok = {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
           "cw": int(u.get("cache_creation_input_tokens") or 0), "cr": int(u.get("cache_read_input_tokens") or 0),
           "ws": int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)}
    p = price_of(model)
    if p is None:
        return None, tok
    usd = (tok["in"] * p[0] + tok["out"] * p[1] + tok["cw"] * p[2] + tok["cr"] * p[3]) / 1_000_000
    return usd + tok["ws"] * WEB_SEARCH_USD, tok


def script_name():
    return Path(sys.argv[0] or "unknown").stem or "unknown"


def _load(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("days"), dict):
            return doc
    except Exception:
        pass
    return {"schema": 1, "days": {}}


def _save(path, doc):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def record(script, model, usage, ok=True, path=None, now=None):
    """호출 1건을 원장에 더한다. 실패한 호출(ok=False)은 건수만 — 과금 여부를 알 수 없다."""
    path = LEDGER if path is None else path
    now = now or now_kst()
    doc = _load(path)
    day = doc["days"].setdefault(now.strftime("%Y-%m-%d"), {})
    row = day.setdefault(script, {"calls": 0, "fail": 0, "in": 0, "out": 0, "cw": 0, "cr": 0, "ws": 0,
                                  "usd": 0.0, "unpriced": 0, "models": {}})
    row["calls"] += 1
    if not ok:
        row["fail"] += 1
    else:
        usd, tok = cost_usd(model, usage)
        for k in ("in", "out", "cw", "cr", "ws"):
            row[k] += tok[k]
        m = row["models"].setdefault(str(model or "?"), {"calls": 0, "usd": 0.0})
        m["calls"] += 1
        if usd is None:
            row["unpriced"] += 1
        else:
            row["usd"] = round(row["usd"] + usd, 6)
            m["usd"] = round(m["usd"] + usd, 6)
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    doc["days"] = {d: v for d, v in doc["days"].items() if d >= cut}
    _save(path, doc)
    return row


def observe(url, resp, path=None, now=None):
    """요청 하나를 본다. Claude Messages 호출이 아니면 아무것도 하지 않는다."""
    if MESSAGES_URL not in str(url or "") or "/batches" in str(url or "") or not enabled():
        return None
    if getattr(resp, "status_code", 0) != 200:
        return record(script_name(), None, None, ok=False, path=path, now=now)
    data = resp.json()
    if not isinstance(data, dict) or "usage" not in data:
        return record(script_name(), None, None, ok=False, path=path, now=now)
    return record(script_name(), data.get("model"), data.get("usage"), ok=True, path=path, now=now)


def install():
    """requests 의 요청 한 곳에 관찰자를 건다(여러 번 불러도 한 번만)."""
    try:
        import requests
    except Exception:
        return False
    sess = requests.sessions.Session
    if getattr(sess.request, "_api_meter", False):
        return True
    orig = sess.request

    def request(self, method, url, *args, **kwargs):
        resp = orig(self, method, url, *args, **kwargs)
        try:
            observe(url, resp)
        except Exception as e:                     # 계량기가 호출을 망치지 않는다 — 흔적만 남긴다
            print(f"[계량] 기록 실패(호출은 정상): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return resp

    request._api_meter = True
    sess.request = request
    return True


install()
