"""Tests for pubsub.py — PubSubManager for Redis Pub/Sub notifications.

Covers: publish to channel, subscribe to channel, unsubscribe from channel,
multi-channel isolation (subscribers only receive their own messages).

Channel pattern: inbox:{agent_id}
Payload: task_id string (lightweight; full Task fetched separately)
"""

import asyncio

import pytest
import fakeredis.aioredis

from hikyaku_registry.pubsub import PubSubManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client():
    """Provide a fake async Redis client for testing."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def pubsub(redis_client):
    """Provide a PubSubManager instance with fake Redis."""
    return PubSubManager(redis_client)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class TestPublish:
    """Tests for PubSubManager.publish — publishing task_id to a channel."""

    @pytest.mark.asyncio
    async def test_publish_sends_message_to_channel(self, redis_client, pubsub):
        """publish() delivers task_id to the specified Redis Pub/Sub channel."""
        channel = "inbox:agent-001"

        # Subscribe using raw Redis to verify publish works
        raw_pubsub = redis_client.pubsub()
        await raw_pubsub.subscribe(channel)
        # Consume the subscribe confirmation message
        await raw_pubsub.get_message(timeout=1)

        await pubsub.publish(channel, "task-abc-123")

        msg = await raw_pubsub.get_message(timeout=1)
        assert msg is not None
        assert msg["type"] == "message"
        assert msg["data"] == "task-abc-123"
        assert msg["channel"] == channel

        await raw_pubsub.unsubscribe(channel)
        await raw_pubsub.aclose()

    @pytest.mark.asyncio
    async def test_publish_to_correct_channel(self, redis_client, pubsub):
        """publish() targets the exact channel name provided."""
        channel_a = "inbox:agent-aaa"
        channel_b = "inbox:agent-bbb"

        raw_pubsub_a = redis_client.pubsub()
        raw_pubsub_b = redis_client.pubsub()
        await raw_pubsub_a.subscribe(channel_a)
        await raw_pubsub_b.subscribe(channel_b)
        await raw_pubsub_a.get_message(timeout=1)
        await raw_pubsub_b.get_message(timeout=1)

        # Publish only to channel_a
        await pubsub.publish(channel_a, "task-only-a")

        msg_a = await raw_pubsub_a.get_message(timeout=1)
        msg_b = await raw_pubsub_b.get_message(timeout=1)

        assert msg_a is not None
        assert msg_a["data"] == "task-only-a"
        assert msg_b is None  # channel_b should not receive anything

        await raw_pubsub_a.unsubscribe(channel_a)
        await raw_pubsub_b.unsubscribe(channel_b)
        await raw_pubsub_a.aclose()
        await raw_pubsub_b.aclose()

    @pytest.mark.asyncio
    async def test_publish_payload_is_task_id_string(self, redis_client, pubsub):
        """Payload published is just the task_id string, not full Task JSON."""
        channel = "inbox:agent-002"

        raw_pubsub = redis_client.pubsub()
        await raw_pubsub.subscribe(channel)
        await raw_pubsub.get_message(timeout=1)

        task_id = "550e8400-e29b-41d4-a716-446655440000"
        await pubsub.publish(channel, task_id)

        msg = await raw_pubsub.get_message(timeout=1)
        assert msg["data"] == task_id

        await raw_pubsub.unsubscribe(channel)
        await raw_pubsub.aclose()


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    """Tests for PubSubManager.subscribe — subscribing to a channel."""

    @pytest.mark.asyncio
    async def test_subscribe_yields_published_messages(self, pubsub):
        """subscribe() returns an async iterator that yields published messages."""
        channel = "inbox:agent-sub-001"
        received = []

        subscription = await pubsub.subscribe(channel)

        # Publish a message after subscribing
        await pubsub.publish(channel, "task-sub-1")

        # Read from the subscription
        async for msg in subscription:
            received.append(msg)
            break  # Just get one message

        assert len(received) == 1
        assert received[0] == "task-sub-1"

    @pytest.mark.asyncio
    async def test_subscribe_yields_multiple_messages_in_order(self, pubsub):
        """subscribe() yields messages in the order they were published."""
        channel = "inbox:agent-sub-002"
        received = []

        subscription = await pubsub.subscribe(channel)

        await pubsub.publish(channel, "task-first")
        await pubsub.publish(channel, "task-second")
        await pubsub.publish(channel, "task-third")

        count = 0
        async for msg in subscription:
            received.append(msg)
            count += 1
            if count >= 3:
                break

        assert received == ["task-first", "task-second", "task-third"]

    @pytest.mark.asyncio
    async def test_subscribe_only_receives_after_subscription(self, pubsub):
        """Messages published before subscribe() are not received."""
        channel = "inbox:agent-sub-003"

        # Publish before subscribing
        await pubsub.publish(channel, "task-before")

        subscription = await pubsub.subscribe(channel)

        # Publish after subscribing
        await pubsub.publish(channel, "task-after")

        async for msg in subscription:
            assert msg == "task-after"
            break


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


class TestUnsubscribe:
    """Tests for PubSubManager.unsubscribe — stopping message reception."""

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving_messages(self, pubsub):
        """After unsubscribe(), no more messages are received on that channel."""
        channel = "inbox:agent-unsub-001"
        received = []

        subscription = await pubsub.subscribe(channel)

        # Publish and receive one message
        await pubsub.publish(channel, "task-before-unsub")
        async for msg in subscription:
            received.append(msg)
            break

        assert received == ["task-before-unsub"]

        # Unsubscribe
        await pubsub.unsubscribe(channel)

        # Publish again — should not be received
        await pubsub.publish(channel, "task-after-unsub")

        # Give a brief moment for any message to arrive (it shouldn't)
        await asyncio.sleep(0.05)

        # Verify no new messages came through
        assert len(received) == 1


# ---------------------------------------------------------------------------
# Multi-Channel Isolation
# ---------------------------------------------------------------------------


class TestMultiChannelIsolation:
    """Tests for channel isolation — subscribers only get their channel's messages."""

    @pytest.mark.asyncio
    async def test_different_channels_receive_only_own_messages(self, pubsub):
        """Subscribers on different channels receive only their own messages."""
        channel_a = "inbox:agent-iso-aaa"
        channel_b = "inbox:agent-iso-bbb"
        received_a = []
        received_b = []

        sub_a = await pubsub.subscribe(channel_a)
        sub_b = await pubsub.subscribe(channel_b)

        await pubsub.publish(channel_a, "task-for-a")
        await pubsub.publish(channel_b, "task-for-b")

        async for msg in sub_a:
            received_a.append(msg)
            break

        async for msg in sub_b:
            received_b.append(msg)
            break

        assert received_a == ["task-for-a"]
        assert received_b == ["task-for-b"]

    @pytest.mark.asyncio
    async def test_publish_to_one_channel_does_not_leak_to_other(self, pubsub):
        """Publishing to channel A does not deliver to channel B subscriber."""
        channel_a = "inbox:agent-leak-aaa"
        channel_b = "inbox:agent-leak-bbb"

        sub_b = await pubsub.subscribe(channel_b)

        await pubsub.publish(channel_a, "task-only-a")

        # Give time for potential leaky delivery
        await asyncio.sleep(0.05)

        # Publish to channel_b so we can test it works normally
        await pubsub.publish(channel_b, "task-for-b")

        async for msg in sub_b:
            # First message should be task-for-b, not task-only-a
            assert msg == "task-for-b"
            break

    @pytest.mark.asyncio
    async def test_multiple_publishes_to_multiple_channels(self, pubsub):
        """Multiple messages to multiple channels are correctly routed."""
        channels = {
            "inbox:agent-multi-1": [],
            "inbox:agent-multi-2": [],
            "inbox:agent-multi-3": [],
        }
        subscriptions = {}

        for ch in channels:
            subscriptions[ch] = await pubsub.subscribe(ch)

        # Publish different task_ids to each channel
        await pubsub.publish("inbox:agent-multi-1", "task-m1")
        await pubsub.publish("inbox:agent-multi-2", "task-m2")
        await pubsub.publish("inbox:agent-multi-3", "task-m3")

        for ch, sub in subscriptions.items():
            async for msg in sub:
                channels[ch].append(msg)
                break

        assert channels["inbox:agent-multi-1"] == ["task-m1"]
        assert channels["inbox:agent-multi-2"] == ["task-m2"]
        assert channels["inbox:agent-multi-3"] == ["task-m3"]
