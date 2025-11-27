from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import SecretItem, SourceConfig


@dataclass
class SecretCandidate:
    name: str
    value: str
    tags: Sequence[str]


class BaseSource:
    def __init__(self, config: SourceConfig):
        self.config = config
        # Backward compat: if no name provided, use type
        if not getattr(self.config, "name", None):
            self.config.name = self.config.type
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")

    async def pull(self) -> Dict[str, SecretItem]:
        raise NotImplementedError

    def _normalize_tag_list(self, raw_tags: Optional[Iterable[object]]) -> List[str]:
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

    def _select_candidate_values(
        self, candidates: Iterable[SecretCandidate], tag_filters: Sequence[str]
    ) -> Dict[str, SecretItem]:
        priority = {tag: idx for idx, tag in enumerate(tag_filters)}
        selections: Dict[str, Tuple[int, Optional[str]]] = {}
        results: Dict[str, SecretItem] = {}
        for candidate in candidates:
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
            previous = selections.get(candidate.name)
            if previous is not None:
                prev_priority, prev_tag = previous
                if match_priority < prev_priority:
                    continue
                if match_priority == prev_priority and match_tag and prev_tag == match_tag:
                    self.logger.warning(
                        "Multiple secrets discovered for key '%s' with the tag '%s'; using last value",
                        candidate.name,
                        match_tag,
                    )
            results[candidate.name] = SecretItem(
                name=candidate.name, value=candidate.value, source=self.config.name
            )
            selections[candidate.name] = (match_priority, match_tag)
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
    raise ValueError(f"Unknown source type: {cfg.type}")
