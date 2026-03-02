"""
API test fixtures — async FastAPI test client with test database.
"""

import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.database import Base, get_db
from backend.main import app


# ─── Test DB engine ──────────────────────────────────────────────────────────

TEST_DATABASE_URL = settings.DATABASE_URL

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    """Override DB dependency with test session."""
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Create a shared event loop for session-scoped fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def setup_db():
    """Create all tables before tests, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db) -> AsyncClient:
    """Async HTTP test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session(setup_db) -> AsyncSession:
    """Direct DB session for setup/teardown in tests."""
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register a test user and return auth headers."""
    import uuid
    username = f"testuser_{uuid.uuid4().hex[:8]}"

    # Register
    await client.post("/api/auth/register", json={
        "username": username,
        "password": "testpass123",
        "email": f"{username}@test.com",
    })

    # Login
    resp = await client.post("/api/auth/login", data={
        "username": username,
        "password": "testpass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
