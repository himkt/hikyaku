import type {
  LoginResponse,
  AgentsResponse,
  MessagesResponse,
  SendMessageResponse,
} from "./types";

let apiKey: string | null = null;

export function setApiKey(key: string | null): void {
  apiKey = key;
}

export function getApiKey(): string | null {
  return apiKey;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }

  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(`/ui/api${path}`, { ...options, headers });

  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<T>;
}

export async function login(): Promise<LoginResponse> {
  return request<LoginResponse>("/login", { method: "POST" });
}

export async function getAgents(): Promise<AgentsResponse> {
  return request<AgentsResponse>("/agents");
}

export async function getInbox(agentId: string): Promise<MessagesResponse> {
  return request<MessagesResponse>(`/agents/${agentId}/inbox`);
}

export async function getSent(agentId: string): Promise<MessagesResponse> {
  return request<MessagesResponse>(`/agents/${agentId}/sent`);
}

export async function sendMessage(
  fromAgentId: string,
  toAgentId: string,
  text: string,
): Promise<SendMessageResponse> {
  return request<SendMessageResponse>("/messages/send", {
    method: "POST",
    body: JSON.stringify({
      from_agent_id: fromAgentId,
      to_agent_id: toAgentId,
      text,
    }),
  });
}
