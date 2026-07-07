# Phase 5: 真实爬虫 + 真实 OAuth + DouyinPublisher

## Context

Phase 4 完成了 Pipeline DAG 编排器，系统已能端到端跑通"采集→分析→生成→合成→发布"流水线。但采集和发布两端仍是 STUB：
- `crawl_scheduler` 生成假数据（"热门商品 #1"），不调用真实爬虫，不持久化到 `products` 表
- `web_backend` OAuth 用 `client_id=stub`，callback 不交换 token，只 hash 授权码
- `base_publisher.refresh_token_if_needed` 是 `pass`，`get_oauth_token` 直接返回 refresh_token 当 access_token
- 无 `DouyinPublisher`，`worker_publishers` 配置缺 `api_upload_url`/`api_publish_url`

本阶段实现真实的爬虫调用、OAuth 授权码交换、token 刷新和平台发布，满足用户"全真实实现"要求。凭证通过 env/platform_config 表配置，代码是真实的但凭证可配置。

---

## 当前状态分析

### 3-way schema 不匹配（必须先修）

| 位置 | 问题 |
|------|------|
| `database/init.sql` L160-174 | `token_encrypted TEXT`, `status ENUM('active','expired','revoked')` — **这是 source of truth** |
| `utils/common_sdk/vault_client.py` L115-116 | 读 `refresh_token` 列 + `status=1` — 列名和状态值都错 |
| `project/backend/web_backend/routes.py` L185,190 | 写 `encrypted_token` 列 — 列名错 |

### 现有可复用基础

- `BasePlatformCrawler`/`APIDirectCrawler`/`RenderCrawler`（[base_crawler.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/base_crawler.py)）— 抽象基类已有 rate-limit、Redis 锁、校验
- `PlatformDataMapper`（[mapper.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/mapper.py)）— JsonPath 解析 + 类型强转，`_resolve_jsonpath` 可复用
- `StandardProduct`/`CrawlRequest`/`CrawlResult`（models.py）— 数据模型已定义
- `vault_client.store_platform_refresh_token`（L124-131）— 存在但从未被调用
- `CrawlerRegistry`/`PublisherRegistry`（registry.py）— register/get/load_from_config 已实现

---

## 设计决策

### D1: 配置驱动，非代码驱动
所有平台差异（OAuth 端点、scope、JsonPath 映射、爬虫 URL）存入 `platform_config` 表，代码通用。避免每个平台一个函数的爆炸。

### D2: OAuthFlow 单一类处理所有平台
4 个平台 OAuth 协议相同（authorization_code flow），仅 URL + JsonPath 不同。单一 `OAuthFlow` 类通过配置参数化。

### D3: Playwright 懒导入
`fetch_html()` 函数内部 `from playwright.async_api import async_playwright`，无浏览器时抛 `RuntimeError`，测试可 mock。

### D4: MongoDB → platform_config 表
架构文档 §4.2 说映射存 MongoDB，但 MongoDB 未接入。务实替代：映射规则存 `platform_config` 表 `crawl_mapping` 键（JSON）。文档注释此偏差。

### D5: 4 个独立爬虫类 + 共享 helper
DOM/API 结构确实不同，需要独立类。共享 `_playwright.fetch_html` 和 `PlatformDataMapper`。

### D6: execute_crawl_job 保持签名
`routes.py:58` 的 `.delay(task_id, platform, keyword, max_count, sort_by, tenant_id)` 固定，重写内部逻辑不改签名。

---

## 实施步骤

### Step 18: 修复 schema 不匹配 + OAuthFlow

#### 18.1 修复 vault_client.py MySQL fallback

**文件**: `utils/common_sdk/vault_client.py` L109-122

```python
async def _fallback_mysql_refresh_token(self, platform: str, tenant: str) -> str | None:
    try:
        from db_clients.mysql import get_mysql_client
        mysql = get_mysql_client()
        row = await mysql.fetchone(
            "SELECT token_encrypted FROM platform_authorizations "
            "WHERE platform=%s AND tenant_id=%s AND status='active' LIMIT 1",
            (platform, tenant),
        )
        return row["token_encrypted"] if row else None
    except Exception as e:
        logger.warning("vault_mysql_fallback_failed", error=str(e))
        return None
```

#### 18.2 修复 web_backend/routes.py callback 列名

