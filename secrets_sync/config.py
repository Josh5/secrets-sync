from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

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


def _resolve_vars(env_vars: Dict[str, str], cfg_vars: Dict[str, Any]) -> Dict[str, str]:
    """Resolve config variables recursively, using environment variables as leaves."""
    raw_cfg_vars = {str(k): str(v) for k, v in cfg_vars.items()}
    resolved_cfg_vars: Dict[str, str] = {}
    resolving: List[str] = []

    def resolve(key: str) -> str:
        if key in resolved_cfg_vars:
            return resolved_cfg_vars[key]
        if key not in raw_cfg_vars:
            if key not in env_vars:
                raise ValueError(f"Missing variable '{key}' for template interpolation")
            return str(env_vars[key])
        if key in resolving:
            cycle_start = resolving.index(key)
            cycle = resolving[cycle_start:] + [key]
            raise ValueError(f"Cyclic variable reference: {' -> '.join(cycle)}")

        resolving.append(key)
        try:
            value = _VAR_PATTERN.sub(lambda match: resolve(match.group(1)), raw_cfg_vars[key])
        finally:
            resolving.pop()
        resolved_cfg_vars[key] = value
        return value

    for key in raw_cfg_vars:
        resolve(key)

    return {**env_vars, **resolved_cfg_vars}


def _types_by_name(items: Any) -> Dict[str, str]:
    return {
        str(item["name"]): str(item.get("type") or "").lower()
        for item in _coerce_list(items)
        if isinstance(item, dict) and item.get("name") is not None
    }


def _effective_type(item: Dict[str, Any], types_by_name: Dict[str, str]) -> str:
    name = item.get("name")
    if name is not None and str(name) in types_by_name:
        return types_by_name[str(name)]
    return str(item.get("type") or "").lower()


def _resolve_document_paths(
    data: Dict[str, Any],
    base_dir: str,
    source_types: Dict[str, str],
    sink_types: Dict[str, str],
) -> None:
    """Resolve local paths while their declaring config file is still known."""
    src_list = data.get("secrets_sources") or data.get("sources")
    for src in _coerce_list(src_list):
        if not isinstance(src, dict):
            continue
        if _effective_type(src, source_types) != "yaml":
            continue
        opts = src.get("options")
        if not isinstance(opts, dict):
            continue
        files = opts.get("files")
        single = opts.get("file")
        if single and not files:
            files = [single]
        if isinstance(files, list):
            opts["files"] = [
                os.path.normpath(os.path.join(base_dir, path))
                if isinstance(path, str) and not os.path.isabs(path)
                else path
                for path in files
            ]
            if "file" in opts:
                del opts["file"]

    for sink in _coerce_list(data.get("sinks")):
        if not isinstance(sink, dict):
            continue
        if _effective_type(sink, sink_types) not in ("dotenv", "dir_files"):
            continue
        opts = sink.get("options")
        if not isinstance(opts, dict):
            continue
        raw_path = opts.get("path") or opts.get("file") or opts.get("dir") or opts.get("directory")
        if isinstance(raw_path, str) and raw_path and not os.path.isabs(raw_path):
            opts["path"] = os.path.normpath(os.path.join(base_dir, raw_path))
            for key in ("file", "dir", "directory"):
                if key in opts:
                    del opts[key]


def load_config_from_files(paths: List[str]) -> AppConfig:
    if not paths:
        raise ValueError("At least one config file must be provided")
    documents: List[Tuple[Dict[str, Any], str]] = []
    raw_merged: Dict[str, Any] = {}
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config file '{p}' must contain a mapping at the root")
        base_dir = os.path.dirname(os.path.abspath(p))
        documents.append((data, base_dir))
        raw_merged = _deep_merge(raw_merged, data)

    # Build vars map: environment first, then config vars override env
    env_vars = dict(os.environ)
    cfg_vars = raw_merged.get("vars", {}) or {}
    if not isinstance(cfg_vars, dict):
        raise ValueError("'vars' must be a mapping of key: value")
    vars_map = _resolve_vars(env_vars, cfg_vars)

    # Interpolate documents before resolving paths, while retaining the directory
    # of the document that declared each path.
    interpolated_documents = [
        (_interpolate(data, vars_map), base_dir) for data, base_dir in documents
    ]
    interpolated_merged: Dict[str, Any] = {}
    for data, _ in interpolated_documents:
        interpolated_merged = _deep_merge(interpolated_merged, data)

    source_types = _types_by_name(
        interpolated_merged.get("secrets_sources") or interpolated_merged.get("sources")
    )
    sink_types = _types_by_name(interpolated_merged.get("sinks"))

    merged: Dict[str, Any] = {}
    for data, base_dir in interpolated_documents:
        _resolve_document_paths(data, base_dir, source_types, sink_types)
        merged = _deep_merge(merged, data)

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
    valid_source_names = {
        (rs.get("name") or rs.get("type")) for rs in raw_sources if isinstance(rs, dict)
    }

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

    return AppConfig(
        aws=aws,
        sources=sources,
        sinks=sinks,
        vars={k: str(v) for k, v in vars_map.items()},
    )
