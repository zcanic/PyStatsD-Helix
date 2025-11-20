# 施工蓝图: worker
# 目标文件: src/pystatsd_helix/worker.py

## 1. 核心目标
为每个 worker 进程提供运行时骨架：初始化事件循环、绑定 UDP、创建协议对象、维护 flush/health 任务，并处理有序关闭。需严格遵守 Shared-Nothing 原则：每个 worker 拥有独立内存空间及后端实例。

## 2. 架构指令
- **uvloop 强制要求**：在 worker 进程内调用 `asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())`; 若导入失败即退出，防止“生产环境偷偷退回默认 loop”。
- **SO_REUSEPORT**：借助 `loop.create_datagram_endpoint(..., reuse_port=True)`；如果操作系统不支持，应记录 FATAL 并终止（宁可 fail fast）。
- **背景任务与 flush 调度分离**：`Worker.run` 仅负责主事件循环，flush 通过 `asyncio.create_task(self._flush_loop())` 实现；确保 flush 错误不会停止 packet 接收。
- **Skeptic 对照**：旧 StatsD worker 会将 aggregator 放在单线程 queue；我们强调 aggregator 与 transport 在同一 loop 内同步运行，避免 context switch。

## 3. 关键依赖
- `asyncio`, `contextlib`, `signal`, `socket`
- 内部：`transport.DatagramProtocol`, `parser.StatsDParser`, `aggregator.Aggregator`, `backends.loader.create_backends`, `config.ServerConfig`

## 4. API 与类设计
```python
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Sequence

from .config import ServerConfig
from .aggregator import Aggregator
from .transport import StatsDProtocol
from .parser import StatsDParser
from .backends.loader import create_backends

class Worker:
    def __init__(self, worker_id: int, config: ServerConfig) -> None: ...
    async def run(self) -> None: ...
    async def _start_server(self) -> None: ...
    async def _flush_loop(self) -> None: ...
    async def _shutdown(self) -> None: ...

def run_worker_process(config: ServerConfig, worker_id: int) -> None: ...
```

## 5. 详细实现逻辑
1. **`run_worker_process`**
   - 设置 `WORKER_ID` 环境变量，便于日志过滤。
   - 重新配置 logging handler，加上 `[worker-{id}]` 前缀。
   - 安装 `asyncio.Runner(loop_factory=uvloop.new_event_loop)`，调用 `Runner.run(Worker(...).run())`。
2. **Worker 初始化**
   - 构建 `StatsDParser`（bytes-only），再创建 `Aggregator(parser, histogram_cfg=config.timer_histogram_config)`。
   - 使用 `create_backends(config)` 创建后端实例列表；每个 backend 在 worker 级别初始化，禁止跨 worker 共享连接。
3. **`run`**
   - 调用 `_start_server()`：
     - `transport = StatsDProtocol(parser, aggregator)`
     - `await loop.create_datagram_endpoint(lambda: transport, local_addr=(config.host, config.port), reuse_port=True)`
   - 创建 flush 任务：`self._flush_task = asyncio.create_task(self._flush_loop())`
   - 等待 `self._shutdown_event.wait()`；该事件在 `SIGTERM` handler 或 `Aggregator.fatal_error` 中触发。
4. **Flush 循环**
   - 使用 jitter：`interval = config.flush_interval * random.uniform(0.95, 1.05)`。
   - `await asyncio.sleep(interval)` -> `await aggregator.flush(backends)`。
   - 捕获异常：记录 `exc_info=True`; 若连续失败超过阈值（默认 3），调用 `_shutdown_event.set()`，让 worker 退出，由主进程决定是否重启。
5. **关闭流程**
   - 在 `_shutdown` 中：
     - 取消 flush 任务并抑制 `CancelledError`。
     - `transport.close()`，等待 `asyncio.sleep(0)` 让 loop 处理关闭。
     - 调用 `backend.close()`（如异步则 await）。
   - `Aggregator` 不需要额外关闭，但可以暴露 `async def drain()` 确保缓冲指标 flush 一次。
6. **Backpressure/过载策略**
   - `StatsDProtocol` 在解析失败时直接丢弃，并增加一个 `parser_error_count` 指标。
   - 若 `aggregator.queue_size` 超过阈值（默认 50k metrics），记录 WARN 并进行采样丢弃。

## 6. 性能与错误处理
- **性能 KPI**：单 worker 需在 99% CPU 利用率下仍维持 <1% packet drop；`uvloop + reuse_port` 是必需条件。
- **错误隔离**：任何 backend flush 的异常不应阻塞 datagram 接收；使用 `asyncio.shield` 或在 flush 中批量并发执行。
- **监控**：为每个 worker 记录 `worker.started`, `worker.shutdown_reason`, `aggregator.flush_duration_ms`, `parser.error_total`，以便 SRE 在共享后端观察差异。
- **自愈**：Worker 自身不重启；只负责向 master 提交非零退出码。老版本中 worker 会尝试内部重启导致 zombie，我们明确禁止。

## 7. 心跳、诊断与 Legacy 映射
- **心跳实现:** 若部署要求 master 收集 worker 心跳，可在 `Worker.run()` 内创建 `asyncio.create_task(self._emit_heartbeat())`，周期性汇报 ingest/flush 指标到 `control_queue` 或 logger。此任务不得依赖共享内存，只能写入 `multiprocessing.Queue` 或日志，由主进程异步消费。
- **Diagnostics Hook:** 参照旧 `01_runtime_topology.md`，保留 `Worker.dump_state()`（同步函数即可）以便 SIGUSR1 时打印 aggregator shard 状态。默认实现可以是空操作，避免无谓开销。
- **Legacy 拒绝项:** 旧蓝图要求 worker 与 master 共享 Unix socket 以接受 config 更新；本实施方案仅支持“终止并重启”模式。若未来要添加热更新，需将控制消息反序列化为新的 `ServerConfig` 并调用 `self._reload_event`，否则禁止在 worker 侧修改共享对象。