"""HTTP SDK: typed operations, with JSON compatibility methods at the wire boundary."""
from uuid import uuid4
import httpx
from pydantic import TypeAdapter
from .models.commands import ContextUpdate, Command
from .models.context import Model, JsonObject
from .models.requests import SessionInput, ConnectInput, BuildInput
from .models.persistence import TemplateInfo, InstanceInfo, ConnectionResult, InstanceView, UpdateResult, UpdateReceipt
from .models.compilation import CompiledContext

class WireUpdate(Model):
    protocol_version: str = "0.1"
    update_id: str
    type: str
    source: str
    turn_id: str | None
    payload: JsonObject
    expected_revision: int | None = None

class ContextClient:
    @staticmethod
    def template_catalog(client: httpx.Client) -> list[TemplateInfo]:
        response = client.get("/templates")
        response.raise_for_status()
        return TypeAdapter(list[TemplateInfo]).validate_python(response.json())

    @staticmethod
    def templates(client: httpx.Client):
        return [item.model_dump(mode="json") for item in ContextClient.template_catalog(client)]

    @classmethod
    def from_template(cls, client: httpx.Client, template_id: str, *, name: str, agent_id=None, slug=None, source="agent-adapter"):
        request = SessionInput(name=name, template_id=template_id, agent_id=agent_id, slug=slug)
        response = client.post("/sessions", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return cls(client, InstanceInfo.model_validate(response.json()).id, source=source)

    @classmethod
    def connect(cls, client: httpx.Client, template_id: str, slug: str, *, agent_id=None, name=None,
                create=True, source="agent-adapter"):
        request = ConnectInput(slug=slug, name=name, agent_id=agent_id, create=create)
        response = client.post(f"/templates/{template_id}/instances/connect", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return cls(client, ConnectionResult.model_validate(response.json()).instance.id, source=source)

    def __init__(self, client: httpx.Client, session_id: str, *, source="agent-adapter"):
        self.client, self.session_id, self.source = client, session_id, source

    @property
    def path(self) -> str:
        return f"/sessions/{self.session_id}"

    def submit(self, command: Command) -> UpdateReceipt:
        command.source = self.source
        body = WireUpdate(protocol_version=command.protocol_version, update_id=command.update_id,
            type=command.type, source=command.source, turn_id=command.turn_id, expected_revision=command.expected_revision,
            payload=JsonObject(command.payload.model_dump(mode="json", by_alias=True, exclude_unset=True))).model_dump(mode="json")
        for attempt in range(2):
            try:
                response = self.client.post(self.path+"/ingest", json=body)
                response.raise_for_status()
                return UpdateResult.model_validate(response.json()).update
            except httpx.TransportError:
                if attempt:
                    raise

    def view(self) -> InstanceView:
        response = self.client.get(self.path)
        response.raise_for_status()
        return InstanceView.model_validate(response.json())

    def compile(self, budget_chars: int = 24000) -> CompiledContext:
        response = self.client.post(self.path+"/compile", json=BuildInput(budget_chars=budget_chars).model_dump())
        response.raise_for_status()
        return CompiledContext.model_validate(response.json())

    # Compatibility for external integrations that exchange the documented JSON wire format.
    def emit(self, kind, payload, turn_id=None, update_id=None):
        wire = WireUpdate(update_id=update_id or str(uuid4()), type=kind, source=self.source,
                          turn_id=turn_id, payload=JsonObject(payload))
        command = ContextUpdate.model_validate(wire.model_dump(mode="json")).root
        return self.submit(command).model_dump(mode="json")

    def state(self):
        return self.view().model_dump(mode="json", by_alias=True)

    def build(self, budget_chars=24000):
        return self.compile(budget_chars).model_dump(mode="json", by_alias=True)
