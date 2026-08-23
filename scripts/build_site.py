"""
build_site.py — 멀티페이지 사이트 빌드

생성 페이지 목록은 ALL_PAGES 하나가 유일한 진실이다 (아래 참조).
새 페이지를 추가할 때 손대야 하는 곳도 ALL_PAGES 한 곳뿐이며,
selftest.py 가 이 단일화를 매 실행마다 검증한다.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SCRIPTS_DIR = HERE / "scripts"

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
        return {"fetched_date": m.get("fetched_date"), "usd_krw": m.get("usd_krw")}
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
    data = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
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
    html = html.replace("{{TOP1_MC}}", f"${top1_mc/1000:.2f}T" if top1_mc >= 1000 else f"${top1_mc:.0f}B")
    html = html.replace("{{TRILLION_COUNT}}", str(trillion_count))
    html = html.replace("{{TOP20_SUM}}", f"${top20_sum/1000:.1f}T" if top20_sum >= 1000 else f"${top20_sum:.0f}B")

    # 오늘의 신호 3건 (ud-aurora-v1) — 필독 우선, 이후 추천수순 · 파일 없으면 우아한 저하
    try:
        sig_all = json.loads((DATA_DIR / "signals.json").read_text(encoding="utf-8")).get("signals", [])
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
        sig_top = [{k: x.get(k) for k in ("title", "ko", "url", "pub", "points", "pin")} for x in sig_top]
    except Exception as e:
        print(f"[warn] index 신호 주입 실패(무시): {e}")
        sig_top = []
    html = html.replace("{{SIGNALS_JSON}}", json.dumps(sig_top, ensure_ascii=False))

    out = HERE / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_latent():
    """latent.html — 잠재지배자"""
    template_path = SCRIPTS_DIR / "latent-template.html"
    if not template_path.exists():
        print(f"[skip] latent-template.html 없음"); return
    data = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    out = HERE / "latent.html"
    out.write_text(html, encoding="utf-8")
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_megatrend():
    """megatrend.html — 메가트렌드 (4 카테고리)"""
    template_path = SCRIPTS_DIR / "megatrend-template.html"
    data_path = DATA_DIR / "megatrend.json"
    if not template_path.exists() or not data_path.exists():
        print(f"[skip] megatrend 자원 없음"); return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["meta"] = _header_meta()
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    out = HERE / "megatrend.html"
    out.write_text(html, encoding="utf-8")
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
    out = HERE / "about.html"
    out.write_text(html, encoding="utf-8")
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_research():
    """research.html — 종목 리서치·분석 기사 (자동 수집)"""
    template_path = SCRIPTS_DIR / "research-template.html"
    if not template_path.exists():
        print("[skip] research-template.html 없음"); return
    data_path = DATA_DIR / "research.json"
    if data_path.exists():
        rdata = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        rdata = {"generated_label": "", "stocks": []}
    cal_path = DATA_DIR / "calendar.json"
    rdata["calendar"] = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path.exists() else {"events": []}
    gurus_path = DATA_DIR / "gurus.json"
    rdata["gurus"] = json.loads(gurus_path.read_text(encoding="utf-8")) if gurus_path.exists() else {"gurus": []}
    signals_path = DATA_DIR / "signals.json"
    rdata["signals"] = json.loads(signals_path.read_text(encoding="utf-8")) if signals_path.exists() else {"signals": []}
    # 🔬 필독 해부실 — 실패 기록(failures)은 내부용이므로 페이지에 싣지 않는다
    tr_path = DATA_DIR / "translations.json"
    tr = json.loads(tr_path.read_text(encoding="utf-8")) if tr_path.exists() else {}
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
                             "items": tr_items,
                             "column_links": column_links}
    template = template_path.read_text(encoding="utf-8")
    # 헤더 배지 값 (placeholder 방식과 동일)
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    usd_krw_str = f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—"
    html = template
    html = html.replace("{{PAGE_TITLE}}", "리서치")
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", usd_krw_str)
    html = html.replace("{{DATA_JSON}}", json.dumps(rdata, ensure_ascii=False))
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "active" if key == "RESEARCH" else "")
    out = HERE / "research.html"
    out.write_text(html, encoding="utf-8")
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
    out = HERE / "community.html"
    out.write_text(html, encoding="utf-8")
    print(f"[OK] {out.name} ({len(html):,} chars)")


def build_observatory():
    """observatory.html — 데이터 천문대 (오늘의 태양계 + 관측일지)"""
    template_path = SCRIPTS_DIR / "observatory-template.html"
    if not template_path.exists():
        print("[skip] observatory-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    latest = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    cols_path = DATA_DIR / "columns.json"
    cols = json.loads(cols_path.read_text(encoding="utf-8")).get("columns", []) if cols_path.exists() else []
    meta = latest.get("meta", {})
    # ── 우주 지도 타일: 전 지역 고유 종목 + 전일 대비 시총 증감 ──
    FLAG_KO = {"🇺🇸": "미국", "🇰🇷": "한국", "🇯🇵": "일본", "🇹🇼": "대만", "🇨🇳": "중국",
               "🇭🇰": "홍콩", "🇬🇧": "영국", "🇩🇪": "독일", "🇫🇷": "프랑스", "🇨🇭": "스위스",
               "🇳🇱": "네덜란드", "🇸🇦": "사우디", "🇩🇰": "덴마크", "🇸🇪": "스웨덴",
               "🇮🇪": "아일랜드", "🇪🇸": "스페인", "🇮🇹": "이탈리아", "🇹🇭": "태국"}
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
            tiles.append({"t": tk, "n": s["name"], "mc": s["mc"], "chg": chg,
                          "g": FLAG_KO.get(s.get("flag", ""), "기타"),
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
        tiles.append({"t": tk, "n": x["name"], "mc": x["mc"], "chg": chg,
                      "g": FLAG_KO.get(x.get("country", ""), "기타"),
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
    obs_data = {
        "earth": latest.get("regions", {}).get("earth", {}).get("stocks", []),
        "fetched": meta.get("fetched_date", ""),
        "columns": cols,
        "tiles": tiles,
        "dissected": dissected,
        "buffett": buffett,
    }
    html = template.replace("{{OBS_DATA}}", json.dumps(obs_data, ensure_ascii=False))
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}", f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    for key in ["HOME", "LATENT", "MEGA", "RESEARCH", "COMMUNITY", "MY"]:
        html = html.replace("{{ACTIVE_" + key + "}}", "")
    (HERE / "observatory.html").write_text(html, encoding="utf-8")
    print(f"[OK] observatory.html ({len(html):,} chars)")
    # 구주소 리다이렉트 (북마크 보호)
    (HERE / "my-universe.html").write_text(
        '<!doctype html><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0; url=observatory.html">'
        '<a href="observatory.html">데이터 천문대로 이동</a>', encoding="utf-8")
    print("[OK] my-universe.html → observatory.html 리다이렉트")


def build_pioneers():
    """pioneers.html — 개척자: 세상을 바꾸는 인물들의 최신 신호"""
    template_path = SCRIPTS_DIR / "pioneers-template.html"
    if not template_path.exists():
        print("[skip] pioneers-template.html 없음"); return
    template = template_path.read_text(encoding="utf-8")
    data_path = DATA_DIR / "pioneers.json"
    if data_path.exists():
        pioneers = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        pioneers = {"updated": "", "people": []}  # 첫 full 수집 전 — 빈 상태 렌더
    meta = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
    fetched_label = meta.get("fetched_date", "—").replace("-", ".")
    usd_krw = meta.get("usd_krw")
    html = template.replace("{{PIONEERS_JSON}}",
                            json.dumps(pioneers, ensure_ascii=False))
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{USD_KRW}}",
                        f"{usd_krw:,.2f}" if isinstance(usd_krw, (int, float)) else "—")
    (HERE / "pioneers.html").write_text(html, encoding="utf-8")
    print(f"[OK] pioneers.html ({len(html):,} chars)")


def fix_nav():
    """전 페이지 nav 정리 (멱등):
    ① 나의우주 → 데이터 천문대  ② 커뮤니티 → 관제센터  ③ 순서: 천문대가 관제센터 앞"""
    import re as _re
    pages = ALL_PAGES
    n = 0
    pat = _re.compile(r'(<a href="community\.html"[^>]*>[^<]*</a>)(\s*)(<a href="observatory\.html"[^>]*>[^<]*</a>)')
    for name in pages:
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        new = html.replace('href="my-universe.html"', 'href="observatory.html"')
        new = new.replace(">나의우주<", ">데이터 천문대<")
        new = new.replace(">커뮤니티<", ">관제센터<")
        new = pat.sub(lambda m: m.group(3) + m.group(2) + m.group(1), new)
        # 관측노트 메뉴 (공개) — 관제센터 뒤에 멱등 삽입
        if 'href="journal.html"' not in new:
            new = _re.sub(r'(<a href="community\.html"[^>]*>관제센터</a>)',
                          lambda m: m.group(1) + '<a href="journal.html" class="nav-tab">관측노트</a>',
                          new, count=1)
        if name == "journal.html":
            new = new.replace('<a href="journal.html" class="nav-tab">관측노트</a>',
                              '<a href="journal.html" class="nav-tab active">관측노트</a>')
        # 개척자 메뉴 (세상을 바꾸는 인물 추적) — 관측노트 뒤에 멱등 삽입
        if 'href="pioneers.html"' not in new:
            new = _re.sub(r'(<a href="journal\.html"[^>]*>관측노트</a>)',
                          lambda m: m.group(1) + '<a href="pioneers.html" class="nav-tab">개척자</a>',
                          new, count=1)
        if name == "pioneers.html":
            new = new.replace('<a href="pioneers.html" class="nav-tab">개척자</a>',
                              '<a href="pioneers.html" class="nav-tab active">개척자</a>')
        # 광폭 레이아웃 (720px → 1280px) — 대시보드 체급에 맞게, 멱등 주입
        if 'id="wide-fix"' not in new:
            new = new.replace("</head>",
                '<style id="wide-fix">.container{max-width:1280px}</style>\n</head>', 1)
        if new != html:
            f.write_text(new, encoding="utf-8"); n += 1
    print(f"[OK] nav 정리(개명·순서): {n}개 페이지")


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
    (HERE / "policies.html").write_text(html, encoding="utf-8")
    print(f"[OK] policies.html ({len(html):,} chars)")


def inject_footer_links():
    """전 페이지 푸터에 정책 링크 줄 주입 (멱등)"""
    snippet = ('<div class="uv-policy-links" style="margin-top:10px;font-size:12px;">'
               '<a href="about.html" style="color:inherit;margin:0 7px">사이트 소개</a>·'
               '<a href="policies.html#terms" style="color:inherit;margin:0 7px">이용약관</a>·'
               '<a href="policies.html#privacy" style="color:inherit;margin:0 7px">개인정보처리방침</a>·'
               '<a href="policies.html#community" style="color:inherit;margin:0 7px">커뮤니티 운영정책</a>·'
               '<a href="policies.html#disclaimer" style="color:inherit;margin:0 7px">투자 고지·면책</a>·'
               '<a href="policies.html#contact" style="color:inherit;margin:0 7px">문의·제안</a></div>')
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "uv-policy-links" in html or "</footer>" not in html:
            continue
        html = html.replace("</footer>", snippet + "\n</footer>", 1)
        f.write_text(html, encoding="utf-8")
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


def companion_data():
    """동행 관측 주입 데이터 — 회사(논제 스냅샷) + 발행 에세이 원장."""
    import companion_essays as ce
    src = DATA_DIR / "companion_sources.json"
    cfg = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {"companies": []}
    companies = []
    for c in cfg.get("companies", []):
        t = ce.read_thesis(c["slug"])
        companies.append({
            "slug": c["slug"], "ko": c.get("ko", c["slug"]), "emoji": c.get("emoji", ""),
            "thesis": {"placeholder": t["placeholder"], "version": t["version"],
                       "updated": t["updated"],
                       "one": "" if t["placeholder"] else thesis_one_line(t["body"]),
                       "html": "" if t["placeholder"] else thesis_md_to_html(t["body"])},
        })
    ess = DATA_DIR / "essays.json"
    essays = json.loads(ess.read_text(encoding="utf-8")).get("essays", []) if ess.exists() else []
    return {"companies": companies, "essays": essays}


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
    (HERE / "journal.html").write_text(html, encoding="utf-8")
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
        out = HERE / p["filename"]
        out.write_text(html, encoding="utf-8")
        print(f"[OK] {out.name} ({len(html):,} chars)")


def build_history(page_key, active_marker, out_filename):
    template_path = SCRIPTS_DIR / "history-template.html"
    data_path = DATA_DIR / f"history-{page_key}.json"
    if not template_path.exists() or not data_path.exists():
        print(f"[skip] history-{page_key} 자원 없음"); return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["meta"] = _header_meta()
    template = template_path.read_text(encoding="utf-8")
    html = template
    html = html.replace("{{PAGE_TITLE}}", data.get("page_title","History"))
    html = html.replace("{{PAGE_DESC}}", data.get("page_desc",""))
    html = html.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False, indent=2))
    for key in ("home","latent"):
        html = html.replace("{{ACTIVE_"+key.upper()+"}}", "active" if active_marker==key else "")
    out = HERE / out_filename
    out.write_text(html, encoding="utf-8")
    print(f"[OK] {out.name} ({len(html):,} chars)")


LOGO_HTML = """<a class="brand ud-logo uvx" href="index.html" aria-label="UNIVERTRIX 홈">
      <span class="uvx-lat"><span class="uvx-word">UNIVERTRIX</span>
        <svg class="uvx-orbit" viewBox="0 0 300 60" preserveAspectRatio="none" fill="none" aria-hidden="true">
          <path d="M8 46 C 62 68, 150 -16, 288 20" stroke-width="1.6"/>
          <circle cx="288" cy="20" r="3.4"/>
        </svg></span>
      <span class="uvx-kr">우주지배자</span>
    </a>"""



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

   ⚠️ 펼침에 max-height 전환을 쓰지 않는다: 본문에 차트 SVG 가 들어 있어 접힌
   상태의 높이를 잴 수 없다(0 이나 잘린 값이 나온다). 애니메이션 한 번 보자고
   근거가 되는 그림이 깨지는 쪽을 택하지 않는다 — 상태 변화는 캐럿 회전이 알린다. */
.ud-acc{background:var(--bg-card);border:1px solid var(--line);border-radius:14px;margin-bottom:10px;scroll-margin-top:80px;overflow:hidden}
.ud-acc-head{display:flex;align-items:center;gap:12px;width:100%;padding:14px 18px;background:none;border:none;text-align:left;cursor:pointer;font-family:inherit;color:inherit;transition:background .15s}
.ud-acc-head:hover{background:var(--bg)}
.ud-acc.open>.ud-acc-head{border-bottom:1px solid var(--line)}
.ud-acc-meta{display:flex;align-items:center;gap:9px;flex-shrink:0;font-size:12.5px;color:var(--text-muted)}
.ud-acc-title{flex:1;min-width:0;font-weight:800;letter-spacing:-0.03em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ud-acc-caret{margin-left:auto;flex-shrink:0;color:var(--text-muted);font-size:12.5px;transition:transform .15s}
.ud-acc.open .ud-acc-caret{transform:rotate(90deg)}
.ud-acc-detail{display:none;padding:16px 22px 20px}
.ud-acc.open>.ud-acc-detail{display:block}
@media(max-width:480px){.ud-acc-head{gap:8px;padding:12px 14px}.ud-acc-detail{padding:14px 16px 18px}}

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
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "ud-aurora-global-v1" in html or "</head>" not in html:
            continue
        html = html.replace("</head>", AURORA_GLOBAL_CSS + "\n</head>", 1)
        f.write_text(html, encoding="utf-8"); n += 1
    print(f"[OK] 오로라 팔레트 주입: {n}개 페이지")


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
  function tick() {
    var now = new Date();
    dt.textContent = now.toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' }).replace(/-/g, '.');
    clk.textContent = now.toLocaleTimeString('en-GB', { hour12: false, timeZone: 'Asia/Seoul' });
  }
  tick();
  setInterval(tick, 1000);
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
          el.innerHTML = '<span style="color:var(--gold,#9C7A3A)">\u2726</span> 지금 ' + n + '\uba85\uc774 \uc6b0\uc8fc\ub97c \uad00\uce21 \uc911';
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
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "uv-presence" in html or "</body>" not in html:
            continue
        html = html.replace("</body>", PRESENCE_SNIPPET + "\n</body>", 1)
        f.write_text(html, encoding="utf-8")
        n += 1
    print(f"[OK] 접속자 카운터 주입: {n}개 페이지")


def inject_header_fix():
    """생성된 모든 페이지 헤더에 동일한 반응형 규칙 주입 (중복 방지)"""
    pages = ALL_PAGES
    n = 0
    for name in pages:
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "ud-hdr-refine-v4" in html or "</head>" not in html:
            continue
        # 브랜드 → UNIVERTRIX 로고 교체 (CSS 주입 전에 — 멱등 체크는 마크업 기준)
        if 'class="brand ud-logo"' not in html:
            html = re.sub(
                r'<div class="brand">.*?</div>',
                lambda m: LOGO_HTML,
                html, count=1, flags=re.S,
            )
        # 푸터 브랜드도 통일
        html = html.replace("✨ Stay hungry. ASI",
                            'UNI<span style="color:var(--gold)">V</span>ERTRIX')
        html = html.replace("</head>", HEADER_FIX_CSS + "\n</head>", 1)
        f.write_text(html, encoding="utf-8")
        n += 1
    print(f"[OK] 헤더 일관성 CSS 주입: {n}개 페이지")


def main():
    print("=" * 50)
    print("우주지배자 사이트 빌드 시작")
    print("=" * 50)
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
    inject_presence()
    fix_nav()
    inject_footer_links()
    inject_aurora_tokens()
    print("=" * 50)
    print("빌드 완료")


if __name__ == "__main__":
    main()
