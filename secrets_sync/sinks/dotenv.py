from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .base import BaseSink
from ..models import SecretItem, SinkConfig

_PORTABLE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DotenvSink(BaseSink):
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
        raw_path = options.get("path") or options.get("file")
        if not raw_path:
            raise ValueError("Dotenv sink requires 'path'")
        self.path = Path(str(raw_path))
        self.file_mode = self._read_file_mode(options)
        self.mode = self._read_mode(options)
        self.key_case = self._read_key_case(options)

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
                    "Dotenv sink 'file_mode' must be an octal file mode such as '0600' or '0644'"
                ) from exc

        if mode < 0 or mode > 0o777:
            raise ValueError("Dotenv sink 'file_mode' must be between 0000 and 0777")
        return mode

    def _read_mode(self, options: Dict[str, object]) -> str:
        mode = str(options.get("mode") or "merge").strip().lower()
        if mode not in ("merge", "replace"):
            raise ValueError("Dotenv sink 'mode' must be 'merge' or 'replace'")
        return mode

    def _read_key_case(self, options: Dict[str, object]) -> str:
        explicit = options.get("key_case")
        if explicit is not None:
            key_case = str(explicit).strip().lower()
            if key_case not in ("preserve", "upper", "lower"):
                raise ValueError(
                    "Dotenv sink 'key_case' must be 'preserve', 'upper', or 'lower'"
                )
            return key_case

        force_upper = bool(options.get("force_uppercase", False))
        force_lower = bool(options.get("force_lowercase", False))
        if force_upper and force_lower:
            raise ValueError(
                "Dotenv sink cannot enable both 'force_uppercase' and 'force_lowercase'"
            )
        if force_upper:
            return "upper"
        if force_lower:
            return "lower"
        return "preserve"

    def _transform_name(self, name: str) -> str:
        transformed = self.transform_name(name)
        if self.key_case == "upper":
            transformed = transformed.upper()
        elif self.key_case == "lower":
            transformed = transformed.lower()
        return transformed

    def _validate_name(self, original_name: str, transformed_name: str) -> None:
        if not transformed_name:
            raise ValueError(
                f"Dotenv sink transformed secret name {original_name!r} into an empty key"
            )
        if not _PORTABLE_KEY_RE.match(transformed_name):
            raise ValueError(
                "Dotenv sink produced a non-portable environment variable name.\n"
                f"Original secret name: {original_name!r}\n"
                f"Transformed name: {transformed_name!r}\n"
                "Dotenv output is intended for environment-style consumers, so keys must "
                "match [A-Za-z_][A-Za-z0-9_]* after prefix stripping and case transforms."
            )

    def _format_value(self, value: str) -> str:
        if value and re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
            return value
        escaped = (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
        )
        return f'"{escaped}"'

    def _render_lines(self, values: Dict[str, str]) -> str:
        lines = [
            f"{name}={self._format_value(value)}"
            for name, value in sorted(values.items())
        ]
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def _parse_key_value_line(self, raw_line: str) -> Optional[Tuple[str, str]]:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            return None
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            return None
        name, raw_value = line.split("=", 1)
        key = name.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            unquoted = value[1:-1]
            value = (
                unquoted.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return key, value

    def _parse_existing_file(self) -> Dict[str, str]:
        if not self.path.exists():
            return {}

        result: Dict[str, str] = {}
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            parsed = self._parse_key_value_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            result[key] = value
        return result

    def _render_merge_file(self, transformed_values: Dict[str, str]) -> str:
        if not self.path.exists():
            return self._render_lines(transformed_values)

        existing_text = self.path.read_text(encoding="utf-8")
        lines = existing_text.splitlines()
        output_lines: List[str] = []
        seen_keys = set()

        for raw_line in lines:
            parsed = self._parse_key_value_line(raw_line)
            if parsed is None:
                output_lines.append(raw_line)
                continue
            key, _ = parsed
            if key in transformed_values:
                output_lines.append(
                    f"{key}={self._format_value(transformed_values[key])}"
                )
                seen_keys.add(key)
            else:
                output_lines.append(raw_line)

        for key in sorted(transformed_values):
            if key not in seen_keys:
                output_lines.append(
                    f"{key}={self._format_value(transformed_values[key])}"
                )

        rendered = "\n".join(output_lines)
        if output_lines:
            rendered += "\n"
        return rendered

    def _write_file(self, rendered: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            delete=False,
        ) as handle:
            handle.write(rendered)
            temp_path = handle.name
        os.replace(temp_path, self.path)
        if self.file_mode is not None:
            os.chmod(self.path, self.file_mode)

    async def push_many(self, items: Iterable[SecretItem]) -> None:
        item_list = self.select_items(items)
        transformed_values: Dict[str, str] = {}
        transformed_items: Dict[str, SecretItem] = {}

        for item in item_list:
            transformed_name = self._transform_name(item.name)
            self._validate_name(item.name, transformed_name)
            previous = transformed_items.get(transformed_name)
            if previous is not None:
                raise ValueError(
                    "Dotenv sink produced duplicate keys after applying strip_prefix and key_case.\n"
                    f"First secret: {previous.name!r}\n"
                    f"Second secret: {item.name!r}\n"
                    f"Transformed key: {transformed_name!r}\n"
                    "Adjust the sink transforms or narrow the source selection so each emitted key is unique."
                )
            transformed_items[transformed_name] = item
            transformed_values[transformed_name] = item.value

        existing_values = await asyncio.to_thread(self._parse_existing_file)
        if self.mode == "replace":
            rendered = self._render_lines(transformed_values)
        else:
            rendered = await asyncio.to_thread(
                self._render_merge_file, transformed_values
            )
        await asyncio.to_thread(self._write_file, rendered)

        if not self.detail_logging_enabled:
            return

        for key, item in sorted(transformed_items.items()):
            existed = key in existing_values
            old_value = existing_values.get(key)
            action = "created"
            if existed:
                action = "unchanged" if old_value == item.value else "changed"
            self.log_sync_success(
                key,
                action,
                old_value=old_value,
                new_value=item.value,
            )
