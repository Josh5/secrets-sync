from __future__ import annotations

import os
from typing import Dict

from ..models import SecretItem, SourceConfig
from .base import BaseSource


class EnvSource(BaseSource):
    """Pull secrets from environment variables.

    options:
      include_regex: str | list[str] regex patterns to include (default: all)
      exclude_regex: str | list[str] regex patterns to exclude
      keys: list[str] explicit variable names to include
      strip_prefix: str | list[str] to remove from key names when producing secret names
      strip_suffix: str | list[str] to remove from key names when producing secret names
    """

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        o = config.options or {}
        self.keys = set(o.get("keys", []) or [])

    def _match(self, key: str) -> bool:
        if self.keys and key not in self.keys:
            return False
        return self.accepts_name(key)

    async def pull(self) -> Dict[str, SecretItem]:
        items: Dict[str, SecretItem] = {}
        for k, v in os.environ.items():
            if not self._match(k):
                continue
            name = self.transform_name(k)
            items[name] = SecretItem(name=name, value=v, source=self.config.name)
        return items
