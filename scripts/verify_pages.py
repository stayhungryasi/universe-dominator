#!/usr/bin/env python3
"""
verify_pages.py — 커밋 직전 관문. **검사만 한다.**
================================================================================
2026-09-05 사고: 빌드가 중간에 죽어 주입 5종이 빠진 페이지가 남았다. build_site 는
원자적 교체로 고쳤지만(142f9c4), 그것만 믿으면 같은 계열의 다음 사고를 놓친다 —
빌드가 아닌 다른 스텝이 페이지를 건드리는 날, 혹은 교체 도중 죽는 날이다.

**이 파일은 build_site.py 에서 아무것도 import 하지 않는다.** 의도적인 중복이다:
같은 함수를 공유하면 그 함수가 죽는 날 검사도 같이 죽는다. 관문은 감시 대상과
다른 다리로 서 있어야 한다(2026-08-30 '도구를 만든 것과 배선한 것은 다르다').

페이지 목록도 공유하지 않는다. 저장소 루트의 *.html 을 직접 훑어
"본문이 있는 페이지" 전부를 대상으로 삼는다 — 목록을 베껴 두면 새 페이지가
추가되는 날 관문만 옛 목록을 본다.

종료 코드: 정상 0 · 하나라도 빠지면 1 (워크플로가 커밋 스텝에 도달하지 못한다)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# 주입 5종의 흔적. build_site 의 상수를 가져오지 않고 **문자열로 다시 적는다.**
MARKERS = {
    "ud-hdr-refine-v4": "헤더 일관성",
    "uv-presence": "접속자 카운터",
    "wide-fix": "광폭 레이아웃",
    "uv-policy-links": "푸터 정책 링크",
    "ud-aurora-global-v1": "오로라 팔레트",
}

# 리다이렉트 스텁처럼 레이아웃이 없는 파일. head 끝 태그가 없고 아주 작다.
STUB_MAX_BYTES = 1000


def targets():
    """검사 대상 — 루트의 *.html 중 레이아웃이 있는 페이지."""
    out, stubs = [], []
    for f in sorted(ROOT.glob("*.html")):
        raw = f.read_text(encoding="utf-8", errors="replace")
        has_head_end = "</hea" + "d>" in raw          # 종료 태그 문자열은 쪼개 적는다
        if not has_head_end and len(raw.encode("utf-8")) <= STUB_MAX_BYTES:
            stubs.append(f.name)
            continue
        out.append((f.name, raw))
    return out, stubs


def check(pages):
    """페이지별 누락 마커 목록 → {파일명: [누락 설명]}"""
    bad = {}
    for name, raw in pages:
        missing = [ko for m, ko in MARKERS.items() if m not in raw]
        if "</hea" + "d>" not in raw:
            missing.append("head 끝 태그 없음")
        if missing:
            bad[name] = missing
    return bad


def notify(text):
    """관문 실패는 **소장 DM 전용**. 수신처가 없으면 로그로만 남긴다(공개 폴백 없음)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print(f"[관문] DM 수신처 미등록 — 발송 생략(공개 채널 폴백 없음): {text}",
              file=sys.stderr)
        return False
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text,
                                "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()
        print("[관문] 실패 알림 1건 발송 → DM")
        return True
    except Exception as e:
        print(f"[관문] 알림 실패 ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def main():
    pages, stubs = targets()
    if not pages:
        print("[관문] 검사할 페이지가 없다 — 빌드 산출물이 통째로 비었다", file=sys.stderr)
        notify("🚨 UNIVERTRIX 관문: 루트에 페이지가 하나도 없습니다 — 커밋 중단")
        return 1
    bad = check(pages)
    if bad:
        print(f"[관문] ❌ 주입 누락 {len(bad)}개 페이지 — 커밋하지 않는다:", file=sys.stderr)
        for name, missing in sorted(bad.items()):
            print(f"    · {name}: {', '.join(missing)}", file=sys.stderr)
        head = ", ".join(f"{n}({len(m)}종)" for n, m in sorted(bad.items())[:5])
        notify(f"🚨 UNIVERTRIX 관문: 주입 누락 {len(bad)}개 페이지 — 커밋 중단\n{head}")
        return 1
    tail = f" · 스텁 제외 {len(stubs)}개" if stubs else ""
    print(f"[관문] ✅ {len(pages)}개 페이지 · 주입 {len(MARKERS)}종 전부 확인{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
