# PyStatsD-Helix 运维手册 (Runbook)

## 1. 监控与告警

### 1.1 关键指标 (Prometheus)

| 指标名称 | 类型 | 描述 | 告警阈值 |
| :--- | :--- | :--- | :--- |
| `pystatsd_gateway_packets_total` | Counter | 接收到的 UDP 数据包总数 | rate(5m) == 0 (服务中断) |
| `pystatsd_gateway_errors_total` | Counter | 数据包处理错误数 | rate(5m) > 0 (解析错误或缓冲区溢出) |
| `pystatsd_worker_heartbeat_total` | Counter | Worker 进程心跳 | rate(1m) == 0 (Worker 卡死) |
| `pystatsd_aggregator_series_count` | Gauge | 当前活跃的时间序列数量 | > 80% max_series (基数爆炸风险) |
| `pystatsd_backend_flush_time_seconds` | Histogram | 后端 Flush 耗时 | P99 > 5s (后端写入慢) |

### 1.2 健康检查
- **Liveness Probe**: `GET /health/live`
    - 返回 200 OK 表示 HTTP 服务存活。
    - 用于 K8s 重启策略。
- **Readiness Probe**: `GET /health/ready`
    - 返回 200 OK 表示所有 Worker 进程存活且心跳正常。
    - 返回 503 Service Unavailable 表示有 Worker 死亡或卡死（超过 3 个 flush 周期未更新心跳）。
    - 用于 K8s 流量切入（虽然 UDP 是无连接的，但这影响 Service IP 的 Endpoint 列表）。

## 2. 故障排查 (Troubleshooting)

### 2.1 丢包率高
**现象**: `pystatsd_gateway_packets_total` 增长慢于预期，或客户端报错。
**排查步骤**:
1. 检查系统 UDP 缓冲区丢包: `netstat -s | grep "packet receive errors"` (Linux) 或 `netstat -e` (Windows)。
2. 检查日志中是否有 `Failed to set SO_RCVBUF` 警告。
3. 增加 `config.toml` 中的 `socket_buffer_size` (默认 4MB)。
4. 增加 `num_workers` (如果 CPU 未跑满)。

### 2.2 Worker 频繁重启
**现象**: 日志出现 `Worker ... died unexpectedly`。
**排查步骤**:
1. 检查内存使用率，是否触发 OOM Killer。
2. 检查 `max_series` 配置，过高可能导致内存溢出。
3. 检查后端连接是否超时导致 Worker 阻塞崩溃（虽然是异步的，但大量积压可能导致问题）。

### 2.3 内存泄漏
**现象**: 内存持续增长不释放。
**排查步骤**:
1. 检查 `pystatsd_aggregator_series_count` 是否持续增长。
2. 确认是否收到大量随机 Tag 的指标（基数爆炸）。
3. 调整 `max_series` 进行限制。

## 3. 常用命令

**启动服务**:
```bash
python -m pystatsd_helix.main --config config.toml
```

**查看指标**:
```bash
curl http://localhost:9102/metrics
```

**健康检查**:
```bash
curl -v http://localhost:9102/health/ready
```
