# PyStatsD-Helix 部署指南

## 1. Docker 部署

### Dockerfile
项目根目录已包含 `Dockerfile`。

### 构建镜像
```bash
docker build -t pystatsd-helix:latest .
```

### 运行容器
```bash
docker run -d \
  --name pystatsd \
  -p 8125:8125/udp \
  -p 9102:9102 \
  -v $(pwd)/config.example.toml:/app/config.toml \
  pystatsd-helix:latest \
  --config /app/config.toml
```

**注意**: 在 Linux 宿主机上，建议使用 `--network host` 以获得最佳 UDP 性能。

## 2. Systemd 部署 (Linux)

创建服务文件 `/etc/systemd/system/pystatsd.service`:

```ini
[Unit]
Description=PyStatsD-Helix Metrics Server
After=network.target

[Service]
Type=simple
User=nobody
Group=nogroup
# 假设安装在 /opt/pystatsd 且使用 venv
ExecStart=/opt/pystatsd/venv/bin/python -m pystatsd_helix.main --config /etc/pystatsd/config.toml
Restart=always
RestartSec=5
# 提高文件描述符限制
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

启用并启动:
```bash
systemctl daemon-reload
systemctl enable pystatsd
systemctl start pystatsd
```

## 3. Kubernetes 部署

### ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pystatsd-config
data:
  config.toml: |
    [server]
    host = "0.0.0.0"
    port = 8125
    num_workers = 4
    obs_host = "0.0.0.0"
    obs_port = 9102
    # ... 其他配置
```

### Deployment
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pystatsd
spec:
  replicas: 2
  selector:
    matchLabels:
      app: pystatsd
  template:
    metadata:
      labels:
        app: pystatsd
    spec:
      containers:
      - name: pystatsd
        image: pystatsd-helix:latest
        ports:
        - containerPort: 8125
          protocol: UDP
        - containerPort: 9102
          protocol: TCP
        volumeMounts:
        - name: config-vol
          mountPath: /app/config.toml
          subPath: config.toml
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 9102
          initialDelaySeconds: 5
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /health/live
            port: 9102
          initialDelaySeconds: 10
          periodSeconds: 30
      volumes:
      - name: config-vol
        configMap:
          name: pystatsd-config
```

### Service
```yaml
apiVersion: v1
kind: Service
metadata:
  name: pystatsd
spec:
  selector:
    app: pystatsd
  ports:
  - name: statsd-udp
    port: 8125
    protocol: UDP
  - name: metrics
    port: 9102
    protocol: TCP
```
