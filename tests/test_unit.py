"""Unit tests — no external dependencies. stayup-api itself is mocked
(unittest.mock.patch on `requests.request`); its actual behavior is covered
by stayup-api's own test suite. Reddit (requests.get / requests.post) is mocked too."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fetch_reddit import (
    _parse_post,
    add_source,
    fetch_posts,
    get_oauth_token,
    get_saved_versions,
    get_sources,
    main,
    process_repository,
    register_provider,
    save_error,
    save_posts,
    subreddit_from_url,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def mock_response(json_body=None, status=200):
    response = MagicMock()
    response.status_code = status
    response.content = b"{}" if json_body is not None else b""
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    return response


def reddit_post(name="t3_a", score=10, **over):
    data = {
        "name": name,
        "title": f"Post {name}",
        "permalink": f"/r/python/comments/{name}/post/",
        "url": "https://example.com/article",
        "is_self": False,
        "subreddit": "python",
        "author": "guido",
        "score": score,
        "num_comments": 4,
        "selftext": "",
        "created_utc": 1_767_225_600,
        "stickied": False,
        "over_18": False,
    }
    data.update(over)
    return data


def listing(*posts):
    return {"data": {"children": [{"data": p} for p in posts]}}


# ---------------------------------------------------------------------------
# stayup-api client
# ---------------------------------------------------------------------------


@patch("fetch_reddit.API_KEY", "test-key")
class TestRegisterProvider:
    @patch("fetch_reddit.requests.request")
    def test_posts_display_name_and_sort_order(self, mock_request):
        mock_request.return_value = mock_response()
        register_provider()
        method, url = mock_request.call_args[0]
        assert method == "POST"
        assert url.endswith("/connector-api/reddit/register")
        body = mock_request.call_args.kwargs["json"]
        assert body["displayName"] == "Reddit"
        assert body["sortOrder"] == 60

    @patch("fetch_reddit.requests.request")
    def test_sends_the_bearer_token(self, mock_request):
        mock_request.return_value = mock_response()
        register_provider()
        assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


class TestApiRequestWithoutKey:
    @patch("fetch_reddit.API_KEY", None)
    def test_raises_when_no_api_key_is_configured(self):
        with pytest.raises(RuntimeError, match="STAYUP_API_KEY"):
            register_provider()


@patch("fetch_reddit.API_KEY", "test-key")
class TestSources:
    @patch("fetch_reddit.requests.request")
    def test_add_source_posts_the_url_and_returns_the_id(self, mock_request):
        mock_request.return_value = mock_response({"id": 7})
        assert add_source("https://www.reddit.com/r/python/") == 7
        assert mock_request.call_args[0][1].endswith("/connector-api/reddit/sources")
        assert mock_request.call_args.kwargs["json"] == {"url": "https://www.reddit.com/r/python/"}

    @patch("fetch_reddit.requests.request")
    def test_get_sources_returns_id_url_config_tuples(self, mock_request):
        mock_request.return_value = mock_response(
            {
                "sources": [
                    {"id": 1, "url": "https://www.reddit.com/r/a/", "config": {"sort": "top"}},
                    {"id": 2, "url": "u"},
                ]
            }
        )
        assert get_sources() == [(1, "https://www.reddit.com/r/a/", {"sort": "top"}), (2, "u", {})]


@patch("fetch_reddit.API_KEY", "test-key")
class TestGetSavedVersions:
    @patch("fetch_reddit.requests.request")
    def test_returns_every_known_post_id(self, mock_request):
        mock_request.return_value = mock_response({"versions": ["t3_a", "t3_b"]})
        assert get_saved_versions(3) == {"t3_a", "t3_b"}
        assert mock_request.call_args[0][1].endswith("/connector-api/reddit/sources/3/versions")


@patch("fetch_reddit.API_KEY", "test-key")
class TestSavePosts:
    @patch("fetch_reddit.requests.request")
    def test_batches_posts_into_one_call(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        posts = [_parse_post(reddit_post("t3_a")), _parse_post(reddit_post("t3_b", score=3))]
        save_posts(1, posts, NOW)
        assert mock_request.call_count == 1
        items = mock_request.call_args.kwargs["json"]["items"]
        assert [i["version"] for i in items] == ["t3_a", "t3_b"]
        assert items[0]["repositoryId"] == 1
        assert items[0]["success"] is True
        assert items[0]["datetime"] == "2026-01-01T00:00:00+00:00"
        assert json.loads(items[0]["content"]) == {
            "title": "Post t3_a",
            "link": "https://www.reddit.com/r/python/comments/t3_a/post/",
            "url": "https://example.com/article",
            "subreddit": "python",
            "author": "guido",
            "score": 10,
            "comments": 4,
            "summary": "",
        }

    @patch("fetch_reddit.requests.request")
    def test_does_nothing_on_an_empty_batch(self, mock_request):
        save_posts(1, [], NOW)
        mock_request.assert_not_called()

    @patch("fetch_reddit.requests.request")
    def test_stores_a_null_date_when_reddit_gave_none(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_posts(1, [_parse_post(reddit_post(created_utc=None))], NOW)
        assert mock_request.call_args.kwargs["json"]["items"][0]["datetime"] is None


@patch("fetch_reddit.API_KEY", "test-key")
class TestSaveError:
    @patch("fetch_reddit.requests.request")
    def test_posts_the_error(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_error(4, "boom", NOW)
        assert mock_request.call_args[0][1].endswith("/connector-api/reddit/errors")
        assert mock_request.call_args.kwargs["json"] == {
            "repositoryId": 4,
            "error": "boom",
            "executedAt": NOW.isoformat(),
        }


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


class TestSubredditFromUrl:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://www.reddit.com/r/python/", "python"),
            ("https://reddit.com/r/Python", "Python"),
            ("https://old.reddit.com/r/python/top/?t=week", "python"),
            ("https://www.reddit.com/r/python+django/", "python+django"),
        ],
    )
    def test_extracts_the_name(self, url, expected):
        assert subreddit_from_url(url) == expected

    def test_rejects_anything_else(self):
        with pytest.raises(RuntimeError, match="Not a subreddit URL"):
            subreddit_from_url("https://www.reddit.com/user/spez")


class TestParsePost:
    def test_builds_the_permalink_and_the_date(self):
        post = _parse_post(reddit_post())
        assert post["version"] == "t3_a"
        assert post["link"] == "https://www.reddit.com/r/python/comments/t3_a/post/"
        assert post["published"] == datetime(2026, 1, 1, tzinfo=UTC)

    def test_a_text_post_has_no_external_url(self):
        assert _parse_post(reddit_post(is_self=True))["url"] is None

    def test_truncates_a_long_text_post(self):
        post = _parse_post(reddit_post(selftext="x" * 2000))
        assert len(post["summary"]) == 601
        assert post["summary"].endswith("…")

    def test_keeps_a_short_text_post_whole(self):
        assert _parse_post(reddit_post(selftext="  hello  "))["summary"] == "hello"

    def test_tolerates_missing_counters(self):
        data = reddit_post()
        del data["score"], data["num_comments"]
        post = _parse_post(data)
        assert post["score"] == 0
        assert post["comments"] == 0


class TestGetOauthToken:
    @patch("fetch_reddit.REDDIT_CLIENT_ID", None)
    @patch("fetch_reddit.REDDIT_CLIENT_SECRET", None)
    @patch("fetch_reddit.requests.post")
    def test_is_none_without_an_app(self, mock_post):
        assert get_oauth_token() is None
        mock_post.assert_not_called()

    @patch("fetch_reddit.REDDIT_CLIENT_ID", "id")
    @patch("fetch_reddit.REDDIT_CLIENT_SECRET", "secret")
    @patch("fetch_reddit.requests.post")
    def test_exchanges_the_app_credentials(self, mock_post):
        mock_post.return_value = mock_response({"access_token": "tok"})
        assert get_oauth_token() == "tok"
        kwargs = mock_post.call_args.kwargs
        assert kwargs["auth"] == ("id", "secret")
        assert kwargs["data"] == {"grant_type": "client_credentials"}
        assert "stayup-reddit" in kwargs["headers"]["User-Agent"]


class TestFetchPosts:
    @patch("fetch_reddit.requests.get")
    def test_reads_the_anonymous_listing(self, mock_get):
        mock_get.return_value = mock_response(listing(reddit_post("t3_a")))
        posts = fetch_posts("python", "hot", 20)
        assert [p["version"] for p in posts] == ["t3_a"]
        url = mock_get.call_args[0][0]
        assert url == "https://www.reddit.com/r/python/hot.json"
        assert mock_get.call_args.kwargs["params"] == {"limit": 20, "raw_json": 1}
        assert "Authorization" not in mock_get.call_args.kwargs["headers"]

    @patch("fetch_reddit.requests.get")
    def test_uses_the_oauth_host_with_a_token(self, mock_get):
        mock_get.return_value = mock_response(listing())
        fetch_posts("python", "new", 5, token="tok")
        assert mock_get.call_args[0][0] == "https://oauth.reddit.com/r/python/new.json"
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"

    @patch("fetch_reddit.requests.get")
    def test_top_is_scoped_to_the_day(self, mock_get):
        mock_get.return_value = mock_response(listing())
        fetch_posts("python", "top", 5)
        assert mock_get.call_args.kwargs["params"]["t"] == "day"

    @patch("fetch_reddit.requests.get")
    def test_leaves_out_stickied_and_nsfw_posts(self, mock_get):
        mock_get.return_value = mock_response(
            listing(
                reddit_post("t3_sticky", stickied=True),
                reddit_post("t3_nsfw", over_18=True),
                reddit_post("t3_ok"),
            )
        )
        assert [p["version"] for p in fetch_posts("python", "hot", 10)] == ["t3_ok"]

    @pytest.mark.parametrize("status", [403, 429])
    @patch("fetch_reddit.requests.get")
    def test_explains_a_blocked_ip(self, mock_get, status):
        mock_get.return_value = mock_response({}, status=status)
        with pytest.raises(RuntimeError, match="REDDIT_CLIENT_ID"):
            fetch_posts("python", "hot", 10)

    @patch("fetch_reddit.requests.get")
    def test_raises_on_other_http_errors(self, mock_get):
        response = mock_response({}, status=500)
        response.raise_for_status.side_effect = RuntimeError("HTTP 500")
        mock_get.return_value = response
        with pytest.raises(RuntimeError, match="HTTP 500"):
            fetch_posts("python", "hot", 10)

    def test_rejects_an_unknown_sort(self):
        with pytest.raises(RuntimeError, match="Unknown sort"):
            fetch_posts("python", "controversial", 10)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

URL = "https://www.reddit.com/r/python/"


class TestProcessRepository:
    @patch("fetch_reddit.save_error")
    @patch("fetch_reddit.save_posts")
    @patch("fetch_reddit.get_saved_versions", return_value={"t3_a"})
    @patch("fetch_reddit.fetch_posts")
    def test_stores_only_the_posts_not_stored_yet(self, mock_fetch, _versions, mock_save, mock_error):
        mock_fetch.return_value = [_parse_post(reddit_post(n)) for n in ("t3_a", "t3_b", "t3_c")]
        process_repository(1, URL, NOW, {})
        assert [p["version"] for p in mock_save.call_args[0][1]] == ["t3_b", "t3_c"]
        mock_error.assert_not_called()

    @patch("fetch_reddit.save_posts")
    @patch("fetch_reddit.get_saved_versions", return_value=set())
    @patch("fetch_reddit.fetch_posts")
    def test_caps_a_run_at_max_entries_and_over_fetches_to_compensate(self, mock_fetch, _versions, mock_save):
        mock_fetch.return_value = [_parse_post(reddit_post(f"t3_{i}")) for i in range(20)]
        process_repository(1, URL, NOW, {"max_entries": 3})
        assert len(mock_save.call_args[0][1]) == 3
        assert mock_fetch.call_args.kwargs["limit"] == 12

    @patch("fetch_reddit.save_posts")
    @patch("fetch_reddit.get_saved_versions", return_value=set())
    @patch("fetch_reddit.fetch_posts")
    def test_defaults_to_five_hot_posts(self, mock_fetch, _versions, mock_save):
        mock_fetch.return_value = [_parse_post(reddit_post(f"t3_{i}")) for i in range(9)]
        process_repository(1, URL, NOW, {})
        assert len(mock_save.call_args[0][1]) == 5
        assert mock_fetch.call_args[0][:2] == ("python", "hot")

    @patch("fetch_reddit.save_posts")
    @patch("fetch_reddit.get_saved_versions", return_value=set())
    @patch("fetch_reddit.fetch_posts")
    def test_skips_posts_under_the_minimum_score(self, mock_fetch, _versions, mock_save):
        mock_fetch.return_value = [
            _parse_post(reddit_post("t3_low", score=2)),
            _parse_post(reddit_post("t3_hi", score=50)),
        ]
        process_repository(1, URL, NOW, {"min_score": 10})
        assert [p["version"] for p in mock_save.call_args[0][1]] == ["t3_hi"]

    @patch("fetch_reddit.save_posts")
    @patch("fetch_reddit.get_saved_versions", return_value=set())
    @patch("fetch_reddit.fetch_posts")
    def test_honors_the_configured_sort_and_token(self, mock_fetch, _versions, _save):
        mock_fetch.return_value = []
        process_repository(1, URL, NOW, {"sort": "top"}, token="tok")
        assert mock_fetch.call_args[0][:2] == ("python", "top")
        assert mock_fetch.call_args.kwargs["token"] == "tok"

    @patch("fetch_reddit.save_error")
    @patch("fetch_reddit.fetch_posts", side_effect=RuntimeError("blocked"))
    def test_reports_a_failure_through_the_api(self, _fetch, mock_error, capsys):
        process_repository(1, URL, NOW, {})
        mock_error.assert_called_once_with(1, "blocked", NOW)
        assert "blocked" in capsys.readouterr().err

    @patch("fetch_reddit.save_error")
    def test_reports_a_url_that_is_not_a_subreddit(self, mock_error):
        process_repository(1, "https://example.com/", NOW, {})
        assert "Not a subreddit URL" in mock_error.call_args[0][1]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class TestMain:
    @patch("fetch_reddit.process_repository")
    @patch("fetch_reddit.get_oauth_token", return_value=None)
    @patch("fetch_reddit.get_sources", return_value=[(1, URL, {}), (2, "https://www.reddit.com/r/go/", {})])
    @patch("fetch_reddit.register_provider")
    def test_registers_then_processes_every_source(self, mock_register, _sources, _token, mock_process):
        with patch("sys.argv", ["fetch_reddit.py"]):
            main()
        mock_register.assert_called_once()
        assert mock_process.call_count == 2
        assert mock_process.call_args_list[0][0][:2] == (1, URL)
        assert mock_process.call_args_list[0].kwargs["token"] is None

    @patch("fetch_reddit.process_repository")
    @patch("fetch_reddit.add_source")
    @patch("fetch_reddit.register_provider")
    def test_add_registers_the_source_and_exits(self, _register, mock_add, mock_process, capsys):
        with patch("sys.argv", ["fetch_reddit.py", "--add", URL]):
            main()
        mock_add.assert_called_once_with(URL)
        mock_process.assert_not_called()
        assert "Repository added" in capsys.readouterr().out

    @patch("fetch_reddit.process_repository")
    @patch("fetch_reddit.get_sources", return_value=[])
    @patch("fetch_reddit.register_provider")
    def test_says_so_when_nothing_is_tracked(self, _register, _sources, mock_process, capsys):
        with patch("sys.argv", ["fetch_reddit.py"]):
            main()
        mock_process.assert_not_called()
        assert "No repositories tracked" in capsys.readouterr().out

    @patch("fetch_reddit.process_repository")
    @patch("fetch_reddit.get_oauth_token", side_effect=RuntimeError("bad credentials"))
    @patch("fetch_reddit.get_sources", return_value=[(1, URL, {})])
    @patch("fetch_reddit.register_provider")
    def test_falls_back_to_anonymous_when_oauth_fails(self, _register, _sources, _token, mock_process, capsys):
        with patch("sys.argv", ["fetch_reddit.py"]):
            main()
        assert mock_process.call_args.kwargs["token"] is None
        assert "continuing anonymously" in capsys.readouterr().err

    @patch("fetch_reddit.process_repository")
    @patch("fetch_reddit.get_oauth_token", return_value="tok")
    @patch("fetch_reddit.get_sources", return_value=[(1, URL, {})])
    @patch("fetch_reddit.register_provider")
    def test_passes_the_token_to_every_source(self, _register, _sources, _token, mock_process):
        with patch("sys.argv", ["fetch_reddit.py"]):
            main()
        assert mock_process.call_args.kwargs["token"] == "tok"
