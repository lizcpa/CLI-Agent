# 生产环境部署 Checklist

> 本文档列出生产环境部署前必须验证的配置项和安全设置。

---

## 一、环境变量强制配置

| 变量 | 生产值 | 说明 |
|------|--------|------|
| `MCP_AUTH_DISABLED` | `false` | **必须设为 false**，否则 API key 验证被旁路 |
| `INTERNAL_JWT_SECRET` | ≥32 字符随机字符串 | 通过 Vault 或 secret manager 注入，禁止使用开发默认值 |
| `MYSQL_PASSWORD` | 强密码 | 通过 Vault 注入 |
| `REDIS_PASSWORD` | 强密码 | 通过 Vault 注入 |
| `MINIO_ROOT_PASSWORD` | 强密码 | 通过 Vault 注入 |
| `NACOS_ENABLED` | `true` | 生产启用配置中心 |
| `NACOS_AUTH_PASSWORD` | 强密码 | 通过 Vault 注入 |
| `VAULT_ENABLED` | `true` | 生产启用凭证存储 |
| `VAULT_TOKEN` | Vault token | 通过 Vault 注入 |
| `CONTENT_SAFETY_ENABLED` | `true`（可选） | 启用阿里云绿网内容安全审核 |
| `PROMETHEUS_METRICS_ENABLED` | `true` | Prometheus 指标端点开启 |

---

## 二、密钥轮换流程

### 2.1 JWT Secret 轮换

1. 在 Vault 生成新 `INTERNAL_JWT_SECRET`（32+ 字符）
2. 更新 Nacos 配置中心的 `INTERNAL_JWT_SECRET` 值
3. 滚动重启所有服务（Kong → 后端服务 → Celery worker）
4. 旧 JWT token 在重启后失效，客户端需重新获取

### 2.2 MySQL/Redis/MinIO 密码轮换

1. 在 Vault 生成新密码
2. 更新基础设施容器的环境变量（docker-compose 或 K8s secret）
3. 重启基础设施容器
4. 更新 Nacos/Vault 中的连接字符串
5. 滚动重启后端服务

### 2.3 MCP API Key 轮换

1. 通过 `web_backend` 的 API Key 管理界面生成新 key
2. 客户端更新配置使用新 key
3. 旧 key 设置 `enabled=0` 或直接删除

---

## 三、基础设施健康验证

部署后执行以下命令验证基础设施状态：

```bash
# MySQL 连通性
docker compose exec mysql mysql -u prodvideo_app -p<password> -e "SELECT 1"

# Redis 连通性
docker compose exec redis redis-cli -a <password> ping

# MinIO bucket 存在
docker compose exec minio mc ls local/prodvideofactory

# RabbitMQ 队列状态
curl -u guest:<password> http://localhost:15672/api/queues

# Kong 健康检查
curl http://localhost:8001/status

# Vault seal 状态（应返回 sealed=false）
curl http://localhost:8200/v1/sys/seal-status
```

---

## 四、首次部署步骤

### 4.1 初始化数据库

```bash
# 启动 MySQL 并执行 init.sql
docker compose up -d mysql
sleep 10
docker compose exec mysql mysql -u root -p<root_password> prodvideo < database/init.sql
```

### 4.2 初始化 Vault

```bash
# 启动 Vault（开发模式用 dev，生产用 server）
docker compose up -d vault
sleep 5

# 初始化 Vault（仅首次）
vault operator init -key-shares=5 -key-threshold=3

# 解封 Vault（用生成的 unseal keys）
vault operator unseal <key1>
vault operator unseal <key2>
vault operator unseal <key3>

# 启用 KV secrets engine
vault secrets enable -path=secret kv
```

### 4.3 创建租户和 API Key

```bash
# 通过 web_backend API 创建租户
curl -X POST http://localhost:8007/api/v1/tenants \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "customer-001", "name": "Customer 001"}'

# 创建 MCP API Key
curl -X POST http://localhost:8007/api/v1/api-keys \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "customer-001", "name": "Production Key", "scopes": ["read", "write"]}'
```

---

## 五、监控告警验证

### 5.1 Grafana Dashboard

访问 `http://localhost:3000`，验证以下 dashboard：
- **Service Health**：所有服务 `/healthz` 状态为 200
- **Business Metrics**：`pipeline_runs_total`、`model_usage_cost_usd_total` 指标存在
- **Resilience**：`circuit_breaker_state`、`retry_attempts_total` 指标正常

### 5.2 Prometheus 规则

验证告警规则已加载：

```bash
curl http://localhost:9090/api/v1/rules
```

应包含：
- `ServiceDown`：服务健康检查失败 > 30s
- `HighErrorRate`：HTTP 5xx 比例 > 5%
- `CircuitBreakerOpen`：熔断器状态为 OPEN > 60s

---

## 六、安全审计

### 6.1 端点鉴权验证

```bash
# 无 JWT 应返回 401
curl http://localhost:8003/api/v1/models

# 有效 JWT 应返回 200
curl -H "Authorization: Bearer <valid_jwt>" http://localhost:8003/api/v1/models

# 无 MCP API key 应返回 401
curl -X POST http://localhost:8010/mcp/message -d '{"tool": "crawl_hot_product"}'

# 有效 MCP API key 应返回成功
curl -X POST http://localhost:8010/mcp/message \
  -H "Authorization: Bearer mcp_sk.<tenant>.<secret>" \
  -d '{"tool": "crawl_hot_product", "params": {"platform": "taobao"}}'
```

### 6.2 跨租户隔离验证

```bash
# tenant-A 的 JWT 访问 tenant-B 的 pipeline_id 应返回 404
curl -H "Authorization: Bearer <tenant-A-jwt>" \
  http://localhost:8008/api/v1/pipelines/<tenant-B-pipeline-id>
```

---

## 七、部署完成确认

全部 checklist 验证通过后，标记部署完成：

- [ ] 环境变量强制配置全部设置
- [ ] 密钥已注入 Vault/Nacos
- [ ] 基础设施健康验证通过
- [ ] 首次部署步骤完成（数据库初始化、租户创建）
- [ ] Grafana dashboard 显示正常
- [ ] Prometheus 告警规则加载
- [ ] 端点鉴权验证通过
- [ ] 跨租户隔离验证通过