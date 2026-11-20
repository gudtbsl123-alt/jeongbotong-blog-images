#!/usr/bin/env python3
"""블로그 붙여넣기 페이지(claude.ai 아티팩트 HTML) → blog/<slug>/ 사진 파일 + blog/index.json.

쓰는 법:  python3 blog/build.py <아티팩트 index.html> [<아티팩트 index.html> ...]
- 아티팩트 한 개 = 한 도시 3편. 같은 slug가 이미 있으면 새 내용으로 바꾼다(사진도 다시 씀).
- 어사이드가 이 목록(blog/index.json)과 게시용 페이지(blog/index.html)를 보고 네이버 블로그에 임시저장한다.
"""
import base64, datetime, html, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent
CITY_ORDER = {"도쿄": 0, "오사카": 1, "후쿠오카": 2}
NUM = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}


def parse(path):
    h = pathlib.Path(path).read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA=(\{.*?\});\n", h, re.S).group(1))
    imgs = json.loads(re.search(r"const IMGS=(\{.*?\});\n", h, re.S).group(1))
    page_title = html.unescape(re.search(r"<title>(.*?)</title>", h).group(1))
    posts = []
    for n, (slug, body) in enumerate(re.findall(r'<section class="post" id="([^"]+)"(.*?)</section>', h, re.S)):
        pill, where = re.search(r'<span class="pill">(.*?)</span><span class="where">(.*?)</span>', body).groups()
        pill, where = html.unescape(pill), html.unescape(where)
        city, food = [x.strip() for x in where.split("·", 1)]
        num = NUM.get(pill[0], n + 1)
        store = pill[1:].strip() if pill[0] in NUM else pill
        caps = re.findall(r'<img data-k="(i\d+)" alt=""><figcaption><b>사진 (\d+)</b> · (.*?)<button', body, re.S)
        photos = []
        for key, pn, cap in caps:
            cap_txt = re.sub(r"<[^>]+>", "", html.unescape(cap)).strip()
            is_cover = "대표 사진" in cap_txt
            photos.append({"n": int(pn), "file": f"photo-{int(pn):02d}.jpg",
                           "label": cap_txt.replace("— 대표 사진으로 지정", "").strip(),
                           "cover": is_cover, "_data": imgs[key]})
        if sum(p["cover"] for p in photos) != 1:
            raise SystemExit(f"{slug}: 대표 사진이 정확히 1장이 아님")
        mmdd = slug[:4]
        posts.append({
            "slug": slug, "mmdd": mmdd, "city": city, "city_order": CITY_ORDER.get(city, 9),
            "food": food, "num": num, "store": store,
            "prefix": f"{mmdd} {city}{food.split('·')[0]}{num}",
            "page_title": page_title,
            "category": f"{city} 맛집 > {food}",
            "title": data[f"t{n}0"],
            "titles": [data[f"t{n}{i}"] for i in range(3) if f"t{n}{i}" in data],
            "body_text": data[f"P{n}"], "body_html": data.get(f"HP{n}") or data.get(f"Hb{n}"),
            "tags": data[f"g{n}"],
            "photos": photos,
        })
    return posts


def main(paths):
    idx_path = ROOT / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"posts": []}
    by_slug = {p["slug"]: p for p in idx["posts"]}
    for path in paths:
        for p in parse(path):
            d = ROOT / p["slug"]
            d.mkdir(exist_ok=True)
            for old in d.glob("photo-*.jpg"):
                old.unlink()
            for ph in p["photos"]:
                head, b64 = ph.pop("_data").split(",", 1)
                assert head.startswith("data:image/jpeg"), head
                (d / ph["file"]).write_bytes(base64.b64decode(b64))
            p["v"] = datetime.datetime.now().strftime("%m%d%H%M")
            by_slug[p["slug"]] = p
            print(f"{p['slug']}: 사진 {len(p['photos'])}장, 대표 {[x['n'] for x in p['photos'] if x['cover']][0]}번")
    posts = sorted(by_slug.values(), key=lambda p: (p["food"], p["city_order"], p["mmdd"], p["num"]))
    kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    idx = {"updated": kst.strftime("%Y-%m-%d %H:%M KST"), "posts": posts}
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
