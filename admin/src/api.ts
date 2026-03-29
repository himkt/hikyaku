import type {
  AgentsResponse,
  MessagesResponse,
  SendMessageResponse,
  ApiKey,
  CreateKeyResponse,
} from "./types";

let getAccessToken: (() => Promise<string>) | null = null;
let tenantId: string | null = null;

export function setGetAccessToken(fn: (() => Promise<string>) | null): void {
  getAccessToken = fn;
}

export function setTenantId(id: string | null): void {
  tenantId = id;
}

export function getTenantId(): string | null {
  return tenantId;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (getAccessToken) {
    const token = await getAccessToken();
    headers["Authorization"] = `Bearer ${token}`;
  }

  if (tenantId) {
    headers["X-Tenant-Id"] = tenantId;
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

export async function getAuthConfig(): Promise<{
  domain: string;
  client_id: string;
}> {
  const resp = await fetch("/ui/api/auth/config");
  if (!resp.ok) {
    throw new Error("Failed to load auth config");
  }
  return resp.json();
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

export async function createKey(): Promise<CreateKeyResponse> {
  return request<CreateKeyResponse>("/keys", { method: "POST" });
}

export async function listKeys(): Promise<ApiKey[]> {
  return request<ApiKey[]>("/keys");
}

export async function revokeKey(keyTenantId: string): Promise<void> {
  await request<void>(`/keys/${keyTenantId}`, { method: "DELETE" });
}
