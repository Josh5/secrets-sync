from __future__ import annotations

from typing import Dict, Any, Iterable, List
import yaml

from ..models import SecretItem, SourceConfig
from .base import BaseSource


def _descend(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if cur is None:
            return None
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _items_from_values(values: Any) -> Dict[str, SecretItem]:
    if isinstance(values, list):
        out: Dict[str, SecretItem] = {}
        for entry in values:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if name is None or value is None:
                continue
            out[str(name)] = SecretItem(
                name=str(name),
                value=str(value),
                description=entry.get("description"),
            )
        return out
    if isinstance(values, dict):
        # Interpret as a mapping of key->value
        return {str(k): SecretItem(name=str(k), value=str(v)) for k, v in values.items()}
    return {}


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
            secrets = _descend(data, self.key) if self.key else data
            # Support structures like { values: [ {name, value, description}, ... ] }
            if isinstance(secrets, dict) and "values" in secrets:
                items = _items_from_values(secrets.get("values"))
            else:
                items = _items_from_values(secrets)
            for name, it in (items or {}).items():
                it.source = self.config.name
                merged[name] = it  # later files override earlier
        return merged
