# 施工蓝图: deployment & SRE playbook
# 目标文件: docs/deploy/**, deploy/systemd/**, charts/pystatsd/**, scripts/pystatsdctl

## 1. 核心目标
- 提供从开发/测试到生产的可重复部署路径（systemd、Docker/Compose、Kubernetes/Helm）。
- 交付容量规划、扩缩容策略、运行/升级/故障处理手册，作为 SRE 执行依据。
- 确保安全与合规要求（镜像签名、SBOM、最小权限）。

## 2. 制品与打包
- PyPI 包：`pystatsd-helix`，包含 CLI、守护线程及 backends。
- OCI 镜像：`ghcr.io/org/pystatsd-helix:<version>`，基于 `python:3.12-slim`，预装 `uvloop`, `hdrhistogram`, `fastdigest`, `structlog`, `opentelemetry`。
- Multi-arch：amd64 + arm64（Buildx）。
- SBOM via `syft`，签名 via `cosign`。发布流水线会上传 SBOM + 签名到 Artifact Registry。

## 3. 参考部署
### 3.1 systemd
- Unit 存于 `deploy/systemd/pystatsd.service`，运行用户 `pystatsd`（非 root）。
- `LimitNOFILE=200000`, `Restart=on-failure`, `EnvironmentFile=/etc/pystatsd/pystatsd.env`。
- 日志到 journald + 可选文件 handler（配合 LoggerBackend）。

### 3.2 Kubernetes/Helm
- Chart `charts/pystatsd`：Deployment（master+workers 同一 Pod）或 master Deployment + worker StatefulSet（P2）。
- ConfigMap/Secret 提供 config 与密钥。
- Service：UDP LoadBalancer (8125) + HTTP 控制/metrics (8080)。
- Pod 安全：`runAsNonRoot`, `allowPrivilegeEscalation=false`, 可选 `NET_BIND_SERVICE` capability。
- Probes：`/health/live` (liveness), `/health/ready` (readiness)。
- HPA：基于 `gateway_queue_utilization` + `flush_latency_p95`。

### 3.3 Docker Compose（开发）
- `docker-compose.yaml` 包括 `pystatsd`, `fake_graphite`, `loadgen`，便于集成测试。

## 4. 容量规划
| 流量 | 推荐 worker | CPU | 内存 | 备注 |
| --- | --- | --- | --- | --- |
| 50k pkt/s | 4 | 2 cores | 4 GiB | MVP baseline |
| 100k pkt/s | 8 | 4 cores | 8 GiB | 需 NIC offload |
| 200k pkt/s | 12 | 6 cores | 12 GiB | 建议水平扩展 |
公式：`workers = ceil(qps / 12.5k)`，留 20% 余量。

## 5. 扩缩容与 SRE 操作
- **纵向扩展**：修改 `config.runtime.workers`，重启 master。
- **横向扩展**：部署多节点，前置 UDP LB (AWS NLB/GCP ILB)。如需 sticky routing，建议客户端按照 namespace 分片。
- **自动扩容**：HPA/Cluster Autoscaler 结合 ingest/drop 指标；提供示例 manifest。
- **例行操作**：
  - Daily：检查仪表板、确认告警静默。
  - Weekly：review config diff、清理旧日志。
  - Monthly：跑性能基准刷新容量模型。
- **升级流程**：Stage -> Canary (10% traffic for 30m) -> Rolling update。失败回滚 `helm rollback` 或 `systemctl restart` 到旧版本。
- **Drain/Shutdown**：`pystatsdctl drain --timeout 60` 触发 worker 自然退出；紧急情况 SIGTERM -> 30s -> SIGKILL。

## 6. 事故响应
- Runbook 模板：症状、即时动作、诊断、缓解、根因、后续任务。
- 示例：“Gateway drops spike”：检查 `pystatsd_gateway_dropped_total`，若>1% 5m-> Critical；扩 worker 或查网络。
- PagerDuty/Alertmanager integration：所有 Critical 告警必须附上 runbook 链接。

## 7. 安全与合规
- Secrets 通过 env/secret manager 注入，禁止硬编码。
- TCP ingest/backends 可启用 mTLS；证书轮转流程文档化。
- 默认非特权端口，若需 8125/udp <1024，分配 `CAP_NET_BIND_SERVICE`。
- 漏洞扫描周频，补丁 SLA 30 天。

## 8. Legacy 对照 (08_deployment_and_sre_playbook.md)
- 旧文稿中的 systemd/K8s/Compose 指南、容量表、扩缩容/升级/incident 步骤已吸收到上文；我们额外声明镜像签名与 SBOM 作为新硬性要求。
- 未实现的“持久化 flush 队列” 保持为开放问题，需另行 ADR。
- 旧文件将替换成 stub，指向本蓝图。
