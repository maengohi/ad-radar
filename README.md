# AD RADAR

한국을 제외한 **일본·태국·미국·영국**의 새 광고 영상을 매일 찾아서, 광고 크리에이티브 관점으로 선별·한국어 번역·해설·태깅해 쌓는 개인 아카이브입니다.

## 취향 규칙
- 제외: 평범한 제품 설명, 유명 모델 의존, 단순 미장센, 일반적인 브랜드 필름, 그저 예쁜 광고
- 우선: IDEA / COPY / DIRECTION / ART / CINEMATOGRAPHY / EDIT / VFX / AI / COMEDY / STORYTELLING / SCALE / WEIRD
- 일본: 카피/언어/인사이트 가중치
- 태국: 비주얼 아이디어/연출/코미디 가중치
- 미국·영국: 스케일/편집/크래프트/빅 아이디어 가중치

## 무료 운영 구조
- Hosting: GitHub Pages
- Scheduler: GitHub Actions
- Discovery: YouTube Data API
- Video analysis + Korean translation: Gemini API (YouTube URL input)
- Archive DB: `data/ads.json`
- Personal SAVE: 브라우저 LocalStorage

## 설치
1. 새 public GitHub repository를 만든 뒤 이 폴더 전체를 `main`에 업로드합니다.
2. Repository → Settings → Secrets and variables → Actions 에 다음 secrets를 추가합니다.
   - `YOUTUBE_API_KEY`
   - `GEMINI_API_KEY`
3. Settings → Pages → Source를 **GitHub Actions**로 선택합니다.
4. Actions 탭에서 `Daily AD RADAR crawl`을 한 번 수동 실행합니다.
5. 이후 매일 06:30 KST에 자동 수집됩니다. GitHub cron은 정확한 초 단위 실행을 보장하지 않습니다.

## 로컬 미리보기
정적 사이트라 간단합니다.

```bash
python -m http.server 8080
```

브라우저에서 `http://localhost:8080` 접속.

## API 키
- YouTube Data API v3 키: Google Cloud Console에서 YouTube Data API v3 활성화 후 생성
- Gemini API 키: Google AI Studio에서 생성

## 데이터 구조
`data/ads.json`은 배열이며 각 레코드에 국가, 영상 URL/썸네일, 캠페인명, RADAR 점수, 한국어 해설, 카피 원문/번역, 태그, 크레딧이 저장됩니다.
