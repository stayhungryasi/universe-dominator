#!/usr/bin/env python3
"""
SpaceX 관제실 v2 (probe_cockpit) — "한 화면에 판단, 클릭 한 번에 근거"
================================================================================
무인 탐사선 탭을 '한 줄로 쌓인 보고서'에서 관제실로 바꾼다. 정보는 하나도 버리지 않고
위계와 진입 경로만 바꾼다(2026-10-23).

  첫 화면(관제판) — 이 모듈이 **빌드 시점에 정적 HTML** 로 그린다(무JS 에서도 읽힌다)
      상태줄 · ① 가격 vs 가정 가치 · ② 이번 회차에 바뀐 것 · ③ 5축 진척 · ④ KPI · ⑤ 다음에 볼 것
  패널 7종·서랍·검색 — observatory-template 의 IIFE(PC)가 PROBE_DATA 로 그린다

헌법: 클라이언트 fetch 금지. 판단층(agent-research)은 읽기만. 보고서 본문은 준법 필터
(agent_report.sanitize)를 통과한 HTML 만 싣는다. 화면에 기계 키를 내보내지 않는다 —
축은 state.axes 의 한글 이름에서 짧은 이름만, 원장 키는 상태 사전으로 번역한다.
PROBE_DATA ≤ 400KB (보고서 섹션 HTML 은 별도 덩어리 probe_reports 로).
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
AR_DIR = HERE / "agent-research"
KST = timezone(timedelta(hours=9))

STATUSES = ("달성", "달성(조기)", "진행", "미달", "미판정")
STATUS_CLS = {"달성": "done", "달성(조기)": "early", "진행": "prog", "미달": "miss", "미판정": "wait"}
# 화면 상태 — 판정 상태(STATUSES)에서 생성한다. '진행'인데 진척 100% · 판정 연도 전이면 '현재 충족'으로 따로 보인다
MET = "현재 충족(판정 연도 전)"
SHOWN = ("달성", "달성(조기)", MET, "진행", "미달", "미판정")


def shown_status(status, pct, year, today):
    """판정 상태 + 진척 + 판정 연도 → 화면 상태. 번역 매핑이 아니라 상태에서 만든다(CLAUDE.md 규칙 8)."""
    try:
        before = int(year) > today.year
    except (TypeError, ValueError):
        before = False
    if status == "진행" and pct is not None and pct >= 1.0 and before:
        return MET
    return status
RUN_KO = {"success": "정상", "failure": "실패", "cancelled": "취소", "timed_out": "시간 초과"}
SRC_STATE_KO = {"ok": "정상", "zero": "새 글 없음", "decode_fail": "디코딩 실패",
                "http_error": "요청 실패", "timeout": "응답 지연"}
EVENT_KIND_KO = {"earnings": "실적", "contract": "계약", "financing": "조달", "report": "정기 공시",
                 "filing": "공시", "starship": "발사", "ir": "보도자료", "essay": "동행 에세이",
                 "withdraw": "철회·정정"}
PANELS = [("milestones", "이정표 매트릭스"), ("events", "사건"), ("fin", "재무·KPI"),
          ("scen", "시나리오·가치"), ("report", "보고서"), ("changelog", "변화 이력"),
          ("sources", "출처")]
MARK = "univertrix.com"


def esc(t):
    return (str("" if t is None else t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


# ────────────────────────────────────────────────────────────────
# 표기 — 한국어 단위, 결측은 '—' (0 으로 채우지 않는다)
# ────────────────────────────────────────────────────────────────

def usd_m_ko(v, digits=1):
    """백만 달러 → '$145.8억' · '−$99.6억' · '$1.2조'."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    sign = "−" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:,.{digits}f}조"
    return f"{sign}${a / 100:,.{digits}f}억"


def man_ko(million):
    """백만 명 → '1,360만'."""
    if not isinstance(million, (int, float)):
        return "—"
    return f"{million * 100:,.0f}만"


def num_ko(v, unit=""):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    if unit == "만 명":
        return f"{v:,.0f}만 명"
    s = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}"
    return s + (" " + unit if unit else "")


def short_axis(axes, key):
    """'축 A — Starlink(Connectivity)' → 'Starlink' · 'AI / 컴퓨트(SI)' → 'AI 컴퓨트'."""
    v = (axes or {}).get(key) if isinstance(axes, dict) else None
    if isinstance(v, dict):
        v = v.get("ko") or v.get("name")
    fallback = {"A": "Starlink", "B": "Starship", "C": "AI 컴퓨트", "D": "재무", "E": "외부"}
    if not isinstance(v, str) or not v.strip():
        return fallback.get((key or "?")[:1].upper(), "기타")
    s = re.sub(r"^\s*축\s*[A-E]\s*[—\-–:]\s*", "", v)
    s = re.sub(r"\s*\(.*$", "", s)
    s = re.sub(r"\s*/\s*", " ", s).strip()
    return s or fallback.get(key[:1].upper(), "기타")


def kst_today():
    return datetime.now(KST).date()


def mmdd(d):
    try:
        return f"{int(d[5:7])}/{int(d[8:10])}"
    except (TypeError, ValueError, IndexError):
        return "—"


# ────────────────────────────────────────────────────────────────
# 데이터 조립
# ────────────────────────────────────────────────────────────────

def meanings_from_reports(files, axes_keys):
    """PHASE 2 표(연도 | 이정표 | 달성 시 의미 | 미달 시 의미 | 확인 방법)에서 의미를 뽑는다.

    표는 축 순서대로 나온다(9월 보고서). 축 안에서 연도가 같고 낱말이 가장 많이 겹치는
    이정표에 붙인다. 못 찾으면 비운다 — 화면은 '—'.
    """
    out = {}
    for f in files:
        md = f.read_text(encoding="utf-8")
        tables, cur = [], None
        for ln in md.splitlines():
            s = ln.strip()
            if s.startswith("|") and "달성 시 의미" in s:
                cur = []
                tables.append(cur)
                continue
            if cur is not None:
                if s.startswith("|"):
                    if not re.fullmatch(r"\|[\s:\-|]+\|?", s):
                        cur.append([c.strip() for c in s.strip("|").split("|")])
                else:
                    cur = None
        if len(tables) < len(axes_keys):
            continue
        for ax, rows in zip(axes_keys, tables):
            for r in rows:
                if len(r) >= 5:
                    out.setdefault(ax, []).append({"year": re.sub(r"\D", "", r[0])[:4],
                                                   "text": re.sub(r"\*\*", "", r[1]),
                                                   "hit": r[2], "miss": r[3], "method": r[4],
                                                   "src": f.stem})
        if out:
            break
    return out


