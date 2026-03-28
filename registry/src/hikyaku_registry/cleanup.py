from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis


async def cleanup_expired_agents(
    redis: aioredis.Redis,
    ttl_days: int = 7,
) -> int:
    """Remove deregistered agents whose TTL has expired.

    Deletes the agent record, all tasks addressed to the agent,
    the context sorted set, and removes task IDs from sender sets.

    Returns the number of agents cleaned up.
    """
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
    cleaned = 0

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="agent:*", count=100)

        for key in keys:
            record = await redis.hgetall(key)
            if not record:
                continue

            if record.get("status") != "deregistered":
                continue

            deregistered_at_str = record.get("deregistered_at")
            if not deregistered_at_str:
                continue

            deregistered_at = datetime.fromisoformat(deregistered_at_str)
            if deregistered_at >= cutoff:
                continue

            agent_id = record.get("agent_id", key.split(":", 1)[1])

            # Remove from tenant set
            api_key_hash = record.get("api_key_hash")
            if api_key_hash:
                await redis.srem(f"tenant:{api_key_hash}:agents", agent_id)

            # Delete all tasks in the agent's context sorted set
            task_ids = await redis.zrange(f"tasks:ctx:{agent_id}", 0, -1)
            for task_id in task_ids:
                task_record = await redis.hgetall(f"task:{task_id}")
                from_agent_id = task_record.get("from_agent_id", "")
                if from_agent_id:
                    await redis.srem(f"tasks:sender:{from_agent_id}", task_id)
                await redis.delete(f"task:{task_id}")

            # Delete the context sorted set
            await redis.delete(f"tasks:ctx:{agent_id}")

            # Delete the agent record
            await redis.delete(key)

            cleaned += 1

        if cursor == 0:
            break

    return cleaned
