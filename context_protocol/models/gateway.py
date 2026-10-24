"""Central tool-source configuration and execution wire contracts."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, JsonValue, model_validator
from .context import Model, RegisteredTool, JsonObject

SourceId = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]


class Connection(Model):
    model_config = ConfigDict(extra='forbid')


class StdioSource(Connection):
    transport: Literal['stdio'] = 'stdio'
    command: str = Field(min_length=1, max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=256)
    cwd: str | None = None
    env_refs: dict[str, str] = Field(default_factory=dict)


class HttpSource(Connection):
    transport: Literal['streamable_http'] = 'streamable_http'
    url: str
    header_env_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_url(self):
        from urllib.parse import urlsplit
        parsed = urlsplit(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Use an HTTP(S) URL without embedded credentials; use header_env_refs for credentials')
        return self


class ContextSource(Connection):
    transport: Literal['context'] = 'context'
    tools: list[Literal['state.set', 'memory.append']] = Field(default_factory=lambda: ['state.set'])


class LocalSource(Connection):
    transport: Literal['local'] = 'local'
    registry_key: str = Field(min_length=1, max_length=200)


ToolConnection = Annotated[StdioSource | HttpSource | ContextSource | LocalSource, Field(discriminator='transport')]


class ToolSourceInput(Connection):
    id: SourceId
    title: str = Field(min_length=1, max_length=120)
    connection: ToolConnection
    enabled: bool = True
    expected_revision: int | None = Field(default=None, ge=0)


class ToolSourceRow(Model):
    id: SourceId
    title: str
    connection: ToolConnection
    enabled: bool = True
    revision: int = 1
    tools: list[RegisteredTool] = Field(default_factory=list)
    discovered_at: datetime | None = None
    last_error: str | None = None


class ToolSourceView(Model):
    id: str
    title: str
    connection: ToolConnection
    enabled: bool
    revision: int
    tools: list[RegisteredTool]
    discovered_at: datetime | None
    last_error: str | None
    availability: Literal['unconfirmed'] = 'unconfirmed'


class GatewayCallInput(Connection):
    preparation_id: str = Field(min_length=1, max_length=200)
    call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    arguments: JsonObject
    assistant_seq: int = Field(ge=1)
    turn_id: str | None = Field(default=None, max_length=128)


class GatewayCallResult(Model):
    call_id: str
    output: JsonValue
    is_error: bool
    duplicate: bool = False
    revision: int
