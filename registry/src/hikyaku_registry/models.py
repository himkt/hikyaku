from pydantic import BaseModel


class RegisterAgentRequest(BaseModel):
    name: str
    description: str
    skills: list[dict] | None = None


class RegisterAgentResponse(BaseModel):
    agent_id: str
    api_key: str
    name: str
    registered_at: str


class AgentSummary(BaseModel):
    agent_id: str
    name: str
    description: str
    registered_at: str


class ListAgentsResponse(BaseModel):
    agents: list[AgentSummary]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
