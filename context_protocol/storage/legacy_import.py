"""One-time, read-only interpretation of archived event data; never a runtime history API."""
import copy
from datetime import datetime, timezone

from sqlalchemy import inspect, select

from ..models.persistence import ContextRow
from ..models.context import ContextState
from ..protocol import initial_state, reduce_event, upgrade_state
from .legacy_sql import SQLUnitOfWork, make_tables


def import_current_contexts(repository):
    names = set(inspect(repository.engine).get_table_names())
    if not {"events", "snapshots"}.issubset(names):
        return
    _, tables = make_tables()
    with repository.transaction(write=True) as tx:
        old = SQLUnitOfWork(tx.connection, tables, repository.row_locks)
        def restore(sid, seq):
            branch = old.branch(sid) if "branches" in names else None
            if branch and branch["parent_id"] and seq <= branch["base_seq"]:
                return restore(branch["parent_id"], seq)
            snap = old.latest_snapshot(sid, seq)
            state = copy.deepcopy(snap["state"]) if snap else initial_state()
            for event in old.events(sid, after=snap["seq"] if snap else 0, upto=seq):
                reduce_event(state, event)
            return upgrade_state(state)
        for session in old.sessions():
            sid = session["id"]
            branch = old.branch(sid) if "branches" in names else None
            if branch and branch["root_id"] != sid:
                continue
            tx.session(sid, lock=True)
            if tx.context(sid) is not None:
                continue
            selection = old.selection(sid) if "context_selections" in names else None
            selected = (selection or {}).get("branch_id", sid)
            stream = old.session(selected)
            revision = (selection or {}).get("seq")
            if revision is None:
                revision = stream["version"]
            state = restore(selected, revision)
            tx.save_context(ContextRow(session_id=sid, state=ContextState.model_validate(state), updated_at=datetime.now(timezone.utc)))
            tx.set_version(sid, revision)
