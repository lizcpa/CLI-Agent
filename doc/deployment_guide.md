# 部署指南

## 前置要求

- **Docker Desktop**（含 Docker Engine + Docker Compose v2）
- **Python 3.11+**（仅开发/调试单服务时需要）
- **Node.js 20+**（仅开发前端时需要）
- 可用内存 ≥ 8 GB（全栈含基础设施）

## 一键启动

```bash
docker compose up -d
```

首次启动会构建 11 个应用镜像（9 后端 + 1 worker + 1 前端）+ 拉取基础设施镜像，预计 5-10 分钟。

查看启动状态：

```bash
docker compose ps
```

全部 `healthy` 后即可使用。前端访问 `http://localhost:1001`。

## 端口表

### 应用服务

| 服务 | 容器内端口 | 主机端口 | 健康检查 |
|------|-----------|---------|---------|
| frontend | 80 | 1001 | - |
| mcp-gateway | 8000 | 8010 | `/healthz` |
| crawl-scheduler | 8001 | 8001 | `/healthz` |
| product-analyzer | 8002 | 8002 | `/healthz` |
| ai-generation | 8003 | 8003 | `/healthz` |
| video-composer | 8004 | 8004 | `/healthz` |
| publish-dispatcher | 8005 | 8005 | `/healthz` |
| asset-manager | 8006 | 8006 | `/healthz` |
| web-backend | 8007 | 8007 | `/healthz` |
| pipeline-orchestrator | 8008 | 8008 | `/healthz` |
| celery-worker | - | - | - |

> mcp-gateway 主机端口 8010，避免与 Kong proxy 8000 冲突。

### 基础设施

| 组件 | 主机端口 | 管理界面 |
|------|---------|---------|
| MySQL | 3306 | - |
| MongoDB | 27017 | - |
| Redis | 6379 | - |
| MinIO | 9000 (API), 9001 (Console) | http://localhost:9001 |
| RabbitMQ | 5672 (AMQP), 15672 (Management) | http://localhost:15672 |
| Kong | 8000 (proxy), 8001 (admin), 8002 (gui) | http://localhost:8002 |
| Nacos | 8848, 9848 | http://localhost:8848/nacos |
| Vault | 8200 | - |

## 环境变量

关键环境变量（通过 docker-compose 注入，开发时可设 `.env`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MYSQL_HOST` | mysql | MySQL 主机 |
| `REDIS_HOST` | redis | Redis 主机 |
| `MINIO_ENDPOINT` | minio:9000 | MinIO 端点 |
| `MONGODB_HOST` | mongodb | MongoDB 主机 |
| `CELERY_BROKER_URL` | amqp://guest:guest@rabbitmq:5672// | Celery broker |
| `CELERY_RESULT_BACKEND` | redis://:dev_redis_2024@redis:6379/0 | Celery 结果后端 |
| `INTERNAL_JWT_SECRET` | dev-jwt-secret-prodvideofactory-2024 | 服务间 JWT 密钥 |
| `NACOS_ENABLED` | false | Nacos 配置中心（开发关闭） |
| `VAULT_ENABLED` | false | Vault 凭证存储（开发关闭，fallback env/MySQL） |
| `CONTENT_SAFETY_ENABLED` | false | 阿里云绿网内容安全（开发 fail-open） |
| `PROMETHEUS_METRICS_ENABLED` | true | Prometheus 指标端点 |

## 开发模式

### 单服务启动（不依赖 Docker）

1. 启动基础设施：`docker compose up -d mysql redis minio rabbitmq`
2. 启动单服务：

```bash
# 设置环境变量指向 localhost
export MYSQL_HOST=localhost REDIS_HOST=localhost MINIO_ENDPOINT=localhost:9000
export CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
export CELERY_RESULT_BACKEND=redis://:dev_redis_2024@localhost:6379/0

# 启动服务
uvicorn project.backend.crawl_scheduler.main:app --port 8001 --reload
```

### Celery worker

```bash
celery -A mq_clients.celery_app:celery_app worker \
  -Q crawl_queue,analyze_queue,ai_queue,compose_queue,publish_queue,orchestrator_queue \
  --loglevel=info
