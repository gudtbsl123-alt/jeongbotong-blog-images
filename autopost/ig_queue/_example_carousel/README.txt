이 폴더는 예시입니다. 이름이 _ 로 시작하는 폴더는 게시되지 않습니다.

카드뉴스(캐러셀) 대기열 폴더 = post.json + caption.txt
  source      카드가 있는 posting 폴더 (저장소 맨 위 기준)
  publish_at  한국 시간
카드 파일은 posting/<slug>/ 에 그대로 두고 복사하지 않습니다.

넣은 뒤: python autopost/ig/ig_publish.py prepare autopost/ig_queue/<폴더이름>
