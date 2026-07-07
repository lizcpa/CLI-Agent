# 开发任务规划

基于架构文档 V4.1 制定的开发计划。

## 阶段 0：基础设施与共享组件 (Week 1)

### 0.1 基础设施部署脚本
- [x] Docker Compose 开发环境 (MySQL, MongoDB, Redis, MinIO, RabbitMQ, Kong, Nacos, Vault)
- [ ] Kubernetes 本地开发配置 (kind/k3d)
- [x] 共享配置管理 (Nacos 配置加载框架 + 代码内 fallback)

### 0.2 共享 Python 包 (utils/)
- [x] `common-sdk`: 统一响应格式、异常处理、JWT 认证 (verify_internal_jwt 已下沉统一)、HTTP 客户端、Nacos/Vault/内容安全客户端
- [x] `db-clients`: MySQL/MongoDB/Redis/MinIO 连接池封装
- [x] `mq-clients`: RabbitMQ/Celery 生产者消费者封装
- [x] `model-adapters`: 模型适配器基类 (LLM, Image, Video, TTS)
- [x] `platform-connectors`: 采集/发布连接器基类

---

## 阶段 1：核心微服务开发 (Week 2-3)

### 1.1 asset-manager (端口 8006) - 素材管理基础服务
**优先级：最高** (被 publish-dispatcher 依赖)
- [ ] MinIO 存储操作封装 (上传/下载/预签名 URL/目录管理)
- [ ] 视频转码适配接口 `/api/v1/assets/adapt` (FFmpeg 调用)
- [ ] 平台适配配置 CRUD (platform_config 表)
- [ ] 模板管理 (视频合成模板 JSON)
- [ ] 健康检查 `/healthz`, `/readyz`, `/metrics`

### 1.2 crawl-scheduler (端口 8001) - 采集调度
- [ ] 采集任务管理 API (创建/查询/取消)
- [ ] 连接器注册表 (Nacos 配置驱动)
- [ ] Celery 任务执行框架
- [ ] 标准化数据映射 (MongoDB platform_mappings)
- [ ] 分布式锁防重复爬取
- [ ] 支持平台：抖音, 淘宝, Amazon, Shopee (预留接口)

### 1.3 product-analyzer (端口 8002) - 商品分析
- [ ] 商品评分算法 (热度、转化、利润多维度)
- [ ] 选品决策阈值配置
- [ ] 评分变更事件发布 (Redis Pub/Sub: product:hot_score_changed)
- [ ] 定时任务集成 (Celery Beat 从 crawl_plans 读取)

### 1.4 ai-generation (端口 8003) - AI 生成编排核心
**优先级：最高** (最复杂，被多个服务依赖)
- [ ] 模型注册表加载 (Nacos 配置热刷新)
- [ ] 模型路由器 (指定/自动/降级/成本策略)
- [ ] 四大适配器接口实现:
  - LLM 适配器 (文案生成)
  - Image 适配器 (生图)
  - Video 适配器 (生视频片段)
  - TTS 适配器 (语音合成)
- [ ] 内部统一接口: `/api/v1/internal/{image,video,llm,tts}/*`
- [ ] 用量记录上报 `/api/v1/internal/usage/log`
- [ ] 任务状态管理 (Redis task:{task_id})

### 1.5 video-composer (端口 8004) - 视频合成
- [ ] FFmpeg 合成编排 (多轨道: 视频+音频+字幕+转场)
- [ ] 模板驱动合成 (JSON 模板定义时间轴)
- [ ] 字幕压制 (SRT/ASS)
- [ ] 输出格式转码 (平台适配前的标准化)

### 1.6 publish-dispatcher (端口 8005) - 发布编排
- [ ] 发布任务编排 (调用 asset-manager 适配 + 平台发布)
- [ ] 平台发布适配器框架 (OAuth token 管理 via Vault)
- [ ] 定时发布支持
- [ ] 发布日志记录 (publish_log 表)

### 1.7 web-backend (端口 8007) - 管理后台 BFF
- [ ] 聚合 API (仪表盘数据、任务监控、配置管理)
- [ ] 租户管理、API Key 管理
- [ ] 平台授权管理 (OAuth 回调、Token 存储到 Vault)
- [ ] 模型/平台配置可视化管理

---

## 阶段 2：MCP Gateway (Week 3-4)

### 2.1 mcp-gateway (端口 8000)
- [ ] MCP Server 实现 (stdio + SSE 双模式)
- [ ] 工具注册机制 (9大核心工具映射)
- [ ] 参数校验、权限校验 (API Key + 租户白名单)
- [ ] 内部服务调用客户端 (携带 JWT + X-Tenant-ID)
- [ ] 异步任务轮询代理 (query_task_status)
- [ ] WebSocket 可选推送支持

