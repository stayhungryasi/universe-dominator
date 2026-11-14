# Claude Code 인증 고르기 — 탐사 엔진(spacex-probe · brk-probe)·자가 수리(self-repair) 공용.
# 실행하지 않고 읽는다:  . scripts/claude_auth.sh   (환경 변수를 이 셸에서 바꿔야 하므로)
#
# 왜(2026-11-14): 탐사·수리 에이전트가 API 키로 돌면서 11월 12일 만에 월 지출 한도(US$150)를 채워
#   조직 전체 API 가 12/1 까지 멈췄다 — 데일리의 집필·번역·뉴스 판정까지 함께 섰다. Claude Code 는
#   구독(Ultra) 토큰으로도 같은 모델을 돌릴 수 있다. 에이전트는 구독으로, 데일리의 파이썬 스크립트는
#   API 키로 갈라 한쪽이 다른 쪽을 세우지 않게 한다.
#
# 규칙:
#   · CLAUDE_AUTH=auto(기본) — 구독 토큰(CLAUDE_CODE_OAUTH_TOKEN)이 있으면 구독, 없으면 API 키(옛 동작 그대로)
#   · CLAUDE_AUTH=subscription — 구독만. 토큰이 없으면 실패(조용히 API 로 새지 않는다)
#   · CLAUDE_AUTH=api — API 키만(구독 토큰이 등록돼 있어도 쓰지 않는다 — 되돌리는 스위치)
#   · 구독으로 갈 때는 ANTHROPIC_API_KEY 를 반드시 지운다: -p 모드의 Claude Code 는 API 키가 환경에 있으면
#     묻지 않고 그 키를 쓴다(구독 토큰보다 우선). 지우지 않으면 '구독으로 옮겼다'고 믿은 채 API 로 과금된다.
#   · 둘 다 없으면 실패 — 침묵은 통과가 아니다.

_claude_auth_mode="${CLAUDE_AUTH:-auto}"
_claude_auth_have_token=0
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then _claude_auth_have_token=1; fi

case "$_claude_auth_mode" in
  auto|subscription|api) ;;
  *)
    echo "::error::CLAUDE_AUTH 값이 잘못됐다: '$_claude_auth_mode' (auto · subscription · api 중 하나)"
    return 1 2>/dev/null || exit 1
    ;;
esac

if [ "$_claude_auth_mode" != "api" ] && [ "$_claude_auth_have_token" = "1" ]; then
  unset ANTHROPIC_API_KEY
  export CLAUDE_AUTH_USED=subscription
  echo "Claude 인증: 구독 토큰 (CLAUDE_AUTH=$_claude_auth_mode)"
elif [ "$_claude_auth_mode" = "subscription" ]; then
  echo "::error::CLAUDE_AUTH=subscription 인데 저장소 시크릿 CLAUDE_CODE_OAUTH_TOKEN 이 비어 있다"
  return 1 2>/dev/null || exit 1
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  unset CLAUDE_CODE_OAUTH_TOKEN
  export CLAUDE_AUTH_USED=api
  echo "Claude 인증: API 키 (CLAUDE_AUTH=$_claude_auth_mode)"
else
  echo "::error::Claude 인증 수단이 없다 — 시크릿 CLAUDE_CODE_OAUTH_TOKEN(구독) 또는 ANTHROPIC_API_KEY 를 등록할 것"
  return 1 2>/dev/null || exit 1
fi
