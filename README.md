# ProdVideo AI Factory

面向电商行业的 AI 视频自动化生产与分发中台，实现从商品采集、选品分析、AI 内容生成、视频合到多平台发布的全链路自动化。

## ✨ 核心特性

- **全链路自动化**：采集 → 分析 → 生成 → 合成 → 发布，端到端无需人工干预
- **多模型智能路由**：支持 GPT-4o、Claude、Gemini、ComfyUI、Sora、Veo3 等多供应商模型，自动选择最优模型并支持故障降级
- **MCP 协议网关**：实现 Model Context Protocol，AI Agent 可直接调用所有系统能力
- **弹性设计**：内置熔断器、重试、限流器、舱壁四种弹性模式，保障系统稳定性
- **多平台适配**：支持抖音、淘宝、Amazon、Shopee 采集，抖音、TikTok、YouTube 发布
- **多租户架构**：数据隔离、配置隔离、权限隔离，面向企业级 SaaS 场景设计
- **完善的可观测性**：Prometheus + Grafana + Jaeger 三位一体的监控体系

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      接入层                                  │
│  MCP Server (stdio/SSE)    │  Web 管理后台 (Vue3)           │
├─────────────────────────────────────────────────────────────┤
│                      网关层                                  │
│               Kong API Gateway (鉴权/限流/路由)               │
├─────────────────────────────────────────────────────────────┤
│                      服务层 (9 微服务)                        │
│  mcp-gateway  │  crawl-scheduler  │  product-analyzer        │
│  ai-generation│  video-composer   │  publish-dispatcher      │
│  asset-manager│  web-backend      │  pipeline-orchestrator   │
├─────────────────────────────────────────────────────────────┤
│                    任务调度层                                │
│    RabbitMQ + Celery + Celery Beat (异步任务/定时调度)        │
├─────────────────────────────────────────────────────────────┤
│                      数据层                                  │
│  MySQL / MongoDB / Redis / MinIO (业务/文档/缓存/对象存储)    │
├─────────────────────────────────────────────────────────────┤
│                  模型推理层 (K8s GPU Pod 池)                  │
│      Stable Diffusion / SVD / vLLM / ComfyUI                │
└─────────────────────────────────────────────────────────────┘
```

## 📁 项目结构

```
ProdVideo AI Factory
├── doc/                    # 架构文档、设计文档、部署指南
│   ├── architecture_v4.1.md    # 系统架构设计
│   ├── deployment_guide.md     # 部署指南
│   ├── development_plan.md     # 开发计划
│   ├── observability.md        # 可观测性文档
│   ├── production_checklist.md # 生产环境检查清单
│   └── security.md             # 安全设计文档
├── project/
│   ├── frontend/           # 前端代码（Vue 3 + TypeScript + Element Plus）
│   └── backend/            # 后端 9 微服务
│       ├── mcp_gateway/        # MCP 协议网关
│       ├── crawl_scheduler/    # 商品采集任务调度
│       ├── product_analyzer/   # 选品分析评分
│       ├── ai_generation/      # AI 内容生成（文案/图片/视频/TTS）
│       ├── video_composer/     # 视频合成与转码
│       ├── publish_dispatcher/ # 多平台发布编排
│       ├── asset_manager/      # 素材与模板管理
│       ├── web_backend/        # Web 管理后台 API + Agent 指挥台
│       └── pipeline_orchestrator # DAG 流水线编排
├── database/               # 数据库脚本 (init.sql)
├── utils/                  # 公共工具包
│   ├── common_sdk/            # 公共 SDK（认证、弹性、监控、HTTP 客户端）
│   ├── db_clients/            # 数据库客户端（MySQL/MongoDB/Redis/MinIO）
│   ├── model_adapters/        # AI 模型适配器（LLM/图像/视频/TTS）
│   └── platform_connectors/   # 平台连接器（采集/发布/OAuth）
├── tests/                  # 测试套件（181+ 测试用例）
├── observability/          # 可观测性配置（Prometheus/Grafana/Jaeger）
├── docker-compose.yml      # 全栈一键启动
├── Dockerfile.backend      # 9 后端共用镜像
├── Dockerfile.worker       # Celery worker 镜像
├── Dockerfile.frontend     # 前端 nginx 镜像
├── requirements.txt
└── pytest.ini
```

## 🚀 一键启动（Docker Compose）

```bash
docker compose up -d
```

等待所有服务 healthcheck 通过：

```bash
docker compose ps
```

全部 `healthy` 后即可访问：

| 服务 | 主机端口 | 用途 |
|------|---------|------|
| 前端管理后台 | **1001** | Vue 3 管理后台 |
| MCP Server | **8010** | MCP 协议网关（SSE 模式） |
| crawl-scheduler | 8001 | 采集调度服务 |
| product-analyzer | 8002 | 选品评分服务 |
| ai-generation | 8003 | AI 内容生成服务 |
| video-composer | 8004 | 视频合成服务 |
| publish-dispatcher | 8005 | 发布编排服务 |
| asset-manager | 8006 | 素材管理服务 |
| web-backend | 8007 | BFF API 服务 |
| pipeline-orchestrator | 8008 | Pipeline DAG 编排服务 |

### 基础设施端口

- MySQL: 3306
- MongoDB: 27017
- Redis: 6379
- MinIO: 9000, 9001
- RabbitMQ: 5672, 15672
- Kong: 8000, 8443, 8001, 8002
- Nacos: 8848
- Vault: 8200

### 可观测性端口

| 服务 | URL | 用途 |
|------|-----|------|
| Prometheus | http://localhost:9090 | 指标采集 |
| Grafana | http://localhost:3000 | 监控看板（admin/admin） |
| Jaeger UI | http://localhost:16686 | 分布式追踪 |

## 🤖 MCP 协议网关

实现 Model Context Protocol，支持 AI Agent（Claude Code、Trae、Aider）直接调用系统能力：

**支持的工具列表：**

| 工具 | 描述 |
|------|------|
| `crawl_hot_product` | 采集热门商品 |
| `analyze_product` | 分析商品评分 |
| `generate_copywriting` | 生成商品文案 |
| `generate_images` | 生成商品图片 |
| `generate_video_clips` | 生成视频片段 |
| `compose_video` | 合成最终视频 |
| `publish_content` | 发布到平台 |
| `query_task_status` | 查询任务状态 |
| `list_models` | 列出可用模型 |

## 🔧 技术栈

### 后端
- **语言**: Python 3.11+
- **框架**: FastAPI + Uvicorn
- **消息队列**: RabbitMQ + Celery + Celery Beat
- **数据库**: MySQL 8.0、MongoDB、Redis 7
- **对象存储**: MinIO
- **API 网关**: Kong 3.7
- **配置中心**: Nacos 2.3
- **密钥管理**: HashiCorp Vault 1.15
- **可观测性**: Structlog + Prometheus + Grafana + OpenTelemetry + Jaeger
- **弹性模式**: 自研 CircuitBreaker、Retry、RateLimiter、Bulkhead
- **安全**: JWT 认证、API Key 管理、多租户隔离、速率限制

### 前端
- **框架**: Vue 3 + TypeScript
- **构建**: Vite 5
- **UI**: Element Plus 2.7
- **状态管理**: Pinia
- **路由**: Vue Router

### AI 模型支持
| 类型 | 支持模型 |
|------|----------|
| LLM | GPT-4o、Claude Sonnet/Opus、Gemini 2.5 Pro、通义千问、豆包、DeepSeek |
| 图像生成 | ComfyUI (SDXL)、DALL-E |
| 视频生成 | Sora-2、Veo 3 |
| 语音合成 | Azure Cognitive Services TTS |

## 📊 健康检查

每个后端服务统一暴露：

- `GET /healthz` — Liveness 探针，进程存活即返回 200
- `GET /readyz` — Readiness 探针，检查依赖（Redis/MySQL/MinIO），失败返回 503
- `GET /metrics` — Prometheus 指标（HTTP 延迟/状态/速率 + 自定义业务指标）

## 🧪 运行测试

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
python -m pytest tests/ -ra --tb=short
```

测试覆盖率要求：70% 阈值强制校验。

## 🔒 安全设计

- **MCP 接入安全**：API Key 认证，绑定租户 ID 和权限范围
- **服务间认证**：内部 JWT，由 Kong 签发
- **平台授权**：OAuth2 令牌加密存储在 Vault，通过 Sidecar 动态获取
- **内容安全**：阿里云绿网检测生成内容，阻断违规内容
- **多租户隔离**：数据层通过 `tenant_id` 字段隔离，配置层独立管理