**文件**: `project/backend/web_backend/routes.py` — 将 `encrypted_token` 替换为 `token_encrypted`（UPDATE 和 INSERT 语句中，2 处）。

#### 18.3 创建 OAuthFlow

**新文件**: `utils/platform_connectors/oauth.py`

```python
class OAuthFlow:
    """Config-driven OAuth2 authorization code flow + refresh."""
    def __init__(self, platform: str, cfg: dict[str, str]) -> None:
        self.platform = platform
        self.client_id = cfg["oauth_client_id"]
        self.client_secret = cfg.get("oauth_client_secret", "")
        self.redirect_uri = cfg["oauth_redirect_uri"]
        self.auth_url = cfg["oauth_auth_url"]
        self.token_url = cfg["oauth_token_url"]
        self.refresh_url = cfg.get("oauth_refresh_url", self.token_url)
        self.scope = cfg.get("oauth_scope", "")
        self.id_field = cfg.get("oauth_client_id_field", "client_id")  # Douyin/TikTok 用 client_key
        # JsonPath 路径（平台响应结构不同）
        self.token_path = cfg.get("oauth_token_path", "$.access_token")
        self.refresh_token_path = cfg.get("oauth_refresh_token_path", "$.refresh_token")
        self.open_id_path = cfg.get("oauth_open_id_path", "")
        self.expires_path = cfg.get("oauth_expires_path", "$.expires_in")

    def build_auth_url(self, state: str) -> str:
        # urlencode client_id/redirect_uri/scope/response_type=code/state
        ...

    async def exchange_code(self, code: str) -> dict:
        # httpx POST token_url with {id_field: client_id, client_secret, code, redirect_uri, grant_type=authorization_code}
        # 用 _resolve_jsonpath 解析响应
        # 返回 {access_token, refresh_token, expires_in, open_id, raw}
        ...

    async def refresh(self, refresh_token: str) -> dict:
        # httpx POST refresh_url with {id_field: client_id, client_secret/grant_type=refresh_token, refresh_token}
        # 同样解析返回
        ...
```

从 `mapper.py` 提取 `resolve_jsonpath` 为模块级函数，`OAuthFlow` 和 `PlatformDataMapper` 都复用。

#### 18.4 重构 mapper.py 提取 resolve_jsonpath

**文件**: `utils/platform_connectors/mapper.py`

将 `_resolve_jsonpath` 提取为模块级 `resolve_jsonpath(data, path)`，`PlatformDataMapper._resolve_jsonpath` 改为调用它。

### Step 19: 真实 token 刷新 + web_backend OAuth

#### 19.1 实现 base_publisher 真实刷新

**文件**: `utils/platform_connectors/base_publisher.py`

```python
class BasePlatformPublisher(ABC):
    def __init__(self, platform_config):
        ...
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def get_oauth_token(self) -> str:
        await self.refresh_token_if_needed()
        if self._access_token:
            return self._access_token
        return os.environ.get(f"{self.platform_id.upper()}_ACCESS_TOKEN", "")

    async def refresh_token_if_needed(self) -> None:
        import time
        if self._access_token and time.time() < self._token_expires_at - 60:
            return
        from common_sdk.vault_client import vault_client
        tenant = self.platform_config.config.get("tenant_id", "default")
        refresh_token = await vault_client.get_platform_refresh_token(self.platform_id, tenant)
        if not refresh_token:
            return
        try:
            from .oauth import OAuthFlow
            oauth = OAuthFlow(self.platform_id, self.platform_config.config)
            result = await oauth.refresh(refresh_token)
            self._access_token = result["access_token"]
            self._token_expires_at = time.time() + result.get("expires_in", 3600)
        except Exception as e:
            logger.warning("token_refresh_failed", platform=self.platform_id, error=str(e))
```

#### 19.2 实现 web_backend 真实 auth-url + callback

**文件**: `project/backend/web_backend/routes.py`

`/platforms/auth-url`:
- 从 `platform_config` 表读取 `oauth_client_id`/`oauth_redirect_uri`/`oauth_scope`/`oauth_auth_url`
- 用 `OAuthFlow.build_auth_url(state=uuid)` 构建真实 URL
- 生成 `state` 并存 Redis（CSRF 防护，5 分钟过期）

