from __future__ import annotations


def calculate_product_score(product_data: dict, threshold: float = 70.0) -> float:
    sales_count = float(product_data.get("sales_count", 0) or 0)
    rating = float(product_data.get("rating", 0) or 0)
    price = float(product_data.get("price", 0) or 0)

    hotness = min(sales_count / 10000.0, 1.0) * 40.0 + rating * 20.0
    conversion = max(0.0, min((1.0 - price / 1000.0) * 35.0, 35.0))
    profit = max(0.0, 1.0 - price / 10000.0) * 25.0

    total = hotness + conversion + profit
    return min(total, 100.0)


def determine_tier(score: float, threshold: float) -> str:
    if score >= threshold:
        return "hot"
    if score >= threshold * 0.6:
        return "normal"
    return "cold"


def get_score_breakdown(product_data: dict) -> dict[str, float]:
    sales_count = float(product_data.get("sales_count", 0) or 0)
    rating = float(product_data.get("rating", 0) or 0)
    price = float(product_data.get("price", 0) or 0)

    hotness = min(sales_count / 10000.0, 1.0) * 40.0 + rating * 20.0
    conversion = max(0.0, min((1.0 - price / 1000.0) * 35.0, 35.0))
    profit = max(0.0, 1.0 - price / 10000.0) * 25.0

    return {
        "hotness": round(hotness, 2),
        "conversion": round(conversion, 2),
        "profit": round(profit, 2),
        "total": round(min(hotness + conversion + profit, 100.0), 2),
    }
