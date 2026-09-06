import { useState, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";

const API = "/api";

const SEV_COLORS = {
  "SEV-1": "#ef4444",
  "SEV-2": "#f97316",
  "SEV-3": "#eab308",
  "SEV-4": "#22c55e",
};

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
        color,
        border: `1px solid ${color}55`,
        letterSpacing: "0.04em",
        whiteSpace: "nowrap",
      }}
    >
      {sev}
    </span>
  );
}

function StatusPill({ status }) {
  const color =
    status === "active" ? "#22c55e" : status === "ended" ? "#64748b" : "#6366f1";
  return (
    <span
      style={{
        padding: "0.12rem 0.5rem",
        borderRadius: 999,
        fontSize: "0.7rem",
        fontWeight: 600,
        background: `${color}20`,
        color,
        border: `1px solid ${color}40`,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        whiteSpace: "nowrap",
      }}
    >
      {status}
    </span>
  );
}

export default function MeetingHistory() {
  const navigate = useNavigate();
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  useEffect(() => {
    fetch(`${API}/meetings?limit=100`)
      .then((r) => r.json())
      .then((d) => {
        setMeetings(d.meetings || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const filtered = meetings.filter((m) => {
    const matchStatus = statusFilter === "all" || m.status === statusFilter;
    const matchSearch =
      !search ||
      m.title.toLowerCase().includes(search.toLowerCase()) ||
      (m.summary || "").toLowerCase().includes(search.toLowerCase());
    return matchStatus && matchSearch;
  });

  function formatDate(d) {
    if (!d) return "—";
    return new Date(d).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatDuration(start, end) {
    if (!start) return "—";
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : Date.now();
    const diff = e - s;
    if (diff < 0) return "—";
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  return (
    <div className="hist-root">
      <div className="bg-grid" aria-hidden="true" />

      {/* Header */}
      <header className="hist-header">
        <div className="hist-header-left">
          <button className="hist-back-btn" onClick={() => navigate("/")}>
            ← Dashboard
          </button>
          <div>
            <h1 className="hist-title">Incident History</h1>
            <p className="hist-sub">
              {meetings.length} incidents recorded · SentinelAI
            </p>
          </div>
        </div>
        <button
          className="dash-new-btn"
          onClick={() => navigate("/new")}
          id="hist-new-btn"
        >
          + New Incident
        </button>
      </header>

      {/* Filters */}
      <div className="hist-filters">
        <input
          type="search"
          className="hist-search"
          placeholder="Search incidents…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          id="hist-search"
        />
        <div className="hist-filter-tabs">
          {["all", "active", "ended"].map((s) => (
            <button
              key={s}
              className={`hist-filter-tab ${statusFilter === s ? "hist-filter-tab--active" : ""}`}
              onClick={() => setStatusFilter(s)}
              id={`filter-${s}`}
            >
              {s.charAt(0).toUpperCase() + s.slice(1)}
              {s !== "all" && (
                <span className="hist-filter-count">
                  {meetings.filter((m) => m.status === s).length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="hist-body">
        {loading && (
          <div className="dash-empty">
            <div className="spinner" />
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="dash-empty">
            <p className="dash-empty-text">
              {search || statusFilter !== "all"
                ? "No incidents match your filters."
                : "No incidents yet."}
            </p>
            <button
              className="dash-start-btn"
              onClick={() => navigate("/new")}
              id="hist-start-btn"
            >
              Start First Incident →
            </button>
          </div>
        )}

        {filtered.map((m) => (
          <div key={m.id} className="hist-card" id={`hist-${m.id}`}>
            <div className="hist-card-top">
              <div className="hist-card-title-row">
                <SevBadge sev={m.incident_severity} />
                <h2 className="hist-card-title">{m.title}</h2>
                <StatusPill status={m.status} />
              </div>
              <div className="hist-card-meta">
                <span>📅 {formatDate(m.created_at)}</span>
                <span>⏱ {formatDuration(m.started_at || m.created_at, m.ended_at)}</span>
                <span>👥 {m.participant_count || 0} participants</span>
                {m.open_action_count > 0 && (
                  <span className="hist-card-meta-alert">
                    ⚡ {m.open_action_count} open actions
                  </span>
                )}
              </div>
            </div>

            {m.summary && (
              <p className="hist-card-summary">
                {m.summary.slice(0, 200)}
                {m.summary.length > 200 ? "…" : ""}
              </p>
            )}

            <div className="hist-card-footer">
              <div className="hist-card-tags">
                {m.root_cause_status && (
                  <span className="hist-tag">RC: {m.root_cause_status}</span>
                )}
                {m.resolution_status && (
                  <span className="hist-tag">Res: {m.resolution_status}</span>
                )}
              </div>
              <div className="hist-card-actions">
                {m.status === "active" && (
                  <button
                    className="dic-btn-join"
                    onClick={() => navigate(`/room/${m.id}`)}
                    id={`hist-join-${m.id}`}
                  >
                    Join Room →
                  </button>
                )}
                <Link
                  to={`/meetings/${m.id}`}
                  className="dic-btn-detail"
                  id={`hist-detail-${m.id}`}
                >
                  View Details
                </Link>
                {m.status === "ended" && (
                  <Link
                    to={`/meetings/${m.id}/report`}
                    className="dic-btn-detail"
                    id={`hist-report-${m.id}`}
                  >
                    Report
                  </Link>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
