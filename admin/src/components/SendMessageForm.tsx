import { useState } from "react";
import type { Agent } from "../types";
import { sendMessage } from "../api";

interface SendMessageFormProps {
  fromAgentId: string;
  agents: Agent[];
}

export default function SendMessageForm({
  fromAgentId,
  agents,
}: SendMessageFormProps) {
  const [toAgentId, setToAgentId] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const otherActiveAgents = agents.filter(
    (a) => a.status === "active" && a.agent_id !== fromAgentId,
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!toAgentId || !text.trim()) return;

    setSending(true);
    setFeedback(null);
    try {
      await sendMessage(fromAgentId, toAgentId, text.trim());
      setFeedback({ type: "success", message: "Message sent" });
      setText("");
    } catch (err) {
      setFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "Send failed",
      });
    } finally {
      setSending(false);
    }
  };

  if (otherActiveAgents.length === 0) {
    return null;
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-gray-200 p-4 bg-gray-50"
    >
      <div className="flex gap-2 mb-2">
        <select
          value={toAgentId}
          onChange={(e) => setToAgentId(e.target.value)}
          className="border border-gray-300 rounded-md px-2 py-1 text-sm flex-shrink-0"
        >
          <option value="">To...</option>
          {otherActiveAgents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.name}
            </option>
          ))}
        </select>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Message body"
        rows={2}
        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm mb-2 resize-none"
      />
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={sending || !toAgentId || !text.trim()}
          className="bg-blue-600 text-white px-4 py-1.5 rounded-md text-sm hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {sending ? "Sending..." : "Send"}
        </button>
        {feedback && (
          <span
            className={`text-sm ${feedback.type === "success" ? "text-green-600" : "text-red-600"}`}
          >
            {feedback.message}
          </span>
        )}
      </div>
    </form>
  );
}
