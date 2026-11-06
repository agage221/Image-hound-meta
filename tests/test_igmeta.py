import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from PIL import Image

from igmeta import insights
from igmeta.config import load_config, media_url
from igmeta.graph import Graph, GraphError
from igmeta.publish import publish_post
from igmeta.queue import load_post, load_queue, parse_time, prepare, validate


# -- a fake graph.instagram.com ---------------------------------------------

class FakeMeta:
    def __init__(self, recent=None, fail_publish=False, status_seq=None):
        self.calls = []
        self.recent = recent or []
        self.fail_publish = fail_publish
        self.status_seq = list(status_seq or [])
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if request.method == "POST":
            params.update(dict(httpx.QueryParams(request.content.decode())))
        self.calls.append((request.method, path, params))
        if path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "1784", "username": "imagehound",
                                             "account_type": "BUSINESS", "followers_count": 42,
                                             "media_count": 3})
        if path.endswith("/1784/media") and request.method == "POST":
            self.n += 1
            return httpx.Response(200, json={"id": f"c{self.n}"})
        if path.endswith("/1784/media"):
            return httpx.Response(200, json={"data": self.recent})
        if path.endswith("/media_publish"):
            if self.fail_publish:
                return httpx.Response(400, json={"error": {"message": "Media upload failed",
                                                           "code": 9004}})
            return httpx.Response(200, json={"id": "m99"})
        if path.endswith("/content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": 2,
                                                       "config": {"quota_total": 100}}]})
        if path.endswith("/insights"):
            metrics = params["metric"].split(",")
            if "bad_metric" in metrics and len(metrics) > 1:
                return httpx.Response(400, json={"error": {"message": "invalid metric"}})
            if metrics == ["bad_metric"]:
                return httpx.Response(400, json={"error": {"message": "invalid metric"}})
            return httpx.Response(200, json={"data": [
                {"name": m, "total_value": {"value": 7}} for m in metrics]})
        if path.endswith("/stories"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/m99"):
            return httpx.Response(200, json={"id": "m99", "permalink": "https://www.instagram.com/p/X/",
                                             "timestamp": "2026-11-10T14:30:00+0000"})
        if path.endswith("refresh_access_token"):
            return httpx.Response(200, json={"access_token": "new", "expires_in": 5184000})
        # container status
        code = self.status_seq.pop(0) if self.status_seq else "FINISHED"
        return httpx.Response(200, json={"status_code": code})


def graph_for(fake: FakeMeta) -> Graph:
    return Graph("tok", client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                 sleep=lambda s: None)


def jpg(path: Path, size=(1080, 1350)) -> Path:
    Image.new("RGB", size, (28, 37, 65)).save(path, "JPEG")
    return path


def make_post(tmp: Path, slug="2026-11-10-test", files=("01.jpg",), spec="", sizes=None) -> Path:
    folder = tmp / "posts" / slug
    folder.mkdir(parents=True)
    for i, name in enumerate(files):
        size = (sizes or {}).get(name, (1080, 1350))
        if name.endswith(".png"):
            Image.new("RGBA", size, (255, 106, 69, 255)).save(folder / name)
        elif name.endswith(".mp4"):
            (folder / name).write_bytes(b"\0" * 100)
        else:
            jpg(folder / name, size)
    (folder / "post.yml").write_text(
        "account: imagehound\npublish_at: 2026-11-10 09:30\n"
        f"media: [{', '.join(files)}]\ncaption: Hello there\n" + spec)
    return folder


URL = lambda f: f"https://example.com/{f.name}"
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


# -- queue ------------------------------------------------------------------

def test_parse_time_defaults_to_eastern():
    assert parse_time("2026-11-10 09:30") == datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
    assert parse_time("2026-07-10 09:30") == datetime(2026, 7, 10, 13, 30, tzinfo=timezone.utc)
    assert parse_time("2026-11-10T09:30:00+00:00").hour == 9
    assert parse_time(None) is None


def test_type_inferred(tmp_path):
    assert load_post(make_post(tmp_path, "a")).type == "image"
    assert load_post(make_post(tmp_path, "b", ("01.jpg", "02.jpg"))).type == "carousel"
    assert load_post(make_post(tmp_path, "c", ("clip.mp4",))).type == "reel"


def test_due_and_ordering(tmp_path):
    make_post(tmp_path, "later", spec="").joinpath("post.yml").write_text(
        "account: imagehound\npublish_at: 2026-12-01 09:00\nmedia: [01.jpg]\n")
    make_post(tmp_path, "paused", spec="paused: true\n")
    make_post(tmp_path, "now")
    posts = load_queue(tmp_path)
    assert [p.slug for p in posts] == ["now", "paused", "later"]
    assert [p.slug for p in posts if p.is_due(NOW)] == ["now"]


def test_validate_good_post(tmp_path):
    errors, warnings = validate(load_post(make_post(tmp_path)), {"imagehound"})
    assert errors == [] and warnings == []


def test_validate_catches_problems(tmp_path):
    folder = make_post(tmp_path, files=("01.png", "02.jpg"), sizes={"02.jpg": (1080, 1920)},
                       spec="account: nobody\n")
    (folder / "post.yml").write_text((folder / "post.yml").read_text().replace(
        "caption: Hello there", "caption: |\n  " + " ".join(f"#t{i}" for i in range(31))))
    errors, _ = validate(load_post(folder), {"imagehound"})
    text = " | ".join(errors)
    assert "must be JPEG" in text
    assert "aspect ratio" in text
    assert "30 hashtags" in text


def test_empty_caption_warned(tmp_path):
    folder = make_post(tmp_path)
    (folder / "post.yml").write_text((folder / "post.yml").read_text().replace(
        "caption: Hello there", "caption: #oops this became a YAML comment"))
    assert any("no caption" in w for w in validate(load_post(folder))[1])


def test_carousel_size_limits(tmp_path):
    folder = make_post(tmp_path, files=("01.jpg",), spec="type: carousel\n")
    errors, _ = validate(load_post(folder))
    assert any("2-10 items" in e for e in errors)


def test_mixed_shapes_warned(tmp_path):
    folder = make_post(tmp_path, files=("01.jpg", "02.jpg"), sizes={"02.jpg": (1080, 1080)})
    errors, warnings = validate(load_post(folder))
    assert not errors and any("different shapes" in w for w in warnings)


def test_prepare_converts_png_and_keeps_yaml(tmp_path):
    folder = make_post(tmp_path, files=("01.png", "02.jpg"))
    post = load_post(folder)
    assert prepare(post) == ["01.png -> 01.jpg"]
    assert (folder / "01.jpg").exists() and not (folder / "01.png").exists()
    text = (folder / "post.yml").read_text()
    assert "media: [01.jpg, 02.jpg]" in text and "caption: Hello there" in text
    assert validate(load_post(folder))[0] == []


# -- publishing -------------------------------------------------------------

def test_publish_single_image(tmp_path):
    fake = FakeMeta()
    post = load_post(make_post(tmp_path))
    result = publish_post(graph_for(fake), post, URL)
    assert result["status"] == "published"
    assert result["permalink"] == "https://www.instagram.com/p/X/"
    created = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/media")]
    assert created[0][2]["image_url"] == "https://example.com/01.jpg"
    assert created[0][2]["caption"] == "Hello there"
    saved = json.loads((post.folder / "status.json").read_text())
    assert saved["media_id"] == "m99"
    assert not load_post(post.folder).is_due(NOW)


def test_publish_carousel(tmp_path):
    fake = FakeMeta()
    post = load_post(make_post(tmp_path, files=("01.jpg", "02.jpg", "03.jpg")))
    assert publish_post(graph_for(fake), post, URL)["status"] == "published"
    created = [c[2] for c in fake.calls if c[0] == "POST" and c[1].endswith("/media")]
    assert [c.get("is_carousel_item") for c in created[:3]] == ["true"] * 3
    assert "caption" not in created[0]
    parent = created[3]
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "c1,c2,c3"
    assert parent["caption"] == "Hello there"


def test_publish_story_drops_caption(tmp_path):
    fake = FakeMeta()
    post = load_post(make_post(tmp_path, sizes={"01.jpg": (1080, 1920)}, spec="type: story\n"))
    assert publish_post(graph_for(fake), post, URL)["status"] == "published"
    created = [c[2] for c in fake.calls if c[0] == "POST" and c[1].endswith("/media")][0]
    assert created["media_type"] == "STORIES" and "caption" not in created


def test_waits_for_container(tmp_path):
    fake = FakeMeta(status_seq=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    post = load_post(make_post(tmp_path))
    assert publish_post(graph_for(fake), post, URL)["status"] == "published"


def test_container_error_is_recorded(tmp_path):
    fake = FakeMeta(status_seq=["ERROR"])
    post = load_post(make_post(tmp_path))
    result = publish_post(graph_for(fake), post, URL, now=lambda: NOW)
    assert result["status"] == "failed" and result["attempts"] == 1
    assert load_post(post.folder).is_due(NOW)  # retried next run


def test_gives_up_after_three_failures(tmp_path):
    fake = FakeMeta(fail_publish=True)
    folder = make_post(tmp_path)
    for _ in range(3):
        publish_post(graph_for(fake), load_post(folder), URL, now=lambda: NOW)
    post = load_post(folder)
    assert post.status["attempts"] == 3 and "Media upload failed" in post.status["error"]
    assert not post.is_due(NOW)


def test_never_posts_twice(tmp_path):
    fake = FakeMeta(recent=[{"id": "old1", "caption": "Hello   there\n",
                             "permalink": "https://www.instagram.com/p/OLD/"}])
    post = load_post(make_post(tmp_path))
    result = publish_post(graph_for(fake), post, URL)
    assert result["status"] == "published" and result["media_id"] == "old1"
    assert not any(c[1].endswith("/media_publish") for c in fake.calls)


def test_dry_run_calls_nothing(tmp_path):
    fake = FakeMeta()
    post = load_post(make_post(tmp_path, files=("01.jpg", "02.jpg")))
    result = publish_post(graph_for(fake), post, URL, dry_run=True)
    assert result["status"] == "dry-run" and len(result["urls"]) == 2
    assert fake.calls == [] and not (post.folder / "status.json").exists()


def test_invalid_post_not_sent(tmp_path):
    fake = FakeMeta()
    post = load_post(make_post(tmp_path, files=("01.png",)))
    assert publish_post(graph_for(fake), post, URL)["status"] == "failed"
    assert fake.calls == []


# -- graph, config, insights ------------------------------------------------

def test_graph_error_and_limit():
    fake = FakeMeta(fail_publish=True)
    g = graph_for(fake)
    assert g.publishing_limit() == {"used": 2, "total": 100}
    with pytest.raises(GraphError, match="Media upload failed"):
        g.publish_container("c1")


def test_insights_skip_bad_metrics():
    g = graph_for(FakeMeta())
    assert g.insights("m1", ["reach", "bad_metric", "views"]) == {"reach": 7, "views": 7}


def test_refresh_token_unversioned():
    fake = FakeMeta()
    assert graph_for(fake).refresh_token()["access_token"] == "new"
    assert fake.calls[-1][1] == "/refresh_access_token"


def test_insights_pull_appends(tmp_path):
    fake = FakeMeta(recent=[{"id": "m1", "caption": "One\nmore", "media_product_type": "FEED"},
                            {"id": "m2", "caption": "Two", "media_product_type": "REELS"}])
    g = graph_for(fake)
    insights.pull(g, tmp_path, "imagehound", today=date(2026, 11, 10))
    insights.pull(g, tmp_path, "imagehound", today=date(2026, 11, 10))  # same day: replaced
    insights.pull(g, tmp_path, "imagehound", today=date(2026, 11, 11))
    media = (tmp_path / "imagehound" / "media_daily.csv").read_text().splitlines()
    assert len(media) == 1 + 4
    acct = (tmp_path / "imagehound" / "account_daily.csv").read_text().splitlines()
    assert len(acct) == 3 and acct[1].startswith("2026-11-09,imagehound,42,3")


def test_config_and_media_url(tmp_path, monkeypatch):
    (tmp_path / "accounts.yml").write_text(
        "accounts:\n  imagehound:\n    token_secret: TOK_A\n  mgi: {}\n")
    cfg = load_config(tmp_path)
    assert cfg.default is None
    assert cfg.account("mgi").token_secret == "IG_TOKEN_MGI"
    monkeypatch.setenv("TOK_A", "abc")
    assert cfg.account("imagehound").token == "abc"
    f = tmp_path / "posts" / "x" / "01.jpg"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    monkeypatch.setenv("GITHUB_REPOSITORY", "agage221/image-hound-meta")
    monkeypatch.setenv("MEDIA_SHA", "abc123")
    assert media_url(tmp_path, f) == \
        "https://raw.githubusercontent.com/agage221/image-hound-meta/abc123/posts/x/01.jpg"
    monkeypatch.setenv("MEDIA_BASE_URL", "https://cdn.example.com/ig/")
    assert media_url(tmp_path, f) == "https://cdn.example.com/ig/posts/x/01.jpg"
