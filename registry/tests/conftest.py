"""Shared test fixtures for hikyaku-registry tests."""

import pytest
import redis.asyncio as aioredis

from hikyaku_registry.registry_store import RegistryStore

# Use DB 15 for tests to avoid interfering with development data
TEST_REDIS_DB = 15


@pytest.fixture
async def redis_client():
    """Provide an async Redis client connected to test DB."""
    client = aioredis.Redis(host="localhost", port=6379, db=TEST_REDIS_DB, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def store(redis_client):
    """Provide a RegistryStore instance with the test Redis client."""
    return RegistryStore(redis_client)