---

## 阶段 3：前端管理后台 (Week 4-5)

### 3.1 Vue3 项目初始化 (端口 1001)
- [ ] Vite + TypeScript + Pinia + Vue Router
- [ ] Element Plus / Ant Design Vue 组件库
- [ ] 权限路由、国际化、主题配置

### 3.2 核心页面
- [ ] 登录/租户选择
- [ ] 仪表盘 (任务概览、成本统计、模型使用率)
- [ ] 采集管理 (计划配置、任务监控、商品浏览)
- [ ] 选品分析 (评分详情、阈值配置、热榜)
- [ ] AI 生成 (模型管理、生成历史、提示词模板)
- [ ] 视频合成 (模板管理、合成任务监控、预览)
- [ ] 发布管理 (平台授权、发布日志、定时计划)
- [ ] 系统设置 (租户配置、API Key、模型/平台注册表)

---

## 阶段 4：集成测试与完善 (Week 5-6)

### 4.1 端到端流水线测试
- [ ] 标准爆品流水线完整跑通
- [ ] 事件驱动自动化触发验证
- [ ] 多模型路由/降级验证
- [ ] 多平台发布适配验证

### 4.2 性能与稳定性
- [ ] 负载测试 (Celery 队列、GPU 推理并发)
- [ ] 熔断/限流/重试策略调优
- [ ] 监控告警规则配置

### 4.3 文档与交付
- [ ] API 文档 (OpenAPI/Swagger)
- [ ] 部署运维手册
- [ ] 开发者指南

---

## 依赖关系图

```
asset-manager (8006) ──┐
                       ├──► publish-dispatcher (8005)
crawl-scheduler (8001) ┤
                       ├──► ai-generation (8003) ──► video-composer (8004)
product-analyzer (8002)┘                              │
                                                         ▼
                                                   publish-dispatcher (8005)
                                                         │
                                                         ▼
                                                      mcp-gateway (8000)
                                                         │
                                                         ▼
                                                    前端 (1001) / Agent
```

---

## 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 模型适配器接口变更 | 高 | 定义严格的 Adapter 基类契约测试 |
| FFmpeg 合成耗时长 | 中 | 异步任务 + 进度回调 + GPU 加速选项 |
| 平台 API 频繁变更 | 高 | 连接器插件化、版本化管理 |
| 多租户数据隔离泄露 | 高 | 中间件强制注入 tenant_id、审计日志 |
| 分布式事务一致性 | 中 | 幂等设计 + 补偿任务 + 最终一致性 |

---

## 当前进度

- [x] 目录结构初始化
- [x] README.md 创建 (端口分配、技术栈、数据库信息)
- [x] 阶段 0 基础设施 (Docker Compose 含 Vault; common-sdk 下沉 verify_internal_jwt + Nacos/Vault/内容安全客户端框架; db-clients/mq-clients/model-adapters/platform-connectors 基类就绪)
- [x] 阶段 1 核心微服务 (ai-generation 任务层接适配器 + FFmpeg 合成 + 真实连接器 + 9 服务深度开发完成)
- [x] 阶段 2 MCP Gateway (9 工具注册 + stdio/SSE 双模式 + JSON-RPC + API key 哈希验证)
- [x] 阶段 3 前端后台 (Vue3 + 8 页面 + dist 已构建)
- [x] 阶段 4 集成测试 (181 测试通过)
- [x] Phase 6 DevOps (Docker 多服务 + GitHub Actions CI + 健康检查工厂)
- [x] Phase 7 可观测性 (Prometheus + Grafana + Jaeger + 业务指标 + OTEL 追踪)
- [x] Phase 8 韧性模式 (CircuitBreaker/Bulkhead/RateLimiter + tenacity 重试 + 幂等设计)
- [x] Phase 9 安全加固 (JWT secret 统一 + auth 统一 + 跨租户隔离 + RateLimiter 中间件)
- [x] Phase 9 E2E 测试 (跨租户隔离 E2E + fixtures)
- [x] Phase 9 生产就绪 (OpenAPI 元数据 + production_checklist + 告警规则)

**当前测试数：181 passed**

**待完成（可选）**：
- [ ] Kubernetes 本地开发配置 (kind/k3d) — P2 优先级，生产可选
- [ ] 前端与后端 API 联调验证 — P1，需真实环境验证
- [ ] 负载测试 (Celery/GPU 并发) — P2