from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List


@dataclass
class SecretItem:
    name: str
    value: str
    description: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    # Origin source name that produced this item
    source: Optional[str] = None


@dataclass
class SyncSummary:
    total: int = 0
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0

    def record(self, action: str, *, failed: bool = False) -> None:
        self.total += 1
        if failed:
            self.failed += 1
            return
        if action == "created":
            self.created += 1
            return
        if action in ("updated", "changed"):
            self.changed += 1
            return
        if action == "unchanged":
            self.unchanged += 1

    def merge(self, other: "SyncSummary") -> None:
        self.total += other.total
        self.created += other.created
        self.changed += other.changed
        self.unchanged += other.unchanged
        self.failed += other.failed

    @property
    def updated(self) -> bool:
        return self.created > 0 or self.changed > 0


@dataclass
class SourceConfig:
    type: str
    name: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    vars: Dict[str, str] = field(default_factory=dict)


@dataclass
class SinkConfig:
    type: str
    name: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)


@dataclass
class AwsConfig:
    region: Optional[str] = None
    profile: Optional[str] = None


@dataclass
class AppConfig:
    aws: AwsConfig = field(default_factory=AwsConfig)
    sources: List[SourceConfig] = field(default_factory=list)
    sinks: List[SinkConfig] = field(default_factory=list)
    vars: Dict[str, str] = field(default_factory=dict)
