import json


def format_json(data) -> str:
    return json.dumps(data, indent=2)


def format_register(data: dict) -> str:
    lines = [
        "Agent registered successfully!",
        f"  agent_id:  {data['agent_id']}",
        f"  api_key:   {data['api_key']}",
        f"  name:      {data.get('name', '')}",
        "",
        "# Set these environment variables for subsequent commands:",
        "export HIKYAKU_URL=${HIKYAKU_URL:-http://localhost:8000}",
        f"export HIKYAKU_API_KEY={data['api_key']}",
        f"export HIKYAKU_AGENT_ID={data['agent_id']}",
    ]
    return "\n".join(lines)


def format_task(task: dict) -> str:
    if "task" in task:
        task = task["task"]
    task_id = task.get("id", "?")
    state = task.get("status", {}).get("state", "?")
    from_agent = task.get("metadata", {}).get("fromAgentId", "?")
    to_agent = task.get("metadata", {}).get("toAgentId", "?")
    msg_type = task.get("metadata", {}).get("type", "?")
    text = ""
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                text = part["text"]
                break
        if text:
            break
    lines = [
        f"  id:    {task_id}",
        f"  state: {state}",
        f"  from:  {from_agent}",
        f"  to:    {to_agent}",
        f"  type:  {msg_type}",
    ]
    if text:
        lines.append(f"  text:  {text}")
    return "\n".join(lines)


def format_task_list(tasks: list) -> str:
    if not tasks:
        return "No messages found."
    parts = []
    for i, task in enumerate(tasks):
        parts.append(f"[{i + 1}]")
        parts.append(format_task(task))
    return "\n".join(parts)


def format_agent(agent: dict) -> str:
    lines = [
        f"  agent_id:    {agent.get('agent_id', '?')}",
        f"  name:        {agent.get('name', '?')}",
        f"  description: {agent.get('description', '?')}",
        f"  status:      {agent.get('status', 'active')}",
    ]
    return "\n".join(lines)


def format_agent_list(agents: list) -> str:
    if not agents:
        return "No agents found."
    parts = []
    for i, agent in enumerate(agents):
        parts.append(f"[{i + 1}]")
        parts.append(format_agent(agent))
    return "\n".join(parts)