def _tok(s):
    return set(re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}|\d[\d,.]*", s or ""))


def attach_meaning(m, meanings):
    cands = [r for r in meanings.get((m.get("axis") or "?")[:1].upper(), []) if r["year"] == str(m.get("year"))]
    if not cands:
        return None
    t = _tok(m.get("statement"))
    return max(cands, key=lambda r: len(t & _tok(r["text"])))


def milestone_rows(st, ar, meanings, today=None):
    axes = st.get("axes") or {}
    out = []
    for m in st.get("milestones") or []:
        if not isinstance(m, dict):
            continue
        parts = []
        for p in ar.milestone_parts(m):
            pp = ar.part_pct(p)
            parts.append({"label": p.get("label", ""), "cur": p.get("current_num"),
                          "tgt": p.get("target_num"), "unit": p.get("unit", ""), "op": p.get("op", ">="),
                          "pct": None if pp is None else round(pp, 3), "reason": p.get("reason", "")})
        pct = ar.milestone_pct(m)
        shown = shown_status(m.get("status", "미판정"), pct, m.get("year"), today or kst_today())
        pg = m.get("progress") if isinstance(m.get("progress"), dict) else {}
        mean = attach_meaning(m, meanings) or {}
        out.append({
            # 축은 머리글자(A~E)로만 싣는다 — 'A_starlink' 같은 기계 키를 페이지 소스에도 남기지 않는다
            "id": m.get("id", ""), "axis": (m.get("axis") or "?")[:1].upper(),
            "axisKo": short_axis(axes, m.get("axis", "")),
            "year": m.get("year"), "text": m.get("statement", ""), "status": m.get("status", "미판정"),
            "shown": shown, "cls": "met" if shown == MET else STATUS_CLS.get(m.get("status"), "wait"),
            "pct": None if pct is None else round(pct, 3), "parts": parts,
            "cur": pg.get("current"), "tgt": pg.get("target"), "unit": pg.get("unit", ""),
            "asof": pg.get("asof", ""), "source": pg.get("source") or m.get("source", ""),
            "reason": m.get("progress_reason", ""),
            "hit": mean.get("hit", ""), "miss": mean.get("miss", ""), "method": mean.get("method", ""),
            "meaningSrc": mean.get("src", ""),
            "history": [{"run": h.get("run_id"), "status": h.get("status")}
                        for h in m.get("status_history") or [] if isinstance(h, dict)],
        })
    return out


def axis_rows(st, ms):
    axes = st.get("axes") or {}
    keys = list(axes.keys()) if isinstance(axes, dict) and axes else sorted({m["axis"] for m in ms})
    out = []
    for full in keys:
        k = full[:1].upper()
        mine = [m for m in ms if m["axis"] == k]
        nums = [m["pct"] for m in mine if m["pct"] is not None]
        counts = {s: sum(1 for m in mine if m["shown"] == s) for s in SHOWN}
        measured = [m for m in mine if m["pct"] is not None]
        # 축 전체가 '현재 충족' — 측정된 이정표가 전부 판정 연도 전 100% 일 때만(진행과 구분)
        all_met = bool(measured) and all(m["shown"] == MET for m in measured)
        out.append({"key": k, "ko": short_axis(axes, full), "n": len(mine), "measured": len(nums),
                    "avg": round(sum(nums) / len(nums), 3) if nums else None, "counts": counts,
                    "met": all_met})
    return out


def kpi_series(kpi_doc, st):
    """KPI 행 — 공시 재무(기계, spacex_kpi.json)는 기계 우선, 운영 KPI 는 탐사 값(state.kpi_history)."""
    qs = list(reversed(((kpi_doc or {}).get("auto") or {}).get("quarters") or []))    # 오래된 → 최신
    hist = {h.get("quarter"): h for h in st.get("kpi_history") or [] if isinstance(h, dict)}

    def qlabel(q):
        if q.get("quarter"):
            return q["quarter"]
        try:
            e = datetime.strptime(q["end"], "%Y-%m-%d")
            return f"{(e.month - 1) // 3 + 1}Q{str(e.year)[2:]}"
        except Exception:
            return q.get("end", "")
    cols = [qlabel(q) for q in qs]
    for h in sorted(hist.values(), key=lambda h: h.get("period_end", "")):
        if h.get("quarter") not in cols:
            cols.append(h["quarter"])
    cols = sorted(set(cols), key=lambda c: (int("20" + c[-2:]) if c[-2:].isdigit() else 0, c[0]))[-8:]
    by_q = {qlabel(q): q for q in qs}

    MACH = [("revenue", "매출", "revenue_usd_m", "usd"), ("op", "영업이익", "operating_income_usd_m", "usd"),
            ("ni", "순이익", "net_income_usd_m", "usd"), ("ocf", "영업현금흐름", "ocf_usd_m", "usd"),
            ("capex", "Capex", "capex_usd_m", "usd"), ("fcf", "분기 FCF", "fcf_usd_m", "usd"),
            ("cash", "현금", "cash_usd_m", "usd"), ("mkt", "유가증권", "marketable_securities_usd_m", "usd"),
            ("debt", "부채·리스", "debt_and_leases_usd_m", "usd")]
    OPS = [("subs", "가입자", "subscribers_m", "man"), ("arpu", "ARPU", "arpu_usd", "arpu"),
           ("gw", "명판 컴퓨트", "ai_nameplate_gw", "gw"), ("launch", "발사", "launches", "cnt"),
           ("conn", "Connectivity 매출", "rev_connectivity_usd_m", "usd"),
           ("si", "AI(SI) 매출", "rev_si_usd_m", "usd"), ("space", "Space 매출", "rev_space_usd_m", "usd")]
    rows = []
    for rid, label, key, kind in MACH:
        cells = []
        for c in cols:
            q = by_q.get(c) or {}
            v = q.get(key)
            src = (q.get("src") or {}).get(key) or {}
            h = hist.get(c) or {}
            if v is None and h.get(key) is not None and str(h.get("fin_source", "")).startswith("http"):
                # 기계가 못 만든 분기 — 탐사 엔진이 S-1 원문에서 기입하고 검증관이 대조한 값(관문 check_kpi_fin)
                v = h[key]
                cells.append({"v": v, "t": fmt_kind(v, kind), "mk": "S-1",
                              "src": {"origin": "S-1 원문(탐사 기입)", "url": h["fin_source"],
                                      "filed": h.get("published"),
                                      "how": (h.get("fin_note") or "S-1 원문 표") + " · 검증관 대조 대상"}})
                continue
            how = src.get("how") or src.get("note")
            derived = bool(v is not None and (src.get("derived") or str(how or "").startswith("누적")))
            cells.append({"v": v, "t": fmt_kind(v, kind), "mk": "계산" if derived else "",
                          "src": {"origin": "공시 자동", "form": src.get("form"), "filed": src.get("filed"),
                                  "concept": src.get("concept"), "how": how, "url": None}})
        rows.append({"id": rid, "label": label, "origin": "공시 자동", "cells": cells})
    for rid, label, key, kind in OPS:
        cells = []
        for c in cols:
            h = hist.get(c) or {}
            v = h.get(key)
            cells.append({"v": v, "t": fmt_kind(v, kind),
                          "src": {"origin": "탐사 취재", "url": h.get("source"), "filed": h.get("published"),
                                  "how": "탐사 엔진이 공시 KPI 표에서 옮김" if v is not None else
                                  ("이 분기 기록 없음" if h else "탐사 기록 없음")}})
        rows.append({"id": rid, "label": label, "origin": "탐사 취재", "cells": cells})
    # 병합 규칙 — 공시 재무가 비면 S-1 기입값(출처 URL·검증관 대조 필수)만 채우고 칸마다 'S-1' 표식을 단다.
    # 누적 공시의 차분으로 만든 분기 값은 '계산' 표식. 둘 다 없으면 '—'.
    return {"cols": cols, "rows": rows}


