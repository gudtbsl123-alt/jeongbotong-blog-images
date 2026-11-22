#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
오카와리 인스타그램 자동 게시 (Instagram API with Instagram Login)

명령
  prepare [폴더...]         올리기 전 점검 + 자동 수정 (PNG→JPEG, 비율 맞춤, 영상 moov 앞으로)
                            → 클로드 코드가 push 전에 로컬에서 실행
  check   [폴더...]         점검만 하고, 공개 주소에서 파일이 열리는지까지 확인
  run     [--dry-run]       예약 시각이 된 게시물을 게시 (GitHub Actions가 30분마다 실행)
  now 폴더 [--dry-run]      그 폴더를 지금 바로 게시
  whoami                    토큰이 맞는지, 어느 계정인지, 24시간 게시 한도 확인
  sources / site            GitHub Actions 전용 (필요한 폴더만 받기 / Pages에 올릴 파일 모으기)

폴더를 안 적으면 대기열(autopost/ig_queue)에서 아직 게시 안 된 폴더 전부가 대상입니다.

환경 변수 (autopost/ig/.env 또는 GitHub Secrets/Variables)
  IG_ACCESS_TOKEN  필수. 메타 앱 대시보드에서 만든 60일 토큰
  IG_USER_ID       선택. 없으면 토큰으로 자동 조회
  PUBLIC_BASE_URL  게시 대기 파일의 공개 주소 (GitHub Pages). {sha}는 커밋 해시로 바뀝니다.
                   예) https://gudtbsl123-alt.github.io/jeongbotong-blog-images
  IG_QUEUE_DIR     선택. 대기열 폴더 (기본 autopost/ig_queue)
  IG_MAX_PER_RUN   선택. 한 번 실행에 올릴 최대 개수 (기본 1 → 같은 시각 예약은 30분 간격으로 나뉨)
  IG_STALE_HOURS   선택. 예약 시각이 이만큼 지난 게시물은 자동으로 올리지 않음 (기본 12)
  IG_API_VERSION   선택. 기본 v26.0
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from PIL import Image, ImageOps, ImageStat
except ImportError:  # pragma: no cover
    Image = ImageOps = ImageStat = None

KST = timezone(timedelta(hours=9), "KST")  # 한국은 서머타임이 없어 고정 +9시간이면 정확함

JPEG_EXT = {".jpg", ".jpeg"}
CONVERT_EXT = {".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEO_EXT = {".mp4", ".mov"}
MEDIA_EXT = JPEG_EXT | CONVERT_EXT | VIDEO_EXT

MIN_RATIO, MAX_RATIO = 4 / 5, 1.91      # 인스타 API 사진 허용 비율
EPS = 1e-6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024
GITHUB_FILE_LIMIT = 100 * 1024 * 1024
MAX_WIDTH = 1440

COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "reel_cover.jpg", "reel_cover.jpeg", "reel_cover.png")
SKIP_PREFIXES = ("cover.", "reel_cover.", "story")   # 자동으로 고를 때 빼는 파일 (표지·스토리용)

PUBLISHED = "published.json"
ERROR = "error.json"
PENDING = "pending.json"

SETUP_CODES = {190, 10, 200}             # 토큰·권한 문제 → 게시물 탓이 아님
RATE_CODES = {4, 17, 32, 613}
RATE_SUBCODES = {2207042}
CODE_HINTS = {
    190: "토큰이 만료됐거나 잘못됐습니다. 앱 대시보드에서 토큰을 다시 만들어 IG_ACCESS_TOKEN을 바꾸세요.",
    10: "권한이 없습니다. 앱에 instagram_business_content_publish 권한이 있는지 확인하세요.",
    200: "권한이 없습니다. 앱에 instagram_business_content_publish 권한이 있는지 확인하세요.",
    4: "호출 한도에 걸렸습니다. 다음 실행에서 다시 시도합니다.",
    36003: "사진 비율이 허용 범위(4:5~1.91:1) 밖입니다. prepare를 실행하세요.",
}
SUBCODE_HINTS = {
    2207052: "메타가 공개 주소에서 파일을 못 가져왔습니다. push 여부와 PUBLIC_BASE_URL을 확인하고, raw 주소라면 GitHub Pages로 바꿔 보세요.",
    2207042: "24시간 게시 한도에 걸렸습니다. 다음 실행에서 이어집니다.",
    2207026: "영상 형식이 맞지 않습니다. H.264 + AAC, 23~60fps, 3초~15분으로 다시 출력하세요.",
}

DRY_RUN = False


# ───────────────────────── 공통 도우미 ─────────────────────────
class SetupError(Exception):
    """설정 문제 (토큰·주소·패키지). 게시물 폴더에는 기록하지 않음"""


class PostError(Exception):
    """이 게시물 하나의 문제. 폴더에 error.json으로 기록"""


class IGError(PostError):
    def __init__(self, msg, code=None, subcode=None, transient=False):
        super().__init__(msg)
        self.code = code
        self.subcode = subcode
        self.transient = transient

    @property
    def rate_limited(self):
        return self.code in RATE_CODES or self.subcode in RATE_SUBCODES


def log(msg: str = "") -> None:
    print(msg, flush=True)


def gh_annotate(level: str, title: str, msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    esc = lambda s: s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    title = esc(title).replace(":", "%3A").replace(",", "%2C")
    print(f"::{level} title={title}::{esc(msg)}", flush=True)


def now_kst() -> datetime:
    return datetime.now(KST)


_ROOT: Path | None = None


def repo_root() -> Path:
    global _ROOT
    if _ROOT is None:
        ws = os.environ.get("GITHUB_WORKSPACE")
        if ws:
            _ROOT = Path(ws).resolve()
        else:
            try:
                out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                     text=True, check=True, cwd=Path(__file__).resolve().parent)
                _ROOT = Path(out.stdout.strip()).resolve()
            except Exception:
                _ROOT = Path.cwd().resolve()
    return _ROOT


