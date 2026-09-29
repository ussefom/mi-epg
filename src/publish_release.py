"""Create a fully validated draft release and publish it atomically as latest."""

from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASSET_NAMES = ("movistar.xml.gz", "movistar-compact.xml.gz", "status.json")
API_VERSION = "2022-11-28"


def request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ussefom-mi-epg-release-publisher",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def upload_asset(upload_url: str, asset: Path, token: str) -> None:
    endpoint = upload_url.split("{")[0]
    endpoint += "?name=" + urllib.parse.quote(asset.name)
    content_type = "application/gzip" if asset.suffix == ".gz" else "application/json"
    request = urllib.request.Request(
        endpoint,
        data=asset.read_bytes(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "ussefom-mi-epg-release-publisher",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=60):
        pass


def release_tag() -> str:
    return datetime.now(timezone.utc).strftime("epg-%Y%m%dT%H%M%SZ")


def verify_remote_assets(release: dict[str, Any], directory: Path) -> None:
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    for name in ASSET_NAMES:
        asset = assets.get(name)
        if asset is None or asset.get("size") != (directory / name).stat().st_size:
            raise RuntimeError(f"uploaded asset verification failed: {name}")


def cleanup_old_releases(api_url: str, repository: str, token: str, keep: int, current_id: int) -> None:
    releases = request_json("GET", f"{api_url}/repos/{repository}/releases?per_page=100", token)
    published = [
        release
        for release in releases
        if not release["draft"] and not release["prerelease"] and release["tag_name"].startswith("epg-")
    ]
    for release in published[keep:]:
        if release["id"] == current_id:
            continue
        try:
            request_json("DELETE", f"{api_url}/repos/{repository}/releases/{release['id']}", token)
            logging.info("deleted expired release tag=%s", release["tag_name"])
        except urllib.error.URLError:
            logging.warning("retention cleanup failed for tag=%s", release["tag_name"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--keep", type=int, default=5)
    arguments = parser.parse_args()
    if not arguments.repository or not arguments.token:
        parser.error("--repository and --token (or GitHub environment variables) are required")
    if arguments.keep < 1:
        parser.error("--keep must be positive")
    assets = [arguments.assets_dir / name for name in ASSET_NAMES]
    if any(not asset.is_file() for asset in assets):
        parser.error("assets directory does not contain the validated release files")

    tag = release_tag()
    release = request_json(
        "POST",
        f"{arguments.api_url}/repos/{arguments.repository}/releases",
        arguments.token,
        {
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"XMLTV {tag[4:]}",
            "draft": True,
            "prerelease": False,
            "generate_release_notes": False,
            "body": "Generated XMLTV assets. This draft is published only after every asset is verified.",
        },
    )
    logging.info("created draft release tag=%s", tag)
    for asset in assets:
        upload_asset(release["upload_url"], asset, arguments.token)
    uploaded = request_json(
        "GET",
        f"{arguments.api_url}/repos/{arguments.repository}/releases/{release['id']}",
        arguments.token,
    )
    verify_remote_assets(uploaded, arguments.assets_dir)
    published = request_json(
        "PATCH",
        f"{arguments.api_url}/repos/{arguments.repository}/releases/{release['id']}",
        arguments.token,
        {"draft": False, "prerelease": False, "make_latest": "true"},
    )
    logging.info("published release url=%s", published["html_url"])
    cleanup_old_releases(arguments.api_url, arguments.repository, arguments.token, arguments.keep, published["id"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
