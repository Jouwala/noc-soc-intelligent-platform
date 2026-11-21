"""pict SSH 登录后 su - root 提权，只读拉取 OCAuto 等 pict 无权直读的文件。

背景（2026-11-20）：备件 SFTP 账号改为 pict，主目录 /home/pict；
OCAuto/OCAutoOutput 仍在 /home/ubuntu 下，pict SFTP 读报 Permission denied。
root 不能直接 SFTP 口令登录，现场流程是：SSH 登录 pict → `su - root` → 再读文件。

约定：
- pict 口令走凭据分组 `sftp_sp`
- root（su）口令走凭据分组 `sftp_sp_root`（只存口令；用户名固定 root）
- 拉数方式：root `cp` 到 `/tmp`（644）→ pict SFTP get → root 删临时文件
"""
from __future__ import annotations

import logging
import os
import shlex
import socket
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SFTP_HOST_DEFAULT = "10.100.20.11"
SFTP_PORT_DEFAULT = 22
SU_TIMEOUT = 60
FIND_TIMEOUT = 90
FETCH_TIMEOUT = 120


def _runtime_pict() -> Tuple[str, str]:
    from backend.services.credential.credential_store import get_credential
    return get_credential("sftp_sp")


def _runtime_root_secret() -> str:
    from backend.services.credential.credential_store import get_credential
    _user, secret = get_credential("sftp_sp_root")
    return secret or ""


def _host_port() -> Tuple[str, int]:
    host, port = SFTP_HOST_DEFAULT, SFTP_PORT_DEFAULT
    try:
        from backend.services.task.task_config_files import load_json_config
        conf = load_json_config(
            Path(__file__).resolve().parent.parent / "sp_sftp_config.json") or {}
        host = conf.get("sftp_host") or host
        port = int(conf.get("sftp_port") or port)
    except Exception:  # noqa: BLE001
        logger.debug("读取 sp_sftp_config.json 失败，用默认主机", exc_info=True)
    return host, port


def _recv_until(chan, needles, timeout: float) -> str:
    """读到任一 needle（或超时）为止；返回累计文本。"""
    if isinstance(needles, str):
        needles = (needles,)
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            chunk = chan.recv(65536).decode("utf-8", "ignore")
            if not chunk:
                break
            buf += chunk
            if any(n in buf for n in needles):
                return buf
        else:
            time.sleep(0.05)
    return buf


def _recv_until_marker_line(chan, marker: str, timeout: float) -> str:
    """读到「整行等于 marker」为止。

    不能用简单的 `marker in buf`：我们发的命令里本身带 `echo MARKER`，
    shell 回显会先命中一次，导致还没跑完就截断。
    """
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            chunk = chan.recv(65536).decode("utf-8", "ignore")
            if not chunk:
                break
            buf += chunk
            for line in buf.replace("\r\n", "\n").split("\n"):
                if line.strip() == marker:
                    return buf
        else:
            time.sleep(0.05)
    return buf


def _open_root_shell(host: str, port: int, pict_user: str, pict_pwd: str, root_pwd: str):
    """SSH 登录 pict，交互式 su - root。返回 (client, chan)。失败抛 RuntimeError。"""
    import paramiko

    if not root_pwd:
        raise RuntimeError("凭据分组 sftp_sp_root 口令为空，无法 su - root")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host, port=port, username=pict_user, password=pict_pwd,
            timeout=30, banner_timeout=30, auth_timeout=30,
            allow_agent=False, look_for_keys=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"pict SSH 登录失败（{pict_user}@{host}:{port}）："
            f"{type(exc).__name__}: {exc}") from exc

    chan = client.invoke_shell(term="vt100", width=200, height=40)
    chan.settimeout(1.0)
    banner = _recv_until(chan, ("$", "#", ">"), SU_TIMEOUT)
    if not banner.strip():
        client.close()
        raise RuntimeError("pict SSH shell 无提示符")

    chan.send("su - root\n")
    prompt = _recv_until(chan, ("Password:", "password:", "#"), SU_TIMEOUT)
    if "Password:" in prompt or "password:" in prompt:
        chan.send(root_pwd + "\n")
        after = _recv_until(chan, ("#", "Authentication failure", "su:"), SU_TIMEOUT)
    else:
        after = prompt

    low = after.lower()
    if "authentication failure" in low or "sorry" in low or (
            "su:" in low and "fail" in low):
        client.close()
        raise RuntimeError("su - root 认证失败（检查 sftp_sp_root 口令）")
    if "#" not in after:
        client.close()
        raise RuntimeError(f"su - root 后未拿到 root 提示符：{after[-200:]!r}")
    return client, chan


