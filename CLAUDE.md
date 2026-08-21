# CLAUDE.md — UNIVERTRIX (우주지배자, univertrix.com)

## 프로젝트 정체
- 글로벌 시가총액 추적·시각화 사이트. 세계관: 기업=천체, 시총 1위=태양(왕좌).
- **존재 이유: 운영자(선장님)의 AI 시대 훈련장.** 기능 제안 기준은 "화려한가"가 아니라 "선장님을 강하게 만드는가".
- 완전 자동화가 북극성 — 수동 단계는 결함이다.
- 호칭: 운영자를 **선장님**으로 부른다.

## 스택 & 구조
- 정적 사이트: `scripts/build_site.py`가 `scripts/*-template.html` → 루트 `*.html` 12페이지 생성
- 데이터: `data/latest.json`(시총 스냅샷), `data/signals.json`(신호 관측소), `data/columns.json` 등
- 파이프라인: GitHub Actions `daily-update.yml` — quick(빌드+배포 ~2분) / full(전체 수집 ~10분), 러너 **python 3.12 고정**
- 3D: Three.js r128 (observatory-template.html의 boot3d), 텍스처 `assets/textures/`
- 호스팅: Cloudflare Pages

## 디자인 확정 스펙 (오로라, 2026-08 확정 — 임의 변경 금지)
- 낮: 스노우 #FBFBFC / 그래파이트 #16181D / 카드 #F5F5F7
- 밤: 우주 블랙 #0A0B0F / #F4F5F8 / 카드 #12141B
- 액센트: 인디고 #565CE8 → 사이언 #3EC8D8 (밤: #8B93FF → #4FE0EF)
- 서체: Pretendard 단일(위계는 굵기 100↔900), 로고 한글만 Nanum Brush Script
- 판형: **1080px 단일 기둥** — 헤더·메뉴·본문·히어로 오버레이 전부 동일 기둥
- 로고: 오로라 UNIVERTRIX(굵기 호흡 6s) + U→X 궤도선 + 붓글씨 우주지배자(광 스윕 호흡)
- 팔레트는 CSS 토큰 리맵 방식: `AURORA_GLOBAL_CSS`(build_site.py)가 전 페이지 </head> 직전 주입

## 작업 규칙 (반드시)
1. 코드 전달·커밋 전 검증: `python -m py_compile scripts/*.py` + 산출 HTML의 `<script>` 블록 `node --check`
2. **실빌드가 최고의 검증**: `python scripts/build_site.py` 로컬 실행 후 산출물 검사
3. `python scripts/selftest.py` 통과 필수 (수집 스크립트 전수 py_compile 포함)
4. 멱등성: 모든 주입은 마커 검사 후 1회만 (`ud-aurora-global-v1`, `ud-hdr-refine-v4`, `wide-fix` 등)
5. 자동 커밋 파일(columns.json 등)은 항상 원격 최신본 기준 — pull 먼저
6. 사고 시: 원인 규명 → 재현 검증 케이스 → 수리 → 재발 방지 3중화
7. 검증 불가 영역(첫 실전 실행 등)은 정직하게 리스크 고지

