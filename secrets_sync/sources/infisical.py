from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, Iterable, Optional

import requests

from ..models import SecretItem, SourceConfig
from ..utils.rate_limiter import TokenBucketRateLimiter
from .base import BaseSource

logger = logging.getLogger(__name__)


def _get_attr(obj, *names: str):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


class InfisicalSource(BaseSource):
    def __init__(self, config: SourceConfig):
        super().__init__(config)
        try:
            from infisical_sdk import InfisicalSDKClient
        except ImportError as exc:
            raise RuntimeError(
                "Infisical source requires the 'infisicalsdk' package to be installed"
            ) from exc

        self._sdk_client_cls = InfisicalSDKClient
        options = config.options or {}
        self.host = str(
            options.get("host")
            or options.get("site_url")
            or os.getenv("INFISICAL_HOST")
            or "https://app.infisical.com"
        ).rstrip("/")
        self.project_id = self._clean_str(options.get("project_id"))
        self.project_slug = self._clean_str(options.get("project_slug"))
        if not self.project_id and not self.project_slug:
            raise ValueError(
                "Infisical source requires either 'project_id' or 'project_slug'"
            )
        self.environment_slug = self._required_str(
            options.get("environment_slug") or options.get("environment"),
            "Infisical source requires 'environment_slug'",
        )
        self.secret_path = self._read_path(
            options.get("secret_path") or options.get("path") or "/"
        )
        self.tag_filters = self._read_tag_list(options.get("tag_filters") or [])
        self.recursive = self._as_bool(options.get("recursive"), default=False)
        self.include_imports = self._as_bool(
            options.get("include_imports"), default=False
        )
        self.expand_secret_references = self._as_bool(
            options.get("expand_secret_references"), default=True
        )
        self.concurrency = int(options.get("concurrency", 5))
        self.rate_limit_rps = float(options.get("rate_limit_rps", 5))
        self._limiter = TokenBucketRateLimiter(
            self.rate_limit_rps, capacity=self.concurrency
        )
        self._sem = asyncio.Semaphore(self.concurrency)

        self.auth_method = self._determine_auth_method(options)
        self.token = self._clean_str(os.getenv("INFISICAL_TOKEN"))
        self.client_id = self._clean_str(os.getenv("INFISICAL_CLIENT_ID"))
        self.client_secret = self._clean_str(os.getenv("INFISICAL_CLIENT_SECRET"))
        self._validate_auth_options()
        self._client = None
        self._client_lock = asyncio.Lock()
        self._api_checked = False
        self._api_check_lock = asyncio.Lock()

    def _clean_str(self, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _required_str(self, value: object, error: str) -> str:
        text = self._clean_str(value)
        if not text:
            raise ValueError(error)
        return text

    def _read_path(self, raw: object) -> str:
        path = self._required_str(raw, "Infisical source requires 'secret_path'")
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    def _as_bool(self, value: object, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value {value!r} in Infisical source options")

    def _determine_auth_method(self, options: Dict[str, object]) -> str:
        explicit = self._clean_str(options.get("auth_method"))
        if explicit:
            method = explicit.lower().replace("-", "_")
            if method not in ("token", "token_auth", "universal", "universal_auth"):
                raise ValueError(
                    "Infisical source 'auth_method' must be 'token' or 'universal_auth'"
                )
            return method
        if os.getenv("INFISICAL_TOKEN"):
            return "token"
        return "universal_auth"

    def _validate_auth_options(self) -> None:
        if self.auth_method in ("token", "token_auth"):
            if not self.token:
                raise ValueError(
                    "Infisical source using token auth requires INFISICAL_TOKEN"
                )
            return
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Infisical source using universal auth requires INFISICAL_CLIENT_ID "
                "and INFISICAL_CLIENT_SECRET"
            )

    async def _client_instance(self):
        async with self._client_lock:
            if self._client is not None:
                return self._client

            client = self._sdk_client_cls(host=self.host)
            if self.auth_method in ("token", "token_auth"):
                await asyncio.to_thread(client.auth.token_auth.login, token=self.token)
            else:
                await asyncio.to_thread(
                    client.auth.universal_auth.login,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )
            self._client = client
            return client

    def _build_api_url(self, path: str) -> str:
        return f"{self.host}/{path.lstrip('/')}"

    def _build_api_scope_params(self) -> Dict[str, str]:
        params = {
            "environment": self.environment_slug,
            "secretPath": self.secret_path,
        }
        if self.project_id:
            params["workspaceId"] = self.project_id
        else:
            params["workspaceSlug"] = self.project_slug  # type: ignore[assignment]
        return params

    def _check_api_access(self, client) -> None:
        response = client.api.session.get(
            self._build_api_url("/api/v3/secrets/raw"),
            params=self._build_api_scope_params(),
            allow_redirects=False,
            timeout=15,
        )
        location = response.headers.get("Location", "")
        content_type = response.headers.get("Content-Type", "")
        if response.is_redirect or response.is_permanent_redirect:
            raise RuntimeError(
                "Infisical API request was redirected instead of returning JSON. "
                f"Host '{self.host}' redirected GET /api/v3/secrets/raw to '{location or '<unknown>'}'. "
                "This usually means the configured host is behind SSO or a reverse proxy auth layer. "
                "Use an Infisical API hostname that is not fronted by interactive login, or exempt '/api/*' "
                "from that auth layer."
            )
        if response.status_code == 200 and "json" not in content_type.lower():
            body_preview = response.text.strip().replace("\n", " ")[:200]
            raise RuntimeError(
                "Infisical API returned a non-JSON success response. "
                f"Host '{self.host}' returned content-type '{content_type or '<missing>'}' for "
                "GET /api/v3/secrets/raw. "
                f"Response preview: {body_preview or '<empty>'}"
            )

    async def _ensure_api_access(self, client) -> None:
        async with self._api_check_lock:
            if self._api_checked:
                return
            await asyncio.to_thread(self._check_api_access, client)
            self._api_checked = True

    def _friendly_error_message(self, operation: str, exc: Exception) -> str:
        message = str(exc).strip()
        project_ref = self.project_id or self.project_slug or "<missing>"

        if "Project with slug" in message and "not found" in message:
            return (
                "Infisical source is configured with a project slug that was not found.\n"
                f"Configured project_slug: {self.project_slug!r}\n"
                f"Configured host: {self.host!r}\n"
                "Check the 'project_slug' value. If you have a project ID instead, use 'project_id'."
            )

        if "Project with id" in message and "not found" in message:
            return (
                "Infisical source is configured with a project ID that was not found.\n"
                f"Configured project_id: {self.project_id!r}\n"
                f"Configured host: {self.host!r}\n"
                "Check the 'project_id' value, or switch to 'project_slug' if that is what you intended."
            )

        if "Status: 401" in message or "Status: 403" in message:
            return (
                "Infisical authentication or authorization failed while reading secrets.\n"
                f"Configured host: {self.host!r}\n"
                f"Configured project: {project_ref!r}\n"
                f"Configured environment_slug: {self.environment_slug!r}\n"
                f"Configured secret_path: {self.secret_path!r}\n"
                "Check that the token or client credentials are valid and that the identity has read access "
                "to the target project, environment, and path."
            )

        if "Status: 404" in message:
            return (
                f"Infisical {operation} failed because the requested resource was not found.\n"
                f"Configured host: {self.host!r}\n"
                f"Configured project: {project_ref!r}\n"
                f"Configured environment_slug: {self.environment_slug!r}\n"
                f"Configured secret_path: {self.secret_path!r}\n"
                "Check the host, project, environment slug, and secret path.\n"
                f"Infisical API error: {message}"
            )

        return (
            f"Infisical {operation} failed.\n"
            f"Configured host: {self.host!r}\n"
            f"Configured project: {project_ref!r}\n"
            f"Configured environment_slug: {self.environment_slug!r}\n"
            f"Configured secret_path: {self.secret_path!r}\n"
            f"Infisical API error: {message}"
        )

    def _raise_request_context(self, operation: str, exc: Exception) -> None:
        raise RuntimeError(self._friendly_error_message(operation, exc)) from exc

    def _secret_scope_kwargs(self) -> Dict[str, object]:
        kwargs: Dict[str, object] = {
            "environment_slug": self.environment_slug,
            "secret_path": self.secret_path,
            "expand_secret_references": self.expand_secret_references,
            "view_secret_value": True,
            "include_imports": self.include_imports,
            "recursive": self.recursive,
        }
        if self.tag_filters:
            kwargs["tag_filters"] = self.tag_filters
        if self.project_id:
            kwargs["project_id"] = self.project_id
        else:
            kwargs["project_slug"] = self.project_slug  # type: ignore[assignment]
        return kwargs

    async def _list_secrets(self) -> Iterable[object]:
        client = await self._client_instance()
        await self._ensure_api_access(client)

        def do_list():
            return client.secrets.list_secrets(**self._secret_scope_kwargs())

        await self._limiter.acquire()
        async with self._sem:
            try:
                response = await asyncio.to_thread(do_list)
            except requests.RequestException as exc:
                self._raise_request_context("list", exc)
            except Exception as exc:
                self._raise_request_context("list", exc)

        raw_secrets = _get_attr(response, "secrets")
        if raw_secrets is None and isinstance(response, list):
            raw_secrets = response
        return raw_secrets or []

    async def pull(self) -> Dict[str, SecretItem]:
        secrets = await self._list_secrets()
        items: Dict[str, SecretItem] = {}
        original_names: Dict[str, str] = {}

        for secret in secrets:
            secret_name = _get_attr(secret, "secretKey", "secret_key", "key", "name")
            if secret_name is None:
                continue
            source_name = str(secret_name)
            if not self.accepts_name(source_name):
                continue
            emitted_name = self.transform_name(source_name)
            secret_value = _get_attr(secret, "secretValue", "secret_value", "value")
            secret_comment = _get_attr(
                secret, "secretComment", "secret_comment", "comment", "description"
            )
            if emitted_name in items:
                previous_name = original_names[emitted_name]
                raise RuntimeError(
                    "Infisical source produced duplicate secret names after applying local filters.\n"
                    f"Original names: {previous_name!r} and {source_name!r}\n"
                    f"Emitted name: {emitted_name!r}\n"
                    f"Configured project: {(self.project_id or self.project_slug)!r}\n"
                    f"Configured environment_slug: {self.environment_slug!r}\n"
                    f"Configured secret_path: {self.secret_path!r}\n"
                    "This can happen when 'recursive' or 'include_imports' pulls multiple paths that reuse "
                    "the same key, or when strip transforms collapse distinct names. Narrow the path or filters "
                    "so each emitted secret name is unique."
                )
            original_names[emitted_name] = source_name
            items[emitted_name] = SecretItem(
                name=emitted_name,
                value="" if secret_value is None else str(secret_value),
                description=None if secret_comment is None else str(secret_comment),
                source=self.config.name,
            )

        logger.debug("Infisical source pulled %d secrets", len(items))
        return items
