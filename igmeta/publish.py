"""Turn a queued post into an Instagram post."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

from .graph import Graph, GraphError
from .queue import VIDEO_EXT, Post, _ext, validate


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def already_posted(graph: Graph, post: Post) -> dict | None:
    """Guard against double posting (e.g. a run that published but failed to save its status)."""
    if not post.caption or post.type == "story":
        return None
    want = _norm(post.caption)
    for m in graph.recent_media(limit=15):
        if _norm(m.get("caption")) == want:
            return m
    return None


def build_container(graph: Graph, post: Post, store) -> str:
    def url_for(name: str) -> str:
        return store.url(post.slug, name)

    caption = post.caption if post.type != "story" else None
    if post.type == "image":
        return graph.create_container(image_url=url_for(post.media[0]), caption=caption)
    if post.type == "story":
        m = post.media[0]
        if _ext(m) in VIDEO_EXT:
            return graph.create_container(media_type="STORIES", video_url=url_for(m))
        return graph.create_container(media_type="STORIES", image_url=url_for(m))
    if post.type == "reel":
        params = dict(media_type="REELS", video_url=url_for(post.media[0]), caption=caption,
                      share_to_feed=str(post.share_to_feed).lower())
        if post.cover:
            params["cover_url"] = url_for(post.cover)
        return graph.create_container(**params)
    if post.type == "carousel":
        children = []
        for m in post.media:
            if _ext(m) in VIDEO_EXT:
                cid = graph.create_container(media_type="VIDEO", video_url=url_for(m),
                                             is_carousel_item="true")
            else:
                cid = graph.create_container(image_url=url_for(m), is_carousel_item="true")
            children.append(cid)
        for cid in children:
            graph.wait_until_ready(cid)
        return graph.create_container(media_type="CAROUSEL", children=",".join(children),
                                      caption=caption)
    raise ValueError(f"unknown post type {post.type}")


def publish_post(graph: Graph, post: Post, store, *, dry_run: bool = False,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Publish one post. `store` gives each file's public URL (and is checked first)."""
    if store is None:
        post.save_status(status="failed", attempts=post.status.get("attempts", 0) + 1,
                         error="no media storage configured (set SUPABASE_URL)",
                         last_try=now().isoformat())
        return post.status
    errors, _ = validate(post, store=store)
    if not errors:  # files checked locally still have to be in storage for Meta to fetch them
        try:
            errors = [f"{n}: not uploaded to storage yet" for n in
                      post.media + ([post.cover] if post.cover else [])
                      if store.size(post.slug, n) is None]
        except Exception as e:
            errors = [f"couldn't reach storage ({e})"]
    if errors:
        post.save_status(status="failed", attempts=post.status.get("attempts", 0) + 1,
                         error="; ".join(errors), last_try=now().isoformat())
        return post.status
    if dry_run:
        urls = [store.url(post.slug, m) for m in post.media]
        return {"status": "dry-run", "type": post.type, "urls": urls}

    existing = already_posted(graph, post)
    if existing:
        post.save_status(status="published", media_id=existing["id"],
                         permalink=existing.get("permalink"), published_at=existing.get("timestamp"),
                         note="found already on the account; not posted twice")
        return post.status

    try:
        container = build_container(graph, post, store)
        graph.wait_until_ready(container, timeout=600 if post.type in ("reel", "story") else 300)
        media_id = graph.publish_container(container)
        info = graph.media(media_id)
    except GraphError as e:
        post.save_status(status="failed", attempts=post.status.get("attempts", 0) + 1,
                         error=str(e), last_try=now().isoformat())
        return post.status
    post.save_status(status="published", media_id=media_id, permalink=info.get("permalink"),
                     published_at=info.get("timestamp") or now().isoformat(), error=None)
    return post.status
