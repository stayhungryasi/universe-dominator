# UNIVERTRIX GLOBAL — 4개 언어 관측소 설계 (단계 0, 2026-10-26)

> 번역이 아니라 **관측 위치**를 바꾼다. 같은 우주(같은 데이터·같은 숫자)를 ko·en·es·ja 네 행성에서 본다.
> 이 문서는 단계 0 산출물이다 — 인벤토리 · 설계 · 비용 추정 · 결정 대기.

## 1. 화면 문자열 인벤토리 (저장소 2026-10-26 기준, 주석 제외 · 한글을 포함한 문자열 조각)

| 파일 | 조각 | 고유 | 한글 글자 |
|---|---:|---:|---:|
| observatory-template.html | 434 | 374 | 3,296 |
| policies-template.html | 79 | 73 | 1,449 |
| journal-template.html | 84 | 77 | 647 |
| template.html (index) | 64 | 58 | 557 |
| research-template.html | 72 | 66 | 557 |
| community-template.html | 67 | 60 | 481 |
| about-template.html | 33 | 33 | 430 |
| megatrend-template.html | 37 | 33 | 308 |
| pioneers-template.html | 16 | 16 | 193 |
| latent-template.html | 19 | 18 | 134 |
| placeholder-template.html | 10 | 10 | 71 |
| history-template.html | 12 | 12 | 62 |
| build_site.py | 178 | 160 | 803 |
| probe_cockpit.py | 173 | 127 | 519 |
| agent_report.py | 18 | 18 | 102 |

화면 문구를 **상태에서 생성**하는 함수(단계 1·2 대상 — 한국어 문장을 번역하지 않고 같은 상태에서 언어별로 만든다):

- build_site.py: `schedule_html` · `progress_cell` · `status_chip` · `timeline_html` · `engine_html` · `_macro_title` · `_macro_label` · `_macro_full_title` · `macro_badges_html` · `macro_wrap` · `legend_asof`(표시) · `fix_nav`(메뉴) · `inject_footer_links` · `LOGO_HTML` · `PRESENCE_SNIPPET`(접속자 수) · 지역 칩 `FLAG_KO`
- probe_cockpit.py: `shown_status` · `run_info` · `delta_blocks` · `trigger_rows`(when) · `event_rows`(kindKo) · `kpi_series`/`fmt_kind`(단위) · `gauge_svg` · `prob_bar` · `cockpit_html` · `PANELS`
- observatory 표시층(JS): `lgNote` · `lgGuardNote` · `lgBasisNote` · `lgEdge` · `lgCapTip` · `lg13FLine` · `pxZone` · `colKind` · 관제실 IIFE 패널 7종
- 측정층이 데이터에 싣는 문장(단계 2): fetch_buffett.py `untested_note` · `eps_note` · `roe_note` · `conv_note` · `divergence_note` · `conv_check_note` / generate_candidates.py 잠재지배자 `story`·`theme` / fetch_data.py 지역 `label` / weekly_history.py `page_title`·`page_desc`
- 텔레그램(send_telegram_briefing · pipeline_sentinel 등)은 범위 밖 — 한국어 그대로

## 2. 콘텐츠 인벤토리 (지금 저장소 기준 재집계)

| 종류 | 건수 | 한글 글자 | 전체 글자(마크업 포함) | 갱신 주기 |
|---|---:|---:|---:|---|
| 칼럼 columns.json | 82 | 105,477 | 425,482 | 매일 1편(신호 칼럼) + 토 주간 |
| 동행 에세이(게시분) essays.json | 44 (전체 49, 철회 5) | 49,575 | 163,609 | 사건 단위(주 1~3편) |
| 필독 해부 translations.json | 75 | 108,053 | 179,748 | 매일 0~3편 — **영어 원문 보유** |
| 리서치 research.json | 32종목 | 6,532 | 54,649 | full 마다 재수집 |
| 신호 signals.json | 65 | 4,322 | 17,263 | full 마다 — 영어 원제 보유 |
| 탐사 보고서(공개본) | 4 + 변화 이력 | 18,724 + 1,557 | 85,740 | 월 1 + 사건 |
| 논제 원장 thesis | 3 | 1,867 | — | 드묾 |
| 개척자 pioneers.json | 10명 | 918 | 15,611 | full 마다 |
| 메가트렌드 megatrend.json | 1 | 676 | 6,723 | 고정 |
| 거인의 어깨 gurus.json | 27 | 725 | 11,538 | 분기 |
| 종목 desc (latest.json) | 172 | 1,546 | 6,272 | 거의 고정 |
| 주요일정 calendar.json | 77 | 421 | 3,582 | full 마다 |
| 한국어 종목명 | 19 | — | — | 고정 → `data/i18n/names.json` |

합계 한글 약 **30만 자**(백로그).

## 3. 비용 추정 (**추정치** — 실측은 단계 3 원장 `fetch_status kind=i18n` 으로 대조)

가정: 한글 1자 ≈ 1 토큰, 마크업·라틴 1자 ≈ 0.3 토큰, 호출당 고정분(지시·용어 사전) ≈ 1.5K 토큰, 관문 재생성 10%.
단가(Anthropic 1st-party, 2026-10 캐시): Haiku 4.5 $1/$5 · Sonnet 5.5 $2/$10 · (참고) Haiku 5.5 $0.10/$0.50 per MTok.

| 구분 | 입력 토큰(3언어) | 출력 토큰(3언어) | Haiku 4.5 전부 | 혼합(장문 숫자 중심 70% Sonnet 5.5) |
|---|---:|---:|---:|---:|
| 백로그 1회 | ≈ 2.7M | ≈ 1.3M | ≈ $9 | ≈ $16 |
| 해부 원문 재수집 재생성(백로그) | ≈ 1.1M | ≈ 0.5M | ≈ $4 | ≈ $6 |
| 일 증분 | ≈ 0.14M | ≈ 0.05M | ≈ $0.4/일 | ≈ $0.7/일 (월 ≈ $20) |

