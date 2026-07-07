"""Business metrics registry (Counters/Histograms).

All metrics are registered with the default prometheus_client registry,
so they are automatically exposed on /metrics alongside the HTTP
auto-instrumentation metrics from setup_metrics.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# Crawl
crawl_jobs_total = Counter(
    "crawl_jobs_total",
    "Total crawl jobs processed",
    ["platform", "status"],
)
crawl_products_found = Histogram(
    "crawl_products_found",
    "Products found per crawl job",
    ["platform"],
    buckets=(0, 10, 50, 100, 500, 1000, 5000),
)

# AI Generation
ai_generation_requests_total = Counter(
    "ai_generation_requests_total",
    "Total AI generation requests",
    ["adapter_type", "model", "status"],
)
ai_generation_duration_seconds = Histogram(
    "ai_generation_duration_seconds",
    "AI generation duration in seconds",
    ["adapter_type"],
    buckets=(0.5, 1, 5, 10, 30, 60, 120, 300),
)

# Video Compose
video_compose_jobs_total = Counter(
    "video_compose_jobs_total",
    "Total video compose jobs",
    ["status"],
)

# Publish
publish_jobs_total = Counter(
    "publish_jobs_total",
    "Total publish jobs",
    ["platform", "status"],
)

# Pipeline
pipeline_runs_total = Counter(
    "pipeline_runs_total",
    "Total pipeline runs",
    ["status"],
)
