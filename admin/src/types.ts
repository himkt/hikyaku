export interface Agent {
  agent_id: string;
  name: string;
  description: string;
  status: "active" | "deregistered";
  registered_at: string;
}

export interface Message {
  task_id: string;
  from_agent_id: string;
  from_agent_name: string;
  to_agent_id: string;
  to_agent_name: string;
  type: string;
  status: "input_required" | "completed" | "canceled";
  created_at: string;
  body: string;
}

export interface LoginResponse {
  tenant_id: string;
  agents: Agent[];
}

export interface AgentsResponse {
  agents: Agent[];
}

export interface MessagesResponse {
  messages: Message[];
}

export interface SendMessageResponse {
  task_id: string;
  status: string;
}