def fmt_kind(v, kind):
    if v is None:
        return "—"
    if kind == "usd":
        return usd_m_ko(v)
    if kind == "man":
        return man_ko(v)
    if kind == "arpu":
        return f"${v:,.0f}"
    if kind == "gw":
        return f"{v:g}GW"
    if kind == "cnt":
        return f"{v:,.0f}회"
    return str(v)


TILES = [("revenue", "매출"), ("subs", "가입자"), ("arpu", "ARPU"), ("si", "AI(SI) 매출"),
         ("gw", "명판 컴퓨트"), ("fcf", "분기 FCF")]


def kpi_tiles(fin):
    rows = {r["id"]: r for r in fin["rows"]}
    out = []
    for rid, label in TILES:
        r = rows.get(rid)
        if not r:
            continue
        pts = [(i, c["v"]) for i, c in enumerate(r["cells"]) if isinstance(c["v"], (int, float))]
        last = pts[-1] if pts else None
        prev = pts[-2] if len(pts) > 1 else None
        if last and prev:
            d = last[1] - prev[1]
            dirn = "up" if d > 0 else ("down" if d < 0 else "flat")
        else:
            dirn = "flat"
        out.append({"id": rid, "label": label, "value": r["cells"][last[0]]["t"] if last else "—",
                    "q": fin["cols"][last[0]] if last else "", "dir": dirn, "origin": r["origin"],
                    "series": [c["v"] if isinstance(c["v"], (int, float)) else None for c in r["cells"]]})
    return out


def run_info(st, watch, vres, today):
    run = st.get("run") or {}
    eng = ((watch or {}).get("engine") or {}).get("last_run") or {}
    try:
        from build_site import probe_schedule
        sch = probe_schedule(run.get("run_date"), st.get("triggers_next_12m"), today)
    except Exception:
        sch = {"next": "", "overdue_days": 0, "age_days": None, "stale": False, "trigger": None}
    checked = int((vres or {}).get("checked") or 0)
    ok = int((vres or {}).get("match") or 0) + int((vres or {}).get("rounding") or 0)
    return {"id": run.get("run_id", ""), "date": run.get("run_date", ""), "mode": run.get("mode", ""),
            "number": run.get("run_number"), "prev": run.get("previous_run_id"),
            "summary": [s for s in run.get("summary") or [] if isinstance(s, str)],
            "engine": RUN_KO.get(eng.get("conclusion") or "", "기록 없음") if eng else "기록 없음",
            "engineOk": eng.get("conclusion") == "success", "engineDate": (eng.get("created_at") or "")[:10],
            "verify": [ok, checked], "next": sch.get("next", ""), "overdue": sch.get("overdue_days", 0),
            "stale": sch.get("stale", False), "age": sch.get("age_days")}


def _plain(md):
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"", md or "")
    return re.sub(r"[*_`>#]+", "", s).strip()


def changelog_summary(ar, run_id, n=3, width=110):
    """변화 이력의 그 회차 절 → 앞 문단 n 개(굵은 머리말 포함, width 자로 자름). 요약이 기록 안 된 회차용."""
    body, _ = ar.sanitize(ar.read_changelog())
    for title, md in split_sections(body):
        if title.split(" ")[0] != run_id:
            continue
        paras = [_plain(p) for p in re.split(r"\n\s*\n", md) if _plain(p)]
        return [p if len(p) <= width else p[:width - 1].rstrip() + "…" for p in paras[:n]]
    return []


