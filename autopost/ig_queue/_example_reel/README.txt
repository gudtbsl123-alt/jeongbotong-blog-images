이 폴더는 예시입니다. 이름이 _ 로 시작하는 폴더는 게시되지 않습니다.

릴스 대기열 폴더 = post.json + caption.txt
  source         reel.mp4 가 있는 posting 폴더 (reel_cover.jpg 가 있으면 자동으로 표지)
  publish_at     한국 시간
  share_to_feed  피드에도 보이기 (기본 true)

넣은 뒤: python autopost/ig/ig_publish.py prepare autopost/ig_queue/<폴더이름>
