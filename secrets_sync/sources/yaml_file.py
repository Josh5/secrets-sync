from __future__ import annotations

import os
import json
from typing import Dict, Any, List

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError

from ..models import SecretItem, SourceConfig
from .base import BaseSource


# -- Jinja2 filters
def _from_json_filter(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        raise ValueError("from_json expects a JSON string input")
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"from_json failed to parse value: {exc}") from exc


def _to_json_filter(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"to_json failed to serialize value: {exc}") from exc


# -- Base Jinja2 environment with custom filters
_BASE_ENV = Environment(autoescape=False, undefined=StrictUndefined)
_BASE_ENV.filters.update({
    "from_json": _from_json_filter,
    "to_json": _to_json_filter,
})


# -- Jinja2 function extensions
def _lookup_file(raw_path: Any, *, base_dir: str) -> str:
    if raw_path is None:
        raise ValueError("lookup('file', path) requires a path argument")
    path_str = str(raw_path)
    path = path_str if os.path.isabs(path_str) else os.path.normpath(os.path.join(base_dir, path_str))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise ValueError(f"lookup('file', ...) failed to read '{path}': {exc.strerror}") from exc


def _resolve_template(value: Any, *, base_dir: str, context: Dict[str, str]) -> Any:
    """Render Jinja2 templates inside a string."""
    if not isinstance(value, str) or "{{" not in value:
        return value

    # -- Jinja2 functions
    def lookup(extension: str, *args: Any) -> str:
        extension_name = str(extension)
        if extension_name == "file":
            return _lookup_file(args[0] if args else None, base_dir=base_dir)
        raise ValueError(f"Unsupported lookup extension '{extension_name}'")

    env = _BASE_ENV.overlay()
    env.globals.update({
        "lookup": lookup,
    })

    try:
        template = env.from_string(value)
        return template.render(**(context or {}))
    except TemplateError as exc:
        raise ValueError(f"Failed to render template in YAML source: {exc!s}") from exc


def _items_from_values(values: Any, *, base_dir: str, context: Dict[str, str]) -> Dict[str, SecretItem]:
    if isinstance(values, list):
        out: Dict[str, SecretItem] = {}
        for entry in values:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if name is None or value is None:
                continue
            value = _resolve_template(value, base_dir=base_dir, context=context)
            out[str(name)] = SecretItem(
                name=str(name),
                value=str(value),
                description=entry.get("description"),
            )
        return out
    if isinstance(values, dict):
        # Interpret as a mapping of key->value
        out_map: Dict[str, SecretItem] = {}
        for k, v in values.items():
            out_map[str(k)] = SecretItem(
                name=str(k),
                value=str(_resolve_template(v, base_dir=base_dir, context=context)),
            )
        return out_map
    return {}


def _get_nested_mapping(obj: Any, path: str | None) -> Any:
    """Traverse a dot-delimited path within a nested dict, returning None if any part is missing."""
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if cur is None:
            return None
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


class YamlSource(BaseSource):
    """Pull secrets from a YAML file.

    options:
      file: path to YAML file (required)
      key: dot-path to dict containing secrets (optional)
    """

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        opts = config.options or {}
        files = opts.get("files")
        # Backward compatibility: allow single 'file'
        if not files and opts.get("file"):
            files = [opts.get("file")]
        self.files: List[str] = [str(p) for p in (files or [])]
        self.key = opts.get("key")
        if not self.files:
            raise ValueError("YamlSource requires 'files' (list of paths)")

    async def pull(self) -> Dict[str, SecretItem]:
        merged: Dict[str, SecretItem] = {}
        for path in self.files:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            base_dir = os.path.dirname(path)
            secrets = _get_nested_mapping(data, self.key) if self.key else data
            # Support structures like { values: [ {name, value, description}, ... ] }
            if isinstance(secrets, dict) and "values" in secrets:
                items = _items_from_values(secrets.get("values"), base_dir=base_dir, context=self.vars)
            else:
                items = _items_from_values(secrets, base_dir=base_dir, context=self.vars)
            for name, it in (items or {}).items():
                it.source = self.config.name
                merged[name] = it  # later files override earlier
        return merged
