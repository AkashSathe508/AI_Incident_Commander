import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";

const API = "/api";

const SEV_COLORS = {
  "SEV-1": "#ef4444",
  "SEV-2": "#f97316",
  "SEV-3": "#eab308",
  "SEV-4": "#22c55e",
};

const TYPE_ICONS = {
  analysis: "🔍",
  recommendation: "💡",
  summary: "📋",
  alert: "🚨",
};

function StatusPill({ status }) {
  const color =
    status === "active"
      ? "#22c55e"
      : status === "ended"
      ? "#64748b"
      : "#6366f1";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.3rem",
        padding: "0.15rem 0.6rem",
        borderRadius: "999px",
        fontSize: "0.72rem",
        fontWeight: 600,
        background: `${color}20`,
        color: color,
        border: `1px solid ${color}40`,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
      }}
    >
      {status === "active" && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: color,
            animation: "pulse-dot 1.5s ease-in-out infinite",
          }}
        />
      )}
      {status}
    </span>
  );
}

function SevBadge({ sev }) {
  if (!sev) return null;
  const color = SEV_COLORS[sev] || "#6366f1";
  return (
    <span
      style={{
        padding: "0.15rem 0.55rem",
        borderRadius: 6,
        fontSize: "0.72rem",
        fontWeight: 700,
        background: `${color}22`,
        color: color,
        border: `1px solid ${color}55`,
        letterSpacing: "0.04em",
      }}
    >
      {sev}
    </span>
  );
}

