"""模块加载引导（TASK-03）。

仓库按 Issue #18 的改动文件清单把 Worker 放在 apps/agent-worker/、
适配器放在 apps/control-plane/backend/adapters/——两个目录名都带连字符，
不是合法 Python 包名，无法用包导入。因此这里用文件路径显式加载，
模块间只通过这里注册的名字互相引用，不写 sys.path  hack。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"无法加载模块 {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_adapter() -> ModuleType:
    return _load(
        "github_issue",
        _REPO_ROOT / "apps" / "control-plane" / "backend" / "adapters" / "github_issue.py",
    )


def load_sibling(name: str) -> ModuleType:
    return _load(f"agent_worker_{name}", _HERE / f"{name}.py")


def repo_root() -> Path:
    return _REPO_ROOT
