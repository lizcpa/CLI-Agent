ProdVideo AI Factory · 企业级架构详细设计文档

文档信息	

项目名称	ProdVideo AI Factory

版 本 号	V4.1（深度架构版）

日 期	2026-06-29

密 级	内部公开

作 者	平台架构组

1\. 系统总体架构与边界

系统定位为基于MCP协议的AI视频生成与分发中台。对外通过MCP Server暴露工具集，供任意MCP客户端（Claude Code、Cline、自研Agent）调用；对内通过微服务集群实现采集、选品、内容生成、视频合成、多平台发布的全链路自动化。



1.1 架构分层

接入层：MCP Server（支持 stdio / SSE 两种传输），可选 Web 管理后台（Vue3）。



网关层：Kong API 网关，统一鉴权、限流、路由、日志。



服务层：微服务集群，每个服务独立部署、独立数据库访问。



任务调度层：RabbitMQ 消息队列 + Celery 异步任务引擎 + Celery Beat 定时调度。



数据层：MySQL（业务关系）、MongoDB（文档/爬虫原始数据）、Redis（缓存/锁/进度）、MinIO（对象存储）。



模型推理层：K8s GPU Pod 池，运行自部署模型（Stable Diffusion、SVD、vLLM 等），并通过内部 API 暴露；云模型 API（Veo 3、Sora 等）通过适配器封装。



1.2 服务列表与职责

服务名	职责	端口

mcp-gateway	MCP Server，暴露工具，对接 Agent	8000 (HTTP/SSE) 或 stdio

crawl-scheduler	采集任务调度与执行	8001

product-analyzer	商品评分、选品决策	8002

ai-generation	AI 生成编排：文案、图片、视频片段	8003

video-composer	视频合成、转码、字幕压制	8004

publish-dispatcher	发布编排与平台适配	8005

asset-manager	素材与模板管理、MinIO 操作封装	8006

web-backend	Web 管理后台 API（BFF）	8007

所有服务通过内部 REST API 通信，需携带 JWT 服务令牌，经过 Kong 内部网关。



2\. 外部交互核心：MCP 工具集与接口对接

MCP Server 将每个工具映射到内部微服务的 API 调用。Agent 通过 JSON-RPC 方式使用工具。



2.1 工具与内部 API 的映射关系

crawl\_hot\_product → 调用 crawl-scheduler 的 POST /api/v1/crawl/jobs



analyze\_product → 调用 product-analyzer 的 POST /api/v1/analyze



generate\_copywriting → 调用 ai-generation 的 POST /api/v1/copywriting



generate\_images → 调用 ai-generation 的 POST /api/v1/images/generate



generate\_video\_clips → 调用 ai-generation 的 POST /api/v1/videos/generate



compose\_video → 调用 video-composer 的 POST /api/v1/compose



publish\_content → 调用 publish-dispatcher 的 POST /api/v1/publish



query\_task\_status → 查询 Redis 中的任务状态，或调用对应服务的 GET /api/v1/tasks/{task\_id}/status



MCP Server 本身无业务逻辑，仅作为协议适配和参数校验网关，将 Agent 的请求转为内部 HTTP 调用，并将响应封装为 MCP 标准格式。



2.2 内部 REST API 约定

所有内部 API 遵循：



前缀 /api/v1/



鉴权头：Authorization: Bearer <internal\_jwt>



租户头：X-Tenant-ID: <tenant\_uuid>



统一响应格式：



json

{

&#x20; "code": 0,

&#x20; "message": "success",

&#x20; "data": { ... }

}

异步任务：



json

{

&#x20; "code": 0,

&#x20; "data": {

&#x20;   "task\_id": "uuid",

&#x20;   "status": "queued",

&#x20;   "estimated\_seconds": 30

&#x20; }

}

2.3 异步任务交互模式

对于长时间运行的工具（crawl\_hot\_product、generate\_images、generate\_video\_clips、compose\_video、publish\_content），流程为：



MCP Server 调用服务 A 的接口，获得 task\_id。



MCP Server 立即返回 task\_id 给 Agent，并可在 MCP 响应中附带 estimated\_seconds。



Agent 后续通过 query\_task\_status 查询进度，该工具会直接从 Redis 读取任务进度哈希表（由 Worker 实时更新）或查询服务状态接口。



任务完成时，Redis 哈希中写入 status: completed 和 result 字段（包含最终 URL 或数据引用）。



