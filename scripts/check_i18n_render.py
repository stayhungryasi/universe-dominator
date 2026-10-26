#!/usr/bin/env python3
"""
check_i18n_render.py — 4개 언어 산출물의 **렌더 결과**를 실측한다(로컬 전용, Playwright 필요)
================================================================================
selftest 는 정적 산출물을 본다. 이 도구는 브라우저로 실제로 그려서 본다:
  ① 콘솔 오류 0                ② 가로 넘침 0(스페인어 길이·일본어 줄바꿈)
  ③ 완성 페이지(i18n.DONE_PAGES)의 en/es/ja 화면에 **표식 없는 한글 0**
     — 한글은 원문 표식(lang="ko", 번역 준비 중) 안에서만 허용
  ④ 언어 전환기가 같은 페이지·같은 해시로 이동
  ⑤ 관제실 첫 화면(①~⑤)이 데스크탑 한 화면 안
스크린샷은 --shots 폴더에 남긴다.

지뢰(프로브 침묵): 이 파일의 JS 는 파이썬 문자열 안에 있다 — 개행 이스케이프를 쓰지 않는다.
실행 흔적은 결과 JSON 의 'ran' 카운트로 남긴다(침묵은 통과가 아니다).

실행: python scripts/check_i18n_render.py --shots <폴더> [--langs ko,en,es,ja] [--quick]
종료 코드: 위반 0 이면 0, 하나라도 있으면 1
"""
import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import i18n  # noqa: E402

OBS_TABS = ("solar", "map", "columns", "parallax", "legend", "agent")
# 탭이 있는 페이지 — (페이지, 클릭 선택자 형식, 탭 id 목록)
TABBED = {
    "observatory.html": ('.obs-tab[data-t="{}"]', OBS_TABS),
    "research.html": ('.group-tab[data-g="{}"]', ("universe", "latent", "calendar", "gurus", "signals", "dissect")),
    "megatrend.html": ('.cat-btn[data-cat="{}"]', ("top10", "throne", "themes", "rank")),
}
# 단계 2: 완성 페이지 전부 — 탭 없는 페이지는 한 번만 연다
PLAIN = tuple(pg for pg in i18n.DONE_PAGES if pg not in TABBED)
VIEWPORTS = (("desktop", 1440, 900), ("mobile", 390, 844))

# 화면에 보이는 텍스트 노드 중 한글이 있는데 원문 표식(lang=ko) 밖에 있는 것
JS_UNMARKED = r"""() => {
  const HAN = /[가-힣]/, out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const s = n.nodeValue;
    if (!s || !HAN.test(s)) continue;
    const el = n.parentElement;
    if (!el || el.closest('script,style,noscript,template')) continue;
    if (el.closest('[lang="ko"]')) continue;
    const r = el.getClientRects();
    if (!r.length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    out.push(s.trim().slice(0, 80));
  }
  return out.slice(0, 20);
}"""
JS_OVERFLOW = r"""() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth,
  culprits: [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
    return r.right > window.innerWidth + 1 && r.width > 0 && getComputedStyle(e).position !== 'fixed'
      && !e.closest('.site-nav-inner,.tabs-scroll,.obs-tabs,.ud-macro,.px-wrap,.pc-wrap,.ag-tw,.pc-segs,.map-chips,.pc-drawer,.solar-hero,.map-wrap'); })
    .slice(0, 5).map(e => e.tagName + '.' + String(e.className).slice(0, 40)) })"""