function MetricChip({ label, value, accent }) {
  return (
    <div className="dash-metric-chip" style={{ "--chip-accent": accent || "#6366f1" }}>
      <span className="dash-metric-value">{value}</span>
      <span className="dash-metric-label">{label}</span>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    fetch(`${API}/meetings?limit=30`)
      .then((r) => r.json())
      .then((data) => {
        setMeetings(data.meetings || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const activeMeetings = meetings.filter((m) => m.status === "active");
  const endedMeetings = meetings.filter((m) => m.status === "ended");

  function formatDuration(startStr) {
    if (!startStr) return "—";
    const start = new Date(startStr).getTime();
    const diffMs = now - start;
    if (diffMs < 0) return "—";
    const h = Math.floor(diffMs / 3600000);
    const m = Math.floor((diffMs % 3600000) / 60000);
    const s = Math.floor((diffMs % 60000) / 1000);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function formatDate(d) {
    if (!d) return "—";
    return new Date(d).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="dash-root">
      {/* Animated background */}
      <div className="bg-grid" aria-hidden="true" />

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="dash-header">
        <div className="dash-header-left">
          <div className="dash-logo" aria-hidden="true">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <circle cx="16" cy="16" r="16" fill="url(#dlg)" />
              <path d="M8 16l6 6 10-10" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              <defs>
                <linearGradient id="dlg" x1="0" y1="0" x2="32" y2="32">
                  <stop stopColor="#6366f1" />
                  <stop offset="1" stopColor="#8b5cf6" />
                </linearGradient>
              </defs>
            </svg>
          </div>
          <div>
            <h1 className="dash-header-title">SENTINEL</h1>
            <p className="dash-header-sub">AI Incident Command Center</p>
          </div>
        </div>
        <div className="dash-header-right">
          <div className="dash-system-status">
            <span className="status-dot" style={{ background: "#22c55e" }} />
            <span>All systems nominal</span>
          </div>
          <Link to="/meetings" className="dash-nav-link">
            History
          </Link>
          <button
            id="new-incident-btn"
            className="dash-new-btn"
            onClick={() => navigate("/new")}
          >
            + New Incident
          </button>
        </div>
      </header>

      {/* ── Command Strip ──────────────────────────────────────────────── */}
      <div className="dash-command-strip">
        <MetricChip
          label="Active Incidents"
          value={activeMeetings.length}
          accent="#ef4444"
        />
        <MetricChip
          label="Total Incidents"
          value={meetings.length}
          accent="#6366f1"
        />
        <MetricChip
          label="Resolved"
          value={endedMeetings.length}
          accent="#22c55e"
        />
        <MetricChip
          label="Pending Review"
          value={activeMeetings.reduce((s, m) => s + (m.open_action_count || 0), 0)}
          accent="#f97316"
        />
      </div>

      {/* ── Main Body ─────────────────────────────────────────────────── */}
      <div className="dash-body">
        {/* Left column */}
        <div className="dash-col dash-col--main">
          {/* Active Incidents */}
          <section className="dash-section">
            <div className="dash-section-hdr">
              <h2 className="dash-section-title">
                <span className="section-dot section-dot--red" />
                Active Incidents
              </h2>
              {activeMeetings.length > 0 && (
                <span className="dash-section-count">{activeMeetings.length}</span>
              )}
            </div>

            {loading && (
              <div className="dash-empty">
                <div className="spinner" />
              </div>
            )}

            {!loading && activeMeetings.length === 0 && (
              <div className="dash-empty">
                <p className="dash-empty-text">No active incidents</p>
                <button
                  className="dash-start-btn"
                  onClick={() => navigate("/new")}
                  id="start-incident-btn"
                >
                  Start Incident Room →
                </button>
              </div>
            )}

            {activeMeetings.map((m) => (
              <div key={m.id} className="dash-incident-card dash-incident-card--active">
                <div className="dic-top">
                  <div className="dic-title-row">
                    <SevBadge sev={m.incident_severity} />
                    <h3 className="dic-title">{m.title}</h3>
                    <StatusPill status={m.status} />
                  </div>
                  <div className="dic-meta">
                    <span>⏱ {formatDuration(m.started_at || m.created_at)}</span>
                    <span>👥 {m.participant_count || 0} participants</span>
                    {m.open_action_count > 0 && (
                      <span className="dic-alert-meta">
                        ⚡ {m.open_action_count} open actions
                      </span>
                    )}
                  </div>
                </div>
                <div className="dic-actions">
                  <button
                    className="dic-btn-join"
                    id={`join-${m.id}`}
                    onClick={() => navigate(`/room/${m.id}`)}
                  >
                    Join Room →
                  </button>
                  <Link to={`/meetings/${m.id}`} className="dic-btn-detail">
                    Details
                  </Link>
                </div>
              </div>
            ))}
          </section>

          {/* Recent Meetings */}
          <section className="dash-section">
            <div className="dash-section-hdr">
              <h2 className="dash-section-title">
                <span className="section-dot section-dot--purple" />
                Recent Incidents
              </h2>
              <Link to="/meetings" className="dash-section-link">
                View all →
              </Link>
            </div>

            {loading && (
              <div className="dash-empty">
                <div className="spinner" />
              </div>
            )}

            {!loading && meetings.length === 0 && (
              <div className="dash-empty">
                <p className="dash-empty-text">
                  No incidents yet.{" "}
                  <button
                    className="dash-inline-btn"
                    onClick={() => navigate("/new")}
                  >
                    Create your first incident room
                  </button>
                </p>
              </div>
            )}

            <div className="dash-meeting-list">
              {meetings.slice(0, 8).map((m) => (
                <Link
                  key={m.id}
                  to={`/meetings/${m.id}`}
                  className="dash-meeting-item"
                  id={`meeting-${m.id}`}
                >
                  <div className="dmi-left">
                    <div className="dmi-title-row">
                      <SevBadge sev={m.incident_severity} />
                      <span className="dmi-title">{m.title}</span>
                    </div>
                    <p className="dmi-summary">
                      {m.summary
                        ? m.summary.slice(0, 100) + (m.summary.length > 100 ? "…" : "")
                        : m.status === "active"
                        ? "Incident in progress…"
                        : "No summary yet."}
                    </p>
                  </div>
                  <div className="dmi-right">
                    <StatusPill status={m.status} />
                    <span className="dmi-date">{formatDate(m.ended_at || m.created_at)}</span>
                    {m.open_action_count > 0 && (
                      <span className="dmi-actions-badge">
                        {m.open_action_count} actions
                      </span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        </div>

        {/* Right column */}
        <div className="dash-col dash-col--side">
          {/* Quick Actions */}
          <section className="dash-section">
            <h2 className="dash-section-title">
              <span className="section-dot section-dot--blue" />
              Quick Actions
            </h2>
            <div className="dash-quick-actions">
              <button
                className="dash-quick-btn dash-quick-btn--primary"
                onClick={() => navigate("/new")}
                id="quick-new-btn"
              >
                <span className="dqb-icon">🚨</span>
                <div>
                  <div className="dqb-label">New Incident</div>
                  <div className="dqb-sub">Spin up a room instantly</div>
                </div>
              </button>
              <button
                className="dash-quick-btn"
                onClick={() => navigate("/meetings")}
                id="quick-history-btn"
              >
                <span className="dqb-icon">📚</span>
                <div>
                  <div className="dqb-label">Meeting History</div>
                  <div className="dqb-sub">Browse past incidents</div>
                </div>
              </button>
            </div>
          </section>

          {/* Incident Overview Stats */}
          {endedMeetings.length > 0 && (
            <section className="dash-section">
              <h2 className="dash-section-title">
                <span className="section-dot section-dot--green" />
                Overview
              </h2>
              <div className="dash-stats-grid">
                <div className="dash-stat">
                  <div className="dash-stat-value">{meetings.length}</div>
                  <div className="dash-stat-label">Total Incidents</div>
                </div>
                <div className="dash-stat">
                  <div className="dash-stat-value" style={{ color: "#ef4444" }}>
                    {activeMeetings.length}
                  </div>
                  <div className="dash-stat-label">Active Now</div>
                </div>
                <div className="dash-stat">
                  <div className="dash-stat-value" style={{ color: "#22c55e" }}>
                    {endedMeetings.length}
                  </div>
                  <div className="dash-stat-label">Resolved</div>
                </div>
                <div className="dash-stat">
                  <div className="dash-stat-value" style={{ color: "#f97316" }}>
                    {activeMeetings.reduce(
                      (s, m) => s + (m.open_action_count || 0),
                      0
                    )}
                  </div>
                  <div className="dash-stat-label">Open Actions</div>
                </div>
              </div>
            </section>
          )}

          {/* Recent ended meetings short list */}
          {endedMeetings.length > 0 && (
            <section className="dash-section">
              <h2 className="dash-section-title">
                <span className="section-dot section-dot--gray" />
                Resolved Incidents
              </h2>
              <div className="dash-resolved-list">
                {endedMeetings.slice(0, 5).map((m) => (
                  <Link
                    key={m.id}
                    to={`/meetings/${m.id}`}
                    className="dash-resolved-item"
                    id={`resolved-${m.id}`}
                  >
                    <div className="dri-title">{m.title}</div>
                    <div className="dri-meta">
                      <SevBadge sev={m.incident_severity} />
                      <span className="dri-date">{formatDate(m.ended_at)}</span>
                    </div>
                    {m.summary && (
                      <p className="dri-summary">
                        {m.summary.slice(0, 80)}
                        {m.summary.length > 80 ? "…" : ""}
                      </p>
                    )}
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
