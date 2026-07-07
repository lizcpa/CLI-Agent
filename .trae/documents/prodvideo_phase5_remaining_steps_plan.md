# ProdVideo Phase 5 — 剩余步骤实施计划 (Steps 19-22)

## 摘要

Phase 5 目标是"全真实实现":真实 OAuth 令牌交换、真实爬虫(Playwright + 真实 API)、真实 FFmpeg 合成、真实发布。Step 18 (schema 修复 + OAuthFlow + mapper 重构) 已完成并经探索验证。本计划覆盖剩余 Steps 19-22:

- **Step 19**: `base_publisher` 真实令牌刷新 + `web_backend` 真实 OAuth 端点
- **Step 20**: 4 个真实爬虫 (Douyin/Taobao Playwright + Amazon/Shopee API) + 商品持久化 + `execute_crawl_job` 重写
- **Step 21**: `DouyinPublisher` + `worker_publishers` 从 DB 加载配置
- **Step 22**: 12 个单元测试

预期最终结果:全部测试通过(现有 ~113 + 新增 12 = ~125),所有外部调用走真实路径(可由 env vars / `platform_config` 表配置凭据)。

---

## 当前状态分析

### 已完成 (Step 18 — 验证通过)
- [oauth.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/oauth.py):`OAuthFlow` 类,config-driven 处理 4 平台
- [mapper.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/mapper.py):模块级 `resolve_jsonpath`
- [vault_client.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/common_sdk/vault_client.py):MySQL fallback 用 `token_encrypted` + `status='active'`
- [web_backend/routes.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/web_backend/routes.py):`token_encrypted` 列名(无 `encrypted_token` 残留)

### 待实现 (本计划范围)

| 位置 | 当前状态 | 目标 |
|------|---------|------|
| [base_publisher.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/base_publisher.py) `get_oauth_token` | 返回 refresh_token(TODO 注释) | 真实刷新 → 返回 access_token |
| [base_publisher.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/base_publisher.py) `refresh_token_if_needed` | `pass` | 用 `OAuthFlow.refresh()` |
| [web_backend/routes.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/web_backend/routes.py) `/platforms/auth-url` | 硬编码 stub URL | 读 `platform_config` + `OAuthFlow.build_auth_url` + Redis state |
| [web_backend/routes.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/web_backend/routes.py) `/platforms/callback` | SHA-256 伪造 token | `OAuthFlow.exchange_code` + Vault 存储 + UPSERT |
| [crawl_scheduler/tasks.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/crawl_scheduler/tasks.py) `execute_crawl_job` | 生成假"热门商品" | 调真实爬虫 + 持久化到 `products` 表 |
| `utils/platform_connectors/_playwright.py` | 不存在 | Playwright 懒加载 helper |
| `project/backend/crawl_scheduler/connectors/{douyin,taobao,amazon,shopee}_crawler.py` | 不存在 | 4 个真实爬虫 |
| `project/backend/crawl_scheduler/connectors/registry.py` | 不存在 | `build_crawler_registry` |
| `project/backend/crawl_scheduler/persistence.py` | 不存在 | `persist_products` (ON DUPLICATE KEY UPDATE) |
| `utils/platform_connectors/douyin_publisher.py` | 不存在 | `DouyinPublisher` (upload + create_post) |
| [worker_publishers.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/publish_dispatcher/worker_publishers.py) | 只注册 youtube/tiktok/instagram,空 config | 加 douyin + `load_worker_publisher_configs()` 从 DB 加载 |
| [publish_dispatcher/main.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/publish_dispatcher/main.py) lifespan | 不加载配置 | 启动时调 `load_worker_publisher_configs()` |
| `tests/test_phase5_{oauth,crawlers,publishers}.py` | 不存在 | 12 个测试 |

### 关键约束 (来自探索)

