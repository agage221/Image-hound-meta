"""igmeta: queue, publish and measure Instagram posts through Meta's API.

    igmeta check                 validate every queued post
    igmeta queue                 list what's queued and when
    igmeta prepare               convert PNG/WebP media to JPEG
    igmeta publish [--dry-run]   publish everything that's due
    igmeta insights --out DIR    append today's insights snapshots
    igmeta limit                 show today's publishing quota use
    igmeta whoami                confirm each account's token works
    igmeta refresh-token NAME    print a refreshed long-lived token
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import insights as insights_mod
from .config import load_config, media_url
from .graph import Graph, GraphError
from .publish import publish_post
from .queue import DEFAULT_TZ, load_queue, prepare, validate

LOCAL = ZoneInfo(DEFAULT_TZ)


def _local(dt: datetime | None) -> str:
    return dt.astimezone(LOCAL).strftime("%a %b %d %I:%M %p") if dt else "unscheduled"


def _graph(cfg, name: str) -> Graph:
    acct = cfg.account(name)
    return Graph(acct.token, acct.user_id)


def cmd_check(args, cfg) -> int:
    posts = load_queue(cfg.root, cfg.default)
    bad = 0
    for p in posts:
        if p.published:
            continue
        errors, warnings = validate(p, set(cfg.accounts))
        mark = "x" if errors else "ok"
        print(f"[{mark}] {p.slug}  ({p.type}, {len(p.media)} file(s), {_local(p.publish_at)})")
        for e in errors:
            print(f"      error: {e}")
        for w in warnings:
            print(f"      note:  {w}")
        bad += bool(errors)
    print(f"\n{len(posts)} post(s), {bad} with errors")
    return 1 if bad else 0


def cmd_queue(args, cfg) -> int:
    for p in load_queue(cfg.root, cfg.default):
        state = p.status.get("status", "paused" if p.paused else "queued")
        link = p.status.get("permalink") or ""
        print(f"{_local(p.publish_at):<24} {state:<10} {p.account:<12} {p.type:<9} {p.slug} {link}")
    return 0


def cmd_prepare(args, cfg) -> int:
    for p in load_queue(cfg.root, cfg.default):
        for change in prepare(p):
            print(f"{p.slug}: {change}")
    return 0


def cmd_publish(args, cfg) -> int:
    now = datetime.now(timezone.utc)
    due = [p for p in load_queue(cfg.root, cfg.default) if p.is_due(now)]
    if args.only:
        due = [p for p in due if p.slug == args.only]
    if not due:
        print("Nothing due.")
        return 0
    graphs: dict[str, Graph] = {}
    failed = 0
    for p in due[: args.max]:
        try:
            g = graphs.get(p.account) or graphs.setdefault(p.account, _graph(cfg, p.account))
        except (KeyError, GraphError) as e:
            print(f"[x] {p.slug}: {e}")
            failed += 1
            continue
        result = publish_post(g, p, lambda f: media_url(cfg.root, f), dry_run=args.dry_run)
        state = result.get("status")
        print(f"[{state}] {p.slug} -> {result.get('permalink') or result.get('error') or result.get('urls')}")
        failed += state == "failed"
    return 1 if failed else 0


def cmd_insights(args, cfg) -> int:
    out = Path(args.out)
    names = [args.account] if args.account else list(cfg.accounts)
    for name in names:
        if not cfg.account(name).token:
            print(f"{name}: no token, skipped")
            continue
        summary = insights_mod.pull(_graph(cfg, name), out, name)
        a = summary["account"]
        print(f"{name}: {a.get('followers')} followers, reach {a.get('reach')}, "
              f"{summary['media']} posts and {summary['stories']} stories snapshotted")
    return 0


def cmd_limit(args, cfg) -> int:
    for name in cfg.accounts:
        lim = _graph(cfg, name).publishing_limit()
        print(f"{name}: {lim['used']} of {lim['total']} API posts used in the last 24 hours")
    return 0


def cmd_whoami(args, cfg) -> int:
    ok = True
    for name, acct in cfg.accounts.items():
        if not acct.token:
            print(f"{name}: {acct.token_secret} is not set")
            ok = False
            continue
        try:
            me = Graph(acct.token, acct.user_id).me()
            print(f"{name}: @{me.get('username')} ({me.get('account_type')}), "
                  f"{me.get('followers_count')} followers, user_id {me.get('user_id')}")
        except GraphError as e:
            print(f"{name}: token rejected: {e}")
            ok = False
    return 0 if ok else 1


def cmd_refresh(args, cfg) -> int:
    body = _graph(cfg, args.account).refresh_token()
    days = int(body.get("expires_in", 0)) // 86400
    if args.json:
        print(json.dumps(body))
    else:
        print(f"Refreshed {args.account}; good for {days} more days.", file=sys.stderr)
        print(body["access_token"])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="igmeta", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root (holds accounts.yml and posts/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("queue")
    sub.add_parser("prepare")
    p = sub.add_parser("publish")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", help="publish just this slug, if it's due")
    p.add_argument("--max", type=int, default=5, help="most posts to publish in one run")
    p = sub.add_parser("insights")
    p.add_argument("--out", required=True)
    p.add_argument("--account")
    sub.add_parser("limit")
    sub.add_parser("whoami")
    p = sub.add_parser("refresh-token")
    p.add_argument("account")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(Path(args.root))
    handler = {"check": cmd_check, "queue": cmd_queue, "prepare": cmd_prepare,
               "publish": cmd_publish, "insights": cmd_insights, "limit": cmd_limit,
               "whoami": cmd_whoami, "refresh-token": cmd_refresh}[args.cmd]
    return handler(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
