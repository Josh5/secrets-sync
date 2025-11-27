from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from keepercommander.__main__ import get_params_from_config
from keepercommander import api

from ..models import SecretItem, SourceConfig
from .base import BaseSource, SecretCandidate


class KeeperSource(BaseSource):
    """Pull secrets from Keeper using the Keeper Commander SDK.

    options:
      folder: required Keeper folder name/path
      tag_filters: list[str] of tags; any match qualifies
      include_regex: optional regex to filter record titles
      config_file: optional path to Keeper CLI config (default ~/.keeper/config.json)
      keeper_server: optional override for Keeper server (or env KEEPER_SERVER)
      keeper_user: optional override for Keeper username (or env KEEPER_USER)
      keeper_password: optional override for Keeper password (or env KEEPER_PASSWORD)
    """

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        o = config.options or {}
        self.folder: str = str(o.get("folder") or "").strip()
        if not self.folder:
            raise ValueError("Keeper source requires 'folder' option")
        self.tag_filters: List[str] = self._normalize_tag_list(o.get("tag_filters") or [])
        self._tag_filter_set: Set[str] = set(self.tag_filters)
        include_regex = str(o.get("include_regex") or "").strip()
        self.include_re: Optional[re.Pattern[str]] = re.compile(include_regex) if include_regex else None
        config_path = o.get("config_file") or "~/.keeper/config.json"
        self.config_file = os.path.expanduser(str(config_path))
        self.keeper_server = self._clean_credential(o.get("keeper_server") or os.getenv("KEEPER_SERVER"))
        self.keeper_user = self._clean_credential(o.get("keeper_user") or os.getenv("KEEPER_USER"))
        self.keeper_password = self._clean_credential(o.get("keeper_password") or os.getenv("KEEPER_PASSWORD"))

    def _clean_credential(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _collect_records(self) -> List[dict]:
        params = self._init_session()
        folder = self._locate_folder(params)
        records: List[dict] = []
        for ref in self._folder_record_refs(folder):
            uid = self._record_uid(ref)
            if not uid:
                continue
            record_dict = self._fetch_record(params, uid)
            if record_dict:
                records.append(record_dict)
        return records

    def _fetch_record(self, params, uid: str) -> Optional[dict]:
        try:
            record_obj = api.get_record(params, uid)
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch Keeper record {uid}: {exc}") from exc
        if not record_obj:
            return None
        record_dict = record_obj.to_dictionary()
        record_dict.setdefault("record_uid", uid)
        return record_dict

    def _init_session(self):
        config_exists = os.path.exists(self.config_file)
        if not config_exists and not self._has_credentials_override():
            raise FileNotFoundError(
                f"Keeper config file '{self.config_file}' not found. Provide keeper credentials or create the file."
            )
        params = get_params_from_config(self.config_file)
        if not params:
            raise RuntimeError(f"Unable to load Keeper params from '{self.config_file}'")
        if self.keeper_server:
            params.server = self.keeper_server
        if self.keeper_user:
            params.user = self.keeper_user
        if self.keeper_password:
            params.password = self.keeper_password
        try:
            api.login(params)
        except Exception as exc:
            raise RuntimeError(
                "Failed to login to Keeper; refresh persistent login via `keeper shell`."
            ) from exc
        api.sync_down(params)
        return params

    def _has_credentials_override(self) -> bool:
        return bool(self.keeper_user and self.keeper_password)

    def _locate_folder(self, params) -> Any:
        matches = api.search_shared_folders(params, self.folder) or []
        if not matches:
            raise ValueError(f"Keeper folder '{self.folder}' not found or inaccessible")
        for match in matches:
            name = getattr(match, "name", "") or getattr(match, "folder_key_unencrypted", "")
            if str(name).strip() == self.folder:
                return match
        return matches[0]

    def _folder_record_refs(self, folder: Any) -> List[dict]:
        records = getattr(folder, "records", None)
        if records is None and isinstance(folder, dict):
            records = folder.get("records")
        if not isinstance(records, list):
            return []
        return [rec for rec in records if rec is not None]

    def _extract_value(self, record: dict) -> Optional[str]:
        for candidate in self._value_candidates(record):
            value = self._first_scalar(candidate)
            if value:
                return value
        return None

    def _value_candidates(self, record: dict) -> Iterable[Union[str, Sequence[Any], None]]:
        yield record.get("password")
        for field in self._field_entries(record, ("fields",)):
            yield field.get("value")
        for field in self._custom_entries(record):
            if self._field_label(field) == "tags":
                continue
            yield field.get("value")
        yield self._container_value(record, "notes")

    def _first_scalar(self, value: Union[str, Sequence[Any], None]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, Sequence):
            for v in value:
                if v:
                    return str(v)
        return None

    def _extract_tags(self, record: dict) -> List[str]:
        for field in self._custom_entries(record):
            if self._field_label(field) not in {"tags", "text:tags"}:
                continue
            tags: List[str] = []
            for candidate in self._expand_field_values(field.get("value")):
                tags.extend(self._split_and_strip(candidate))
            return self._normalize_tag_list(tags)
        return []

    def _record_title(self, record: dict) -> str:
        return str(record.get("title") or record.get("record_title") or record.get("name") or "")

    def _record_uid(self, record: Any) -> Optional[str]:
        if record is None:
            return None
        if isinstance(record, str):
            return record
        if isinstance(record, dict):
            for key in ("record_uid", "recordUid", "uid", "id"):
                if record.get(key):
                    return str(record[key])
            return None
        for attr in ("record_uid", "recordUid", "uid", "id"):
            value = getattr(record, attr, None)
            if value:
                return str(value)
        return None

    def _tags_match(self, record_tags: List[str]) -> bool:
        if not self._tag_filter_set:
            return True
        normalized = {tag for tag in record_tags if tag}
        return bool(normalized & self._tag_filter_set)

    def _custom_entries(self, record: dict) -> Iterable[dict]:
        for entry in self._field_entries(record, ("custom_fields", "custom")):
            yield entry

    def _field_label(self, field: dict) -> str:
        return str(field.get("label") or field.get("name") or "").strip().lower()

    def _expand_field_values(self, raw_value: Union[str, Sequence[Any], None]) -> List[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        if isinstance(raw_value, Sequence):
            return [str(v) for v in raw_value if v is not None]
        return [str(raw_value)]

    def _containers(self, record: dict) -> Iterable[dict]:
        yield record
        for key in ("data", "details"):
            sub = record.get(key)
            if isinstance(sub, dict):
                yield sub

    def _field_entries(self, record: dict, names: Iterable[str]) -> Iterable[dict]:
        for container in self._containers(record):
            for key in names:
                collection = container.get(key)
                if isinstance(collection, list):
                    for entry in collection:
                        if isinstance(entry, dict):
                            yield entry

    def _split_and_strip(self, raw: str) -> List[str]:
        return [part for part in (segment.strip() for segment in raw.split(",")) if part]

    def _container_value(self, record: dict, key: str) -> Optional[str]:
        for container in self._containers(record):
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def pull(self) -> Dict[str, SecretItem]:
        records = await asyncio.to_thread(self._collect_records)
        candidates: List[SecretCandidate] = []

        for detail in records:
            title = self._record_title(detail)
            if not title:
                continue
            if self.include_re and not self.include_re.search(title):
                continue
            tags = self._extract_tags(detail)
            if self.tag_filters and not self._tags_match(tags):
                continue
            value = self._extract_value(detail)
            if value is None:
                continue
            candidates.append(SecretCandidate(name=title, value=str(value), tags=tags))

        return self._select_candidate_values(candidates, self.tag_filters)
