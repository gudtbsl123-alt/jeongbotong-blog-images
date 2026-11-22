#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인스타 60일 토큰을 다시 60일로 연장합니다. (만든 지 24시간이 지난, 아직 안 만료된 토큰만 가능)

  python autopost/ig/refresh_token.py --out 새토큰.txt

새 토큰은 화면에 찍지 않고 --out 파일에만 씁니다.
GitHub Actions의 '인스타 토큰 연장' 워크플로가 매주 이 스크립트를 실행하고 Secret을 바꿔 줍니다.
"""
import argparse
import os
import sys
from pathlib import Path

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="새 토큰을 적을 파일")
    args = ap.parse_args()

    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not token:
        print("[오류] IG_ACCESS_TOKEN이 없습니다.")
        return 2
    host = os.environ.get("IG_GRAPH_HOST", "https://graph.instagram.com").rstrip("/")
    try:
        r = requests.get(f"{host}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[오류] 연장 요청 실패: {type(e).__name__}")  # 주소에 토큰이 있어 상세 메시지는 찍지 않음
        return 1
    new = data.get("access_token") if isinstance(data, dict) else None
    if not new:
        err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
        print(f"[오류] 메타가 연장을 거절했습니다: {err.get('code')} {err.get('message')}")
        print("→ 토큰이 이미 만료됐다면 앱 대시보드에서 새로 만들어 IG_ACCESS_TOKEN Secret을 바꾸세요.")
        return 1
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{new}")
    Path(args.out).write_text(new, encoding="utf-8")
    days = int(data.get("expires_in", 0)) / 86400
    print(f"[OK] 토큰을 연장했습니다. 앞으로 약 {days:.0f}일 유효합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
