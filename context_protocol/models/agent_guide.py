"""Public, language-independent bootstrap information for agent developers."""
from typing import Literal
from pydantic import Field
from .context import Model, JsonObject


class GuideLinks(Model):
    guide: str
    markdown: str
    openapi: str
    protocol: str
    health: str
    api_docs: str
    context_tools: str
    tool_sources: str


class GuideStep(Model):
    id: str
    description: str
    method: Literal["GET", "POST"]
    path: str
    body: JsonObject | None = None
    notes: list[str] = Field(default_factory=list)


class GuideResource(Model):
    path: str
    media_type: str
    description: str
    content: str


class AgentGuide(Model):
    guide_version: str = "0.2"
    title: str = "Context Protocol Agent 接入说明"
    service_base_url: str
    api_base_url: str
    links: GuideLinks
    required_inputs: list[str]
    instructions: str
    rules: list[str]
    placeholders: dict[str, str]
    workflow: list[GuideStep]
    preferred_execution_mode: Literal['gateway'] = 'gateway'
    gateway_workflow: list[GuideStep]
    schemas: dict[str, JsonObject]
    resources: list[GuideResource]
    quickstart_argv: list[list[str]]
    limitations: list[str]
