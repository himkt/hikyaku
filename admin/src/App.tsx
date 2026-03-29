import { useState, useEffect, useCallback } from "react";
import { Auth0Provider, useAuth0 } from "@auth0/auth0-react";
import type { Agent } from "./types";
import { setGetAccessToken, setTenantId, getAgents, getAuthConfig } from "./api";
import LoginPage from "./components/LoginPage";
import KeyManagement from "./components/KeyManagement";
import Dashboard from "./components/Dashboard";

function AppContent() {
  const { isAuthenticated, isLoading, getAccessTokenSilently } = useAuth0();
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);

  useEffect(() => {
    if (isAuthenticated) {
      setGetAccessToken(() => getAccessTokenSilently());
    } else {
      setGetAccessToken(null);
    }
  }, [isAuthenticated, getAccessTokenSilently]);

  const handleSelectTenant = useCallback(async (tenantId: string) => {
    setTenantId(tenantId);
    try {
      const data = await getAgents();
      setAgents(data.agents);
      setSelectedTenantId(tenantId);
    } catch {
      setTenantId(null);
    }
  }, []);

  const handleBackToKeys = useCallback(() => {
    setSelectedTenantId(null);
    setTenantId(null);
    setAgents([]);
  }, []);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  if (!selectedTenantId) {
    return <KeyManagement onSelectTenant={handleSelectTenant} />;
  }

  return (
    <Dashboard
      tenantId={selectedTenantId}
      initialAgents={agents}
      onLogout={handleBackToKeys}
    />
  );
}

function App() {
  const [authConfig, setAuthConfig] = useState<{
    domain: string;
    client_id: string;
  } | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    getAuthConfig()
      .then(setAuthConfig)
      .catch((err) =>
        setConfigError(err instanceof Error ? err.message : "Failed to load config"),
      );
  }, []);

  if (configError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <p className="text-red-600">Error: {configError}</p>
      </div>
    );
  }

  if (!authConfig) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <Auth0Provider
      domain={authConfig.domain}
      clientId={authConfig.client_id}
      authorizationParams={{
        redirect_uri: window.location.origin,
        audience: authConfig.client_id,
      }}
    >
      <AppContent />
    </Auth0Provider>
  );
}

export default App;
