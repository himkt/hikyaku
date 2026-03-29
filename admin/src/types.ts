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

export interface ApiKey {
  tenant_id: string;
  key_prefix: string;
  created_at: string;
  status: "active" | "revoked";
  agent_count: number;
}

export interface CreateKeyResponse {
  api_key: string;
  tenant_id: string;
  created_at: string;
}
