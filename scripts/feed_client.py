#!/usr/bin/env python3
"""
피드 공용 클라이언트 (feed_client) — 구글뉴스 레이트 리밋 방어 + fetch 상태 원장
================================================================================
배경 (2026-08-22 실측 사고): 논제 v1 반영 후 첫 full 에서 동행 관측의 구글뉴스
소스 7개가 **전부 503** 이었다. 우아한 저하 덕에 파이프라인은 살았지만 결과는
"후보 0 · 발행 0" 이었고, 그건 조용한 날과 구분되지 않는다.
같은 회차에 fetch_signals(4) + companion(7) = 11개 요청이 간격 없이 같은 호스트로
나간 것이 원인으로 의심된다.

이 모듈이 하는 일:
  ① 페이싱 — 같은 호스트로 연속 요청 사이에 최소 간격 + 지터를 둔다.
     프로세스가 달라도(fetch_signals / companion_essays) 마지막 타격 시각을
     원장에 남겨 이어받는다.
  ② 재시도 — 503·429·5xx 는 지수 백오프로 3회까지 다시 두드린다.
  ③ 순서 섞기 — 같은 호스트 연타를 피하도록 소스 순서를 재배열한다.

⚠️ 저하 동작은 바꾸지 않는다. 실패는 여전히 예외가 아니라 '건너뜀'이고,
   상한·호스트 필터·파싱은 호출자가 그대로 갖는다. 여기서는 HTTP 만 맡는다.
"""
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
MIN_GAP = 2.0          # 같은 호스트 연속 요청 최소 간격(초)
JITTER = 1.5           # 그 위에 얹는 무작위 지터 상한(초) — 규칙적 패턴 회피
RETRIES = 3            # 503/429/5xx 재시도 횟수
BACKOFF = (4, 10, 20)  # 지수 백오프 대기(초)
RETRY_CODES = {429, 500, 502, 503, 504}

_last_hit = {}         # {host: monotonic ts} — 이 프로세스 안의 마지막 타격


def _host(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _pace(host):
    """같은 호스트로 너무 빨리 다시 두드리지 않는다."""
    if not host:
        return
    gap = MIN_GAP + random.random() * JITTER
    last = _last_hit.get(host)
    if last is not None:
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def interleave_by_host(sources, key=lambda s: s.get("url", "")):
    """같은 호스트가 연달아 나오지 않도록 순서를 재배열한다.

    호스트가 한 종류뿐이면(동행처럼 전부 구글뉴스) 순서는 그대로다 — 그때는
    페이싱만이 유효한 지렛대다. 호스트가 섞인 목록(신호 관측소)에서만 효과가 있다.
    """
    buckets = {}
    for s in sources:
        buckets.setdefault(_host(key(s)), []).append(s)
    out = []
    while any(buckets.values()):
        for h in list(buckets):
            if buckets[h]:
                out.append(buckets[h].pop(0))
    return out


def fetch(url, label, kind, headers=None, timeout=25):
    """피드 하나를 가져온다 → (text | None, outcome, code).

    outcome 은 이 시점의 HTTP 결과만 말한다 — ok · http_error · timeout.
    '성공했는데 0건'인지는 파싱·필터 뒤에야 알 수 있으므로 호출자의 몫이다.
    """
    host = _host(url)
    code = None
    for attempt in range(RETRIES + 1):
        _pace(host)
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            code = r.status_code
            if code in RETRY_CODES and attempt < RETRIES:
                wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                print(f"[피드] {label}: HTTP {code} — {wait}초 뒤 재시도 "
                      f"({attempt + 1}/{RETRIES})", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text, "ok", code
        except requests.exceptions.Timeout:
            if attempt < RETRIES:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                continue
            return None, "timeout", None
        except Exception as e:
            if code in RETRY_CODES and attempt < RETRIES:
                continue
            print(f"[피드] {label}: 수집 실패 ({e})", file=sys.stderr)
            return None, "http_error", code
    return None, "http_error", code
