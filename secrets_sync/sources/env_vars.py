from __future__ import annotations

import os
import re
from typing import Dict, Iterable

from ..models import SecretItem, SourceConfig
from .base import BaseSource


class EnvSource(BaseSource):
    """Pull secrets from environment variables.

    options:
      include: list[str] regex patterns to include (default: all)
      exclude: list[str] regex patterns to exclude
      keys: list[str] explicit variable names to include
      strip_prefix: str to remove from key names when producing secret names
    """

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        o = config.options or {}
        # Support either include: [patterns] or include_regex: 'pattern'
        include_patterns = []
        if "include" in o and isinstance(o["include"], list):
            include_patterns.extend(o.get("include", []) or [])
        if o.get("include_regex"):
            include_patterns.append(o.get("include_regex"))
        self.include = [re.compile(p) for p in include_patterns]
        self.exclude = [re.compile(p) for p in o.get("exclude", [])]
        self.keys = set(o.get("keys", []) or [])
        self.strip_prefix = o.get("strip_prefix") or ""

    def _match(self, key: str) -> bool:
        if self.keys and key not in self.keys:
            return False
        if self.include and not any(r.search(key) for r in self.include):
            return False
        if self.exclude and any(r.search(key) for r in self.exclude):
            return False
        return True

    async def pull(self) -> Dict[str, SecretItem]:
        items: Dict[str, SecretItem] = {}
        for k, v in os.environ.items():
            if not self._match(k):
                continue
            name = k
            if self.strip_prefix and name.startswith(self.strip_prefix):
                name = name[len(self.strip_prefix):]
            items[name] = SecretItem(name=name, value=v, source=self.config.name)
        return items
