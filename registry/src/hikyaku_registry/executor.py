import uuid
from datetime import UTC, datetime

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Artifact,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore


class BrokerExecutor(AgentExecutor):
    def __init__(
        self,
        registry_store: RegistryStore,
        task_store: RedisTaskStore,
        pubsub=None,
    ) -> None:
        self._registry_store = registry_store
        self._task_store = task_store
        self._pubsub = pubsub

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        agent_id = context.call_context.state["agent_id"]
        tenant_id = context.call_context.state.get("tenant_id")
        message = context.message

        # Determine flow: ACK (multi-turn) vs send
        has_destination = (
            message is not None
            and message.metadata is not None
            and "destination" in message.metadata
        )

        if context.task_id and not has_destination:
            await self._handle_ack(context, event_queue, agent_id)
            return

        if not has_destination:
            raise ValueError("Missing destination in message metadata")

        destination = message.metadata["destination"]

        if destination == "*":
            await self._handle_broadcast(
                event_queue, agent_id, message, tenant_id
            )
        else:
            await self._handle_unicast(
                event_queue, agent_id, destination, message, tenant_id
            )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        agent_id = context.call_context.state["agent_id"]
        task_id = context.task_id

        task = await self._task_store.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        if task.metadata is None or task.metadata.get("fromAgentId") != agent_id:
            raise PermissionError("Only the sender can cancel a task")

        if task.status.state != TaskState.input_required:
            raise ValueError(
                f"Cannot cancel task in state {task.status.state}"
            )

        now = datetime.now(UTC).isoformat()
        canceled_task = Task(
            id=task.id,
            context_id=task.context_id,
            status=TaskStatus(state=TaskState.canceled, timestamp=now),
            artifacts=task.artifacts,
            metadata=task.metadata,
            history=task.history,
        )

        await self._task_store.save(canceled_task)
        await event_queue.enqueue_event(canceled_task)

    async def _handle_unicast(
        self,
        event_queue: EventQueue,
        from_agent_id: str,
        destination: str,
        message,
        tenant_id: str | None = None,
    ) -> None:
        try:
            uuid.UUID(destination)
        except ValueError:
            raise ValueError(f"Invalid destination format: {destination}")

        agent = await self._registry_store.get_agent(destination)
        if agent is None or agent.get("status") == "deregistered":
            raise ValueError(f"Destination agent not found: {destination}")

        if tenant_id is not None:
            is_same_tenant = await self._registry_store.verify_agent_tenant(
                destination, tenant_id
            )
            if not is_same_tenant:
                raise ValueError(
                    f"Destination agent not found: {destination}"
                )

        now = datetime.now(UTC).isoformat()
        delivery_task = Task(
            id=str(uuid.uuid4()),
            context_id=destination,
            status=TaskStatus(state=TaskState.input_required, timestamp=now),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    parts=message.parts,
                )
            ],
            metadata={
                "fromAgentId": from_agent_id,
                "toAgentId": destination,
                "type": "unicast",
            },
        )

        await self._task_store.save(delivery_task)
        if self._pubsub is not None:
            await self._pubsub.publish(
                f"inbox:{destination}", delivery_task.id
            )
        await event_queue.enqueue_event(delivery_task)

    async def _handle_broadcast(
        self,
        event_queue: EventQueue,
        from_agent_id: str,
        message,
        tenant_id: str | None = None,
    ) -> None:
        active_agents = await self._registry_store.list_active_agents(
            tenant_id=tenant_id
        )
        recipients = [
            a for a in active_agents if a["agent_id"] != from_agent_id
        ]

        for agent in recipients:
            now = datetime.now(UTC).isoformat()
            delivery_task = Task(
                id=str(uuid.uuid4()),
                context_id=agent["agent_id"],
                status=TaskStatus(
                    state=TaskState.input_required, timestamp=now
                ),
                artifacts=[
                    Artifact(
                        artifact_id=str(uuid.uuid4()),
                        parts=message.parts,
                    )
                ],
                metadata={
                    "fromAgentId": from_agent_id,
                    "toAgentId": agent["agent_id"],
                    "type": "unicast",
                },
            )

            await self._task_store.save(delivery_task)
            if self._pubsub is not None:
                await self._pubsub.publish(
                    f"inbox:{agent['agent_id']}", delivery_task.id
                )
            await event_queue.enqueue_event(delivery_task)

        summary_task = Task(
            id=str(uuid.uuid4()),
            context_id=from_agent_id,
            status=TaskStatus(
                state=TaskState.completed,
                timestamp=datetime.now(UTC).isoformat(),
            ),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    parts=[
                        Part(
                            root=TextPart(
                                text=f"Broadcast sent to {len(recipients)} recipients"
                            )
                        )
                    ],
                )
            ],
            metadata={
                "fromAgentId": from_agent_id,
                "type": "broadcast_summary",
                "recipientCount": len(recipients),
            },
        )

        await self._task_store.save(summary_task)
        await event_queue.enqueue_event(summary_task)

    async def _handle_ack(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        agent_id: str,
    ) -> None:
        task_id = context.task_id
        task = await self._task_store.get(task_id)

        if task is None:
            raise ValueError(f"Task {task_id} not found")

        if task.context_id != agent_id:
            raise PermissionError("Only the recipient can ACK a task")

        if task.status.state != TaskState.input_required:
            raise ValueError(
                f"Cannot ACK task in state {task.status.state}"
            )

        now = datetime.now(UTC).isoformat()
        completed_task = Task(
            id=task.id,
            context_id=task.context_id,
            status=TaskStatus(state=TaskState.completed, timestamp=now),
            artifacts=task.artifacts,
            metadata=task.metadata,
            history=task.history,
        )

        await self._task_store.save(completed_task)
        await event_queue.enqueue_event(completed_task)
