from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import SecretItem, SourceConfig


@dataclass
class SecretCandidate:
    name: str
    value: str
    tags: Sequence[str]


def _clean_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_string_list(*values: object) -> List[str]:
    items: List[str] = []
    for value in values:
        if value is None:
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            text = _clean_str(entry)
            if text:
                items.append(text)
    return items


def _compile_regex_list(*values: object) -> List[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in _read_string_list(*values)]


class BaseSource:
    def __init__(self, config: SourceConfig):
        self.config = config
        # Backward compat: if no name provided, use type
        if not getattr(self.config, "name", None):
            self.config.name = self.config.type
        self.vars = dict(getattr(self.config, "vars", {}) or {})
        self.logger = logging.getLogger(
            f"{self.__class__.__module__}.{self.__class__.__name__}"
        )
        options = self.config.options or {}
        self.include_res = _compile_regex_list(
            options.get("include_regex"), options.get("include")
        )
        self.exclude_res = _compile_regex_list(options.get("exclude_regex"))
        self.strip_prefixes = _read_string_list(
            options.get("strip_prefixes"), options.get("strip_prefix")
        )
        self.strip_suffixes = _read_string_list(
            options.get("strip_suffixes"), options.get("strip_suffix")
        )

    async def pull(self) -> Dict[str, SecretItem]:
        raise NotImplementedError

    def _read_tag_list(self, raw_tags: Optional[Iterable[object]]) -> List[str]:
        if not raw_tags:
            return []
        result: List[str] = []
        for tag in raw_tags:
            if tag is None:
                continue
            tag_text = str(tag).strip()
            if tag_text:
                result.append(tag_text)
        return result

    def accepts_name(self, name: str) -> bool:
        if self.include_res and not any(
            pattern.search(name) for pattern in self.include_res
        ):
            return False
        return not any(pattern.search(name) for pattern in self.exclude_res)

    def transform_name(self, name: str) -> str:
        transformed = name
        for prefix in self.strip_prefixes:
            if transformed.startswith(prefix):
                transformed = transformed[len(prefix) :]
        for suffix in self.strip_suffixes:
            if transformed.endswith(suffix):
                transformed = transformed[: -len(suffix)]
        return transformed

    def _select_candidate_values(
        self, candidates: Iterable[SecretCandidate], tag_filters: Sequence[str]
    ) -> Dict[str, SecretItem]:
        priority = {tag: idx for idx, tag in enumerate(tag_filters)}
        selections: Dict[str, Tuple[int, Optional[str]]] = {}
        results: Dict[str, SecretItem] = {}
        for candidate in candidates:
            emitted_name = self.transform_name(candidate.name)
            match_tag: Optional[str] = None
            match_priority = -1
            for tag in candidate.tags:
                if tag not in priority:
                    continue
                tag_priority = priority[tag]
                if tag_priority >= match_priority:
                    match_priority = tag_priority
                    match_tag = tag
            if tag_filters and match_tag is None:
                continue
            previous = selections.get(emitted_name)
            if previous is not None:
                prev_priority, prev_tag = previous
                if match_priority < prev_priority:
                    continue
                if (
                    match_priority == prev_priority
                    and match_tag
                    and prev_tag == match_tag
                ):
                    self.logger.warning(
                        "Multiple secrets discovered for key '%s' with the tag '%s'; using last value",
                        emitted_name,
                        match_tag,
                    )
            results[emitted_name] = SecretItem(
                name=emitted_name, value=candidate.value, source=self.config.name
            )
            selections[emitted_name] = (match_priority, match_tag)
        return results


def build_source(cfg: SourceConfig) -> BaseSource:
    t = (cfg.type or "").lower()
    if t == "env":
        from .env_vars import EnvSource

        return EnvSource(cfg)
    if t == "yaml":
        from .yaml_file import YamlSource

        return YamlSource(cfg)
    if t in ("1password", "onepassword", "op"):
        from .onepassword import OnePasswordSource

        return OnePasswordSource(cfg)
    if t in ("keeper",):
        from .keeper import KeeperSource

        return KeeperSource(cfg)
    if t in ("infisical",):
        from .infisical import InfisicalSource

        return InfisicalSource(cfg)
    raise ValueError(f"Unknown source type: {cfg.type}")
