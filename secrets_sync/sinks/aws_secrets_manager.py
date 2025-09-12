from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import boto3

from .base import BaseSink
from ..models import SecretItem, SinkConfig
from ..utils.rate_limiter import TokenBucketRateLimiter
from ..utils.retry import retry_aws

logger = logging.getLogger(__name__)


class SecretsManagerSink(BaseSink):
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
        self.prefix = o.get("prefix", "")
        self.kms_key_id = o.get("kms_key_id")
        self.rate_limit_rps = float(o.get("rate_limit_rps", 5))
        self.concurrency = int(o.get("concurrency", 5))
        self._limiter = TokenBucketRateLimiter(self.rate_limit_rps, capacity=self.concurrency)
        self._sem = asyncio.Semaphore(self.concurrency)

        session = boto3.session.Session()
        self.client = session.client("secretsmanager")

    def _name(self, item: SecretItem) -> str:
        if self.prefix:
            return f"{self.prefix.rstrip('/')}/{item.name}"
        return item.name

    async def _ensure_secret(self, name: str, description: str | None) -> None:
        """Create secret if it does not exist."""
        try:
            await asyncio.to_thread(self.client.describe_secret, SecretId=name)
            return
        except self.client.exceptions.ResourceNotFoundException:
            kwargs = dict(Name=name)
            if description:
                kwargs["Description"] = description
            if self.kms_key_id:
                kwargs["KmsKeyId"] = self.kms_key_id
            async def do_create():
                await asyncio.to_thread(self.client.create_secret, **kwargs)
            await retry_aws(do_create)
            logger.debug("SecretsManager created %s", name)

    async def _fetch_existing_value(self, name: str) -> tuple[bool, str | None]:
        try:
            resp = await asyncio.to_thread(self.client.get_secret_value, SecretId=name)
            return True, resp.get("SecretString")
        except self.client.exceptions.ResourceNotFoundException:
            return False, None

    def _classify_action(self, existed: bool, old_value: str | None, new_value: str) -> str:
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
            old_value: str | None = None
            if self.detail_logging_enabled:
                existed, old_value = await self._fetch_existing_value(name)
            action = self._classify_action(existed, old_value, item.value)
            await self._ensure_secret(name, item.description)
            async def do_put():
                await asyncio.to_thread(
                    self.client.put_secret_value,
                    SecretId=name,
                    SecretString=item.value,
                )
            try:
                await retry_aws(do_put)
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
            logger.debug("SecretsManager put value for %s", name)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        tasks = [self._put_one(i) for i in items]
        await asyncio.gather(*tasks)
