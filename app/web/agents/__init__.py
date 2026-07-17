from __future__ import annotations

from app.web.agents.adapter import AdapterAgent, AdapterResult, adapter_agent
from app.web.agents.base import BaseAgent
from app.web.agents.template import TemplateAgent, AnalysisResult, template_agent

__all__ = [
    "BaseAgent",
    "TemplateAgent",
    "AdapterAgent",
    "AnalysisResult",
    "AdapterResult",
    "template_agent",
    "adapter_agent",
]
