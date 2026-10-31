#!/usr/bin/env python3
"""Build status table row data from GitHub Releases and GHCR deployed-* tags (stdlib only)."""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

APP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
IMAGE_RE = re.compile(r"^ghcr\.io/verbanium-lab/[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7}$")
DEPLOY_LINE = re.compile(r"Deploy by digest: `([^`]+)`")


def api_request(url: str, token: str) -> bytes:
    request = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def list_releases(repository: str, token: str) -> list[dict]:
    releases = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"
        batch = json.loads(api_request(url, token).decode("utf-8"))
        if not isinstance(batch, list):
            raise SystemExit("Unexpected releases response")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def package_name(image: str) -> str:
    return image.rsplit("/", 1)[-1]


def registry_digest(image: str, tag: str, token: str) -> str | None:
    name = package_name(image)
    url = f"https://ghcr.io/v2/verbanium-lab/{name}/manifests/{tag}"
    request = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json",
        },
        method="HEAD",
    )
    try:
        with urlopen(request, timeout=60) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
    except OSError:
        return None
    return digest if DIGEST_RE.fullmatch(digest) else None


def release_for_digest(releases: list[dict], image: str, digest: str, server: str, repository: str) -> dict | None:
    needle = f"{image}@{digest}"
    for release in releases:
        body = release.get("body") or ""
        match = DEPLOY_LINE.search(body)
        if match and match.group(1) == needle:
            tag = release.get("tag_name") or ""
            if not RELEASE_TAG_RE.fullmatch(tag):
                continue
            return {
                "tag": tag,
                "url": f"{server}/{repository}/releases/tag/{quote(tag, safe='')}",
            }
    return None


def digest_cell(releases: list[dict], image: str, digest: str | None, server: str, repository: str) -> dict | None:
    if not digest:
        return None
    mapped = release_for_digest(releases, image, digest, server, repository)
    if mapped:
        return {"label": mapped["tag"], "url": mapped["url"]}
    short = digest[7:19]
    return {"label": short, "url": None}


def latest_release_for_slug(releases: list[dict], slug: str, server: str, repository: str) -> dict | None:
    prefix = slug + "-"
    candidates = []
    for release in releases:
        tag = release.get("tag_name") or ""
        if tag.startswith(prefix) and RELEASE_TAG_RE.fullmatch(tag):
            candidates.append(release)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    tag = candidates[0]["tag_name"]
    return {"tag": tag, "url": f"{server}/{repository}/releases/tag/{quote(tag, safe='')}"}


def main() -> int:
    token = os.environ.get("GH_TOKEN", "")
    apps_json = os.environ.get("APPS_JSON", "")
    repository = os.environ.get("REPOSITORY", "")
    server = os.environ.get("SERVER", "https://github.com").rstrip("/")
    owner = os.environ.get("OWNER", "")
    if owner != "verbanium-lab":
        raise SystemExit("Organization repository required")
    if not token or not repository or "/" not in repository:
        raise SystemExit("Missing repository context")
    try:
        apps = json.loads(apps_json)
    except json.JSONDecodeError as error:
        raise SystemExit("Invalid apps JSON") from error
    if not isinstance(apps, list) or not apps:
        raise SystemExit("Apps list required")
    for app in apps:
        if not isinstance(app, dict):
            raise SystemExit("Invalid app entry")
        slug = app.get("slug", "")
        image = app.get("image", "")
        if not APP_RE.fullmatch(slug) or not IMAGE_RE.fullmatch(image):
            raise SystemExit("Invalid slug or image")

    releases = list_releases(repository, token)
    rows = []
    for app in apps:
        slug = app["slug"]
        image = app["image"]
        prod_digest = registry_digest(image, "deployed-prod", token)
        rc_digest = registry_digest(image, "deployed-rc", token)
        rows.append({
            "slug": slug,
            "release": latest_release_for_slug(releases, slug, server, repository),
            "production": digest_cell(releases, image, prod_digest, server, repository),
            "rc": digest_cell(releases, image, rc_digest, server, repository),
        })

    payload = {"repository": repository, "server": server, "rows": rows}
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
