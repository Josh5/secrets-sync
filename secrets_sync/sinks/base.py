from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence

from ..models import SecretItem, SinkConfig
from ..utils.logging import LevelColorFormatter, bcolours

_detail_logger = logging.getLogger("secrets_sync.sync_details")


def _clean_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_pattern_values(value: object) -> List[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    patterns: List[str] = []
    for raw in values:
        text = _clean_str(raw)
        if text:
            patterns.append(text)
    return patterns


def _compile_regex_list(*values: object) -> List[re.Pattern[str]]:
    patterns: List[re.Pattern[str]] = []
    for value in values:
        for pattern in _read_pattern_values(value):
            patterns.append(re.compile(pattern))
    return patterns


def sink_item_matches_name(options: Dict[str, object] | None, item_name: str) -> bool:
    opts = options or {}
    include_res = _compile_regex_list(opts.get("include_regex"))
    exclude_res = _compile_regex_list(opts.get("exclude_regex"))
    if include_res and not any(pattern.search(item_name) for pattern in include_res):
        return False
    return not any(pattern.search(item_name) for pattern in exclude_res)


def _read_name_transforms(
    options: Dict[str, object] | None, singular: str
) -> List[str]:
    opts = options or {}
    return _read_pattern_values(opts.get(f"{singular}es")) + _read_pattern_values(
        opts.get(singular)
    )


def transform_sink_item_name(options: Dict[str, object] | None, item_name: str) -> str:
    transformed = item_name
    for prefix in _read_name_transforms(options, "strip_prefix"):
        if transformed.startswith(prefix):
            transformed = transformed[len(prefix) :]
    for suffix in _read_name_transforms(options, "strip_suffix"):
        if transformed.endswith(suffix):
            transformed = transformed[: -len(suffix)]
    return transformed


def select_sink_items(cfg: SinkConfig, items: Sequence[SecretItem]) -> List[SecretItem]:
    selected = [item for item in items if not cfg.sources or item.source in cfg.sources]
    return [item for item in selected if sink_item_matches_name(cfg.options, item.name)]


class BaseSink:
    def __init__(
        self,
        config: SinkConfig,
        *,
        print_sync_details: bool = False,
        detail_value_snapshots: bool = False,
    ):
        self.config = config
        self._print_sync_details = print_sync_details
        self._detail_value_snapshots = bool(
            print_sync_details and detail_value_snapshots
        )
        options = config.options or {}
        self.include_res = _compile_regex_list(options.get("include_regex"))
        self.exclude_res = _compile_regex_list(options.get("exclude_regex"))
        self.strip_prefixes = _read_name_transforms(options, "strip_prefix")
        self.strip_suffixes = _read_name_transforms(options, "strip_suffix")

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        raise NotImplementedError

    @property
    def sink_label(self) -> str:
        return self.config.name or self.__class__.__name__

    @property
    def detail_logging_enabled(self) -> bool:
        return self._print_sync_details

    def accepts_item(self, item: SecretItem) -> bool:
        if self.include_res and not any(
            pattern.search(item.name) for pattern in self.include_res
        ):
            return False
        return not any(pattern.search(item.name) for pattern in self.exclude_res)

    def select_items(self, items: Iterable[SecretItem]) -> List[SecretItem]:
        return [item for item in items if self.accepts_item(item)]

    def transform_name(self, name: str) -> str:
        transformed = name
        for prefix in self.strip_prefixes:
            if transformed.startswith(prefix):
                transformed = transformed[len(prefix) :]
        for suffix in self.strip_suffixes:
            if transformed.endswith(suffix):
                transformed = transformed[: -len(suffix)]
        return transformed

    def log_sync_success(
        self,
        item_name: str,
        action: str,
        *,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
    ) -> None:
        if not self._print_sync_details:
            return
        detail = self._format_action_detail(action, old_value, new_value)
        detail = self._apply_action_colour(detail, action, level=logging.INFO)
        _detail_logger.info(
            "[%s] %s -> succeeded (%s)", self.sink_label, item_name, detail
        )

    def log_sync_failure(
        self,
        item_name: str,
        action: str,
        error: Optional[Exception] = None,
        *,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
    ) -> None:
        if not self._print_sync_details:
            return
        detail = self._format_action_detail(action, old_value, new_value)
        detail = self._apply_action_colour(detail, action, level=logging.ERROR)
        if error:
            _detail_logger.error(
                "[%s] %s -> failed (%s): %s", self.sink_label, item_name, detail, error
            )
        else:
            _detail_logger.error(
                "[%s] %s -> failed (%s)", self.sink_label, item_name, detail
            )

    def _format_action_detail(
        self,
        action: str,
        old_value: Optional[str],
        new_value: Optional[str],
    ) -> str:
        if not self._detail_value_snapshots:
            return action

        def _fmt(value: Optional[str]) -> str:
            if value is None:
                return "''"
            return repr(value)

        if action == "created":
            return f"{action} {_fmt(new_value)}"
        if action == "unchanged":
            return f"{action} {_fmt(old_value)}"
        if action in ("updated", "changed"):
            return f"changed {_fmt(old_value)} -> {_fmt(new_value)}"
        return action

    def _log_level_colour(self, level: int) -> str:
        return LevelColorFormatter.COLOR_MAP.get(level, bcolours.HIGHINTENSITYWHITE)

    def _action_colour(self, action: str) -> Optional[str]:
        if action == "created":
            return bcolours.OKGREEN
        if action in ("updated", "changed"):
            return bcolours.WARNING
        return None

    def _apply_action_colour(self, detail: str, action: str, *, level: int) -> str:
        action_colour = self._action_colour(action)
        if not action_colour:
            return detail
        base_colour = self._log_level_colour(level)
        if detail.startswith(action):
            return f"{action_colour}{action}{base_colour}{detail[len(action) :]}"
        return detail.replace(action, f"{action_colour}{action}{base_colour}", 1)


def build_sink(
    cfg: SinkConfig,
    *,
    print_sync_details: bool = False,
    detail_value_snapshots: bool = False,
):
    t = (cfg.type or "").lower()
    if t == "ssm":
        from .aws_ssm import SsmSink

        return SsmSink(
            cfg,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
    if t in ("secrets", "secrets_manager", "secretsmanager"):
        from .aws_secrets_manager import SecretsManagerSink

        return SecretsManagerSink(
            cfg,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
    if t == "infisical":
        from .infisical import InfisicalSink

        return InfisicalSink(
            cfg,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
    if t == "dotenv":
        from .dotenv import DotenvSink

        return DotenvSink(
            cfg,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
    raise ValueError(f"Unknown sink type: {cfg.type}")
