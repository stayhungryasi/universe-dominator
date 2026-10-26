"""
build_site.py — 멀티페이지 사이트 빌드

생성 페이지 목록은 ALL_PAGES 하나가 유일한 진실이다 (아래 참조).
새 페이지를 추가할 때 손대야 하는 곳도 ALL_PAGES 한 곳뿐이며,
selftest.py 가 이 단일화를 매 실행마다 검증한다.
"""
import html as html_mod
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import i18n

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SCRIPTS_DIR = HERE / "scripts"

# ── 산출물이 놓이는 곳 — 빌드 중에는 **스테이징 디렉터리**를 가리킨다 ──────────
# 2026-09-05 사고: journal.html 쓰기에서 OSError 로 빌드가 죽었는데, 그때는 이미
# 12페이지 중 6개가 실제 경로에 쓰인 뒤였다. 그런데 주입 5종(팔레트·카운터·nav·
# 푸터·헤더)은 **모든 페이지 생성이 끝난 뒤에** 돌기 때문에, 남은 것은 "HTML 은
# 멀쩡하고 페이지도 열리는데 공용 스타일만 없는" 산출물이었다. 산출물 검사는
# 주입 뒤에 있어 아예 실행되지 않았다 — 검사를 늘려도 못 잡는 자리다.
#
# 그래서 검사를 늘리는 대신 **실제 경로를 건드리는 시점을 옮긴다.** 빌드는 전부
# 스테이징에서 하고, 검사까지 통과한 뒤에 os.replace 로 한 파일씩 갈아끼운다.
# 어디서 죽든 실제 경로에는 직전 완성본이 그대로 남는다.
OUT_DIR = HERE
# 지금 빌드 중인 언어 — _build_all 이 i18n.LANGS 를 돌며 바꾼다. 기본(ko)이면 지금 화면 그대로
LANG = i18n.DEFAULT


def _w(path, text):
    """산출물 쓰기 — 윈도우 로컬에서 방금 쓴 파일을 다른 프로세스(색인·백신)가 잠깐 물어
    errno 22 로 죽는 일이 잦다(CI 리눅스는 무관). 짧게 세 번 다시 시도하고, 그래도면 그대로 던진다
    (원자적 빌드 검사는 끝내 실패하는 쓰기를 그대로 재현한다)."""
    for i in range(3):
        try:
            return path.write_text(text, encoding="utf-8")
        except OSError:
            if i == 2:
                raise
            time.sleep(0.4 * (i + 1))

# ── 전 페이지 목록 — 단일 진실 공급원 (Single Source of Truth) ──────────────
# 2026-08 관측노트 누락 사고: 이 목록이 fix_nav / inject_footer_links /
# inject_presence / inject_header_fix / inject_aurora_tokens 5곳에 흩어져 있어
# 새 페이지가 일부 주입에서만 빠지는 사고가 반복됐다 (policies.html 도 로고·
# 접속자 카운터가 누락된 채였다). 이제 전 주입 함수가 이 상수 하나만 읽는다.
#
# 새 페이지 추가 절차: ① build_xxx() 작성 ② main() 에 호출 추가
#                     ③ 여기 ALL_PAGES 에 파일명 추가 — 끝.
# (③ 을 빠뜨리면 selftest.py 의 "페이지 목록 동기화" 검사가 즉시 실패한다)
ALL_PAGES = (
    "index.html",           # 우주지배자 (메인 · TOP 20)
    "latent.html",          # 잠재지배자
    "megatrend.html",       # 메가트렌드
    "research.html",        # 리서치
    "community.html",       # 관제센터
    "observatory.html",     # 데이터 천문대 (3D)
    "journal.html",         # 관측노트
    "pioneers.html",        # 개척자
    "history-top20.html",   # 우주지배자 변동 이력
    "history-latent.html",  # 잠재지배자 변동 이력
    "about.html",           # 사이트 소개
    "policies.html",        # 약관·개인정보·면책
)

# 빌드가 만들지만 주입 대상이 아닌 파일 (레이아웃 없는 스텁 등).
# 루트에 HTML을 새로 두면서 주입은 원치 않을 때만 여기에 등록한다.
UNMANAGED_PAGES = (
    "my-universe.html",     # observatory.html 로 보내는 리다이렉트 스텁
)


def _col_seed(s):
    """observatory-template.html 의 colSeed()와 동일한 FNV-1a 32비트 해시.

    JS 원본:
      let h = 2166136261;
      for (const ch of String(str||'')) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619) >>> 0; }
      return h >>> 0;
    JS는 코드포인트 단위로 순회하고(이모지 안전) Math.imul + >>>0 이 32비트 랩어라운드다.
    파이썬 문자열도 코드포인트 단위 순회이므로 & 0xFFFFFFFF 로 동일 결과를 얻는다.
    ⚠️ 이 함수와 JS colSeed 는 한 몸이다 — 한쪽만 바꾸면 칼럼 앵커가 어긋난다.
    """
    h = 2166136261
    for ch in str(s or ""):
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _col_slug(col):
    """observatory 의 colSlug(c) = 'log-' + colSeed(title + date) 와 동일"""
    return "log-" + str(_col_seed((col.get("title") or "") + (col.get("date") or "")))


def _header_meta():
    """헤더 배지용 날짜·환율 (latest.json meta 기준)"""
    try:
        m = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
        return {"fetched_date": m.get("fetched_date"), "usd_krw": m.get("usd_krw"),
                "macro": m.get("macro") or {}}
    except Exception:
        return {}


def _prev_snapshot_map(data):
    """오늘 이전 가장 최근 스냅샷 → {region: {TICKER: {r, mc}}} (없으면 빈 맵 — 우아한 저하)"""
    try:
        cur_date = (data.get("meta", {}).get("fetched_date") or "")[:10]
        snaps = sorted((DATA_DIR / "snapshots").glob("????-??-??.json"))
        prev_file = None
        for f in snaps:
            if not cur_date or f.stem < cur_date:
                prev_file = f
        if prev_file is None:
            return {}
        sd = json.loads(prev_file.read_text(encoding="utf-8"))
        out = {}
        for rname, region in (sd.get("regions") or {}).items():
            stocks = region.get("stocks", region) if isinstance(region, dict) else region
            m = {}
            for i, s in enumerate(stocks or []):
                tk = (s.get("ticker") or "").upper().strip()
                if tk:
                    m[tk] = {"r": s.get("rank") or (i + 1), "mc": s.get("mc") or 0}
            out[rname] = m
        return out
    except Exception as e:
        print(f"[warn] 전일 스냅샷 대조 실패(무시): {e}")
        return {}


def build_main():
    """index.html — 우주지배자 메인 (TOP 20)"""
    data = localize_latest(load_data("latest.json"))
    template = (SCRIPTS_DIR / "template.html").read_text(encoding="utf-8")
    
    data_json = json.dumps(data, ensure_ascii=False, indent=2)
    meta = data.get("meta", {})
    fetched_date = meta.get("fetched_date", "—")
    fetched_label = fetched_date.replace("-", ".")
    
    earth_stocks = data.get("regions", {}).get("earth", {}).get("stocks", [])
    top1_mc = earth_stocks[0]["mc"] if earth_stocks else 0
    top1_name = earth_stocks[0]["name"] if earth_stocks else "—"
    trillion_count = sum(1 for s in earth_stocks if s["mc"] >= 1000)
    top20_sum = sum(s["mc"] for s in earth_stocks)
    
    html = template
    html = html.replace("{{DATA_JSON}}", data_json)
    html = html.replace("{{PREV_JSON}}", json.dumps(_prev_snapshot_map(data), ensure_ascii=False))
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{TOP1_NAME}}", top1_name)
    html = html.replace("{{TOP1_MC}}", i18n.mc(LANG, top1_mc))
    html = html.replace("{{TRILLION_COUNT}}", str(trillion_count))
    html = html.replace("{{TOP20_SUM}}", i18n.mc1(LANG, top20_sum))

    # 오늘의 신호 3건 (ud-aurora-v1) — 필독 우선, 이후 추천수순 · 파일 없으면 우아한 저하
    try:
        sig_all = load_data("signals.json", {}).get("signals", [])
        # 최신 수신순 3건 (RFC822/ISO 혼재 파싱 · 같은 시각이면 필독→추천수)
        from email.utils import parsedate_to_datetime as _p822
        from datetime import datetime as _dt
        def _sig_ts(x):
            for v in (x.get("pub"), x.get("captured")):
                if not v: continue
                v = str(v)
                try: return _p822(v).timestamp()
                except Exception: pass
                try: return _dt.fromisoformat(v.replace("Z", "+00:00")).timestamp()
                except Exception: pass
            return 0.0
        sig_top = sorted(sig_all, key=lambda x: (_sig_ts(x), bool(x.get("pin")), x.get("points") or 0),
                         reverse=True)[:3]
        sig_top = [localize_signal({k: x.get(k) for k in ("title", "ko", "url", "pub", "points", "pin")},
                                   _sig_ts(x)) for x in sig_top]
    except Exception as e:
        print(f"[warn] index 신호 주입 실패(무시): {e}")
        sig_top = []
    html = html.replace("{{SIGNALS_JSON}}", json.dumps(sig_top, ensure_ascii=False))

    out = OUT_DIR / "index.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


# ── 데이터 현지화 — 빌드 시점에 그 언어로 심는다(화면은 받은 것을 그릴 뿐) ──────────
def load_data(name, default=None):
    """data/<name> → 문서. 다른 언어 빌드에서는 **번역 캐시**(단계 3)를 끼운 사본을 준다.

    번역은 관문(숫자·용어·준법·마크업·링크)을 통과하고 원문 해시가 지금 원문과 같은 것만 들어간다
    (i18n_content.apply). 없으면 원문 그대로 — 화면이 TX/TXB 로 '번역 준비 중' 표식을 단다.
    콘텐츠 파일은 반드시 이 함수로 읽는다: 읽는 곳마다 따로 끼우면 페이지마다 번역이 갈라진다.
    """
    p = DATA_DIR / name
    if not p.exists():
        return default
    doc = json.loads(p.read_text(encoding="utf-8"))
    if LANG != i18n.DEFAULT:
        import i18n_content as IC
        for kind, spec in IC.SPECS.items():
            if spec["file"] == name:
                doc, _ = IC.apply(kind, LANG, doc)
    return doc


def localize_latest(data):
    """latest.json 사본 → 지역 라벨(카탈로그) · 한국어 종목명(이름 사전). 숫자는 손대지 않는다(G2)."""
    data = json.loads(json.dumps(data))
    for key, reg in (data.get("regions") or {}).items():
        if isinstance(reg, dict):
            reg["label"] = i18n.t(LANG, "region." + key) if ("region." + key) in i18n.catalog(i18n.DEFAULT) \
                else reg.get("label")
            for s in reg.get("stocks") or []:
                if isinstance(s, dict):
                    s["name"] = i18n.stock_name(LANG, s.get("ticker"), s.get("name"))
    for x in data.get("latent") or []:
        if isinstance(x, dict):
            x["name"] = i18n.stock_name(LANG, x.get("ticker"), x.get("name"))
    return data


def localize_signal(x, ts):
    """오늘의 신호 카드 — ko 는 한국어 제목, en 은 **영어 원제**(한국어를 거치지 않는다),
    es·ja 는 번역 캐시가 있으면 그 언어, 없으면 영어 원제. 날짜는 ISO 로 함께 싣는다."""
    from datetime import datetime, timezone
    x = dict(x)
    if ts:
        x["pub_iso"] = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
    if LANG != i18n.DEFAULT:
        x["ko"] = x.get("title") or x.get("ko")
    return x


# ── 단계 2 데이터 현지화(빌드 시점) — 상태에서 그 언어로 만든다. 숫자는 손대지 않는다 ─────────
# 신호 분류(수집기 분류값) → id. 화면 이름·색은 id 에서(카탈로그 sig.tag.*). 모든 언어(ko 포함)에 붙인다
SIG_TAG_ID = {"에세이": "essay", "모델 발표": "model", "연구": "research", "정책·규제": "policy",
              "투자·산업": "invest", "기타": "other"}
# 동행 에세이 판정·출처 — 원장의 한국어 판정값 → id(화면 코드가 한국어 값을 비교하지 않게)
VERDICT_ID = {"강화": "up", "불변": "flat", "약화": "down"}
# 실적 일정 제목 — 수집기(fetch_calendar)가 만든 형식. 구조화 필드(name·hour)가 있으면 그것이 먼저
_EARN_TITLE = re.compile(r"^(?P<name>.+?) \((?P<tk>[^()\s]+)\) 실적 발표(?: · (?P<hour>장전|장후|장중))?$")
_HOUR_ID = {"장전": "bmo", "장후": "amc", "장중": "dmh"}
_SEP_CLASS = re.compile(r" \(별도 클래스( \d+)?\)")


def latent_view(rows):
    """잠재지배자 카드 — 테마·사유가 수집기의 **자동 문장**이면 같은 상태(테마·순위·1년)로 그 언어 문장을 만든다.
    사람이 쓴 해설(latent_overrides)은 콘텐츠 — 번역 캐시 전까지 원문(표식)."""
    if LANG == i18n.DEFAULT:
        return rows
    try:
        import generate_candidates as gc
        theme_id, tmpl = gc.THEME_ID, gc.template_story
    except Exception as e:
        print(f"[warn] 잠재지배자 자동 문장 판정 불가 — 원문 유지: {e}", file=sys.stderr)
        return rows
    out = []
    for c in rows or []:
        c = dict(c)
        tid = c.get("theme_id") or theme_id.get(c.get("theme"))
        auto = c.get("story_auto")
        if auto is None:
            try:
                auto = (c.get("story") == tmpl(c))
            except Exception:
                auto = False
        if tid:
            c["theme"] = i18n.t(LANG, "latent.theme." + tid)
            if auto and isinstance(c.get("momentum_1y"), (int, float)):
                c["story"] = i18n.t(LANG, "latent.story", theme=c["theme"], rank=c.get("rank"),
                                    m=f"{c['momentum_1y']:+}")
        out.append(c)
    return out


