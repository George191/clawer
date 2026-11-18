"""Exercise the 80→81 upgrade and team key pages through the production FastAPI router."""
from importlib import import_module
import os
import unittest
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.api.dependencies.session import get_db
from app.web.api.models import InviteCodes, RoleTypes, Team, TeamUserJoin, Tenant, User
from app.web.core.security import get_password_hash


URL = os.getenv("TEAM_KEY_MIGRATION_TEST_DATABASE_URL")
migration = import_module("app.web.alembic.versions.81a1b2c3d4e5_team_api_key_status")


@unittest.skipUnless(URL, "Requires an isolated team_key_migration_regression database migrated to 80")
class TeamKeyMigration(unittest.IsolatedAsyncioTestCase):
    async def test_migrated_key_pages_and_roles(self):
        from app.web.main import app

        target = make_url(URL)
        self.assertIn(target.host, {"localhost", "127.0.0.1"})
        self.assertEqual(target.database, "team_key_migration_regression")
        engine = create_async_engine(target)
        self.addAsyncCleanup(engine.dispose)

        def apply_revision(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        async with engine.connect() as db:
            transaction = await db.begin()
            try:
                self.assertEqual(await db.scalar(text("SELECT version_num FROM public.alembic_version")), "80a1b2c3d4e5")
                self.assertEqual(await db.scalar(text("""SELECT count(*) FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='apikey' AND column_name='status'""")), 0)
                sessions = async_sessionmaker(db, expire_on_commit=False, join_transaction_mode="create_savepoint")
                async def test_session():
                    async with sessions() as session:
                        yield session
                app.dependency_overrides[get_db] = test_session
                self.addCleanup(app.dependency_overrides.pop, get_db)
                password = uuid4().hex
                hashed = get_password_hash(password)
                users = [User(email=f"{name}@qa.example", phone=str(9000+i), hashed_password=hashed,
                              is_superuser=(name=="admin")) for i, name in enumerate(("admin","owner","member","outsider"))]
                admin, owner, member, outsider = users
                async with sessions() as session:
                    tenant = Tenant(name="Migration test")
                    invite = InviteCodes(code=uuid4().hex)
                    session.add_all([tenant, invite, *users])
                    await session.flush()
                    team = Team(name="Test team", tenant_id=tenant.id)
                    other = Team(name="Other team", tenant_id=tenant.id)
                    session.add_all([team, other])
                    await session.flush()
                    for person, role in ((owner, RoleTypes.OWNER),(member, RoleTypes.MEMBER)):
                        session.add(TeamUserJoin(team_id=team.id, user_id=person.id, invite_code=invite.id,
                                                 invite_by=admin.id, role=role))
                    await session.commit()
                identifiers = sorted(uuid4() for _ in range(5))
                for i, key_id in enumerate(identifiers):
                    await db.execute(text("""INSERT INTO public.apikey(id,owner_id,team_id,hashed_key,short_key,created_at)
                        VALUES (:id,:owner,:team,'test-hash','test-short','2025-01-01')"""),
                        {"id":key_id,"owner":(member if i==1 else owner).id,"team":(other if i==3 else team).id})
                # Legacy revoke removes the entire row. The migration must leave that identity absent.
                await db.execute(text("DELETE FROM public.apikey WHERE id=:id"), {"id":identifiers[4]})
                await db.run_sync(apply_revision)
                self.assertEqual(await db.scalar(text("SELECT count(*) FROM public.apikey WHERE status='ENABLE'")),4)
                self.assertFalse(await db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.apikey WHERE id=:id)"),{"id":identifiers[4]}))
                # Environments initially created by SQLModel may already carry an explicit disabled state.
                await db.execute(text("UPDATE public.apikey SET status='DISABLE' WHERE id=:id"),{"id":identifiers[0]})
                await db.run_sync(apply_revision)
                self.assertEqual(await db.scalar(text("SELECT status::text FROM public.apikey WHERE id=:id"),{"id":identifiers[0]}),"DISABLE")
                async with app.router.lifespan_context(app), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    async def login(user):
                        response = await client.post("/api/v1/login/token",data={"username":user.email,"password":password})
                        self.assertEqual(response.status_code,200)
                        return {"Authorization":"Bearer "+response.json()["access_token"]}

                    async def page(actor, number=1, group=team):
                        return await client.get(f"/api/v1/apikey/{group.id}",headers=actor,params={"page":number,"size":2})

                    for manager in (admin, owner):
                        actor = await login(manager)
                        first, second = await page(actor), await page(actor,2)
                        self.assertEqual((first.status_code,second.status_code),(200,200))
                        one, two = first.json(),second.json()
                        self.assertEqual((one["total"],one["pages"],two["page"]),(3,2,2))
                        visible=one["items"]+two["items"]
                        self.assertEqual([r["id"] for r in visible],list(map(str,identifiers[:3])))
                        self.assertTrue(all(not {"key","hashed_key"}.intersection(r) for r in visible))
                        self.assertEqual(visible[0]["status"],1)
                    actor = await login(member)
                    own = await page(actor)
                    self.assertEqual(own.status_code,200)
                    self.assertEqual([r["id"] for r in own.json()["items"]],[str(identifiers[1])])
                    self.assertEqual((await page(actor,group=other)).status_code,403)
                    self.assertEqual((await page(await login(outsider))).status_code,403)
            finally:
                await transaction.rollback()