def load_dotenv() -> None:
    for p in (Path(__file__).resolve().parent / ".env", repo_root() / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root(), capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "main"


def public_base() -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not base:
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not repo:
            raise SetupError("PUBLIC_BASE_URL이 없습니다. autopost/README.md '2-2. 공개 주소'를 보세요.")
        base = f"https://raw.githubusercontent.com/{repo}/{{sha}}"
    if "{sha}" in base:
        base = base.replace("{sha}", git_sha())
    return base.rstrip("/")


def public_url(path: Path) -> str:
    rel = path.resolve().relative_to(repo_root()).as_posix()
    return public_base() + "/" + urllib.parse.quote(rel)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        return None


def write_json(path: Path, data) -> None:
    if DRY_RUN:
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def parse_kst(raw) -> datetime:
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y.%m.%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=KST)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except ValueError:
        raise ValueError(f"publish_at 형식을 알 수 없습니다: {raw!r} (예: \"2026-11-23 19:10\")")


def queue_dir() -> Path:
    q = Path(os.environ.get("IG_QUEUE_DIR") or "autopost/ig_queue")
    return q if q.is_absolute() else repo_root() / q


# ───────────────────────── 게시물 폴더 ─────────────────────────
class Post:
    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        self.name = folder.name
        self.data: dict = {}
        self.load_error = None
        try:
            data = json.loads((folder / "post.json").read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("맨 바깥이 { } 여야 합니다")
            self.data = data
        except FileNotFoundError:
            self.load_error = "post.json이 없습니다"
        except ValueError as e:
            self.load_error = f"post.json을 읽을 수 없습니다: {e}"
        cap = folder / "caption.txt"
        self.caption = cap.read_text(encoding="utf-8-sig").strip() if cap.is_file() else None

    def has(self, name: str) -> bool:
        return (self.folder / name).exists()

    def source_rel(self):
        """post.json의 source (저장소 맨 위 기준 폴더, 예: posting/1118-fukuoka-ramen2-roundup)"""
        src = str(self.data.get("source") or "").strip().strip("/")
        return src or None

    def source_dir(self) -> Path:
        rel = self.source_rel()
        return (repo_root() / rel).resolve() if rel else self.folder

    def cover_name(self):
        c = self.data.get("cover")
        if c:
            return c
        for n in COVER_NAMES:
            if (self.source_dir() / n).is_file():
                return n
        return None

    def _type_field(self) -> str:
        t = str(self.data.get("type") or "").lower().strip()
        return {"reels": "reel", "video": "reel", "photo": "image", "album": "carousel"}.get(t, t)

    def media_files(self) -> list[Path]:
        d = self.source_dir()
        names = self.data.get("media")
        if names:
            return [d / n for n in names]
        if not d.is_dir():
            return []
        cover = self.cover_name()
        files = [p for p in sorted(d.iterdir())
                 if p.is_file() and p.suffix.lower() in MEDIA_EXT and p.name != cover
                 and not p.name.lower().startswith(SKIP_PREFIXES)
                 and ".tmp." not in p.name and ".faststart." not in p.name]
        t = self._type_field()
        if t == "reel":
            return [p for p in files if p.suffix.lower() in VIDEO_EXT]
        if t in ("carousel", "image"):
            return [p for p in files if p.suffix.lower() not in VIDEO_EXT]
        return files

    def post_type(self) -> str:
        t = self._type_field()
        if t:
            return t
        files = self.media_files()
        if len(files) == 1:
            return "reel" if files[0].suffix.lower() in VIDEO_EXT else "image"
        return "carousel"

    def publish_at(self):
        raw = self.data.get("publish_at")
        return parse_kst(raw) if raw else None


def list_posts() -> list[Post]:
    q = queue_dir()
    if not q.is_dir():
        return []
    return [Post(p) for p in sorted(q.iterdir())
            if p.is_dir() and not p.name.startswith(("_", "."))]


def resolve_folder(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p.resolve()
    q = queue_dir() / arg
    if q.is_dir():
        return q.resolve()
    raise SetupError(f"폴더를 찾을 수 없습니다: {arg}")


# ───────────────────────── 점검 ─────────────────────────
def caption_errors(text: str) -> list[str]:
    errs = []
    if len(text) > 2200:
        errs.append(f"캡션이 {len(text)}자입니다 → 2,200자 이하로 줄이세요")
    tags = re.findall(r"#[^\s#]+", text)
    if len(tags) > 30:
        errs.append(f"해시태그가 {len(tags)}개입니다 → 30개 이하로")
    mentions = re.findall(r"(?<![\w.])@[A-Za-z0-9._]+", text)
    if len(mentions) > 20:
        errs.append(f"@언급이 {len(mentions)}개입니다 → 20개 이하로")
    return errs


def exif_orientation(im) -> int:
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def check_image(f: Path):
    errors, warns = [], []
    if f.suffix.lower() not in JPEG_EXT:
        errors.append(f"{f.name}: 인스타 API는 JPEG만 받습니다 → prepare로 변환하세요")
        return errors, warns, None
    size = f.stat().st_size
    if size > MAX_IMAGE_BYTES:
        errors.append(f"{f.name}: 8MB를 넘습니다 ({size / 1e6:.1f}MB) → prepare로 줄이세요")
    if Image is None:
        warns.append("Pillow가 없어 사진 비율 점검을 건너뜁니다 (pip install Pillow)")
        return errors, warns, None
    try:
        with Image.open(f) as im:
            fmt, (w, h), mode, orient = im.format, im.size, im.mode, exif_orientation(im)
    except Exception as e:
        errors.append(f"{f.name}: 사진을 열 수 없습니다 ({e})")
        return errors, warns, None
    if orient in (5, 6, 7, 8):
        w, h = h, w
    if orient != 1:
        warns.append(f"{f.name}: 사진에 회전 정보가 있습니다 → prepare로 똑바로 저장하세요")
    if fmt != "JPEG":
        errors.append(f"{f.name}: 확장자는 jpg인데 실제 형식은 {fmt}입니다 → prepare로 변환하세요")
    if mode not in ("RGB", "L"):
        errors.append(f"{f.name}: 색 모드가 {mode}입니다 → prepare로 RGB 변환하세요")
    ratio = w / h
    if ratio < MIN_RATIO - EPS or ratio > MAX_RATIO + EPS:
        errors.append(f"{f.name}: {w}×{h}는 허용 비율(4:5~1.91:1) 밖입니다 → prepare가 여백을 넣어 맞춥니다")
    if w > MAX_WIDTH:
        warns.append(f"{f.name}: 가로 {w}px → 인스타가 1440px로 줄입니다")
    if w < 320:
        warns.append(f"{f.name}: 가로 {w}px로 너무 작습니다")
    return errors, warns, ratio


def moov_first(path: Path) -> bool:
    """MP4/MOV에서 moov 상자가 mdat보다 앞에 있으면 True"""
    total = path.stat().st_size
    with open(path, "rb") as fh:
        pos = 0
        while pos + 8 <= total:
            fh.seek(pos)
            hdr = fh.read(8)
            size, typ = int.from_bytes(hdr[:4], "big"), hdr[4:8]
            if size == 1:
                size = int.from_bytes(fh.read(8), "big")
            elif size == 0:
                size = total - pos
            if typ == b"moov":
                return True
            if typ == b"mdat":
                return False
            if size < 8:
                break
            pos += size
    return True


def ffprobe(path: Path):
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout or "{}")
    except Exception:
        return None


def _fps(stream) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key) or ""
        if "/" in val:
            a, b = val.split("/", 1)
            try:
                if float(b):
                    return float(a) / float(b)
            except ValueError:
                pass
    return None


