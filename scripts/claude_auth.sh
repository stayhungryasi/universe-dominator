# Claude Code 인증 고르기 — 탐사 엔진(spacex-probe · brk-probe)·자가 수리(self-repair) 공용.
# 실행하지 않고 읽는다:  . scripts/claude_auth.sh   (환경 변수를 이 셸에서 바꿔야 하므로)
#
# 왜(2026-11-14 ㉕): 탐사·수리 에이전트가 API 키로 돌면서 11월 12일 만에 월 지출 한도(US$150)를 채워
#   조직 전체 API 가 12/1 까지 멈췄다 — 데일리의 집필·번역·뉴스 판정까지 함께 섰다. Claude Code 는
#   구독(Ultra) 토큰으로도 같은 모델을 돌릴 수 있다. 에이전트는 구독으로, 데일리의 파이썬 스크립트는
#   API 키로 갈라 한쪽이 다른 쪽을 세우지 않게 한다.
#
# 규칙:
#   · CLAUDE_AUTH=auto(기본) — 구독 토큰(CLAUDE_CODE_OAUTH_TOKEN)이 있으면 구독, 없으면 API 키(옛 동작 그대로)
#   · CLAUDE_AUTH=subscription — 구독만. 토큰이 없거나 구독이 응답하지 않으면 실패(조용히 API 로 새지 않는다)
#   · CLAUDE_AUTH=api — API 키만(구독 토큰이 등록돼 있어도 쓰지 않는다 — 되돌리는 스위치)
#   · 구독으로 갈 때는 ANTHROPIC_API_KEY 를 반드시 지운다: -p 모드의 Claude Code 는 API 키가 환경에 있으면
#     묻지 않고 그 키를 쓴다(구독 토큰보다 우선). 지우지 않으면 '구독으로 옮겼다'고 믿은 채 API 로 과금된다.
#   · 결과가 달라지지 않게 — 모델 별칭 'best' 는 구독에서 다른 모델(Fable)로 풀릴 수 있다. 구독 경로에서는
#     'best' 를 지금 API 경로가 실제로 쓰는 모델(CLAUDE_SUBSCRIPTION_BEST, 기본 claude-opus-5-5)로 고정한다.
#     저장소 변수로 모델을 명시했으면(PROBE_MODEL·REPAIR_MODEL ≠ best) 그 값을 그대로 둔다.
#   · 구독이 이번 회차를 못 돌리면(토큰 만료·사용량 한도·옵션 거절) auto 는 API 키로 물러난다 — 탐사는 서지 않는다.
#     물러난 사실은 CLAUDE_AUTH_FALLBACK_FILE 에 적고, 워크플로의 알림 스텝(claude_auth_alarm.py)이 소장 DM 으로 알린다.
#     실제 호출과 같은 모델·같은 예산 옵션으로 한 턴을 먼저 돌려 본다(사전 점검). 회차 도중에 한도에 닿는 것은
#     여기서 못 막는다 — 그 회차는 관문 실패 경로(probe/ 브랜치 + DM)로 간다.
#   · 둘 다 없으면 실패 — 침묵은 통과가 아니다.

_ca_mode="${CLAUDE_AUTH:-auto}"
_ca_note="${CLAUDE_AUTH_FALLBACK_FILE:-${RUNNER_TEMP:-/tmp}/claude_auth_fallback.txt}"
_ca_py="$(command -v python || command -v python3 || true)"

_ca_fail() {
  echo "::error::$1"
}

# 구독으로 한 턴 — 실제 호출과 같은 모델·예산 옵션. 성공이면 0.
_ca_ping() {
  _ca_out="$(env -u ANTHROPIC_API_KEY timeout "${CLAUDE_AUTH_PING_TIMEOUT:-180}" claude -p "Reply with the single word: ok" \
      --model "$1" --max-turns 1 --max-budget-usd 1 --tools "" --output-format json 2>&1)" || {
    _ca_why="종료 코드 $? · $(printf '%s' "$_ca_out" | tail -c 300 | tr '\n' ' ')"
    return 1
  }
  if [ -z "$_ca_py" ]; then return 0; fi
  _ca_why="$(printf '%s' "$_ca_out" | "$_ca_py" -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw[raw.index("{"):])
except Exception:
    print("응답을 읽지 못함 · " + raw[-200:].replace(chr(10), " ")); sys.exit(0)
if d.get("is_error") or d.get("subtype") not in (None, "success"):
    print("오류 응답 · " + str(d.get("subtype")) + " · " + str(d.get("result"))[:200].replace(chr(10), " "))
')"
  [ -z "$_ca_why" ]
}

_ca_use_api() {
  unset CLAUDE_CODE_OAUTH_TOKEN
  export CLAUDE_AUTH_USED="$1"
  echo "Claude 인증: API 키 (CLAUDE_AUTH=$_ca_mode)"
}

case "$_ca_mode" in
  auto|subscription|api) ;;
  *)
    _ca_fail "CLAUDE_AUTH 값이 잘못됐다: '$_ca_mode' (auto · subscription · api 중 하나)"
    return 1 2>/dev/null || exit 1
    ;;
esac

if [ "$_ca_mode" != "api" ] && [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  # 구독 경로의 모델 — 'best' 만 지금 쓰는 모델로 고정(명시한 모델은 그대로)
  _ca_model="${PROBE_MODEL:-${REPAIR_MODEL:-best}}"
  if [ "$_ca_model" = "best" ]; then _ca_model="${CLAUDE_SUBSCRIPTION_BEST:-claude-opus-5-5}"; fi
  _ca_why=""
  if _ca_ping "$_ca_model"; then
    unset ANTHROPIC_API_KEY
    if [ -n "${PROBE_MODEL:-}" ]; then export PROBE_MODEL="$_ca_model"; fi
    if [ -n "${REPAIR_MODEL:-}" ]; then export REPAIR_MODEL="$_ca_model"; fi
    export CLAUDE_AUTH_USED=subscription
    echo "Claude 인증: 구독 토큰 · 모델 $_ca_model (CLAUDE_AUTH=$_ca_mode)"
  elif [ "$_ca_mode" = "subscription" ]; then
    _ca_fail "구독 인증 사전 점검 실패(CLAUDE_AUTH=subscription 이라 API 로 물러나지 않는다) — $_ca_why"
    return 1 2>/dev/null || exit 1
  elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "::warning::구독 인증 사전 점검 실패 — 이번 호출은 API 키로 돈다(과금). 사유: $_ca_why"
    printf '%s\n' "${GITHUB_WORKFLOW:-?} · 모델 $_ca_model · $_ca_why" >> "$_ca_note" 2>/dev/null || true
    _ca_use_api api-fallback
  else
    _ca_fail "구독 인증 사전 점검 실패 · 물러날 API 키도 없다 — $_ca_why"
    return 1 2>/dev/null || exit 1
  fi
elif [ "$_ca_mode" = "subscription" ]; then
  _ca_fail "CLAUDE_AUTH=subscription 인데 저장소 시크릿 CLAUDE_CODE_OAUTH_TOKEN 이 비어 있다"
  return 1 2>/dev/null || exit 1
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  _ca_use_api api
else
  _ca_fail "Claude 인증 수단이 없다 — 시크릿 CLAUDE_CODE_OAUTH_TOKEN(구독) 또는 ANTHROPIC_API_KEY 를 등록할 것"
  return 1 2>/dev/null || exit 1
fi
