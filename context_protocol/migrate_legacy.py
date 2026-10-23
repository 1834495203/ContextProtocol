"""Explicit, non-destructive migration from the old PostgreSQL schema.

DATABASE_URL is read ONLY by this opt-in migration utility, never runtime.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv, set_key
from sqlalchemy import MetaData, Table, create_engine, select, text

from .storage.factory import create_repository, normalize_url

ROOT = Path(__file__).resolve().parent.parent


def normalized(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(v) for v in value]
    return value


def digest(rows):
    data = sorted([normalized(row) for row in rows], key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_legacy(url):
    url = normalize_url(url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("The legacy source must be PostgreSQL")
    engine = create_engine(url, isolation_level="REPEATABLE READ", connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = {}
            for name in ("sessions", "events", "snapshots", "builds"):
                table = Table(name, MetaData(), schema="context_protocol", autoload_with=conn)
                rows[name] = [dict(row) for row in conn.execute(select(table)).mappings()]
            return rows
    finally:
        engine.dispose()


def portable_rows(rows):
    result = {}
    for name, items in rows.items():
        result[name] = []
        for original in items:
            row = dict(original)
            for key in ("id", "session_id"):
                if key in row:
                    row[key] = str(row[key])
            if name == "builds":
                row["sha256"] = row["artifact"]["sha256"]
            result[name].append(row)
    return result


def repository_rows(repository):
    rows = {name: [] for name in ("sessions", "events", "snapshots", "builds")}
    with repository.transaction() as tx:
        from .storage.legacy_sql import SQLUnitOfWork, make_tables
        _, tables = make_tables()
        tx = SQLUnitOfWork(tx.connection, tables, repository.row_locks)
        rows["sessions"] = tx.sessions()
        for session in rows["sessions"]:
            sid = session["id"]
            rows["events"].extend(tx.events(sid))
            rows["snapshots"].extend(tx.snapshots(sid))
            rows["builds"].extend(tx.builds(sid))
    return rows


def copy_records(rows, target):
    """Idempotent full copy. Refuse to merge an unrelated/nonidentical target."""
    rows = portable_rows(rows)
    target.initialize()
    from .storage.legacy_sql import make_tables
    archive_metadata, archive_tables = make_tables()
    archive_metadata.create_all(target.engine)
    existing = repository_rows(target)
    if existing["sessions"]:
        if any(digest(existing[name]) != digest(rows[name]) for name in rows):
            raise ValueError("Target is nonempty and differs from source; refusing to overwrite or merge")
    else:
        with target.transaction(write=True) as tx:
            for name in ("sessions", "events", "snapshots", "builds"):
                for row in rows[name]:
                    tx.connection.execute(archive_tables[name].insert().values(**row))
    copied = repository_rows(target)
    report = {}
    for name in rows:
        source_hash, target_hash = digest(rows[name]), digest(copied[name])
        if source_hash != target_hash:
            raise ValueError(f"Migration verification failed for {name}")
        report[name] = {"count": len(rows[name]), "sha256": target_hash}
    from .store import Store
    Store(target).initialize()  # Materialize each imported current context once.
    return report


def create_postgres_database(url):
    """Administrative provisioning is opt-in, never done by application startup."""
    url = normalize_url(url)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise ValueError("--create-database requires a PostgreSQL target URL")
    if url.database in ("postgres", "template0", "template1"):
        raise ValueError("Refusing to use a PostgreSQL system database")
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": url.database}).first():
                quoted = conn.dialect.identifier_preparer.quote_identifier(url.database)
                conn.exec_driver_sql("CREATE DATABASE " + quoted)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", default="DATABASE_URL", help="Environment variable holding the legacy source URL")
    parser.add_argument("--target-env", default="CONTEXT_DATABASE_URL", help="Environment variable holding the target URL")
    parser.add_argument("--target-database", help="Derive target PostgreSQL connection from source, using this database name")
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument("--write-config", action="store_true", help="Write verified target URL to .env, leaving DATABASE_URL unchanged")
    parser.add_argument("--report", default="docs/storage-migration-report.json")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    source = normalize_url(os.environ[args.source_env])
    target_value = source.set(database=args.target_database) if args.target_database else normalize_url(os.environ[args.target_env])
    if source == target_value:
        raise ValueError("Source and target must be different databases")
    if args.create_database:
        create_postgres_database(target_value.render_as_string(hide_password=False))
    rows = read_legacy(source.render_as_string(hide_password=False))
    target = create_repository(target_value.render_as_string(hide_password=False))
    try:
        report = {"source": {"host": source.host, "database": source.database, "schema": "context_protocol"},
                  "target": {"backend": target.backend, "host": target_value.host, "database": target_value.database},
                  "tables": copy_records(rows, target), "source_preserved": True, "verified": True}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.write_config:
            set_key(ROOT / ".env", "CONTEXT_DATABASE_URL", target_value.render_as_string(hide_password=False))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        target.close()


if __name__ == "__main__":
    main()
