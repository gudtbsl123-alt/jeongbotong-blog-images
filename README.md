# jeongbotong-blog-images

일본정보통 네이버 블로그(blog.naver.com/jeongbotong_japan) 글에 붙여 넣는 우리 이미지 공개 보관소.

- `<slug>/card-01..07.jpg` — 인스타 카드(@japan_jeongbotong에 이미 올린 것)
- `<slug>/menu-pair.jpg` — 메뉴판 원본 + 한국어로 옮긴 것 나란히
- `<slug>/pair-<a>-<b>.jpg` — 글의 나란히 두 장을 가로로 붙인 한 장(c = 인스타 카드, b = 블로그 사진 번호). 네이버는 붙여넣은 사진을 콜라주로 못 묶는다

복사 페이지가 `https://raw.githubusercontent.com/gudtbsl123-alt/jeongbotong-blog-images/main/<slug>/<file>` 주소로 이미지를 넣어, 네이버 글쓰기에 붙이면 사진까지 한 번에 들어간다.
원본 저장소(비공개) `gudtbsl123-alt/japan-sosiktong`의 `cardnews/out/<slug>/`에서 만든다. 한 장짜리 여행 사진(블로그 NN.jpg)은 원본 주소를 쓰고 여기 올리지 않는다.

## blog/ — 네이버 블로그 게시용 페이지
- `blog/index.html` — 글마다 제목(1안)·글 전체·태그 복사, 사진 받기/복사, 대표 사진(인스타 표지) 표시. 어사이드 브라우저가 이 페이지로 임시저장한다.
- `blog/index.json` — 글 목록(제목·본문·태그·카테고리·사진 순서). `blog/<slug>/photo-NN.jpg` — 글에 들어가는 순서대로의 사진.
- `python3 blog/build.py <블로그 붙여넣기 아티팩트 index.html ...>` 로 추가·갱신한다(같은 slug는 덮어씀).