`/platforms/callback`:
- 验证 `state` 匹配 Redis
- 从 `platform_config` 读取完整 OAuth 配置
- `oauth = OAuthFlow(platform, cfg); tokens = await oauth.exchange_code(code)`
- `await vault_client.store_platform_refresh_token(platform, tenant, tokens["refresh_token"], extra={...})`
- UPDATE `platform_authorizations` SET `token_encrypted=refresh_token, platform_user_id=open_id, access_token_expires_at=..., scopes=..., status='active'`

### Step 20: 4 个真实爬虫 + 持久化

#### 20.1 创建 _playwright helper

**新文件**: `utils/platform_connectors/_playwright.py`

```python
async def fetch_html(url: str, proxy: str | None = None, wait_selector: str | None = None) -> str:
    """Lazy-import Playwright, render page, return HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright not installed — run: pip install playwright && playwright install chromium")
    async with async_playwright() as p:
        browser = await p.chromium.launch(proxy={"server": proxy} if proxy else None)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        if wait_selector:
            await page.wait_for_selector(wait_selector, timeout=10000)
        html = await page.content()
        await browser.close()
        return html
```

#### 20.2 创建 4 个爬虫

**新文件**: `project/backend/crawl_scheduler/connectors/douyin_crawler.py`

```python
class DouyinCrawler(RenderCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        from _playwright import fetch_html  # 或 platform_connectors._playwright
        url = self._build_search_url(request.keyword, request.sort_by)
        html = await fetch_html(url, proxy=self.platform_config.proxy_required and self._get_proxy())
        raw_items = self._extract_items(html)  # 解析搜索结果 HTML
        mapper = PlatformDataMapper({self.platform_id: self.platform_config.config.get("crawl_mapping", {})})
        products = mapper.map_batch(self.platform_id, raw_items[:request.max_count])
        return CrawlResult(products=products, total_found=len(raw_items))
```

`taobao_crawler.py` — 同结构，不同 URL 模板和选择器。

`amazon_crawler.py`:
```python
class AmazonCrawler(APIDirectCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self._api_url(request), headers=self._sign(request))
            resp.raise_for_status()
            data = resp.json()
            raw_items = resolve_jsonpath(data, self.platform_config.config.get("crawl_items_path", "$.SearchResult.Items"))
        mapper = PlatformDataMapper({self.platform_id: self.platform_config.config.get("crawl_mapping", {})})
        products = mapper.map_batch(self.platform_id, raw_items[:request.max_count])
        return CrawlResult(products=products, total_found=len(raw_items))
```

`shopee_crawler.py` — 同结构，不同 API 签名和端点。

#### 20.3 创建 connectors/registry.py

**新文件**: `project/backend/crawl_scheduler/connectors/registry.py`

```python
async def build_crawler_registry(tenant_id="default") -> CrawlerRegistry:
    reg = CrawlerRegistry()
    reg.register_crawler("douyin", DouyinCrawler)
    reg.register_crawler("taobao", TaobaoCrawler)
    reg.register_crawler("amazon", AmazonCrawler)
    reg.register_crawler("shopee", ShopeeCrawler)
    configs = await _load_adapter_configs(tenant_id)  # 读 platform_config 表
    reg.load_from_config(configs)
    return reg
```

#### 20.4 创建 persistence.py

**新文件**: `project/backend/crawl_scheduler/persistence.py`

```python
async def persist_products(products: list[StandardProduct], tenant_id: str = "default") -> int:
    if not products:
        return 0
    mysql = get_mysql_client()
    rows = [(tenant_id, p.platform, p.platform_product_id, p.title, p.description,
             p.main_image_url, json.dumps(p.image_urls), p.price, p.currency,
             p.sales_count, p.rating, p.category, json.dumps(p.tags), json.dumps(p.raw_data))
            for p in products]
    sql = """INSERT INTO products (tenant_id, platform, platform_product_id, title, description,
             main_image_url, image_urls, price, currency, sales_count, rating, category, tags, raw_data)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
             ON DUPLICATE KEY UPDATE title=VALUES(title), price=VALUES(price),
             sales_count=VALUES(sales_count), rating=VALUES(rating), updated_at=NOW()"""
    await mysql.executemany(sql, rows)
    return len(rows)
```

#### 20.5 重写 execute_crawl_job

