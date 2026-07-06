"""
build_site.py — 멀티페이지 사이트 빌드

생성 페이지 (8개):
  1. index.html          — 우주지배자 (main, TOP 20)
  2. latent.html         — 잠재지배자
  3. megatrend.html      — 메가트렌드 (4 카테고리)
  4. research.html       — 리서치 (placeholder)
  5. community.html      — 커뮤니티 (placeholder)
  6. my-universe.html    — 나의우주 (placeholder)
  7. history-top20.html  — 우주지배자 변동 이력
  8. history-latent.html — 잠재지배자 변동 이력
"""
import json
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
SCRIPTS_DIR = HERE / "scripts"


def _header_meta():
    """헤더 배지용 날짜·환율 (latest.json meta 기준)"""
    try:
        m = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8")).get("meta", {})
        return {"fetched_date": m.get("fetched_date"), "usd_krw": m.get("usd_krw")}
    except Exception:
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
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{TOP1_NAME}}", top1_name)
    html = html.replace("{{TOP1_MC}}", f"${top1_mc/1000:.2f}T" if top1_mc >= 1000 else f"${top1_mc:.0f}B")
    html = html.replace("{{TRILLION_COUNT}}", str(trillion_count))
    html = html.replace("{{TOP20_SUM}}", f"${top20_sum/1000:.1f}T" if top20_sum >= 1000 else f"${top20_sum:.0f}B")
    
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
    {"filename":"community.html",   "title":"커뮤니티", "desc":"구독자 토론, Q&A, 종목 공유 공간.",                       "icon":"💬", "active":"community"},
    {"filename":"my-universe.html", "title":"나의우주", "desc":"관심 종목 핀, 보유 종목 트래킹, 개인화 대시보드.",          "icon":"👤", "active":"my"},
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


HEADER_FIX_CSS = """<style>
/* 헤더 모바일 일관성 + 실시간 날짜·시계 (전 페이지 공통 — build_site.py 주입) */
@media (max-width: 480px) {
  .site-header-inner { flex-wrap: wrap; }
  .brand { flex-shrink: 0; }
}
.update-badge .ud-clock { margin-left: 6px; font-variant-numeric: tabular-nums; letter-spacing: 0.02em; }
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


def inject_header_fix():
    """생성된 모든 페이지 헤더에 동일한 반응형 규칙 주입 (중복 방지)"""
    pages = ["index.html", "latent.html", "megatrend.html", "research.html",
             "community.html", "my-universe.html", "history-top20.html", "history-latent.html", "about.html"]
    n = 0
    for name in pages:
        f = HERE / name
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        if "헤더 모바일 일관성" in html or "</head>" not in html:
            continue
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
    build_research()
    build_about()
    build_history("top20",  "home",   "history-top20.html")
    build_history("latent", "latent", "history-latent.html")
    inject_header_fix()
    print("=" * 50)
    print("빌드 완료")


if __name__ == "__main__":
    main()
