# 오카와리 자동 업로드

- **인스타 카드뉴스·릴스** → 인스타 공식 API로 예약 게시 (GitHub Actions가 30분마다 확인)
- **네이버 블로그** → Aside가 하루 1개 **임시저장까지만** (발행은 직접)

```
japan-sosiktong (제작, 클로드 코드)
   │ push
   ▼
jeongbotong-blog-images (이 저장소)
 ├─ posting/<slug>/            카드 01~08.jpg, reel.mp4, reel_cover.jpg  (지금처럼)
 ├─ autopost/ig_queue/<폴더>/   post.json(어느 posting 폴더를, 언제) + caption.txt
 │      └─▶ Pages에 대기 파일만 공개 ─▶ 30분마다 예약 확인 ─▶ 인스타 게시 ─▶ published.json 기록
 └─ autopost/blog_queue/<폴더>/ 블로그 원고·사진 ─▶ (내 컴퓨터) Aside 루틴 ─▶ 네이버 임시저장
```

| 파일 | 하는 일 |
|---|---|
| `autopost/ig/ig_publish.py` | 인스타 게시 스크립트 (prepare / check / run / now / whoami) |
| `autopost/ig/refresh_token.py` | 60일 토큰 연장 |
| `.github/workflows/okawari-ig-publish.yml` | 30분마다 예약 게시 |
| `.github/workflows/okawari-ig-pages.yml` | 게시 대기 파일만 GitHub Pages로 공개 |
| `.github/workflows/okawari-ig-token-refresh.yml` | 매주 토큰 자동 연장 |
| `autopost/blog/aside_routine.md` | Aside 루틴 설정법 + 붙여 넣을 지시문 |
| `autopost/CLAUDE_AUTOPOST.md` | 클로드 코드가 지킬 대기열 형식·블로그 글쓰기 기준 |

> **왜 Pages에 대기 파일만 올리나요?** 메타는 공개 주소에서 사진·영상을 직접 내려받아 갑니다.
> 그런데 이 저장소는 1GB가 넘어 Pages(최대 1GB)에 통째로 못 올리고, raw 주소는 영상을
> `application/octet-stream` 으로 내보내 릴스가 실패할 수 있습니다. 그래서 아직 게시 안 된 파일만 따로 공개합니다.

---

## 1. 직접 할 일 한눈에 (처음 한 번)

- [ ] 2-1. 메타 앱 만들기 → 테스터 등록 → 토큰 복사
- [ ] 2-2. 저장소 Settings → Pages → Source: **GitHub Actions**
- [ ] 2-3. 깃허브에 `IG_ACCESS_TOKEN` Secret, `PUBLIC_BASE_URL` Variable 등록
- [ ] 3. 토큰 자동 연장용 `GH_PAT` 등록
- [ ] 4. japan-sosiktong 의 `CLAUDE.md` 에 규칙 한 줄 추가
- [ ] 6. Aside 루틴 만들기 (`autopost/blog/aside_routine.md`)

---

## 2. 인스타 API 연결

준비물: 인스타 **프로페셔널 계정**(@okawari_japan은 Business Suite에 연결돼 있어 해당), 페이스북 계정.

### 2-1. 메타 앱 만들기 → 토큰
1. developers.facebook.com → 로그인 → **My Apps → Create App**
2. 사용 사례(Use cases)에서 **인스타그램 메시지·콘텐츠 관리** 항목을 고릅니다. 앱 유형을 물으면 **Business**.
3. 앱 대시보드 **App roles → Roles → Add People → Instagram Tester** → `okawari_japan`
4. 계정 주인이 instagram.com 로그인 → `instagram.com/accounts/manage_access` → **Tester Invites** 탭 → 수락
5. 왼쪽 메뉴 **Instagram → API setup with Instagram business login**
   - 권한 목록에 `instagram_business_basic`, `instagram_business_content_publish` 가 있는지 확인 (없으면 추가)
   - **Generate access tokens → Add account** → okawari_japan 로그인 → 계정 옆 **Generate token** → 토큰 복사
   - 계정 옆 숫자가 `IG_USER_ID` (안 넣어도 자동으로 찾습니다)

> 앱은 **개발 모드(Development) 그대로** 둡니다. 내 계정에만 올리는 용도라 앱 검수(App Review)도, Facebook 페이지 연결도 필요 없습니다.
> 토큰은 **60일짜리**입니다. 3번 자동 연장을 꼭 해 두세요.

### 2-2. 공개 주소 (GitHub Pages) 켜기
1. 이 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions**
2. 주소는 `https://gudtbsl123-alt.github.io/jeongbotong-blog-images` 가 됩니다.
3. 대기열에 게시물이 생기면 `인스타 게시 파일 공개 (Pages)` 워크플로가 알아서 그 파일만 올립니다 (push 후 1~2분).

### 2-3. 깃허브에 등록
이 저장소 **Settings → Secrets and variables → Actions**
- **Secrets** 탭 → New repository secret
  - `IG_ACCESS_TOKEN` = 2-1에서 복사한 토큰
  - `IG_USER_ID` = 계정 숫자 (선택)
- **Variables** 탭 → New repository variable
  - `PUBLIC_BASE_URL` = `https://gudtbsl123-alt.github.io/jeongbotong-blog-images`

### 2-4. 첫 시험
1. 실제로 올릴 게시물 하나를 대기열에 넣고 push (형식은 `CLAUDE_AUTOPOST.md`)
2. Actions 탭에서 `인스타 게시 파일 공개 (Pages)` 가 초록색으로 끝나는지 확인
3. Actions → **인스타 자동 게시 → Run workflow** → 폴더 이름 입력 + `미리보기만` 체크 → 로그에 `공개 주소 모두 열림` 확인
4. 괜찮으면 `미리보기만` 을 끄고 다시 실행 (또는 예약 시각까지 기다림) → 인스타에서 확인

