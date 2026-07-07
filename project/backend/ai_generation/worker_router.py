from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from common_sdk.logger import get_logger

from .registry_manager import RegistryManager
from .router import ModelRouterService

logger = get_logger(__name__)

_reg_manager = RegistryManager()
_reg_manager.register_default_adapters()
worker_router: ModelRouterService = ModelRouterService(_reg_manager.registry)

logger.info("worker_router_initialized", adapter_count=len(_reg_manager.registry.list_adapters()))

__all__ = ["worker_router"]
