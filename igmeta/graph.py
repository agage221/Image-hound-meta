"""Thin client for the Instagram API with Instagram Login (graph.instagram.com).

Only the calls this project needs: publishing containers, the publishing
limit, recent media, insights and long-lived token refresh.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

HOST = "https://graph.instagram.com"
DEFAULT_VERSION = os.environ.get("IG_API_VERSION", "v25.0")


class GraphError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: int | None = None,
                 subcode: int | None = None):
        super().__init__(message)
        self.status, self.code, self.subcode = status, code, subcode


class Graph:
    def __init__(self, token: str, user_id: str | None = None, *, version: str = DEFAULT_VERSION,
                 client: httpx.Client | None = None, sleep=time.sleep):
        if not token:
            raise GraphError("No access token. Set the account's token secret.")
        self.token = token
        self.version = version
        self.http = client or httpx.Client(timeout=60)
        self.sleep = sleep
        self._user_id = user_id

    # -- plumbing -----------------------------------------------------------
    def _url(self, path: str, versioned: bool = True) -> str:
        path = path.lstrip("/")
        return f"{HOST}/{self.version}/{path}" if versioned else f"{HOST}/{path}"

    def _check(self, r: httpx.Response) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
            raise GraphError(msg, r.status_code, err.get("code"), err.get("error_subcode"))
        return body

    def get(self, path: str, versioned: bool = True, **params) -> dict[str, Any]:
        params["access_token"] = self.token
        return self._check(self.http.get(self._url(path, versioned), params=params))

    def post(self, path: str, **data) -> dict[str, Any]:
        data = {k: v for k, v in data.items() if v is not None}
        data["access_token"] = self.token
        return self._check(self.http.post(self._url(path), data=data))

    # -- account ------------------------------------------------------------
    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = str(self.me()["user_id"])
        return self._user_id

    def me(self) -> dict[str, Any]:
        return self.get("me", fields="user_id,username,account_type,followers_count,media_count")

    def publishing_limit(self) -> dict[str, Any]:
        body = self.get(f"{self.user_id}/content_publishing_limit", fields="config,quota_usage")
        data = (body.get("data") or [{}])[0]
        return {"used": data.get("quota_usage", 0),
                "total": (data.get("config") or {}).get("quota_total", 100)}

    def recent_media(self, limit: int = 25) -> list[dict[str, Any]]:
        fields = ("id,caption,media_type,media_product_type,timestamp,permalink,"
                  "like_count,comments_count")
        return self.get(f"{self.user_id}/media", fields=fields, limit=limit).get("data", [])

    def stories(self) -> list[dict[str, Any]]:
        return self.get(f"{self.user_id}/stories",
                        fields="id,media_type,timestamp,permalink").get("data", [])

    # -- publishing ---------------------------------------------------------
    def create_container(self, **params) -> str:
        return str(self.post(f"{self.user_id}/media", **params)["id"])

    def wait_until_ready(self, container_id: str, timeout: float = 300, every: float = 5) -> None:
        waited = 0.0
        while True:
            body = self.get(container_id, fields="status_code,status")
            code = body.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise GraphError(f"Container {container_id} {code}: {body.get('status', '')}")
            if waited >= timeout:
                raise GraphError(f"Container {container_id} not ready after {timeout:.0f}s ({code})")
            self.sleep(every)
            waited += every

    def publish_container(self, container_id: str) -> str:
        return str(self.post(f"{self.user_id}/media_publish", creation_id=container_id)["id"])

    def media(self, media_id: str) -> dict[str, Any]:
        return self.get(media_id, fields="id,permalink,timestamp,media_type,media_product_type")

    # -- insights -----------------------------------------------------------
    def insights(self, object_id: str, metrics: list[str], **params) -> dict[str, Any]:
        """Return {metric: value}. Metrics that are rejected for this object are skipped."""
        out: dict[str, Any] = {}
        try:
            out.update(_flatten(self.get(f"{object_id}/insights", metric=",".join(metrics), **params)))
            return out
        except GraphError:
            pass  # one bad metric fails the whole call; retry one at a time
        for m in metrics:
            try:
                out.update(_flatten(self.get(f"{object_id}/insights", metric=m, **params)))
            except GraphError:
                continue
        return out

    # -- tokens -------------------------------------------------------------
    def refresh_token(self) -> dict[str, Any]:
        """Extend a long-lived token by 60 days. The token must be at least 24 hours old."""
        return self.get("refresh_access_token", versioned=False, grant_type="ig_refresh_token")


def _flatten(body: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for item in body.get("data", []):
        name = item.get("name")
        if "total_value" in item:
            out[name] = (item["total_value"] or {}).get("value")
        elif item.get("values"):
            out[name] = item["values"][-1].get("value")
    return out
