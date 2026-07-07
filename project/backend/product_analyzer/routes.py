from __future__ import annotations

import json
import uuid

from celery import current_app
from fastapi import APIRouter, Depends, Request

from utils.common_sdk.response import error_response, success_response
from utils.db_clients import get_mysql_client, get_redis_client

from .auth import verify_internal_jwt
from .config import DEFAULT_SCORE_THRESHOLD, REDIS_HOT_SORTED_SET
from .models import AnalyzeRequest, ScoreConfig, TenantScoreConfig
from .scoring import get_score_breakdown

router = APIRouter(prefix="/api/v1", tags=["product-analyzer"])


@router.post("/analyze")
async def analyze_products(
    body: AnalyzeRequest,
    request: Request,
    _auth: dict = Depends(verify_internal_jwt),
):
    task_id = str(uuid.uuid4())
    tenant_id = getattr(request.state, "tenant_id", "default")

    mysql = get_mysql_client()
    tenant_config = await mysql.fetchone(
        "SELECT config_value FROM tenant_config WHERE tenant_id=%s AND config_key=%s",
        (tenant_id, "score_threshold"),
    )
    threshold = DEFAULT_SCORE_THRESHOLD
    if tenant_config:
        try:
            threshold = float(tenant_config["config_value"])
        except (ValueError, TypeError):
            threshold = DEFAULT_SCORE_THRESHOLD

    celery_app = current_app
    celery_app.send_task(
        "product_analyzer.analyze_products",
        args=[],
        kwargs={
            "task_id": task_id,
            "product_ids": body.product_ids,
            "platform": body.platform,
            "limit": body.limit,
            "threshold": threshold,
        },
        queue="analyze_queue",
    )

    redis = get_redis_client()
    await redis.set_task_progress(task_id, "queued", 0)

    return success_response({"task_id": task_id, "status": "queued"})


@router.get("/products/{product_id}/score")
async def get_product_score(
    product_id: int,
    request: Request,
    _auth: dict = Depends(verify_internal_jwt),
):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    product = await mysql.fetchone(
        "SELECT id, title, platform, score, tier, sales_count, rating, price FROM products WHERE id=%s AND tenant_id=%s",
        (product_id, tenant_id),
    )
    if not product:
        return error_response(404, "Product not found")

    breakdown = get_score_breakdown(product)
    return success_response({
        "product_id": product["id"],
        "title": product["title"],
        "platform": product["platform"],
        "score": float(product.get("score", 0) or 0),
        "tier": product.get("tier", "normal"),
        "dimensions": breakdown,
    })


@router.get("/products/hot")
async def get_hot_products(
    request: Request,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_jwt),
):
    tenant_id = getattr(request.state, "tenant_id", "default")
    redis = get_redis_client()
    results = await redis.zrange(REDIS_HOT_SORTED_SET, 0, limit - 1, desc=True, withscores=True)

    hot_products = []
    for product_id_str, score in results:
        hot_products.append({
            "product_id": int(product_id_str),
            "score": float(score),
        })

    if hot_products:
        mysql = get_mysql_client()
        ids = [p["product_id"] for p in hot_products]
        placeholders = ",".join(["%s"] * len(ids))
        rows = await mysql.fetchall(
            f"SELECT id, title, platform, tier FROM products WHERE id IN ({placeholders}) AND tenant_id=%s",
            tuple(ids) + (tenant_id,),
        )
        row_map = {row["id"]: row for row in rows}
        for p in hot_products:
            row = row_map.get(p["product_id"])
            if row:
                p["title"] = row["title"]
                p["platform"] = row["platform"]
                p["tier"] = row["tier"]

    return success_response(hot_products)


@router.get("/config/score")
async def get_score_config(
    request: Request,
    _auth: dict = Depends(verify_internal_jwt),
):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()

    row = await mysql.fetchone(
        "SELECT config_value FROM tenant_config WHERE tenant_id=%s AND config_key=%s",
        (tenant_id, "score_config"),
    )
    if row:
        try:
            config_data = json.loads(row["config_value"])
            return success_response(TenantScoreConfig(
                tenant_id=tenant_id,
                score_threshold=config_data.get("score_threshold", DEFAULT_SCORE_THRESHOLD),
                weight_hotness=config_data.get("weight_hotness", 0.4),
                weight_conversion=config_data.get("weight_conversion", 0.35),
                weight_profit=config_data.get("weight_profit", 0.25),
            ).model_dump())
        except (json.JSONDecodeError, TypeError):
            pass

    return success_response(TenantScoreConfig(
        tenant_id=tenant_id,
        score_threshold=DEFAULT_SCORE_THRESHOLD,
    ).model_dump())


@router.put("/config/score")
async def update_score_config(
    body: ScoreConfig,
    request: Request,
    _auth: dict = Depends(verify_internal_jwt),
):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()

    config_value = json.dumps({
        "score_threshold": body.score_threshold,
        "weight_hotness": body.weight_hotness,
        "weight_conversion": body.weight_conversion,
        "weight_profit": body.weight_profit,
    })

    existing = await mysql.fetchone(
        "SELECT id FROM tenant_config WHERE tenant_id=%s AND config_key=%s",
        (tenant_id, "score_config"),
    )

    if existing:
        await mysql.execute(
            "UPDATE tenant_config SET config_value=%s, updated_at=NOW() WHERE tenant_id=%s AND config_key=%s",
            (config_value, tenant_id, "score_config"),
        )
    else:
        await mysql.execute(
            "INSERT INTO tenant_config (tenant_id, config_key, config_value, description) VALUES (%s, %s, %s, %s)",
            (tenant_id, "score_config", config_value, "Product scoring configuration"),
        )

    await mysql.execute(
        "INSERT INTO tenant_config (tenant_id, config_key, config_value, description) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE config_value=%s, updated_at=NOW()",
        (tenant_id, "score_threshold", str(body.score_threshold), "Default score threshold for hot products",
         str(body.score_threshold)),
    )

    return success_response({"tenant_id": tenant_id, "score_threshold": body.score_threshold})