def calendar_view(cal):
    """주요일정 — 실적 일정은 수집기가 만든 한 줄을 상태(종목·티커·장 시간)에서 그 언어로 다시 만든다."""
    if LANG == i18n.DEFAULT or not isinstance(cal, dict):
        return cal
    cal = dict(cal)
    evs = []
    for e in cal.get("events") or []:
        e = dict(e)
        if e.get("type") == "earnings":
            m = _EARN_TITLE.match(str(e.get("title") or ""))
            name, tk, hour = (e.get("name"), e.get("ticker"), e.get("hour"))
            if m and not name:
                name, tk, hour = m.group("name"), m.group("tk"), _HOUR_ID.get(m.group("hour") or "", "")
            if name and tk:
                e["title"] = i18n.t(LANG, "cal.earn", name=i18n.stock_name(LANG, tk, name), tk=tk) + \
                    (i18n.t(LANG, "cal.hour." + hour) if hour in ("bmo", "amc", "dmh") else "")
        evs.append(e)
    cal["events"] = evs
    return cal


def gurus_view(g):
    """거인의 어깨 — 분기 표기는 보고 기준일(상태)에서, 클래스 표기는 그 언어로. 기관 이름·소개는 콘텐츠."""
    if not isinstance(g, dict):
        return g
    g = dict(g)
    out = []
    for x in g.get("gurus") or []:
        x = dict(x)
        m = re.match(r"^(\d{4})-(\d{2})", str(x.get("period") or ""))
        if m and LANG != i18n.DEFAULT:
            x["period_label"] = i18n.t(LANG, "gu.period", y=m.group(1), q=(int(m.group(2)) - 1) // 3 + 1)
        if LANG != i18n.DEFAULT:
            x["holdings"] = [dict(h, name=_SEP_CLASS.sub(lambda mm: " (" + i18n.t(LANG, "gu.sep_class") +
                                                                 (mm.group(1) or "") + ")", str(h.get("name") or "")))
                             for h in x.get("holdings") or [] if isinstance(h, dict)]
        out.append(x)
    g["gurus"] = out
    return g


def signals_view(sig):
    """신호 — 분류 id 는 모든 언어에. 제목은 ko 한국어 · en **영어 원제**(한국어를 거치지 않는다)."""
    if not isinstance(sig, dict):
        return sig
    sig = dict(sig)
    out = []
    for x in sig.get("signals") or []:
        x = dict(x)
        x["tag_id"] = SIG_TAG_ID.get(x.get("tag") or "기타", "other")
        if LANG != i18n.DEFAULT:
            x["ko"] = x.get("title") or x.get("ko")
        out.append(x)
    sig["signals"] = out
    return sig


def research_view(rdata):
    """리서치 기사 — ko 는 한국어 제목, 다른 언어는 원제. 요약은 콘텐츠(번역 캐시 전까지 원문 표식)."""
    if LANG == i18n.DEFAULT:
        return rdata
    rdata = dict(rdata)
    st = []
    for s in rdata.get("stocks") or []:
        s = dict(s)
        s["name"] = i18n.stock_name(LANG, s.get("ticker"), s.get("name"))
        s["articles"] = [dict(a, title_ko=None) for a in s.get("articles") or [] if isinstance(a, dict)]
        st.append(s)
    rdata["stocks"] = st
    return rdata


def dissect_view(items):
    """필독 해부실 — 다른 언어는 원제(영어 원문)를 제목으로. 문단 브리핑은 단계 3 이 원문에서 새로 만든다."""
    if LANG == i18n.DEFAULT:
        return items
    return [dict(it, ko_title=None) for it in items or [] if isinstance(it, dict)]


def pioneers_view(doc):
    """개척자 — 이름은 ko 한국어 · 다른 언어 영문. 헤드라인은 ko 번역 · 다른 언어 원제. 역할·메모는 콘텐츠."""
    if not isinstance(doc, dict):
        return doc
    doc = dict(doc)
    people = []
    for p in doc.get("people") or []:
        p = dict(p)
        p["name"] = p.get("ko") if LANG == i18n.DEFAULT else (p.get("en") or p.get("ko"))
        if LANG != i18n.DEFAULT:
            p["items"] = [dict(it, ko=it.get("title") or it.get("ko")) for it in p.get("items") or []
                          if isinstance(it, dict)]
        people.append(p)
    doc["people"] = people
    return doc


def essays_view(essays):
    """동행 에세이 — 판정·출처 id 를 모든 언어에 붙인다(화면 코드가 한국어 판정값을 비교하지 않게)."""
    out = []
    for e in essays or []:
        e = dict(e)
        e["verdict_id"] = VERDICT_ID.get(str(e.get("verdict") or "").strip(), "hold")
        e["origin_id"] = "direct" if e.get("origin") == "직접" else ""
        out.append(e)
    return out


def history_view(data, page_key):
    """변동 이력 — 제목·설명은 카탈로그. 구조화 사건(events)이 있는 기록은 그 언어로 다시 만든다.
    사건 구조가 없는 과거 기록은 콘텐츠(번역 캐시 전까지 원문 표식)."""
    if LANG == i18n.DEFAULT:
        return data
    data = dict(data)
    data["page_title"] = i18n.t(LANG, f"hist.{page_key}.title")
    data["page_desc"] = i18n.t(LANG, f"hist.{page_key}.desc")
    try:
        import weekly_history as wh
        render = wh.render_entry
    except Exception:
        render = None
    if render:
        data["entries"] = [render(e, LANG) if isinstance(e, dict) and e.get("events") else e
                           for e in data.get("entries") or []]
    return data


def build_latent():
    """latent.html — 잠재지배자"""
    template_path = SCRIPTS_DIR / "latent-template.html"
    if not template_path.exists():
        print(f"[skip] latent-template.html 없음"); return
    data = localize_latest(load_data("latest.json"))
    data["latent"] = latent_view(data.get("latent"))
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    out = OUT_DIR / "latent.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_megatrend():
    """megatrend.html — 메가트렌드 (4 카테고리)"""
    template_path = SCRIPTS_DIR / "megatrend-template.html"
    data_path = DATA_DIR / "megatrend.json"
    if not template_path.exists() or not data_path.exists():
        print(f"[skip] megatrend 자원 없음"); return
    data = load_data("megatrend.json")
    data["meta"] = _header_meta()
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    out = OUT_DIR / "megatrend.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


PLACEHOLDERS = [
]


def build_about():
    """about.html — UNIVERTRIX 브랜드 스토리"""
    template_path = SCRIPTS_DIR / "about-template.html"
    if not template_path.exists():
        print("[skip] about-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    usd_krw_str = f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—"
    html = template
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", usd_krw_str)
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "")  # About은 어느 탭도 비활성
    out = OUT_DIR / "about.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_research():
    """research.html — 종목 리서치·분석 기사 (자동 수집)"""
    template_path = SCRIPTS_DIR / "research-template.html"
    if not template_path.exists():
        print("[skip] research-template.html 없음"); return
    data_path = DATA_DIR / "research.json"
    rdata = load_data("research.json", {"generated_label": "", "stocks": []})
    cal_path = DATA_DIR / "calendar.json"
    rdata = research_view(rdata)
    rdata["calendar"] = calendar_view(load_data("calendar.json", {"events": []}))
    gurus_path = DATA_DIR / "gurus.json"
    rdata["gurus"] = gurus_view(load_data("gurus.json", {"gurus": []}))
    signals_path = DATA_DIR / "signals.json"
    rdata["signals"] = signals_view(load_data("signals.json", {"signals": []}))
    # 🔬 필독 해부실 — 실패 기록(failures)은 내부용이므로 페이지에 싣지 않는다
    tr_path = DATA_DIR / "translations.json"
    tr = load_data("translations.json", {})
    tr_items = tr.get("items", [])
    # 역방향 링크 {해부 id: 칼럼 영구 슬러그} — 같은 원문으로 쓴 칼럼이 있을 때만.
    # 칼럼이 없는 해부 글은 키가 없어 '칼럼 보기' 버튼이 아예 만들어지지 않는다.
    by_url = {it["url"]: it["id"] for it in tr_items if it.get("url") and it.get("id")}
    column_links = {}
    cols_path = DATA_DIR / "columns.json"
    if cols_path.exists():
        try:
            for c in json.loads(cols_path.read_text(encoding="utf-8")).get("columns", []):
                dx_id = by_url.get(c.get("url"))
                if dx_id and dx_id not in column_links:  # 같은 원문 칼럼이 여럿이면 최신 1건
                    column_links[dx_id] = _col_slug(c)
        except Exception as e:
            print(f"[warn] 칼럼 역방향 링크 생성 실패(무시): {e}")
    rdata["translations"] = {"generated_label": tr.get("generated_label", ""),
                             "items": dissect_view(tr_items),
                             "column_links": column_links}
    template = template_path.read_text(encoding="utf-8")
    # 헤더 배지 값 (placeholder 방식과 동일)
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    usd_krw_str = f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—"
    html = template
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", usd_krw_str)
    html = html.replace("{{DATA_JSON}}", json.dumps(rdata, ensure_ascii=False))
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "active" if key == "RESEARCH" else "")
    out = OUT_DIR / "research.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_community():
    """community.html — 커뮤니티 (Firebase 교신 피드)"""
    template_path = SCRIPTS_DIR / "community-template.html"
    if not template_path.exists():
        print("[skip] community-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    usd_krw_str = f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—"
    html = template
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", usd_krw_str)
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "active" if key == "COMMUNITY" else "")
    out = OUT_DIR / "community.html"
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


AGENT_DISCLAIMER = ("무인 리서치 실험의 산출물이며 투자 권유가 아닙니다. "
                    "확률·가치 추정은 기계 생성 가정입니다.")


# 축 한글 이름 — state.axes 가 1순위, 없으면 이 고정 사전(축 머리글자 기준).
# 화면에 'A_starlink' 같은 기계 키를 내보내지 않는다(작업 규칙 8).
AXIS_KO = {"A": "Starlink", "B": "Starship", "C": "AI 컴퓨트", "D": "재무", "E": "외부 변수"}
_AXIS_KEY = re.compile(r"\b([A-E])_[a-z]+\b")
STATUS_CHIP = {"달성": "done", "달성(조기)": "done", "진행": "prog", "미달": "miss", "미판정": "wait"}
RUN_RESULT_KO = {"success": "성공", "failure": "실패", "cancelled": "취소", "skipped": "건너뜀",
                 "timed_out": "시간 초과"}
SOURCE_STATE_KO = {"ok": "정상", "zero": "새 글 없음", "decode_fail": "디코딩 실패",
                   "http_error": "요청 실패", "timeout": "응답 지연"}


def axis_name(key, axes):
    """축 키 → 한글 이름. state.axes(dict 또는 list) → 고정 사전 → '기타'."""
    if isinstance(axes, dict):
        v = axes.get(key)
        if isinstance(v, dict):
            v = v.get("ko") or v.get("name")
        if isinstance(v, str) and v.strip():
            return v.strip()
    elif isinstance(axes, list):
        for a in axes:
            if isinstance(a, dict) and key in (a.get("id"), a.get("key"), a.get("axis")):
                v = a.get("ko") or a.get("name")
                if v:
                    return str(v)
    return AXIS_KO.get((key or "?")[:1].upper(), "기타")


def ko_axes(text, axes):
    """본문 속 축 키(A_starlink 등)를 한글 이름으로 — 보고서·변화 기록 공용."""
    return _AXIS_KEY.sub(lambda m: axis_name(m.group(0), axes), text or "")


def _kst_today():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).date()


def probe_schedule(run_date, triggers, today=None):
    """관측 일정 상태 — 화면 문구는 이 상태에서 생성한다(D1·E6)."""
    from datetime import date
    today = today or _kst_today()
    nxt = next_probe_date(run_date or "")
    out = {"next": nxt, "overdue_days": 0, "age_days": None, "stale": False, "trigger": None,
           "today": today}
    try:
        nd = date.fromisoformat(nxt)
        if today > nd:
            out["overdue_days"] = (today - nd).days
    except ValueError:
        pass
    try:
        out["age_days"] = (today - date.fromisoformat((run_date or "")[:10])).days
        out["stale"] = out["age_days"] > 35
    except ValueError:
        pass
    try:
        from spacex_watch import parse_trigger_date
    except Exception:
        parse_trigger_date = None
    best = None
    for t in triggers or []:
        if not isinstance(t, dict) or (t.get("status") or "대기") != "대기" or not parse_trigger_date:
            continue
        d = parse_trigger_date(t.get("expected_date"))
        if d and (best is None or d < best[0]):
            best = (d, t)
    if best:
        exp = best[1].get("expected_date", "")
        out["trigger"] = {"id": best[1].get("id", ""), "name": best[1].get("name", ""),
                          "dday": (best[0] - today).days, "expected": exp,
                          "window_end": _window_end(exp)}
    return out


def _window_end(expected):
    """'2026-4Q'·'2026-2H ~ 2027-1H' 같은 구간 예상일의 끝 날짜(구간이 아니면 None).

    분기·반기 예상은 시작일 하루로 읽으면 구간 첫날부터 '경과'로 보인다(2026-10-23 실측:
    '2026-4Q' 가 10/23 에 '예상일 경과 22일'로 표시됐다).
    """
    import re as _re
    from datetime import date
    m = list(_re.finditer(r"(\d{4})-([1-4])Q|(\d{4})-([12])H", expected or ""))
    if not m:
        return None
    g = m[-1]
    if g.group(1):
        y, q = int(g.group(1)), int(g.group(2))
        nxt = date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1)
    else:
        y, h = int(g.group(3)), int(g.group(4))
        nxt = date(y + (h == 2), 1 if h == 2 else 7, 1)
    from datetime import timedelta
    return nxt - timedelta(days=1)


