"""顶层配置：各源的子配置聚合。"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from .sources.javdb.config import JavdbSourceConfig


@dataclass
class ScraperPluginConfig:
    javdb: JavdbSourceConfig = field(default_factory=JavdbSourceConfig)

    @classmethod
    def load(cls, path: Path) -> "ScraperPluginConfig":
        if not path.exists():
            cfg = cls()
            try:
                cfg.save(path)
            except OSError:
                pass
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        kwargs = {}
        for f in dataclasses.fields(cls):
            sub = data.get(f.name)
            if isinstance(sub, dict):
                sub_cls = _sub_config_class(f.name)
                if sub_cls is not None:
                    kwargs[f.name] = sub_cls.from_dict(sub)
                    continue
            # 兜底：使用默认值
        return cls(**kwargs) if kwargs else cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if dataclasses.is_dataclass(value):
                data[f.name] = dataclasses.asdict(value)    # type: ignore[arg-type]
            else:
                data[f.name] = value
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _sub_config_class(field_name: str):
    """字段名 → 子配置类。新增源时在这里加一行。"""
    if field_name == "javdb":
        return JavdbSourceConfig
    return None