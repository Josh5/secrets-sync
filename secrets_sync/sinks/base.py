from __future__ import annotations

import logging
from typing import Iterable, Optional

from ..models import SecretItem, SinkConfig

_detail_logger = logging.getLogger("secrets_sync.sync_details")


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
        self._detail_value_snapshots = bool(print_sync_details and detail_value_snapshots)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        raise NotImplementedError

    @property
    def sink_label(self) -> str:
        return self.config.name or self.__class__.__name__

    @property
    def detail_logging_enabled(self) -> bool:
        return self._print_sync_details

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
        _detail_logger.info("[%s] %s -> succeeded (%s)", self.sink_label, item_name, detail)

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
        if error:
            _detail_logger.error(
                "[%s] %s -> failed (%s): %s", self.sink_label, item_name, detail, error
            )
        else:
            _detail_logger.error("[%s] %s -> failed (%s)", self.sink_label, item_name, detail)

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
    raise ValueError(f"Unknown sink type: {cfg.type}")