def schedule_html(sch, run, esc):
    """D1 — 정상이면 다음 예정일 + 다음 트리거 D-day, 경과면 경과 일수(상태에서 생성)."""
    rid = esc(run.get("run_id") or "—")
    rdate = esc(run.get("run_date") or "—")
    tr = sch.get("trigger")
    if tr:
        dd = tr["dday"]
        we = tr.get("window_end")
        if dd < 0 and we is not None and (sch.get("today") or _kst_today()) <= we:
            when = f"예상 구간 중(~{we.isoformat()})"
        else:
            when = (f"D-{dd}" if dd > 0 else "D-day" if dd == 0 else f"예상일 경과 {-dd}일")
        tr_txt = " · 다음 트리거 " + esc(tr["id"]) + " " + esc(tr["name"]) + " " + when
    else:
        tr_txt = ""
    if sch["overdue_days"] > 0 or sch["stale"]:
        age = sch.get("age_days")
        msg = (f"예정일 경과 {sch['overdue_days']}일 — 엔진 점검 중"
               if sch["overdue_days"] > 0 else "관측 공백이 길어지고 있습니다 — 엔진 점검 중")
        return ('<div class="ag-delay"><b>' + esc(msg) + '</b><br>마지막 관측 ' + rdate +
                ('' if age is None else f'({age}일 전)') + ' · ' + rid + ' 회차 · 예정일 ' +
                esc(sch["next"]) + tr_txt + '</div>')
    return ('<div class="ag-meta">' + rid + ' 회차 · 관측일 ' + rdate + ' · 다음 관측 예정 ' +
            esc(sch["next"]) + ' (매월 1일)' + tr_txt + '</div>')


def _fmt_num(v):
    if isinstance(v, bool) or v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def progress_cell(m, esc):
    """D3 — 현재값/목표 + 진척 막대. progress 가 null 이면 사유, 키가 없으면 미기록."""
    if "progress" not in m:
        return '<span class="ag-np">진척 미기록 — 다음 회차부터 측정</span>', ""
    pg = m.get("progress")
    if not isinstance(pg, dict):
        return '<span class="ag-np">' + esc(m.get("progress_reason") or "측정 불가") + '</span>', ""
    cur, tgt, unit = pg.get("current"), pg.get("target"), pg.get("unit") or ""
    txt = esc(_fmt_num(cur)) + ' → 목표 ' + esc(_fmt_num(tgt)) + ' ' + esc(unit)
    meta = (' <span class="ag-np">(' + esc(pg.get("asof") or "") + ')</span>'
            if pg.get("asof") else "")
    bar = ""
    if isinstance(cur, (int, float)) and isinstance(tgt, (int, float)) and tgt > 0 and cur >= 0:
        ratio = (tgt / cur if pg.get("direction") == "down" and cur > 0 else cur / tgt)
        pct = max(0, min(100, round(ratio * 100)))
        bar = ('<div class="ag-bar" title="' + str(pct) + '%"><i style="width:' + str(pct) +
               '%"></i></div><span class="ag-np">' + str(pct) + '%</span>')
    return txt + meta, bar


def status_chip(st, esc):
    cls = STATUS_CHIP.get(st or "", "wait")
    return '<span class="ag-chip ag-chip-' + cls + '">' + esc(st or "미판정") + '</span>'


def timeline_html(watch, esc, n=10):
    """D4 — 최근 사건 10건(공시·발사·Starship·실적·보도자료). 측정층 원장에서."""
    evs = [e for e in (watch or {}).get("events") or [] if isinstance(e, dict)][:n]
    if not evs:
        return ""
    rows = []
    for e in evs:
        title = esc(e.get("title") or "")
        if e.get("url"):
            title = ('<a href="' + esc(e["url"]) + '" target="_blank" rel="noopener">' +
                     title + '</a>')
        rows.append('<li><span class="ag-tl-d">' + esc(e.get("date") or "") + '</span>'
                    '<span class="ag-tl-s">' + esc(e.get("source") or "") + ' · ' +
                    esc(e.get("form") or "") + '</span>' + title + '</li>')
    return ('<div class="ag-h">최근 사건 — 측정층 자동 기록</div><ul class="ag-tl">' +
            "".join(rows) + '</ul>')


def engine_html(watch, vres, status, esc):
    """D5 — 월간 엔진 마지막 실행·결과·검증 일치 n/m, 사건층 소스 상태."""
    eng = ((watch or {}).get("engine") or {})
    last = eng.get("last_run") or {}
    if last:
        res = RUN_RESULT_KO.get(last.get("conclusion") or "", "결과 미상")
        e_txt = ("월간 엔진 마지막 실행 " + esc((last.get("created_at") or "")[:10]) +
                 " · " + esc(res))
    elif eng:
        e_txt = "월간 엔진 실행 기록 없음(첫 실행 전)"
    else:
        e_txt = "월간 엔진 상태 미확인"
    if isinstance(vres, dict) and vres.get("checked"):
        ok = int(vres.get("match") or 0) + int(vres.get("rounding") or 0)
        v_txt = f" · 검증 일치 {ok}/{int(vres['checked'])}"
    else:
        v_txt = " · 검증 기록 없음"
    srcs = []
    for k, r in sorted(((status or {}).get("sources") or {}).items()):
        if (isinstance(r, dict) and r.get("kind") == "companion"
                and str(r.get("source", "")).startswith("spacex:")):
            name = str(r["source"]).split(":", 1)[1]
            srcs.append(esc(name) + " " + esc(SOURCE_STATE_KO.get(r.get("outcome"), "미기록")))
    s_txt = ("<br>사건층 소스: " + " · ".join(srcs)) if srcs else ""
    return '<div class="ag-engine">' + e_txt + v_txt + s_txt + '</div>'


def _read_data_json(name):
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def render_agent_cockpit(ar, today=None, watch=None, status=None, vres=None):
    """관제실 v2(2026-10-23) → (관제판 HTML, PROBE_DATA, 보고서 덩어리). 판단층은 읽기만 한다."""
    import probe_cockpit
    st = ar.read_state()
    if not st and not ar.report_files():
        return "", {}, []
    html, data, reports = probe_cockpit.render(ar, today=today, watch=watch, status=status, vres=vres,
                                               lang=LANG)
    size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    print(f"[OK] 관제실: PROBE_DATA {size // 1024}KB · 보고서 {len(reports)}건 · 검색 {len(data.get('search', []))}건")
    return html, data, reports


def render_agent_probe(ar, today=None, watch=None, status=None, vres=None):
    """탐사선 탭 HTML(관제판) — 하위 호환 진입점. 화면은 관제실 v2 가 그린다."""
    return render_agent_cockpit(ar, today=today, watch=watch, status=status, vres=vres)[0]


def render_agent_probe_v1(ar, today=None, watch=None, status=None, vres=None):
    """(구) 한 줄로 쌓인 탐사선 탭 — 관제실 v2 이전. 참고용으로만 남긴다."""
    st = ar.read_state()
    files = ar.report_files()
    if not st and not files:
        return ""
    esc = ar.esc
    run = st.get("run") or {}
    sc = st.get("scenarios") or {}
    val = st.get("valuation") or {}
    board = st.get("scorecard") or {}
    axes = st.get("axes")
    watch = _read_data_json("spacex_watch.json") if watch is None else watch
    status = _read_data_json("fetch_status.json") if status is None else status
    if vres is None:
        try:
            vres = json.loads((HERE / "agent-research" / "state" / "verify_result.json")
                              .read_text(encoding="utf-8"))
        except Exception:
            vres = {}

    h = ['<div class="ag-notice"><b>' + esc(AGENT_DISCLAIMER) + '</b></div>']
    sch = probe_schedule(run.get("run_date"), st.get("triggers_next_12m"), today)
    h.append(schedule_html(sch, run, esc))

    # 상단 카드 — 시나리오 확률 3개 · 적정가치 밴드
    #   valuation.recommendation·max_position 계열은 **싣지 않는다**(준법 필터 ③)
    cards = []
    for key, ko in (("bull", "강세"), ("base", "기본"), ("bear", "약세")):
        sc_i = sc.get(key) or {}
        pr = sc_i.get("probability")
        cards.append('<div class="ag-card"><div class="ag-k">' + ko + ' 시나리오</div>' +
                     '<div class="ag-v">' + ('—' if pr is None else f"{pr * 100:.0f}%") +
                     '</div><div class="ag-s">10년 뒤 주당 ' +
                     ('—' if sc_i.get("value_per_share_usd") is None
                      else '$' + str(sc_i["value_per_share_usd"])) + '</div></div>')
    band = val.get("fair_value_band_usd") or []
    band_txt = (f"${band[0]}~${band[1]}" if len(band) == 2 else "—")
    cards.append('<div class="ag-card"><div class="ag-k">확률가중 적정가치 밴드</div>' +
                 '<div class="ag-v">' + band_txt + '</div>' +
                 '<div class="ag-s">중앙 ' +
                 ('—' if val.get("fair_value_midpoint_usd") is None
                  else '$' + str(val["fair_value_midpoint_usd"])) + '</div></div>')
    h.append('<div class="ag-cards">' + "".join(cards) + '</div>')
    h.append(engine_html(watch, vres, status, esc))

    # 이정표 채점표 — 현재값/목표 · 진척 막대 · 판정 칩
    ms = st.get("milestones") or []
    if ms:
        h.append('<div class="ag-h">이정표 채점표 — 달성 ' +
                 str(board.get("achieved", 0)) + ' · 미달 ' + str(board.get("missed", 0)) +
                 ' · 판정 대기 ' + str(board.get("pending", len(ms))) + '</div>')
        rows = []
        for m in ms:
            val_txt, bar = progress_cell(m, esc)
            rows.append('<tr><td>' + esc(m.get("year", "")) + '</td><td>' +
                        esc(axis_name(m.get("axis", ""), axes)) + '</td><td>' +
                        esc(ko_axes(m.get("statement", ""), axes)) + '</td><td>' + val_txt +
                        '</td><td>' + bar + '</td><td>' + status_chip(m.get("status"), esc) +
                        '</td></tr>')
        h.append("<div class='ag-tw'><table class='ag-table'><thead><tr>"
                 "<th>연도</th><th>축</th><th>이정표</th><th>현재 / 목표</th><th>진척</th>"
                 "<th>판정</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")

    h.append(timeline_html(watch, esc))

    # 지난달 대비 변화
    ch = ar.read_changelog()
    if ch.strip():
        body, _ = ar.sanitize(ch)
        h.append('<div class="ag-h">지난달 대비 변화</div>')
        h.append('<div class="ag-body">' + ar.md_to_html(ko_axes(body, axes)) + '</div>')

    # 최신 보고서 + 과거 회차 목차(해부실 폼 공용 클래스)
    cut = {"phase4": False, "blocks": 0}
    if files:
        if len(files) > 1:
            h.append('<div class="ud-index"><div class="ud-index-title">관측 회차 ' +
                     str(len(files)) + '건</div>' +
                     "".join('<a class="ud-index-row" href="#agent-' + f.stem + '">' +
                             '<span class="ud-index-date">' + esc(f.stem) + '</span>' +
                             esc(report_title(f)) + '</a>' for f in files) + '</div>')
        for idx, f in enumerate(files):
            raw = f.read_text(encoding="utf-8")
            body, info = ar.sanitize(raw)
            if idx == 0:
                cut = info
            open_attr = " open" if idx == 0 else ""
            h.append('<details class="ag-run" id="agent-' + f.stem + '"' + open_attr +
                     '><summary>' + esc(f.stem) + ' — ' + esc(report_title(f)) +
                     '</summary><div class="ag-body">' + ar.md_to_html(ko_axes(body, axes)) +
                     '</div></details>')
    print(f"[OK] 무인 탐사선: 보고서 {len(files)}건 · PHASE4 제외 {cut['phase4']} · "
          f"권고 블록 제외 {cut['blocks']} · 일정 경과 {sch['overdue_days']}일")

    h.append('<div class="ag-link">사건 단위 기록은 '
             '<a href="journal.html#companion">관측노트 동행 관측 🚀SpaceX</a> 에서.</div>')
    return "".join(h)


def report_title(f):
    """보고서 첫 h1 을 제목으로. 없으면 파일명."""
    try:
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.startswith("# "):
                return ln[2:].strip()
    except Exception:
        pass
    return f.stem


def next_probe_date(run_date):
    """다음 관측 예정일 = 다음 달 1일."""
    try:
        y, m = int(run_date[:4]), int(run_date[5:7])
    except (ValueError, IndexError):
        return "매월 1일"
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y}-{m:02d}-01"


def legend_rows(items):
    """레전드 표시층 원장 — 병합된 판단층에서 **표시에 필요한 칸만** 뽑는다(순수 함수).

    여기서 기계가 판정하지 않는다 — 사람이 적어 넣은 값과 기계가 잰 값을 그대로 나른다.
    """
    legend = {}
    for it in items or []:
        b = it.get("buffett") or {}
        og = it.get("buffett_origin") or {}
        conv = b.get("conversion") or {}
        risk5 = b.get("risk5") or {}
        reason = b.get("guard_reason")
        legend[it.get("ticker")] = {
            "origin": og,
            "conv": conv.get("value"),
            "conv_basis": conv.get("basis"),
            "cert": risk5.get("business_certainty"),
            "guard": bool(b.get("cyclical_peak_guard")),
            # 가드 근거 문장(사람 전용) — 없으면 null. 화면이 '미기재'를 상태로 말한다
            "guard_reason": reason.strip() if isinstance(reason, str) and reason.strip()
            else None,
            # 취재가 끝나 값이 산출되면 비고에 "기준 {as_of} · {period}" 로 뜬다.
            # as_of 는 병합값(사람이 이긴다)이 아니라 **사람·자동 중 최신 갱신일**이다 —
            # 자동층이 매일 다시 잰 값을 사람의 옛 날짜로 표시하지 않는다(legend-audit D)
            "as_of": (it.get("buffett_asof") or {}).get("latest") or b.get("as_of"),
            "period": b.get("period"),
            "method": b.get("method"),
            # 측정 방식의 화면 이름 id — 원문 method 는 태그명이 들어 있어 그대로 쓰지 않는다(규칙 8)
            "method_id": ("xbrl" if str(b.get("method") or "").startswith("SEC XBRL")
                          else "gaap" if str(b.get("method") or "").startswith("GAAP 미조정") else ""),
        }
    return legend


