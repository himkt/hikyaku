import { useAuth0 } from "@auth0/auth0-react";

export default function LoginPage() {
  const { loginWithRedirect, isLoading, error } = useAuth0();

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50">
      <div className="bg-white rounded-lg shadow-md p-8 w-full max-w-md">
        <h1 className="text-2xl font-bold text-center mb-6 text-gray-900">
          Hikyaku
        </h1>
        {error && (
          <p className="text-red-600 text-sm mb-4 text-center">
            {error.message}
          </p>
        )}
        <button
          onClick={() => loginWithRedirect()}
          disabled={isLoading}
          className="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "Loading..." : "Login with Auth0"}
        </button>
      </div>
      <p className="mt-6 text-xs text-gray-400 max-w-md text-center px-4">
        Data is ephemeral — stored in Redis only. Cleanup deletes tasks after
        deregistration TTL. Redis restart without persistence config loses all
        data.
      </p>
    </div>
  );
}