MCP Server 与 Agent 间通过 MCP 协议进行轮询，无需 WebSocket（也可选配 WebSocket 推送）。



3\. 多模型兼容架构细节

3.1 模型适配器接口规范

为支持 LLM、生图、生视频、TTS 四类模型，系统定义了统一的内部适配器接口规范，所有模型实现必须遵循。接口以 REST API 或 gRPC 形式存在（自部署模型通过内部 REST，云 API 在适配器内直接调用）。



生图适配器接口：



端点：POST /api/v1/internal/image/generate



请求体：



prompts：提示词列表



size：输出尺寸



n：数量



negative\_prompt：反向词



model：指定适配器名称（如 sd\_xl）



seed：随机种子（可选）



响应体：



task\_id：异步任务 ID



最终结果通过 GET /api/v1/internal/tasks/{task\_id}/result 获取，返回 image\_urls 列表。



生视频适配器接口：



端点：POST /api/v1/internal/video/generate



请求体：



type：text2video 或 image2video



prompts：文本提示词列表



reference\_image\_url：参考图（仅 image2video）



model：适配器名称（如 veo3、sora）



duration、resolution、count、motion\_strength



响应：task\_id，完成后返回 clip\_urls。



LLM 适配器接口：



端点：POST /api/v1/internal/llm/chat



请求体：



messages：标准消息列表



max\_tokens、temperature



model：如 gpt-4o



响应：同步返回 text。



TTS 适配器接口：



端点：POST /api/v1/internal/tts/synthesize



请求体：



text、voice、language、speed



model：如 azure-tts



响应：task\_id，完成后返回 audio\_url。



所有适配器接口由 ai-generation 服务统一管理和路由。



3.2 模型注册表与动态发现

模型配置存储在配置中心（Nacos），数据结构：



yaml

adapters:

&#x20; image:

&#x20;   - id: sd\_xl

&#x20;     type: image

&#x20;     endpoint: http://comfyui-service:8188

&#x20;     protocol: comfyui\_api

&#x20;     priority: 10

&#x20;     max\_concurrency: 5

&#x20;     capabilities:

&#x20;       max\_size: 2048x2048

&#x20; video:

&#x20;   - id: veo3

&#x20;     type: video

&#x20;     endpoint: https://videogeneration.googleapis.com

&#x20;     protocol: google\_vertex

&#x20;     auth\_ref: vault:veo3\_credentials

&#x20;     priority: 10

&#x20;     max\_concurrency: 2

&#x20;     capabilities:

&#x20;       max\_duration: 10

&#x20;       max\_resolution: 1080p

ai-generation 服务启动时加载注册表，并定时监听 Nacos 变更。当配置更新，服务内部路由表热刷新。



MCP Server 通过调用 ai-generation 的 GET /api/v1/models?type=video 获取所有已启用且健康的视频模型 ID 列表，用于动态生成 generate\_video\_clips 工具的 model 枚举值。



3.3 模型路由与降级策略

ai-generation 内部路由器根据以下逻辑选择适配器实例：



指定模型：直接使用。



自动策略：



若商品 tier == "hot"，从优先级排序中选取质量最高且并发达标未满的适配器（如 veo3）。



若 tier == "normal"，选取成本标签为 free 或 low 的适配器（如 local-svd）。



故障降级：当首选适配器连续失败 3 次，路由器将其暂时标记为不可用（持续 5 分钟），自动将后续请求导向优先级次高的适配器。不可用列表通过 Redis 分布式同步。



成本计费：每次适配器调用后，将用量和成本记录发送到 POST /api/v1/internal/usage/log（由专门的计量服务消费，写入 model\_usage\_log 表）。



4\. 多平台采集与发布详细设计

4.1 平台采集器标准化接口

crawl-scheduler 服务内部为每个平台维护一个采集连接器，所有连接器实现统一的内部接口：



crawl(keyword, max\_count, sort\_by) → 返回 List<StandardProduct>



采集连接器分为两类：



API 直连型（如 Amazon、Shopee）：直接调用平台开放 API，需配置 API Key。



渲染抓取型（如抖音、淘宝）：使用 Playwright 模拟浏览器，经由代理池。



连接器注册表类似模型注册表，存储于配置中心：



yaml

platforms:

&#x20; - id: douyin

&#x20;   connector\_class: crawlers.douyin.DouyinCrawler