def legend_asof(cfg, auto_doc, items):
    """레전드 헤더의 '판단층 기준일' — 사람 값·자동 값 중 **최신 갱신일**(순수 함수).

    예전엔 사람 파일의 asof(2026-09-03)만 보였다. 자동층은 full 마다 다시 재고 스카우트도
    도는데 화면은 멈춘 것처럼 보였다. 날짜는 파일 시각(mtime)이 아니라 **내용 속
    타임스탬프**에서 읽는다(CI 신선 체크아웃 지뢰).
    """
    def day(v):
        s = str(v or "")[:10]
        return s if len(s) == 10 and s[4] == "-" else None
    hs = [day((cfg or {}).get("asof"))]
    as_ = [day((auto_doc or {}).get("generated_at")), day((auto_doc or {}).get("scout_label"))]
    for it in items or []:
        pair = it.get("buffett_asof") or {}
        hs.append(day(pair.get("human")))
        as_.append(day(pair.get("auto")))
    h = max([d for d in hs if d], default=None)
    a = max([d for d in as_ if d], default=None)
    return {"latest": max([d for d in (h, a) if d], default=None), "human": h, "auto": a}


def build_observatory():
    """observatory.html — 데이터 천문대 (오늘의 태양계 + 관측일지)"""
    template_path = SCRIPTS_DIR / "observatory-template.html"
    if not template_path.exists():
        print("[skip] observatory-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    latest = load_data("latest.json")
    cols = load_data("columns.json", {}).get("columns", [])
    meta = latest.get("meta", {})
    # ── 우주 지도 타일: 전 지역 고유 종목 + 전일 대비 시총 증감 ──
    # 국가 이름은 카탈로그(country.<국기 코드>) — 국기 이모지(지역 표시 문자 2개)에서 코드를 만든다
    def country_name(flag):
        cps = [ord(c) - 0x1F1E6 for c in (flag or "")]
        code = "".join(chr(97 + c) for c in cps) if len(cps) == 2 and all(0 <= c < 26 for c in cps) else ""
        key = "country." + code
        return i18n.t(LANG, key) if code and key in i18n.catalog(i18n.DEFAULT) else i18n.t(LANG, "country.other")
    prev_mc = {}
    snap_dir = DATA_DIR / "snapshots"
    today = meta.get("fetched_date", "")
    if snap_dir.exists():
        for s in sorted(snap_dir.glob("*.json"), reverse=True):
            if today and today in s.name:
                continue
            try:
                prev = json.loads(s.read_text(encoding="utf-8"))
                for reg in prev.get("regions", {}).values():
                    rows = reg if isinstance(reg, list) else reg.get("stocks", [])
                    for r in rows:
                        if r.get("ticker") and r.get("mc"):
                            prev_mc.setdefault(r["ticker"], r["mc"])
                for r in prev.get("latent", []):
                    if r.get("ticker") and r.get("mc"):
                        prev_mc.setdefault(r["ticker"], r["mc"])
                break
            except Exception:
                continue
    earth_set = {s.get("ticker") for s in latest.get("regions", {}).get("earth", {}).get("stocks", [])}
    latent_rows = latest.get("latent", [])
    latent_set = {x.get("ticker") for x in latent_rows}
    tiles, seen = [], set()
    for reg in latest.get("regions", {}).values():
        for s in reg.get("stocks", []):
            tk = s.get("ticker")
            if not tk or tk in seen or not s.get("mc"):
                continue
            seen.add(tk)
            pm = prev_mc.get(tk)
            chg = round((s["mc"] - pm) / pm * 100, 2) if pm else None
            tiles.append({"t": tk, "n": i18n.stock_name(LANG, tk, s["name"]), "mc": s["mc"], "chg": chg,
                          "g": country_name(s.get("flag", "")),
                          "f": s.get("flag", ""),
                          "e": 1 if tk in earth_set else 0,
                          "l": 1 if tk in latent_set else 0})
    # 잠재지배자(30~200위권) — 기존 지도에 없는 종목은 새 타일로 편입
    for x in latent_rows:
        tk = x.get("ticker")
        if not tk or tk in seen or not x.get("mc"):
            continue
        seen.add(tk)
        pm = prev_mc.get(tk)
        chg = round((x["mc"] - pm) / pm * 100, 2) if pm else None
        tiles.append({"t": tk, "n": i18n.stock_name(LANG, tk, x["name"]), "mc": x["mc"], "chg": chg,
                      "g": country_name(x.get("country", "")),
                      "f": x.get("country", ""), "e": 0, "l": 1})
    # 🔬 해부실 연결 지도: {원문 URL: 해부 id} — 칼럼에 '해부실에서 보기' 버튼을
    #    띄울지 판정하는 근거. 해부 글이 없는 칼럼은 버튼을 아예 만들지 않는다.
    tr_path = DATA_DIR / "translations.json"
    dissected = {}
    if tr_path.exists():
        try:
            for it in json.loads(tr_path.read_text(encoding="utf-8")).get("items", []):
                if it.get("url") and it.get("id"):
                    dissected[it["url"]] = it["id"]
        except Exception as e:
            print(f"[warn] translations.json 읽기 실패(무시): {e}")
    # 시차 관측 — 측정층 산출물 (없어도 탭은 빈 상태로 뜬다)
    buffett = {}
    bf_path = DATA_DIR / "buffett.json"
    if bf_path.exists():
        try:
            buffett = json.loads(bf_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] buffett.json 읽기 실패(무시): {e}")
    # 레전드벤치마크 표시층 — 판단층(buffett_config.json)에서 **표시에 필요한 칸만** 뽑는다.
    # 측정층 산출물(buffett.json)에는 사람이 취재한 전환율·risk5 가 실리지 않는다.
    # 여기서 기계가 판정하지 않는다 — 사람이 적어 넣은 값을 그대로 나른다.
    legend = {}
    legend_meta = {}
    lc_path = DATA_DIR / "buffett_config.json"
    if lc_path.exists():
        try:
            _cfg = json.loads(lc_path.read_text(encoding="utf-8"))
            try:                       # 측정층과 **같은** 병합기를 쓴다 (규칙 두 벌 금지)
                import buffett_layers
                _items = buffett_layers.merged_items(_cfg)
            except Exception as e:
                print(f"[warn] 판단층 병합 실패 → 사람 판단층만 표시: {e}")
                _items = _cfg.get("items", [])
            legend = legend_rows(_items)
            _auto_doc = {}
            try:
                _auto_doc = json.loads((DATA_DIR / "buffett_auto.json").read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[warn] buffett_auto.json 읽기 실패 — 기준일은 사람 값만: {e}")
            legend_meta = legend_asof(_cfg, _auto_doc, _items)
        except Exception as e:
            print(f"[warn] buffett_config.json 읽기 실패(무시): {e}")
    # 🛰 무인 탐사선 — agent-research/ 를 **빌드 시점에** 정적 렌더한다.
    #    클라이언트가 판단층 파일을 직접 긁으면 준법 필터를 우회할 길이 생긴다.
    agent_html, probe_data, probe_reports = "", {}, []
    try:
        import agent_report
        agent_html, probe_data, probe_reports = render_agent_cockpit(agent_report)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[warn] 무인 탐사선 렌더 실패(탭은 빈 상태): {e}")
    obs_data = {
        "earth": localize_latest(latest).get("regions", {}).get("earth", {}).get("stocks", []),
        "fetched": meta.get("fetched_date", ""),
        "columns": columns_view(cols),
        "tiles": tiles,
        "dissected": dissected,
        "buffett": buffett_view(buffett),
        "legend": legend,
        "legend_meta": legend_meta,
        "agent": agent_html,
        "probe": probe_data,
        "probe_reports": probe_reports,
    }
    # 스크립트 블록 안의 JSON — 문자열 속 종료 태그가 블록을 닫지 못하게 슬래시를 이스케이프('<\/')
    html = template.replace("{{OBS_DATA}}", json.dumps(obs_data, ensure_ascii=False).replace("</", "<\\/"))
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "")
    _w(OUT_DIR / "observatory.html", html)
    print(f"[OK] observatory.html ({len(html):,} chars)")
    # 구주소 리다이렉트 (북마크 보호) — 옛 주소는 한국어 루트에만 있었다
    if LANG == i18n.DEFAULT:
        _w(OUT_DIR / "my-universe.html",
           '<!doctype html><meta charset="utf-8">'
           '<meta http-equiv="refresh" content="0; url=observatory.html">'
           '<a href="observatory.html">데이터 천문대로 이동</a>')
        print("[OK] my-universe.html → observatory.html 리다이렉트")


# 칼럼 유형 — **원문 제목**에서 판정한다(번역 제목으로 판정하면 언어마다 갈라진다)
def column_kind(title):
    t = title or ""
    if t.startswith("📡"):
        return "signal"
    if "주간 관측" in t:
        return "weekly"
    return "deep"


def columns_view(cols):
    """관측일지 — 공유 앵커(slug)와 유형(kind)을 원문 기준으로 붙인다. 본문은 원문(번역은 단계 3 캐시)."""
    out = []
    for c in cols or []:
        c = dict(c)
        c["slug"] = _col_slug(c)
        c["kind"] = column_kind(c.get("title"))
        viz = c.get("viz")
        if LANG != i18n.DEFAULT and isinstance(viz, dict) and isinstance(viz.get("planets"), list):
            viz = dict(viz)
            viz["planets"] = [dict(p, n=i18n.stock_name(LANG, p.get("t"), p.get("n")))
                              if isinstance(p, dict) else p for p in viz["planets"]]
            c["viz"] = viz
        out.append(c)
    return out


# 판단층 분류(사람이 정한 한국어 이름) → 화면 이름 id. 화면 이름은 카탈로그(px.type.*)가 만든다.
# 모르는 분류는 id 없이 원문 그대로 — 화면이 원문 표식을 단다(지어낸 번역으로 메우지 않는다)
TYPE_ID = (("추정불가", "too_hard"), ("플라이트세이프티형", "flight"), ("통행료형", "toll"),
           ("씨즈형", "sees"), ("시클리컬", "cyclical"), ("플로트형", "float"))
BASIS_ID = {"forward": "forward", "trailing": "trailing", "미취재": "uncovered",
            "too_hard": "too_hard", "unlisted": "unlisted", "float": "float"}


def buffett_view(buffett):
    """시차 관측·레전드 — 분류·바구니에 기계 id 를 붙이고 종목명을 그 언어로. 측정값은 그대로."""
    if not isinstance(buffett, dict):
        return buffett
    b = dict(buffett)
    items = []
    for x in b.get("items") or []:
        if not isinstance(x, dict):
            items.append(x)
            continue
        x = dict(x)
        ty = str(x.get("type") or "").strip()
        # 분류 이름과 **정확히 같을 때만** id — '씨즈형·고자본 혼합' 같은 사람의 수식어를 잘라내지 않는다
        x["type_id"] = next((i for k, i in TYPE_ID if k == ty), "")
        # 사업 확실성 ✕ 판정(F6)은 수식어가 붙어도 '추정불가'를 포함하면 참 — 표시 이름과 분리한다
        x["uncertain"] = "추정불가" in ty
        x["basis_id"] = BASIS_ID.get(x.get("basis"), "")
        x["name"] = i18n.stock_name(LANG, x.get("ticker"), x.get("name"))
        items.append(x)
    b["items"] = items
    return b


def build_pioneers():
    """pioneers.html — 개척자: 세상을 바꾸는 인물들의 최신 신호"""
    template_path = SCRIPTS_DIR / "pioneers-template.html"
    if not template_path.exists():
        print("[skip] pioneers-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    data_path = DATA_DIR / "pioneers.json"
    pioneers = load_data("pioneers.json", {"updated": "", "people": []})  # 첫 full 수집 전 — 빈 상태 렌더
    pioneers = pioneers_view(pioneers)
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = template.replace("{{PIONEERS_JSON}}",
                            json.dumps(pioneers, ensure_ascii=False))
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}",
                        f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    _w((OUT_DIR / "pioneers.html"), html)
    print(f"[OK] pioneers.html ({len(html):,} chars)")


def nav_html(page):
    """메뉴 — 순서·활성 탭을 상태(페이지)에서 만든다. 탭 이름은 카탈로그(nav.*)의 {{t:}} 자리표시자."""
    act = NAV_ACTIVE.get(page, page)
    tabs = "".join(f'<a href="{href}" class="nav-tab{" active" if href == act else ""}">{{{{t:{key}}}}}</a>'
                   for href, key in NAV)
    return f'<nav class="site-nav">\n  <div class="site-nav-inner container">{tabs}</div>\n</nav>'


_NAV_RE = re.compile(r'<nav class="site-nav">.*?</nav>', re.S)


def fix_nav():
    """전 페이지 nav 를 생성기 출력으로 갈아끼운다 (멱등).
    예전엔 템플릿 메뉴를 정규식으로 개명·재배열했다(나의우주→데이터 천문대 등). 언어가 넷이 되면
    한국어 탭 이름을 찾는 정규식은 못 쓴다 — 메뉴 전체를 상태에서 다시 만든다(규칙 8)."""
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        new = _NAV_RE.sub(lambda m: nav_html(name), html, count=1)
        # 광폭 레이아웃 (720px → 1280px) — 대시보드 체급에 맞게, 멱등 주입
        if 'id="wide-fix"' not in new:
            new = new.replace("</head>",
                '<style id="wide-fix">.container{max-width:1280px}</style>\n</head>', 1)
        if new != html:
            _w(f, new); n += 1
    print(f"[OK] nav 생성: {n}개 페이지")


def build_policies():
    """policies.html — 이용약관·개인정보·운영정책·면책"""
    template_path = SCRIPTS_DIR / "policies-template.html"
    if not template_path.exists():
        print("[skip] policies-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = template.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "")
    _w((OUT_DIR / "policies.html"), html)
    print(f"[OK] policies.html ({len(html):,} chars)")


def inject_footer_links():
    """전 페이지 푸터에 정책 링크 줄 주입 (멱등)"""
    snippet = ('<div class="uv-policy-links" style="margin-top:10px;font-size:12px;">'
               '<a href="about.html" style="color:inherit;margin:0 7px">{{t:foot.about}}</a>·'
               '<a href="policies.html#terms" style="color:inherit;margin:0 7px">{{t:foot.terms}}</a>·'
               '<a href="policies.html#privacy" style="color:inherit;margin:0 7px">{{t:foot.privacy}}</a>·'
               '<a href="policies.html#community" style="color:inherit;margin:0 7px">{{t:foot.community}}</a>·'
               '<a href="policies.html#disclaimer" style="color:inherit;margin:0 7px">{{t:foot.disclaimer}}</a>·'
               '<a href="policies.html#contact" style="color:inherit;margin:0 7px">{{t:foot.contact}}</a></div>')
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "uv-policy-links" in html or "</footer>" not in html:
            continue
        html = html.replace("</footer>", snippet + "\n</footer>", 1)
        _w(f, html)
        n += 1
    print(f"[OK] 푸터 정책 링크 주입: {n}개 페이지")


def thesis_md_to_html(md):
    """논제 원장(md) → 안전한 HTML. 판단층이라 원본은 절대 건드리지 않고 표시만 변환한다.

    지원 범위는 원장이 실제로 쓰는 문법으로 한정한다(제목·목록·인용·굵게).
    HTML 을 먼저 이스케이프하므로 원장에 태그가 섞여도 페이지가 깨지지 않는다.
    """
    esc = (md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    out, mode = [], None          # mode: None | "ul" | "ol"
    # 머리글의 버전 줄은 카드 배지가 이미 보여준다 — 본문에서 한 번 더 찍지 않는다.
    # (같은 인용 블록의 다른 줄, 예컨대 Anthropic 이해관계 고지는 그대로 살린다)
    meta_line = re.compile(r"^&gt;\s*v[0-9][0-9.]*\s*[·|]\s*\d{4}-\d{2}-\d{2}")

    para = []                     # 이어지는 산문 줄은 한 문단으로 (마크다운 기본 규칙)

    def flush_para():
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()

    def close_list():
        nonlocal mode
        if mode:
            out.append(f"</{mode}>")
            mode = None

    def close():                  # 구조가 바뀌는 지점에서만 부른다
        flush_para()
        close_list()

    for raw in esc.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_para()          # 빈 줄이 문단 경계다
            continue
        # 목록 항목의 이어지는 들여쓴 줄(근거·반증 조건)은 같은 항목 안에 붙인다
        if mode and not para and raw.startswith("   ") and out and out[-1].endswith("</li>"):
            out[-1] = out[-1][:-5] + " " + line.strip() + "</li>"
            continue
        m_ol = re.match(r"^(\d+)\.\s+(.*)$", line)
        if line.startswith("- ") or m_ol:
            want = "ol" if m_ol else "ul"
            if mode != want:
                close()
                out.append(f"<{want}>")
                mode = want
            out.append(f"<li>{m_ol.group(2) if m_ol else line[2:]}</li>")
            continue
        if meta_line.match(line):
            close()
            continue
        if line.startswith("## "):
            close()
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            close()
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("&gt;"):
            close()
            out.append(f"<blockquote>{line.lstrip('&gt;').strip()}</blockquote>")
        else:
            close_list()          # 산문은 문단을 이어 붙인다 — flush 하지 않는다
            para.append(line)
    close()
    html = "".join(out)
    # 연속된 인용줄은 한 덩어리로 (Anthropic 이해관계 고지가 3줄로 쪼개지지 않게)
    html = html.replace("</blockquote><blockquote>", " ")
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)


def thesis_one_line(md):
    """논제 원장에서 '10년 논제' 한 문장만 뽑는다 (고정 공지 카드용).

    판단층을 요약하지 않는다 — 소장이 쓴 첫 문장을 그대로 가져올 뿐이다.
    """
    m = re.search(r"^##\s*10년\s*논제\s*$(.*?)(?=^##\s|\Z)", md or "", re.S | re.M)
    body = (m.group(1) if m else md or "")
    body = re.sub(r"\s+", " ", re.sub(r"[*`>#-]", " ", body)).strip()
    if not body:
        return ""
    # 첫 문장 — 마침표에서 끊되 숫자 뒤의 점($4.3B, 2026.08)은 문장 끝이 아니다
    m2 = re.search(r"^(.{20,}?[^0-9]\.)(?:\s|$)", body)
    return (m2.group(1) if m2 else body[:160].rstrip() + "…")


def thesis_node(t):
    """논제 원장 → 번역 단위 {"one", "body"} — 화면과 번역 엔진이 같은 칸을 본다."""
    if t.get("placeholder"):
        return {"one": "", "body": ""}
    return {"one": thesis_one_line(t["body"]), "body": t["body"]}


def companion_data():
    """동행 관측 주입 데이터 — 회사(논제 스냅샷) + 발행 에세이 원장."""
    import companion_essays as ce
    src = DATA_DIR / "companion_sources.json"
    cfg = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {"companies": []}
    companies = []
    import i18n_content as IC
    for c in cfg.get("companies", []):
        t = ce.read_thesis(c["slug"])
        # 논제 원장(판단층) — 다른 언어는 번역 캐시(관문 통과)가 있을 때만 그 언어로. 원장 파일은 읽기만
        node = IC.apply_node("thesis", LANG, c["slug"], thesis_node(t), IC.THESIS)
        companies.append({
            "slug": c["slug"], "ko": c.get("ko", c["slug"]), "emoji": c.get("emoji", ""),
            "thesis": {"placeholder": t["placeholder"], "version": t["version"],
                       "updated": t["updated"],
                       "one": node["one"],
                       "html": "" if t["placeholder"] else thesis_md_to_html(node["body"])},
        })
    doc = load_data("essays.json", {})
    # 철회된 에세이는 렌더하지 않는다(원장에는 남는다) — 대신 정정 줄을 싣는다
    essays = essays_view([e for e in doc.get("essays", []) if ce.is_public(e)])
    corrections = [c for c in doc.get("corrections", []) if isinstance(c, dict)]
    return {"companies": companies, "essays": essays, "corrections": corrections}


def build_journal():
    """journal.html — 관측노트 (한줄 노트 + 동행 관측 3사 탭)"""
    template_path = SCRIPTS_DIR / "journal-template.html"
    if not template_path.exists():
        print("[skip] journal-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = template.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "")
    ce_data = companion_data()
    html = html.replace("{{COMPANION_DATA}}", json.dumps(ce_data, ensure_ascii=False))
    _w((OUT_DIR / "journal.html"), html)
    waiting = [c["ko"] for c in ce_data["companies"] if c["thesis"]["placeholder"]]
    print(f"[OK] journal.html ({len(html):,} chars) — 관측노트 · 동행 {len(ce_data['companies'])}사"
          f" · 에세이 {len(ce_data['essays'])}편"
          + (f" · 논제 취재 중: {', '.join(waiting)}" if waiting else ""))


def build_placeholders():
    template_path = SCRIPTS_DIR / "placeholder-template.html"
    if not template_path.exists():
        print(f"[skip] placeholder-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    # 헤더 날짜/환율 값 (메인과 동일하게 latest.json meta 에서)
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    usd_krw_str = f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—"
    for p in PLACEHOLDERS:
        html = template
        html = html.replace("{{PAGE_TITLE}}", p["title"])
        html = html.replace("{{PAGE_DESC}}", p["desc"])
        html = html.replace("{{PAGE_ICON}}", p["icon"])
        html = html.replace("{{FETCHED_DATE}}", fetched_label)
        html = html.replace("{{USD_KRW}}", usd_krw_str)
        for key in ("home","latent","mega","research","community","my"):
            html = html.replace("{{ACTIVE_"+key.upper()+"}}", "active" if p["active"]==key else "")
        out = OUT_DIR / p["filename"]
        _w(out, html)
        print(f"[OK] {out.name} ({len(html):,} chars)")


def build_history(page_key, active_marker, out_filename):
    template_path = SCRIPTS_DIR / "history-template.html"
    data_path = DATA_DIR / f"history-{page_key}.json"
    if not template_path.exists() or not data_path.exists():
        print(f"[skip] history-{page_key} 자원 없음"); return
    data = history_view(json.loads(data_path.read_text(encoding="utf-8")), page_key)
    data["meta"] = _header_meta()
    template = template_path.read_text(encoding="utf-8")
    html = template
    html = html.replace("{{PAGE_TITLE}}", html_mod.escape(data.get("page_title", "History")))
    html = html.replace("{{PAGE_DESC}}", html_mod.escape(data.get("page_desc", "")))
    html = html.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    for key in ("home","latent"):
        html = html.replace("{{ACTIVE_"+key.upper()+"}}", "active" if active_marker==key else "")
    out = OUT_DIR / out_filename
    _w(out, html)
    print(f"[OK] {out.name} ({len(html):,} chars)")


LOGO_HTML = """<a class="brand ud-logo uvx" href="index.html" aria-label="{{t:brand.home}}">
      <span class="uvx-lat"><span class="uvx-word">UNIVERTRIX</span>
        <svg class="uvx-orbit" viewBox="0 0 300 60" preserveAspectRatio="none" fill="none" aria-hidden="true">
          <path d="M8 46 C 62 68, 150 -16, 288 20" stroke-width="1.6"/>
          <circle cx="288" cy="20" r="3.4"/>
        </svg></span>
      <span class="uvx-kr">{{t:brand.sub}}</span>
    </a>"""


def logo_html(lang):
    """로고 — 붓글씨 '우주지배자'는 ko 전용. 다른 언어는 같은 자리에 언어 부제(.uvx-sub, 오로라 그대로)."""
    if lang == i18n.DEFAULT:
        return LOGO_HTML
    return LOGO_HTML.replace('<span class="uvx-kr">', '<span class="uvx-kr uvx-sub">', 1)



AURORA_GLOBAL_CSS = """<link href="https://fonts.googleapis.com/css2?family=Nanum+Brush+Script&display=swap" rel="stylesheet">
<style id="ud-aurora-global-v1">
/* ud-aurora-global-v1 — 전 페이지 오로라 팔레트 통일 (build_site 주입 · 멱등)
   토큰 리맵이므로 각 페이지 기존 컴포넌트가 자동으로 새 팔레트를 입는다 */
:root, [data-theme="light"] {
  --bg:#FBFBFC; --bg-card:#F5F5F7; --bg-hover:#ECECF0;
  --text:#16181D; --text-secondary:#4A4E58; --text-muted:#7E828B;
  --gold:#565CE8; --gold-bg:rgba(86,92,232,0.08); --aurora2:#3EC8D8;
  --line:#E8E9EC; --line-strong:#D8DAE0; --up:#565CE8;
  --header-bg:rgba(251,251,252,0.92);
  --tab-active-bg:var(--text); --tab-active-text:var(--bg);
  --toggle-icon-color:var(--text);
}
[data-theme="dark"] {
  --bg:#0A0B0F; --bg-card:#12141B; --bg-hover:#191C25;
  --text:#F4F5F8; --text-secondary:#C3C7D1; --text-muted:#7C8290;
  --gold:#8B93FF; --gold-bg:rgba(139,147,255,0.10); --aurora2:#4FE0EF;
  --line:#1E2027; --line-strong:#2A2D38; --up:#8B93FF;
  --header-bg:rgba(10,11,15,0.92);
  --tab-active-bg:var(--gold); --tab-active-text:var(--bg);
  --toggle-icon-color:var(--gold);
}
/* ── 정렬 통일: 헤더·메뉴·본문을 같은 1080px 기둥에 ── */
.site-header-inner{max-width:1080px !important;margin:0 auto !important;padding:14px 20px !important;display:flex;align-items:center;justify-content:space-between;gap:12px}
.site-nav-inner{max-width:1080px !important;margin:0 auto !important;padding:0 20px !important;justify-content:space-between}
/* 첫/끝 탭의 내부 패딩만큼 광학 보정 — 탭 '글자'가 로고 좌변·캡슐 우변과 정렬 */
.site-nav-inner .nav-tab:first-child{margin-left:-14px}
.site-nav-inner .nav-tab:last-child{margin-right:-14px}
@media(max-width:860px){.site-nav-inner{justify-content:flex-start}.site-nav-inner .nav-tab:first-child,.site-nav-inner .nav-tab:last-child{margin:0}}
.container{max-width:1080px !important}

/* 메뉴 하단 헤어라인: 골드 → 오로라 */
.site-nav::after{background:linear-gradient(90deg,transparent 8%,rgba(86,92,232,.35),rgba(62,200,216,.35),transparent 92%) !important}
[data-theme="dark"] .site-nav::after{background:linear-gradient(90deg,transparent 8%,rgba(139,147,255,.4),rgba(79,224,239,.4),transparent 92%) !important}

/* ── ud-accordion-v1 — 목록형 아코디언 공용 문법 ────────────────────────────
   동행 관측 에세이(journal)와 관측일지 칼럼(observatory)이 **같은 구조**를 쓴다.
   같은 것을 두 벌 구현하면 반드시 갈라지므로 구조·동작은 여기 한 곳에만 둔다.
   각 페이지는 제목 크기 같은 '자기 눈금'만 따로 준다.

   여백·모서리·스크롤 여유는 필독 해부실 본문 카드(.dx-card)와 같은 값을 쓴다
   — 같은 사이트의 '읽는 카드'는 한 톤이어야 한다(2026-08-24 실측 대조 후 정렬).

   ⚠️ 펼침에 max-height 전환을 쓰지 않는다: 본문에 차트 SVG 가 들어 있어 접힌
   상태의 높이를 잴 수 없다(0 이나 잘린 값이 나온다). 애니메이션 한 번 보자고
   근거가 되는 그림이 깨지는 쪽을 택하지 않는다 — 상태 변화는 캐럿 회전이 알린다. */
.ud-acc{background:var(--bg-card);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;scroll-margin-top:90px;overflow:hidden}
.ud-acc-head{display:flex;align-items:center;gap:12px;width:100%;padding:14px 18px;background:none;border:none;text-align:left;cursor:pointer;font-family:inherit;color:inherit;transition:background .15s}
.ud-acc-head:hover{background:var(--bg)}
.ud-acc.open>.ud-acc-head{border-bottom:1px solid var(--line)}
.ud-acc-meta{display:flex;align-items:center;gap:9px;flex-shrink:0;font-size:12.5px;color:var(--text-muted)}
.ud-acc-title{flex:1;min-width:0;font-weight:800;letter-spacing:-0.03em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ud-acc-caret{margin-left:auto;flex-shrink:0;color:var(--text-muted);font-size:12.5px;transition:transform .15s}
.ud-acc.open .ud-acc-caret{transform:rotate(90deg)}
.ud-acc-detail{display:none;padding:22px 24px}
.ud-acc.open>.ud-acc-detail{display:block}
@media(max-width:480px){.ud-acc-head{gap:8px;padding:12px 14px}.ud-acc-detail{padding:18px 16px}}

/* ── ud-pin-v1 — 📌 고정 공지 카드 공용 문법 ────────────────────────────────
   관제센터 공지(.post.pinned)에서 뽑아 올렸다. 관측노트의 논제 원장이 같은
   '맨 위에 못 박힌 것' 이라는 뜻을 쓰므로 같은 표식을 써야 한 사이트로 읽힌다. */
.ud-pin{border-color:var(--gold) !important;border-width:1.5px !important}
.ud-pin-badge{display:inline-flex;align-items:center;flex-shrink:0;font-size:11px;font-weight:800;border-radius:100px;padding:2px 9px;background:var(--gold);color:#fff}
/* 판단층이 바뀐 사실은 눈에 띄어야 한다 — 갱신 7일 이내면 배지에 점이 붙는다 */
.ud-new-dot::after{content:'';display:inline-block;width:6px;height:6px;margin-left:6px;border-radius:50%;background:currentColor;animation:ud-new-pulse 2s ease-in-out infinite;vertical-align:middle}
@keyframes ud-new-pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ── ud-index-v1 — 목록 앞의 목차(연표) 공용 문법 ──────────────────────────
   필독 해부실(.dx-index)에서 뽑아 올렸다. 관측노트 동행 탭의 에세이 목차가
   같은 일을 하므로 같은 문법을 쓴다 — 헤어라인 행, 골드 대문자 소제목.
   행의 배치(block / flex)는 페이지가 정한다: 해부실은 제목이 길어 줄바꿈이 맞고,
   에세이 목차는 날짜·판정·제목을 한 줄에 세우는 편이 연표로 읽힌다.
   ⚠️ 이 블록은 head 끝에 나중에 주입된다 — 같은 특이도면 여기가 이긴다.
   페이지에서 덮어쓰려면 특이도를 한 단 올려라(#id 등). */
.ud-index{background:var(--bg-card);border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:18px}
.ud-index-title{font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);margin-bottom:10px}
.ud-index-row{display:block;width:100%;padding:7px 0;border:0;border-top:1px solid var(--line);background:none;text-align:left;font-family:inherit;font-size:13.5px;font-weight:600;color:var(--text);text-decoration:none;line-height:1.45;cursor:pointer}
.ud-index-row:first-of-type{border-top:0}
.ud-index-row:hover{color:var(--gold)}
.ud-index-date{color:var(--text-muted);font-weight:700;font-size:12px;margin-right:8px;font-variant-numeric:tabular-nums}
</style>
"""


def inject_aurora_tokens():
    """전 페이지 </head> 직전에 오로라 토큰 주입 (마지막 주입 = 캐스케이드 승리, 멱등)"""
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "ud-aurora-global-v1" in html or "</head>" not in html:
            continue
        html = html.replace("</head>", AURORA_GLOBAL_CSS + "\n</head>", 1)
        _w(f, html); n += 1
    print(f"[OK] 오로라 팔레트 주입: {n}개 페이지")


def verify_pages():
    """산출물 검사 — 주입이 빠진 페이지가 하나라도 있으면 **시끄럽게** 죽는다.

    2026-08-24: 빌드가 두 번 조용히 중간에 멈춰(헤드리스 크롬이 파일을 물고 있는
    동안 쓰기가 막힌 것으로 추정) 오로라·공용 CSS 가 빠진 observatory.html 이 남았다.
    페이지는 열리고 대부분 정상으로 보이는데 공용 컴포넌트만 민무늬가 된다 —
    그 상태로 검증하면 '스타일이 안 먹는다'는 엉뚱한 결론에 이른다(실제로 두 번 겪었다).
    빌드가 끝났다고 말하려면 산출물이 정말 그렇게 되었는지 확인하고 말해야 한다.
    """
    bad = []
    for lang in i18n.LANGS:
      for page in ALL_PAGES:
        name = i18n.subdir(lang) + page
        f = OUT_DIR / name
        if not f.exists():
            bad.append(f"{name}: 파일 없음")
            continue
        html = f.read_text(encoding="utf-8")
        if "ud-aurora-global-v1" not in html:
            bad.append(f"{name}: 오로라 주입 누락")
            continue
        if I18N_MARK not in html or I18N_HEAD_MARK not in html:
            bad.append(f"{name}: 다국어 주입 누락")
            continue
        if "{{t:" in html:
            bad.append(f"{name}: 카탈로그 자리표시자 미치환")
            continue
        head = html[:html.find("</head>")]
        i = head.find("<style")
        if i >= 0 and "<style" in head[i + 6:head.find("</style>")]:
            bad.append(f"{name}: <style> 블록 오염(주입 지점 침범)")
    if bad:
        print("[FAIL] 산출물 검사 실패 — 빌드를 신뢰할 수 없습니다:", file=sys.stderr)
        for b in bad:
            print(f"    · {b}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] 산출물 검사: {len(i18n.LANGS)}개 언어 × {len(ALL_PAGES)}개 페이지 주입·구조 정상")


HEADER_FIX_CSS = """<style>
/* 헤더 모바일 일관성 + 실시간 날짜·시계 (전 페이지 공통 — build_site.py 주입) */
@media (max-width: 480px) {
  .site-header-inner { flex-wrap: wrap; }
  .brand { flex-shrink: 0; }
}
.update-badge .ud-clock { margin-left: 6px; font-variant-numeric: tabular-nums; letter-spacing: 0.02em; }
/* UNIVERTRIX 로고 (build_site.py 주입) */
.brand.ud-logo { display: inline-flex; align-items: center; gap: 10px; text-decoration: none; color: inherit; }
/* ── UNIVERTRIX 종합 로고 (오로라×궤도×호흡×서예) ── */
.uvx{display:inline-flex;align-items:center;gap:13px;text-decoration:none}
.uvx-lat{position:relative;display:inline-block}
.uvx-word{font-size:20px;letter-spacing:.3em;font-weight:400;line-height:1;
  background:linear-gradient(95deg,var(--text) 12%,var(--gold) 46%,var(--aurora2) 62%,var(--text) 92%);
  -webkit-background-clip:text;background-clip:text;color:transparent;
  font-variation-settings:'wght' 528}
.uvx-orbit{position:absolute;inset:-12px -11px -8px -7px;width:calc(100% + 18px);height:calc(100% + 20px);pointer-events:none;overflow:visible}
.uvx-orbit path{stroke:var(--gold);opacity:.9}
.uvx-orbit circle{fill:var(--aurora2)}
.uvx-kr{font-family:'Nanum Brush Script',cursive;font-size:26px;line-height:1;padding-left:13px;border-left:1px solid var(--text-muted);white-space:nowrap;
  background:linear-gradient(95deg,var(--text) 10%,var(--gold) 42%,var(--aurora2) 60%,var(--text) 90%);background-size:220% 100%;
  -webkit-background-clip:text;background-clip:text;color:transparent}
@media(prefers-reduced-motion:no-preference){
  .uvx-word{animation:uvbreath 6s ease-in-out infinite}
  .uvx-kr{animation:krbreath 6s ease-in-out infinite}
  @keyframes uvbreath{0%,100%{font-variation-settings:'wght' 340}50%{font-variation-settings:'wght' 800}}
  @keyframes krbreath{0%,100%{background-position:0% 0}50%{background-position:100% 0}}
}
[data-theme="dark"] .uvx-word{filter:drop-shadow(0 0 13px rgba(139,147,255,.4))}
[data-theme="dark"] .uvx-kr{filter:drop-shadow(0 0 11px rgba(139,147,255,.35))}
@media(max-width:760px){.uvx{gap:9px}.uvx-word{font-size:15.5px;letter-spacing:.22em}.uvx-kr{font-size:20px;padding-left:9px}.uvx-orbit{inset:-9px -8px -6px -5px}}
.ud-logo-word .v { color: var(--gold); }
@media (max-width: 480px) { .ud-logo-word { font-size: 13.5px; letter-spacing: 0.16em; } .ud-logo-mark { height: 22px; } }

/* ud-hdr-refine-v2 — 상태 스트립 통합: 배지 3개 → 계기판 캡슐 하나 */
.site-header-inner { padding: 15px 20px; }
.header-right {
  gap: 0; background: var(--bg-card); border: 1px solid var(--line);
  border-radius: 100px; padding: 3px 5px 3px 4px; align-items: center;
  box-shadow: 0 1px 2px rgba(28,35,51,0.04);
}
.header-right .update-badge {
  background: transparent; border: none; border-radius: 0;
  padding: 5px 13px; color: var(--text-secondary);
}
[data-theme="dark"] .header-right .update-badge { border: none; }
.header-right .update-badge .ud-clock { color: var(--gold); font-weight: 800; }
.header-right .rate-badge {
  background: transparent; border: none; border-radius: 0;
  border-left: 1px solid var(--line); padding: 5px 13px;
}
.header-right .rate-badge:hover { border-color: var(--line); }
.header-right .rate-badge:hover #usdKrwDisplay, .header-right .rate-badge:hover .rate-badge-arrow { color: var(--gold); }
.header-right .theme-toggle {
  width: 34px; height: 30px; border: none; border-radius: 0 100px 100px 0;
  border-left: 1px solid var(--line); background: transparent; margin-left: 0;
}
.header-right .theme-toggle:hover { color: var(--gold); }
@media (max-width: 480px) {
  .header-right { padding: 2px 3px 2px 2px; }
  .header-right .update-badge, .header-right .rate-badge { padding: 4px 9px; }
}

/* ud-hdr-refine-v4 — 아이보리 복원: 헤더는 본문과 한 몸, 세련됨은 디테일에서.
   (한때 페이지에 주입됐던 v3 네이비 블록을 아래 리셋이 캐스케이드로 무력화한다) */
.site-header { background: var(--header-bg); border-bottom: none; position: relative; }
.site-header::after { content: none; }
.site-header .ud-logo, .site-header .ud-logo-word { color: var(--text); }
.site-header .ud-logo-word { letter-spacing: 0.2em; }
.site-header .ud-logo-word .v { color: var(--gold); }
.site-header .ud-logo-mark { --gold: #9C7A3A; }
[data-theme="dark"] .site-header .ud-logo-mark { --gold: #D4A658; }
.site-header .header-right {
  background: var(--bg-card); border-color: var(--line);
  box-shadow: 0 1px 2px rgba(28,35,51,0.05);
}
.site-header .update-badge, .site-header .update-badge .ud-date { color: var(--text-secondary); }
.site-header .update-badge .ud-clock { color: var(--gold); }
.site-header .update-badge::before { background: var(--gold); box-shadow: none; }
[data-theme="dark"] .site-header .update-badge::before { box-shadow: 0 0 8px var(--gold); }
.site-header .rate-badge { color: var(--text-secondary); border-left-color: var(--line); }
.site-header .rate-badge #usdKrwDisplay { color: var(--text); }
.site-header .rate-badge:hover #usdKrwDisplay, .site-header .rate-badge:hover .rate-badge-arrow { color: var(--gold); }
.site-header .theme-toggle { color: var(--text-secondary); border-left-color: var(--line); }
.site-header .theme-toggle:hover { color: var(--gold); }
/* 헤더+메뉴 = 하나의 아이보리 존, 경계는 메뉴 아래 은은한 골드 헤어라인 한 줄 */
.site-nav { border-bottom: 1px solid var(--line); }
.site-nav::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent 8%, rgba(156,122,58,0.38), transparent 92%);
}
[data-theme="dark"] .site-nav::after {
  background: linear-gradient(90deg, transparent 8%, rgba(212,166,88,0.3), transparent 92%);
}
</style>
<script>
document.addEventListener('DOMContentLoaded', function () {
  var badge = document.querySelector('.update-badge');
  if (!badge) return;
  badge.innerHTML = '<span class="ud-date"></span><span class="ud-clock"></span>';
  var dt = badge.querySelector('.ud-date');
  var clk = badge.querySelector('.ud-clock');
  // 2026-09-26 폭 절약: 날짜는 MM.DD, 시계는 HH:MM(초 없음) → 30초마다 갱신이면 충분하다
  function tick() {
    var now = new Date();
    dt.textContent = now.toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' }).slice(5).replace('-', '.');
    clk.textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit',
                                                      hour12: false, timeZone: 'Asia/Seoul' });
  }
  tick();
  setInterval(tick, 30000);
});
</script>"""


# ── 실시간 접속자 카운터 (Firebase 프레즌스) ──
# 방문자마다 익명 하트비트를 남기고, 최근 2분 내 신호 수를 푸터에 표시.
# firebase_config.json이 비어 있으면 아무것도 표시하지 않는다 (우아한 대기).
PRESENCE_SNIPPET = """<!-- uv-presence -->
<script type="module">
(async () => {
  try {
    const cfg = await (await fetch('data/firebase_config.json?v=' + Date.now())).json();
    if (!cfg || !cfg.apiKey) return;
    const { initializeApp } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js');
    const { getFirestore, doc, setDoc, collection, query, where, Timestamp, serverTimestamp, getCountFromServer } =
      await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js');
    const db = getFirestore(initializeApp(cfg, 'presence'));

    let pid = localStorage.getItem('uv_pid');
    if (!pid) { pid = 'p' + Math.random().toString(36).slice(2, 12) + Date.now().toString(36); localStorage.setItem('uv_pid', pid); }

    const beat = () => setDoc(doc(db, 'presence', pid), { t: serverTimestamp() }).catch(() => {});
    const count = async () => {
      try {
        const cutoff = Timestamp.fromMillis(Date.now() - 2 * 60 * 1000);
        const snap = await getCountFromServer(query(collection(db, 'presence'), where('t', '>', cutoff)));
        const n = snap.data().count;
        if (n > 0) {
          let el = document.getElementById('uvPresence');
          if (!el) {
            const ft = document.querySelector('.site-footer') || document.querySelector('footer');
            if (!ft) return;
            el = document.createElement('div');
            el.id = 'uvPresence';
            el.style.cssText = 'margin-top:8px;font-size:12px;color:var(--text-muted,#8a8577);';
            ft.appendChild(el);
          }
          el.innerHTML = '<span style="color:var(--gold,#9C7A3A)">\u2726</span> ' + T('common.presence', {n: n});
        }
      } catch (e) {}
    };
    await beat(); await count();
    setInterval(beat, 60 * 1000 + Math.floor(Math.random() * 5000));
    setInterval(count, 60 * 1000 + Math.floor(Math.random() * 5000));
  } catch (e) {}
})();
</script>"""


def inject_presence():
    """전 페이지 </body> 직전에 접속자 카운터 스니펫 주입 (멱등)"""
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "uv-presence" in html or "</body>" not in html:
            continue
        html = html.replace("</body>", PRESENCE_SNIPPET + "\n</body>", 1)
        _w(f, html)
        n += 1
    print(f"[OK] 접속자 카운터 주입: {n}개 페이지")


def inject_header_fix():
    """생성된 모든 페이지 헤더에 동일한 반응형 규칙 주입 (중복 방지)"""
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "ud-hdr-refine-v4" in html or "</head>" not in html:
            continue
        # 브랜드 → UNIVERTRIX 로고 교체 (CSS 주입 전에 — 멱등 체크는 마크업 기준)
        if 'class="brand ud-logo"' not in html:
            html = re.sub(
                r'<div class="brand">.*?</div>',
                lambda m: logo_html(LANG),
                html, count=1, flags=re.S,
            )
        # 푸터 브랜드도 통일
        html = html.replace("✨ Stay hungry. ASI",
                            'UNI<span style="color:var(--gold)">V</span>ERTRIX')
        html = html.replace("</head>", HEADER_FIX_CSS + "\n</head>", 1)
        _w(f, html)
        n += 1
    print(f"[OK] 헤더 일관성 CSS 주입: {n}개 페이지")


# ── 헤더 시장 지표 띠 (2026-09-26) ─────────────────────────────────────
# USD/KRW 배지 옆에 4종. **정적 렌더** — 값은 빌드 시점에 latest.json meta.macro 에서
# 박아 넣는다(브라우저는 아무것도 부르지 않는다). 12페이지 전부에 같은 규격으로 나가야
# 하므로 페이지별 복붙이 아니라 ALL_PAGES 를 도는 공용 주입으로 둔다.
# 순서는 선장님 지정: USD/KRW · WTI · 미10년 · 미30년 · USD/JPY
MACRO_MARK = "ud-macro-v1"
MACRO_BADGES = (
    # (원장 키, 화면 라벨, 원본 링크, 표기 방식)
    ("wti", "WTI", "https://fred.stlouisfed.org/series/DCOILWTICO", "usd"),
    ("ust10", "미10년", "https://fred.stlouisfed.org/series/DGS10", "pct"),
    ("ust30", "미30년", "https://fred.stlouisfed.org/series/DGS30", "pct"),
    ("usd_jpy", "USD/JPY",
     "https://finance.naver.com/marketindex/worldExchangeDetail.naver?marketindexCd=FX_USDJPY",
     "fx"),
)
MACRO_CSS = """<style>
/* ud-macro-v1 — 헤더 시장 지표 띠. 배지는 USD/KRW 와 같은 규격(rate-badge)을 쓴다.
   좁은 폭에서는 줄바꿈하지 않고 띠 안에서 가로 스크롤한다(판형 전역 정리는 이월 건).
   header-right 에는 줄어들 수 있게 하는 최소 속성만 준다 — 레이아웃을 새로 짜지 않는다. */
.site-header .header-right { min-width: 0; max-width: 100%; flex-shrink: 1; flex-wrap: nowrap; }
.ud-macro { display: inline-flex; align-items: center; flex-wrap: nowrap; min-width: 0;
  max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain;
  scrollbar-width: none; -webkit-overflow-scrolling: touch; }
.ud-macro::-webkit-scrollbar { display: none; }
.ud-macro .rate-badge { flex: 0 0 auto; white-space: nowrap; }
/* 띠 안 배지 좌우 여백 13→11px (1280px 여유 확보). 480px 이하 규칙(9px)은 건드리지 않는다 */
@media (min-width: 481px) {
  .site-header .header-right .ud-macro .rate-badge { padding-left: 11px; padding-right: 11px; }
}
.rate-badge .ud-mv { color: var(--text); font-weight: 800; }
.rate-badge:hover .ud-mv { color: var(--gold); }
</style>"""


def _macro_value(v, fmt):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"                      # 결측은 대시 — 0 으로 흘려보내지 않는다
    if fmt == "pct":
        return f"{v:.2f}%"
    if fmt == "usd":
        return f"${v:,.2f}"
    if fmt == "usd0":                   # 헤더 전용 — 정수 달러($72). 폭 여유 확보
        return f"${v:,.0f}"
    if fmt == "fx1":                    # 헤더 전용 — 소수 1자리(148.3)
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def _macro_title(e, today):
    """title — '측정 HH:MM · 출처 …'. **상태에서 만든다**(원장 키·outcome 은 쓰지 않는다)."""
    e = e if isinstance(e, dict) else {}
    ts = str(e.get("measured_at") or "")
    if not ts or e.get("value") is None:
        return i18n.t(LANG, "macro.none")
    when = ts[11:16] if ts[:10] == (today or "") else f"{ts[5:10]} {ts[11:16]}"
    t = i18n.t(LANG, "macro.measured", when=when, src=e.get("source") or "—")
    if e.get("carried"):
        t += i18n.t(LANG, "macro.carried")
    return t


def _macro_label(key, label, e, today):
    """WTI 는 전일 종가 시리즈 — 관측일이 측정일보다 이르면 '(전일)' 을 붙인다(상태에서 생성)."""
    e = e if isinstance(e, dict) else {}
    measured = str(e.get("measured_at") or "")[:10]
    if key == "wti" and e.get("value") is not None and e.get("as_of") \
            and str(e["as_of"])[:10] < measured:
        return label + i18n.t(LANG, "macro.prev_day")
    return label


# 헤더 띠 전용 축약 라벨(2026-09-26 폭 절약 — 1280px 에서 5개가 스크롤 없이 보이게).
# 전체 이름은 title 로 옮긴다. 브리핑은 폭 제약이 없으므로 MACRO_BADGES 의 전체 라벨을 쓴다.
MACRO_SHORT = {"usd_krw": "KRW", "wti": "WTI", "ust10": "10Y", "ust30": "30Y", "usd_jpy": "JPY"}
# 헤더 띠 전용 숫자 폭 — WTI 정수($72) · JPY 소수 1자리(148.3). 100달러·자릿수 변화에도
# 1280px 여유가 남게(2026-09-26). 브리핑은 MACRO_BADGES 의 표기(소수 2자리)를 그대로 쓴다.
MACRO_HEADER_FMT = {"wti": "usd0", "usd_jpy": "fx1"}
def _macro_full(key, label=""):
    """지표 전체 이름 — 카탈로그(macro.full.<원장 키>)."""
    k = "macro.full." + key
    return i18n.t(LANG, k) if k in i18n.catalog(i18n.DEFAULT) else label


def _macro_full_title(key, label, e, today):
    """title — 전체 이름 + (WTI 전일 종가 여부) + 측정 시각·출처. 모두 상태에서 만든다."""
    head = _macro_full(key, label)
    if _macro_label(key, label, e, today) != label:       # 관측일 < 측정일 → 전일 종가
        head += i18n.t(LANG, "macro.prev_close")
    return f"{head} · {_macro_title(e, today)}"


def macro_badges_html(macro, today):
    """USD/KRW 옆 4개 배지 HTML — 순수 함수. 화면엔 축약 라벨, 전체 이름은 title."""
    macro = macro if isinstance(macro, dict) else {}
    out = []
    for key, label, url, fmt in MACRO_BADGES:
        e = macro.get(key) if isinstance(macro.get(key), dict) else {}
        out.append(
            f'<a href="{url}" target="_blank" rel="noopener" class="rate-badge ud-mb" '
            f'data-mk="{key}" title="{html_mod.escape(_macro_full_title(key, label, e, today))}">'
            f'{MACRO_SHORT.get(key, label)} '
            f'<span class="ud-mv">{_macro_value(e.get("value"), MACRO_HEADER_FMT.get(key, fmt))}</span>'
            f'<span class="rate-badge-arrow" aria-hidden="true">↗</span></a>')
    return "".join(out)


_RATE_BADGE_RE = re.compile(r'<a[ ][^>]*class="rate-badge"[^>]*>.*?</a>', re.S)


def macro_wrap(page, macro, today):
    """USD/KRW 배지를 띠로 감싸고 4개를 잇는다 → (새 HTML, 성공 여부). 순수 함수."""
    m = _RATE_BADGE_RE.search(page)
    if not m:
        return page, False
    krw = m.group(0)
    e = (macro or {}).get("usd_krw") if isinstance(macro, dict) else None
    title = _macro_full("usd_krw") + " · " + i18n.t(LANG, "common.naver_fx")
    if isinstance(e, dict) and e.get("value") is not None:
        title = (_macro_full("usd_krw") + " · " + _macro_title(e, today) + " · "
                 + i18n.t(LANG, "common.naver_fx"))
    krw = krw.replace('class="rate-badge"', 'class="rate-badge" data-mk="usd_krw"', 1)
    krw = re.sub(r'title="[^"]*"', lambda _m: f'title="{html_mod.escape(title)}"', krw, count=1)
    # 화면 라벨만 축약 — 템플릿의 'USD/KRW ' 텍스트(값 span 앞)를 'KRW ' 로
    krw = re.sub(r'>(\s*)USD/KRW(\s*)<span', lambda _m: f">{_m.group(1)}{MACRO_SHORT['usd_krw']}"
                 f"{_m.group(2)}<span", krw, count=1)
    band = (f'<span class="ud-macro" data-band="{MACRO_MARK}">' + krw
            + macro_badges_html(macro, today) + "</span>")
    return page[:m.start()] + band + page[m.end():], True


def inject_macro_badges():
    """전 페이지 헤더에 시장 지표 띠 주입 (멱등 — 마커 검사 후 1회)."""
    meta = _header_meta()
    macro, today = meta.get("macro") or {}, meta.get("fetched_date") or ""
    pages = ALL_PAGES
    n, miss = 0, []
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if MACRO_MARK in html or "</head>" not in html:
            continue
        html, ok = macro_wrap(html, macro, today)
        if not ok:
            miss.append(name)
            continue
        html = html.replace("</head>", MACRO_CSS + chr(10) + "</head>", 1)
        _w(f, html)
        n += 1
    if miss:
        print(f"[warn] 시장 지표 띠 — USD/KRW 배지를 못 찾은 페이지: {miss}", file=sys.stderr)
    print(f"[OK] 시장 지표 띠 주입: {n}개 페이지")



# ── 4개 언어 관측소 (2026-10-26 UNIVERTRIX GLOBAL) ─────────────────────────────
# 빌드는 LANGS × ALL_PAGES. ko 는 루트(주소 불변 — 텔레그램 링크 보호), en/es/ja 는 하위 폴더.
# 화면 문구는 카탈로그(data/i18n/{lang}.json)에서, 숫자는 같은 데이터에서(G2).
# 주입 마커 멱등성은 언어별 산출물마다 — 각 언어 폴더의 페이지가 각자 마커를 1회 갖는다.
I18N_MARK = "uvx-i18n-v1"          # head 첫머리의 런타임(T·UVX) — i18n.runtime_js 가 id 로 단다
I18N_HEAD_MARK = "uvx-hreflang-v1"   # hreflang·canonical·og·서체·언어 CSS

# 활성 탭 — 이력 페이지는 부모 탭, 소개·정책은 없음
NAV = (("index.html", "nav.home"), ("latent.html", "nav.latent"), ("megatrend.html", "nav.mega"),
       ("research.html", "nav.research"), ("observatory.html", "nav.observatory"),
       ("community.html", "nav.community"), ("journal.html", "nav.journal"),
       ("pioneers.html", "nav.pioneers"))
NAV_ACTIVE = {"history-top20.html": "index.html", "history-latent.html": "latent.html"}

I18N_CSS = """<style id="uvx-i18n-css">
/* uvx-i18n — 언어 전환기 · 첫 방문 언어 안내 띠 · 원문(번역 준비 중) 표식. 기존 토큰만 쓴다 */
.uvx-lang{position:relative;display:inline-flex;align-items:center;border-left:1px solid var(--line)}
.uvx-lang>summary{list-style:none;cursor:pointer;padding:5px 11px;font-size:12px;font-weight:800;letter-spacing:.06em;color:var(--text-secondary);white-space:nowrap}
.uvx-lang>summary::-webkit-details-marker{display:none}
.uvx-lang>summary:hover,.uvx-lang[open]>summary{color:var(--gold)}
.uvx-lang-menu{position:absolute;right:0;top:calc(100% + 8px);z-index:80;min-width:150px;background:var(--bg-card);border:1px solid var(--line-strong);border-radius:12px;padding:6px;box-shadow:0 12px 30px rgba(0,0,0,.18)}
.uvx-lang-menu a{display:flex;justify-content:space-between;gap:10px;padding:7px 10px;border-radius:8px;font-size:13px;font-weight:700;color:var(--text);text-decoration:none}
.uvx-lang-menu a:hover{background:var(--gold-bg);color:var(--gold)}
.uvx-lang-menu a[aria-current="true"]{color:var(--gold)}
.uvx-lang-menu a small{color:var(--text-muted);font-weight:800;letter-spacing:.06em}
.uvx-band{display:flex;align-items:center;justify-content:center;gap:12px;padding:7px 16px;background:var(--gold-bg);border-bottom:1px solid var(--line);font-size:13px;font-weight:700;color:var(--text)}
.uvx-band a{color:var(--gold);text-decoration:none;font-weight:800}
.uvx-band button{border:0;background:none;color:var(--text-muted);font:inherit;font-size:15px;cursor:pointer;padding:0 4px}
.uvx-pending{display:block;width:fit-content;margin:0 0 8px;font-size:11px;font-weight:800;letter-spacing:.04em;color:var(--text-muted);border:1px dashed var(--line-strong);border-radius:100px;padding:2px 9px}
.uvx-pagepending{max-width:1080px;margin:10px auto 0;padding:0 20px;font-size:12.5px;font-weight:700;color:var(--text-muted)}
/* 로고 — 붓글씨 '우주지배자'는 ko 전용. 다른 언어는 같은 자리에 언어 부제(오로라 광 스윕 그대로) */
.uvx-kr.uvx-sub{font-family:inherit;font-size:13.5px;font-weight:300;letter-spacing:.08em}
@media(max-width:760px){.uvx-kr.uvx-sub{font-size:11px;letter-spacing:.04em}}
/* 서체 — Pretendard 단일 + ja 는 Noto Sans JP 폴백 (위계는 굵기) */
html[lang="ja"] body{font-family:'Pretendard Variable','Pretendard','Noto Sans JP',-apple-system,sans-serif}
</style>"""

# 첫 방문 언어 안내 띠 — 브라우저 언어가 페이지 언어와 다르면 작은 띠 하나. 강제 이동 없음(G1).
# 닫으면 다시 안 뜬다(localStorage, try/catch). 문구는 **대상 언어로** — 그 언어 독자에게 말한다.
BAND_JS = r"""<script id="uvx-band">
(function(){
  try {
    var LANGS = __LANGS__, cur = document.documentElement.lang, K = 'uvx-band-off';
    try { if (localStorage.getItem(K)) return; } catch (e) {}
    var want = ((navigator.languages && navigator.languages[0]) || navigator.language || '').slice(0, 2).toLowerCase();
    if (!want || want === cur || !LANGS[want]) return;
    var a = document.querySelector('.uvx-lang-menu a[hreflang="' + want + '"]');
    if (!a) return;
    var b = document.createElement('div');
    b.className = 'uvx-band'; b.setAttribute('lang', want);
    var link = document.createElement('a');
    link.href = a.getAttribute('href') + location.hash; link.textContent = LANGS[want].go;
    var x = document.createElement('button'); x.type = 'button'; x.textContent = '×';
    x.setAttribute('aria-label', LANGS[want].close);
    x.onclick = function(){ try { localStorage.setItem(K, '1'); } catch (e) {} b.remove(); };
    b.appendChild(link); b.appendChild(x);
    document.body.insertBefore(b, document.body.firstChild);
  } catch (e) {}
})();
/* 언어 전환기 — 같은 페이지 · 같은 해시로 */
document.addEventListener('click', function(ev){
  var a = ev.target.closest && ev.target.closest('.uvx-lang-menu a');
  if (a && location.hash) { ev.preventDefault(); location.href = a.getAttribute('href') + location.hash; }
});
</script>"""


def lang_switcher(lang, page):
    """헤더 계기판 캡슐의 언어 전환기 — <details> 라 JS 없이도 열리고 링크로 이동한다."""
    rows = []
    for L2 in i18n.LANGS:
        cur = ' aria-current="true"' if L2 == lang else ""
        rows.append(f'<a href="{i18n.href_to(lang, L2, page)}" hreflang="{L2}" lang="{L2}"{cur}>'
                    f'{i18n.SELF_NAME[L2]}<small>{i18n.SHORT[L2]}</small></a>')
    return (f'<details class="uvx-lang"><summary aria-label="{html_mod.escape(i18n.t(lang, "common.lang"))}">'
            f'{i18n.SHORT[lang]}</summary><div class="uvx-lang-menu">{"".join(rows)}</div></details>')


def head_links(lang, page):
    """hreflang 4개 + x-default + canonical + og:locale(+대체) · ja 서체."""
    done = page in i18n.DONE_PAGES
    out = [f'<link rel="canonical" href="{i18n.canonical(lang, page)}">']
    if done or lang == i18n.DEFAULT:
        for L2 in i18n.LANGS:
            if done or L2 == i18n.DEFAULT:
                out.append(f'<link rel="alternate" hreflang="{L2}" href="{i18n.canonical(L2, page)}">')
        if done:
            out.append(f'<link rel="alternate" hreflang="x-default" href="{i18n.canonical(i18n.X_DEFAULT, page)}">')
    out.append(f'<meta property="og:locale" content="{i18n.OG_LOCALE[lang]}">')
    if done:
        out += [f'<meta property="og:locale:alternate" content="{i18n.OG_LOCALE[L2]}">'
                for L2 in i18n.LANGS if L2 != lang]
    out.append(f'<meta property="og:url" content="{i18n.canonical(lang, page)}">')
    if lang != i18n.DEFAULT and not done:
        out.append('<meta name="robots" content="noindex">')     # 본문 번역 전 — 색인하지 않는다
    if lang == "ja":
        out.append('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@100..900&display=swap">')
    return "\n".join(out)


_ASSET_REF = re.compile(r"""(?<=["'(])(assets|data)/""")


def relocate(html, lang):
    """하위 폴더(/en/ 등) 산출물의 루트 자원 참조(assets/·data/)를 한 단 위로. 페이지 간 링크는 같은 폴더라 그대로."""
    if lang == i18n.DEFAULT:
        return html
    return _ASSET_REF.sub(lambda m: "../" + m.group(1) + "/", html)


def rates_for_runtime():
    """관측 위치 렌즈 환율 — 빌드 시점에 심는다(클라이언트 fetch 없음). 1 USD 당 현지 통화."""
    m = (_header_meta().get("macro") or {})
    r = {"USD": 1}
    for key, ccy in (("usd_krw", "KRW"), ("usd_jpy", "JPY")):
        v = (m.get(key) or {}).get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            r[ccy] = v
    return r


def inject_i18n():
    """전 페이지 마지막 주입(멱등) — {{t:키}} 치환 · <html lang> · head 런타임 · hreflang · 전환기 · 경로.

    런타임에 싣는 키는 **완성된 페이지**에서 센다(앞선 주입이 넣은 T() 호출까지) — 그래서 마지막에 돈다.
    """
    pages = ALL_PAGES
    n = 0
    rates = rates_for_runtime()
    band_langs = {L2: {"go": i18n.t(L2, "common.band_go"), "close": i18n.t(L2, "common.close")}
                  for L2 in i18n.LANGS}
    for name in pages:
        f = OUT_DIR / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if I18N_HEAD_MARK in html or "</head>" not in html:
            continue
        html = i18n.render(html, LANG)
        html = re.sub(r'<html lang="[a-z]+"', f'<html lang="{i18n.HTML_LANG[LANG]}"', html, count=1)
        # 전환기 — 계기판 캡슐의 테마 토글 앞
        if 'class="theme-toggle"' in html:
            i = html.find('<button class="theme-toggle"')
            if i >= 0:
                html = html[:i] + lang_switcher(LANG, name) + html[i:]
        # 본문 번역 전 페이지(다른 언어) — 메뉴 바로 아래 한 줄
        if LANG != i18n.DEFAULT and name not in i18n.DONE_PAGES and "</nav>" in html:
            html = html.replace("</nav>", "</nav>\n" + f'<div class="uvx-pagepending">'
                                f'{html_mod.escape(i18n.t(LANG, "common.page_pending"))}</div>', 1)
        html = html.replace("</head>", f'<!-- {I18N_HEAD_MARK} -->\n' + head_links(LANG, name) + "\n"
                            + I18N_CSS + "\n</head>", 1)
        html = html.replace("</body>", BAND_JS.replace("__LANGS__", json.dumps(band_langs, ensure_ascii=False))
                            + "\n</body>", 1)
        html = relocate(html, LANG)
        # 런타임은 head 첫머리 — 페이지의 어떤 스크립트보다 먼저. 키는 완성된 페이지에서 센다
        rt = i18n.runtime_js(LANG, i18n.js_keys(html), rates)
        html = html.replace("<head>", "<head>\n" + rt, 1)
        _w(f, html)
        n += 1
    print(f"[OK] 다국어 주입({LANG}): {n}개 페이지")


def write_sitemap(root):
    """sitemap.xml — 완성 페이지는 4개 언어 대체 링크, 나머지는 ko 만. robots.txt 에 위치를 적는다."""
    from datetime import date
    today = (_header_meta().get("fetched_date") or date.today().isoformat())[:10]
    urls = []
    for page in ALL_PAGES:
        langs = i18n.LANGS if page in i18n.DONE_PAGES else (i18n.DEFAULT,)
        for L1 in langs:
            alts = "".join(f'<xhtml:link rel="alternate" hreflang="{L2}" href="{i18n.canonical(L2, page)}"/>'
                           for L2 in langs) if len(langs) > 1 else ""
            if len(langs) > 1:
                alts += (f'<xhtml:link rel="alternate" hreflang="x-default" '
                         f'href="{i18n.canonical(i18n.X_DEFAULT, page)}"/>')
            urls.append(f"<url><loc>{i18n.canonical(L1, page)}</loc><lastmod>{today}</lastmod>{alts}</url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(urls) + "\n</urlset>\n")
    _w(root / "sitemap.xml", xml)
    _w(root / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {i18n.SITE}/sitemap.xml\n")
    print(f"[OK] sitemap.xml: {len(urls)}개 주소")


def publish(staging, dest):
    """스테이징의 산출물을 실제 경로로 **한 파일씩 원자 교체**한다.

    os.replace 는 같은 파일시스템 안에서 원자적이다 — 교체 도중 죽어도 어떤 파일도
    '반쯤 쓰인' 상태가 되지 않는다. 스테이징을 저장소 안에 두는 이유가 그것이다
    (임시 폴더가 다른 드라이브면 원자성이 깨진다).

    파일 단위 원자성이지 **묶음 단위는 아니다.** 교체 중간에 죽으면 새 페이지와 옛
    페이지가 섞일 수 있다. 그래도 이번 사고(주입 빠진 페이지)는 막힌다: 스테이징의
    모든 페이지가 이미 검사를 통과한 완성본이기 때문이다. 섞이더라도 양쪽 다 완성본이다.
    """
    moved = 0
    for f in sorted(staging.glob("*.html")):
        os.replace(str(f), str(dest / f.name))
        moved += 1
    # 언어 폴더(/en/ 등)와 사이트맵 — 같은 원자 교체. 폴더가 없으면 만든다
    for lang in i18n.LANGS:
        if lang == i18n.DEFAULT or not (staging / lang).is_dir():
            continue
        (dest / lang).mkdir(exist_ok=True)
        for f in sorted((staging / lang).glob("*.html")):
            os.replace(str(f), str(dest / lang / f.name))
            moved += 1
    for extra in ("sitemap.xml", "robots.txt"):
        if (staging / extra).exists():
            os.replace(str(staging / extra), str(dest / extra))
            moved += 1
    return moved


def main():
    global OUT_DIR
    print("=" * 50)
    print("우주지배자 사이트 빌드 시작")
    print("=" * 50)
    staging = HERE / "_build_tmp"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    OUT_DIR = staging
    try:
        _build_all()
        # 여기까지 왔다는 것은 주입 6종과 산출물 검사까지 끝났다는 뜻이다.
        n = publish(staging, HERE)
        print(f"[OK] 원자적 교체: {n}개 파일 → 실제 경로")
    finally:
        global LANG
        OUT_DIR = HERE
        LANG = i18n.DEFAULT
        shutil.rmtree(staging, ignore_errors=True)
    print("=" * 50)
    print("빌드 완료")


def _build_all():
    """LANGS × ALL_PAGES — 언어마다 같은 빌드·같은 주입을 그 언어 폴더에. 검사는 네 언어가 다 끝난 뒤."""
    global OUT_DIR, LANG
    root = OUT_DIR
    i18n.MISSING.clear()
    try:
        for lang in i18n.LANGS:
            LANG = lang
            OUT_DIR = root / lang if lang != i18n.DEFAULT else root
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            print(f"── {lang} ──")
            _build_lang()
    finally:
        OUT_DIR, LANG = root, i18n.DEFAULT
    if i18n.MISSING:
        print(f"[warn] 카탈로그 키 누락 {len(set(i18n.MISSING))}건(ko 로 대신): "
              f"{sorted(set(i18n.MISSING))[:12]}", file=sys.stderr)
    verify_pages()
    write_sitemap(root)


def _build_lang():
    build_main()
    build_latent()
    build_megatrend()
    build_placeholders()
    build_community()
    build_observatory()
    build_policies()
    build_journal()
    build_pioneers()
    build_research()
    build_about()
    build_history("top20",  "home",   "history-top20.html")
    build_history("latent", "latent", "history-latent.html")
    inject_header_fix()
    inject_macro_badges()
    inject_presence()
    fix_nav()
    inject_footer_links()
    inject_aurora_tokens()
    inject_i18n()


if __name__ == "__main__":
    main()
