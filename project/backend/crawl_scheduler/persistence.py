from __future__ import annotations

import json
import logging

from db_clients.mysql import get_mysql_client
from platform_connectors.models import StandardProduct

logger = logging.getLogger(__name__)


async def persist_products(products: list[StandardProduct], tenant_id: str = "default") -> tuple[int, list[int]]:
    if not products:
        return 0, []
    mysql = get_mysql_client()
    sql = (
        "INSERT INTO products "
        "(tenant_id, platform, platform_product_id, title, description, main_image_url, "
        "image_urls, price, currency, sales_count, rating, category, tags, raw_data, status) "
        "VALUES (" + ",".join(["%s"] * 15) + ") "
        "ON DUPLICATE KEY UPDATE "
        "title = VALUES(title), description = VALUES(description), "
        "main_image_url = VALUES(main_image_url), image_urls = VALUES(image_urls), "
        "price = VALUES(price), currency = VALUES(currency), sales_count = VALUES(sales_count), "
        "rating = VALUES(rating), category = VALUES(category), tags = VALUES(tags), "
        "raw_data = VALUES(raw_data), status = 'active'"
    )
    rows = []
    for p in products:
        rows.append(
            (
                tenant_id,
                p.platform,
                p.platform_product_id,
                p.title,
                p.description,
                p.main_image_url,
                json.dumps(p.image_urls or []),
                p.price,
                p.currency,
                p.sales_count,
                p.rating,
                p.category,
                json.dumps(p.tags or []),
                json.dumps(p.raw_data or {}, ensure_ascii=False, default=str),
                "active",
            )
        )
    try:
        await mysql.execute_many(sql, rows)
    except Exception as e:
        logger.warning("persist_products_failed", error=str(e), count=len(rows))
        return 0, []

    platform = products[0].platform
    pid_list = [p.platform_product_id for p in products]
    placeholders = ",".join(["%s"] * len(pid_list))
    id_rows = await mysql.fetchall(
        f"SELECT id FROM products WHERE platform=%s AND platform_product_id IN ({placeholders})",
        (platform, *pid_list),
    )
    product_ids = [r["id"] for r in id_rows] if id_rows else []
    return len(rows), product_ids
