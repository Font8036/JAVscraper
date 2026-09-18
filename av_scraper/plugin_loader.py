"""插件发现、依赖检查、动态加载。

设计要点：
- 用户目录（exe 同级 / 项目根）优先于内置目录（_MEIPASS）。
- 依赖检查用 import 而不是 importlib.metadata，
  避免 PyInstaller 打包后 metadata 查询失效的问题。
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .plugin_api import Plugin, PluginContext

logger = logging.getLogger(__name__)

# 包名 → import 名（大部分情况只需把 - 换成 _）
_IMPORT_NAME_MAP = {
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "python-dateutil": "dateutil",
    "pyyaml": "yaml",
}


@dataclass
class PluginSpec:
    dir: Path
    name: str
    version: str
    entry_module: str
    entry_class: str
    dependencies: list[str] = field(default_factory=list)
    plugin_class: Optional[type] = None
    error: Optional[str] = None


# ---------- 路径定位 ----------
def user_plugins_root() -> Path:
    """用户可写的插件目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "plugins"
    return Path(__file__).resolve().parent.parent / "plugins"


def bundled_plugins_root() -> Optional[Path]:
    """打包时用 --add-data 塞进去的插件目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "plugins"
        if p.exists():
            return p
    return None


def _candidate_roots() -> list[Path]:
    roots = []
    user = user_plugins_root()
    if user.exists():
        roots.append(user)
    bundled = bundled_plugins_root()
    if bundled:
        roots.append(bundled)
    return roots


# ---------- 依赖检查 ----------
def _import_name(pkg: str) -> str:
    return _IMPORT_NAME_MAP.get(pkg.lower(), pkg.replace("-", "_"))


def _missing_dependencies(specs: list[str]) -> list[str]:
    """返回无法 import 的依赖名。specs 形如 'pandas>=2.0'。"""
    missing = []
    for spec in specs:
        pkg = (spec.split(">=")[0].split("==")[0]
                   .split("<")[0].split(">")[0].split("!=")[0].strip())
        try:
            importlib.import_module(_import_name(pkg))
        except ImportError:
            missing.append(spec)
    return missing


# ---------- 模块加载 ----------
def _import_plugin_module(plugin_dir: Path, module_name: str):
    """把 plugin_dir 的父目录加入 sys.path，按 <plugin_dir.name>.<module> 导入。"""
    parent = plugin_dir.parent
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))

    full = f"{plugin_dir.name}.{module_name}"
    if full in sys.modules:
        return importlib.reload(sys.modules[full])
    return importlib.import_module(full)


# ---------- 发现 ----------
def _load_spec(plugin_dir: Path) -> Optional[PluginSpec]:
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return PluginSpec(
            dir=plugin_dir, name=plugin_dir.name, version="?",
            entry_module="", entry_class="",
            error=f"plugin.json 解析失败: {e}",
        )

    name = manifest.get("name", plugin_dir.name)
    version = manifest.get("version", "0.0.0")
    entry = manifest.get("entry", "")
    deps = manifest.get("dependencies", [])

    if ":" not in entry:
        return PluginSpec(
            dir=plugin_dir, name=name, version=version,
            entry_module="", entry_class="",
            error="plugin.json 缺少合法的 entry 字段（应为 '模块:类名'）",
        )

    mod_name, cls_name = entry.split(":", 1)

    missing = _missing_dependencies(deps)
    if missing:
        return PluginSpec(
            dir=plugin_dir, name=name, version=version,
            entry_module=mod_name, entry_class=cls_name, dependencies=deps,
            error=f"缺少依赖：{', '.join(missing)}",
        )

    try:
        mod = _import_plugin_module(plugin_dir, mod_name)
        cls = getattr(mod, cls_name)
    except Exception as e:
        logger.exception("插件 %s 加载失败", plugin_dir.name)
        return PluginSpec(
            dir=plugin_dir, name=name, version=version,
            entry_module=mod_name, entry_class=cls_name, dependencies=deps,
            error=f"加载失败: {e}",
        )

    return PluginSpec(
        dir=plugin_dir, name=name, version=version,
        entry_module=mod_name, entry_class=cls_name,
        dependencies=deps, plugin_class=cls,
    )


def discover_plugins() -> list[PluginSpec]:
    """按优先级合并用户目录和内置目录，同名以用户目录为准。"""
    seen: set[str] = set()
    specs: list[PluginSpec] = []
    for root in _candidate_roots():
        for plugin_dir in sorted(root.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith((".", "_")):
                continue
            if plugin_dir.name in seen:
                continue
            spec = _load_spec(plugin_dir)
            if spec:
                specs.append(spec)
                seen.add(plugin_dir.name)
    return specs


def instantiate_plugin(spec: PluginSpec, ctx: PluginContext) -> Optional[Plugin]:
    if spec.plugin_class is None:
        return None
    try:
        return spec.plugin_class(ctx)
    except Exception:
        logger.exception("插件 %s 实例化失败", spec.name)
        return None