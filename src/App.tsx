
import { useState } from "react";
import { checkBackend } from "./lib/api";

function App() {
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConnect() {
    setLoading(true);
    setMessage(null);
    setError(null);

    try {
      const data = await checkBackend();
      setMessage(data.message);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to connect to backend"
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center gap-6 p-6">
      <h1 className="text-4xl font-semibold tracking-tight">
        AstroChronoscope
      </h1>

      <button
        onClick={handleConnect}
        disabled={loading}
        className="rounded border border-neutral-700 bg-neutral-900 px-5 py-2 text-sm text-neutral-200 hover:bg-neutral-800 disabled:opacity-50 cursor-pointer"
      >
        {loading ? "Connecting..." : "Connect to Backend"}
      </button>

      {message && (
        <p className="text-sm text-neutral-300">{message}</p>
      )}

      {error && (
        <p className="text-sm text-red-400">{error}</p>
      )}
    </main>
  );
}

export default App;
