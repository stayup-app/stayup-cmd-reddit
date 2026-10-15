#!/usr/bin/env python3
"""
Stayup — monitors subreddits and stores their posts via stayup-api.

For each tracked repository of type 'reddit' (a subreddit URL such as
https://www.reddit.com/r/python/), the script fetches the current posts and
stores the ones it has not stored yet, up to config["max_entries"] (default 5)
per run, skipping stickied and NSFW posts and those under config["min_score"]
(default 0). config["sort"] picks the listing: hot (default), new, top or rising.

Reddit blocks many datacenter IPs (including GitHub Actions') on its anonymous
endpoints. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (a free "script" app,
https://www.reddit.com/prefs/apps) to use the OAuth API, which does not.

Talks to stayup-api over HTTP (STAYUP_API_URL + STAYUP_API_KEY) — it never
touches a database directly. See stayup-api/docs/self-hosting-and-providers.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

import requests

PROVIDER_TYPE = "reddit"

# Display name of the provider in the apps (fallback: capitalized table name).
DISPLAY_NAME = "Reddit"

# Where this connector ranks among the others in the sidebar.
SORT_ORDER = 60

# The stayup-api instance to talk to, and the key that authenticates this
# connector for the 'reddit' provider — obtained from that instance's admin
# (see stayup-api/docs/self-hosting-and-providers.md).
API_URL = os.environ.get("STAYUP_API_URL", "http://localhost:3000").rstrip("/")
API_KEY = os.environ.get("STAYUP_API_KEY")

# Optional: a Reddit "script" app, to use the OAuth API instead of the anonymous one.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")

# Reddit requires a descriptive User-Agent and throttles generic ones.
USER_AGENT = "stayup-reddit/1.0 (+https://github.com/stayup-app/stayup-cmd-reddit)"

DEFAULT_MAX_ENTRIES = 5
DEFAULT_MIN_SCORE = 0
DEFAULT_SORT = "hot"
SORTS = ("hot", "new", "top", "rising")

# A text post is stored as an excerpt: enough for a card, not the whole essay.
SUMMARY_MAX_CHARS = 600

_SUBREDDIT_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_+]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# stayup-api client
# ---------------------------------------------------------------------------


def api_request(method: str, path: str, **kwargs) -> dict | None:
    """Call one of stayup-api's /connector-api/reddit/* endpoints.

    Raises RuntimeError if STAYUP_API_KEY isn't set, or requests.HTTPError on
    a non-2xx response (via raise_for_status — 403 means this key isn't
    scoped to 'reddit', 401 means it's missing or revoked).
    """
    if not API_KEY:
        raise RuntimeError("STAYUP_API_KEY is not set.")
    url = f"{API_URL}/connector-api/{PROVIDER_TYPE}{path}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def register_provider() -> None:
    """Self-declaration at startup — display name and sort order."""
    api_request(
        "POST",
        "/register",
        json={
            "displayName": DISPLAY_NAME,
            "sortOrder": SORT_ORDER,
        },
    )


def add_source(url: str) -> int:
    """Track a new subreddit URL and return its id."""
    result = api_request("POST", "/sources", json={"url": url})
    return result["id"]


def get_sources() -> list[tuple[int, str, dict]]:
    """Return all tracked sources as (id, url, config) tuples."""
    result = api_request("GET", "/sources")
    return [(s["id"], s["url"], s.get("config") or {}) for s in result["sources"]]


def get_saved_versions(repository_id: int) -> set[str]:
    """Return every post id already stored for a repository.

    A subreddit listing is not chronological (hot, top…), so "stop at the last
    known post" does not work: the whole set is needed to skip what is stored.
    """
    result = api_request("GET", f"/sources/{repository_id}/versions")
    return set(result["versions"])


def save_posts(repository_id: int, posts: list[dict], executed_at: datetime) -> None:
    """Persist a batch of posts in a single call. No-op on an empty batch."""
    if not posts:
        return
    items = [
        {
            "repositoryId": repository_id,
            "version": post["version"],
            "content": json.dumps(
                {
                    "title": post["title"],
                    "link": post["link"],
                    "url": post["url"],
                    "subreddit": post["subreddit"],
                    "author": post["author"],
                    "score": post["score"],
                    "comments": post["comments"],
                    "summary": post["summary"],
                },
                ensure_ascii=False,
            ),
            "datetime": post["published"].isoformat() if post["published"] else None,
            "executedAt": executed_at.isoformat(),
            "success": True,
        }
        for post in posts
    ]
    api_request("POST", "/items", json={"items": items})


def save_error(repository_id: int | None, error: str, executed_at: datetime) -> None:
    """Persist a retrieval error."""
    api_request(
        "POST",
        "/errors",
        json={"repositoryId": repository_id, "error": error, "executedAt": executed_at.isoformat()},
    )


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


def subreddit_from_url(url: str) -> str:
    """Extract the subreddit name from a reddit.com/r/<name> URL."""
    match = _SUBREDDIT_RE.search(url)
    if not match:
        raise RuntimeError(f"Not a subreddit URL: {url}")
    return match.group(1)


def get_oauth_token() -> str | None:
    """Return an application-only OAuth token, or None when no app is configured."""
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    response = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _parse_post(data: dict) -> dict:
    """Extract the relevant fields from a Reddit listing child."""
    created = data.get("created_utc")
    published = datetime.fromtimestamp(created, tz=UTC) if created else None
    summary = (data.get("selftext") or "").strip()
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "…"
    return {
        "version": data["name"],
        "title": data.get("title"),
        "link": f"https://www.reddit.com{data['permalink']}",
        # The external link of a link post; for a text post Reddit points back at itself.
        "url": None if data.get("is_self") else data.get("url"),
        "subreddit": data.get("subreddit"),
        "author": data.get("author"),
        "score": data.get("score", 0),
        "comments": data.get("num_comments", 0),
        "summary": summary,
        "published": published,
    }


def fetch_posts(subreddit: str, sort: str, limit: int, token: str | None = None) -> list[dict]:
    """Return a subreddit's posts, in the order Reddit lists them.

    Stickied and NSFW posts are left out. Raises on a non-2xx answer — a 403 or
    429 here usually means Reddit is blocking this IP, see the module docstring.
    """
    if sort not in SORTS:
        raise RuntimeError(f"Unknown sort '{sort}' (expected one of {', '.join(SORTS)}).")
    base = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"limit": limit, "raw_json": 1}
    if sort == "top":
        params["t"] = "day"
    response = requests.get(f"{base}/r/{subreddit}/{sort}.json", headers=headers, params=params, timeout=30)
    if response.status_code in (403, 429):
        raise RuntimeError(
            f"Reddit refused the request (HTTP {response.status_code}). "
            "It often blocks datacenter IPs: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."
        )
    response.raise_for_status()
    children = response.json()["data"]["children"]
    return [_parse_post(c["data"]) for c in children if not c["data"].get("stickied") and not c["data"].get("over_18")]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_repository(
    repository_id: int,
    repository_url: str,
    executed_at: datetime,
    config: dict,
    token: str | None = None,
) -> None:
    """Fetch the posts of one subreddit and persist the ones not stored yet.

    - Posts are kept in the order Reddit lists them, up to config["max_entries"]
      (default 5) new ones per run.
    - Posts under config["min_score"] (default 0) are skipped.
    - Any exception is caught, logged via the API, and printed to stderr.
    """
    max_entries = config.get("max_entries", DEFAULT_MAX_ENTRIES)
    min_score = config.get("min_score", DEFAULT_MIN_SCORE)
    sort = config.get("sort", DEFAULT_SORT)
    try:
        subreddit = subreddit_from_url(repository_url)
        # A bounded margin over what we keep: the posts we skip (already stored,
        # under the score) must not starve the batch.
        posts = fetch_posts(subreddit, sort, limit=max_entries * 4, token=token)
        saved = get_saved_versions(repository_id)
        fresh = [p for p in posts if p["version"] not in saved and p["score"] >= min_score]
        save_posts(repository_id, fresh[:max_entries], executed_at)

    except Exception as e:
        save_error(repository_id, str(e), executed_at)
        print(f"[{repository_url}] Error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor subreddits and store their posts.")
    parser.add_argument("--add", metavar="URL", help="Add a subreddit to track and exit.")
    args = parser.parse_args()

    register_provider()

    if args.add:
        add_source(args.add)
        print(f"Repository added: {args.add}")
        return

    executed_at = datetime.now(tz=UTC)
    sources = get_sources()

    if not sources:
        print("No repositories tracked. Use --add <url> to add one.")
        return

    try:
        token = get_oauth_token()
    except Exception as e:
        # Fall back to the anonymous API rather than skipping every subreddit.
        print(f"Reddit OAuth failed, continuing anonymously: {e}", file=sys.stderr)
        token = None

    for repository_id, repository_url, config in sources:
        process_repository(repository_id, repository_url, executed_at, config, token=token)


if __name__ == "__main__":
    main()
