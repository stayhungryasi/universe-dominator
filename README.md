# 우주지배자 사이트 — 설치·운영 가이드

매일 08:00 KST 자동 갱신되는 글로벌 시총 대시보드.
호스팅케이알 도메인 + GitHub Actions + Cloudflare Pages 조합.

---

## 📁 파일 구조

```
universe-dominator/
├── .github/
│   └── workflows/
│       └── daily-update.yml      # 매일 자동 실행 설정
├── scripts/
│   ├── fetch_data.py             # companiesmarketcap.com 스크래퍼
│   ├── build_site.py             # data → index.html 빌드
│   └── template.html             # HTML 템플릿
├── data/
│   └── latest.json               # 최신 데이터 (자동 갱신됨)
├── index.html                    # 실제 사이트 (자동 생성됨)
├── requirements.txt              # Python 패키지
├── .gitignore
└── README.md                     # 이 파일
```

---

## 🚀 1단계: 호스팅케이알 서비스 확인

먼저 부장님이 호스팅케이알에 무엇을 갖고 계신지 확인:

1. https://www.hosting.kr/ 로그인
2. **마이페이지 → 서비스 관리** 메뉴
3. 화면에 무엇이 있는지 확인:
   - **도메인 관리**만 있음 → 도메인만 있음 (이 가이드대로 진행)
   - **웹호스팅**도 있음 → DNS만 쓸 수도 있고 호스팅에 올려도 됨 (이 가이드는 DNS만 쓰는 방식)

> 💡 **이 가이드는 어느 경우든 작동합니다.** Cloudflare Pages가 호스팅을 담당하고, 호스팅케이알은 도메인 DNS만 관리해요.

---

## 🚀 2단계: GitHub 계정 & 저장소 만들기

### 2-1. GitHub 가입
1. https://github.com 접속
2. **Sign up** 클릭
3. 이메일·비밀번호·username 입력 (username은 영문, 예: `kkm-stayhungry`)
4. 이메일 인증

### 2-2. 저장소(Repository) 만들기
1. GitHub 로그인 후 우측 상단 **+ → New repository**
2. 입력:
   - **Repository name**: `universe-dominator` (또는 원하는 이름)
   - **Private** 선택 권장 (코드 비공개)
   - **README, .gitignore, license 모두 체크 해제**
3. **Create repository** 클릭

### 2-3. 파일 업로드
이 패키지(zip) 안의 파일들을 모두 업로드:

**방법 1 — 웹에서 직접 업로드 (간단):**
1. 방금 만든 저장소 페이지에서 **"uploading an existing file"** 링크 클릭
2. zip 파일 압축 해제한 폴더의 **내용 전체**(폴더 자체가 아님)를 드래그앤드롭
3. 아래 **Commit changes** 클릭

**방법 2 — Git 명령어 (CLI 익숙하면):**
```bash
git clone https://github.com/<your-username>/universe-dominator.git
cd universe-dominator
# 받은 파일들 복사
git add .
git commit -m "Initial setup"
git push
```

---

## 🚀 3단계: Cloudflare Pages 연결

### 3-1. Cloudflare 가입
1. https://dash.cloudflare.com/sign-up 가입 (무료)
2. 이메일 인증

### 3-2. Pages 프로젝트 만들기
1. Cloudflare 대시보드에서 **Workers & Pages → Create application → Pages → Connect to Git**
2. GitHub 계정 연결 (권한 승인)
3. 방금 만든 `universe-dominator` 저장소 선택
4. **Set up builds and deployments**:
   - **Project name**: 원하는 이름 (예: `universe-dominator`)
   - **Production branch**: `main`
   - **Framework preset**: `None`
   - **Build command**: 비워두기 (이미 빌드된 index.html 사용)
   - **Build output directory**: `/` (루트)
5. **Save and Deploy** 클릭

배포가 1~2분 만에 끝나고 임시 URL 부여됨 (예: `universe-dominator.pages.dev`).
이 주소로 들어가서 사이트가 잘 뜨는지 확인.

---

## 🚀 4단계: 도메인 연결 (호스팅케이알 DNS → Cloudflare)

### 4-1. Cloudflare Pages에 커스텀 도메인 추가
1. Cloudflare Pages 프로젝트 → **Custom domains** 탭
2. **Set up a custom domain** 클릭
3. 호스팅케이알에서 갖고 계신 도메인 입력 (예: `yourdomain.com`)
4. Cloudflare가 안내하는 **CNAME 값** 복사 (예: `universe-dominator.pages.dev`)