def delta_blocks(st, ar, n=3):
    """② 이번 회차에 바뀐 것 — 최신이 사건 회차면 [사건 회차] + [직전 월간 회차] 두 블록, 월간이면 하나.

    요약은 기록된 것만 쓴다: 현재 회차는 state.run.summary, 지난 회차는 run_history 줄의 summary,
    그것도 없으면 변화 이력(changelog)의 그 회차 절 앞 문단. 새로 지어내지 않는다.
    """
    run = st.get("run") or {}
    hist = {h.get("run_id"): h for h in st.get("run_history") or [] if isinstance(h, dict)}

    def summ(rid, own=None):
        s = [x for x in (own or (hist.get(rid) or {}).get("summary") or []) if isinstance(x, str)]
        return (s or changelog_summary(ar, rid, n))[:n]
    blocks = [{"id": run.get("run_id", ""), "kind": "사건 회차" if run.get("mode") == "event" else "월간 회차",
               "lines": summ(run.get("run_id"), run.get("summary"))}]
    if run.get("mode") == "event":
        prev = run.get("previous_monthly_run_id") or ""
        if not prev:      # 기록이 없으면 run_history 에서 사건 회차가 아닌 마지막 줄
            months = [h.get("run_id") for h in st.get("run_history") or []
                      if isinstance(h, dict) and re.fullmatch(r"\d{4}-\d{2}", str(h.get("run_id")))]
            prev = months[-1] if months else ""
        if prev:
            blocks.append({"id": prev, "kind": "직전 월간 회차", "lines": summ(prev)})
    return blocks


def trigger_rows(st, today):
    try:
        from spacex_watch import parse_trigger_date
        from build_site import _window_end
    except Exception:
        parse_trigger_date = _window_end = None
    out = []
    for t in st.get("triggers_next_12m") or []:
        if not isinstance(t, dict):
            continue
        d = parse_trigger_date(t.get("expected_date")) if parse_trigger_date else None
        we = _window_end(t.get("expected_date", "")) if _window_end else None
        if d is None:
            when, dday = "예상일 미정", None
        else:
            dday = (d - today).days
            if dday < 0 and we is not None and today <= we:
                when = f"구간 중 ~{we.month}/{we.day}"
            elif dday < 0:
                when = f"예상일 경과 {-dday}일"
            else:
                when = "D-day" if dday == 0 else f"D-{dday}"
        out.append({"id": t.get("id", ""), "name": t.get("name", ""), "expected": t.get("expected_date", ""),
                    "status": t.get("status", "대기"), "when": when, "dday": dday,
                    "sort": d.isoformat() if d else "9999", "check": t.get("check", ""),
                    "note": t.get("note", ""), "prev": t.get("prev", "")})
    out.sort(key=lambda r: (r["status"] != "대기", r["sort"]))
    return out


def event_rows(watch, essays_doc, today, triggers):
    out = []
    cut = (today - timedelta(days=90)).isoformat()
    try:
        from spacex_watch import trigger_kind
    except Exception:
        def trigger_kind(_):
            return None
    for e in (watch or {}).get("events") or []:
        if not isinstance(e, dict) or (e.get("date") or "") < cut:
            continue
        kind = e.get("kind") or "filing"
        rel_t = [t["id"] for t in triggers if trigger_kind(t["name"]) == kind]
        rel_ax = {"starship": ["B"], "earnings": ["A", "C", "D"],
                  "financing": ["D"], "contract": ["C", "D"]}.get(kind, [])
        out.append({"id": "ev-" + re.sub(r"[^0-9A-Za-z]", "", (e.get("accn") or e.get("id") or e.get("url") or ""))[-16:],
                    "date": e.get("date", ""), "kind": kind, "kindKo": EVENT_KIND_KO.get(kind, "공시"),
                    "title": e.get("title", ""), "url": e.get("url", ""), "source": e.get("source", ""),
                    "form": e.get("form", ""), "axes": rel_ax, "triggers": rel_t})
    for e in (essays_doc or {}).get("essays") or []:
        if not isinstance(e, dict) or e.get("company") != "spacex" or (e.get("date") or "") < cut:
            continue
        wd = e.get("status") == "withdrawn"
        # 철회 에세이는 원제목·사유를 싣지 않는다 — 사유에는 틀린 숫자가 인용돼 있고, 제목이
        # 페이지에 다시 나타나면 철회가 무효가 된다(관문 verify_pages 가 막는다). 정정 문구만.
        out.append({"id": "es-" + e.get("slug", "")[-10:], "date": e.get("date", ""),
                    "kind": "withdraw" if wd else "essay", "kindKo": "철회·정정" if wd else "동행 에세이",
                    "title": "동행 에세이 1편 철회 — 원문으로 확인되지 않은 숫자" if wd else e.get("title", ""),
                    "url": "journal.html#companion",
                    "source": "관측노트 동행 관측", "form": "에세이", "note": "",
                    "axes": [], "triggers": []})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def scenario_rows(st):
    sc = st.get("scenarios") or {}
    out = []
    for k, ko in (("bull", "강세"), ("base", "기본"), ("bear", "약세")):
        s = sc.get(k) or {}
        y = s.get("y2036") or {}
        out.append({"key": k, "ko": ko, "prob": s.get("probability"), "prev": s.get("probability_prev"),
                    "vps": s.get("value_per_share_usd"), "multiple": s.get("terminal_multiple"),
                    "rationale": s.get("rationale", ""),
                    "rev": y.get("revenue_usd_bn"), "op": y.get("op_income_usd_bn"),
                    "fcf": y.get("fcf_usd_bn"), "netDebt": y.get("net_debt_usd_bn")})
    return out


def rule_rows(st):
    out = []
    for r in st.get("probability_adjustment_rules") or []:
        if not isinstance(r, dict):
            continue
        d = []
        for k, ko in (("bull", "강세"), ("base", "기본"), ("bear", "약세")):
            if isinstance(r.get(k), (int, float)):
                d.append(f"{ko} {'+' if r[k] > 0 else '−'}{abs(r[k]) * 100:.0f}p")
        st_txt = str(r.get("status", "대기"))
        state = "발동" if st_txt.startswith("발동") else "대기"
        out.append({"id": r.get("id", ""), "cond": r.get("condition", ""), "delta": " · ".join(d),
                    "state": state, "status": st_txt})
    props = [{"cond": p.get("condition", ""), "reason": p.get("reason", ""), "on": p.get("proposed_on", "")}
             for p in st.get("rule_proposals_not_applied") or [] if isinstance(p, dict)]
    return out, props


# ── 보고서 · 변화 이력 · 출처 ──────────────────────────────────────

