"""Owner-scoped chats and permission-checked, immutable share snapshots."""

import json
import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import text
from agent.contracts import AgentRunRequest

from app.web.api.dependencies.common import CurrentUser, get_current_user
from app.web.api.dependencies.session import SessionDep
from app.web.core.config import settings

@asynccontextmanager
async def lifespan(_app):
    from agent.chat_schedules import serve_schedules
    from agent.store import AgentStore

    schedules = asyncio.create_task(serve_schedules(AgentStore()))
    try:
        yield
    finally:
        schedules.cancel()
        with suppress(asyncio.CancelledError):
            await schedules


router = APIRouter(prefix="/scout", tags=["scout-chats"], lifespan=lifespan)
optional_token = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/login/token", auto_error=False)


class ChatAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(max_length=128)
    content: str = Field(max_length=14 * 1024 * 1024, pattern=r"^[A-Za-z0-9+/]*={0,2}$")


class ReviewRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    createdAt: str = Field(max_length=40)
    message: str = Field(max_length=100000)
    turnId: int = Field(ge=1)
    template: str = Field(max_length=500000)
    adapter: str = Field(max_length=500000)


class ReviewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    revisionId: str = Field(min_length=1, max_length=100)
    path: Literal["template", "adapter"]
    side: Literal["additions", "deletions"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    body: str = Field(min_length=1, max_length=10000)
    resolved: bool = False

    @model_validator(mode="after")
    def check_range(self):
        if self.end < self.start:
            raise ValueError("Comment end must not precede its start")
        return self


class ReviewStaged(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str | None = Field(default=None, max_length=500000)
    adapter: str | None = Field(default=None, max_length=500000)


class ChatReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revisions: list[ReviewRevision] = Field(default_factory=list, max_length=200)
    staged: ReviewStaged = Field(default_factory=ReviewStaged)
    comments: list[ReviewComment] = Field(default_factory=list, max_length=500)


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    prompt: str = Field(max_length=100000)
    reply: str = Field(default="", max_length=500000)
    template: str = Field(default="", max_length=500000)
    adapter: str = Field(default="", max_length=500000)
    step: int = Field(ge=0, le=5)
    done: int = Field(ge=0, le=6)
    complete: bool
    waiting: Literal["template", "adapter"] | None = None
    vote: Literal["up", "down"] | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=20)
    createdAt: str | None = Field(default=None, max_length=40)
    review: ChatReview | None = None

    @model_validator(mode="after")
    def check_revision_owner(self):
        if self.review and any(revision.turnId != self.id for revision in self.review.revisions):
            raise ValueError("A correction must belong to its response")
        return self


class SaveChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    turns: list[ChatTurn] = Field(max_length=200)
    pinned: bool = False
    archived: bool = False
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def check_size(self):
        if len(self.model_dump_json().encode()) > 10 * 1024 * 1024:
            raise ValueError("This chat exceeds the 10 MiB sharing limit")
        return self


class ShareChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: Literal["anyone", "invited"] = "anyone"
    emails: list[EmailStr] = Field(default_factory=list, max_length=100)
    snapshot: SaveChat

    @model_validator(mode="after")
    def check_recipients(self):
        if self.audience == "invited" and not self.emails:
            raise ValueError("Add at least one invited person")
        return self


async def owned(session, chat_id, user_id):
    row = (await session.execute(text("SELECT * FROM app.scout_chats WHERE id=:id AND owner_id=:owner"),
                                 {"id": chat_id, "owner": user_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Chat not found")
    return dict(row)


async def cancel_schedule(session, chat_id, owner_id):
    from agent.store import AgentStore

    run_id = (await session.execute(text("SELECT last_run_id FROM app.scout_chat_schedules WHERE chat_id=:id FOR UPDATE"),
                                    {"id": chat_id})).scalar_one_or_none()
    if run_id:
        cancelled = (await session.execute(text("""UPDATE app.agent_runs SET status='cancelled',
            lease_token=NULL,lease_until=NULL,error_code=NULL WHERE id=:id AND owner_id=:owner
            AND status IN ('queued','running') RETURNING id"""), {"id": run_id, "owner": owner_id})).scalar_one_or_none()
        if cancelled:
            await AgentStore.event(session, run_id, {"type": "cancelled", "activity": "Paused"})
    await session.execute(text("DELETE FROM app.scout_chat_schedules WHERE chat_id=:id"), {"id": chat_id})


@router.get("/chats")
async def list_chats(user: CurrentUser, session: SessionDep, response: Response,
                     archived: bool = False, offset: int = Query(0, ge=0)):
    response.headers["Cache-Control"] = "no-store"
    rows = await session.execute(text("""SELECT id,title,pinned,archived,revision,updated_at
        FROM app.scout_chats WHERE owner_id=:owner AND archived=:archived
        ORDER BY pinned DESC,updated_at DESC,id LIMIT 100 OFFSET :offset"""),
        {"owner": user.id, "archived": archived, "offset": offset})
    return [dict(row) for row in rows.mappings()]


@router.get("/chats/{chat_id}")
async def read_chat(chat_id: UUID, user: CurrentUser, session: SessionDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await owned(session, chat_id, user.id)


@router.put("/chats/{chat_id}")
async def save_chat(chat_id: UUID, payload: SaveChat, user: CurrentUser, session: SessionDep):
    params = {**payload.model_dump(exclude={"turns"}), "id": chat_id, "owner": user.id,
              "turns": json.dumps([turn.model_dump(exclude_none=True) for turn in payload.turns])}
    row = (await session.execute(text("""INSERT INTO app.scout_chats
        (id,owner_id,title,turns,pinned,archived)
        SELECT :id,:owner,:title,CAST(:turns AS jsonb),:pinned,:archived WHERE :revision=0
        ON CONFLICT (id) DO NOTHING RETURNING *"""), params)).mappings().first()
    if row is None:
        row = (await session.execute(text("""UPDATE app.scout_chats SET title=:title,
            turns=CAST(:turns AS jsonb),pinned=:pinned,archived=:archived,
            revision=revision+1,updated_at=now()
            WHERE id=:id AND owner_id=:owner AND revision=:revision RETURNING *"""), params)).mappings().first()
    if row is None:
        raise HTTPException(409, "Chat changed in another window. Reopen it before saving again.")
    if payload.archived:
        await cancel_schedule(session, chat_id, user.id)
    await session.commit()
    return dict(row)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: UUID, user: CurrentUser, session: SessionDep):
    await owned(session, chat_id, user.id)
    await cancel_schedule(session, chat_id, user.id)
    await session.execute(text("DELETE FROM app.scout_chats WHERE id=:id AND owner_id=:owner"),
                          {"id": chat_id, "owner": user.id})
    await session.commit()
    return {"deleted": True}


@router.post("/chats/{chat_id}/branch", status_code=201)
async def branch_chat(chat_id: UUID, user: CurrentUser, session: SessionDep):
    source = await owned(session, chat_id, user.id)
    ident = uuid4()
    await session.execute(text("""INSERT INTO app.scout_chats (id,owner_id,title,turns)
        VALUES (:id,:owner,:title,CAST(:turns AS jsonb))"""),
        {"id": ident, "owner": user.id, "title": source["title"], "turns": json.dumps(source["turns"])})
    await session.commit()
    return await owned(session, ident, user.id)


@router.post("/chats/{chat_id}/shares", status_code=201)
async def share_chat(chat_id: UUID, payload: ShareChat, user: CurrentUser, session: SessionDep):
    await owned(session, chat_id, user.id)
    ident = uuid4()
    await session.execute(text("""INSERT INTO app.scout_chat_shares (id,chat_id,title,turns,audience,emails)
        VALUES (:id,:chat,:title,CAST(:turns AS jsonb),:audience,CAST(:emails AS jsonb))"""),
        {"id": ident, "chat": chat_id, "title": payload.snapshot.title,
         "turns": json.dumps([turn.model_dump(exclude={"review"}, exclude_none=True) for turn in payload.snapshot.turns]),
         "audience": payload.audience, "emails": json.dumps(sorted({str(e).lower() for e in payload.emails}))})
    await session.commit()
    return {"id": ident}


@router.get("/shares/{share_id}")
async def read_share(share_id: UUID, session: SessionDep, response: Response,
                     token: Annotated[str | None, Depends(optional_token)] = None):
    response.headers["Cache-Control"] = "no-store"
    row = (await session.execute(text("""SELECT s.*,c.owner_id FROM app.scout_chat_shares s
        JOIN app.scout_chats c ON c.id=s.chat_id WHERE s.id=:id"""), {"id": share_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Shared chat not found")
    if row["audience"] == "invited":
        if token is None:
            raise HTTPException(401, "Sign in to view this shared chat")
        user = await get_current_user(session, token)
        if user.id != row["owner_id"] and str(user.email).lower() not in row["emails"]:
            raise HTTPException(403, "You are not invited to this shared chat")
    return {"title": row["title"], "turns": row["turns"], "created_at": row["created_at"]}


class ChatSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_seconds: int = Field(ge=3600, le=31536000)
    request: AgentRunRequest


@router.get("/chats/{chat_id}/schedule")
async def read_schedule(chat_id: UUID, user: CurrentUser, session: SessionDep):
    await owned(session, chat_id, user.id)
    row = (await session.execute(text("""SELECT s.*,r.status,r.result,r.error_code
        FROM app.scout_chat_schedules s LEFT JOIN app.agent_runs r ON r.id=s.last_run_id
        WHERE s.chat_id=:id"""), {"id": chat_id})).mappings().first()
    return dict(row) if row else None


@router.put("/chats/{chat_id}/schedule")
async def save_schedule(chat_id: UUID, payload: ChatSchedule, user: CurrentUser, session: SessionDep):
    from agent.executors import EXECUTORS
    from agent.registry import AgentRegistry
    from pydantic import ValidationError

    chat = await owned(session, chat_id, user.id)
    if chat["archived"]:
        raise HTTPException(409, "Unarchive this chat before scheduling it")
    _, _, agent = await AgentRegistry().resolve(user.id, payload.request.agent_id)
    try:
        EXECUTORS[agent["harness"]]["request"].model_validate(payload.request.input)
    except ValidationError as error:
        raise HTTPException(422, "Input does not match the selected agent") from error
    await session.execute(text("""INSERT INTO app.scout_chat_schedules(chat_id,request,interval_seconds,next_run_at)
        VALUES (:id,CAST(:request AS jsonb),:seconds,now()+make_interval(secs => CAST(:seconds AS double precision)))
        ON CONFLICT (chat_id) DO UPDATE SET request=excluded.request,
        interval_seconds=excluded.interval_seconds,next_run_at=excluded.next_run_at,enabled=true,last_error=NULL"""),
        {"id": chat_id, "request": payload.request.model_dump_json(), "seconds": payload.interval_seconds})
    await session.commit()
    return await read_schedule(chat_id, user, session)


@router.delete("/chats/{chat_id}/schedule")
async def remove_schedule(chat_id: UUID, user: CurrentUser, session: SessionDep):
    await owned(session, chat_id, user.id)
    await cancel_schedule(session, chat_id, user.id)
    await session.commit()
    return {"deleted": True}
