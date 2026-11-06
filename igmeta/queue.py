"""The post queue: one folder per post under posts/, holding post.yml and its media.

    posts/2026-11-10-jury-award/
        post.yml        what to post and when
        01.jpg ...      the media, in order
        status.json     written by the publisher (do not edit)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from PIL import Image

DEFAULT_TZ = "America/New_York"
IMAGE_EXT = {".jpg", ".jpeg"}
CONVERTIBLE = {".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov"}
TYPES = {"image", "carousel", "reel", "story"}
MAX_ATTEMPTS = 3


@dataclass
class Post:
    slug: str
    folder: Path
    account: str
    publish_at: datetime | None
    type: str
    media: list[Path]
    caption: str = ""
    share_to_feed: bool = True
    cover: Path | None = None
    paused: bool = False
    status: dict = field(default_factory=dict)

    @property
    def published(self) -> bool:
        return self.status.get("status") == "published"

    @property
    def gave_up(self) -> bool:
        return self.status.get("status") == "failed" and self.status.get("attempts", 0) >= MAX_ATTEMPTS

    def is_due(self, now: datetime) -> bool:
        return (not self.paused and not self.published and not self.gave_up
                and self.publish_at is not None and self.publish_at <= now)

    def save_status(self, **fields) -> None:
        self.status.update(fields)
        (self.folder / "status.json").write_text(json.dumps(self.status, indent=2) + "\n")


def parse_time(value, tz: str = DEFAULT_TZ) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T", 1))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


def load_post(folder: Path, default_account: str | None = None) -> Post:
    spec = yaml.safe_load((folder / "post.yml").read_text()) or {}
    media = [folder / m for m in (spec.get("media") or [])]
    if not media:  # default: every media file in the folder, in name order
        media = sorted(p for p in folder.iterdir()
                       if p.suffix.lower() in IMAGE_EXT | CONVERTIBLE | VIDEO_EXT
                       and p.stem != "cover")
    kind = spec.get("type") or _infer_type(media)
    status_file = folder / "status.json"
    return Post(
        slug=folder.name,
        folder=folder,
        account=spec.get("account") or default_account or "",
        publish_at=parse_time(spec.get("publish_at"), spec.get("timezone", DEFAULT_TZ)),
        type=kind,
        media=media,
        caption=(spec.get("caption") or "").strip(),
        share_to_feed=spec.get("share_to_feed", True),
        cover=folder / spec["cover"] if spec.get("cover") else None,
        paused=bool(spec.get("paused", False)),
        status=json.loads(status_file.read_text()) if status_file.exists() else {},
    )


def _infer_type(media: list[Path]) -> str:
    if len(media) > 1:
        return "carousel"
    if media and media[0].suffix.lower() in VIDEO_EXT:
        return "reel"
    return "image"


def load_queue(root: Path, default_account: str | None = None) -> list[Post]:
    posts_dir = root / "posts"
    if not posts_dir.exists():
        return []
    posts = [load_post(f, default_account) for f in sorted(posts_dir.iterdir())
             if (f / "post.yml").exists()]
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(posts, key=lambda p: (p.publish_at or far, p.slug))


# -- validation -------------------------------------------------------------

def validate(post: Post, accounts: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block publishing; warnings don't."""
    errors: list[str] = []
    warnings: list[str] = []
    if post.type not in TYPES:
        errors.append(f"type must be one of {sorted(TYPES)}, not {post.type!r}")
    if not post.account:
        errors.append("no account set (add `account:` or set a default in accounts.yml)")
    elif accounts is not None and post.account not in accounts:
        errors.append(f"account {post.account!r} is not in accounts.yml")
    if post.publish_at is None and not post.paused:
        warnings.append("no publish_at, so it will never go out")

    if not post.media:
        errors.append("no media")
    for m in post.media:
        if not m.exists():
            errors.append(f"{m.name}: file not found")

    n = len(post.media)
    if post.type == "carousel" and not 2 <= n <= 10:
        errors.append(f"a carousel needs 2-10 items, this has {n}")
    if post.type in ("image", "reel", "story") and n != 1:
        errors.append(f"a {post.type} takes exactly one file, this has {n}")
    if post.type == "reel" and post.media and post.media[0].suffix.lower() not in VIDEO_EXT:
        errors.append("a reel needs an .mp4 or .mov file")

    caption = post.caption
    if not caption and post.type != "story":
        warnings.append("no caption (a caption starting with # needs `caption: |` on its own line)")
    if post.type == "story" and caption:
        warnings.append("stories don't show captions; it will be ignored")
    if len(caption) > 2200:
        errors.append(f"caption is {len(caption)} characters; the limit is 2,200")
    if len(re.findall(r"(?<!\w)#\w+", caption)) > 30:
        errors.append("more than 30 hashtags")
    if len(re.findall(r"(?<!\w)@[\w.]+", caption)) > 20:
        errors.append("more than 20 @mentions")

    ratios = []
    for m in post.media:
        if not m.exists():
            continue
        ext = m.suffix.lower()
        if ext in CONVERTIBLE:
            errors.append(f"{m.name}: images must be JPEG; run `igmeta prepare`")
            continue
        if ext in VIDEO_EXT:
            if m.stat().st_size > 300 * 1024 * 1024:
                errors.append(f"{m.name}: videos must be 300 MB or less")
            continue
        if ext not in IMAGE_EXT:
            errors.append(f"{m.name}: unsupported file type")
            continue
        if m.stat().st_size > 8 * 1024 * 1024:
            errors.append(f"{m.name}: images must be 8 MB or less")
        with Image.open(m) as im:
            w, h = im.size
        ratio = w / h
        ratios.append(round(ratio, 2))
        if post.type == "story":
            if abs(ratio - 9 / 16) > 0.02:
                warnings.append(f"{m.name}: stories look best at 9:16 (1080x1920); this is {w}x{h}")
        elif not 0.8 - 0.005 <= ratio <= 1.91 + 0.005:
            errors.append(f"{m.name}: aspect ratio {w}x{h} is outside 4:5 to 1.91:1")
        if w < 320:
            errors.append(f"{m.name}: at least 320 px wide")
        elif w > 1440:
            warnings.append(f"{m.name}: wider than 1440 px; Instagram will downsize it")
    if post.type == "carousel" and len(set(ratios)) > 1:
        warnings.append("carousel images have different shapes; Instagram crops them all to the first")
    return errors, warnings


def prepare(post: Post, quality: int = 92) -> list[str]:
    """Convert PNG/WebP media to JPEG in place and update post.yml. Returns what changed."""
    changed = []
    spec_path = post.folder / "post.yml"
    text = spec_path.read_text()
    for m in post.media:
        if m.suffix.lower() not in CONVERTIBLE or not m.exists():
            continue
        out = m.with_suffix(".jpg")
        with Image.open(m) as im:
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
        m.unlink()
        # keep the author's formatting: swap the file name only
        text = re.sub(rf"(?<![\w.-]){re.escape(m.name)}(?![\w.-])", out.name, text)
        changed.append(f"{m.name} -> {out.name}")
    if changed:
        spec_path.write_text(text)
    return changed
