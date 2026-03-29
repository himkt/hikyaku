import { useState } from "react";
import type { Message } from "../types";

interface MessageRowProps {
  message: Message;
  view: "inbox" | "sent";
}

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function statusBadge(status: string) {
  switch (status) {
    case "input_required":
      return (
        <span className="inline-block px-2 py-0.5 text-xs rounded-full bg-yellow-100 text-yellow-800">
          Pending
        </span>
      );
    case "completed":
      return (
        <span className="inline-block px-2 py-0.5 text-xs rounded-full bg-green-100 text-green-800">
          Acknowledged
        </span>
      );
    case "canceled":
      return (
        <span className="inline-block px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">
          Canceled
        </span>
      );
    default:
      return (
        <span className="inline-block px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-600">
          {status}
        </span>
      );
  }
}

export default function MessageRow({ message, view }: MessageRowProps) {
  const [expanded, setExpanded] = useState(false);

  const directionLabel = view === "inbox" ? "From:" : "To:";
  const counterpartName =
    view === "inbox" ? message.from_agent_name : message.to_agent_name;

  const bodyPreview =
    message.body.length > 120 ? message.body.slice(0, 120) + "..." : message.body;

  return (
    <div
      className="border-b border-gray-200 py-3 px-4 hover:bg-gray-50 cursor-pointer"
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs text-gray-500 shrink-0">
            {directionLabel}
          </span>
          <span className="font-medium text-sm text-gray-900 truncate">
            {counterpartName || "(unknown)"}
          </span>
          {statusBadge(message.status)}
        </div>
        <span className="text-xs text-gray-400 shrink-0">
          {formatDate(message.created_at)}
        </span>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        {expanded ? message.body : bodyPreview}
        {!expanded && message.body.length > 120 && (
          <span className="text-blue-500 ml-1 text-xs">show more</span>
        )}
      </p>
    </div>
  );
}