JS_COCKPIT = r"""() => { const c = document.querySelector('.pc-c5'); if (!c) return null;
  return { bottom: Math.round(c.getBoundingClientRect().bottom), ih: window.innerHeight }; }"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    handler = functools.partial(_Quiet, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="")
    ap.add_argument("--langs", default=",".join(i18n.LANGS))
    ap.add_argument("--quick", action="store_true", help="데스크탑만·관측일지 생략")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    bad, ran = [], 0
    report = {"checks": []}
    vps = VIEWPORTS[:1] if a.quick else VIEWPORTS
    with sync_playwright() as p:
        b = p.chromium.launch()
        for lang in a.langs.split(","):
            for vpname, w, h in vps:
                for theme in (("light", "dark") if vpname == "desktop" else ("light",)):
                    ctx = b.new_context(viewport={"width": w, "height": h}, locale={"ko": "ko-KR", "en": "en-US", "es": "es-ES", "ja": "ja-JP"}[lang])
                    ctx.add_init_script("try{localStorage.setItem('ud-theme','" + theme + "');localStorage.setItem('uvx-band-off','1')}catch(e){}")
                    pg = ctx.new_page()
                    errs = []
                    pg.on("console", lambda m, e=errs: m.type == "error" and e.append(m.text[:200]))
                    pg.on("pageerror", lambda x, e=errs: e.append("PAGEERROR " + str(x)[:200]))
                    if theme == "dark":
                        targets = [("index.html", None), ("journal.html", None), ("research.html", "signals")] +                             [("observatory.html", t) for t in ("solar", "agent")]
                    else:
                        targets = [(pg, None) for pg in PLAIN]
                        for pg_, (_, tabs) in TABBED.items():
                            if a.quick:
                                tabs = tuple(t for t in tabs if t not in ("columns", "dissect"))
                            targets += [(pg_, t) for t in tabs]
                    for page, tab in targets:
                        key = f"{lang}/{page}{'#' + tab if tab else ''} {vpname} {theme}"
                        errs.clear()
                        pg.goto(base + i18n.subdir(lang) + page, wait_until="load")
                        pg.wait_for_timeout(900)
                        if tab:
                            pg.click(TABBED[page][0].format(tab))
                            pg.wait_for_timeout(700)
                        ran += 1
                        rec = {"key": key}
                        ov = pg.evaluate(JS_OVERFLOW)
                        rec["overflow"] = ov
                        if ov["sw"] > ov["iw"] + 1:
                            bad.append(f"{key}: 가로 넘침 {ov['sw']}>{ov['iw']} {ov['culprits']}")
                        real_errs = [e for e in errs if "favicon" not in e]
                        if real_errs:
                            bad.append(f"{key}: 콘솔 오류 {real_errs[:3]}")
                        if lang != i18n.DEFAULT and page in i18n.DONE_PAGES:
                            um = pg.evaluate(JS_UNMARKED)
                            rec["unmarked_hangul"] = um
                            if um:
                                bad.append(f"{key}: 표식 없는 한글 {um[:4]}")
                        if page == "observatory.html" and tab == "agent" and vpname == "desktop":
                            ck = pg.evaluate(JS_COCKPIT)
                            rec["cockpit"] = ck
                            if ck and ck["bottom"] > ck["ih"]:
                                bad.append(f"{key}: 관제실 첫 화면이 한 화면을 넘음 ({ck['bottom']}>{ck['ih']})")
                        if shots:
                            name = f"{lang}_{page[:-5]}{'_' + tab if tab else ''}_{vpname}_{theme}.png"
                            pg.screenshot(path=str(shots / name), full_page=False)
                        report["checks"].append(rec)
                    # ④ 언어 전환기 — 같은 페이지·같은 해시
                    if vpname == "desktop" and theme == "light":
                        other = next(L for L in i18n.LANGS if L != lang)
                        pg.goto(base + i18n.subdir(lang) + "observatory.html#legend", wait_until="load")
                        pg.wait_for_timeout(500)
                        pg.click(".uvx-lang > summary")
                        pg.click(f'.uvx-lang-menu a[hreflang="{other}"]')
                        pg.wait_for_timeout(700)
                        want = i18n.subdir(other) + "observatory.html#legend"
                        ran += 1
                        if not pg.url.endswith(want):
                            bad.append(f"{lang}: 언어 전환기 → {pg.url} (기대 …/{want})")
                    ctx.close()
        b.close()
    srv.shutdown()
    report.update({"ran": ran, "violations": bad})
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[렌더 검증] 실행 {ran}건 · 위반 {len(bad)}건")
    for x in bad:
        print("   ✗", x)
    if ran == 0:
        print("[렌더 검증] 실행 0건 — 침묵은 통과가 아니다", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