def check_video(f: Path, reel: bool):
    errors, warns = [], []
    if f.suffix.lower() not in VIDEO_EXT:
        errors.append(f"{f.name}: 영상은 MP4 또는 MOV여야 합니다")
        return errors, warns
    size = f.stat().st_size
    if size > MAX_VIDEO_BYTES:
        errors.append(f"{f.name}: 300MB를 넘습니다")
    elif size > GITHUB_FILE_LIMIT:
        errors.append(f"{f.name}: 100MB를 넘어 깃허브에 올릴 수 없습니다 → 비트레이트를 낮춰 다시 출력하세요")
    try:
        if not moov_first(f):
            warns.append(f"{f.name}: 영상 정보(moov)가 파일 뒤쪽에 있습니다 → prepare(ffmpeg 필요)로 앞으로 옮기세요")
    except OSError:
        pass
    info = ffprobe(f)
    if info is None:
        warns.append(f"{f.name}: ffprobe가 없어 코덱·길이 점검을 건너뜁니다")
        return errors, warns
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        errors.append(f"{f.name}: 영상 트랙이 없습니다")
    else:
        if v.get("codec_name") not in ("h264", "hevc"):
            errors.append(f"{f.name}: 영상 코덱 {v.get('codec_name')} → H.264로 다시 출력하세요")
        fps = _fps(v)
        if fps and not (23 - 0.5 <= fps <= 60 + 0.5):
            errors.append(f"{f.name}: {fps:.1f}fps → 23~60fps여야 합니다")
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if w > 1920:
            errors.append(f"{f.name}: 가로 {w}px → 1920px 이하여야 합니다")
        if v.get("pix_fmt") and v["pix_fmt"] not in ("yuv420p", "yuvj420p"):
            errors.append(f"{f.name}: 색 형식 {v['pix_fmt']} → yuv420p로 다시 출력하세요")
        if reel and w and h and abs(w / h - 9 / 16) > 0.02:
            warns.append(f"{f.name}: {w}×{h} → 릴스는 9:16(1080×1920)을 권장합니다")
    if a and a.get("codec_name") != "aac":
        errors.append(f"{f.name}: 오디오 코덱 {a.get('codec_name')} → AAC여야 합니다")
    try:
        dur = float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        dur = 0
    if reel and dur and not (3 <= dur <= 900):
        errors.append(f"{f.name}: 길이 {dur:.1f}초 → 릴스는 3초~15분이어야 합니다")
    return errors, warns