### 4-2. 호스팅케이알에서 DNS 설정
1. https://www.hosting.kr/ → **마이페이지 → 도메인 관리**
2. 해당 도메인 → **DNS 관리** 클릭
3. **레코드 추가**:
   - **종류**: CNAME
   - **호스트**: `@` 또는 빈칸 (루트 도메인) / 또는 `www`
   - **값**: Cloudflare가 알려준 주소 (예: `universe-dominator.pages.dev`)
   - **TTL**: 기본값(3600)
4. 저장

> 💡 만약 CNAME으로 루트 도메인(@) 설정이 안 되면, 대신 A 레코드를 다음 Cloudflare IP로 설정:
> - `192.0.2.1` 또는 Cloudflare가 안내하는 값
> 또는 `www.yourdomain.com` 만 CNAME으로 설정하고 루트는 호스팅케이알의 도메인 포워딩 기능 사용.

### 4-3. SSL 인증서 활성화
Cloudflare가 자동으로 무료 SSL 인증서를 발급해줍니다. 1~24시간 안에 활성화. 그 후 `https://yourdomain.com` 으로 접속 가능.

---

## 🚀 5단계: 자동 갱신 작동 확인

설정이 끝나면 매일 **23:00 UTC (= 08:00 KST 다음날)** 에 자동 갱신됩니다.

### 즉시 테스트 (수동 실행)
1. GitHub 저장소 → **Actions** 탭
2. **Daily Universe Dominator Update** 워크플로우 클릭
3. 우측 **Run workflow** 버튼 → 녹색 확인
4. 1~3분 후 자동으로:
   - companiesmarketcap.com에서 데이터 수집
   - data/latest.json 갱신
   - index.html 재빌드
   - 자동 커밋 & 푸시
   - Cloudflare Pages 자동 재배포 (1~2분)

### 작동 확인
- GitHub Actions 탭에서 ✓ 표시되면 성공
- 사이트(yourdomain.com)에 새 데이터 반영 여부 확인 (상단 날짜 변경됨)

---

## 🔧 잠재지배자 / 변천사 큐레이션 갱신

이 두 섹션은 **자동 수집되지 않습니다** (큐레이션 영역).
직접 수정하실 때는 `data/latest.json` 파일의 `latent`, `history` 부분을 GitHub 웹에서 편집:

1. GitHub 저장소 → `data/latest.json` 클릭
2. ✏️ (연필 아이콘) 클릭
3. 원하는 부분 수정
4. 아래 **Commit changes** 클릭
5. 자동으로 Cloudflare Pages 재배포

또는 다음에 Claude에게 "잠재지배자 갱신해서 사이트용 JSON 만들어줘"라고 하시면 새 `latest.json`을 만들어 드릴 수 있어요.

---

## 🛠 트러블슈팅

### Q1. 스크래퍼가 실패해요 (HTTP 403 / 429)
companiesmarketcap.com이 차단할 수 있습니다. 해결:
- `scripts/fetch_data.py` 에서 `time.sleep(2)` 를 `time.sleep(5)` 로 늘리기
- 또는 `cloudscraper` 패키지로 교체 (`pip install cloudscraper` 후 import 수정)

### Q2. 사이트가 안 떠요
- Cloudflare Pages 대시보드 → **Deployments** 탭에서 배포 상태 확인
- 빌드 실패 시 로그 확인 (보통 단순 파일 누락 문제)

### Q3. 도메인이 연결 안 돼요
- DNS 변경 후 최대 24~48시간 propagation 시간 필요
- https://dnschecker.org/ 에서 도메인 입력해 전파 상태 확인

### Q4. 잠재지배자/변천사가 사라졌어요
스크래퍼는 이 두 섹션을 건드리지 않도록 설계되어 있습니다 (`fetch_data.py`에서 기존 데이터 보존). 만약 사라졌다면 `data/latest.json` 초기 파일로 복원하세요.

---

## 📞 운영 팁

- **모니터링**: GitHub Actions 실패 시 GitHub가 이메일로 알려줌
- **데이터 백업**: 매번 커밋되므로 git history에서 과거 데이터 조회 가능
- **수정 후 즉시 반영**: GitHub에 푸시 → Cloudflare Pages 1~2분 후 자동 배포
- **변경 알림**: 추후 Telegram 봇과 연결해 갱신 시 채널에 자동 포스팅도 가능

---

✨ **Stay hungry. ASI** | 하나증권 The Centerfield W | 김광모 부장