def split_sections(md):
    """## 머리글 기준으로 절을 나눈다 → [(제목, 본문 md)]. 첫 # 제목 앞부분은 '개요'."""
    out, title, buf = [], "개요", []
    for ln in (md or "").splitlines():
        m = re.match(r"^##\s+(.+)$", ln.strip())
        if m:
            if "".join(buf).strip():
                out.append((title, "\n".join(buf)))
            title, buf = m.group(1).strip(), []
            continue
        if ln.startswith("# "):
            continue
        buf.append(ln)
    if "".join(buf).strip():
        out.append((title, "\n".join(buf)))
    return out


def toc_label(title):
    t = re.sub(r"\s*\(.*?\)\s*", " ", title).strip()
    t = re.sub(r"\s+[—-]\s+.*$", "", t) if t.upper().startswith("PHASE") else t
    m = re.match(r"^(\d+)\.\s*(.+)$", t)
    return (m.group(1) + " " + m.group(2)) if m else t


def report_bundle(ar, axes):
    """보고서별 절 HTML(준법 필터 통과본) — PROBE_DATA 와 별도 덩어리."""
    from build_site import report_title, ko_axes
    reps = []
    for f in ar.report_files():
        body, info = ar.sanitize(f.read_text(encoding="utf-8"))
        secs = []
        for i, (title, md) in enumerate(split_sections(body)):
            sid = f"s{i}"
            secs.append({"id": sid, "title": ko_axes(title, axes), "toc": ko_axes(toc_label(title), axes),
                         "html": ar.md_to_html(ko_axes(md, axes))})
        reps.append({"id": f.stem, "title": report_title(f), "phase4Cut": info["phase4"],
                     "blocksCut": info["blocks"], "sections": secs})
    return reps


def changelog_runs(ar, axes):
    from build_site import ko_axes
    body, _ = ar.sanitize(ar.read_changelog())
    runs = []
    for title, md in split_sections(body):
        if title == "개요":
            continue
        rid = title.split(" ")[0]
        runs.append({"id": rid, "title": ko_axes(title, axes), "html": ar.md_to_html(ko_axes(md, axes))})
    runs.sort(key=lambda r: r["id"], reverse=True)
    return runs


_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_DATE_IN = re.compile(r"(20\d{2})[-.](\d{1,2})(?:[-.](\d{1,2}))?")


def source_rows(reports_md, st, events):
    """URL 전수 중복 제거 — 도메인·문서명·날짜·그 출처를 쓴 주장 수·주장 목록."""
    srcs = {}

    def add(url, name, where, claim):
        url = url.rstrip(".,")
        s = srcs.setdefault(url, {"url": url, "domain": urlparse(url).netloc.replace("www.", ""),
                                  "name": name or "", "date": "", "claims": []})
        if name and not s["name"]:
            s["name"] = name
        if not s["date"]:
            m = _DATE_IN.search(name or "") or _DATE_IN.search(claim or "")
            if m:
                s["date"] = f"{m.group(1)}-{int(m.group(2)):02d}" + (f"-{int(m.group(3)):02d}" if m.group(3) else "")
        if len(s["claims"]) < 40:
            s["claims"].append({"where": where, "text": re.sub(r"\s+", " ", _LINK.sub(r"\1", claim or ""))[:160]})
    for rid, md in reports_md:
        sec = "개요"
        for ln in md.splitlines():
            m = re.match(r"^##\s+(.+)$", ln.strip())
            if m:
                sec = toc_label(m.group(1))
                continue
            for name, url in _LINK.findall(ln):
                add(url, name, f"보고서 {rid} · {sec}", ln.strip().lstrip("|-> "))
    for m in st.get("milestones") or []:
        pg = m.get("progress") if isinstance(m.get("progress"), dict) else {}
        u = pg.get("source", "")
        if isinstance(u, str) and u.startswith("http"):
            add(u, "", f"이정표 {m.get('id')}", m.get("statement", ""))
    for h in st.get("kpi_history") or []:
        if isinstance(h, dict) and str(h.get("source", "")).startswith("http"):
            add(h["source"], "", f"운영 KPI {h.get('quarter')}", f"{h.get('quarter')} 가입자·ARPU·명판·발사·부문 매출")
    for e in events:
        if str(e.get("url", "")).startswith("http"):
            add(e["url"], e.get("title", ""), f"사건 {e.get('date')}", e.get("title", ""))
    rows = sorted(srcs.values(), key=lambda s: (-len(s["claims"]), s["domain"]))
    for i, s in enumerate(rows):
        s["id"] = f"src{i}"
        s["n"] = len(s["claims"])
    return rows


def search_index(ms, events, reports, changelog, sources, triggers):
    """검색 색인 [{kind, id, title, text(앞 300자), anchor}] — 빌드 시점에 만든다."""
    def plain(h):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or "")).strip()
    idx = []
    for m in ms:
        idx.append({"kind": "이정표", "id": m["id"], "title": f"{m['id']} {m['text']}",
                    "text": f"{m['axisKo']} · {m['status']} · {m.get('cur') or ''} → {m.get('tgt') or ''}"[:300],
                    "anchor": f"milestones/{m['id']}"})
    for e in events:
        idx.append({"kind": "사건", "id": e["id"], "title": e["title"],
                    "text": f"{e['date']} · {e['kindKo']} · {e['source']} {e.get('note', '')}"[:300],
                    "anchor": f"events/{e['id']}"})
    for t in triggers:
        idx.append({"kind": "트리거", "id": t["id"], "title": f"{t['id']} {t['name']}",
                    "text": f"{t['expected']} {t['check']} {t['note']}"[:300], "anchor": f"triggers/{t['id']}"})
    for r in reports:
        for s in r["sections"]:
            idx.append({"kind": "보고서", "id": f"{r['id']}:{s['id']}", "title": f"{r['id']} · {s['toc']}",
                        "text": plain(s["html"])[:300], "anchor": f"report/{r['id']}:{s['id']}",
                        "full": plain(s["html"])[:4000]})
    for c in changelog:
        idx.append({"kind": "변화 이력", "id": c["id"], "title": c["title"], "text": plain(c["html"])[:300],
                    "anchor": f"changelog/{c['id']}", "full": plain(c["html"])[:2000]})
    for s in sources:
        idx.append({"kind": "출처", "id": s["id"], "title": f"{s['domain']} {s['name']}"[:160],
                    "text": f"{s['url']} · 주장 {s['n']}건"[:300], "anchor": f"sources/{s['id']}"})
    return idx


