"""Tests for product_analyzer/scoring.py — pure functions, no external dependencies."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest

# 直接导入源文件（通过 utils/ 已加 sys.path）
from project.backend.product_analyzer.scoring import (
    calculate_product_score,
    determine_tier,
    get_score_breakdown,
)


# ===================================================================
# TestCalculateProductScore
# ===================================================================


class TestCalculateProductScore:
    """calculate_product_score(product_data, threshold=70.0) -> float"""

    def test_high_sales_high_rating(self):
        """大量销售 + 高评分 → 接近满分"""
        score = calculate_product_score({
            "sales_count": 50000,
            "rating": 5.0,
            "price": 50.0,
        })
        assert 90.0 <= score <= 100.0

    def test_zero_sales_zero_rating(self):
        """无销售无评分 → 接近 0"""
        score = calculate_product_score({
            "sales_count": 0,
            "rating": 0,
            "price": 100.0,
        })
        assert score < 30.0

    def test_mid_range_product(self):
        """中等数据"""
        score = calculate_product_score({
            "sales_count": 5000,
            "rating": 3.5,
            "price": 200.0,
        })
        assert 30.0 <= score <= 80.0

    def test_caps_at_100(self):
        """极高评分和销售 → 不超过 100"""
        score = calculate_product_score({
            "sales_count": 999999,
            "rating": 5.0,
            "price": 1.0,
        })
        assert score == 100.0

    def test_missing_fields(self):
        """缺失字段 → 不报错，按 0 处理"""
        score = calculate_product_score({})
        assert isinstance(score, float)
        assert 0.0 <= score <= 30.0

    def test_high_price_reduces_score(self):
        """高价格应降低分数"""
        cheap = calculate_product_score({"sales_count": 1000, "rating": 4.0, "price": 50.0})
        expensive = calculate_product_score({"sales_count": 1000, "rating": 4.0, "price": 5000.0})
        assert expensive < cheap

    def test_none_values_treated_as_zero(self):
        """None 值应按 0 处理"""
        score = calculate_product_score({
            "sales_count": None,
            "rating": None,
            "price": None,
        })
        assert isinstance(score, float)
        assert 0.0 <= score <= 30.0


# ===================================================================
# TestDetermineTier
# ===================================================================


class TestDetermineTier:
    """determine_tier(score, threshold) -> 'hot' | 'normal' | 'cold'"""

    def test_above_threshold_returns_hot(self):
        assert determine_tier(85.0, 70.0) == "hot"

    def test_exactly_at_threshold_returns_hot(self):
        assert determine_tier(70.0, 70.0) == "hot"

    def test_between_60pct_and_threshold_returns_normal(self):
        """threshold*0.6 ≤ score < threshold → 'normal'"""
        assert determine_tier(50.0, 70.0) == "normal"
        assert determine_tier(42.0, 70.0) == "normal"

    def test_exactly_at_60pct_threshold_returns_normal(self):
        assert determine_tier(42.0, 70.0) == "normal"

    def test_below_60pct_threshold_returns_cold(self):
        assert determine_tier(30.0, 70.0) == "cold"
        assert determine_tier(0.0, 70.0) == "cold"

    def test_custom_threshold(self):
        """自定义 threshold"""
        assert determine_tier(50.0, 50.0) == "hot"
        assert determine_tier(30.0, 50.0) == "normal"
        assert determine_tier(20.0, 50.0) == "cold"

    def test_high_threshold_all_cold(self):
        """极高的 threshold 使所有分数都为 cold"""
        assert determine_tier(10.0, 100.0) == "cold"

    def test_low_threshold_all_hot(self):
        """极低的 threshold 使所有分数都为 hot"""
        assert determine_tier(10.0, 1.0) == "hot"


# ===================================================================
# TestGetScoreBreakdown
# ===================================================================


class TestGetScoreBreakdown:
    """get_score_breakdown(product_data) -> dict[str, float]"""

    def test_breakdown_contains_expected_keys(self):
        breakdown = get_score_breakdown({"sales_count": 1000, "rating": 4.0, "price": 200.0})
        assert "hotness" in breakdown
        assert "conversion" in breakdown
        assert "profit" in breakdown
        assert "total" in breakdown

    def test_breakdown_total_matches_calculate(self):
        """breakdown.total 应与 calculate_product_score 一致"""
        data = {"sales_count": 5000, "rating": 3.5, "price": 150.0}
        breakdown = get_score_breakdown(data)
        score = calculate_product_score(data)
        assert abs(breakdown["total"] - score) < 0.01

    def test_breakdown_high_price_low_profit(self):
        """高价格时 profit 应较低"""
        breakdown = get_score_breakdown({"sales_count": 0, "rating": 0, "price": 8000.0})
        assert breakdown["profit"] < 5.0

    def test_breakdown_zero_data(self):
        """零值数据的 breakdown"""
        breakdown = get_score_breakdown({"sales_count": 0, "rating": 0, "price": 0})
        assert breakdown["hotness"] == 0.0
        assert breakdown["conversion"] == 35.0  # price=0 → max conversion
        assert breakdown["profit"] == 25.0  # price=0 → max profit
        assert breakdown["total"] == 60.0

    def test_breakdown_missing_fields(self):
        """缺失字段不报错"""
        breakdown = get_score_breakdown({})
        assert isinstance(breakdown, dict)
        assert len(breakdown) == 4

    def test_breakdown_values_are_rounded(self):
        breakdown = get_score_breakdown({"sales_count": 1234, "rating": 4.2, "price": 299.0})
        for v in breakdown.values():
            # 最多2位小数
            s = str(v)
            if "." in s:
                decimal_places = len(s.split(".")[1])
                assert decimal_places <= 2, f"Value {v} has {decimal_places} decimal places"
