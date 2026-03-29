import { useState } from "react";

interface LoginPageProps {
  onLogin: (apiKey: string) => Promise<void>;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim()) return;

    setError("");
    setLoading(true);
    try {
      await onLogin(apiKey.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50">
      <div className="bg-white rounded-lg shadow-md p-8 w-full max-w-md">
        <h1 className="text-2xl font-bold text-center mb-6 text-gray-900">
          Hikyaku
        </h1>
        <form onSubmit={handleSubmit}>
          <label
            htmlFor="api-key"
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            API Key
          </label>
          <input
            id="api-key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="hky_..."
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 mb-4"
          />
          {error && (
            <p className="text-red-600 text-sm mb-4">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading || !apiKey.trim()}
            className="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Logging in..." : "Login"}
          </button>
        </form>
      </div>
      <p className="mt-6 text-xs text-gray-400 max-w-md text-center px-4">
        Data is ephemeral — stored in Redis only. Cleanup deletes tasks after
        deregistration TTL. Redis restart without persistence config loses all
        data.
      </p>
    </div>
  );
}
