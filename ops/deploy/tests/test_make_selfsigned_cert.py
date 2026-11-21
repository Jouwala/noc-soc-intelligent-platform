"""make-selfsigned-cert.py 的证书落点探测测试（#202 跟进，补 #209 评审观察 1）。

覆盖 `_default_cert_dir` 的 5 个探测用例（PR #209 称「5/5」，本文件即其依据）
与 `main()` 的两条确定性路径。只用标准库 + pytest，不依赖 cryptography
（缺 cryptography 的那条路径与环境有关，不在此断言）。

跑法（仓库根）：
  python -m pytest ops/deploy/tests/test_make_selfsigned_cert.py -v

注意：本文件不在交付包里（zip-manifest 只按文件白名单发 ops/deploy 的
运行脚本；测试文件不在白名单内）。CI 暂无专门跑 ops/deploy 测试的作业，
本文件目前靠人工跑与 compileall 的语法检查。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "make-selfsigned-cert.py"


def _load():
    """按路径加载被测脚本（文件名带连字符，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location("make_selfsigned_cert_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


# --- _default_cert_dir：5 个探测用例 ---


def test_linux_sbin(tmp_path):
    """Linux 随包布局：二进制在 sbin/ 下 → runtime/nginx-1.28.0/certs。"""
    _touch(tmp_path / "runtime" / "nginx-1.28.0" / "sbin" / "nginx")
    assert _load()._default_cert_dir(tmp_path) == tmp_path / "runtime" / "nginx-1.28.0" / "certs"


def test_windows_exe(tmp_path):
    """Windows 布局：版本目录根的 nginx.exe → 同目录 certs。"""
    _touch(tmp_path / "nginx-1.28.0" / "nginx.exe")
    assert _load()._default_cert_dir(tmp_path) == tmp_path / "nginx-1.28.0" / "certs"


def test_root_flat_nginx(tmp_path):
    """版本目录根的平铺 nginx → 同目录 certs。"""
    _touch(tmp_path / "nginx-1.28.0" / "nginx")
    assert _load()._default_cert_dir(tmp_path) == tmp_path / "nginx-1.28.0" / "certs"


def test_no_nginx_raises(tmp_path):
    """两处都找不到 nginx → 抛 FileNotFoundError，不再静默写错地方。"""
    with pytest.raises(FileNotFoundError, match="找不到 nginx"):
        _load()._default_cert_dir(tmp_path)


def test_root_first_when_both_present(tmp_path):
    """两处都有时版本根优先（与 noc-paths.sh 同序）。"""
    _touch(tmp_path / "nginx-1.28.0" / "nginx.exe")
    _touch(tmp_path / "runtime" / "nginx-1.28.0" / "sbin" / "nginx")
    assert _load()._default_cert_dir(tmp_path) == tmp_path / "nginx-1.28.0" / "certs"


# --- main()：两条确定性路径（子进程跑，避免改动本进程的 argv） ---


def test_main_dir_with_existing_cert_skips(tmp_path):
    """--dir 且证书已存在 → 打印跳过，exit 0。"""
    _touch(tmp_path / "portal.crt")
    _touch(tmp_path / "portal.key")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "跳过" in proc.stdout


def test_main_no_nginx_no_dir_exits_1(tmp_path):
    """无 --dir 且脚本根目录下找不到 nginx → 干净报错 exit 1，无 traceback。"""
    script_root = SCRIPT.parents[2]
    if (script_root / "nginx-1.28.0").exists() or (
        script_root / "runtime" / "nginx-1.28.0"
    ).exists():
        pytest.skip("本机仓库根已有 nginx 目录，探测会走生成路径")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    assert "找不到 nginx" in proc.stderr
    assert "Traceback" not in proc.stderr
