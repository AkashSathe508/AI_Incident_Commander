import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";

const API = "/api";

const NOTE_CATEGORIES = [
  "observation",
  "decision",
  "follow_up",
  "unresolved",
  "technical",
  "lesson_learned",
];

const NOTE_CAT_COLORS = {
  observation: "#6366f1",
  decision: "#22c55e",
  follow_up: "#3b82f6",
  unresolved: "#ef4444",
  technical: "#8b5cf6",
  lesson_learned: "#f59e0b",
};

const RESPONSE_TYPE_COLOR = {
  analysis: "#6366f1",
  recommendation: "#22c55e",
  summary: "#3b82f6",
  alert: "#ef4444",
};

function SectionHeader({ title, count, accent }) {
  return (
    <div className="detail-section-hdr">
      <h3 className="detail-section-title" style={{ "--accent": accent || "#6366f1" }}>
        <span className="detail-section-dot" />
        {title}
      </h3>
      {count !== undefined && <span className="detail-section-badge">{count}</span>}
    </div>
  );
}

function EmptyState({ msg }) {
  return <p className="detail-empty">{msg}</p>;
}

export default function MeetingDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [report, setReport] = useState(null);
  const [aiResponses, setAiResponses] = useState([]);
  const [notes, setNotes] = useState([]);
  const [context, setContext] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("overview");

  // Notes form
  const [noteContent, setNoteContent] = useState("");
  const [noteCategory, setNoteCategory] = useState("observation");
  const [noteAuthor, setNoteAuthor] = useState("Incident Commander");
  const [savingNote, setSavingNote] = useState(false);
  const [editingNote, setEditingNote] = useState(null);
  const noteInputRef = useRef(null);

  useEffect(() => {
    Promise.all([
      fetch(`${API}/meetings/${id}/report`).then((r) => r.ok ? r.json() : null),
      fetch(`${API}/meetings/${id}/ai-responses`).then((r) => r.ok ? r.json() : { ai_responses: [] }),
      fetch(`${API}/meetings/${id}/notes`).then((r) => r.ok ? r.json() : { notes: [] }),
      fetch(`${API}/meetings/${id}/context`).then((r) => r.ok ? r.json() : null),
      fetch(`${API}/meetings/${id}/approvals`).then((r) => r.ok ? r.json() : { approvals: [] }),
    ])
      .then(([rep, aiR, notesR, ctx, appR]) => {
        setReport(rep);
        setAiResponses(aiR.ai_responses || []);
        setNotes(notesR.notes || []);
        setContext(ctx);
        setApprovals(appR.approvals || []);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message || "Failed to load meeting");
        setLoading(false);
      });
  }, [id]);

  async function saveNote(e) {
    e.preventDefault();
    if (!noteContent.trim()) return;
    setSavingNote(true);
    try {
      if (editingNote) {
        const r = await fetch(`${API}/meetings/${id}/notes/${editingNote.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: noteContent, category: noteCategory }),
        });
        if (r.ok) {
          const updated = await r.json();
          setNotes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
          setEditingNote(null);
          setNoteContent("");
        }
      } else {
        const r = await fetch(`${API}/meetings/${id}/notes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content: noteContent,
            category: noteCategory,
            author_name: noteAuthor,
          }),
        });
        if (r.ok) {
          const newNote = await r.json();
          setNotes((prev) => [...prev, newNote]);
          setNoteContent("");
        }
      }
    } finally {
      setSavingNote(false);
    }
  }

  async function deleteNote(noteId) {
    await fetch(`${API}/meetings/${id}/notes/${noteId}`, { method: "DELETE" });
    setNotes((prev) => prev.filter((n) => n.id !== noteId));
  }

  function startEditNote(note) {
    setEditingNote(note);
    setNoteContent(note.content);
    setNoteCategory(note.category);
    noteInputRef.current?.focus();
  }

  function formatDate(d) {
    if (!d) return "—";
    return new Date(d).toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
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

  if (loading) {
    return (
      <div className="room-root room-root--center">
        <div className="spinner" style={{ width: 32, height: 32 }} />
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="room-root room-root--center">
        <div className="room-error-card">
          <p className="room-error-msg">{error || "Meeting not found"}</p>
          <button onClick={() => navigate("/")} className="btn-primary">
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "ai_commander", label: `AI Commander (${aiResponses.length})` },
    { id: "intelligence", label: "Intelligence" },
    { id: "actions", label: `Actions (${report.action_items?.length || 0})` },
    { id: "notes", label: `Notes (${notes.length})` },
    { id: "timeline", label: "Timeline" },
    { id: "approvals", label: `Approvals (${approvals.length})` },
  ];

  return (
    <div className="detail-root">
      <div className="bg-grid" aria-hidden="true" />

      {/* Header */}
      <header className="detail-header">
        <div className="detail-header-left">
          <button className="hist-back-btn" onClick={() => navigate("/meetings")}>
            ← History
          </button>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
              <h1 className="detail-title">{report.title}</h1>
              <span
                style={{
                  padding: "0.12rem 0.5rem",
                  borderRadius: 999,
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  background: report.status === "active" ? "#22c55e20" : "#64748b20",
                  color: report.status === "active" ? "#22c55e" : "#64748b",
                  border: `1px solid ${report.status === "active" ? "#22c55e40" : "#64748b40"}`,
                  textTransform: "uppercase",
                }}
              >
                {report.status}
              </span>
              {report.incident_severity && (
                <span
                  style={{
                    padding: "0.12rem 0.5rem",
                    borderRadius: 6,
                    fontSize: "0.7rem",
                    fontWeight: 700,
                    background: "#ef444422",
                    color: "#ef4444",
                    border: "1px solid #ef444440",
                  }}
                >
                  {report.incident_severity}
                </span>
              )}
            </div>
            <p className="detail-meta-bar">
              {formatDate(report.created_at)} ·
              Duration: {formatDuration(report.created_at, report.ended_at)} ·
              {report.facts?.length || 0} facts ·
              {report.decisions?.length || 0} decisions
            </p>
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {report.status === "active" && (
            <button
              className="dic-btn-join"
              onClick={() => navigate(`/room/${id}`)}
              id="detail-join-btn"
            >
              Join Room →
            </button>
          )}
          {report.status === "ended" && (
            <Link to={`/meetings/${id}/report`} className="dic-btn-detail" id="detail-report-btn">
              Full Report
            </Link>
          )}
        </div>
      </header>

      {/* Previous Context Banner */}
      {context?.has_context && (
        <div className="detail-context-banner">
          <span className="context-banner-icon">🔗</span>
          <div>
            <strong>Cross-incident context found:</strong>{" "}
            {context.related_meetings?.length} related previous meeting
            {context.related_meetings?.length !== 1 ? "s" : ""} — data is
            available to inform this incident.
          </div>
          <button
            className="context-banner-btn"
            onClick={() => setActiveTab("overview")}
          >
            View Context
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="detail-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`detail-tab ${activeTab === t.id ? "detail-tab--active" : ""}`}
            onClick={() => setActiveTab(t.id)}
            id={`tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="detail-body">
        {/* ── Overview ─────────────────────────────── */}
        {activeTab === "overview" && (
          <div className="detail-tab-content">
            {/* AI Summary */}
            {report.executive_summary && (
              <div className="detail-section">
                <SectionHeader title="AI-Generated Summary" accent="#6366f1" />
                <div className="detail-summary-card">
                  <pre className="detail-summary-text">{report.executive_summary}</pre>
                </div>
              </div>
            )}

            {/* Stats row */}
            <div className="detail-stats-row">
              {[
                { label: "Facts", value: report.facts?.length || 0, color: "#6366f1" },
                { label: "Hypotheses", value: report.assumptions?.length || 0, color: "#8b5cf6" },
                { label: "Decisions", value: report.decisions?.length || 0, color: "#22c55e" },
                { label: "Actions", value: report.action_items?.length || 0, color: "#f59e0b" },
                { label: "Conflicts", value: report.conflicts?.length || 0, color: "#ef4444" },
                { label: "Risks", value: report.risks?.length || 0, color: "#f97316" },
              ].map((s) => (
                <div key={s.label} className="detail-stat-chip" style={{ "--chip-accent": s.color }}>
                  <span className="dsc-value">{s.value}</span>
                  <span className="dsc-label">{s.label}</span>
                </div>
              ))}
            </div>

            {/* Cross-meeting context */}
            {context?.has_context && context.related_meetings?.length > 0 && (
              <div className="detail-section">
                <SectionHeader
                  title="Previous Incident Context"
                  count={context.related_meetings.length}
                  accent="#3b82f6"
                />
                {context.related_meetings.map((m) => (
                  <div key={m.id} className="detail-context-card">
                    <div className="dcc-title">{m.title}</div>
                    <div className="dcc-date">{m.ended_at ? new Date(m.ended_at).toLocaleDateString() : "Unknown date"}</div>
                    {m.summary && <p className="dcc-summary">{m.summary.slice(0, 200)}</p>}
                    {m.key_decisions?.length > 0 && (
                      <div className="dcc-section">
                        <strong>Decisions:</strong>
                        <ul>
                          {m.key_decisions.slice(0, 3).map((d, i) => <li key={i}>{d}</li>)}
                        </ul>
                      </div>
                    )}
                    {m.unresolved_conflicts?.length > 0 && (
                      <div className="dcc-section dcc-section--warn">
                        <strong>Unresolved:</strong>{" "}
                        {m.unresolved_conflicts.slice(0, 2).join("; ")}
                      </div>
                    )}
                    {m.open_actions?.length > 0 && (
                      <div className="dcc-section dcc-section--action">
                        <strong>Open actions:</strong>{" "}
                        {m.open_actions.slice(0, 2).join("; ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Conflicts */}
            {report.conflicts?.length > 0 && (
              <div className="detail-section">
                <SectionHeader
                  title="Conflicts"
                  count={report.conflicts.length}
                  accent="#ef4444"
                />
                {report.conflicts.map((c) => (
                  <div key={c.id} className="detail-conflict-card">
                    <span
                      className="conflict-status"
                      style={{ color: c.status === "open" ? "#ef4444" : "#22c55e" }}
                    >
                      [{c.status.toUpperCase()}]
                    </span>{" "}
                    {c.description}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── AI Commander ─────────────────────────── */}
        {activeTab === "ai_commander" && (
          <div className="detail-tab-content">
            <SectionHeader
              title="AI Incident Commander — Response History"
              count={aiResponses.length}
              accent="#6366f1"
            />
            {aiResponses.length === 0 && (
              <EmptyState msg="No AI responses yet. AI responses are generated when transcript is analyzed." />
            )}
            <div className="detail-ai-list">
              {aiResponses.map((r) => {
                const typeColor = RESPONSE_TYPE_COLOR[r.response_type] || "#6366f1";
                return (
                  <div key={r.id} className="detail-ai-card" id={`ai-resp-${r.id}`}>
                    <div className="ai-card-header">
                      <span
                        className="ai-type-badge"
                        style={{ background: `${typeColor}20`, color: typeColor, border: `1px solid ${typeColor}40` }}
                      >
                        {r.response_type}
                      </span>
                      {r.trigger && (
                        <span className="ai-trigger-badge">⚡ {r.trigger.replace(/_/g, " ")}</span>
                      )}
                      {r.approval_status !== "none" && (
                        <span
                          className="ai-approval-badge"
                          style={{
                            background:
                              r.approval_status === "approved"
                                ? "#22c55e20"
                                : r.approval_status === "rejected"
                                ? "#ef444420"
                                : "#f59e0b20",
                            color:
                              r.approval_status === "approved"
                                ? "#22c55e"
                                : r.approval_status === "rejected"
                                ? "#ef4444"
                                : "#f59e0b",
                          }}
                        >
                          {r.approval_status}
                        </span>
                      )}
                      {r.confidence && (
                        <span className="ai-confidence">
                          {Math.round(r.confidence * 100)}% confidence
                        </span>
                      )}
                      <span className="ai-ts">{formatDate(r.created_at)}</span>
                    </div>
                    <p className="ai-response-text">{r.response_text}</p>
                    {r.jira_ticket_ref && (
                      <div className="ai-jira-ref">
                        🎫 Jira: <strong>{r.jira_ticket_ref}</strong>
                      </div>
                    )}
                    {(r.related_fact_ids?.length > 0 ||
                      r.related_assumption_ids?.length > 0 ||
                      r.related_action_ids?.length > 0) && (
                      <div className="ai-related">
                        {r.related_fact_ids?.length > 0 && (
                          <span className="ai-related-chip">
                            📌 {r.related_fact_ids.length} facts
                          </span>
                        )}
                        {r.related_assumption_ids?.length > 0 && (
                          <span className="ai-related-chip">
                            💭 {r.related_assumption_ids.length} hypotheses
                          </span>
                        )}
                        {r.related_action_ids?.length > 0 && (
                          <span className="ai-related-chip">
                            ✅ {r.related_action_ids.length} actions
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Intelligence ─────────────────────────── */}
        {activeTab === "intelligence" && (
          <div className="detail-tab-content detail-two-col">
            <div>
              <SectionHeader title="Facts" count={report.facts?.length} accent="#6366f1" />
              {report.facts?.length === 0 && <EmptyState msg="No facts extracted." />}
              {report.facts?.map((f) => (
                <div key={f.id} className="detail-fact-card">
                  {f.confidence && (
                    <span className="fact-confidence">
                      {Math.round(f.confidence * 100)}%
                    </span>
                  )}
                  {f.content}
                </div>
              ))}
            </div>
            <div>
              <SectionHeader title="Hypotheses" count={report.assumptions?.length} accent="#8b5cf6" />
              {report.assumptions?.length === 0 && <EmptyState msg="No hypotheses detected." />}
              {report.assumptions?.map((a) => (
                <div key={a.id} className="detail-assumption-card">
                  <span
                    className="assumption-status"
                    style={{
                      color:
                        a.status === "confirmed"
                          ? "#22c55e"
                          : a.status === "rejected"
                          ? "#ef4444"
                          : "#f59e0b",
                    }}
                  >
                    [{(a.status || "pending").toUpperCase()}]
                  </span>{" "}
                  {a.content}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Actions ──────────────────────────────── */}
        {activeTab === "actions" && (
          <div className="detail-tab-content">
            <SectionHeader
              title="Action Items"
              count={report.action_items?.length}
              accent="#f59e0b"
            />
            {report.action_items?.length === 0 && <EmptyState msg="No action items." />}
            <div className="detail-action-list">
              {report.action_items?.map((a) => (
                <div key={a.id} className="detail-action-card">
                  <div className="dac-header">
                    <span
                      className="dac-status"
                      style={{
                        background:
                          a.status === "completed"
                            ? "#22c55e20"
                            : a.status === "open"
                            ? "#f59e0b20"
                            : "#64748b20",
                        color:
                          a.status === "completed"
                            ? "#22c55e"
                            : a.status === "open"
                            ? "#f59e0b"
                            : "#64748b",
                      }}
                    >
                      {a.status}
                    </span>
                    {a.due_date && (
                      <span className="dac-due">📅 {a.due_date}</span>
                    )}
                  </div>
                  <p className="dac-desc">{a.description}</p>
                </div>
              ))}
            </div>

            {/* Decisions */}
            {report.decisions?.length > 0 && (
              <>
                <SectionHeader
                  title="Decisions Made"
                  count={report.decisions?.length}
                  accent="#22c55e"
                />
                {report.decisions.map((d) => (
                  <div key={d.id} className="detail-decision-card">
                    <p className="ddc-content">{d.content}</p>
                    {d.rationale && (
                      <p className="ddc-rationale">💡 {d.rationale}</p>
                    )}
                  </div>
                ))}
              </>
            )}

            {/* Risks */}
            {report.risks?.length > 0 && (
              <>
                <SectionHeader
                  title="Identified Risks"
                  count={report.risks?.length}
                  accent="#f97316"
                />
                {report.risks.map((r) => (
                  <div key={r.id} className="detail-risk-card">
                    <span className="risk-sev" style={{ color: r.severity === "critical" ? "#ef4444" : "#f97316" }}>
                      [{r.severity?.toUpperCase()}]
                    </span>{" "}
                    {r.description}
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {/* ── Notes ────────────────────────────────── */}
        {activeTab === "notes" && (
          <div className="detail-tab-content">
            <SectionHeader title="Meeting Notes" count={notes.length} accent="#3b82f6" />

            {/* Note form */}
            <form onSubmit={saveNote} className="note-form">
              <div className="note-form-row">
                <input
                  type="text"
                  className="field-input"
                  placeholder="Your name"
                  value={noteAuthor}
                  onChange={(e) => setNoteAuthor(e.target.value)}
                  style={{ width: "160px", flexShrink: 0 }}
                  disabled={!!editingNote}
                />
                <select
                  className="field-input note-cat-select"
                  value={noteCategory}
                  onChange={(e) => setNoteCategory(e.target.value)}
                >
                  {NOTE_CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </div>
              <textarea
                ref={noteInputRef}
                className="field-input note-textarea"
                placeholder="Add a note… (observation, decision, follow-up, unresolved question…)"
                value={noteContent}
                onChange={(e) => setNoteContent(e.target.value)}
                rows={3}
                id="note-input"
              />
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button
                  type="submit"
                  className="btn-primary"
                  style={{ width: "auto", padding: "0.5rem 1.5rem" }}
                  disabled={savingNote || !noteContent.trim()}
                  id="note-save-btn"
                >
                  {savingNote ? <span className="spinner" /> : editingNote ? "Update Note" : "Add Note"}
                </button>
                {editingNote && (
                  <button
                    type="button"
                    className="dic-btn-detail"
                    onClick={() => {
                      setEditingNote(null);
                      setNoteContent("");
                    }}
                  >
                    Cancel
                  </button>
                )}
              </div>
            </form>

            {/* Notes list */}
            {notes.length === 0 && <EmptyState msg="No notes yet. Add your first observation." />}
            <div className="detail-notes-list">
              {notes.map((n) => {
                const catColor = NOTE_CAT_COLORS[n.category] || "#6366f1";
                return (
                  <div key={n.id} className="detail-note-card" id={`note-${n.id}`}>
                    <div className="dnc-header">
                      <span
                        className="dnc-category"
                        style={{
                          background: `${catColor}20`,
                          color: catColor,
                          border: `1px solid ${catColor}40`,
                        }}
                      >
                        {n.category.replace(/_/g, " ")}
                      </span>
                      <span className="dnc-author">{n.author_name}</span>
                      <span className="dnc-date">{formatDate(n.created_at)}</span>
                      <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem" }}>
                        <button
                          className="dnc-edit-btn"
                          onClick={() => startEditNote(n)}
                          id={`edit-note-${n.id}`}
                        >
                          Edit
                        </button>
                        <button
                          className="dnc-del-btn"
                          onClick={() => deleteNote(n.id)}
                          id={`del-note-${n.id}`}
                        >
                          ×
                        </button>
                      </div>
                    </div>
                    <p className="dnc-content">{n.content}</p>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Timeline ─────────────────────────────── */}
        {activeTab === "timeline" && (
          <div className="detail-tab-content">
            <SectionHeader
              title="Incident Timeline"
              count={report.timeline_events?.length}
              accent="#6366f1"
            />
            {report.timeline_events?.length === 0 && (
              <EmptyState msg="No timeline events recorded." />
            )}
            <div className="detail-timeline">
              {report.timeline_events?.map((t, idx) => (
                <div key={t.id} className="timeline-event" id={`te-${t.id}`}>
                  <div className="timeline-event-dot" />
                  {idx < report.timeline_events.length - 1 && (
                    <div className="timeline-event-line" />
                  )}
                  <div className="timeline-event-body">
                    <div className="te-time">{formatDate(t.occurred_at)}</div>
                    <div className="te-type">{t.event_type.replace(/_/g, " ")}</div>
                    <p className="te-desc">{t.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Approvals ────────────────────────────── */}
        {activeTab === "approvals" && (
          <div className="detail-tab-content">
            <SectionHeader
              title="Pending & Resolved Approvals"
              count={approvals.length}
              accent="#f59e0b"
            />
            {approvals.length === 0 && <EmptyState msg="No approvals for this meeting." />}
            {approvals.map((a) => (
              <div key={a.id} className="detail-approval-card" id={`appr-${a.id}`}>
                <div className="dappr-header">
                  <span className="dappr-type">{a.action_type?.replace(/_/g, " ").toUpperCase()}</span>
                  <span
                    className="dappr-status"
                    style={{
                      color:
                        a.status === "approved"
                          ? "#22c55e"
                          : a.status === "rejected"
                          ? "#ef4444"
                          : "#f59e0b",
                    }}
                  >
                    {a.status}
                  </span>
                </div>
                <h4 className="dappr-title">{a.title}</h4>
                {a.description && <p className="dappr-desc">{a.description}</p>}
                {a.execution_result && (
                  <pre className="dappr-result">
                    {JSON.stringify(a.execution_result, null, 2)}
                  </pre>
                )}
                {a.resolved_at && (
                  <p className="dappr-resolved">Resolved: {formatDate(a.resolved_at)}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
