"""Global definitions with owner editing, public use and explicit user grants."""
from typing import Literal
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from agent.contracts import AgentSettings
from agent.executors import EXECUTORS
from agent.store import AgentStore, cipher, encoded
from app.web.api.models import User
from app.web.core.db import engine
from app.web.api.utils.models import StatusTypes

ACCESS = """(d.owner_id=:actor OR d.visibility='public' OR
    (d.visibility='shared' AND EXISTS (SELECT 1 FROM app.agent_shares s WHERE s.agent_id=d.id AND s.user_id=:actor)))"""


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    expected_revision: int = Field(default=0, ge=0)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        return AgentSettings(base_url=value).base_url


class DefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    icon: str = Field(default="🤖", min_length=1, max_length=16)
    harness: str = "assistant"
    connection_id: UUID
    instructions: str = Field(default="", max_length=12000)
    model: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    timeout_seconds: int = Field(default=120, ge=10, le=300)
    max_output_tokens: int = Field(default=8192, ge=1024, le=16384)
    enabled: bool = False
    visibility: Literal["private", "shared", "public"] = "private"
    shared_with: list[EmailStr] = Field(default_factory=list, max_length=100)
    expected_revision: int = Field(default=0, ge=0)

    @field_validator("harness")
    @classmethod
    def supported_executor(cls, value):
        if value not in EXECUTORS:
            raise ValueError("Unsupported executor")
        return value

    @model_validator(mode="after")
    def sharing(self):
        if (self.visibility == "shared") != bool(self.shared_with):
            raise ValueError("Choose recipients only for shared agents; shared agents require at least one recipient")
        return self

    def settings(self, base_url: str) -> AgentSettings:
        return AgentSettings.model_validate({**self.model_dump(include=set(AgentSettings.model_fields)), "base_url": base_url})


