import { useState, useEffect, useCallback } from "react";
import type { Agent, Message } from "../types";
import { getAgents, getInbox, getSent } from "../api";
import AgentTabs from "./AgentTabs";
import MessageList from "./MessageList";
import SendMessageForm from "./SendMessageForm";

interface DashboardProps {
  tenantId: string;
  initialAgents: Agent[];
  onLogout: () => void;
}

export default function Dashboard({
  tenantId,
  initialAgents,
  onLogout: onBack,
}: DashboardProps) {
  const [agents, setAgents] = useState<Agent[]>(initialAgents);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(
    initialAgents.length > 0 ? initialAgents[0].agent_id : null,
  );
  const [activeSubTab, setActiveSubTab] = useState<"inbox" | "sent">("inbox");
  const [messages, setMessages] = useState<Message[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const selectedAgent = agents.find((a) => a.agent_id === selectedAgentId);

  const loadMessages = useCallback(async () => {
    if (!selectedAgentId) {
      setMessages([]);
      return;
    }
    try {
      const data =
        activeSubTab === "inbox"
          ? await getInbox(selectedAgentId)
          : await getSent(selectedAgentId);
      setMessages(data.messages);
    } catch {
      setMessages([]);
    }
  }, [selectedAgentId, activeSubTab]);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const data = await getAgents();
      setAgents(data.agents);
      await loadMessages();
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  const handleSelectAgent = (agentId: string) => {
    setSelectedAgentId(agentId);
    setActiveSubTab("inbox");
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">
          Hikyaku —{" "}
          <span className="font-mono text-sm text-gray-500">
            {tenantId.slice(0, 8)}
          </span>
        </h1>
        <button
          onClick={onBack}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          Back to Keys
        </button>
      </header>

      <div className="flex-1 max-w-4xl w-full mx-auto mt-4">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
          <AgentTabs
            agents={agents}
            selectedAgentId={selectedAgentId}
            onSelectAgent={handleSelectAgent}
            activeSubTab={activeSubTab}
            onSelectSubTab={setActiveSubTab}
            onRefresh={handleRefresh}
            refreshing={refreshing}
          />

          <div className="min-h-[300px]">
            {selectedAgentId ? (
              <MessageList messages={messages} view={activeSubTab} />
            ) : (
              <p className="text-center text-gray-400 py-8">
                Select an agent to view messages
              </p>
            )}
          </div>

          {selectedAgent && selectedAgent.status === "active" && (
            <SendMessageForm
              fromAgentId={selectedAgent.agent_id}
              agents={agents}
            />
          )}
        </div>
      </div>

      <footer className="text-center text-xs text-gray-400 py-4 px-4">
        Message data is stored in Redis only and is ephemeral. The cleanup
        process deletes tasks after the deregistration TTL expires. A Redis
        restart without persistence configuration will lose all data.
      </footer>
    </div>
  );
}
