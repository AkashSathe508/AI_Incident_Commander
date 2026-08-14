import { useState } from "react";
import { useNavigate } from "react-router-dom";

const API = "/api";

// Vibrant color list for avatar backgrounds
const AVATAR_COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899",
  "#f59e0b", "#10b981", "#3b82f6",
  "#ef4444", "#14b8a6",
];

export function avatarColor(seed) {
  const n = typeof seed === "number"
    ? seed
    : [...String(seed)].reduce((a, c) => a + c.charCodeAt(0), 0);
  return AVATAR_COLORS[n % AVATAR_COLORS.length];
}

export function getInitials(name = "") {
  return name
    .split(" ")
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2) || "?";
}

export default function CreateRoom() {
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { meeting_id, channel_name, title }
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  async function handleCreate(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/meetings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim() || "Incident Room" }),
      });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data = await res.json();
      setResult(data);
    } catch (err) {
      setError(err.message || "Failed to create room");
    } finally {
      setLoading(false);
    }
  }

  const joinUrl = result
    ? `${window.location.origin}/room/${result.meeting_id}`
    : "";

  async function copyLink() {
    await navigator.clipboard.writeText(joinUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }

  return (
    <div className="create-root">
      {/* Background grid */}
      <div className="bg-grid" aria-hidden="true" />

      <div className="create-card">
        {/* Logo mark */}
        <div className="logo-mark" aria-hidden="true">
          <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
            <circle cx="20" cy="20" r="20" fill="url(#lg)" />
            <path
              d="M13 20a7 7 0 1 1 14 0"
              stroke="#fff"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
            <circle cx="20" cy="20" r="3" fill="#fff" />
            <defs>
              <linearGradient id="lg" x1="0" y1="0" x2="40" y2="40">
                <stop stopColor="#6366f1" />
                <stop offset="1" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
          </svg>
        </div>

        <h1 className="create-heading">Incident Commander</h1>
        <p className="create-sub">
          Start a secure, real-time voice room for your team.
        </p>

        {!result ? (
          <form onSubmit={handleCreate} className="create-form">
            <div className="field-wrap">
              <label htmlFor="room-title" className="field-label">
                Room title <span className="field-optional">(optional)</span>
              </label>
              <input
                id="room-title"
                type="text"
                placeholder="e.g. Production outage — DB cluster"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="field-input"
                maxLength={255}
                autoFocus
              />
            </div>

            {error && <p className="error-msg">{error}</p>}

            <button
              type="submit"
              disabled={loading}
              className="btn-primary"
            >
              {loading ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  Creating…
                </>
              ) : (
                <>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  Create Incident Room
                </>
              )}
            </button>
          </form>
        ) : (
          <div className="success-wrap">
            <div className="success-badge">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Room created
            </div>

            <p className="room-title-display">{result.title}</p>

            <div className="link-box">
              <span className="link-text">{joinUrl}</span>
              <button
                onClick={copyLink}
                className={`btn-copy ${copied ? "btn-copy--done" : ""}`}
                title="Copy link"
              >
                {copied ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <rect x="9" y="9" width="13" height="13" rx="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                )}
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>

            <div className="cta-row">
              <button
                onClick={() => navigate(`/room/${result.meeting_id}`)}
                className="btn-primary"
              >
                Join as host →
              </button>
              <button
                onClick={() => setResult(null)}
                className="btn-ghost"
              >
                Create another
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
