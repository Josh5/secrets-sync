from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Dict, List, Optional

from ..models import SecretItem, SourceConfig
from .base import BaseSource, SecretCandidate


class OnePasswordSource(BaseSource):
    """Pull secrets from 1Password using the `op` CLI.

    options:
      vault: required vault name
      tag_filters: list[str] of tags; any match qualifies
      include_regex: optional regex to filter item titles
      service_account_token: optional; if not provided, reads OP_SERVICE_ACCOUNT_TOKEN
      concurrency: optional int controlling parallel fetches (default 8)
    """

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        o = config.options or {}
        self.vault: str = o.get("vault") or ""
        if not self.vault:
            raise ValueError("1Password source requires 'vault' option")
        self.tag_filters: List[str] = self._normalize_tag_list(o.get("tag_filters") or [])
        self._tag_filter_set = set(self.tag_filters)
        self.include_re: Optional[re.Pattern[str]] = None
        if o.get("include_regex"):
            self.include_re = re.compile(str(o.get("include_regex")))
        self.token: Optional[str] = o.get("service_account_token") or os.getenv("OP_SERVICE_ACCOUNT_TOKEN")
        self.concurrency: int = int(o.get("concurrency", 8))

    def _env(self) -> dict:
        env = os.environ.copy()
        if self.token:
            env["OP_SERVICE_ACCOUNT_TOKEN"] = self.token
        return env

    def _run(self, args: List[str]) -> str:
        return subprocess.check_output(args, text=True, env=self._env())

    def _list_items(self) -> List[dict]:
        args = ["op", "item", "list", "--vault", self.vault, "--format", "json"]
        out = self._run(args)
        data = json.loads(out) or []
        items: List[dict] = []
        for it in data:
            title = it.get("title", "")
            tags = self._normalize_tag_list(it.get("tags") or [])
            if self.tag_filters and not any(tag in self._tag_filter_set for tag in tags):
                continue
            if self.include_re and not self.include_re.search(title):
                continue
            items.append(it)
        return items

    def _extract_value(self, item_detail: dict) -> Optional[str]:
        # Prefer a field with id "password" or type "CONCEALED"; else first field with value
        for field in item_detail.get("fields", []) or []:
            if field.get("id") == "password" and field.get("value"):
                return str(field.get("value"))
        for field in item_detail.get("fields", []) or []:
            if field.get("type") == "CONCEALED" and field.get("value"):
                return str(field.get("value"))
        for field in item_detail.get("fields", []) or []:
            if field.get("value"):
                return str(field.get("value"))
        return None

    def _get_item_detail(self, item_id: str) -> dict:
        args = ["op", "item", "get", item_id, "--vault", self.vault, "--format", "json"]
        out = self._run(args)
        return json.loads(out)

    async def pull(self) -> Dict[str, SecretItem]:
        # List matching items
        items = await asyncio.to_thread(self._list_items)

        sem = asyncio.Semaphore(self.concurrency)
        candidates: List[SecretCandidate] = []

        async def fetch_one(it: dict) -> Optional[SecretCandidate]:
            async with sem:
                detail = await asyncio.to_thread(self._get_item_detail, it.get("id"))
                title = detail.get("title") or it.get("title")
                value = self._extract_value(detail)
                if title and value is not None:
                    tags = self._normalize_tag_list(detail.get("tags") or it.get("tags") or [])
                    return SecretCandidate(name=str(title), value=str(value), tags=tags)
                return None

        for candidate in await asyncio.gather(*(fetch_one(it) for it in items)):
            if candidate:
                candidates.append(candidate)
        return self._select_candidate_values(candidates, self.tag_filters)