def validate(post: Post):
    errors, warns = [], []
    if post.load_error:
        return [post.load_error], warns
    if post.source_rel() and not post.source_dir().is_dir():
        return [f"source 폴더가 없습니다: {post.source_rel()} (저장소 맨 위 기준 경로인지, push 했는지 확인)"], warns
    t = post.post_type()
    if t not in ("carousel", "reel", "image"):
        errors.append(f"type은 carousel / reel / image 중 하나여야 합니다 (지금: {t})")
        return errors, warns
    try:
        post.publish_at()
    except ValueError as e:
        errors.append(str(e))
    if post.caption is None:
        errors.append("caption.txt가 없습니다")
    else:
        errors += caption_errors(post.caption)
    files = post.media_files()
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        errors.append("파일이 없습니다: " + ", ".join(missing))
        return errors, warns
    images = [f for f in files if f.suffix.lower() not in VIDEO_EXT]
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXT]
    if t == "carousel" and not 2 <= len(files) <= 10:
        errors.append(f"캐러셀은 2~10개여야 합니다 (지금 {len(files)}개)")
    if t == "reel" and (len(videos) != 1 or images):
        errors.append("릴스 폴더에는 영상 1개만 두세요 (표지는 cover.jpg)")
    if t == "image" and (len(images) != 1 or videos):
        errors.append("사진 1장 게시물 폴더에는 사진 1장만 두세요")
    first_ratio = None
    for f in images:
        e, w, ratio = check_image(f)
        errors += e
        warns += w
        if ratio:
            if first_ratio is None:
                first_ratio = ratio
            elif abs(ratio - first_ratio) > 0.01:
                warns.append(f"{f.name}: 첫 장과 비율이 달라 첫 장 비율로 잘립니다")
    for f in videos:
        e, w = check_video(f, reel=(t == "reel"))
        errors += e
        warns += w
    cover = post.cover_name()
    if cover and t == "reel":
        cp = post.source_dir() / cover
        if not cp.is_file():
            errors.append(f"표지 파일이 없습니다: {cover}")
        elif cp.suffix.lower() not in JPEG_EXT:
            errors.append(f"표지는 JPEG여야 합니다: {cover} → prepare로 변환하세요")
    collab = post.data.get("collaborators")
    if collab and (not isinstance(collab, list) or len(collab) > 3):
        errors.append("collaborators는 [\"아이디\", ...] 형식이고 최대 3개입니다")
    return errors, warns


