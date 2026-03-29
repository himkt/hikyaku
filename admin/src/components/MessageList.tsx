import type { Message } from "../types";
import MessageRow from "./MessageRow";

interface MessageListProps {
  messages: Message[];
  view: "inbox" | "sent";
}

export default function MessageList({ messages, view }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <p className="text-center text-gray-400 py-8">No messages yet</p>
    );
  }

  return (
    <div className="divide-y divide-gray-200">
      {messages.map((msg) => (
        <MessageRow key={msg.task_id} message={msg} view={view} />
      ))}
    </div>
  );
}