def build(ar, today=None, watch=None, status=None, vres=None, kpi=None, essays=None):
    """→ (PROBE_DATA, probe_reports). 판단층은 읽기만 한다."""
    st = ar.read_state() or {}
    today = today or kst_today()
    watch = read_json(DATA_DIR / "spacex_watch.json", {}) if watch is None else watch
    status = read_json(DATA_DIR / "fetch_status.json", {}) if status is None else status
    vres = read_json(AR_DIR / "state" / "verify_result.json", {}) if vres is None else vres
    kpi = read_json(DATA_DIR / "spacex_kpi.json", {}) if kpi is None else kpi
    essays = read_json(DATA_DIR / "essays.json", {}) if essays is None else essays
    axes = st.get("axes") or {}
    files = ar.report_files()
    meanings = meanings_from_reports(sorted(files, key=lambda f: f.stem), [k[:1].upper() for k in axes.keys()])
    ms = milestone_rows(st, ar, meanings, today)
    triggers = trigger_rows(st, today)
    events = event_rows(watch, essays, today, triggers)
    fin = kpi_series(kpi, st)
    reports = report_bundle(ar, axes)
    changelog = changelog_runs(ar, axes)
    reports_md = [(f.stem, ar.sanitize(f.read_text(encoding="utf-8"))[0]) for f in files]
    sources = source_rows(reports_md, st, events)
    rules, props = rule_rows(st)
    val = st.get("valuation") or {}
    mkt = st.get("market") or {}
    srcs_state = [{"name": r.get("source", "").split(":", 1)[-1], "state": SRC_STATE_KO.get(r.get("outcome"), "미기록")}
                  for r in ((status or {}).get("sources") or {}).values()
                  if isinstance(r, dict) and r.get("kind") == "companion"
                  and str(r.get("source", "")).startswith("spacex:")]
    data = {
        "v": 2, "today": today.isoformat(),
        "run": run_info(st, watch, vres, today),
        "delta": delta_blocks(st, ar),
        "price": {"v": mkt.get("price_usd"), "date": mkt.get("price_asof", ""), "src": mkt.get("price_source", "")},
        "band": {"lo": (val.get("fair_value_band_usd") or [None, None])[0],
                 "hi": (val.get("fair_value_band_usd") or [None, None])[-1],
                 "mid": val.get("fair_value_midpoint_usd"), "pw": val.get("probability_weighted_value_usd"),
                 "rate": val.get("discount_rate"), "rateBasis": val.get("discount_rate_basis", ""),
                 "multiple": val.get("multiple_crosscheck_basis", "")},
        "scen": scenario_rows(st), "rules": rules, "proposals": props,
        "history": [h for h in st.get("run_history") or [] if isinstance(h, dict)],
        "axes": axis_rows(st, ms), "milestones": ms, "triggers": triggers, "events": events,
        "fin": fin, "tiles": kpi_tiles(fin), "changelog": changelog, "sources": sources,
        "feeds": srcs_state, "panels": [{"id": p, "ko": ko} for p, ko in PANELS],
    }
    data["search"] = search_index(ms, events, reports, changelog, sources, triggers)
    return data, reports


# ────────────────────────────────────────────────────────────────
# 관제판(첫 화면) 정적 HTML
# ────────────────────────────────────────────────────────────────

DISCLAIMER = "기계 생성 가정 · 투자 권유 아님 · 목표가 아님"
SCEN_GAP = 110       # 게이지 눈금 글자를 한 줄로 묶는 간격(viewBox 단위 — 모바일 글자 14 기준 라벨 폭 ≈ 80~100)


def label_w(s, em=14):
    """게이지 글자 폭 어림(viewBox 단위) — 가장 큰 글자(모바일 14) 기준. 한글 1em, 그 밖 0.6em."""
    return sum(em if ord(c) > 0x3000 else em * 0.6 for c in s)


