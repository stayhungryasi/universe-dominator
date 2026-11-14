#!/usr/bin/env python3
"""
구독 인증 물러남 알림 (2026-11-14 ㉕) — 에이전트(탐사·자가 수리)가 구독 토큰으로 못 돌아 API 키로 물러난 회차를
소장 DM 으로 알린다. 물러남은 탐사를 세우지 않으려는 안전장치지만, 조용하면 '구독으로 옮긴 줄 알았는데
계속 API 로 과금'이 된다 — 11/12 의 지출 한도 정지가 그렇게 시작됐다. 침묵은 통과가 아니다.

읽는 것: scripts/claude_auth.sh 가 적은 CLAUDE_AUTH_FALLBACK_FILE(기본 $RUNNER_TEMP/claude_auth_fallback.txt)
보내는 곳: TELEGRAM_ALERT_CHAT_ID(소장 DM) 전용 — 미등록이면 로그로만(공개 폴백 없음)
실행: python scripts/claude_auth_alarm.py   (물러난 적 없으면 아무것도 하지 않는다)
"""
import os
import sys
from pathlib import Path


def note_path():
    base = os.environ.get("RUNNER_TEMP") or "/tmp"
    return Path(os.environ.get("CLAUDE_AUTH_FALLBACK_FILE") or (Path(base) / "claude_auth_fallback.txt"))


def build_message(lines, run_url=""):
    """물러난 호출 목록 → DM 본문. 물러난 적이 없으면 빈 문자열."""
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        return ""
    seen, uniq = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            uniq.append(ln)
    out = ["⚠️ 구독 인증 실패 — 에이전트가 API 키로 물러나 돌았습니다(API 과금)",
           f"물러난 호출 {len(lines)}건"]
    out += ["· " + ln[:300] for ln in uniq[:3]]
    out.append("토큰 만료면 로컬에서 claude setup-token 을 다시 돌려 시크릿 CLAUDE_CODE_OAUTH_TOKEN 을 바꿔 주세요. "
               "구독 사용량 한도라면 한도가 풀릴 때까지 API 로 돕니다.")
    if run_url:
        out.append(run_url)
    return "\n".join(out)


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("[인증 알림] DM 수신처 미등록 — 발송 생략(공개 폴백 없음). 내용:\n" + text)
        return False
    sys.path.insert(0, str(Path(__file__).parent))
    from send_telegram_briefing import send_telegram, esc
    send_telegram(token, chat, esc(text))
    return True


def main():
    p = note_path()
    if not p.exists():
        print("[인증 알림] 물러난 호출 없음")
        return 0
    repo, run = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    url = f"https://github.com/{repo}/actions/runs/{run}" if repo and run else ""
    text = build_message(p.read_text(encoding="utf-8", errors="replace").splitlines(), url)
    if not text:
        print("[인증 알림] 물러난 호출 없음")
        return 0
    print(text)
    try:
        send(text)
    except Exception as e:                      # 알림 실패가 탐사 결과를 바꾸지 않게 — 실패는 실패로 보고한다
        print(f"[인증 알림] DM 발송 실패: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
