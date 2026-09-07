"""Layer 9: Stages & Flow.

Pipelines, stages, flow channels, and promotions — the mechanics
of how information moves through the system.
"""

from scoped.flow.engine import FlowEngine, FlowResolution
from scoped.flow.models import (
    FlowChannel,
    FlowPointType,
    Pipeline,
    Promotion,
    Stage,
    StageTransition,
)
from scoped.flow.pipeline import PipelineManager
from scoped.flow.promotion import PromotionManager

__all__ = [
    "FlowChannel",
    "FlowEngine",
    "FlowPointType",
    "FlowResolution",
    "Pipeline",
    "PipelineManager",
    "Promotion",
    "PromotionManager",
    "Stage",
    "StageTransition",
]
