from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import yaml

from .models import AppConfig, AwsConfig, SinkConfig, SourceConfig


def _coerce_list(val: Any) -> List[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _deep_merge(a: Any, b: Any) -> Any:
    """Deep-merge two YAML-loaded structures. Lists of dicts with 'name' merge by name."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _deep_merge(out.get(k), v)
        return out
    if isinstance(a, list) and isinstance(b, list):
        # If list items are dicts with a 'name', merge by name; else override entirely with b
        if all(isinstance(i, dict) and "name" in i for i in a) and all(
            isinstance(i, dict) and "name" in i for i in b
        ):
            by_name: Dict[str, Dict[str, Any]] = {str(i["name"]): dict(i) for i in a}
            for item in b:
                name = str(item.get("name"))
                if name in by_name:
                    by_name[name] = _deep_merge(by_name[name], item)
                else:
                    by_name[name] = dict(item)
            return list(by_name.values())
        return list(b)
    # For anything else, prefer b if provided, else a
    return b if b is not None else a


_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _interpolate(obj: Any, vars_map: Dict[str, str]) -> Any:
    """Recursively interpolate {{ VAR }} in strings using vars_map.
    Raises ValueError if a placeholder has no value.
    """
    if isinstance(obj, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in vars_map or vars_map[key] is None:
                raise ValueError(f"Missing variable '{key}' for template interpolation")
            return str(vars_map[key])

        return _VAR_PATTERN.sub(replace, obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v, vars_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(v, vars_map) for v in obj]
    return obj


def load_config_from_files(paths: List[str]) -> AppConfig:
    if not paths:
        raise ValueError("At least one config file must be provided")
    merged: Dict[str, Any] = {}
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Resolve relative file paths in YAML sources relative to this config file
        base_dir = os.path.dirname(os.path.abspath(p))
        src_list = data.get("secrets_sources") or data.get("sources")
        if isinstance(src_list, list):
            for src in src_list:
                if not isinstance(src, dict):
                    continue
                t = (src.get("type") or "").lower()
                if t != "yaml":
                    continue
                opts = src.get("options")
                if not isinstance(opts, dict):
                    continue
                files = opts.get("files")
                single = opts.get("file")
                if single and not files:
                    files = [single]
                if isinstance(files, list):
                    resolved = []
                    for fp in files:
                        if isinstance(fp, str) and not os.path.isabs(fp):
                            resolved.append(os.path.normpath(os.path.join(base_dir, fp)))
                        else:
                            resolved.append(fp)
                    opts["files"] = resolved
                    if "file" in opts:
                        del opts["file"]

        merged = _deep_merge(merged, data)

    # Build vars map: environment first, then config vars override env
    env_vars = dict(os.environ)
    cfg_vars = merged.get("vars", {}) or {}
    if not isinstance(cfg_vars, dict):
        raise ValueError("'vars' must be a mapping of key: value")
    vars_map: Dict[str, str] = {**env_vars, **{k: str(v) for k, v in cfg_vars.items()}}

    # Interpolate placeholders across the entire structure
    merged = _interpolate(merged, vars_map)

    aws_data = merged.get("aws", {}) or {}
    aws = AwsConfig(
        region=aws_data.get("region") or os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION"),
        profile=aws_data.get("profile") or os.getenv("AWS_PROFILE"),
    )

    sources = []
    raw_sources = _coerce_list(merged.get("secrets_sources") or merged.get("sources"))
    for s in raw_sources:
        if not s:
            continue
        sources.append(
            SourceConfig(
                name=s.get("name"),
                type=s.get("type"),
                options=s.get("options", {}) or {},
                vars=dict(vars_map),
            )
        )

    # Validate sink routing references existing source names
    valid_source_names = {(rs.get("name") or rs.get("type")) for rs in raw_sources if isinstance(rs, dict)}

    sinks = []
    for s in _coerce_list(merged.get("sinks")):
        if not s:
            continue
        src_filter = [str(x) for x in _coerce_list(s.get("sources"))]
        for ref in src_filter:
            if ref not in valid_source_names:
                sink_label = s.get("name") or s.get("type") or "<unnamed-sink>"
                raise ValueError(f"Sink '{sink_label}' references unknown source '{ref}'")
        sinks.append(
            SinkConfig(
                name=s.get("name"),
                type=s.get("type"),
                options=s.get("options", {}) or {},
                sources=src_filter,
            )
        )

    return AppConfig(aws=aws, sources=sources, sinks=sinks, vars={k: str(v) for k, v in vars_map.items()})
