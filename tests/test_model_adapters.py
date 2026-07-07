import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest
from model_adapters import (
    BaseModelAdapter, UsageRecord,
    AdapterRegistry, ModelRouter, CostCalculator,
)


class StubAdapter(BaseModelAdapter):
    def generate(self, request: dict) -> dict:
        return {"task_id": "stub"}
    def check_health(self) -> bool:
        return self.is_healthy


def _adapter(aid, atype="llm", healthy=True, prio=10):
    a = StubAdapter(
        adapter_id=aid, adapter_type=atype, model=aid,
        endpoint="http://localhost", priority=prio, max_concurrency=5,
    )
    a.is_healthy = healthy
    a.failure_count = 0
    return a


class TestBaseModelAdapter:
    def test_adapter_creation(self):
        a = _adapter("test", "llm", True, 10)
        assert a.adapter_id == "test"
        assert a.adapter_type == "llm"
        assert a.can_accept() is True

    def test_mark_failure_degradation(self):
        a = _adapter("test")
        a.mark_failure()
        assert a.failure_count == 1
        assert a.can_accept() is True
        a.mark_failure()
        a.mark_failure()
        assert a.failure_count == 3
        assert a.can_accept() is False

    def test_mark_success_resets(self):
        a = _adapter("test")
        a.failure_count = 5
        a.mark_success()
        assert a.failure_count == 0


class TestAdapterRegistry:
    def test_register_and_get(self):
        r = AdapterRegistry()
        r.register(_adapter("gpt4o"))
        f = r.get_adapter("gpt4o")
        assert f is not None
        assert f.adapter_id == "gpt4o"

    def test_list_by_type(self):
        r = AdapterRegistry()
        for i in range(3): r.register(_adapter(f"llm_{i}"))
        for i in range(2): r.register(_adapter(f"img_{i}", "image"))
        assert len(r.list_adapters("llm")) == 3
        assert len(r.list_adapters("image")) == 2

    def test_healthy_filter(self):
        r = AdapterRegistry()
        r.register(_adapter("ok", healthy=True))
        r.register(_adapter("bad", healthy=False))
        hl = r.get_healthy_adapters("llm")
        assert len(hl) == 1
        assert hl[0].adapter_id == "ok"

    def test_unregister(self):
        r = AdapterRegistry()
        r.register(_adapter("temp"))
        r.unregister("temp")
        assert r.get_adapter("temp") is None

    def test_to_config_yaml(self):
        r = AdapterRegistry()
        r.register(_adapter("gpt"))
        assert "gpt" in r.to_config_yaml()


class TestModelRouter:
    def test_route_specific_model(self):
        r = AdapterRegistry()
        r.register(_adapter("m1", prio=10))
        r.register(_adapter("m2", prio=5))
        router = ModelRouter(r)
        result = router.route("llm", "normal", "m2")
        assert result.adapter_id == "m2"

    def test_route_hot_tier_highest_priority(self):
        r = AdapterRegistry()
        r.register(_adapter("low", prio=20))
        r.register(_adapter("high", prio=5))
        router = ModelRouter(r)
        result = router.route("llm", "hot", None)
        assert result.adapter_id == "high"

    def test_route_no_healthy_returns_none(self):
        r = AdapterRegistry()
        r.register(_adapter("dead", healthy=False))
        router = ModelRouter(r)
        assert router.route("llm", "normal", None) is None

    def test_route_unknown_model(self):
        r = AdapterRegistry()
        r.register(_adapter("m1"))
        router = ModelRouter(r)
        assert router.route("llm", "normal", "nonexistent") is None


class TestCostCalculator:
    def test_calculate_llm_cost(self):
        calc = CostCalculator()
        cost = calc.calculate_cost("llm", "gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost > 0

    def test_calculate_image_cost(self):
        calc = CostCalculator()
        cost = calc.calculate_cost("image", "dall-e-3", image_count=2)
        assert cost > 0

    def test_calculate_video_cost(self):
        calc = CostCalculator()
        cost = calc.calculate_cost("video", "sora", duration_seconds=10)
        assert cost > 0

    def test_calculate_unknown_model_returns_zero(self):
        calc = CostCalculator()
        cost = calc.calculate_cost("llm", "__unknown__", input_tokens=1000)
        assert cost == 0.0

    def test_calculate_sdxl_cost(self):
        calc = CostCalculator()
        cost = calc.calculate_cost("image", "sdxl", image_count=5)
        assert cost > 0


class TestUsageRecord:
    def test_usage_record_creation(self):
        rec = UsageRecord(adapter_id="gpt4o", adapter_type="llm", model="gpt-4o", pipeline_id="p1", tenant_id="default", input_tokens=500, output_tokens=200, estimated_cost_usd=0.015, status="success")
        assert rec.adapter_id == "gpt4o"
        assert rec.input_tokens == 500
