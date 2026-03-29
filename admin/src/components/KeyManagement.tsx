import { useState, useEffect, useCallback } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import type { ApiKey } from "../types";
import { listKeys, createKey, revokeKey } from "../api";

interface KeyManagementProps {
  onSelectTenant: (tenantId: string) => void;
}

export default function KeyManagement({ onSelectTenant }: KeyManagementProps) {
  const { logout } = useAuth0();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newRawKey, setNewRawKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadKeys = useCallback(async () => {
    try {
      const data = await listKeys();
      setKeys(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load keys");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadKeys();
  }, [loadKeys]);

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    setNewRawKey(null);
    try {
      const result = await createKey();
      setNewRawKey(result.api_key);
      setCopied(false);
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create key");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (tenantId: string) => {
    setError(null);
    try {
      await revokeKey(tenantId);
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    }
  };

  const handleCopy = async () => {
    if (newRawKey) {
      await navigator.clipboard.writeText(newRawKey);
      setCopied(true);
    }
  };

  const activeKeys = keys.filter((k) => k.status === "active");
  const revokedKeys = keys.filter((k) => k.status === "revoked");

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">
          Hikyaku — API Keys
        </h1>
        <button
          onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          Logout
        </button>
      </header>

      <div className="flex-1 max-w-2xl w-full mx-auto mt-4 px-4">
        {error && (
          <div className="bg-red-50 text-red-700 text-sm rounded-md px-4 py-2 mb-4">
            {error}
          </div>
        )}

        {newRawKey && (
          <div className="bg-green-50 border border-green-200 rounded-md p-4 mb-4">
            <p className="text-sm font-medium text-green-800 mb-2">
              API key created. Copy it now — it won't be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-white border border-green-300 rounded px-3 py-2 text-sm font-mono select-all break-all">
                {newRawKey}
              </code>
              <button
                onClick={handleCopy}
                className="shrink-0 bg-green-600 text-white px-3 py-2 rounded text-sm hover:bg-green-700"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
            <h2 className="text-sm font-medium text-gray-700">Your API Keys</h2>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="bg-blue-600 text-white px-3 py-1.5 rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create Key"}
            </button>
          </div>

          {loading ? (
            <p className="text-center text-gray-400 py-8">Loading...</p>
          ) : keys.length === 0 ? (
            <p className="text-center text-gray-400 py-8">
              No API keys yet. Create one to get started.
            </p>
          ) : (
            <div className="divide-y divide-gray-200">
              {[...activeKeys, ...revokedKeys].map((key) => (
                <div
                  key={key.tenant_id}
                  className="px-4 py-3 flex items-center justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <code className="text-sm font-mono text-gray-900">
                        {key.key_prefix}...
                      </code>
                      <span
                        className={`inline-block px-2 py-0.5 text-xs rounded-full ${
                          key.status === "active"
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-600"
                        }`}
                      >
                        {key.status}
                      </span>
                      <span className="text-xs text-gray-400">
                        {key.agent_count} agent{key.agent_count !== 1 ? "s" : ""}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      Created {new Date(key.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {key.status === "active" && (
                      <>
                        <button
                          onClick={() => onSelectTenant(key.tenant_id)}
                          className="text-sm text-blue-600 hover:text-blue-800"
                        >
                          Dashboard
                        </button>
                        <button
                          onClick={() => handleRevoke(key.tenant_id)}
                          className="text-sm text-red-600 hover:text-red-800"
                        >
                          Revoke
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <footer className="text-center text-xs text-gray-400 py-4 px-4">
        Data is ephemeral — stored in Redis only. Cleanup deletes tasks after
        deregistration TTL. Redis restart without persistence config loses all
        data.
      </footer>
    </div>
  );
}
