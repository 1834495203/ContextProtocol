"""HTTP request DTOs. Endpoint handlers contain no inline schema definitions."""
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from .commands import ContextUpdate

class SessionInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    demo: bool = False
    slug: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    template_id: UUID | None = None
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_demo(self):
        if self.demo and self.template_id:
            raise ValueError("示例运行内容不能与模板初始化同时使用")
        return self


class TemplateInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    source_template_id: UUID | None = None
    source_context_id: UUID | None = None


class TemplateMetadata(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    expected_revision: int = Field(ge=0)


class InstanceInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)


class InstanceMetadata(InstanceInput):
    expected_revision: int = Field(ge=0)


class AssignInput(BaseModel):
    model_config = {"extra": "forbid"}
    template_id: UUID
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)


class ConnectInput(BaseModel):
    model_config = {"extra": "forbid"}
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    create: bool = True



class BuildInput(BaseModel):
    model_config = {"extra": "forbid"}
    budget_chars: int = Field(default=24000, ge=100, le=2000000)


UpdateInput = ContextUpdate
