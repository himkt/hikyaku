from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from hikyaku_registry.config import settings


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="Hikyaku Broker",
        description=(
            "A2A-native message broker that enables coding agents "
            "to exchange unicast and broadcast messages."
        ),
        url=f"{settings.broker_base_url}/a2a",
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="send-message",
                name="Send Message",
                description=(
                    "Send a unicast or broadcast message to other agents. "
                    "Set metadata.destination to a target agent_id or '*' for broadcast."
                ),
            ),
            AgentSkill(
                id="ack-message",
                name="Acknowledge Message",
                description=(
                    "Acknowledge receipt of a message by sending a multi-turn "
                    "reply referencing the delivery task_id."
                ),
            ),
        ],
    )
