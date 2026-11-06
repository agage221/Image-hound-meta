"""accounts.yml: which Instagram accounts this repo can post to, and where their tokens live."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Account:
    name: str
    token_secret: str
    user_id: str | None = None

    @property
    def token(self) -> str:
        return os.environ.get(self.token_secret, "")


@dataclass
class Config:
    root: Path
    accounts: dict[str, Account]
    default: str | None

    def account(self, name: str) -> Account:
        if name not in self.accounts:
            raise KeyError(f"account {name!r} is not in accounts.yml")
        return self.accounts[name]


def load_config(root: Path) -> Config:
    path = root / "accounts.yml"
    spec = yaml.safe_load(path.read_text()) if path.exists() else {}
    spec = spec or {}
    accounts = {}
    for name, a in (spec.get("accounts") or {}).items():
        a = a or {}
        accounts[name] = Account(
            name=name,
            token_secret=a.get("token_secret") or f"IG_TOKEN_{name.upper().replace('-', '_')}",
            user_id=str(a["user_id"]) if a.get("user_id") else None,
        )
    default = spec.get("default") or (next(iter(accounts)) if len(accounts) == 1 else None)
    return Config(root=root, accounts=accounts, default=default)


def media_url(root: Path, file: Path) -> str:
    """A public URL Meta can fetch the file from.

    MEDIA_BASE_URL wins if set (e.g. a public storage bucket that mirrors posts/).
    Otherwise use raw.githubusercontent.com pinned to the current commit, which works
    because this repo is public and the media is committed before it is published.
    """
    rel = file.resolve().relative_to(root.resolve()).as_posix()
    base = os.environ.get("MEDIA_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/{rel}"
    repo = os.environ.get("GITHUB_REPOSITORY") or _repo_from_remote(root)
    sha = os.environ.get("MEDIA_SHA") or subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{rel}"


def _repo_from_remote(root: Path) -> str:
    url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    url = url.removesuffix(".git")
    return "/".join(url.replace(":", "/").split("/")[-2:])
