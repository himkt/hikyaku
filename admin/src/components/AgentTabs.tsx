import type { Agent } from "../types";

interface AgentTabsProps {
  agents: Agent[];
  selectedAgentId: string | null;
  onSelectAgent: (agentId: string) => void;
  activeSubTab: "inbox" | "sent";
  onSelectSubTab: (tab: "inbox" | "sent") => void;
  onRefresh: () => void;
  refreshing: boolean;
}

export default function AgentTabs({
  agents,
  selectedAgentId,
  onSelectAgent,
  activeSubTab,
  onSelectSubTab,
  onRefresh,
  refreshing,
}: AgentTabsProps) {
  const activeAgents = agents
    .filter((a) => a.status === "active")
    .sort((a, b) => a.name.localeCompare(b.name));

  const deregisteredAgents = agents
    .filter((a) => a.status === "deregistered")
    .sort((a, b) => a.name.localeCompare(b.name));

  const sorted = [...activeAgents, ...deregisteredAgents];

  return (
    <div>
      <div className="flex items-center border-b border-gray-200">
        <div className="flex overflow-x-auto flex-1">
          {sorted.map((agent) => (
            <button
              key={agent.agent_id}
              onClick={() => onSelectAgent(agent.agent_id)}
              className={`px-4 py-2 text-sm whitespace-nowrap border-b-2 transition-colors ${
                selectedAgentId === agent.agent_id
                  ? "border-blue-500 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              } ${agent.status === "deregistered" ? "opacity-50" : ""}`}
            >
              {agent.name}
              {agent.status === "deregistered" && (
                <span className="text-xs ml-1">(deregistered)</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {selectedAgentId && (
        <div className="flex items-center gap-2 border-b border-gray-100 px-4">
          <button
            onClick={() => onSelectSubTab("inbox")}
            className={`px-3 py-2 text-sm ${
              activeSubTab === "inbox"
                ? "text-blue-600 border-b-2 border-blue-500"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Inbox
          </button>
          <button
            onClick={() => onSelectSubTab("sent")}
            className={`px-3 py-2 text-sm ${
              activeSubTab === "sent"
                ? "text-blue-600 border-b-2 border-blue-500"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Sent
          </button>
          <div className="flex-1" />
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="text-xs text-gray-500 hover:text-gray-700 disabled:opacity-50 px-2 py-1"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      )}
    </div>
  );
}