**文件**: `project/backend/crawl_scheduler/tasks.py`

保持签名 `(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default")`，重写内部：

```python
@create_task("execute_crawl_job", queue="crawl_queue")
def execute_crawl_job(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default"):
    import asyncio
    _set_status(self, task_id, status="running", progress_percent="10")
    try:
        async def _run():
            reg = await build_crawler_registry(tenant_id)
            crawler = reg.get_crawler(platform)
            if crawler is None:
                raise RuntimeError(f"No crawler for platform={platform}")
            result = await crawler.run_crawl(CrawlRequest(keyword=keyword, max_count=max_count, sort_by=sort_by))
            count = await persist_products(result.products, tenant_id)
            return result, count

        result, count = asyncio.run(_run())
        _set_status(self, task_id, status="completed", progress_percent="100",
                    result=json.dumps({"products_found": len(result.products), "persisted": count}, ensure_ascii=False))
        return {"products_found": len(result.products), "persisted": count, "platform": platform}
    except Exception as e:
        _set_status(self, task_id, status="failed", error=str(e))
        raise
```

### Step 21: DouyinPublisher + worker_publishers 配置

#### 21.1 创建 DouyinPublisher

**新文件**: `utils/platform_connectors/douyin_publisher.py`

```python
class DouyinPublisher(BasePlatformPublisher):
    async def publish(self, request: PublishRequest) -> PublishResult:
        token = await self.get_oauth_token()
        open_id = self.platform_config.config.get("oauth_open_id", "")
        video_id = await self.upload_video(request.content.video_url, token, open_id)
        post_id = await self.create_post(video_id, request.content, token, open_id)
        return PublishResult(platform_post_id=post_id, status="published", public_url="")

    async def upload_video(self, video_url: str, token: str, open_id: str) -> str:
        url = self.platform_config.config.get("douyin_upload_url", "https://open.douyin.com/api/douyin/v1/video/upload_video/")
        # 下载视频 → httpx multipart POST → 解析 video_id
        ...

    async def create_post(self, video_id: str, content: PublishContent, token: str, open_id: str) -> str:
        url = self.platform_config.config.get("douyin_create_url", "https://open.douyin.com/api/douyin/v1/video/create_video/")
        # httpx JSON POST {video_id, text=content.title+description} → 解析 item_id
        ...
```

#### 21.2 修复 worker_publishers.py 配置加载

**文件**: `project/backend/publish_dispatcher/worker_publishers.py`

```python
from platform_connectors.douyin_publisher import DouyinPublisher

_DEFAULT_PLATFORMS = ("youtube", "tiktok", "instagram", "douyin")

reg = PublisherRegistry()
reg.register_publisher("youtube", GenericHTTPPublisher)
reg.register_publisher("tiktok", GenericHTTPPublisher)
reg.register_publisher("instagram", GenericHTTPPublisher)
reg.register_publisher("douyin", DouyinPublisher)

async def load_worker_publisher_configs() -> None:
    """从 platform_config 表加载真实配置，fail-soft。"""
    try:
        configs = await _load_adapter_configs("default")  # 读 platform_config
        reg.load_from_config(configs)
    except Exception as e:
        logger.warning("publisher_config_load_failed", error=str(e))

# 启动时调用 load_worker_publisher_configs()（从 main.py lifespan）
worker_publishers = reg
```

**文件**: `project/backend/publish_dispatcher/main.py` — lifespan 中调用 `await load_worker_publisher_configs()`。

### Step 22: 测试（~12 个）

#### 22.1 创建 test_phase5_oauth.py（3 个测试）

- `test_oauth_build_auth_url_contains_client_id_and_scope` — 验证 URL 构建
- `test_oauth_exchange_code_parses_jsonpath` — Douyin 嵌套 `$.data.access_token` vs Google 平 `$.access_token`
- `test_oauth_refresh_posts_to_refresh_url` — 验证刷新调用正确端点

#### 22.2 创建 test_phase5_crawlers.py（4 个测试）

- `test_douyin_crawler_uses_playwright_and_maps_products` — mock fetch_html 返回 HTML，验证 StandardProduct 映射
- `test_amazon_crawler_calls_api_and_maps_products` — mock httpx，验证 API 调用 + 映射
- `test_persist_products_upserts_on_duplicate` — mock mysql.executemany，验证 SQL 含 ON DUPLICATE KEY UPDATE
- `test_execute_crawl_job_uses_registry_and_persists` — mock build_crawler_registry + persist_products，验证完整流程

