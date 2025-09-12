from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

import boto3

from .base import BaseSink
from ..models import SecretItem, SinkConfig
from ..utils.rate_limiter import TokenBucketRateLimiter
from ..utils.retry import retry_aws

logger = logging.getLogger(__name__)


class SsmSink(BaseSink):
    def __init__(
        self,
        config: SinkConfig,
        *,
        print_sync_details: bool = False,
        detail_value_snapshots: bool = False,
    ):
        super().__init__(
            config,
            print_sync_details=print_sync_details,
            detail_value_snapshots=detail_value_snapshots,
        )
        o = config.options or {}
        # Options
        self.prefix = o.get("prefix") or o.get("path_prefix", "")
        self.overwrite = bool(o.get("overwrite", True))
        self.param_type: str = str(o.get("type", "SecureString"))
        if self.param_type not in ("SecureString", "String"):
            raise ValueError("SSM 'type' must be 'SecureString' or 'String'")
        self.kms_key_id = o.get("kms_key_id")
        self.rate_limit_rps = float(o.get("rate_limit_rps", 10))
        self.concurrency = int(o.get("concurrency", 10))
        self._limiter = TokenBucketRateLimiter(self.rate_limit_rps, capacity=self.concurrency)
        self._sem = asyncio.Semaphore(self.concurrency)

        session = boto3.session.Session()
        self.client = session.client("ssm")

    def _name(self, item: SecretItem) -> str:
        if self.prefix:
            return f"{self.prefix.rstrip('/')}/{item.name}"
        return item.name

    async def _fetch_existing_value(self, name: str) -> tuple[bool, Optional[str]]:
        try:
            resp = await asyncio.to_thread(
                self.client.get_parameter, Name=name, WithDecryption=True
            )
            param = resp.get("Parameter") or {}
            return True, param.get("Value")
        except self.client.exceptions.ParameterNotFound:
            return False, None

    def _classify_action(self, existed: bool, old_value: Optional[str], new_value: str) -> str:
        if not existed:
            return "created"
        if old_value == new_value:
            return "unchanged"
        return "changed"

    async def _put_one(self, item: SecretItem) -> None:
        await self._limiter.acquire()
        async with self._sem:
            name = self._name(item)
            existed = False
            old_value: Optional[str] = None
            if self.detail_logging_enabled:
                existed, old_value = await self._fetch_existing_value(name)
            action = self._classify_action(existed, old_value, item.value)
            kwargs = dict(
                Name=name,
                Value=item.value,
                Type=self.param_type,
                Overwrite=self.overwrite,
            )
            if self.kms_key_id:
                kwargs["KeyId"] = self.kms_key_id
            if item.description:
                kwargs["Description"] = item.description
            # Run boto3 call with retries

            async def do_call():
                await asyncio.to_thread(self.client.put_parameter, **kwargs)

            try:
                await retry_aws(do_call)
            except Exception as exc:
                if self.detail_logging_enabled:
                    self.log_sync_failure(
                        name,
                        action,
                        exc,
                        old_value=old_value,
                        new_value=item.value,
                    )
                raise
            else:
                if self.detail_logging_enabled:
                    self.log_sync_success(
                        name,
                        action,
                        old_value=old_value,
                        new_value=item.value,
                    )
            logger.debug("SSM put %s", name)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        tasks = [self._put_one(i) for i in items]
        # Limit number of pending tasks to avoid excessive memory use
        # but here concurrency is already bounded by semaphore
        await asyncio.gather(*tasks)
