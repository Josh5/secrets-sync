from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable

from ..models import SecretItem, SinkConfig
from .base import BaseSink


class DirFilesSink(BaseSink):
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
        options = config.options or {}
        raw_path = options.get("path") or options.get("dir") or options.get("directory")
        if not raw_path:
            raise ValueError("Dir files sink requires 'path'")
        self.path = Path(str(raw_path))
        self.file_mode = self._read_file_mode(options)

    def _read_file_mode(self, options: Dict[str, object]) -> int | None:
        raw_mode = options.get("file_mode")
        if raw_mode is None:
            raw_mode = options.get("permissions")
        if raw_mode is None or raw_mode == "":
            return None

        if isinstance(raw_mode, int):
            mode = raw_mode
        else:
            text = str(raw_mode).strip().lower()
            try:
                if text.startswith("0o"):
                    mode = int(text, 8)
                elif text.startswith("0") and text != "0":
                    mode = int(text, 8)
                else:
                    mode = int(text, 8)
            except ValueError as exc:
                raise ValueError(
                    "Dir files sink 'file_mode' must be an octal file mode such as '0600' or '0644'"
                ) from exc

        if mode < 0 or mode > 0o777:
            raise ValueError("Dir files sink 'file_mode' must be between 0000 and 0777")
        return mode

    def _transform_name(self, name: str) -> str:
        return self.transform_name(name)

    def _validate_name(self, original_name: str, transformed_name: str) -> None:
        if not transformed_name:
            raise ValueError(
                f"Dir files sink transformed secret name {original_name!r} into an empty file name"
            )
        candidate = Path(transformed_name)
        if (
            candidate.is_absolute()
            or len(candidate.parts) != 1
            or transformed_name in {".", ".."}
        ):
            raise ValueError(
                "Dir files sink produced an unsafe file name.\n"
                f"Original secret name: {original_name!r}\n"
                f"Transformed file name: {transformed_name!r}\n"
                "File sink names must resolve to a single relative file name without path separators."
            )

    def _write_file(self, file_name: str, value: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        target_path = self.path / file_name
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path),
            delete=False,
        ) as handle:
            handle.write(value)
            temp_path = handle.name
        os.replace(temp_path, target_path)
        if self.file_mode is not None:
            os.chmod(target_path, self.file_mode)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        item_list = self.select_items(items)
        transformed_items: Dict[str, SecretItem] = {}

        for item in item_list:
            transformed_name = self._transform_name(item.name)
            self._validate_name(item.name, transformed_name)
            previous = transformed_items.get(transformed_name)
            if previous is not None:
                raise ValueError(
                    "Dir files sink produced duplicate file names after applying strip_prefix.\n"
                    f"First secret: {previous.name!r}\n"
                    f"Second secret: {item.name!r}\n"
                    f"Transformed file name: {transformed_name!r}\n"
                    "Adjust the sink transforms or narrow the sink filters so each emitted file name is unique."
                )
            transformed_items[transformed_name] = item

        for file_name, item in sorted(transformed_items.items()):
            target_path = self.path / file_name
            old_value = None
            existed = await asyncio.to_thread(target_path.exists)
            if existed:
                old_value = await asyncio.to_thread(
                    target_path.read_text, encoding="utf-8"
                )
            await asyncio.to_thread(self._write_file, file_name, item.value)
            if not self.detail_logging_enabled:
                continue
            action = "created"
            if existed:
                action = "unchanged" if old_value == item.value else "changed"
            self.log_sync_success(
                file_name,
                action,
                old_value=old_value,
                new_value=item.value,
            )
