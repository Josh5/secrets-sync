from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Dict, List
import json

import boto3

from .config import load_config_from_files
from .models import SecretItem
from .sources.base import build_source
from .sinks.base import build_sink
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def collect_secrets_from_cfg(cfg) -> Dict[str, SecretItem]:

    # Configure AWS session from cfg.aws or env (GitLab CI vars supported)
    session_kwargs = {}
    if cfg.aws.region:
        session_kwargs["region_name"] = cfg.aws.region
    if cfg.aws.profile:
        session_kwargs["profile_name"] = cfg.aws.profile
    if session_kwargs:
        boto3.setup_default_session(**session_kwargs)

    # Parallel pull from sources
    sources = [build_source(s) for s in cfg.sources]
    results = await asyncio.gather(*[s.pull() for s in sources])

    merged: Dict[str, SecretItem] = {}
    for d in results:
        for k, item in (d or {}).items():
            merged[k] = item  # last-in wins
    return merged


async def push_to_sinks_from_cfg(
    cfg,
    items: Dict[str, SecretItem],
    *,
    print_sync_details: bool = False,
    detail_value_snapshots: bool = False,
) -> None:
    sinks = [
        build_sink(
            s,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
        for s in cfg.sinks
    ]
    # Route by sink.sources if provided
    tasks = []
    for sink_obj, sink_cfg in zip(sinks, cfg.sinks):
        if sink_cfg.sources:
            filtered = [i for i in items.values() if i.source in sink_cfg.sources]
        else:
            filtered = list(items.values())
        tasks.append(sink_obj.push_many(filtered))
    await asyncio.gather(*tasks)


def _prefixed_name(sink_cfg, item: SecretItem) -> str:
    t = (sink_cfg.type or "").lower()
    opts = sink_cfg.options or {}
    if t == "ssm":
        prefix = opts.get("prefix") or opts.get("path_prefix") or ""
        if prefix:
            return f"{prefix.rstrip('/')}/{item.name}"
        return item.name
    if t in ("secrets", "secrets_manager", "secretsmanager"):
        prefix = opts.get("prefix") or ""
        if prefix:
            return f"{prefix.rstrip('/')}/{item.name}"
        return item.name
    return item.name


def _print_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            if len(cell) > widths[i]:
                widths[i] = len(cell)
    # Header
    line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(line)
    print(sep)
    # Rows
    for r in rows:
        print(" | ".join((r[i] if r[i] is not None else "").ljust(widths[i]) for i in range(len(headers))))


def print_sink_outputs(cfg, items: Dict[str, SecretItem], fmt: str = "list") -> None:
    for sink_cfg in cfg.sinks:
        t = (sink_cfg.type or "").lower()
        opts = sink_cfg.options or {}
        name = sink_cfg.name or t
        sources = sink_cfg.sources or []
        header_details = []
        if t == "ssm":
            pref = opts.get("prefix") or opts.get("path_prefix") or ""
            if pref:
                header_details.append(f"prefix='{pref}'")
        elif t in ("secrets", "secrets_manager", "secretsmanager"):
            pref = opts.get("prefix") or ""
            if pref:
                header_details.append(f"prefix='{pref}'")
        if sources:
            header_details.append(f"sources={','.join(sources)}")
        header = f"--- Sink: {name} [{t}]"
        if header_details:
            header += " " + " ".join(header_details)

        # Filter by configured sources
        selected = [i for i in items.values() if not sources or i.source in sources]
        if fmt == "none":
            # Don't print anything
            pass
        elif fmt == "json":
            # Collect JSON objects; print once after loop
            pass
        else:
            print(header)
            if selected:
                if fmt == "table":
                    headers = ["Name", "Value"]
                    rows: List[List[str]] = []
                    for it in selected:
                        full_name = _prefixed_name(sink_cfg, it)
                        rows.append([full_name, it.value])
                    _print_table(headers, rows)
                else:  # list
                    for it in selected:
                        full_name = _prefixed_name(sink_cfg, it)
                        print(f"{full_name}={it.value}")
            else:
                print("(no items)")
            print()

    if fmt == "json":
        out = []
        for sink_cfg in cfg.sinks:
            t = (sink_cfg.type or "").lower()
            opts = sink_cfg.options or {}
            name = sink_cfg.name or t
            sources = sink_cfg.sources or []
            prefix = ""
            if t == "ssm":
                prefix = opts.get("prefix") or opts.get("path_prefix") or ""
            elif t in ("secrets", "secrets_manager", "secretsmanager"):
                prefix = opts.get("prefix") or ""
            selected = [i for i in items.values() if not sources or i.source in sources]
            items_list = [
                {
                    "name": _prefixed_name(sink_cfg, it),
                    "value": it.value,
                    "description": it.description or "",
                }
                for it in selected
            ]
            out.append(
                {
                    "name": name,
                    "type": t,
                    "prefix": prefix,
                    "sources": sources,
                    "items": items_list,
                }
            )
        print(json.dumps(out, ensure_ascii=False, indent=2))


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync secrets to AWS SSM / Secrets Manager")
    p.add_argument("--file", "-f", action="append", dest="files",
                   help="YAML config file(s) to merge; later overrides earlier", default=[])
    p.add_argument("--print-values", action="store_true", help="Print gathered secrets to STDOUT")
    p.add_argument(
        "--print-format",
        choices=["none", "list", "table", "json"],
        default="none",
        help="Output format for --print-values",
    )
    p.add_argument("--dry-run", action="store_true", help="Collect and optionally print, but don't push")
    p.add_argument(
        "--print-sync-details",
        action="store_true",
        help="Print per-item results while pushing to sinks",
    )
    return p.parse_args(argv)


async def _main_async() -> int:
    setup_logging()
    args = parse_args()

    # Load config
    cfg = None
    try:
        if not args.files:
            logger.error("No config files provided. Use one or more -f/--file options.")
            return 2
        cfg = load_config_from_files(args.files)
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return 2

    logger.info("Collecting secrets from sources…")
    items = await collect_secrets_from_cfg(cfg)
    logger.info("Collected %d items", len(items))

    if getattr(args, "print_values", False):
        # Print per-sink preview including prefixes and routing
        print_sink_outputs(cfg, items, fmt=args.print_format)

    if args.dry_run:
        logger.info("Dry run enabled; not pushing to AWS")
        return 0

    if not items:
        logger.info("No items to push")
        return 0

    logger.info("Pushing to sinks…")
    include_value_details = bool(args.print_sync_details and args.print_values)
    await push_to_sinks_from_cfg(
        cfg,
        items,
        print_sync_details=args.print_sync_details,
        detail_value_snapshots=include_value_details,
    )
    logger.info("Push complete")
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_main_async()))
    except KeyboardInterrupt:
        raise SystemExit(130)
