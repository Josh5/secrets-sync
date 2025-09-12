from __future__ import annotations

from typing import Dict

from ..models import SecretItem, SourceConfig


class BaseSource:
    def __init__(self, config: SourceConfig):
        self.config = config
        # Backward compat: if no name provided, use type
        if not getattr(self.config, "name", None):
            self.config.name = self.config.type

    async def pull(self) -> Dict[str, SecretItem]:
        raise NotImplementedError


def build_source(cfg: SourceConfig) -> BaseSource:
    t = (cfg.type or "").lower()
    if t == "env":
        from .env_vars import EnvSource

        return EnvSource(cfg)
    if t == "yaml":
        from .yaml_file import YamlSource

        return YamlSource(cfg)
    if t in ("1password", "onepassword", "op"):
        from .onepassword import OnePasswordSource

        return OnePasswordSource(cfg)
    if t in ("keeper",):
        from .keeper import KeeperSource

        return KeeperSource(cfg)
    raise ValueError(f"Unknown source type: {cfg.type}")
