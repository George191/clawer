"""Probe Satellite Today WordPress media API response shapes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curl_cffi import requests


BASE_URL = "https://www.satellitetoday.com"


def _build_url(path: str, params: dict[str, Any]) -> str:
    query = urlencode(params, doseq=True, safe=":,._")
    return f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Referer": f"{BASE_URL}/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }


def _load_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"_non_json_preview": response.text[:500]}


def _unwrap_posts(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("body"), list):
        return [item for item in data["body"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("id"), int):
        return [data]
    return []


def _top_keys(data: Any) -> list[str]:
    return sorted(data.keys()) if isinstance(data, dict) else []


def _first_featured_media_href(post: dict[str, Any]) -> str:
    links = post.get("_links")
    if not isinstance(links, dict):
        return ""
    featured = links.get("wp:featuredmedia")
    if not isinstance(featured, list) or not featured:
        return ""
    href = featured[0].get("href") if isinstance(featured[0], dict) else ""
    return href if isinstance(href, str) else ""


def _post_shape(data: Any) -> dict[str, Any]:
    posts = _unwrap_posts(data)
    first = posts[0] if posts else {}
    embedded = first.get("_embedded") if isinstance(first, dict) else None
    links = first.get("_links") if isinstance(first, dict) else None
    featured_media = first.get("featured_media") if isinstance(first, dict) else None
    return {
        "post_count": len(posts),
        "top_keys": _top_keys(data),
        "first_post_keys": sorted(first.keys()) if first else [],
        "featured_media": featured_media,
        "featured_media_type": type(featured_media).__name__,
        "has_links": isinstance(links, dict),
        "links_keys": sorted(links.keys()) if isinstance(links, dict) else [],
        "featured_media_href": _first_featured_media_href(first) if first else "",
        "has_embedded": isinstance(embedded, dict),
        "embedded_keys": sorted(embedded.keys()) if isinstance(embedded, dict) else [],
    }


def _media_shape(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    media_details = data.get("media_details")
    sizes = media_details.get("sizes") if isinstance(media_details, dict) else None
    full = sizes.get("full") if isinstance(sizes, dict) else None
    return {
        "keys": sorted(data.keys()),
        "id": data.get("id"),
        "source_url": data.get("source_url"),
        "link": data.get("link"),
        "media_type": data.get("media_type"),
        "mime_type": data.get("mime_type"),
        "full_source_url": full.get("source_url") if isinstance(full, dict) else None,
    }


def _fetch(session: requests.Session, url: str) -> tuple[int | None, Any, str]:
    try:
        response = session.get(url)
        return response.status_code, _load_json(response), ""
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Satellite Today posts/media API response fields.",
    )
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=1)
    parser.add_argument("--media-id", type=int, default=0, help="Explicit media id to probe")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    post_fields = "id,date,modified,slug,link,title,categories,tags,excerpt,content,featured_media"
    media_fields = "id,date,modified,link,source_url,media_type,mime_type,media_details.sizes.full.source_url"
    post_cases = [
        (
            "posts_plain",
            "/wp-json/wp/v2/posts",
            {"per_page": args.per_page, "page": args.page},
        ),
        (
            "posts_embed_no_fields",
            "/wp-json/wp/v2/posts",
            {"per_page": args.per_page, "page": args.page, "_embed": "1"},
        ),
        (
            "posts_embed_with_links_embedded_fields",
            "/wp-json/wp/v2/posts",
            {
                "per_page": args.per_page,
                "page": args.page,
                "_embed": "1",
                "_fields": f"{post_fields},_links,_embedded",
            },
        ),
        (
            "posts_envelope_current_template",
            "/wp-json/wp/v2/posts",
            {
                "per_page": args.per_page,
                "page": args.page,
                "_envelope": "1",
                "_embed": "1",
                "_fields": post_fields,
            },
        ),
        (
            "posts_envelope_try_embedded_featuredmedia",
            "/wp-json/wp/v2/posts",
            {
                "per_page": args.per_page,
                "page": args.page,
                "_envelope": "1",
                "_embed": "1",
                "_fields": f"{post_fields},_links,_embedded,_embedded.wp:featuredmedia",
            },
        ),
    ]

    result: dict[str, Any] = {"posts": [], "media": []}
    media_id = args.media_id
    media_href = ""

    with requests.Session(
        impersonate="chrome120",
        timeout=args.timeout,
        headers=_headers(),
        verify=False,
    ) as session:
        for name, path, params in post_cases:
            url = _build_url(path, params)
            status, data, error = _fetch(session, url)
            shape = _post_shape(data) if error == "" else {}
            result["posts"].append(
                {
                    "name": name,
                    "url": url,
                    "status": status,
                    "error": error,
                    "shape": shape,
                }
            )
            posts = _unwrap_posts(data)
            if posts and not media_id:
                featured_media = posts[0].get("featured_media")
                if isinstance(featured_media, int):
                    media_id = featured_media
            if posts and not media_href:
                media_href = _first_featured_media_href(posts[0])

        media_cases: list[tuple[str, str]] = []
        if media_id:
            media_cases.append(
                (
                    "media_by_featured_media_id",
                    _build_url(
                        f"/wp-json/wp/v2/media/{media_id}",
                        {"_fields": media_fields},
                    ),
                )
            )
        if media_href:
            media_cases.append(("media_by_links_href", media_href))

        for name, url in media_cases:
            status, data, error = _fetch(session, url)
            result["media"].append(
                {
                    "name": name,
                    "url": url,
                    "status": status,
                    "error": error,
                    "shape": _media_shape(data) if error == "" else {},
                }
            )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
