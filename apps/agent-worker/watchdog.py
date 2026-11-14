"""Worker 看门狗（TASK-03）。

职责：拉起 Worker 子进程，周期性读心跳文件；心跳过期（Worker 卡死）
或进程退出即重启，重启次数有上限，超限退出并报告。心跳文件由 Worker
每个轮询周期刷新（见 worker.py 的 _write_heartbeat）。

设计成可注入 process_factory / clock / sleep，离线自测不需要真的等时间流逝。
在 Windows 上直接 `python watchdog.py --agent muse-2 ...` 运行，
与 Worker 一起随任务计划/看门狗脚本常驻。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


@dataclass
class WatchdogConfig:
    stale_after_s: float = 180.0     # 心跳超过这个秒数没更新 → 判定卡死
    check_interval_s: float = 15.0   # 看门狗自身检查间隔
    max_restarts: int = 5            # 累计重启上限，超限放弃并以非零码退出
    start_grace_s: float = 60.0      # 刚拉起后的宽限期（还没写出首个心跳不算卡死）


class Watchdog:
    def __init__(
        self,
        worker_cmd: list[str],
        heartbeat_file: Path,
        config: WatchdogConfig | None = None,
        process_factory: Callable[..., object] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.worker_cmd = worker_cmd
        self.heartbeat_file = Path(heartbeat_file)
        self.config = config or WatchdogConfig()
        self.process_factory = process_factory
        self.clock = clock
        self.sleep = sleep
        self.log = log
        self.restarts = 0
        self._proc: Optional[object] = None
        self._started_at = 0.0

    # -- 心跳 ---------------------------------------------------------------------
    def heartbeat_age(self) -> Optional[float]:
        """心跳文件里的时间戳距今秒数；文件不存在/不可解析返回 None。"""
        try:
            raw = self.heartbeat_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            ts = datetime.fromisoformat(data["at"]).timestamp()
        except Exception:
            return None
        return self.clock() - ts

    def _is_stale(self) -> bool:
        age = self.heartbeat_age()
        if age is None:
            return self.clock() - self._started_at > self.config.start_grace_s
        return age > self.config.stale_after_s

    # -- 进程管理 -------------------------------------------------------------------
    def _start(self) -> None:
        self._proc = self.process_factory(self.worker_cmd)
        self._started_at = self.clock()
        self.log(f"[watchdog] Worker 已拉起：{' '.join(self.worker_cmd)}")

    def _stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        self._proc = None

    def _restart(self, reason: str) -> bool:
        """重启 Worker；超过上限返回 False 表示放弃。"""
        if self.restarts >= self.config.max_restarts:
            self.log(f"[watchdog] 重启已达上限 {self.config.max_restarts} 次，放弃：{reason}")
            return False
        self.restarts += 1
        self.log(f"[watchdog] 第 {self.restarts} 次重启：{reason}")
        self._stop()
        self._start()
        return True

    # -- 主循环 -----------------------------------------------------------------------
    def run(self, max_cycles: Optional[int] = None) -> int:
        """返回进程退出码：0=被外部要求停（max_cycles 到达），2=重启超限放弃。"""
        self._start()
        cycles = 0
        while True:
            self.sleep(self.config.check_interval_s)
            cycles += 1
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                if not self._restart(f"Worker 进程已退出（exit={proc.poll()}）"):
                    return 2
            elif self._is_stale():
                if not self._restart("Worker 心跳过期（疑似卡死）"):
                    return 2
            if max_cycles is not None and cycles >= max_cycles:
                self._stop()
                return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Worker 看门狗")
    parser.add_argument("--agent", default="muse-2", help="Worker 身份（决定心跳文件名）")
    parser.add_argument("--repo-root", default=".", help="仓库根目录")
    parser.add_argument("--stale-after", type=float, default=180.0)
    parser.add_argument("--max-restarts", type=int, default=5)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    heartbeat = repo_root / ".agent-worktrees" / f".heartbeat-{args.agent}.json"
    worker_py = Path(__file__).resolve().parent / "worker.py"
    cmd = [sys.executable, str(worker_py), "--agent", args.agent, "--repo-root", str(repo_root)]
    config = WatchdogConfig(stale_after_s=args.stale_after, max_restarts=args.max_restarts)
    return Watchdog(cmd, heartbeat, config).run()


if __name__ == "__main__":
    raise SystemExit(main())