- 백로그 총액 **≈ $15~25**, 월 운영 **≈ $12~25** 로 추정.
- 회차 상한 40건 → 첫 회차 최근 30일분, 이후 백로그를 나눠 소화(약 1~2주에 소진 예상).

## 4. 설계

- **LANGS 상수 하나**(`scripts/i18n.py`)와 **ALL_PAGES 상수 하나**(build_site)만 읽는다. 빌드는 `LANGS × ALL_PAGES`:
  ko → 루트(주소 불변, G1), en/es/ja → `/en/` `/es/` `/ja/` 같은 파일명. 주입 마커 멱등성은 언어별 산출물마다.
- **메시지 카탈로그** `data/i18n/{ko,en,es,ja}.json` — 키 = 의미 단위(`nav.journal`, `obs.tab.legend`), 매개변수 `{n}`·`{date}`, 복수형 `{"one":…, "other":…}`.
  - HTML: 템플릿의 `{{t:키}}` 를 빌드가 언어별로 치환(HTML 이스케이프).
  - JS: 빌드가 head 첫머리에 `uvx-i18n-v1` 블록을 심는다 — `T(키, 매개변수)` + 숫자·날짜 서식(`UVX.mc` 등) + 그 페이지가 쓰는 키만. **클라이언트 fetch 없음**.
  - ko 카탈로그 = 지금 화면 문장 그대로 → ko 산출물의 렌더 결과는 변경 전과 같아야 한다(회귀 기준).
- **데이터 현지화는 빌드에서**: 지역 라벨·국가명·한국어 종목명은 카탈로그/이름 사전으로 바꿔 심는다. 콘텐츠(칼럼·desc 등)는 번역 캐시가 있으면 그 언어, 없으면 원문 + `lang="ko" data-uvx-pending` 표식 + "번역 준비 중"(그 언어로).
- 경로: en/es/ja 산출물의 `assets/`·`data/` 참조는 `../` 로 옮긴다(페이지 간 링크는 같은 폴더라 그대로).
- 헤더 계기판 캡슐에 언어 전환기(같은 페이지·같은 해시), 첫 방문 언어 안내 띠(localStorage, try/catch), `<html lang>` · hreflang 4개 + x-default(/en/) · canonical(확장자 없는 주소) · og:locale · `sitemap.xml` 대체 링크.
- 서식: ko 조·억(지금 그대로) / en `$5.09T` / es `US$5,09T`(소수점 쉼표) / ja `5.09兆ドル`·`8,120億ドル`. 날짜 ko `2026.10.26` · en `Oct 26, 2026` · es `26 oct 2026` · ja `2026年10月26日`. 결측 `—` 공통.
- 서체: Pretendard 단일 유지 + ja 는 Noto Sans JP 폴백(Google Fonts). 붓글씨 로고는 ko 전용, 다른 언어는 같은 자리에 언어 부제.
- 관문(단계 3): 숫자 집합 동일 — `companion_essays.check_numbers` 를 양방향으로 재사용(번역 ⊆ 원문 · 원문 ⊆ 번역), 언어별 단위(億·兆·万 / mil millones·billones)를 정규화한 뒤 대조. 용어 사전 · 언어별 금칙 · 마크업 구조(차트 블록 위치·개수) · 링크 보존.
- 검증 3중: ① selftest(카탈로그 키 누락 0 · 산출물 한글 0 · hreflang 상호 일치 · 금칙 4개 언어 0) ② Playwright(4언어 × 3페이지 × 2폭 — 가로 넘침 0 · 콘솔 오류 0 · 렌더된 한글은 전부 `lang=ko` 표식 안) ③ 변이(키 삭제 · 숫자 변조 · 한글 주입 → 반드시 실패).

## 5. 결정 대기 (기본안으로 진행 중)

1. 용어 사전 확정(`data/i18n/glossary.json`, 현재 '초안')
2. es 기본 통화 — 기본안 USD
3. es 금액 표기 — 지시대로 `US$5,09T`(T·B). 스페인어 'billón'=10¹² 와 혼동 소지 → 'B' 대신 'mil M' 을 쓸지
4. 로고 부제 문구 — 기본안 en 'Sovereigns' · es 'Soberanos' · ja '宇宙の覇者'('Universe Sovereigns' 는 1440px 헤더 계기판을 밀어내 탈락)
5. 정책·소개 문서 번역본 법적 검수 — 단계 2 에서 en·es·ja 정식 번역을 실었고 페이지 상단에 '번역본 · 검수 대기 · 한국어 원문 우선' 고지(`pol.review`)를 붙였다. 검수 후 고지 문구를 비우면 사라진다
8. 관제센터 사용자 글 — 원문 그대로 + 언어 표식(자동 번역 없음). 번역 버튼을 둘지
9. 커뮤니티 금지 조항의 en·es·ja 문구 — 준법 관문이 'buy or sell'·'買い·売り'를 권고로 읽어 'trading in particular stocks'·'売買を煽る' 로 옮겼다(뜻은 같다)
6. 시차 관측 존 이름 en/es — 탐색·본격·관망·고평가 → Explore·Focus·Watch·Overvalued ('본격'을 'Commit' 으로 옮기면 행동 권유처럼 읽혀 'Focus' 로)
7. 번역 모델 — 지시 기본 Haiku 4.5. Haiku 5.5 가 입력·출력 모두 1/10 단가 → 관문 통과율을 보고 전환할지
