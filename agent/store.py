"""PostgreSQL is the queue, checkpoint and event authority; no process-local job state."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import text

from agent.contracts import (
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    MAX_CHECKPOINT_BYTES,
    AgentSettings,
    CollectionRequest,
    LeaseLost,
    RunConflict,
    SettingsUpdate,
    SettingsView,
)
from app.storage.connection_credentials import connection_cipher
from app.web.core.config import settings
from app.web.core.db import engine


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cipher():
    if "SECRET_KEY" not in settings.model_fields_set or not settings.SECRET_KEY or settings.SECRET_KEY.lower() == "changethis":
        raise HTTPException(503, "Configure a persistent system SECRET_KEY before saving credentials")
    return connection_cipher(settings.SECRET_KEY)


class CollectionStore:
    def __init__(self, database=engine):
        self.database = database

    async def settings(self) -> SettingsView:
        async with self.database.connect() as conn:
            row = (await conn.execute(text("SELECT * FROM app.collection_agent_settings WHERE singleton"))).mappings().one()
        return SettingsView(**row["config"], api_key_configured=bool(row["api_key_ciphertext"]), revision=row["revision"], connection_verified=row["tested_revision"] == row["revision"])

    async def save_settings(self, payload: SettingsUpdate, owner: UUID) -> SettingsView:
        if payload.clear_api_key and payload.api_key and payload.api_key.get_secret_value():
            raise RunConflict("Choose either replace or clear API key")
        async with self.database.begin() as conn:
            row = (await conn.execute(text("SELECT * FROM app.collection_agent_settings WHERE singleton FOR UPDATE"))).mappings().one()
            if row["revision"] != payload.expected_revision:
                raise RunConflict("Settings changed; reload before saving")
            ciphertext = row["api_key_ciphertext"]
            if payload.clear_api_key:
                ciphertext = None
            elif payload.api_key and payload.api_key.get_secret_value():
                ciphertext = cipher().encrypt(payload.api_key.get_secret_value().encode()).decode()
            if payload.enabled and not ciphertext:
                raise RunConflict("An API key is required before enabling collection")
            config = AgentSettings.model_validate(payload.model_dump(include=set(AgentSettings.model_fields)))
            old_config = AgentSettings.model_validate(row["config"])
            verified = (row["tested_revision"] == row["revision"] and ciphertext == row["api_key_ciphertext"]
                        and config.model_dump(exclude={"enabled"}) == old_config.model_dump(exclude={"enabled"}))
            if config.enabled and not verified:
                raise RunConflict("Save the model configuration disabled, test its connection, then enable it")
            await conn.execute(text("""UPDATE app.collection_agent_settings
                SET config=CAST(:config AS jsonb), api_key_ciphertext=:key, revision=revision+1,
                tested_revision=:tested, updated_by=:owner, updated_at=now() WHERE singleton"""),
                {"config": config.model_dump_json(), "key": ciphertext, "owner": owner, "tested": row["revision"] + 1 if verified else None})
        return SettingsView(**config.model_dump(), api_key_configured=bool(ciphertext), revision=row["revision"] + 1, connection_verified=verified)

    async def runtime_settings(self, revision: int, *, require_enabled: bool = True) -> tuple[AgentSettings, str]:
        async with self.database.connect() as conn:
            row = (await conn.execute(text("SELECT * FROM app.collection_agent_settings WHERE singleton"))).mappings().one()
        config = AgentSettings.model_validate(row["config"])
        if row["revision"] != revision:
            raise HTTPException(409, "SETTINGS_CHANGED")
        if (require_enabled and (not config.enabled or row["tested_revision"] != revision)) or not row["api_key_ciphertext"]:
            raise HTTPException(503, "AGENT_NOT_CONFIGURED")
        try:
            key = cipher().decrypt(row["api_key_ciphertext"].encode()).decode()
        except InvalidToken as exc:
            raise HTTPException(503, "AGENT_CREDENTIALS_UNREADABLE") from exc
        return config, key

    async def mark_verified(self, revision: int) -> None:
        async with self.database.begin() as conn:
            result = await conn.execute(text("UPDATE app.collection_agent_settings SET tested_revision=revision,tested_at=now() WHERE singleton AND revision=:revision"), {"revision": revision})
            if result.rowcount != 1:
                raise RunConflict("Settings changed while the connection test was running")

    @staticmethod
    async def event(conn, run_id: UUID, event: dict) -> None:
        seq = (await conn.execute(text("UPDATE app.collection_agent_runs SET event_seq=event_seq+1, updated_at=now() WHERE id=:id RETURNING event_seq"), {"id": run_id})).scalar_one()
        await conn.execute(text("INSERT INTO app.collection_agent_events(run_id,seq,event) VALUES (:id,:seq,CAST(:event AS jsonb))"), {"id": run_id, "seq": seq, "event": encoded(event)})

    async def enqueue(self, owner: UUID, key: str, payload: CollectionRequest) -> dict:
        request = payload.model_dump()
        digest = hashlib.sha256(encoded(request).encode()).hexdigest()
        async with self.database.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"collection-owner:{owner}"})
            existing = (await conn.execute(text("SELECT * FROM app.collection_agent_runs WHERE owner_id=:owner AND idempotency_key=:key"), {"owner": owner, "key": key})).mappings().first()
            if existing:
                if existing["request_hash"] != digest:
                    raise RunConflict("Idempotency key was already used for a different collection request")
                return self.view(existing)
            config = (await conn.execute(text("SELECT * FROM app.collection_agent_settings WHERE singleton FOR SHARE"))).mappings().one()
            if not config["config"].get("enabled") or not config["api_key_ciphertext"] or config["tested_revision"] != config["revision"]:
                raise HTTPException(503, "Configure and enable the collection agent in Settings")
            count = (await conn.execute(text("SELECT count(*) FROM app.collection_agent_runs WHERE owner_id=:owner AND status IN ('queued','running')"), {"owner": owner})).scalar_one()
            if count >= 3:
                raise HTTPException(429, "Collection queue limit reached for this user")
            run_id = uuid4()
            await conn.execute(text("""INSERT INTO app.collection_agent_runs
                (id,owner_id,idempotency_key,request_hash,request,model_config)
                VALUES (:id,:owner,:key,:hash,CAST(:request AS jsonb),CAST(:config AS jsonb))"""),
                {"id": run_id, "owner": owner, "key": key, "hash": digest, "request": encoded(request),
                 "config": encoded({**AgentSettings.model_validate(config["config"]).model_dump(), "revision": config["revision"]})})
            await self.event(conn, run_id, {"type": "queued", "activity": "None"})
        return await self.get(owner, run_id)

    @staticmethod
    def view(row) -> dict:
        return {key: row[key] for key in ("id", "request", "model_config", "status", "stage", "result", "error_code", "attempts", "event_seq", "created_at", "updated_at")}

    async def get(self, owner: UUID, run_id: UUID) -> dict:
        async with self.database.connect() as conn:
            row = (await conn.execute(text("SELECT * FROM app.collection_agent_runs WHERE id=:id AND owner_id=:owner"), {"id": run_id, "owner": owner})).mappings().first()
        if row is None:
            raise HTTPException(404, "Collection run not found")
        return self.view(row)

    async def events(self, owner: UUID, run_id: UUID, after: int, limit: int) -> list[dict]:
        await self.get(owner, run_id)
        async with self.database.connect() as conn:
            rows = (await conn.execute(text("SELECT seq,event,created_at FROM app.collection_agent_events WHERE run_id=:id AND seq>:after ORDER BY seq LIMIT :limit"), {"id": run_id, "after": after, "limit": limit})).mappings().all()
        return [dict(row) for row in rows]

    async def control(self, owner: UUID, run_id: UUID, action: str) -> dict:
        async with self.database.begin() as conn:
            row = (await conn.execute(text("SELECT * FROM app.collection_agent_runs WHERE id=:id AND owner_id=:owner FOR UPDATE"), {"id": run_id, "owner": owner})).mappings().first()
            if row is None:
                raise HTTPException(404, "Collection run not found")
            if action == "cancel":
                if row["status"] in {"completed", "cancelled", "failed"}:
                    return self.view(row)
                status, activity = "cancelled", "Paused"
            else:
                if row["status"] != "failed" or row["attempts"] >= MAX_ATTEMPTS:
                    raise RunConflict("Only failed runs with remaining attempts can be resumed")
                status, activity = "queued", "None"
            await conn.execute(text("UPDATE app.collection_agent_runs SET status=:status,lease_token=NULL,lease_until=NULL,error_code=NULL WHERE id=:id"), {"status": status, "id": run_id})
            await self.event(conn, run_id, {"type": status, "activity": activity})
        return await self.get(owner, run_id)

    async def claim(self) -> dict | None:
        async with self.database.begin() as conn:
            if not (await conn.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('collection-agent-claim'))"))).scalar_one():
                return None
            active = (await conn.execute(text("SELECT count(*) FROM app.collection_agent_runs WHERE status='running' AND lease_until>now()"))).scalar_one()
            if active >= 2:
                return None
            row = (await conn.execute(text("""SELECT * FROM app.collection_agent_runs
                WHERE status='queued' OR (status='running' AND lease_until<=now())
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"""))).mappings().first()
            if row is None:
                return None
            run_id = row["id"]
            if row["attempts"] >= MAX_ATTEMPTS:
                await conn.execute(text("UPDATE app.collection_agent_runs SET status='failed',error_code='ATTEMPTS_EXHAUSTED',lease_token=NULL,lease_until=NULL WHERE id=:id"), {"id": run_id})
                await self.event(conn, run_id, {"type": "failed", "activity": "Error", "code": "ATTEMPTS_EXHAUSTED"})
                return None
            token = uuid4()
            updated = (await conn.execute(text("""UPDATE app.collection_agent_runs SET status='running',attempts=attempts+1,
                lease_token=:token,lease_until=now()+(:lease * interval '1 second'),error_code=NULL
                WHERE id=:id RETURNING *"""), {"token": token, "lease": LEASE_SECONDS, "id": run_id})).mappings().one()
            await self.event(conn, run_id, {"type": "started", "stage": updated["stage"], "attempt": updated["attempts"], "activity": "Working"})
            return dict(updated)

    async def heartbeat(self, run_id: UUID, token: UUID) -> bool:
        async with self.database.begin() as conn:
            result = await conn.execute(text("""UPDATE app.collection_agent_runs SET lease_until=now()+(:lease * interval '1 second')
                WHERE id=:id AND lease_token=:token AND status='running' AND lease_until>now()"""), {"id": run_id, "token": token, "lease": LEASE_SECONDS})
        return result.rowcount == 1

    async def checkpoint(self, run: dict, name: str, value: dict) -> None:
        checkpoints = {**run["checkpoints"], name: value}
        if len(encoded(checkpoints).encode()) > MAX_CHECKPOINT_BYTES:
            raise ValueError("CHECKPOINT_LIMIT_EXCEEDED")
        async with self.database.begin() as conn:
            result = await conn.execute(text("""UPDATE app.collection_agent_runs SET checkpoints=CAST(:data AS jsonb),stage=stage+1
                WHERE id=:id AND lease_token=:token AND status='running' AND lease_until>now() AND stage=:stage"""),
                {"data": encoded(checkpoints), "id": run["id"], "token": run["lease_token"], "stage": run["stage"]})
            if result.rowcount != 1:
                raise LeaseLost()
            await self.event(conn, run["id"], {"type": "stage_completed", "stage": name, "activity": "Working"})
        run["checkpoints"] = checkpoints
        run["stage"] += 1

    async def progress(self, run: dict, stage: str, activity: str) -> None:
        async with self.database.begin() as conn:
            row = (await conn.execute(text("SELECT id FROM app.collection_agent_runs WHERE id=:id AND lease_token=:token AND status='running' AND lease_until>now() FOR UPDATE"), {"id": run["id"], "token": run["lease_token"]})).first()
            if row is None:
                raise LeaseLost()
            await self.event(conn, run["id"], {"type": "stage_started", "stage": stage, "activity": activity})

    async def finish(self, run: dict, status: str, *, result: dict | None = None, error: str | None = None) -> bool:
        async with self.database.begin() as conn:
            updated = await conn.execute(text("""UPDATE app.collection_agent_runs
                SET status=:status,result=CAST(:result AS jsonb),error_code=:error,lease_token=NULL,lease_until=NULL
                WHERE id=:id AND lease_token=:token AND status='running' AND lease_until>now()"""),
                {"status": status, "result": encoded(result) if result is not None else None, "error": error, "id": run["id"], "token": run["lease_token"]})
            if updated.rowcount != 1:
                return False
            await self.event(conn, run["id"], {"type": status, "code": error, "activity": "Ready" if status == "completed" else "Error" if status == "failed" else "Paused"})
        return True
