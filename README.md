# Stayup

[![CI](https://github.com/stayup-app/stayup-cmd-reddit/actions/workflows/ci.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-reddit/actions/workflows/ci.yml)
[![Daily Reddit fetch](https://github.com/stayup-app/stayup-cmd-reddit/actions/workflows/daily.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-reddit/actions/workflows/daily.yml)

**Website:** https://stayup-ui.vercel.app

Monitors subreddits and stores their posts. For each tracked subreddit the script lists the posts (hot by default), skips stickied and NSFW ones, and stores those it has not stored yet — up to `max_entries` (default 5) per run, and only those with at least `min_score` points (default 0). `sort` can be `hot`, `new`, `top` (of the day) or `rising`. The script never touches a database directly, it only calls [stayup-api](https://github.com/stayup-app/stayup-api)'s `/connector-api/reddit/*` endpoints. Old content is pruned by `stayup-api` itself — retention is an instance-wide setting an admin manages, not this connector's job.

## Requirements

- Python 3.13, or [Docker](https://www.docker.com/)
- A `stayup-api` instance (see [stayup-api](https://github.com/stayup-app/stayup-api))
- An API key for the `reddit` provider, created from that instance's admin panel (Connector keys → New key, provider `reddit`). The key is shown once — copy it right away.
- *(Recommended)* A Reddit "script" app (https://www.reddit.com/prefs/apps → create app → type *script*), for `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`. **Reddit blocks many datacenter IPs — including GitHub Actions' — on its anonymous endpoints**: without an app the nightly workflow may be refused (HTTP 403/429, reported in the admin's error log). Run it from your own machine or server, or set the app credentials.

## Setup

```bash
git clone https://github.com/stayup-app/stayup-cmd-reddit.git
cd stayup-cmd-reddit
cp .env.example .env
```

Open `.env` and set `STAYUP_API_URL` (your `stayup-api` instance) and `STAYUP_API_KEY` (the key you created for `reddit`).

> **Note:** the provider registers itself automatically on every run — nothing to create by hand beyond the key. The apps render it with the template bundled in them (`src/lib/providers.ts`), so use a version of the apps that knows `reddit`.

## Usage

**Track a subreddit:**
```bash
docker compose run --rm fetch_reddit --add https://www.reddit.com/r/python/
docker compose run --rm fetch_reddit --add https://www.reddit.com/r/selfhosted/
```

**Run the script manually:**
```bash
docker compose run --rm fetch_reddit
```

Without Docker:
```bash
pip install -r requirements.txt
STAYUP_API_URL=... STAYUP_API_KEY=... python fetch_reddit.py
```

## Automation

The script runs automatically every night at midnight UTC via GitHub Actions.

To enable it on your fork, add `STAYUP_API_URL` and `STAYUP_API_KEY` secrets in:
**Settings → Secrets and variables → Actions → New repository secret**

Optionally add `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` the same way (see Requirements).

You can also trigger the workflow manually from the **Actions → Daily Reddit fetch → Run workflow** tab.

## Development

**Install the pre-commit hook** (runs linter + tests before every commit):
```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Run tests** (no external dependencies — `stayup-api` and network calls are mocked):
```bash
docker compose run --rm test
```

**Check linting:**
```bash
docker compose run --rm --entrypoint="" test sh -c "ruff check . && black --check ."
```

**Auto-format code:**
```bash
docker run --rm --entrypoint="" -v $(pwd):/app -w /app stayup-test black .
```

## What gets stored

Each stored post is a JSON `content` blob `{"title", "link", "url", "subreddit", "author", "score", "comments", "summary"}` — `link` is the Reddit thread, `url` the external link of a link post (`null` for a text post), `summary` an excerpt of a text post. It is keyed by the post's fullname (`t3_…`, the `version`), and `stayup-api`'s `/versions` endpoint tells the script which posts it already has, since a hot or top listing is not chronological. See `stayup-api`'s `connector-api` docs for the full contract.

## Project structure

```
stayup-cmd-reddit/
├── fetch_reddit.py   # Main script
├── tests/
│   └── test_unit.py   # Tests — stayup-api and network calls are mocked
├── .env.example       # Configuration template
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml     # Ruff + Black configuration
```