&#x20;   proxy\_required: true

&#x20;   rate\_limit: 10/min

&#x20;   auth: none

&#x20; - id: amazon

&#x20;   connector\_class: crawlers.amazon.AmazonCrawler

&#x20;   auth\_ref: vault:amazon\_pa\_api

crawl-scheduler 负责解析连接器，并通过 Celery 任务执行具体抓取逻辑。



4.2 数据映射与标准化

每个平台返回的原始 JSON 结构各异。系统采用声明式映射模板实现标准化。映射模板存储在 MongoDB 的 platform\_mappings 集合，每条记录定义平台 ID 和一组 JQ/JsonPath 转换规则，将原始字段映射到 StandardProduct 字段。crawl-scheduler 在抓取完成后自动调用映射服务完成转换，存入 products 表。



4.3 多平台发布适配器接口

publish-dispatcher 服务维护多个发布适配器，每个适配器实现内部接口：



publish(request: PublishRequest) -> PublishResult



发布流程：



MCP Server 接收 publish\_content 请求，提取参数。



调用 asset-manager 的 POST /api/v1/assets/adapt 接口，传入原始视频 URL 和平台列表，返回每个平台适配后的视频 URL 和封面 URL。



对每个目标平台，发送异步任务到 Celery，调用对应平台的发布适配器。



发布适配器内部：



拼接平台官方 API 请求（内容上传接口）。



处理 OAuth 令牌：从 Vault 获取加密的 refresh token，调用平台 token 刷新接口获取 access token。



上传视频和封面，设置标题、描述、标签、商品挂载。



若指定 scheduled\_time，调用平台的定时发布接口。



返回 platform\_post\_id 和 public\_url。



所有平台结果汇总后写入 publish\_log 表，并通过 Redis 通知 MCP Server。



内容适配服务 (asset-manager)：



接口：POST /api/v1/assets/adapt



请求体：video\_url、platforms（数组）



内部动作：对每个平台，根据预配置的尺寸和时长限制，生成 FFmpeg 转码任务，输出对应格式的视频和自动截图封面，上传到 MinIO 特定目录，返回新 URL。



平台适配配置由运营在 Web 后台维护，存储于 MySQL platform\_config 表。



5\. 自动化流水线详细设计

5.1 流水线 DAG 任务定义

流水线由一系列 Celery 任务组成，通过 Canvas 串联。每个任务发布到 RabbitMQ 的特定队列：



队列 crawl\_queue：采集任务



队列 analyze\_queue：分析任务



队列 ai\_queue：AI 生成任务（文案、图片、视频片段）



队列 compose\_queue：视频合成任务



队列 publish\_queue：发布任务



标准爆品流水线：



crawl\_hot\_product 任务完成，结果写入 Redis。



自动触发 analyze\_product 任务（通过 Redis 键空间通知或 Celery 回调）。



分析完成后，若最高分 > 阈值（可配置），触发并行的 generate\_copywriting、generate\_images、generate\_video\_clips（以商品主图为参考）。



当三个并行任务全部完成，触发 compose\_video 任务。



合成完成后，触发 publish\_to\_authorized\_platforms 任务（根据租户已授权平台自动发布，默认发布为草稿或按预设时间）。



整个流水线状态和参数通过 generation\_pipelines 表持久化，每步更新 stage 字段。



5.2 定时任务配置

Celery Beat 从 MySQL crawl\_plans 表动态读取启用的计划，生成周期任务。管理后台修改计划后，通过 Redis 发布消息，Beat Scheduler 重载调度表。



5.3 事件驱动

product-analyzer 在计算出新品评分后，通过 Redis Pub/Sub 发布消息 product:hot\_score\_changed。ai-generation 监听该频道，若发现评分超过阈值，自动创建流水线，无需外部指令。这使得系统可以实时响应突发事件。



6\. 数据存储与接口交互细节

6.1 关键数据表与服务交互

products 表由 crawl-scheduler 写入，product-analyzer 读取并更新评分和状态。



generation\_pipelines 表由 mcp-gateway（或自动化引擎）创建，随后由 ai-generation、video-composer 更新步骤状态和结果 URL。



publish\_log 由 publish-dispatcher 写入。



model\_usage\_log 由各适配器调用后通过内部 API 写入，供成本核算。



platform\_config 存储每个平台的适配参数（尺寸、时长、文案长度限制等），由 asset-manager 和 publish-dispatcher 读取。



