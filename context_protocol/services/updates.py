"""Transactional mutation application; payload validation belongs to command models."""
import hashlib
import json
from typing import Literal
from ..models.commands import ContextUpdate
from ..models.context import Model, JsonObject
from ..models.persistence import ContextRow, ReceiptRow, UpdateReceipt, UpdateResult
from ..domain.updates.registry import UpdateRegistry
from ..domain.updates.base import UpdateContext
from ..storage.base import ContextRepository
from .access import identity, now, current_instance
from .errors import Conflict

class MutationFingerprint(Model):
    protocol_version: str
    update_id: str
    type: str
    source: str
    turn_id: str | None
    payload: JsonObject
    actor: str

class UpdateService:
    def __init__(self, repository: ContextRepository, registry: UpdateRegistry):
        self.repository, self.registry = repository, registry

    def apply(self, sid: str, update: ContextUpdate, expected_revision: int | None = None,
              *, actor: Literal["editor", "producer", "legacy"] = "editor") -> UpdateResult:
        sid = identity(sid)
        command = update.root
        # Only serialization boundaries produce mappings. The command itself remains typed.
        fingerprint = MutationFingerprint(protocol_version=command.protocol_version, update_id=command.update_id,
            type=command.type, source=command.source, turn_id=command.turn_id, actor=actor,
            payload=JsonObject(command.payload.model_dump(mode="json", by_alias=True, exclude_unset=True)))
        digest = hashlib.sha256(json.dumps(fingerprint.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        expected = expected_revision if expected_revision is not None else command.expected_revision
        with self.repository.transaction(write=True) as tx:
            current = current_instance(tx, sid, lock=True)
            previous = tx.receipt(sid, command.update_id)
            if previous:
                if previous.digest != digest:
                    raise Conflict("update_id 已用于其他内容")
                return UpdateResult(update=UpdateReceipt(update_id=command.update_id, seq=previous.revision), duplicate=True)
            if expected is not None and expected != current.revision:
                raise Conflict("上下文已被修改，请刷新后重试")
            revision = current.revision+1
            self.registry.apply(UpdateContext(state=current.state, seq=revision, turn_id=command.turn_id, actor=actor), command)
            tx.save_context(ContextRow(session_id=sid, state=current.state, updated_at=now()))
            tx.set_version(sid, revision)
            tx.insert_receipt(ReceiptRow(session_id=sid, update_id=command.update_id, digest=digest, revision=revision))
            return UpdateResult(update=UpdateReceipt(update_id=command.update_id, seq=revision), duplicate=False)