```

> 生产环境建议按队列拆分 worker 容器，分别设置 concurrency 与资源限制。

## 排障

### MySQL 连不上
- 检查 `MYSQL_HOST` 是否为 `mysql`（容器内）/ `localhost`（开发）
- 检查 `docker compose ps mysql` 是否 healthy
- 首次启动需等 init.sql 执行完毕（约 10s）

### Redis 密码错误
- 开发环境密码为 `dev_redis_2024`，由 docker-compose 注入
- 本地开发时需手动 `export REDIS_PASSWORD=dev_redis_2024`

### MinIO bucket 不存在
- MinIO 首次启动时 `minio-init` 容器自动创建 `prodvideofactory` bucket
- 若 bucket 缺失：`docker compose up -d minio-init` 重新初始化

### Vault 关闭
- 开发环境 `VAULT_ENABLED=false`，凭证 fallback 到 env / `platform_config` 表
- 启用 Vault：`VAULT_ENABLED=true VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=root`

### Nacos 关闭
- 开发环境 `NACOS_ENABLED=false`，配置使用代码内 fallback
- 服务注册未启用（Phase 7 待实现）

### 服务健康检查失败
- `docker compose logs <service>` 查看日志
- 检查 `depends_on` 的基础设施是否 healthy
- 检查环境变量是否正确注入

## 停止

```bash
# 停止所有服务（保留数据）
docker compose down

# 停止并清除数据卷（⚠️ 不可逆）
docker compose down -v
```

## 构建单个镜像

```bash
# 后端服务
docker compose build crawl-scheduler

# Worker
docker compose build celery-worker

# 前端
docker compose build frontend

# 全部
docker compose build
```

---

## 生产环境扩缩容

### Celery Worker 并发

根据队列负载调整 worker 数量和并发度：

```bash
# 高负载队列（ai_queue, compose_queue）- 需要 GPU
celery -A mq_clients.celery_app:celery_app worker \
  -Q ai_queue,compose_queue \
  --concurrency=2 \
  --loglevel=info

# 低负载队列（crawl_queue, analyze_queue）
celery -A mq_clients.celery_app:celery_app worker \
  -Q crawl_queue,analyze_queue \
  --concurrency=4 \
  --loglevel=info
```

K8s 环境可使用 HorizontalPodAutoscaler (HPA) 基于 CPU/内存自动扩缩。

### GPU 资源分配

视频合成和 AI 生成依赖 GPU：

- `ai_queue` worker：至少 1 GPU（推荐 NVIDIA T4 或更好）
- `compose_queue` worker：可选 GPU（FFmpeg 硬编码加速）

---

## 备份恢复

### MySQL 备份

```bash
# 每日全量备份
docker compose exec mysql mysqldump -u root -p<password> prodvideo > backup_$(date +%Y%m%d).sql

# 恢复
docker compose exec -T mysql mysql -u root -p<password> prodvideo < backup_20240101.sql
```

### MinIO 备份

```bash
# 使用 mc mirror 同步到备份 bucket 或外部存储
docker compose exec minio mc mirror local/prodvideofactory backup/prodvideofactory-backup
```

---

## 告警规则配置

### Prometheus 规则文件

在 `prometheus/rules.yml` 中定义：

```yaml
groups:
  - name: service_health
    rules:
      - alert: ServiceDown
        expr: up{job="backend-services"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Service {{ $labels.service }} is down"

      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High 5xx error rate on {{ $labels.service }}"

  - name: resilience
    rules:
      - alert: CircuitBreakerOpen
        expr: circuit_breaker_state{} == 1
        for: 60s
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker {{ $labels.name }} is OPEN"

      - alert: HighRetryRate
        expr: rate(retry_attempts_total{}[5m]) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High retry rate for {{ $labels.name }}"
```

### Grafana Notification Channels

配置通知渠道（Alertmanager、Slack、Email）：

1. Grafana → Alerting → Contact points → Add contact point
2. 选择类型（Slack webhook、Email、Webhook）
3. 配置接收地址
4. 在 Notification policies 中关联告警规则
