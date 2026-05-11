from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, Iterable, Optional

import requests

from .base import BaseSink
from ..models import SecretItem, SinkConfig
from ..utils.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)


def _get_attr(obj, *names: str):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


class InfisicalSink(BaseSink):
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
        try:
            from infisical_sdk import InfisicalSDKClient
        except ImportError as exc:
            raise RuntimeError(
                "Infisical sink requires the 'infisicalsdk' package to be installed"
            ) from exc

        self._sdk_client_cls = InfisicalSDKClient
        o = config.options or {}
        self.host = str(
            o.get("host")
            or o.get("site_url")
            or os.getenv("INFISICAL_HOST")
            or "https://app.infisical.com"
        ).rstrip("/")
        self.project_id = self._clean_str(o.get("project_id"))
        self.project_slug = self._clean_str(o.get("project_slug"))
        if not self.project_id and not self.project_slug:
            raise ValueError(
                "Infisical sink requires either 'project_id' or 'project_slug'"
            )
        self.environment_slug = self._required_str(
            o.get("environment_slug") or o.get("environment"),
            "Infisical sink requires 'environment_slug'",
        )
        self.secret_path = self._normalize_path(
            o.get("secret_path") or o.get("path") or "/"
        )
        self.name_prefix = (
            self._clean_str(o.get("name_prefix") or o.get("prefix")) or ""
        )
        self.concurrency = int(o.get("concurrency", 5))
        self.rate_limit_rps = float(o.get("rate_limit_rps", 5))
        self._limiter = TokenBucketRateLimiter(
            self.rate_limit_rps, capacity=self.concurrency
        )
        self._sem = asyncio.Semaphore(self.concurrency)

        self.auth_method = self._determine_auth_method(o)
        self.token = self._clean_str(os.getenv("INFISICAL_TOKEN"))
        self.client_id = self._clean_str(os.getenv("INFISICAL_CLIENT_ID"))
        self.client_secret = self._clean_str(os.getenv("INFISICAL_CLIENT_SECRET"))
        self._validate_auth_options()
        self._client = None
        self._client_lock = asyncio.Lock()
        self._api_checked = False
        self._api_check_lock = asyncio.Lock()
        self._resolved_project_id = self.project_id
        self._project_id_lock = asyncio.Lock()
        self._project_data = None
        self._project_data_lock = asyncio.Lock()
        self._environment_checked = False
        self._environment_check_lock = asyncio.Lock()
        self._path_checked = False
        self._path_check_lock = asyncio.Lock()

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

    def _normalize_path(self, raw: object) -> str:
        path = self._required_str(raw, "Infisical sink requires 'secret_path'")
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    def _determine_auth_method(self, options: Dict[str, object]) -> str:
        explicit = self._clean_str(options.get("auth_method"))
        if explicit:
            method = explicit.lower().replace("-", "_")
            if method not in ("token", "token_auth", "universal", "universal_auth"):
                raise ValueError(
                    "Infisical sink 'auth_method' must be 'token' or 'universal_auth'"
                )
            return method
        if os.getenv("INFISICAL_TOKEN"):
            return "token"
        return "universal_auth"

    def _validate_auth_options(self) -> None:
        if self.auth_method in ("token", "token_auth"):
            if not self.token:
                raise ValueError(
                    "Infisical sink using token auth requires INFISICAL_TOKEN"
                )
            return
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Infisical sink using universal auth requires INFISICAL_CLIENT_ID "
                "and INFISICAL_CLIENT_SECRET"
            )

    def _secret_name(self, item: SecretItem) -> str:
        if self.name_prefix:
            return f"{self.name_prefix}{item.name}"
        return item.name

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
                "Infisical sink is configured with a project slug that was not found.\n"
                f"Configured project_slug: {self.project_slug!r}\n"
                f"Configured host: {self.host!r}\n"
                "Check the 'project_slug' value in the sink config. If you have a project ID instead, "
                "use 'project_id' rather than 'project_slug'."
            )

        if "Project with id" in message and "not found" in message:
            return (
                "Infisical sink is configured with a project ID that was not found.\n"
                f"Configured project_id: {self.project_id!r}\n"
                f"Configured host: {self.host!r}\n"
                "Check the 'project_id' value in the sink config, or switch to 'project_slug' if that is "
                "what you intended to use."
            )

        if "Status: 401" in message or "Status: 403" in message:
            return (
                "Infisical authentication or authorization failed.\n"
                f"Configured host: {self.host!r}\n"
                f"Configured project: {project_ref!r}\n"
                f"Configured environment_slug: {self.environment_slug!r}\n"
                "Check that the machine identity token or client credentials are valid and that the identity "
                "has access to the target project and path."
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

    def _fetch_projects(self, client):
        response = client.api.session.get(
            self._build_api_url("/api/v1/projects"),
            allow_redirects=False,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    async def _fetch_project_data(self, client):
        if self._project_data is not None:
            return self._project_data
        async with self._project_data_lock:
            if self._project_data is not None:
                return self._project_data
            try:
                data = await asyncio.to_thread(self._fetch_projects, client)
            except Exception as exc:
                self._raise_request_context("project lookup", exc)
            self._project_data = data
            return data

    async def _resolve_project(self, client) -> dict:
        data = await self._fetch_project_data(client)

        for project in data.get("projects", []):
            project_id = str(project.get("id", "")).strip()
            project_slug = str(project.get("slug", "")).strip()
            if self.project_id and project_id == self.project_id:
                self._resolved_project_id = project_id
                return project
            if self.project_slug and project_slug == self.project_slug:
                if project_id:
                    self._resolved_project_id = project_id
                return project

        if self.project_id:
            self._raise_request_context(
                "project lookup",
                RuntimeError(f"Project with id {self.project_id!r} was not found"),
            )
        self._raise_request_context(
            "project lookup",
            RuntimeError(f"Project with slug {self.project_slug!r} was not found"),
        )

    async def _resolve_project_id(self, client) -> str:
        if self._resolved_project_id:
            return self._resolved_project_id
        async with self._project_id_lock:
            if self._resolved_project_id:
                return self._resolved_project_id
            project = await self._resolve_project(client)
            project_id = str(project.get("id", "")).strip()
            if not project_id:
                self._raise_request_context(
                    "project lookup",
                    RuntimeError("Project lookup returned a project without an ID"),
                )
            self._resolved_project_id = project_id
            return project_id

    def _create_environment(
        self,
        client,
        *,
        project_id: str,
        environment_slug: str,
        environment_name: str,
        position: Optional[int],
    ):
        payload = {
            "name": environment_name,
            "slug": environment_slug,
        }
        if position is not None:
            payload["position"] = position
        response = client.api.session.post(
            self._build_api_url(f"/api/v1/projects/{project_id}/environments"),
            json=payload,
            allow_redirects=False,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    async def _ensure_environment(self, client) -> str:
        if self._environment_checked:
            return await self._resolve_project_id(client)

        async with self._environment_check_lock:
            if self._environment_checked:
                return await self._resolve_project_id(client)

            project = await self._resolve_project(client)
            project_id = str(project.get("id", "")).strip()
            if not project_id:
                self._raise_request_context(
                    "project lookup",
                    RuntimeError("Project lookup returned a project without an ID"),
                )

            environments = project.get("environments") or []
            for env in environments:
                if str(_get_attr(env, "slug") or "").strip() == self.environment_slug:
                    self._resolved_project_id = project_id
                    self._environment_checked = True
                    return project_id

            positions = []
            for env in environments:
                raw_position = _get_attr(env, "position")
                if raw_position is None:
                    continue
                try:
                    positions.append(int(raw_position))
                except (TypeError, ValueError):
                    continue
            next_position = (max(positions) + 1) if positions else None

            try:
                await asyncio.to_thread(
                    self._create_environment,
                    client,
                    project_id=project_id,
                    environment_slug=self.environment_slug,
                    environment_name=self.environment_slug,
                    position=next_position,
                )
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" not in message:
                    self._raise_request_context("environment create", exc)

            self._resolved_project_id = project_id
            self._environment_checked = True
            self._project_data = None
            return project_id

    async def _list_folders(self, client, *, project_id: str, path: str):
        def do_list():
            return client.folders.list_folders(
                project_id=project_id,
                environment_slug=self.environment_slug,
                path=path,
                recursive=False,
            )

        return await asyncio.to_thread(do_list)

    async def _create_folder(
        self,
        client,
        *,
        project_id: str,
        parent_path: str,
        folder_name: str,
    ) -> None:
        def do_create():
            return client.folders.create_folder(
                name=folder_name,
                environment_slug=self.environment_slug,
                project_id=project_id,
                path=parent_path,
            )

        await asyncio.to_thread(do_create)

    async def _ensure_secret_path(self, client) -> None:
        async with self._path_check_lock:
            if self._path_checked:
                return

            project_id = await self._ensure_environment(client)
            if self.secret_path == "/":
                self._path_checked = True
                return

            current_path = "/"
            for segment in [part for part in self.secret_path.split("/") if part]:
                try:
                    folders_response = await self._list_folders(
                        client,
                        project_id=project_id,
                        path=current_path,
                    )
                except Exception as exc:
                    self._raise_request_context("folder lookup", exc)

                folders = _get_attr(folders_response, "folders") or []
                exists = any(
                    str(_get_attr(folder, "name") or "") == segment
                    for folder in folders
                )
                if not exists:
                    try:
                        await self._create_folder(
                            client,
                            project_id=project_id,
                            parent_path=current_path,
                            folder_name=segment,
                        )
                    except Exception as exc:
                        message = str(exc)
                        if "already exists" not in message.lower():
                            self._raise_request_context("folder create", exc)
                current_path = (
                    f"/{segment}"
                    if current_path == "/"
                    else f"{current_path.rstrip('/')}/{segment}"
                )

            self._path_checked = True

    def _secret_scope_kwargs(self) -> Dict[str, str]:
        kwargs = {
            "environment_slug": self.environment_slug,
            "secret_path": self.secret_path,
        }
        if self.project_id:
            kwargs["project_id"] = self.project_id
        else:
            kwargs["project_slug"] = self.project_slug  # type: ignore[assignment]
        return kwargs

    async def _list_existing(self) -> Dict[str, str]:
        client = await self._client_instance()
        await self._ensure_api_access(client)
        await self._ensure_secret_path(client)

        def do_list():
            return client.secrets.list_secrets(
                **self._secret_scope_kwargs(),
                expand_secret_references=True,
                view_secret_value=True,
                include_imports=False,
                recursive=False,
            )

        try:
            response = await asyncio.to_thread(do_list)
        except requests.RequestException as exc:
            self._raise_request_context("list", exc)
        except Exception as exc:
            self._raise_request_context("list", exc)
        raw_secrets = _get_attr(response, "secrets")
        if raw_secrets is None and isinstance(response, list):
            raw_secrets = response

        existing: Dict[str, str] = {}
        for secret in raw_secrets or []:
            name = _get_attr(secret, "secretKey", "secret_key", "key", "name")
            value = _get_attr(secret, "secretValue", "secret_value", "value")
            if name is None:
                continue
            existing[str(name)] = "" if value is None else str(value)
        return existing

    def _classify_action(
        self, existed: bool, old_value: Optional[str], new_value: str
    ) -> str:
        if not existed:
            return "created"
        if old_value == new_value:
            return "unchanged"
        return "changed"

    async def _create_or_update_one(
        self,
        item: SecretItem,
        existing_values: Dict[str, str],
    ) -> None:
        await self._limiter.acquire()
        async with self._sem:
            client = await self._client_instance()
            secret_name = self._secret_name(item)
            existed = secret_name in existing_values
            old_value = existing_values.get(secret_name)
            action = self._classify_action(existed, old_value, item.value)

            if action == "unchanged":
                if self.detail_logging_enabled:
                    self.log_sync_success(
                        secret_name,
                        action,
                        old_value=old_value,
                        new_value=item.value,
                    )
                return

            kwargs = dict(
                self._secret_scope_kwargs(),
                secret_value=item.value,
                secret_comment=item.description,
                skip_multiline_encoding=False,
            )

            try:
                if existed:
                    await asyncio.to_thread(
                        client.secrets.update_secret_by_name,
                        current_secret_name=secret_name,
                        **kwargs,
                    )
                else:
                    await asyncio.to_thread(
                        client.secrets.create_secret_by_name,
                        secret_name=secret_name,
                        **kwargs,
                    )
            except requests.RequestException as exc:
                wrapped = RuntimeError(
                    f"Infisical write failed for secret {secret_name!r}: {exc}"
                )
                if self.detail_logging_enabled:
                    self.log_sync_failure(
                        secret_name,
                        action,
                        wrapped,
                        old_value=old_value,
                        new_value=item.value,
                    )
                raise wrapped from exc
            except Exception as exc:
                wrapped = RuntimeError(
                    f"Infisical write failed for secret {secret_name!r}: {exc}"
                )
                if self.detail_logging_enabled:
                    self.log_sync_failure(
                        secret_name,
                        action,
                        wrapped,
                        old_value=old_value,
                        new_value=item.value,
                    )
                raise wrapped from exc
            else:
                existing_values[secret_name] = item.value
                if self.detail_logging_enabled:
                    self.log_sync_success(
                        secret_name,
                        action,
                        old_value=old_value,
                        new_value=item.value,
                    )
                logger.debug("Infisical synced %s", secret_name)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        item_list = list(items)
        if not item_list:
            return
        existing_values = await self._list_existing()
        await asyncio.gather(
            *(self._create_or_update_one(item, existing_values) for item in item_list)
        )
