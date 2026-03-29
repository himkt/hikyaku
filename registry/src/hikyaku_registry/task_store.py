from datetime import UTC, datetime

import redis.asyncio as aioredis
from a2a.types import Task


class RedisTaskStore:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def save(self, task: Task) -> None:
        task_id = task.id
        context_id = task.context_id
        metadata = task.metadata or {}
        from_agent_id = metadata.get("fromAgentId", "")
        to_agent_id = metadata.get("toAgentId", "")
        msg_type = metadata.get("type", "")

        assert task.status.timestamp is not None
        ts = datetime.fromisoformat(task.status.timestamp)
        score = ts.timestamp()

        existing_created_at = await self._redis.hget(f"task:{task_id}", "created_at")
        created_at = existing_created_at or datetime.now(UTC).isoformat()

        record = {
            "task_json": task.model_dump_json(),
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "type": msg_type,
            "created_at": created_at,
        }

        pipe = self._redis.pipeline()
        pipe.hset(f"task:{task_id}", mapping=record)
        pipe.zadd(f"tasks:ctx:{context_id}", {task_id: score})
        pipe.sadd(f"tasks:sender:{from_agent_id}", task_id)
        await pipe.execute()

    async def get(self, task_id: str) -> Task | None:
        task_json = await self._redis.hget(f"task:{task_id}", "task_json")
        if task_json is None:
            return None
        return Task.model_validate_json(task_json)

    async def delete(self, task_id: str) -> None:
        record = await self._redis.hgetall(f"task:{task_id}")
        if not record:
            return

        task_json = record.get("task_json")
        from_agent_id = record.get("from_agent_id", "")
        context_id = None
        if task_json:
            task = Task.model_validate_json(task_json)
            context_id = task.context_id

        pipe = self._redis.pipeline()
        pipe.delete(f"task:{task_id}")
        if context_id:
            pipe.zrem(f"tasks:ctx:{context_id}", task_id)
        if from_agent_id:
            pipe.srem(f"tasks:sender:{from_agent_id}", task_id)
        await pipe.execute()

    async def list(self, context_id: str) -> list[Task]:
        task_ids = await self._redis.zrevrange(f"tasks:ctx:{context_id}", 0, -1)
        if not task_ids:
            return []

        tasks = []
        for task_id in task_ids:
            task = await self.get(task_id)
            if task is not None:
                tasks.append(task)
        return tasks
