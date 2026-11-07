"""Where post media lives: a public Supabase Storage bucket (or any public base URL).

Each post's files sit under a folder named after the post:

    <bucket>/2026-11-10-jury-award/01.jpg

Meta downloads them from the bucket's public URL. Nothing is committed to the repo.

Settings (env vars / GitHub secrets and variables):
    SUPABASE_URL          https://<project>.supabase.co
    SUPABASE_BUCKET       bucket name (default "instagram"); must be a public bucket
    SUPABASE_SERVICE_KEY  only needed to upload (igmeta upload)
    MEDIA_BASE_URL        optional override: any public URL laid out as <base>/<slug>/<file>
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

import httpx


class MediaError(RuntimeError):
    pass


class MediaStore:
    def __init__(self, base_url: str, *, upload_url: str | None = None, key: str | None = None,
                 client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.upload_url = upload_url.rstrip("/") if upload_url else None
        self.key = key
        self.http = client or httpx.Client(timeout=60, follow_redirects=True)

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> "MediaStore | None":
        base = os.environ.get("MEDIA_BASE_URL")
        sb = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        bucket = os.environ.get("SUPABASE_BUCKET") or "instagram"
        upload = f"{sb}/storage/v1/object/{bucket}" if sb else None
        if not base and sb:
            base = f"{sb}/storage/v1/object/public/{bucket}"
        if not base:
            return None
        return cls(base, upload_url=upload, key=os.environ.get("SUPABASE_SERVICE_KEY"),
                   client=client)

    def url(self, slug: str, name: str) -> str:
        return f"{self.base_url}/{quote(slug)}/{quote(name)}"

    def fetch(self, slug: str, name: str) -> bytes | None:
        """The file's bytes, or None if it isn't in storage."""
        r = self.http.get(self.url(slug, name))
        if r.status_code in (400, 404):
            return None
        if r.status_code >= 400:
            raise MediaError(f"{name}: storage returned {r.status_code}")
        return r.content

    def size(self, slug: str, name: str) -> int | None:
        r = self.http.head(self.url(slug, name))
        if r.status_code in (400, 404):
            return None
        if r.status_code >= 400:
            raise MediaError(f"{name}: storage returned {r.status_code}")
        return int(r.headers.get("content-length") or 0)

    def upload(self, slug: str, path: Path) -> str:
        if not (self.upload_url and self.key):
            raise MediaError("uploading needs SUPABASE_URL and SUPABASE_SERVICE_KEY")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        r = self.http.post(
            f"{self.upload_url}/{quote(slug)}/{quote(path.name)}",
            content=path.read_bytes(),
            headers={"Authorization": f"Bearer {self.key}", "apikey": self.key,
                     "Content-Type": ctype, "x-upsert": "true"},
        )
        if r.status_code >= 400:
            raise MediaError(f"{path.name}: upload failed ({r.status_code}) {r.text[:200]}")
        return self.url(slug, path.name)
