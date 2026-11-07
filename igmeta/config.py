"""accounts.yml: which Instagram accounts this repo can post to, and where their tokens live."""
from __future__ import annotations

import os
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