## 알려진 지뢰 (재발 방지 기록)
- **f-string 중첩 따옴표**: 러너 3.11에서 SyntaxError났던 이력 → 3.12 고정했지만 변수 분리 습관 유지
- **3D 블록 부분 추출 금지**: observatory의 boot3d는 FLAG_COLOR·flagColor·sunTex·milkyTex·cleanupSolar와 한 몸 — 연속 범위로만 다룰 것 (2회 사고)
- **node --check는 미정의 참조를 못 잡는다** — 자유 함수 호출 vs 정의 대조 스캔 병행
- ~~주입 페이지 목록이 5곳에 흩어져 있음~~ → **2026-08-15 해결**: `build_site.py`의 `ALL_PAGES` 상수 하나로 통합(5개 주입 함수 전부 이 상수만 읽음). 새 페이지 추가는 ① build_xxx() ② main() 호출 ③ ALL_PAGES 등재 3단계뿐. selftest가 매 실행마다 ⓐ 5개 함수의 ALL_PAGES 사용 ⓑ 지역 목록 부활 여부 ⓒ 루트 HTML↔목록 일치를 검사해 등재 누락 시 즉시 실패. 주입 제외 파일은 `UNMANAGED_PAGES`(현재 my-universe.html 리다이렉트 스텁)에 명시
- 국기 이모지는 윈도우 크롬에서 깨짐 → flagcdn 이미지 사용
- 신호 pub 날짜는 RFC822/ISO 혼재 — email.utils 파싱 필수
- **프로브가 침묵하면 환경보다 프로브 문법부터 의심** (2026-08-17, 며칠간 반복 오판): 헤드리스 크롬 검증 스크립트가 아무 출력도 안 낼 때 "CDN 지연·virtual-time·load 이벤트 미발화" 같은 환경 탓으로 결론내고 정적 검증으로 갈음했는데, 진짜 원인은 프로브 코드의 문법 오류였다. 파이썬으로 JS를 생성하면서 `'\n'`이 **실제 개행**으로 풀려 문자열 리터럴이 깨졌고, 스크립트 블록 전체가 실행되지 않았다. 증상이 지독한 이유: `<pre>`는 초기값 그대로 남고 `window.onerror`조차 안 걸려 **완전 무증상**이다.
  - 규칙 ① 파이썬이 생성하는 JS에는 개행 이스케이프를 쓰지 말고 `String.fromCharCode(10)`
  - 규칙 ② 프로브 첫 줄에 `el.textContent='script-started'`를 박아 실행 여부부터 분리 판정
  - 규칙 ③ 그래도 침묵하면 dump에서 해당 `<script>` 원문을 눈으로 확인 (개행이 섞였는지)
  - 규칙 ④ "환경 탓"으로 결론내기 전에 동일 환경에서 3줄짜리 최소 페이지가 도는지 먼저 확인
  - 대가: 빈 상태 렌더·CSS 계산값·3D 간섭 등 여러 건을 실측 대신 추론으로 보고했다. 도구가 조용히 죽으면 검증한 줄 알고 넘어간다 — 침묵은 통과가 아니다.

## 미결 (등재만 — 지시 없이 착수 금지)
- **신호 소스별 fetch 상태 기록 → 정비 관제탑 2단계** (등재 2026-08-21)
  - 현재 sentinel은 `signals.json`의 결과물만 보므로 **"조용한 날"과 "죽은 소스"를 구분하지 못한다.** 소스가 0건일 때 그날 발표가 없었던 것인지(정상), 404·타임아웃으로 못 가져온 것인지(장애) 알 수 없다.
  - 그래서 오탐을 피하려 연속 0건 임계를 4회(≈하루 반)로 늦춰 뒀다 — 그만큼 **탐지가 늦다**. 근거 사례: 2026-08-20 Anthropic 하루 종일 0건(다음날 3건 정상) → 임계 2회였다면 오탐.
  - 개선안: `fetch_signals`가 소스별 fetch 결과(`ok`/`http_404`/`timeout`/`parse_fail` + 원문 항목 수)를 `signals.json` 메타에 남긴다. 그러면 sentinel이 "요청 실패"는 **즉시** 경보하고, "성공했는데 0건"만 streak으로 누적할 수 있다 → 임계를 다시 낮출 수 있다.
  - 주의: 이는 수집 스크립트를 건드리는 일이다. 저하 동작(한 소스가 죽어도 나머지는 수집)은 절대 바꾸지 말고 **기록만 추가**할 것.

## 커뮤니케이션
- 한국어, 간결하게. "허접한데?" 류 터프한 피드백 = 품질 요구 → 즉시 개선으로 응답
- 큰 변경 전 요약 보고, 파괴적 작업(삭제·강제 푸시)은 반드시 사전 확인
- 배포: push 후 Actions에서 Run workflow(quick) — 배포 확인까지가 작업 완료
