# -*- coding: utf-8 -*-
"""PRTG 采集配置解析（Issue #227）。

地址与凭据分两处维护，本模块是唯一合并入口：

  地址  环境变量 ``PRTG_BASE_URL`` > ``backend/services/prtg_config.json``
        （页面可编辑，字段 base_url，缺失时由 host/port 拼）
  凭据  环境变量 ``PRTG_USERNAME`` / ``PRTG_PASSHASH`` > 凭据设置页 ``prtg`` 组
        （``credential_store.get_credential("prtg")``，缓存 30 秒）

口令字段是 **PRTG Passhash**（Setup → Account Settings），不是登录密码。
本模块与日志只出现键名，不落任何值。
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from backend.services.credential.credential_store import get_credential
from backend.services.prtg.client import PrtgConfig, PrtgConfigError

SERVICES_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SERVICES_DIR / "prtg_config.json"

ENV_BASE_URL = "PRTG_BASE_URL"
ENV_ENABLED = "PRTG_COLLECTOR_ENABLED"
ENV_POLL_SECONDS = "PRTG_POLL_SECONDS"
ENV_BACKFILL_PER_MINUTE = "PRTG_BACKFILL_PER_MINUTE"
ENV_RAW_RETENTION_DAYS = "PRTG_RAW_RETENTION_DAYS"

DEFAULT_POLL_SECONDS = 60
DEFAULT_BACKFILL_PER_MINUTE = 5
DEFAULT_RAW_RETENTION_DAYS = 90


@dataclass(frozen=True)
class PrtgSettings:
    """采集服务运行参数（凭据只在内存里，repr 不输出）。"""

    config: PrtgConfig
    poll_seconds: int = DEFAULT_POLL_SECONDS
    backfill_per_minute: int = DEFAULT_BACKFILL_PER_MINUTE
    raw_retention_days: int = DEFAULT_RAW_RETENTION_DAYS

    def __repr__(self) -> str:  # 防止误把 passhash 打进日志
        return (
            f"PrtgSettings(base_url={self.config.base_url!r}, "
            f"poll_seconds={self.poll_seconds}, "
            f"backfill_per_minute={self.backfill_per_minute}, "
            f"raw_retention_days={self.raw_retention_days})"
        )


def read_config_file(path: Path | None = None) -> dict:
    """读 prtg_config.json；缺失或损坏返回空 dict（不抛，交给上层报缺字段）。"""
    p = path or CONFIG_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_base_url(config_file: dict, environ: dict | None = None) -> str:
    """地址优先级：环境变量 > 配置文件 base_url > host:port。"""
    env = os.environ if environ is None else environ
    explicit = (env.get(ENV_BASE_URL) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = str(config_file.get("base_url") or "").strip()
    if base:
        return base.rstrip("/")
    host = str(config_file.get("host") or "").strip()
    if not host:
        raise PrtgConfigError(f"未配置 PRTG 地址：请在 {CONFIG_PATH.name} 填 base_url 或 host")
    port = int(config_file.get("port") or 80)
    return f"http://{host}:{port}"


def load_settings(environ: dict | None = None, config_file: dict | None = None) -> PrtgSettings:
    """合并地址与凭据；缺 Passhash 时抛 PrtgConfigError（消息只带键名）。"""
    env = os.environ if environ is None else environ
    cfg = read_config_file() if config_file is None else config_file
    base_url = resolve_base_url(cfg, env)
    username, passhash = get_credential("prtg")
    if not passhash:
        raise PrtgConfigError(
            "PRTG Passhash 为空：请在设置页「PRTG 流量 / 传感器采集」组填 Passhash"
            "（或设置环境变量 PRTG_PASSHASH），不要写入代码"
        )
    config = PrtgConfig(base_url=base_url, username=username, passhash=passhash)
    return PrtgSettings(
        config=config,
        poll_seconds=_positive_int(env.get(ENV_POLL_SECONDS), DEFAULT_POLL_SECONDS),
        backfill_per_minute=_positive_int(env.get(ENV_BACKFILL_PER_MINUTE), DEFAULT_BACKFILL_PER_MINUTE),
        raw_retention_days=_positive_int(env.get(ENV_RAW_RETENTION_DAYS), DEFAULT_RAW_RETENTION_DAYS),
    )


def collector_enabled(environ: dict | None = None) -> bool:
    """是否常驻采集。默认关闭，生产 backend/.env 里显式 PRTG_COLLECTOR_ENABLED=1 才启动。

    默认关闭是为了让 CI / 冒烟 / 单测环境不会去连 PRTG 内网；生产必须显式打开。
    """
    env = os.environ if environ is None else environ
    return (env.get(ENV_ENABLED) or "0").strip().lower() in ("1", "true", "yes", "on")


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
