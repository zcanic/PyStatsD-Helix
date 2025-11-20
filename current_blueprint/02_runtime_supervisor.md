# 施工蓝图: main
# 目标文件: src/pystatsd_helix/main.py

## 1. 核心目标
实现 CLI/进程编排入口，负责配置加载、logging 初始化、worker 进程生命周期管理与健康信号传播。该模块不得包含业务逻辑，仅扮演 orchestrator；任何指标处理均属于 `worker`/`aggregator` 范畴。

## 2. 架构指令
- **清洁启动**：使用 `multiprocessing.set_start_method("spawn", force=True)`，确保 worker 没有父进程残留状态。
- **配置一次性加载**：`main` 调用 `config.load_config`，然后 `ServerConfig.model_copy(deep=True)` 传入每个 worker，禁止 worker 向主进程请求配置。
- **日志与信号统一**：主进程设定 `logging.basicConfig(level=config.log_level)`，安装 `SIGTERM/SIGINT` 处理器并驱动“优雅关停 -> 超时 -> 强制结束”的顺序。
- **Skeptic 要求**：必须记录我们在老架构中遇到的三个典型失败：① 忘记 join 导致僵尸进程；② 在 `fork` + `uvloop` 下崩溃；③ 主进程在 worker 自旋时过度重启。新实现必须对照写出防线。

## 3. 关键依赖
- 标准库：`argparse`, `logging`, `multiprocessing`, `signal`, `sys`, `time`。
- 内部模块：`config.load_config`, `worker.run_worker_process`。

## 4. API 与函数设计
```python
import argparse
import logging
import multiprocessing as mp
import signal
import sys
from typing import List

from .config import ServerConfig, load_config
from .worker import run_worker_process

SHUTDOWN_TIMEOUT = 10.0

def build_cli() -> argparse.ArgumentParser: ...

def main(argv: list[str] | None = None) -> int: ...

class WorkerProcess(mp.Process):
    def __init__(self, worker_id: int, config: ServerConfig) -> None: ...

class Supervisor:
    def __init__(self, config: ServerConfig) -> None: ...
    def start(self) -> None: ...
    def stop(self, reason: str, timeout: float = SHUTDOWN_TIMEOUT) -> None: ...
    def restart_dead_workers(self) -> None: ...  # 默认关闭，只记录日志
```

## 5. 详细实现逻辑
1. **CLI 解析**
   - 支持 `--config/-c`, `--workers/-w`, `--log-level`, `--version`, `--dry-run`。
   - `--workers` 等 CLI 参数组装成 dict 传给 `load_config(cli_overrides=...)`。
2. **配置加载**
   - 捕获 `ConfigError`; 打印友好信息并 `return 2`。
   - 成功后记录配置摘要：监听地址、worker 数、active backends。
3. **Supervisor.start**
   - 根据 `config.get_num_workers()` 计算 `n`; 若 `n==0`，打印 CRITICAL 并退出（老版本曾允许 0，导致 master 空转）。
   - 循环创建 `WorkerProcess(worker_id=i, config=config.model_copy(deep=True))`。
   - 每个子进程 `start()` 前确保 `daemon=False`，否则系统信号无法正确传播。
4. **信号处理**
   - 主进程注册 `signal.signal(SIGTERM/SIGINT, handle_sig)`，`handle_sig` 调用 `Supervisor.stop(reason=f"signal {sig}")`。
   - 在 `stop` 内：
     1. 发送自定义 `shutdown_event`? —— Skeptic 认为没必要；直接 `proc.terminate()` 即可，由于 worker 没有持久连接。
     2. `proc.join(timeout)`；若超时，记录 ERROR 并 `proc.kill()`。
5. **worker 监控**
   - 主循环：`while procs: check proc.is_alive()`；
   - 若发现异常退出：记录 `CRITICAL`，调用 `stop(reason="worker crashed", timeout=SHUTDOWN_TIMEOUT)`；默认不自动重启，但保留 `Supervisor.restart_dead_workers` 以备未来 Feature Flag。
6. **退出码契约**
   - 正常关闭 -> `return 0`。
   - 配置/启动失败 -> `return 2`。
   - Worker 崩溃 -> `return 3`。
   - README 需提醒系统服务（systemd/k8s）据此做健康判定。

## 6. 性能与错误处理
- **性能**：主进程不在热路径，只需保证创建大量 worker 时的 `config.model_copy` 不成为瓶颈（N<=64 时可忽略）。
- **错误处理**：任何子进程异常都必须记录 `worker_id`, `exitcode`；Supervisor 停止流程要捕获 `KeyboardInterrupt`，避免再次抛出造成不一致。
- **防御点**：老实现中 `Process.daemon=True` 导致主进程 crash 时无法清理；现在显式设置 False，并在 `atexit` 中调用 `stop("atexit cleanup")`；同样，拒绝在 `fork` start method 下运行 uvloop。

## 7. 运行期可观测与控制对接
- **心跳通道:** 继承旧 `01_runtime_topology.md` 的经验，主进程每 5s 通过 `multiprocessing.SimpleQueue` 接收 worker 心跳（`worker_id`, `ingest_packets`, `queue_util`, `last_flush_ns`），但我们不再要求集中式队列；实现者可以选择 no-op stub，只要指标通过 Logger/Graphite 写出。若 future 版本需要自动重启，可将 `Supervisor.restart_dead_workers` feature flag 与心跳信号绑定。
- **Drain/Reload:** `pystatsdctl drain` 通过控制平面触发 `Supervisor.stop(reason="drain")`，先标记 `self._accepting=False` 防止新 worker 启动，再等待 flush 队列清空后 `terminate`。Reload 则复用 config 模块的热更新校验，允许只有 `mutable_fields` 改变的场景直接替换 `self._config`，其余则走“滚动重启”路径。
- **IPC 策略:** 旧版要求 Unix socket + JSON 协议；新蓝图明确: master→worker 停止信号仍使用 POSIX 信号即可（最大兼容性），只有在需要热更新时才通过控制平面队列发送 `ControlMessage`。此处需写出接口占位（例如 `send_control_message(worker_id, payload)`），让未来演进不会破坏当前 Minimal 控制面。

## 8. Legacy 映射
- 大部分 `01_runtime_topology.md` 内容（uvloop 预检、SO_REUSEPORT、drain 流程、CPU 亲和）已拆散并落地到 `config`, `worker`, `transport` 与本文件。未采纳的共享队列/自动重启机制在上文 7 节列出替代方案。
- 旧文档将被替换为 stub，引用本蓝图及 `worker` 蓝图。若未来重新引入全量心跳管控，需要在 ADR 中说明对 Shared-Nothing 的影响。