#### 22.3 创建 test_phase5_publishers.py（5 个测试）

- `test_douyin_publisher_calls_upload_then_create` — mock httpx，验证两步调用 + open_id header
- `test_base_publisher_refresh_token_if_needed_calls_oauth_refresh` — mock vault + OAuthFlow.refresh
- `test_base_publisher_get_oauth_token_falls_back_to_env` — 无 refresh_token 时用 env
- `test_worker_publishers_loads_config_from_db` — mock mysql.fetchall 返回 platform_config 行
- `test_web_backend_callback_exchanges_code_and_stores_in_vault` — mock OAuthFlow.exchange_code + vault_client.store

---

## 假设与约定

1. **凭证来源**: OAuth client_id/secret、API key 通过 `platform_config` 表或 env 变量提供，代码不硬编码
2. **Playwright 可选**: 懒导入，测试不需要浏览器；真实运行需 `playwright install chromium`
3. **MongoDB 偏差**: 用 `platform_config` 表替代 MongoDB 存映射，在模块 docstring 注明
4. **Celery sync→async**: `execute_crawl_job` 是同步 Celery 任务，内部用 `asyncio.run()` 调用异步爬虫（与 publish_dispatcher 一致）
5. **state CSRF**: auth-url 生成 state 存 Redis 5 分钟，callback 验证匹配
6. **fail-soft**: 配置加载失败时 worker 仍启动（用 env fallback）；token 刷新失败时用 env token
7. **Douyin open_id**: 存 Vault extra + 镜像到 `platform_authorizations.platform_user_id`

---

## 验证步骤

1. **Schema 修复验证**: grep 确认无 `encrypted_token` 和 `status=1` 残留
2. **Phase 5 测试**: `python -m pytest tests/test_phase5_*.py -v` — 预期 12 passed
3. **全量回归**: `python -m pytest tests/ -v` — 预期 125 passed（113 + 12）
4. **import 一致性**: 确认新代码无 `utils.` 前缀导入
5. **Playwright 懒导入**: 确认 `import playwright` 只在 `fetch_html` 函数内部

---

## 文件清单

### 修改的文件（7 个）

| 文件 | 修改 |
|------|------|
| `utils/common_sdk/vault_client.py` | 18.1: 修 MySQL fallback 列名 + status |
| `project/backend/web_backend/routes.py` | 18.2 + 19.2: 修列名 + 真实 auth-url/callback |
| `utils/platform_connectors/mapper.py` | 18.4: 提取 resolve_jsonpath 为模块级 |
| `utils/platform_connectors/base_publisher.py` | 19.1: 真实 token 刷新 |
| `project/backend/crawl_scheduler/tasks.py` | 20.5: 重写 execute_crawl_job |
| `project/backend/publish_dispatcher/worker_publishers.py` | 21.2: 注册 DouyinPublisher + 配置加载 |
| `project/backend/publish_dispatcher/main.py` | 21.2: lifespan 调用配置加载 |

### 新建的文件（10 个）

| 文件 | 内容 |
|------|------|
| `utils/platform_connectors/oauth.py` | OAuthFlow 类 |
| `utils/platform_connectors/_playwright.py` | fetch_html 懒导入 helper |
| `utils/platform_connectors/douyin_publisher.py` | DouyinPublisher |
| `project/backend/crawl_scheduler/connectors/douyin_crawler.py` | DouyinCrawler |
| `project/backend/crawl_scheduler/connectors/taobao_crawler.py` | TaobaoCrawler |
| `project/backend/crawl_scheduler/connectors/amazon_crawler.py` | AmazonCrawler |
| `project/backend/crawl_scheduler/connectors/shopee_crawler.py` | ShopeeCrawler |
| `project/backend/crawl_scheduler/connectors/registry.py` | build_crawler_registry |
| `project/backend/crawl_scheduler/persistence.py` | persist_products |
| `tests/test_phase5_oauth.py` | 3 个 OAuth 测试 |
| `tests/test_phase5_crawlers.py` | 4 个爬虫测试 |
| `tests/test_phase5_publishers.py` | 5 个发布器测试 |