class AgentRegistry:
    def __init__(self, database=engine):
        self.database = database

    async def list(self, actor: UUID):
        async with self.database.connect() as conn:
            connections = (await conn.execute(text("SELECT id,name,base_url,revision,updated_at FROM app.agent_connections WHERE owner_id=:actor ORDER BY name"), {"actor": actor})).mappings().all()
            definitions = (await conn.execute(text(f"""SELECT d.*, c.revision AS connection_revision,
                ARRAY(SELECT u.email FROM app.agent_shares s JOIN app."user" u ON u.id=s.user_id
                    WHERE s.agent_id=d.id AND d.owner_id=:actor ORDER BY u.email) AS shared_with
                FROM app.agent_definitions d LEFT JOIN app.agent_connections c ON c.id=d.connection_id AND c.owner_id=d.owner_id
                WHERE {ACCESS} ORDER BY d.system_key NULLS LAST,d.updated_at DESC"""), {"actor": actor})).mappings().all()
            defaults = (await conn.execute(text("SELECT config,revision,tested_revision FROM app.agent_settings WHERE singleton"))).mappings().one()
        agents = []
        for row in definitions:
            config = {**row["config"], **defaults["config"]} if row["system_key"] else row["config"]
            verified = (defaults["tested_revision"] == defaults["revision"] if row["system_key"] else
                        row["tested_revision"] == row["revision"] and row["tested_connection_revision"] == row["connection_revision"])
            agents.append({**config, "id": row["id"], "connection_id": row["connection_id"], "revision": row["revision"],
                           "verified": verified, "visibility": row["visibility"], "shared_with": row["shared_with"],
                           "system_key": row["system_key"], "can_edit": row["owner_id"] == actor})
            agents[-1].update(created_at=row["created_at"], updated_at=row["updated_at"])
        return {"connections": [dict(row) for row in connections], "agents": agents,
                "harnesses": {key: {"name": item["name"], "tools": item["steps"]} for key, item in EXECUTORS.items()}}

    async def save_connection(self, owner: UUID, payload: ConnectionUpdate, ident: UUID | None = None):
        base_url = AgentSettings(base_url=payload.base_url).base_url
        try:
            async with self.database.begin() as conn:
                row = None
                if ident:
                    row = (await conn.execute(text("SELECT * FROM app.agent_connections WHERE id=:id AND owner_id=:owner FOR UPDATE"), {"id": ident, "owner": owner})).mappings().first()
                    if row is None:
                        raise HTTPException(404, "Connection not found")
                if payload.expected_revision != (row["revision"] if row else 0):
                    raise HTTPException(409, "Connection changed. Reload before saving.")
                secret = row["api_key_ciphertext"] if row else None
                if payload.api_key and payload.api_key.get_secret_value():
                    secret = cipher().encrypt(payload.api_key.get_secret_value().encode()).decode()
                if not secret:
                    raise HTTPException(422, "An API key is required")
                ident = ident or uuid4()
                if row:
                    await conn.execute(text("UPDATE app.agent_connections SET name=:name,base_url=:url,api_key_ciphertext=:secret,revision=revision+1,updated_at=now() WHERE id=:id AND owner_id=:owner"), {"id": ident, "owner": owner, "name": payload.name, "url": base_url, "secret": secret})
                else:
                    await conn.execute(text("INSERT INTO app.agent_connections(id,owner_id,name,base_url,api_key_ciphertext) VALUES(:id,:owner,:name,:url,:secret)"), {"id": ident, "owner": owner, "name": payload.name, "url": base_url, "secret": secret})
        except IntegrityError as exc:
            raise HTTPException(409, "A connection with this name already exists") from exc
        return {"id": ident}

    async def save_definition(self, owner: UUID, payload: DefinitionUpdate, ident: UUID | None = None):
        async with self.database.begin() as conn:
            connection = (await conn.execute(text("SELECT * FROM app.agent_connections WHERE id=:id AND owner_id=:owner FOR SHARE"), {"id": payload.connection_id, "owner": owner})).mappings().first()
            if not connection:
                raise HTTPException(404, "Connection not found")
            payload.settings(connection["base_url"])
            row = None
            if ident:
                row = (await conn.execute(text("SELECT * FROM app.agent_definitions WHERE id=:id AND owner_id=:owner FOR UPDATE"), {"id": ident, "owner": owner})).mappings().first()
                if not row:
                    raise HTTPException(404, "Agent not found or not owned by you")
            if payload.expected_revision != (row["revision"] if row else 0):
                raise HTTPException(409, "Agent changed. Reload before saving.")
            recipients = sorted({str(email).lower() for email in payload.shared_with})
            users = (await conn.execute(select(User.id, func.lower(User.email).label('email')).where(
                func.lower(User.email).in_(recipients), User.status == StatusTypes.ENABLE))).mappings().all()
            if {user["email"] for user in users} != set(recipients):
                raise HTTPException(422, "All recipients must be active registered users")
            config = payload.model_dump(exclude={"connection_id", "expected_revision", "visibility", "shared_with"})
            ident = ident or uuid4()
            if row:
                old = {k: v for k, v in row["config"].items() if k != "enabled"}
                same = old == {k: v for k, v in config.items() if k != "enabled"} and row["connection_id"] == payload.connection_id
                tested = row["revision"] + 1 if same and row["tested_revision"] == row["revision"] else None
                await conn.execute(text("""UPDATE app.agent_definitions SET config=CAST(:config AS jsonb),connection_id=:connection,
                    visibility=:visibility,revision=revision+1,tested_revision=:tested,updated_at=now() WHERE id=:id AND owner_id=:owner"""),
                    {"id": ident, "owner": owner, "config": encoded(config), "connection": payload.connection_id, "tested": tested, "visibility": payload.visibility})
            else:
                await conn.execute(text("INSERT INTO app.agent_definitions(id,owner_id,connection_id,config,visibility) VALUES(:id,:owner,:connection,CAST(:config AS jsonb),:visibility)"), {"id": ident, "owner": owner, "connection": payload.connection_id, "config": encoded(config), "visibility": payload.visibility})
            await conn.execute(text("DELETE FROM app.agent_shares WHERE agent_id=:id"), {"id": ident})
            for user in users:
                await conn.execute(text("INSERT INTO app.agent_shares(agent_id,user_id) VALUES(:id,:user)"), {"id": ident, "user": user["id"]})
        return {"id": ident}

    async def resolve(self, actor: UUID, ident: UUID, *, require_enabled=True, require_owner=False):
        async with self.database.connect() as conn:
            row = (await conn.execute(text(f"""SELECT d.*,c.base_url,c.api_key_ciphertext,c.revision AS connection_revision
                FROM app.agent_definitions d LEFT JOIN app.agent_connections c ON c.id=d.connection_id AND c.owner_id=d.owner_id
                WHERE d.id=:id AND {ACCESS}"""), {"id": ident, "actor": actor})).mappings().first()
        if not row:
            raise HTTPException(404, "Agent not found or not shared with you")
        if require_owner and row["owner_id"] != actor:
            raise HTTPException(403, "Only the owner can test this agent configuration")
        if row["system_key"]:
            defaults = AgentStore(self.database)
            view = await defaults.settings()
            config, key = await defaults.runtime_settings(view.revision, require_enabled=require_enabled)
            connection_revision = view.revision
        else:
            verified = row["tested_revision"] == row["revision"] and row["tested_connection_revision"] == row["connection_revision"]
            if require_enabled and (not row["config"]["enabled"] or not verified):
                raise HTTPException(409, "Save, test and enable the agent before running")
            config = DefinitionUpdate(**row["config"], connection_id=row["connection_id"]).settings(row["base_url"])
            try:
                key = cipher().decrypt(row["api_key_ciphertext"].encode()).decode()
            except InvalidToken as exc:
                raise HTTPException(503, "Saved connection cannot be decrypted") from exc
            connection_revision = row["connection_revision"]
        snapshot = {**config.model_dump(), "agent_id": str(ident), "agent_revision": row["revision"],
                    "connection_revision": connection_revision, "harness": row["config"]["harness"],
                    "instructions": row["config"]["instructions"]}
        return config, key, snapshot

    async def mark_verified(self, owner: UUID, snapshot: dict):
        async with self.database.begin() as conn:
            result = await conn.execute(text("""UPDATE app.agent_definitions d SET tested_revision=d.revision,tested_connection_revision=:connection_revision
                FROM app.agent_connections c WHERE d.id=:id AND d.owner_id=:owner AND d.revision=:revision
                AND c.id=d.connection_id AND c.owner_id=d.owner_id AND c.revision=:connection_revision"""),
                {"id": UUID(snapshot["agent_id"]), "owner": owner, "revision": snapshot["agent_revision"], "connection_revision": snapshot["connection_revision"]})
            if result.rowcount != 1:
                raise HTTPException(409, "Agent is read-only or configuration changed during connection test")

    async def delete(self, owner: UUID, kind: str, ident: UUID, revision: int):
        table = {"agents": "agent_definitions", "connections": "agent_connections"}[kind]
        try:
            async with self.database.begin() as conn:
                result = await conn.execute(text(f"DELETE FROM app.{table} WHERE id=:id AND owner_id=:owner AND revision=:revision"), {"id": ident, "owner": owner, "revision": revision})
                if result.rowcount != 1:
                    raise HTTPException(409, "Item not owned by you, missing or changed. Reload before deleting.")
        except IntegrityError as exc:
            raise HTTPException(409, "Remove agent bindings before deleting this connection") from exc
