import { useState } from "react";
import type { Agent } from "./types";
import { setApiKey, login } from "./api";
import LoginPage from "./components/LoginPage";
import Dashboard from "./components/Dashboard";

function App() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);

  const handleLogin = async (key: string) => {
    setApiKey(key);
    try {
      const data = await login();
      setTenantId(data.tenant_id);
      setAgents(data.agents);
    } catch (err) {
      setApiKey(null);
      throw err;
    }
  };

  const handleLogout = () => {
    setApiKey(null);
    setTenantId(null);
    setAgents([]);
  };

  if (!tenantId) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <Dashboard
      tenantId={tenantId}
      initialAgents={agents}
      onLogout={handleLogout}
    />
  );
}

export default App;