로컬에서 확인하고 싶을 때 (선택)
```bash
pip install -r autopost/ig/requirements.txt
cp autopost/ig/.env.example autopost/ig/.env     # 메모장으로 열어 토큰 붙여넣기
python autopost/ig/ig_publish.py whoami          # [OK] @okawari_japan 이 나오면 연결 성공
python autopost/ig/ig_publish.py check <폴더>     # 파일마다 [열림] ... image/jpeg, 릴스는 video/mp4
```

---

## 3. 토큰 자동 연장 (꼭 해 두기)

매주 월요일 새벽에 60일 토큰을 다시 60일로 늘리고 Secret을 바꿔 줍니다. Secret을 바꿀 수 있는 깃허브 토큰이 필요합니다.

1. 깃허브 오른쪽 위 프로필 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
   - Repository access: **Only select repositories** → `jeongbotong-blog-images`
   - Permissions → Repository permissions → **Secrets: Read and write**
   - Expiration: 1년 (달력에 만료 알림)
2. 이 저장소 Secrets에 `GH_PAT` 이름으로 등록
3. 인스타 토큰을 만든 지 **24시간 지난 뒤** Actions → **인스타 토큰 연장 → Run workflow** 로 한 번 시험

> GH_PAT가 없으면 연장 워크플로는 연장하지 않고 실패로 끝나며, 깃허브가 메일로 알려 줍니다.

---

## 4. 클로드 코드 연결

제작 저장소 **japan-sosiktong** 의 `CLAUDE.md` 에 한 줄 추가합니다.

```
카드·릴스를 jeongbotong-blog-images 에 올린 뒤, 인스타 게시 대기열과 블로그 원고는 jeongbotong-blog-images/autopost/CLAUDE_AUTOPOST.md 규칙대로 넣는다.
```

---

## 5. 매일 운영

1. 클로드 코드가 `posting/<slug>/` 에 카드·릴스를 올리고, `autopost/ig_queue/<폴더>/` 에 `post.json`·`caption.txt` 를 넣고 `prepare` → push
2. Pages 워크플로가 그 파일을 공개하고, 예약 시각이 지나면 30분 안에 게시됩니다 (깃허브 예약 실행은 바쁠 때 몇 분~수십 분 늦을 수 있음).
3. 결과는 대기열 폴더에 남습니다.
   - `published.json` — 게시 완료, 인스타 주소
   - `error.json` — 실패 이유 (깃허브가 실패 메일도 보냄). 고친 뒤 **error.json을 지우고** push하면 다시 시도
   - `pending.json` — 메타가 영상 처리 중. 다음 실행에서 자동으로 이어짐
4. 지금 바로 올리기: Actions → 인스타 자동 게시 → Run workflow → 폴더 이름 입력

안전장치
- 한 번 실행에 **1개만** 올립니다. 같은 시각 예약이 겹치면 30분 간격으로 나뉩니다.
- 예약 시각이 **12시간 넘게 지난** 게시물은 자동으로 올리지 않고 error.json을 남깁니다 (몰아서 올라가는 일 방지).
- 방금 push해서 공개 주소에 아직 파일이 없으면 오류로 치지 않고 다음 실행에서 다시 확인합니다.
- 토큰 문제는 게시물 폴더를 건드리지 않고 실행 전체를 멈춥니다.
- 올릴 게 없을 때는 토큰을 쓰지 않고 바로 끝나서, 세팅 전에도 실패 메일이 오지 않습니다.

## 6. 블로그 (Aside)

→ `autopost/blog/aside_routine.md`

---

## 자주 나는 오류

| 메시지 | 할 일 |
|---|---|
| `[메타 오류 190]` 토큰 만료 | 2-1에서 새 토큰 → `IG_ACCESS_TOKEN` Secret 교체, 3번 자동 연장 확인 |
| 공개 주소에서 파일을 못 찾았습니다 | Pages 워크플로가 성공했는지, `PUBLIC_BASE_URL` 오타, posting 파일을 push 했는지 확인 |
| `2207052` 메타가 파일을 못 가져옴 | `check` 로 형식 표시(Content-Type) 확인 |
| source 폴더가 없습니다 | `post.json` 의 `source` 가 `posting/<slug>` 처럼 저장소 맨 위 기준인지 확인 |
| 허용 비율 밖 / JPEG만 받습니다 | `prepare` 실행 (4:5로 여백을 넣고 JPEG로 바꿈, 원본은 `_src/`) |
| 영상 코덱·fps·색 형식 | H.264 + AAC, 30fps, yuv420p 로 다시 출력 |
| 예약 시각이 12시간 넘게 지나 | `publish_at` 을 고치고 error.json 지우기 |

## 알아 둘 점
- 인스타 API 게시 한도는 계정당 24시간에 수십 개 이상이라(캐러셀은 1개로 셈) 하루 3~8개는 넉넉합니다. `whoami` 로 사용량을 볼 수 있습니다.
- 캐러셀은 모든 장이 **첫 장 비율로** 잘립니다. 카드는 지금처럼 1080×1350 유지.
- 공개 저장소라 예약해 둔 게시물 파일이 게시 전에도 주소로 보일 수 있습니다.
- 메타 앱 대시보드 메뉴 이름은 바뀔 수 있습니다. 위치가 다르면 이름으로 찾아 들어가면 됩니다.