6.2 MinIO 存储结构

text

prodvideofactory

├── products/                # 商品原始图片缓存

├── generated/

│   ├── images/{tenant}/{pipeline\_id}/{timestamp}\_{hash}.png

│   ├── video\_clips/{tenant}/{pipeline\_id}/clip\_{n}.mp4

│   └── tts/{tenant}/{pipeline\_id}/audio.mp3

├── final/{tenant}/{pipeline\_id}/output.mp4

├── published/{tenant}/{platform}/{pipeline\_id}/adapted.mp4

└── templates/               # 视频合成模板JSON文件

访问控制：使用 MinIO 的 bucket policy 和预签名 URL，外部客户端通过带过期时间的 URL 下载。



6.3 Redis 数据结构规划

task:{task\_id}：哈希，存储 status、progress、result（JSON）、error。



lock:crawl:{platform}:{keyword}：字符串，分布式锁，避免重复爬取。



hot\_products:daily：有序集合，按热度评分排列。



model\_status:{adapter\_id}：哈希，记录健康状态、失败计数、禁用截止时间。



oauth\_token:{platform}:{tenant}：字符串，缓存加密的 access token。



7\. 安全与权限设计

7.1 MCP 接入安全

MCP Server 支持基于 API Key 的认证。客户端连接时需在 HTTP 头或环境变量中提供 Authorization: Bearer mcp\_sk\_xxx。



API Key 通过 Web 管理后台生成，绑定租户 ID 和权限范围（允许的模型白名单、平台白名单、最大并发数等）。



所有工具调用在 MCP Server 层进行权限校验，拒绝超范围请求。



7.2 内部服务通信安全

内部 REST API 调用使用服务间 JWT（由 Kong 签发），通过 KONG\_SERVICE\_TOKEN 环境变量注入。



网络层面使用 Kubernetes Network Policy 限制 Pod 间访问，仅开放必要端口。



7.3 平台授权

用户通过 Web 前端发起 OAuth 授权流程，回调后 web-backend 将 refresh token 加密后存入 Vault（路径 /secret/platforms/{platform}/{tenant}）。



发布适配器通过 Vault Agent Sidecar 动态获取令牌，不直接接触明文密钥。



7.4 内容安全

在 ai-generation 和 publish-dispatcher 中，调用第三方内容安全 API（如阿里云绿网）对生成文案、图片、视频进行检测。检测接口为 POST https://api.aliyun.com/green/...，同步返回风险等级。若命中违规，则阻断流程并标记任务为 content\_filtered。



8\. 扩展性与多租户

8.1 横向扩展

所有微服务无状态，可水平扩缩。K8s HPA 根据 CPU 和自定义指标（如 Celery 队列长度）自动调整副本数。GPU 推理服务通过 KEDA 根据 Redis 队列长度缩放带有 GPU 资源的 Pod。



8.2 多租户实现

数据隔离：MySQL、MongoDB、MinIO 均通过 tenant\_id 字段或路径前缀隔离。



任务隔离：消息队列使用租户特定的 routing key 或虚拟主机（vhost）。



配置隔离：每个租户可在 Web 后台设置自己的默认模型策略、发布定时、BGM 库等，存储于 tenant\_config 表。



8.3 插件化扩展

新增模型或平台完全通过配置注册，无需修改任何核心服务代码。新增工具功能可在 MCP Server 中注册新 Python 模块，调用已有的内部服务或新微服务。



9\. 监控与运维接口

健康检查：每个服务暴露 /healthz 和 /readyz，由 K8s 探针使用。



指标暴露：/metrics（Prometheus 格式），包含请求量、延迟、错误率、队列深度、GPU 利用率等。



任务状态查询：统一异步任务查询接口 GET /api/v1/tasks/{task\_id}/status，所有服务遵循同一响应格式。



配置变更通知：Nacos 变更回调通过内部广播通知各服务刷新本地缓存，无需重启。



10\. 总结

本文档以接口和组件交互为核心，详细描述了 ProdVideo AI Factory 的架构实现：通过 MCP 工具集对外统一暴露能力，内部微服务之间通过明确定义的 REST API 和消息队列协作，模型和平台则通过适配器注册与配置驱动实现热插拔。此设计确保系统兼具开放性、可扩展性和企业级稳定性，能够支持电商行业 24 小时自动化的视频生产与分发需求。