1. **DB schema** ([init.sql](file:///c:/Users/29048/PycharmProjects/PythonProject1/database/init.sql)):
   - `platform_authorizations`: 列 `token_encrypted`(存 refresh_token)、`platform_user_id`(存 open_id)、`access_token_expires_at`、`scopes`、`status ENUM('active','expired','revoked')`;唯一键 `(tenant_id, platform)`
   - `platform_config`: 唯一键 `(platform, config_key)` — **跨租户全局**;列 `config_key`/`config_value`
   - `products`: 唯一键 `(platform, platform_product_id)` 支持 UPSERT;`score`/`tier` 由 `product_analyzer` 下游填充,爬虫不写

2. **路径注入模式** (test_pipeline_orchestrator.py 已验证):
   ```python
   sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
   sys.path.insert(0, str(Path(__file__).parent.parent))
   ```
   所有 crawl_scheduler 内部模块用 `from mq_clients.celery_app import ...` (无 `utils.` 前缀)

3. **Celery 任务调用模式**:`@create_task(name, queue)` + `bind=True`,首参 `self`;测试中 `tasks.xxx_task.run(...)` 同步调用

4. **已存在的 `GenericHTTPPublisher`** ([generic_http_publisher.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/generic_http_publisher.py)):读 `cfg["api_upload_url"]`/`cfg["api_publish_url"]`,Bearer auth — 作为 `DouyinPublisher` 的参考

5. **`platform_config` 已在 web_backend 读取** (lines 230-273 的 `/platform-configs` CRUD) — Step 19 复用此读取模式

6. **`PublisherRegistry.get_publisher` 在无配置时自动用合成 config 实例化** — Step 21 测试可依赖此行为

7. **`connectors/` 目录已存在**(只有空 `__init__.py`)— Step 20 直接往里加文件

8. **环境警示**:项目根有零字节 `nul` 文件会导致全仓 grep 报错;不在本计划范围,但实施时若遇 grep 失败需 scope 到子目录

---

## 假设与决策

### 假设
1. `platform_config` 表中各平台的 OAuth 配置键名遵循 `oauth_*` 前缀(与 `OAuthFlow.__init__` 一致):`oauth_client_id`、`oauth_client_secret`、`oauth_redirect_uri`、`oauth_auth_url`、`oauth_token_url`、`oauth_refresh_url`(可选,默认=token_url)、`oauth_scope`、`oauth_client_id_field`(可选,默认 `client_id`)、`oauth_client_secret_field`(可选,默认 `client_secret`)、`oauth_token_path`(可选,默认 `$.access_token`)、`oauth_refresh_token_path`、`oauth_open_id_path`、`oauth_expires_path`
2. `platform_config` 中爬虫配置键名:`crawler_type` ∈ {`playwright`,`api`}、`crawler_url_template`(含 `{keyword}`/`{page}` 占位符)、`crawler_api_endpoint`、`crawler_mapping`(JSON 字符串,传给 `PlatformDataMapper`)、`crawler_sort_map`(JSON,把 `sort_by` 映射到平台参数)
3. `platform_config` 中 DouyinPublisher 配置键名:`api_upload_url`、`api_publish_url`、`api_open_id_path`(可选)
4. Playwright 已在依赖中(`playwright` 包);若未安装,爬虫应 fail-soft 返回空 `CrawlResult` 并 log warning
5. 测试不依赖真实网络/浏览器 — 全部用 mock
6. `tenant_id` 默认 `"default"`;web_backend 已有 `verify_admin_request` 中间件提供 `request.state.tenant_id`(参考 `/platform-configs` 路由)

### 决策

**D1 — base_publisher 令牌缓存**:`_access_token` + `_token_expires_at` 实例属性;`refresh_token_if_needed` 在过期前 60s 触发刷新;刷新后回写新 refresh_token 到 Vault(若返回了新值)。这样 `DouyinPublisher`/`GenericHTTPPublisher` 自动获益。

**D2 — web_backend state 管理**:Redis key `oauth:state:{state}` → JSON `{"platform", "tenant_id", "created_at"}`,TTL 600s;callback 时 GET + DEL(单次使用)。用 `RedisClient`(async,已在 main.py lifespan 连接)。

**D3 — 爬虫架构**:`RenderCrawler` 子类(Douyin/Taobao)用 `_playwright.py` helper;`APIDirectCrawler` 子类(Amazon/Shopee)用 `httpx`。所有爬虫:
- 从 `platform_config.config` 读 `crawler_url_template`/`crawler_api_endpoint` + `crawler_mapping`
- 用 `PlatformDataMapper` 把 raw → `StandardProduct`
- `crawl()` 返回 `CrawlResult`;`run_crawl()`(基类)加 rate-limit + validate + timing
- fail-soft:`crawl()` 内 try/except,出错返回空 `CrawlResult` + log warning

**D4 — 持久化**:`persist_products` 用单条多值 INSERT ... ON DUPLICATE KEY UPDATE,更新 `title`/`description`/`main_image_url`/`image_urls`/`price`/`currency`/`sales_count`/`rating`/`category`/`tags`/`raw_data`/`status='active'`(不覆盖 `score`/`tier`)。用 `MySQLClient.executemany` 或拼一条大 SQL。

**D5 — DouyinPublisher**:继承 `BasePlatformPublisher`;`publish()` 走 `upload_video` → `create_post` 两步;open_id 从 `platform_authorizations.platform_user_id` 读(由 Step 19 callback 写入)。上传用 `httpx` 流式 POST。

**D6 — worker_publishers 配置加载**:`load_worker_publisher_configs(mysql) -> dict[str, PlatformAdapterConfig]` 读 `platform_config` 表,按 platform 聚合 config_key/config_value 成 dict,构造 `PlatformAdapterConfig`。注册映射:`douyin → DouyinPublisher`、`youtube/tiktok/instagram → GenericHTTPPublisher`。main.py lifespan 启动时调用。

**D7 — 测试 mock 策略**:
- OAuth 测试:mock `httpx.AsyncClient` 返回固定 token 响应
- 爬虫测试:mock `_playwright` helper(避免启动浏览器)+ mock `httpx`(API 爬虫)
- Publisher 测试:mock `httpx` + mock `BasePlatformPublisher.get_oauth_token`(避免 Vault 调用)
- web_backend 测试:mock `MySQLClient` + mock `RedisClient` + mock `OAuthFlow.exchange_code`
- 全部用 `tasks.xxx.run(...)` 同步调用模式(参考 test_pipeline_orchestrator.py)

**D8 — `connectors/__init__.py`**:从空文件改为导出 `build_crawler_registry`(方便 tasks.py 导入)。

**D9 — `execute_crawl_job` 保持签名**:`(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default")` 不变(routes.py 调用方依赖此签名)。

**D10 — 不删除 `nul` 文件**:不在本计划范围;实施时若 grep 失败则 scope 到子目录(已验证有效)。

---

## 实施步骤

### Step 19:真实令牌刷新 + web_backend OAuth

#### 19.1 修改 [base_publisher.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/utils/platform_connectors/base_publisher.py)

**Why**: 当前 `get_oauth_token` 直接返回 refresh_token(错误),`refresh_token_if_needed` 是 `pass`。所有 publisher 都依赖此方法拿 access_token。

**What**:
- `__init__` 加:`self._access_token: str | None = None`、`self._token_expires_at: float = 0.0`、`self._open_id: str = ""`、`self._oauth_flow: OAuthFlow | None = None`
- 新增 `_get_oauth_flow() -> OAuthFlow`:懒加载,从 `self.platform_config.config` 构造 `OAuthFlow(self.platform_id, cfg)`
- 重写 `get_oauth_token()`:`await self.refresh_token_if_needed()`;返回 `self._access_token or os.environ.get(f"{self.platform_id.upper()}_ACCESS_TOKEN", "")`
- 实现 `refresh_token_if_needed()`:
  ```python
  import time
  if self._access_token and time.time() < self._token_expires_at - 60:
      return
  refresh_token = await vault_client.get_platform_refresh_token(self.platform_id, tenant)
  if not refresh_token:
      return  # 退化为 env 变量
  flow = self._get_oauth_flow()
  result = await flow.refresh(refresh_token)
  self._access_token = result["access_token"]
  self._token_expires_at = time.time() + result["expires_in"]
  self._open_id = result["open_id"]
  if result["refresh_token"]:
      await vault_client.store_platform_refresh_token(
          self.platform_id, tenant, result["refresh_token"],
          extra={"open_id": result["open_id"], "expires_in": result["expires_in"]})
  ```
- 新增 `get_open_id() -> str`:返回 `self._open_id`(DouyinPublisher 用)
- 新增 `get_platform_user_id_from_db() -> str | None`:查 `platform_authorizations.platform_user_id`(callback 写入的 open_id)— 作为 fallback

**How**: 用 `time.time()` 而非 `datetime` 避免时区问题;`OAuthFlow.refresh` 内部已 `raise_for_status`,异常向上抛(fail-soft 由调用方决定);`try/except` 包整个刷新,失败 log warning 但不抛(让 `get_oauth_token` 退化为 env)。

#### 19.2 修改 [web_backend/routes.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/web_backend/routes.py)

**Why**: `/platforms/auth-url` 硬编码 stub;`/platforms/callback` SHA-256 伪造 token。需读 `platform_config` + 调 `OAuthFlow` + 存 Vault。

**What**:

**A. 重写 `GET /platforms/auth-url`** (当前 lines 159-169):
```python
@router.get("/platforms/auth-url")
async def get_platform_auth_url(
    platform: str = Query(...),
    tenant_id: str = Query("default"),
    request: Request = None,
):
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform=%s",
        (platform,))
    if not rows:
        raise HTTPException(404, f"no config for platform {platform}")
    cfg = {r["config_key"]: r["config_value"] for r in rows}
    state = uuid.uuid4().hex
    redis = get_redis_client()
    await redis.set(f"oauth:state:{state}",
        json.dumps({"platform": platform, "tenant_id": tenant_id, "created_at": time.time()}),
        ex=600)
    flow = OAuthFlow(platform, cfg)
    auth_url = flow.build_auth_url(state)
    return {"auth_url": auth_url, "state": state, "platform": platform}
```

**B. 重写 `POST /platforms/callback`** (当前 lines 172-193):
```python
@router.post("/platforms/callback")
async def platform_oauth_callback(payload: dict = Body(...)):
    platform = payload["platform"]
    code = payload["code"]
    state = payload["state"]
    tenant_id = payload.get("tenant_id", "default")
    redis = get_redis_client()
    state_raw = await redis.get(f"oauth:state:{state}")
    if not state_raw:
        raise HTTPException(400, "invalid or expired state")
    await redis.delete(f"oauth:state:{state}")
    state_data = json.loads(state_raw)
    if state_data["platform"] != platform:
        raise HTTPException(400, "platform mismatch")
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform=%s",
        (platform,))
    cfg = {r["config_key"]: r["config_value"] for r in rows}
    flow = OAuthFlow(platform, cfg)
    token_data = await flow.exchange_code(code)
    refresh_token = token_data["refresh_token"]
    open_id = token_data["open_id"]
    expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])
    await vault_client.store_platform_refresh_token(
        platform, tenant_id, refresh_token,
        extra={"open_id": open_id, "expires_in": token_data["expires_in"]})
    await mysql.execute(
        "INSERT INTO platform_authorizations "
        "(tenant_id, platform, platform_user_id, token_encrypted, access_token_expires_at, status) "
        "VALUES (%s,%s,%s,%s,%s,'active') "
        "ON DUPLICATE KEY UPDATE token_encrypted=VALUES(token_encrypted), "
        "platform_user_id=VALUES(platform_user_id), "
        "access_token_expires_at=VALUES(access_token_expires_at), status='active'",
        (tenant_id, platform, open_id, refresh_token, expires_at))
    return {"status": "ok", "platform": platform, "open_id": open_id}
```

**C. 顶部加 import**:`from platform_connectors.oauth import OAuthFlow`、`from common_sdk.vault_client import vault_client`、`import uuid, time, json`、`from datetime import datetime, timedelta`

**How**: state 用 Redis TTL 自动过期;callback 严格校验 state(防 CSRF);UPSERT 用 `ON DUPLICATE KEY UPDATE`(唯一键 `(tenant_id, platform)` 已存在);`platform_user_id` 存 open_id 供 DouyinPublisher 用。

---

### Step 20:4 个真实爬虫 + 持久化 + execute_crawl_job 重写

#### 20.1 创建 `utils/platform_connectors/_playwright.py`

**Why**: Playwright 懒加载 — 不在模块顶层 import(否则无浏览器环境测试会失败)。

**What**:
```python
from __future__ import annotations
import logging
from typing import Any
logger = logging.getLogger(__name__)

async def render_page(url: str, wait_for_selector: str | None = None,
                      timeout_ms: int = 30000, extract_script: str | None = None) -> Any:
    """Lazy-load Playwright, render page, return extracted data or HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        logger.warning("playwright_not_installed", error=str(e))
        return None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=timeout_ms)
            if extract_script:
                return await page.evaluate(extract_script)
            return await page.content()
        finally:
            await browser.close()
```

**How**: 单一函数 `render_page`,既可返回 HTML 也可返回 JS evaluate 结果。爬虫传 `extract_script` 直接拿 JSON 数据,避免 HTML 解析。

#### 20.2 创建 4 个爬虫

**位置**:`project/backend/crawl_scheduler/connectors/`

**A. `douyin_crawler.py`** — `DouyinCrawler(RenderCrawler)`:
```python
from platform_connectors.base_crawler import RenderCrawler
from platform_connectors.models import CrawlRequest, CrawlResult, StandardProduct
from platform_connectors.mapper import PlatformDataMapper
from platform_connectors._playwright import render_page
import json, logging, urllib.parse
logger = logging.getLogger(__name__)

class DouyinCrawler(RenderCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        try:
            cfg = request.platform_config or {}
            url_template = cfg.get("crawler_url_template",
                "https://www.douyin.com/search/{keyword}?type=video")
            sort_map = json.loads(cfg.get("crawler_sort_map", "{}"))
            sort_param = sort_map.get(request.sort_by, "")
            url = url_template.format(keyword=urllib.parse.quote(request.keyword))
            if sort_param:
                url += f"&sort_type={sort_param}"
            extract = cfg.get("crawler_extract_script", DEFAULT_EXTRACT)
            data = await render_page(url, wait_for_selector=cfg.get("crawler_wait_selector"),
                                      extract_script=extract)
            if not data:
                return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
            mapping = json.loads(cfg.get("crawler_mapping", "{}"))
            mapper = PlatformDataMapper({"douyin": mapping})
            products = [mapper.map_to_standard("douyin", item)
                        for item in data[:request.max_count]]
            return CrawlResult(products=products, total_found=len(products),
                              crawl_duration_ms=0)
        except Exception as e:
            logger.warning("douyin_crawl_failed", error=str(e))
            return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
```
`DEFAULT_EXTRACT` = JS 脚本,从 DOM 提取商品列表(标题/图片/链接)。

**B. `taobao_crawler.py`** — `TaobaoCrawler(RenderCrawler)`:同构,URL template 默认 `https://s.taobao.com/search?q={keyword}`,mapping 不同。

**C. `amazon_crawler.py`** — `AmazonCrawler(APIDirectCrawler)`:
```python
class AmazonCrawler(APIDirectCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        try:
            cfg = request.platform_config or {}
            endpoint = cfg.get("crawler_api_endpoint",
                "https://webservices.amazon.com/paapi5/searchitems")
            import httpx
            params = {"Keywords": request.keyword,
                      "ItemCount": min(request.max_count, 10),
                      "SearchIndex": "All",
                      "ItemPage": 1,
                      "Resources": ["ItemInfo.Title","Images.Primary.MediumURL",
                                    "Offers.Listings.Price"]}
            headers = {"x-amz-target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
                       "content-type": "application/json; charset=utf-8"}
            # 真实场景需 AWS Signature v4 签名;此处用 access_token Bearer 简化
            token = cfg.get("api_key", "") or os.environ.get("AMAZON_API_KEY", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(endpoint, json=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            mapping = json.loads(cfg.get("crawler_mapping", "{}"))
            mapper = PlatformDataMapper({"amazon": mapping})
            items = data.get("SearchResult", {}).get("Items", [])[:request.max_count]
            products = [mapper.map_to_standard("amazon", item) for item in items]
            return CrawlResult(products=products, total_found=len(products),
                              crawl_duration_ms=0)
        except Exception as e:
            logger.warning("amazon_crawl_failed", error=str(e))
            return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
```

**D. `shopee_crawler.py`** — `ShopeeCrawler(APIDirectCrawler)`:同构,endpoint 默认 `https://shopee.com/api/v4/search/search_items`,参数 `keyword`/`limit`/`newest`,token 从 `cfg["api_key"]` 或 env。

**How**: 4 个爬虫统一 fail-soft;mapping 从 `platform_config.crawler_mapping` 读(JSON 字符串),用 `PlatformDataMapper` 转 `StandardProduct`;`run_crawl()`(基类)自动加 rate-limit + validate。

#### 20.3 创建 `project/backend/crawl_scheduler/connectors/registry.py`

**What**:
```python
from platform_connectors.registry import CrawlerRegistry
from platform_connectors.models import PlatformAdapterConfig
from .douyin_crawler import DouyinCrawler
from .taobao_crawler import TaobaoCrawler
from .amazon_crawler import AmazonCrawler
from .shopee_crawler import ShopeeCrawler

_CRAWLER_CLASSES = {
    "douyin": DouyinCrawler,
    "taobao": TaobaoCrawler,
    "amazon": AmazonCrawler,
    "shopee": ShopeeCrawler,
}

def build_crawler_registry(configs: dict[str, PlatformAdapterConfig] | None = None) -> CrawlerRegistry:
    reg = CrawlerRegistry()
    for platform, cls in _CRAWLER_CLASSES.items():
        reg.register_crawler(platform, cls)
    if configs:
        reg.load_from_config(list(configs.values()))
    return reg
```

**同时更新 `connectors/__init__.py`**(当前空):
```python
from .registry import build_crawler_registry
__all__ = ["build_crawler_registry"]
```

#### 20.4 创建 `project/backend/crawl_scheduler/persistence.py`

**What**:
```python
import json, logging
from db_clients.mysql import get_mysql_client
from platform_connectors.models import StandardProduct
logger = logging.getLogger(__name__)

async def persist_products(products: list[StandardProduct], tenant_id: str = "default") -> int:
    if not products:
        return 0
    mysql = get_mysql_client()
    sql = (
        "INSERT INTO products "
        "(tenant_id, platform, platform_product_id, title, description, main_image_url, "
        "image_urls, price, currency, sales_count, rating, category, tags, raw_data, status) "
        "VALUES (" + ",".join(["%s"] * 15) + ") "
        "ON DUPLICATE KEY UPDATE "
        "title=VALUES(title), description=VALUES(description), "
        "main_image_url=VALUES(main_image_url), image_urls=VALUES(image_urls), "
        "price=VALUES(price), currency=VALUES(currency), sales_count=VALUES(sales_count), "
        "rating=VALUES(rating), category=VALUES(category), tags=VALUES(tags), "
        "raw_data=VALUES(raw_data), status='active'"
    )
    rows = []
    for p in products:
        rows.append((
            tenant_id, p.platform, p.platform_product_id, p.title, p.description,
            p.main_image_url, json.dumps(p.image_urls or []), p.price, p.currency,
            p.sales_count, p.rating, p.category, json.dumps(p.tags or []),
            json.dumps(p.raw_data or {}, ensure_ascii=False, default=str), "active",
        ))
    try:
        await mysql.executemany(sql, rows)
        return len(rows)
    except Exception as e:
        logger.warning("persist_products_failed", error=str(e), count=len(rows))
        return 0
```

**How**: 单条多值 INSERT(实际由 `executemany` 批量);不写 `score`/`tier`(下游 product_analyzer 负责);`raw_data` 用 `default=str` 防非序列化对象。

#### 20.5 重写 [crawl_scheduler/tasks.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/crawl_scheduler/tasks.py) `execute_crawl_job`

**What**:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio, json, uuid, time
from mq_clients.celery_app import create_task
from common_sdk.config import config_manager
from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client
from platform_connectors.models import CrawlRequest, PlatformAdapterConfig
from connectors import build_crawler_registry
from persistence import persist_products

logger = get_logger(__name__)

def _get_redis(): ...  # 保持不变

async def _load_platform_config(platform: str) -> dict:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform=%s",
        (platform,))
    return {r["config_key"]: r["config_value"] for r in rows} if rows else {}

@create_task("execute_crawl_job", queue="crawl_queue")
def execute_crawl_job(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default"):
    r = _get_redis()
    try:
        r.hset(f"task:{task_id}", mapping={"status": "running", "progress": 10})

        async def _run():
            cfg_dict = await _load_platform_config(platform)
            adapter_cfg = PlatformAdapterConfig(
                platform_id=platform, connector_class=cfg_dict.get("crawler_type", "playwright"),
                config=cfg_dict)
            reg = build_crawler_registry({platform: adapter_cfg})
            crawler = reg.get_crawler(platform)
            if not crawler:
                raise RuntimeError(f"no crawler for platform {platform}")
            request = CrawlRequest(keyword=keyword, max_count=max_count,
                                   sort_by=sort_by, platform_config=cfg_dict)
            r.hset(f"task:{task_id}", mapping={"status": "running", "progress": 30})
            result = await crawler.run_crawl(request)
            r.hset(f"task:{task_id}", mapping={
                "status": "running", "progress": 70,
                "products_found": str(result.total_found)})
            persisted = await persist_products(result.products, tenant_id)
            return result, persisted

        result, persisted = asyncio.run(_run())
        r.hset(f"task:{task_id}", mapping={
            "status": "completed", "progress": 100,
            "result": json.dumps([p.model_dump() for p in result.products],
                                 ensure_ascii=False, default=str),
            "products_found": str(result.total_found),
            "persisted": str(persisted)})
        r.expire(f"task:{task_id}", 86400)
        return {"products_found": result.total_found, "persisted": persisted,
                "platform": platform, "keyword": keyword}
    except Exception as e:
        try:
            r.hset(f"task:{task_id}", mapping={"status": "failed", "error": str(e)[:500]})
        except Exception:
            pass
        raise
```

**How**: 保持签名不变(D9);`asyncio.run` 包整个异步流程;从 DB 读 config → 建 registry → 拿 crawler → run_crawl → persist;Redis 进度分 10/30/70/100 四档。

---

### Step 21:DouyinPublisher + worker_publishers 配置加载

#### 21.1 创建 `utils/platform_connectors/douyin_publisher.py`

**What**:
```python
from __future__ import annotations
import logging
from typing import Any
import httpx
from .base_publisher import BasePlatformPublisher
from .models import PublishContent, PublishRequest, PublishResult
from .mapper import resolve_jsonpath
logger = logging.getLogger(__name__)

class DouyinPublisher(BasePlatformPublisher):
    platform_id = "douyin"

    async def publish(self, request: PublishRequest) -> PublishResult:
        try:
            video_url = request.video_url
            cover_url = request.cover_url or ""
            video_id = await self.upload_video(video_url)
            cover_id = await self.upload_cover(cover_url) if cover_url else ""
            post_id = await self.create_post(video_id, cover_id, request.content)
            return PublishResult(success=True, platform_post_id=post_id,
                                 platform=self.platform_id)
        except Exception as e:
            logger.warning("douyin_publish_failed", error=str(e))
            return PublishResult(success=False, error=str(e), platform=self.platform_id)

    async def upload_video(self, video_url: str) -> str:
        cfg = self.platform_config.config
        upload_url = cfg.get("api_upload_url", "https://open.douyin.com/api/douyin/v1/video/upload_video/")
        access_token = await self.get_oauth_token()
        open_id = await self._get_open_id()
        async with httpx.AsyncClient(timeout=120) as client:
            # 流式上传:先下载 video_url,再 POST 到抖音
            video_resp = await client.get(video_url)
            video_resp.raise_for_status()
            files = {"video": ("video.mp4", video_resp.content, "video/mp4")}
            data = {"open_id": open_id}
            headers = {"Authorization": f"Bearer {access_token}"}
            resp = await client.post(upload_url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            body = resp.json()
        video_field = cfg.get("api_video_id_path", "data.video.video_id")
        return resolve_jsonpath(body, video_field) or ""

    async def upload_cover(self, cover_url: str) -> str:
        cfg = self.platform_config.config
        upload_url = cfg.get("api_cover_upload_url",
                              "https://open.douyin.com/api/douyin/v1/video/cover_upload/")
        access_token = await self.get_oauth_token()
        open_id = await self._get_open_id()
        async with httpx.AsyncClient(timeout=60) as client:
            img_resp = await client.get(cover_url)
            img_resp.raise_for_status()
            files = {"image": ("cover.jpg", img_resp.content, "image/jpeg")}
            data = {"open_id": open_id}
            headers = {"Authorization": f"Bearer {access_token}"}
            resp = await client.post(upload_url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            body = resp.json()
        cover_field = cfg.get("api_cover_id_path", "data.image.image_id")
        return resolve_jsonpath(body, cover_field) or ""

    async def create_post(self, platform_video_id: str, cover_id: str,
                          content: PublishContent) -> str:
        cfg = self.platform_config.config
        publish_url = cfg.get("api_publish_url",
                               "https://open.douyin.com/api/douyin/v1/video/publish_video/")
        access_token = await self.get_oauth_token()
        open_id = await self._get_open_id()
        body = {
            "video_id": platform_video_id,
            "cover_id": cover_id,
            "text": content.description or content.title,
            "open_id": open_id,
        }
        headers = {"Authorization": f"Bearer {access_token}",
                   "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(publish_url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        post_field = cfg.get("api_post_id_path", "data.item_id")
        return resolve_jsonpath(data, post_field) or ""

    async def _get_open_id(self) -> str:
        if self._open_id:
            return self._open_id
        # fallback: 从 platform_authorizations 读
        from db_clients.mysql import get_mysql_client
        mysql = get_mysql_client()
        tenant = self.platform_config.config.get("tenant_id", "default")
        row = await mysql.fetchone(
            "SELECT platform_user_id FROM platform_authorizations "
            "WHERE platform=%s AND tenant_id=%s AND status='active' LIMIT 1",
            (self.platform_id, tenant))
        return row["platform_user_id"] if row else ""
```

**How**: 继承 `BasePlatformPublisher` 自动获益于 Step 19.1 的真实令牌刷新;`_open_id` 由 `refresh_token_if_needed` 填充(从 OAuth 响应),fallback 查 DB;URL/path 全可配(`platform_config.api_upload_url` 等)。

#### 21.2 修改 [worker_publishers.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/publish_dispatcher/worker_publishers.py) + [main.py](file:///c:/Users/29048/PycharmProjects/PythonProject1/project/backend/publish_dispatcher/main.py)

**What** (worker_publishers.py 完整重写):
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import logging
from platform_connectors.registry import PublisherRegistry
from platform_connectors.models import PlatformAdapterConfig
from platform_connectors.generic_http_publisher import GenericHTTPPublisher
from platform_connectors.douyin_publisher import DouyinPublisher
logger = logging.getLogger(__name__)

_PUBLISHER_CLASSES = {
    "douyin": DouyinPublisher,
    "youtube": GenericHTTPPublisher,
    "tiktok": GenericHTTPPublisher,
    "instagram": GenericHTTPPublisher,
}

worker_publishers = PublisherRegistry()
for _p, _cls in _PUBLISHER_CLASSES.items():
    worker_publishers.register_publisher(_p, _cls)

async def load_worker_publisher_configs() -> None:
    """从 platform_config 表加载各平台配置,注入 registry。"""
    try:
        from db_clients.mysql import get_mysql_client
        mysql = get_mysql_client()
        rows = await mysql.fetchall(
            "SELECT platform, config_key, config_value FROM platform_config")
        cfg_by_platform: dict[str, dict[str, str]] = {}
        for row in rows:
            cfg_by_platform.setdefault(row["platform"], {})[row["config_key"]] = row["config_value"]
        configs = []
        for platform, cfg in cfg_by_platform.items():
            cfg.setdefault("tenant_id", "default")
            configs.append(PlatformAdapterConfig(
                platform_id=platform,
                connector_class=cfg.get("connector_class", "GenericHTTPPublisher"),
                config=cfg))
        worker_publishers.load_from_config(configs)
        logger.info("worker_publisher_configs_loaded", count=len(configs))
    except Exception as e:
        logger.warning("load_worker_publisher_configs_failed", error=str(e))
```

**What** (main.py lifespan 加载):
```python
# 在 lifespan startup 末尾(MySQL 连接后)加:
from worker_publishers import load_worker_publisher_configs
await load_worker_publisher_configs()
```

**How**: 启动时从 DB 加载真实配置注入 registry;fail-soft(加载失败用注册时的默认空 config);worker 单例模式(模块级 `worker_publishers`)。

---

### Step 22:12 个单元测试

#### 22.1 创建 `tests/test_phase5_oauth.py` (3 tests)

1. `test_oauthflow_build_auth_url`:构造 `OAuthFlow("douyin", {oauth_client_id, oauth_auth_url, oauth_redirect_uri, oauth_scope, oauth_client_id_field="client_key"})`,调 `build_auth_url("state123")`,断言 URL 含 `client_key=...`、`state=state123`、`scope=...`
2. `test_oauthflow_exchange_code`:mock `httpx.AsyncClient.post` 返回 `{"data": {"access_token": "at123", "refresh_token": "rt456", "open_id": "oid789", "expires_in": 3600}}`,cfg 用 `oauth_token_path="$.data.access_token"` 等,断言 `_normalize` 返回正确字段
3. `test_oauthflow_refresh`:类似 2,验证 `refresh()` 调用 refresh_url + body 含 `grant_type=refresh_token`

#### 22.2 创建 `tests/test_phase5_crawlers.py` (4 tests)

1. `test_douyin_crawler_with_mock_playwright`:patch `connectors.douyin_crawler.render_page` 返回 `[{title, video_id, cover_url, ...}]`,cfg 含 `crawler_mapping`,断言返回 `CrawlResult` 含 `StandardProduct`,`platform="douyin"`
2. `test_taobao_crawler_fail_soft`:patch `render_page` 返回 `None`,断言返回空 `CrawlResult`,不抛异常
3. `test_amazon_crawler_with_mock_httpx`:patch `httpx.AsyncClient.post` 返回 `{"SearchResult": {"Items": [...]}}`,断言 products 列表正确
4. `test_persist_products_upsert`:mock `MySQLClient.executemany`,调 `persist_products`,断言 SQL 含 `ON DUPLICATE KEY UPDATE` 且参数行数 = 输入数

#### 22.3 创建 `tests/test_phase5_publishers.py` (5 tests)

1. `test_base_publisher_refresh_token_if_needed`:构造 `BasePlatformPublisher` 子类(实例化抽象类用 `type("X", (BasePlatformPublisher,), {"publish": ...})`),patch `vault_client.get_platform_refresh_token` 返回 `"rt"`,patch `OAuthFlow.refresh` 返回固定 token,断言 `_access_token`/`_token_expires_at` 被设置 + `store_platform_refresh_token` 被调用
2. `test_base_publisher_get_oauth_token_fallback_env`:vault 返回 None,env `{PLATFORM}_ACCESS_TOKEN=env_tok`,断言 `get_oauth_token()` 返回 `"env_tok"`
3. `test_douyin_publisher_upload_and_post`:patch `httpx.AsyncClient.get`/`.post` + `get_oauth_token`,调 `upload_video` → `create_post`,断言 Bearer header + open_id 在 body
4. `test_worker_publishers_registry_has_douyin`:`from worker_publishers import worker_publishers`,断言 `get_publisher("douyin")` 返回 `DouyinPublisher` 实例
5. `test_web_backend_callback_exchange_and_store`:patch `RedisClient.get`/`delete` + `MySQLClient.fetchall`/`.execute` + `OAuthFlow.exchange_code`,POST `/platforms/callback`,断言 `vault_client.store_platform_refresh_token` 被调 + SQL 含 `INSERT INTO platform_authorizations`

**测试文件头**(统一):
```python
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))
```

---

## 验证步骤

实施完成后执行:

1. **单文件测试**:
   ```
   python -m pytest tests/test_phase5_oauth.py tests/test_phase5_crawlers.py tests/test_phase5_publishers.py -v
   ```
   预期:12 passed

2. **回归测试**(确保 Step 18-22 不破坏现有):
   ```
   python -m pytest tests/ -v --tb=short
   ```
   预期:~125 passed(现有 ~113 + 新 12)

3. **导入完整性**:
   ```
   python -c "from platform_connectors.oauth import OAuthFlow; from platform_connectors.douyin_publisher import DouyinPublisher; from platform_connectors.base_publisher import BasePlatformPublisher; print('ok')"
   python -c "import sys; sys.path.insert(0, 'project/backend/crawl_scheduler'); from connectors import build_crawler_registry; from persistence import persist_products; print('ok')"
   python -c "import sys; sys.path.insert(0, 'project/backend/publish_dispatcher'); from worker_publishers import worker_publishers, load_worker_publisher_configs; print(worker_publishers.list_platforms())"
   ```
   预期:全部 ok,worker_publishers.list_platforms() 含 douyin/youtube/tiktok/instagram

4. **schema 一致性检查**:确认 `web_backend/routes.py` callback SQL 列名与 `init.sql` `platform_authorizations` 表一致(`token_encrypted`/`platform_user_id`/`access_token_expires_at`/`status`)

5. **fail-soft 验证**:测试中爬虫 mock 返回 None / 抛异常时,`CrawlResult(products=[])` 而非向上抛

---

## 范围外(不做)

- 真实 Playwright 浏览器测试(需安装浏览器,CI 环境复杂)— 全 mock
- 真实抖音/Amazon API 调用(需真实凭据)— 配置在 `platform_config` 表,运行时注入
- AWS Signature v4 签名(Amazon PA-API)— 用 Bearer 简化,真实签名可后续加
- 删除项目根 `nul` 文件 — 不在本计划范围
- 现有 `test_platform_connectors.py` 改动 — 不重叠(新测试在 `test_phase5_*.py`)