def gauge_svg(d):
    """① 가격 vs 가정 가치 밴드 — 인라인 SVG. '목표가'·'상승여력' 표현 없음."""
    p, b = d["price"].get("v"), d["band"]
    vals = [x for x in (p, b.get("hi"), b.get("lo")) + tuple(s.get("vps") for s in d["scen"])
            if isinstance(x, (int, float))]
    top = max(vals + [100]) * 1.15
    top = int((top + 49) // 50 * 50)
    W, X0, X1 = 520, 14, 506

    def x(v):
        return X0 + (X1 - X0) * max(0, min(1, v / top))
    o = [f'<svg class="pc-gauge" viewBox="0 0 {W} 90" role="img" aria-label="가격과 가정 가치 밴드">']
    o.append(f'<line x1="{X0}" y1="40" x2="{X1}" y2="40" class="pc-g-axis"/>')
    if isinstance(b.get("lo"), (int, float)) and isinstance(b.get("hi"), (int, float)):
        o.append(f'<rect x="{x(b["lo"]):.1f}" y="30" width="{x(b["hi"]) - x(b["lo"]):.1f}" height="20" rx="4" class="pc-g-band"/>')
        o.append(f'<text x="{x(b["lo"]):.1f}" y="24" class="pc-g-lbl" text-anchor="middle">${b["lo"]:g}</text>')
        o.append(f'<text x="{x(b["hi"]):.1f}" y="24" class="pc-g-lbl" text-anchor="middle">${b["hi"]:g}</text>')
    if isinstance(b.get("mid"), (int, float)):
        o.append(f'<line x1="{x(b["mid"]):.1f}" y1="28" x2="{x(b["mid"]):.1f}" y2="52" class="pc-g-mid"/>')
        o.append(f'<text x="{x(b["mid"]):.1f}" y="24" class="pc-g-lbl pc-g-strong" text-anchor="middle">${b["mid"]:g}</text>')
    # 가까운 시나리오 눈금은 눈금은 따로, 글자는 한 줄로 묶는다(2026-10-23 F2). 줄 엇갈림은 모바일에서
    # 글자 크기(14)가 줄 간격(11)보다 커 '약세 $3.2'·'기본 $24' 가 여전히 겹쳤다(390px 실측).
    groups = []
    for s in sorted([s for s in d["scen"] if isinstance(s.get("vps"), (int, float))], key=lambda s: s["vps"]):
        xv = x(s["vps"])
        if groups and xv - groups[-1][0][0] < SCEN_GAP:
            groups[-1].append((xv, s))
        else:
            groups.append([(xv, s)])
    right = [X0 - 10, X0 - 10]          # 줄(0·1)마다 이미 쓴 글자의 오른쪽 끝
    for g in groups:
        x0 = g[0][0]
        label = " · ".join(f'{s["ko"]} ${s["vps"]:g}' for _, s in g)
        w = label_w(label)
        start = x0 < 48 or len(g) > 1                         # 왼쪽 끝·묶음은 첫 눈금에서 왼쪽 정렬
        lx = max(x0 - 4, X0) if start else x0
        left = lx if start else lx - w / 2
        row = 0 if left > right[0] + 6 else 1                 # 앞 글자와 닿으면 둘째 줄로
        right[row] = left + w
        for xv, _ in g:
            o.append(f'<line x1="{xv:.1f}" y1="52" x2="{xv:.1f}" y2="{58 + row * 14}" class="pc-g-tick"/>')
        o.append(f'<text x="{lx:.1f}" y="{70 + row * 14}" class="pc-g-sm" '
                 f'text-anchor="{"start" if start else "middle"}">{esc(label)}</text>')
    if isinstance(p, (int, float)):
        o.append(f'<circle cx="{x(p):.1f}" cy="40" r="7" class="pc-g-price"/>')
        o.append(f'<text x="{min(x(p), X1 - 30):.1f}" y="86" class="pc-g-lbl pc-g-strong" text-anchor="middle">주가 ${p:g}</text>')
    o.append(f'<text x="{X0}" y="50" class="pc-g-sm" text-anchor="start" dy="-12">$0</text>')
    o.append(f'<text x="{X1}" y="60" class="pc-g-sm" text-anchor="end">${top}</text>')
    o.append(f'<text x="{X1}" y="10" class="pc-mark" text-anchor="end">{MARK}</text>')
    o.append("</svg>")
    return "".join(o)


def prob_bar(d):
    hist = d.get("history") or []
    base = None
    run_id = d["run"]["id"]
    monthly = [h for h in hist if "event" not in str(h.get("run_id", ""))]
    if len(monthly) >= 2:
        base = monthly[-2]
    cells = []
    for s in d["scen"]:
        pr = s.get("prob")
        if not isinstance(pr, (int, float)):
            continue
        dlt = ""
        if base and isinstance((base.get("probs") or {}).get(s["key"]), (int, float)):
            dd = round((pr - base["probs"][s["key"]]) * 100)
            if dd:
                dlt = f'<i class="pc-{"up" if dd > 0 else "down"}">{"▲" if dd > 0 else "▼"}{abs(dd)}</i>'
        cells.append(f'<span class="pc-pb pc-pb-{s["key"]}" style="flex:{pr:.3f}">'
                     f'{esc(s["ko"])} {pr * 100:.0f}{dlt}</span>')
    lbl = f'{esc(base["run_id"])} 대비' if base else ""
    return f'<div class="pc-probs" aria-label="시나리오 확률">{"".join(cells)}</div><div class="pc-sub">확률 · {lbl}</div>'


def spark(series, w=84, h=14):
    pts = [(i, v) for i, v in enumerate(series) if isinstance(v, (int, float))]
    if len(pts) < 2:
        return f'<svg class="pc-spark" viewBox="0 0 {w} {h}" aria-hidden="true"></svg>'
    vs = [v for _, v in pts]
    lo, hi = min(vs), max(vs)
    n = max(1, len(series) - 1)
    path = " ".join(f'{"M" if k == 0 else "L"}{i / n * (w - 4) + 2:.1f},{h - 3 - (0 if hi == lo else (v - lo) / (hi - lo)) * (h - 6):.1f}'
                    for k, (i, v) in enumerate(pts))
    return f'<svg class="pc-spark" viewBox="0 0 {w} {h}" aria-hidden="true"><path d="{path}"/></svg>'


ARROW = {"up": "↗", "down": "↘", "flat": "→"}


def cockpit_html(d):
    r = d["run"]
    tr = (d["triggers"] or [{}])[0] if d["triggers"] else None
    eng_cls = "ok" if r["engineOk"] and not r["overdue"] and not r["stale"] else "warn"
    if r["overdue"] > 0:
        eng_txt = f"예정일 경과 {r['overdue']}일 — 엔진 점검 중"
    elif r["stale"]:
        eng_txt = "관측 공백이 길어지고 있습니다 — 엔진 점검 중"
    else:
        eng_txt = f"엔진 {r['engine']}"
    v_ok, v_n = r["verify"]
    trig = (f' · 다음 트리거 <b>{esc(short_name(tr["name"]))}</b> ({esc(tr["when"])})' if tr else "")
    h = [f'<div class="pc" id="pcRoot" data-pc-run="{esc(r["id"])}">']
    h.append(f'<div class="pc-note">{DISCLAIMER}</div>')
    h.append(f'<div class="pc-status"><span class="pc-dot pc-dot-{eng_cls}" aria-hidden="true"></span>'
             f'<span class="pc-status-t"><b>{esc(r["id"])}</b> 회차 · 관측 {mmdd(r["date"])} · {esc(eng_txt)}'
             f' · 검증 {v_ok}/{v_n} · 다음 회차 {mmdd(r["next"])}{trig}</span>'
             f'<button class="pc-search-btn" id="pcSearchBtn" type="button" aria-label="검색(단축키 /)">'
             f'<span>검색</span><kbd>/</kbd></button></div>')
    h.append('<div class="pc-search" id="pcSearch" hidden><input id="pcQ" type="search" autocomplete="off" '
             'placeholder="보고서·이정표·사건·변화 이력·출처 검색" aria-label="탐사선 검색">'
             '<div class="pc-results" id="pcResults" role="listbox"></div></div>')
    # ① ②
    b = d["band"]
    band_txt = (f'가정 가치 밴드 ${b["lo"]:g}–${b["hi"]:g} · 중앙 ${b["mid"]:g}'
                if all(isinstance(b.get(k), (int, float)) for k in ("lo", "hi", "mid")) else "가정 가치 밴드 —")
    price = d["price"]
    h.append('<div class="pc-grid">')
    h.append('<section class="pc-card pc-c1" aria-labelledby="pcH1"><h3 id="pcH1">① 가격 vs 가정 가치</h3>'
             + gauge_svg(d) + f'<div class="pc-sub">{esc(band_txt)} · 주가 ${price.get("v")} ({mmdd(price.get("date"))})</div>'
             + prob_bar(d) + '</section>')
    blocks = []
    for bl in d.get("delta") or [{"id": r["id"], "kind": "", "lines": r["summary"]}]:
        items = "".join(f'<li>{esc(s)}</li>' for s in bl["lines"][:3]) or "<li>요약 없음</li>"
        blocks.append(f'<div class="pc-dblk"><div class="pc-dlbl"><b>{esc(bl["id"])}</b> {esc(bl["kind"])}</div>'
                      f'<ul class="pc-delta">{items}</ul></div>')
    h.append(f'<section class="pc-card pc-c2{" pc-c2-two" if len(blocks) > 1 else ""}" aria-labelledby="pcH2">'
             f'<h3 id="pcH2">② 이번 회차에 바뀐 것'
             f'<button class="pc-more pc-more-h" type="button" data-pc-go="changelog">전체 델타 →</button></h3>'
             f'{"".join(blocks)}</section>')
    # ③ ④
    rows = []
    for a in d["axes"]:
        pct = a["avg"]
        c = a["counts"]
        cnt = " · ".join(f"{'현재 충족' if k == MET else k} {c[k]}" for k in SHOWN
                         if c.get(k) and not (a.get("met") and k == MET))
        if a.get("met"):
            cnt = MET + (" · " + cnt if cnt else "")
        rows.append(f'<button class="pc-axis" type="button" data-pc-go="milestones" data-pc-axis="{esc(a["key"])}">'
                    f'<span class="pc-axis-n">{esc(a["ko"])}</span>'
                    f'<span class="pc-bar"><i style="width:{0 if pct is None else round(pct * 100)}%"></i></span>'
                    f'<span class="pc-axis-c">{"—" if pct is None else str(round(pct * 100)) + "%"}'
                    f'(측정 {a["measured"]}/{a["n"]}) · {esc(cnt)}</span></button>')
    h.append('<section class="pc-card pc-c3" aria-labelledby="pcH3"><h3 id="pcH3">③ 5축 진척'
             '<span class="pc-tag">측정된 이정표 평균</span></h3>' + "".join(rows) + '</section>')
    tiles = []
    for t in d["tiles"]:
        tiles.append(f'<button class="pc-tile" type="button" data-pc-go="fin" data-pc-row="{esc(t["id"])}">'
                     f'<span class="pc-tile-l">{esc(t["label"])} <em>{esc(t["q"])}</em></span>'
                     f'<span class="pc-tile-v">{esc(t["value"])} <i class="pc-{t["dir"]}">{ARROW[t["dir"]]}</i></span>'
                     f'{spark(t["series"])}<span class="pc-tile-o">{"공시" if t["origin"] == "공시 자동" else "취재"}</span></button>')
    h.append('<section class="pc-card pc-c4" aria-labelledby="pcH4"><h3 id="pcH4">④ KPI'
             f'<span class="pc-tag">분기 · 추세</span></h3><div class="pc-tiles">{"".join(tiles)}</div>'
             f'<div class="pc-mark-row">{MARK}</div></section>')
    # ⑤
    trs = []
    for t in (d["triggers"] or [])[:3]:
        trs.append(f'<button class="pc-trig" type="button" data-pc-open="triggers" data-pc-id="{esc(t["id"])}">'
                   f'<span class="pc-trig-d">{esc(t["when"])}</span>'
                   f'<span class="pc-trig-n"><b>{esc(t["id"])}</b> {esc(t["name"])}</span>'
                   f'<span class="pc-trig-e">{esc(t["expected"])}</span></button>')
    h.append('<section class="pc-card pc-c5" aria-labelledby="pcH5"><h3 id="pcH5">⑤ 다음에 볼 것'
             f'<span class="pc-tag">트리거 · 예상일 근거</span></h3><div class="pc-trigs">{"".join(trs)}</div></section>')
    h.append('</div>')
    # 패널 · 서랍
    tabs = "".join(f'<button class="pc-seg" type="button" role="tab" data-pc-panel="{p["id"]}" aria-selected="false">'
                   f'<kbd>{i + 1}</kbd>{esc(p["ko"])}</button>' for i, p in enumerate(d["panels"]))
    h.append(f'<div class="pc-segs" role="tablist" aria-label="근거 패널">{tabs}</div>')
    h.append('<div class="pc-panel" id="pcPanel" role="tabpanel"></div>')
    h.append('<aside class="pc-drawer" id="pcDrawer" aria-hidden="true" tabindex="-1">'
             '<div class="pc-drawer-h"><button type="button" class="pc-nav" data-pc-step="-1" aria-label="이전 항목">←</button>'
             '<button type="button" class="pc-nav" data-pc-step="1" aria-label="다음 항목">→</button>'
             '<span class="pc-drawer-t" id="pcDrawerT"></span>'
             '<button type="button" class="pc-x" id="pcDrawerX" aria-label="닫기(Esc)">✕</button></div>'
             '<div class="pc-drawer-b" id="pcDrawerB"></div></aside>')
    h.append('<div class="pc-scrim" id="pcScrim" hidden></div>')
    h.append('</div>')
    return "".join(h)


def short_name(name):
    """트리거 이름의 앞부분 — 'Starship Flight 15 — …' → 'Starship Flight 15'."""
    return re.split(r"\s+[—–-]\s+|\s*·\s*|\(", name or "")[0].strip()[:40]


def render(ar, today=None, **kw):
    """→ (관제판 HTML, PROBE_DATA, 보고서 덩어리)."""
    data, reports = build(ar, today=today, **kw)
    return cockpit_html(data), data, reports
