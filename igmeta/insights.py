"""Daily snapshots of account and per-post insights, appended to CSV files.

    <out>/<account>/account_daily.csv   one row per day
    <out>/<account>/media_daily.csv     one row per post per day (last N posts)
    <out>/<account>/stories.csv         one row per story (stories expire after 24 hours)
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .graph import Graph

ACCOUNT_TOTALS = ["reach", "views", "accounts_engaged", "total_interactions", "likes",
                  "comments", "shares", "saves", "profile_links_taps", "follows_and_unfollows"]
FEED_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"]
REEL_METRICS = FEED_METRICS + ["ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
STORY_METRICS = ["views", "reach", "replies", "shares", "total_interactions"]


def _append(path: Path, rows: list[dict], key: tuple[str, ...]) -> int:
    """Append rows, replacing any existing row with the same key. Returns rows written."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if path.exists():
        with path.open(newline="") as f:
            existing = list(csv.DictReader(f))
    new_keys = {tuple(str(r.get(k, "")) for k in key) for r in rows}
    kept = [r for r in existing if tuple(r.get(k, "") for k in key) not in new_keys]
    fields: list[str] = []
    for r in kept + rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(kept + rows)
    return len(rows)


def account_snapshot(graph: Graph, day: date) -> dict:
    me = graph.me()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    row = {"date": day.isoformat(), "username": me.get("username"),
           "followers": me.get("followers_count"), "posts": me.get("media_count")}
    row.update(graph.insights(graph.user_id, ACCOUNT_TOTALS, period="day", metric_type="total_value",
                              since=int(start.timestamp()), until=int(end.timestamp())))
    # follower_count (daily follows) only appears once the account has 100+ followers
    row.update(graph.insights(graph.user_id, ["follower_count"], period="day",
                              since=int(start.timestamp()), until=int(end.timestamp())))
    return row


def media_snapshot(graph: Graph, today: date, limit: int = 50) -> list[dict]:
    rows = []
    for m in graph.recent_media(limit=limit):
        product = m.get("media_product_type") or ""
        metrics = REEL_METRICS if product == "REELS" else FEED_METRICS
        row = {"date": today.isoformat(), "media_id": m["id"], "posted": m.get("timestamp"),
               "type": m.get("media_type"), "product": product, "permalink": m.get("permalink"),
               "caption": (m.get("caption") or "").split("\n")[0][:80]}
        row.update(graph.insights(m["id"], metrics))
        rows.append(row)
    return rows


def story_snapshot(graph: Graph) -> list[dict]:
    rows = []
    for s in graph.stories():
        row = {"media_id": s["id"], "posted": s.get("timestamp"), "type": s.get("media_type"),
               "permalink": s.get("permalink")}
        row.update(graph.insights(s["id"], STORY_METRICS))
        rows.append(row)
    return rows


def pull(graph: Graph, out_dir: Path, account: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)  # full day; Meta's numbers can lag up to 48 hours
    base = out_dir / account
    acct = account_snapshot(graph, yesterday)
    media = media_snapshot(graph, today)
    stories = story_snapshot(graph)
    _append(base / "account_daily.csv", [acct], ("date",))
    _append(base / "media_daily.csv", media, ("date", "media_id"))
    _append(base / "stories.csv", stories, ("media_id",))
    return {"account": acct, "media": len(media), "stories": len(stories)}
