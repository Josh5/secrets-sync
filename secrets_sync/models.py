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