# ───────────────────────── 자동 수정 (prepare) ─────────────────────────
def border_color(img):
    w, h = img.size
    s = max(1, min(w, h) // 100)
    boxes = [(0, 0, w, s), (0, h - s, w, h), (0, 0, s, h), (w - s, 0, w, h)]
    total, count = [0.0, 0.0, 0.0], 0
    for box in boxes:
        strip = img.crop(box)
        n = strip.size[0] * strip.size[1]
        mean = ImageStat.Stat(strip).mean
        for i in range(3):
            total[i] += mean[i] * n
        count += n
    return tuple(int(round(c / count)) for c in total)


def to_rgb(img):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img if img.mode == "RGB" else img.convert("RGB")


def fit_ratio(img):
    w, h = img.size
    r = w / h
    if r < MIN_RATIO - EPS:
        new_w, new_h = math.ceil(h * MIN_RATIO), h
    elif r > MAX_RATIO + EPS:
        new_w, new_h = w, math.ceil(w / MAX_RATIO)
    else:
        return img, False
    canvas = Image.new("RGB", (new_w, new_h), border_color(img))
    canvas.paste(img, ((new_w - w) // 2, (new_h - h) // 2))
    return canvas, True


def fix_image(f: Path, backup_dir: Path, fit: bool = True, max_width: int = MAX_WIDTH):
    """고칠 게 있으면 고친 JPEG 경로와 설명을, 없으면 (None, None)을 돌려줌
    fit=False: 비율은 그대로 둠 (릴스 표지는 9:16이라 피드 사진 비율 규칙을 적용하지 않음)"""
    with Image.open(f) as im:
        fmt, orient = im.format, exif_orientation(im)
        img = ImageOps.exif_transpose(im)
        img.load()
        icc = im.info.get("icc_profile")
    notes = []
    if fmt != "JPEG" or f.suffix.lower() not in JPEG_EXT:
        notes.append(f"{fmt}→JPEG")
    if orient != 1:
        notes.append("회전 정보 반영")
    if img.mode not in ("RGB", "L"):
        notes.append(f"{img.mode}→RGB")
    img = to_rgb(img)
    if fit:
        img, padded = fit_ratio(img)
        if padded:
            notes.append(f"비율 맞춤 여백 → {img.width}×{img.height}")
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
        notes.append(f"가로 {max_width}px로 축소")
    if not notes and f.stat().st_size <= MAX_IMAGE_BYTES:
        return None, None
    out = f.with_suffix(".jpg")
    tmp = out.with_name(out.stem + ".tmp.jpg")
    quality = 95
    while True:
        kw = {"quality": quality, "optimize": True}
        if icc:
            kw["icc_profile"] = icc
        img.save(tmp, "JPEG", **kw)
        if tmp.stat().st_size <= MAX_IMAGE_BYTES or quality <= 70:
            break
        quality -= 5
    if quality < 95:
        notes.append(f"용량 줄임(품질 {quality})")
    backup_dir.mkdir(exist_ok=True)
    if out == f:
        shutil.copy2(f, backup_dir / f.name)
    else:
        shutil.move(str(f), str(backup_dir / f.name))
    tmp.replace(out)
    return out, ", ".join(notes)


def faststart(f: Path) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    tmp = f.with_name(f.stem + ".faststart" + f.suffix)
    r = subprocess.run([exe, "-y", "-v", "error", "-i", str(f), "-c", "copy", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(f)
    return True


def prepare_post(post: Post) -> list[str]:
    if Image is None:
        raise SetupError("prepare에는 Pillow가 필요합니다: pip install -r autopost/ig/requirements.txt")
    notes, renamed = [], {}
    backup = post.source_dir() / "_src"
    targets = [(f, False) for f in post.media_files()]
    cover = post.cover_name()
    if cover and (post.source_dir() / cover).is_file():
        targets.append((post.source_dir() / cover, True))
    for f, is_cover in targets:
        if not f.is_file():
            continue
        if f.suffix.lower() in VIDEO_EXT:
            if not moov_first(f):
                notes.append(f"{f.name}: moov를 앞으로 옮김" if faststart(f)
                             else f"{f.name}: ffmpeg가 없어 moov를 못 옮김 (메타가 처리 못 하면 영상 출력 시 faststart 옵션 사용)")
            continue
        if f.suffix.lower() not in JPEG_EXT | CONVERT_EXT:
            continue
        out, desc = fix_image(f, backup, fit=not is_cover, max_width=1920 if is_cover else MAX_WIDTH)
        if out:
            label = f.name if out.name == f.name else f"{f.name} → {out.name}"
            notes.append(f"{label}: {desc} (원본은 _src/)")
            if out.name != f.name:
                renamed[f.name] = out.name
    if renamed:
        changed = False
        if isinstance(post.data.get("media"), list):
            post.data["media"] = [renamed.get(n, n) for n in post.data["media"]]
            changed = True
        if post.data.get("cover") in renamed:
            post.data["cover"] = renamed[post.data["cover"]]
            changed = True
        if changed:
            write_json(post.folder / "post.json", post.data)
    return notes


# ───────────────────────── 인스타 API ─────────────────────────
def make_ig_error(err: dict, status: int, text: str) -> IGError:
    code, sub = err.get("code"), err.get("error_subcode")
    parts = [err.get("message") or f"HTTP {status} {text[:200]}"]
    for k in ("error_user_title", "error_user_msg"):
        if err.get(k):
            parts.append(str(err[k]))
    hint = SUBCODE_HINTS.get(sub) or CODE_HINTS.get(code)
    label = f"{code}/{sub}" if sub else f"{code}"
    msg = f"[메타 오류 {label}] " + " / ".join(parts) + (f"\n→ {hint}" if hint else "")
    transient = bool(err.get("is_transient")) or status >= 500 or code in (1, 2)
    return IGError(msg, code=code, subcode=sub, transient=transient)


class IG:
    def __init__(self, token: str, user_id: str | None = None):
        if requests is None:
            raise SetupError("requests가 필요합니다: pip install -r autopost/ig/requirements.txt")
        host = os.environ.get("IG_GRAPH_HOST", "https://graph.instagram.com").rstrip("/")
        self.base = f"{host}/{os.environ.get('IG_API_VERSION', 'v26.0')}"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self._user_id = user_id
        self.poll_scale = float(os.environ.get("IG_POLL_SCALE", "1"))  # 시험용

    @property
    def user_id(self) -> str:
        if not self._user_id:
            me = self.get("me", fields="user_id,username")
            self._user_id = str(me.get("user_id") or me.get("id"))
        return self._user_id

    def _call(self, method: str, path: str, params: dict) -> dict:
        url = f"{self.base}/{path}"
        last = None
        for attempt in range(3):
            try:
                if method == "GET":
                    r = self.s.get(url, params=params, timeout=60)
                else:
                    r = self.s.post(url, data=params, timeout=120)
            except requests.RequestException as e:  # 주소·토큰이 로그에 찍히지 않게 이름만
                last = IGError(f"네트워크 오류: {type(e).__name__}", transient=True)
                time.sleep(5 * (attempt + 1) * self.poll_scale)
                continue
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.ok and isinstance(body, dict) and "error" not in body:
                return body
            err = body.get("error") if isinstance(body, dict) else None
            last = make_ig_error(err or {}, r.status_code, r.text)
            if last.transient and attempt < 2:
                time.sleep(10 * (attempt + 1) * self.poll_scale)
                continue
            raise last
        raise last

    def get(self, path, **params):
        return self._call("GET", path, params)

    def post(self, path, **params):
        return self._call("POST", path, {k: v for k, v in params.items() if v is not None})

    def create(self, **params) -> str:
        return str(self.post(f"{self.user_id}/media", **params)["id"])

    def status(self, cid: str):
        b = self.get(cid, fields="status_code,status")
        return b.get("status_code") or "IN_PROGRESS", b.get("status") or ""

    def wait_all(self, ids: list[str], timeout: float, interval: float) -> dict:
        deadline = time.monotonic() + timeout * self.poll_scale
        states = {}
        while True:
            for cid in ids:
                if states.get(cid, ("IN_PROGRESS",))[0] == "IN_PROGRESS":
                    states[cid] = self.status(cid)
            if all(s[0] != "IN_PROGRESS" for s in states.values()):
                return states
            if time.monotonic() + interval * self.poll_scale > deadline:
                return states
            time.sleep(interval * self.poll_scale)

    def publish(self, cid: str) -> str:
        return str(self.post(f"{self.user_id}/media_publish", creation_id=cid)["id"])

    def permalink(self, mid: str):
        try:
            return self.get(mid, fields="permalink").get("permalink")
        except PostError:
            return None

    def quota(self):
        b = self.get(f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")
        d = (b.get("data") or [{}])[0]
        return d.get("quota_usage"), (d.get("config") or {}).get("quota_total")


def make_client() -> IG:
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not token:
        raise SetupError("IG_ACCESS_TOKEN이 없습니다. autopost/README.md '2. 인스타 API 연결'을 보세요.")
    return IG(token, os.environ.get("IG_USER_ID", "").strip() or None)


def url_ready(url: str):
    try:
        r = requests.head(url, allow_redirects=True, timeout=30)
        if r.status_code in (403, 405):
            r = requests.get(url, stream=True, timeout=30)
            r.close()
        return r.status_code == 200, r.status_code, r.headers.get("Content-Type", "")
    except requests.RequestException as e:
        return False, None, type(e).__name__


# ───────────────────────── 게시 흐름 ─────────────────────────
def b(v) -> str:
    return "true" if v else "false"


def common_params(post: Post) -> dict:
    extra = {}
    collab = post.data.get("collaborators")
    if collab:
        extra["collaborators"] = json.dumps(collab, ensure_ascii=False)
    if post.data.get("ai_label") is True:
        extra["is_ai_generated"] = "true"
    return extra


def timing(has_video: bool):
    # 문서 권장: 1분에 한 번, 5분 이내. 사진은 금방 끝나서 짧게
    return (300, 30) if has_video else (60, 5)


def record_published(post: Post, mid, cid, link=None) -> str:
    write_json(post.folder / PUBLISHED, {
        "media_id": mid, "permalink": link, "container_id": cid, "type": post.post_type(),
        "published_at": now_kst().isoformat(timespec="seconds")})
    for name in (PENDING, ERROR):
        p = post.folder / name
        if p.exists() and not DRY_RUN:
            p.unlink()
    log(f"  [게시 완료] {link or mid or cid}")
    return "published"


def save_pending(post: Post, note: str = "메타가 아직 처리 중 → 다음 실행에서 이어서 게시합니다.", **data) -> str:
    data.setdefault("created_at", now_kst().isoformat(timespec="seconds"))
    write_json(post.folder / PENDING, data)
    log("  " + note)
    return "pending"


def finish(ig: IG, post: Post, cid: str, created_at=None) -> str:
    try:
        mid = ig.publish(cid)
    except IGError as e:
        if e.rate_limited:
            save_pending(post, note="준비는 끝났지만 게시 한도에 걸림 → 다음 실행에서 게시합니다.",
                         stage="parent", container_id=cid,
                         **({"created_at": created_at} if created_at else {}))
            raise
        try:
            code, _ = ig.status(cid)
        except PostError:
            code = None
        if code == "PUBLISHED":
            return record_published(post, None, cid)
        raise
    return record_published(post, mid, cid, ig.permalink(mid))


def wait_and_publish(ig: IG, post: Post, cid: str, has_video: bool) -> str:
    timeout, interval = timing(has_video)
    code, detail = ig.wait_all([cid], timeout, interval)[cid]
    if code == "FINISHED":
        return finish(ig, post, cid)
    if code == "PUBLISHED":
        return record_published(post, None, cid)
    if code == "IN_PROGRESS":
        return save_pending(post, stage="parent", container_id=cid, has_video=has_video)
    raise PostError(f"메타가 미디어 처리에 실패했습니다: {code} {detail}".strip())


def carousel_from_children(ig: IG, post: Post, children: list[str], has_video: bool, created_at=None) -> str:
    timeout, interval = timing(has_video)
    states = ig.wait_all(children, timeout, interval)
    if any(s[0] == "IN_PROGRESS" for s in states.values()):
        return save_pending(post, stage="children", children=children, has_video=has_video,
                            **({"created_at": created_at} if created_at else {}))
    bad = [f"{i + 1}번째: {states[c][0]} {states[c][1]}".strip()
           for i, c in enumerate(children) if states[c][0] != "FINISHED"]
    if bad:
        raise PostError("메타가 캐러셀 파일 처리에 실패했습니다 — " + "; ".join(bad))
    parent = ig.create(media_type="CAROUSEL", children=",".join(children),
                       caption=post.caption or "", **common_params(post))
    log(f"  캐러셀 묶음 만듦 ({len(children)}장)")
    return wait_and_publish(ig, post, parent, has_video)


def resume_pending(ig: IG, post: Post, pending: dict) -> str:
    created = pending.get("created_at")
    if created:
        try:
            if now_kst() - parse_kst(created) > timedelta(hours=23):
                (post.folder / PENDING).unlink()
                raise PostError("메타 처리 대기가 23시간을 넘어 컨테이너가 만료됐습니다. error.json을 지우면 처음부터 다시 올립니다.")
        except ValueError:
            pass
    has_video = pending.get("has_video", True)
    if pending.get("stage") == "children":
        children = [str(c) for c in pending.get("children") or []]
        states = {c: ig.status(c) for c in children}
        if any(s[0] == "IN_PROGRESS" for s in states.values()):
            log("  아직 처리 중입니다.")
            return "pending"
        (post.folder / PENDING).unlink()
        return carousel_from_children(ig, post, children, has_video, created_at=created)
    cid = str(pending.get("container_id"))
    code, detail = ig.status(cid)
    if code == "IN_PROGRESS":
        log("  아직 처리 중입니다.")
        return "pending"
    if code == "FINISHED":
        return finish(ig, post, cid, created_at=created)
    if code == "PUBLISHED":
        return record_published(post, None, cid)
    (post.folder / PENDING).unlink()
    raise PostError(f"메타가 미디어 처리에 실패했습니다: {code} {detail}".strip())


def publish_post(ig: IG | None, post: Post, strict_urls: bool) -> str:
    pending = read_json(post.folder / PENDING)
    if pending and not DRY_RUN:
        return resume_pending(ig, post, pending)
    t = post.post_type()
    files = post.media_files()
    urls = [public_url(f) for f in files]
    cover = post.cover_name() if t == "reel" else None
    cover_url = public_url(post.source_dir() / cover) if cover else None
    if requests is None:
        raise SetupError("requests가 필요합니다: pip install -r autopost/ig/requirements.txt")
    missing = []
    for u in urls + ([cover_url] if cover_url else []):
        ok, code, ctype = url_ready(u)
        if not ok:
            missing.append(f"{u} ({code or ctype})")
    if missing:
        msg = "공개 주소에서 파일을 못 찾았습니다:\n  " + "\n  ".join(missing)
        if strict_urls:
            raise PostError(msg + "\n→ push 했는지, PUBLIC_BASE_URL이 맞는지 확인하세요.")
        log("  " + msg.replace("\n", "\n  ") + "\n  → 아직 배포 중일 수 있어 다음 실행에서 다시 확인합니다.")
        return "waiting"
    has_video = any(f.suffix.lower() in VIDEO_EXT for f in files)
    if DRY_RUN:
        log(f"  [미리보기] {t}, 파일 {len(files)}개, 캡션 {len(post.caption or '')}자 — 공개 주소 모두 열림")
        for u in urls:
            log(f"    {u}")
        return "dry"
    caption = post.caption or ""
    if t == "image":
        params = dict(image_url=urls[0], caption=caption, **common_params(post))
        if post.data.get("alt_text"):
            params["alt_text"] = str(post.data["alt_text"])[:1000]
        cid = ig.create(**params)
    elif t == "reel":
        params = dict(media_type="REELS", video_url=urls[0], caption=caption,
                      share_to_feed=b(post.data.get("share_to_feed", True)), **common_params(post))
        if cover_url:
            params["cover_url"] = cover_url
        elif post.data.get("thumb_offset_ms") is not None:
            params["thumb_offset"] = str(int(post.data["thumb_offset_ms"]))
        cid = ig.create(**params)
    else:
        children = []
        for f, u in zip(files, urls):
            if f.suffix.lower() in VIDEO_EXT:
                children.append(ig.create(media_type="VIDEO", video_url=u, is_carousel_item="true"))
            else:
                children.append(ig.create(image_url=u, is_carousel_item="true"))
        log(f"  파일 {len(children)}개 등록")
        return carousel_from_children(ig, post, children, has_video)
    log("  컨테이너 만듦, 처리 대기")
    return wait_and_publish(ig, post, cid, has_video)


def fail(post: Post, msg: str) -> None:
    log("  [오류] " + msg.replace("\n", "\n         "))
    write_json(post.folder / ERROR, {
        "error": msg, "at": now_kst().isoformat(timespec="seconds"),
        "how_to_retry": "고친 뒤 이 error.json 파일을 지우면 다음 실행에서 다시 시도합니다."})
    gh_annotate("error", f"인스타 게시 실패 {post.name}", msg)


# ───────────────────────── 명령 ─────────────────────────
def target_posts(folders: list[str]) -> list[Post]:
    if folders:
        return [Post(resolve_folder(f)) for f in folders]
    return [p for p in list_posts() if not p.has(PUBLISHED)]


def print_report(post: Post, errors, warns) -> None:
    for w in warns:
        log("  [주의] " + w)
    for e in errors:
        log("  [오류] " + e)
    if not errors:
        at = post.publish_at()
        when = f"{at:%Y-%m-%d %H:%M} 예약" if at else "예약 시각 없음 → 다음 실행 때 바로 게시"
        log(f"  [OK] {post.post_type()} · 파일 {len(post.media_files())}개 · {when}")


def cmd_prepare(args) -> int:
    posts = target_posts(args.folders)
    if not posts:
        log("대상 폴더가 없습니다.")
        return 0
    bad = 0
    for post in posts:
        log(f"- {post.name}")
        if post.load_error:
            log("  [오류] " + post.load_error)
            bad += 1
            continue
        for note in prepare_post(post):
            log("  [수정] " + note)
        post = Post(post.folder)
        errors, warns = validate(post)
        print_report(post, errors, warns)
        bad += bool(errors)
    return 1 if bad else 0


def cmd_check(args) -> int:
    posts = target_posts(args.folders)
    bad = 0
    try:
        base_ok = bool(public_base())
    except SetupError as e:
        log(f"(공개 주소 확인 생략: {e})")
        base_ok = False
    for post in posts:
        log(f"- {post.name}")
        errors, warns = validate(post)
        print_report(post, errors, warns)
        bad += bool(errors)
        if base_ok and not post.load_error and requests is not None:
            files = post.media_files()
            cover = post.cover_name() if post.post_type() == "reel" else None
            for f in files + ([post.source_dir() / cover] if cover else []):
                if not f.is_file():
                    continue
                u = public_url(f)
                ok, code, ctype = url_ready(u)
                kind = "video/" if f.suffix.lower() in VIDEO_EXT else "image/jpeg"
                note = "" if ctype.startswith(kind) else f"  ← 형식 표시가 {kind}가 아님 (GitHub Pages 권장)"
                log(f"  [{'열림' if ok else '안 열림'}] {f.name} {code or ''} {ctype}{note}")
                bad += not ok
    return 1 if bad else 0


def find_due(now: datetime):
    """올릴 차례인 게시물 [(예약시각, Post)]과 publish_at이 잘못된 게시물 [(Post, 오류)]"""
    due, bad = [], []
    for post in list_posts():
        if post.has(PUBLISHED) or post.has(ERROR):
            continue
        if post.has(PENDING):
            due.append((datetime.min.replace(tzinfo=KST), post))
            continue
        try:
            at = post.publish_at()
        except ValueError as e:
            bad.append((post, str(e)))
            continue
        if at is None or at <= now:
            due.append((at or now, post))
    due.sort(key=lambda x: x[0])
    return due, bad


def cmd_run(args) -> int:
    now = now_kst()
    stale_h = float(os.environ.get("IG_STALE_HOURS") or 12)
    max_n = int(os.environ.get("IG_MAX_PER_RUN") or 1)
    due, bad = find_due(now)
    for post, msg in bad:
        fail(post, msg)
    failures = len(bad)
    if not due:
        log(f"[{now:%m-%d %H:%M} KST] 올릴 차례인 게시물이 없습니다.")
        return 1 if failures else 0
    # 올릴 게 있을 때만 토큰을 요구 → 설정 전에도 30분마다 실패 메일이 오지 않음
    ig = None if DRY_RUN else make_client()
    if ig:
        try:
            used, total = ig.quota()
            log(f"24시간 게시 한도: {used}/{total}")
            if used is not None and total and used >= total:
                log("한도에 걸려 다음 실행으로 미룹니다.")
                return 0
        except IGError as e:
            if e.code in SETUP_CODES:
                raise SetupError(str(e))
            log(f"(게시 한도 조회 실패, 계속 진행: {e})")
    published = 0
    for at, post in due:
        pending = post.has(PENDING)
        if published >= max_n and not DRY_RUN:
            log(f"- {post.name}: 이번 실행 한도({max_n}개) → 다음 실행에서 올립니다.")
            continue
        log(f"- {post.name}" + ("" if pending else f"  (예약 {at:%m-%d %H:%M})"))
        if not pending and at < now - timedelta(hours=stale_h):
            fail(post, f"예약 시각({at:%Y-%m-%d %H:%M})이 {stale_h:g}시간 넘게 지나 자동 게시를 건너뛰었습니다.\n"
                       f"→ post.json의 publish_at을 고치고 이 error.json을 지우면 다시 올라갑니다.")
            failures += 1
            continue
        if not pending:
            errors, warns = validate(post)
            for w in warns:
                log("  [주의] " + w)
            if errors:
                fail(post, "\n".join(errors))
                failures += 1
                continue
        try:
            result = publish_post(ig, post, strict_urls=(not pending and at < now - timedelta(hours=2)))
        except IGError as e:
            if e.code in SETUP_CODES:
                raise SetupError(str(e))
            if e.rate_limited:
                log("  한도에 걸려 이번 실행을 멈춥니다. 다음 실행에서 이어집니다.")
                break
            fail(post, str(e))
            failures += 1
            continue
        except PostError as e:
            fail(post, str(e))
            failures += 1
            continue
        if result == "published":
            published += 1
    return 1 if failures else 0


def cmd_now(args) -> int:
    post = Post(resolve_folder(args.folder))
    log(f"- {post.name} (지금 바로)")
    if post.has(PUBLISHED):
        log("  이미 게시된 폴더입니다(published.json). 다시 올리려면 그 파일을 지우세요.")
        return 1
    ig = None if DRY_RUN else make_client()
    if not post.has(PENDING):
        errors, warns = validate(post)
        print_report(post, errors, warns)
        if errors:
            if not DRY_RUN:
                fail(post, "\n".join(errors))
            return 1
    try:
        publish_post(ig, post, strict_urls=True)
    except IGError as e:
        if e.code in SETUP_CODES:
            raise SetupError(str(e))
        fail(post, str(e))
        return 1
    except PostError as e:
        fail(post, str(e))
        return 1
    return 0


def gh_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def cmd_sources(args) -> int:
    """GitHub Actions용: 필요한 posting 폴더 목록 (저장소 전체를 받지 않으려고)"""
    if args.folder:
        posts = [Post(resolve_folder(args.folder))]
    elif args.due:
        due, bad = find_due(now_kst())
        posts = [p for _, p in due] + [p for p, _ in bad]
    else:
        posts = [p for p in list_posts() if not p.has(PUBLISHED)]
    dirs = sorted({p.source_rel() for p in posts if p.source_rel()})
    log(f"대상 게시물 {len(posts)}개" + (f", 필요한 폴더: {' '.join(dirs)}" if dirs else ""))
    gh_output(count=len(posts), dirs=" ".join(dirs))
    return 0


def cmd_site(args) -> int:
    """GitHub Pages용 사이트: 아직 게시 안 된 게시물의 사진·영상만 저장소와 같은 경로로 복사"""
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    posts = [p for p in list_posts() if not p.has(PUBLISHED) and not p.load_error]
    count = 0
    for post in posts:
        files = list(post.media_files())
        cover = post.cover_name() if post.post_type() == "reel" else None
        if cover:
            files.append(post.source_dir() / cover)
        for f in files:
            if not f.is_file():
                log(f"  [주의] {post.name}: 파일 없음 {f.name}")
                continue
            dest = out / f.resolve().relative_to(repo_root())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            count += 1
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>okawari</title><p>오카와리 인스타 게시 대기 파일</p>\n",
        encoding="utf-8")
    log(f"게시 대기 {len(posts)}개, 파일 {count}개를 {out}에 모았습니다.")
    gh_output(posts=len(posts), files=count)
    return 0


def cmd_whoami(args) -> int:
    ig = make_client()
    try:
        me = ig.get("me", fields="user_id,username")
    except IGError as e:
        raise SetupError(str(e))
    log(f"[OK] @{me.get('username')}  (IG_USER_ID = {me.get('user_id') or me.get('id')})")
    try:
        used, total = ig.quota()
        log(f"24시간 게시 한도: {used}/{total}")
    except IGError as e:
        log(f"(게시 한도 조회 실패: {e})")
    return 0


def main(argv=None) -> int:
    global DRY_RUN
    load_dotenv()
    ap = argparse.ArgumentParser(description="오카와리 인스타 자동 게시")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="올리기 전 점검 + 자동 수정")
    p.add_argument("folders", nargs="*")
    p = sub.add_parser("check", help="점검 + 공개 주소 확인")
    p.add_argument("folders", nargs="*")
    p = sub.add_parser("run", help="예약 시각이 된 게시물 게시")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("now", help="이 폴더를 지금 게시")
    p.add_argument("folder")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("whoami", help="토큰·계정 확인")
    p = sub.add_parser("sources", help="(Actions용) 필요한 posting 폴더 목록")
    p.add_argument("--due", action="store_true")
    p.add_argument("--folder")
    p = sub.add_parser("site", help="(Actions용) Pages에 올릴 게시 대기 파일 모으기")
    p.add_argument("--out", default="_site")
    args = ap.parse_args(argv)
    DRY_RUN = bool(getattr(args, "dry_run", False))
    cmds = {"prepare": cmd_prepare, "check": cmd_check, "run": cmd_run, "now": cmd_now, "whoami": cmd_whoami,
            "sources": cmd_sources, "site": cmd_site}
    try:
        return cmds[args.cmd](args)
    except SetupError as e:
        log("[설정 오류] " + str(e))
        gh_annotate("error", "인스타 자동 게시 설정 오류", str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