def _root_exec(chan, command: str, timeout: float) -> str:
    """在已提权的 shell 上执行一条命令，用唯一 marker 截取 stdout。"""
    marker = f"__SP_SU_{os.getpid()}_{int(time.time() * 1000)}__"
    wrapped = f"sh -c {shlex.quote(command)} 2>&1; echo {marker}"
    chan.send(wrapped + "\n")
    buf = _recv_until_marker_line(chan, marker, timeout)
    lines = buf.replace("\r\n", "\n").split("\n")
    marker_idx = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            marker_idx = i
            break
    if marker_idx is None:
        raise RuntimeError(f"root 命令超时（{timeout}s）：{command[:120]}")
    body_lines = lines[:marker_idx]
    out_lines = []
    skipping = True
    for line in body_lines:
        if skipping:
            if wrapped[:40] in line or "echo " + marker in line:
                continue
            if not line.strip():
                continue
            skipping = False
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def _parse_find_path(out: str) -> Optional[str]:
    for line in reversed(out.splitlines()):
        line = line.strip()
        if "\t" in line:
            path = line.split("\t", 1)[1].strip()
        else:
            path = line
        if path.startswith("/") and path.lower().endswith(".csv"):
            return path
    return None


def _sftp_pict_get(remote_file: str, local_path: Path) -> None:
    """pict SFTP 下载单个文件到本地（原子替换）。"""
    import paramiko
    from backend.services.atomic_write import atomic_replace_file

    host, port = _host_port()
    pict_user, pict_pwd = _runtime_pict()
    sock = None
    trans = None
    try:
        sock = socket.create_connection((host, port), timeout=30)
        trans = paramiko.Transport(sock)
        trans.banner_timeout = 30
        trans.start_client(timeout=30)
        trans.auth_password(pict_user, pict_pwd)
        sftp = paramiko.SFTPClient.from_transport(trans)
        if sftp is None:
            raise RuntimeError("SFTP 通道建立失败")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = local_path.with_suffix(local_path.suffix + f".{os.getpid()}.tmp")
        try:
            sftp.get(remote_file, str(tmp))
            atomic_replace_file(str(tmp), str(local_path))
        finally:
            try:
                sftp.close()
            except Exception:  # noqa: BLE001
                logger.debug("关闭 pict SFTP 失败", exc_info=True)
    finally:
        if trans is not None:
            try:
                trans.close()
            except Exception:  # noqa: BLE001
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_latest_ocrule_csv(
    local_path: str | Path,
    remote_folder: str = "/home/ubuntu/OCAuto/OCAutoOutput",
) -> str:
    """找最新 ocrule csv，经 /tmp 中转拉到本地并转 xlsx。返回远端源路径。"""
    import pandas as pd
    from backend.services.atomic_write import atomic_to_excel

    host, port = _host_port()
    pict_user, pict_pwd = _runtime_pict()
    root_pwd = _runtime_root_secret()
    folder = remote_folder.rstrip("/") or "/home/ubuntu/OCAuto/OCAutoOutput"
    stage = f"/tmp/sp_ocrule_fetch_{os.getpid()}.csv"
    remote: Optional[str] = None

    client = None
    try:
        client, chan = _open_root_shell(host, port, pict_user, pict_pwd, root_pwd)
        find_cmd = (
            f"find {shlex.quote(folder)} -type f -iname '*ocrule*.csv' "
            f"-printf '%T@\\t%p\\n' 2>/dev/null | sort -nr | head -1"
        )
        out = _root_exec(chan, find_cmd, FIND_TIMEOUT)
        remote = _parse_find_path(out or "")
        if not remote:
            raise FileNotFoundError(f"root 下未找到 ocrule csv：{folder}")
        _root_exec(
            chan,
            f"cp -f {shlex.quote(remote)} {shlex.quote(stage)} "
            f"&& chmod 644 {shlex.quote(stage)}",
            FETCH_TIMEOUT,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                logger.debug("关闭 su-root SSH 失败", exc_info=True)

    final = Path(local_path)
    raw_csv = final.with_suffix(".csv.fetch.tmp")
    try:
        _sftp_pict_get(stage, raw_csv)
        df = pd.read_csv(raw_csv)
        final.parent.mkdir(parents=True, exist_ok=True)
        atomic_to_excel(df, str(final), index=False)
    finally:
        try:
            raw_csv.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            client2, chan2 = _open_root_shell(
                host, port, pict_user, pict_pwd, root_pwd)
            try:
                _root_exec(chan2, f"rm -f {shlex.quote(stage)}", SU_TIMEOUT)
            finally:
                client2.close()
        except Exception:  # noqa: BLE001
            logger.warning("清理远端临时文件失败: %s", stage, exc_info=True)

    return remote  # type: ignore[return-value]
