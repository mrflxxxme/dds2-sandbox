"""
API test fixtures — async FastAPI test client with test database.

Uses TESTING=1 env var to disable rate limiter.
Uses existing DB schema from Alembic (no create_all / drop_all).
"""

import os

os.environ["TESTING"] = "1"  # Disable rate limiter before importing app

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.database import get_db
from backend.main import app

# ─── Test DB engine (session-scoped to avoid pool conflicts) ──────────────────
# Loop scope is set to "session" in pytest.ini (asyncio_default_test_loop_scope),
# so the module-level engine stays bound to one event loop for the whole test run.

TEST_DATABASE_URL = settings.DATABASE_URL

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_recycle=300,
)
TestSessionLocal = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    """Override DB dependency with test session."""
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async HTTP test client for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    """Direct DB session for setup/teardown in tests."""
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register a test user and return auth headers."""
    username = f"testuser_{uuid.uuid4().hex[:8]}"

    # Register
    await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "testpass123",
            "email": f"{username}@test.com",
        },
    )

    # Login
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": "testpass123",
        },
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    return {"Authorization": f"Bearer {data['access_token']}"}
