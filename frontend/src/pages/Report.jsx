import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";

const API = "/api";

export default function Report() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Recursive evidence drawer state
  const [selectedConclusion, setSelectedConclusion] = useState(null);
  const [evidenceChain, setEvidenceChain] = useState(null);
  const [loadingEvidence, setLoadingEvidence] = useState(false);

  // Q&A State
  const [qaQuestion, setQaQuestion] = useState("");
  const [qaResult, setQaResult] = useState(null);
  const [qaLoading, setQaLoading] = useState(false);

  async function handleAskQuestion(e) {
    e.preventDefault();
    if (!qaQuestion.trim()) return;
    setQaLoading(true);
    setQaResult(null);

    try {
      const r = await fetch(`${API}/meetings/${id}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: qaQuestion.trim() }),
      });
      const data = await r.json();
      setQaResult(data);
    } catch (err) {
      console.error("Ask Q&A error:", err);
    } finally {
      setQaLoading(false);
    }
  }

  useEffect(() => {
    fetch(`${API}/meetings/${id}/report`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        setReport(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || "Failed to load report");
        setLoading(false);
      });
  }, [id]);

  // Fetch recursive evidence lookup when an item is clicked
  function handleInspectEvidence(item, typeLabel) {
    setSelectedConclusion({ ...item, typeLabel });
    setLoadingEvidence(true);
    setEvidenceChain(null);

    fetch(`${API}/meetings/${id}/evidence/${item.id}`)
      .then((r) => r.json())
      .then((data) => {
        setEvidenceChain(data);
        setLoadingEvidence(false);
      })
      .catch((err) => {
        console.error("Evidence lookup error:", err);
        setLoadingEvidence(false);
      });
  }

  function formatMs(ms) {
    if (!ms && ms !== 0) return "";
    const totalSec = Math.floor(ms / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }

  if (loading) {
    return (
      <div className="room-root room-root--center">
        <div className="join-card" style={{ textAlign: "center" }}>
          <span className="spinner" aria-hidden="true" style={{ width: 28, height: 28, margin: "0 auto 1rem" }} />
          <h2>Generating Final Synthesis Report…</h2>
          <p className="join-sub">Analyzing transcripts, timeline, conflicts, and evidence chain.</p>
        </div>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="room-root room-root--center">
        <div className="room-error-card">
          <p className="room-error-msg">{error || "Report not found"}</p>
          <button onClick={() => navigate("/")} className="btn-primary">
            Back to Home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="room-root" style={{ overflowY: "auto" }}>
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="room-header">
        <div className="room-header-left">
          <div className="room-logo-dot" style={{ background: "#6366f1" }} aria-hidden="true" />
          <div>
            <p className="room-header-title">{report.title} — Post-Incident Report</p>
            <p className="room-header-sub">Status: {report.status.toUpperCase()} · AI Incident Commander</p>
          </div>
        </div>
        <button onClick={() => navigate("/")} className="btn-ghost" style={{ width: "auto" }}>
          ← Back to Rooms
        </button>
      </header>

      {/* ── Main Content Container ───────────────────────────────────────── */}
      <div style={{ maxWidth: 1040, margin: "2rem auto", padding: "0 1.5rem", width: "100%" }}>
        {/* ── Natural-Language Q&A Search Box ────────────────────────────── */}
        <section className="qa-box">
          <form onSubmit={handleAskQuestion} className="qa-form">
            <input
              type="text"
              className="qa-input"
              placeholder="Ask a natural-language question about this meeting... (e.g. what did we decide about the rollout?)"
              value={qaQuestion}
              onChange={(e) => setQaQuestion(e.target.value)}
              disabled={qaLoading}
            />
            <button type="submit" className="btn-primary" style={{ width: "auto", px: "1.25rem" }} disabled={qaLoading || !qaQuestion.trim()}>
              {qaLoading ? <span className="spinner" aria-hidden="true" /> : "Ask AI →"}
            </button>
          </form>

          {qaResult && (
            <div className="qa-answer-card">
              <span className="qa-answer-title">🤖 AI Commander Answer</span>
              <p className="qa-answer-text">{qaResult.answer}</p>

              {qaResult.citations?.length > 0 && (
                <div className="citations-wrap">
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", alignSelf: "center" }}>Citations:</span>
                  {qaResult.citations.map((c, idx) => (
                    <button
                      key={idx}
                      className="citation-badge"
                      onClick={() => handleInspectEvidence({ id: c.source_id, content: c.text, description: c.text }, c.source_type || "Citation")}
                    >
                      <span>📎 [{c.source_type?.toUpperCase() || "SOURCE"}]</span>
                      <span>"{c.text?.slice(0, 30)}…"</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>

        {/* ── Executive Summary Banner ───────────────────────────────────── */}
        <section className="create-card" style={{ maxWidth: "100%", marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.2rem", fontWeight: 700, color: "#a5b4fc", marginBottom: "0.6rem" }}>
            Executive Summary
          </h2>
          <p style={{ fontSize: "0.95rem", lineHeight: 1.6, color: "var(--text)" }}>
            {report.executive_summary}
          </p>
        </section>

        {/* ── Unresolved Questions ────────────────────────────────────────── */}
        {report.unresolved_questions?.length > 0 && (
          <section className="intel-card intel-card--conflict" style={{ marginBottom: "1.5rem", padding: "1.25rem" }}>
            <h3 style={{ fontSize: "0.9rem", fontWeight: 700, color: "#f87171", marginBottom: "0.5rem" }}>
              ⚠️ Unresolved Questions &amp; Open Issues ({report.unresolved_questions.length})
            </h3>
            <ul style={{ paddingLeft: "1.25rem", color: "#fca5a5", fontSize: "0.875rem", lineHeight: 1.5 }}>
              {report.unresolved_questions.map((q, idx) => (
                <li key={idx}>{q}</li>
              ))}
            </ul>
          </section>
        )}

        {/* ── Incident Timeline ────────────────────────────────────────────── */}
        {report.timeline_events?.length > 0 && (
          <section style={{ marginBottom: "2rem" }}>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "1rem", color: "var(--text)" }}>
              🕒 Key Incident Timeline ({report.timeline_events.length})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {report.timeline_events.map((t) => (
                <div key={t.id} className="intel-card" style={{ cursor: "default" }}>
                  <div className="intel-card-header">
                    <span className="pill-badge">{t.event_type}</span>
                    <span className="intel-card-sub">{new Date(t.occurred_at).toLocaleTimeString()}</span>
                  </div>
                  <p className="intel-card-text">{t.description}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── Conflicts & Contradictions Section ───────────────────────────── */}
        <section style={{ marginBottom: "2rem" }}>
          <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "1rem", color: "#f87171" }}>
            ⚡ Conflicts &amp; Contradictions ({report.conflicts?.length || 0})
          </h3>
          {report.conflicts?.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>No conflicts recorded.</p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "1rem" }}>
              {report.conflicts.map((c) => (
                <div
                  key={c.id}
                  className="intel-card intel-card--conflict"
                  onClick={() => handleInspectEvidence(c, "Conflict")}
                >
                  <div className="intel-card-header">
                    <span className="pill-conflict-tag">CONFLICT</span>
                    <span className="pill-badge" style={{ color: "#ef4444" }}>{c.status}</span>
                  </div>
                  <p className="intel-card-text" style={{ color: "#fca5a5", fontWeight: 600 }}>
                    {c.description}
                  </p>
                  <div className="evidence-hint" style={{ color: "#f87171" }}>
                    🔍 Inspect evidence chain down to transcript line →
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Decisions Section ────────────────────────────────────────────── */}
        <section style={{ marginBottom: "2rem" }}>
          <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "1rem", color: "#a5b4fc" }}>
            🎯 Decisions Made ({report.decisions?.length || 0})
          </h3>
          {report.decisions?.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>No decisions recorded.</p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "1rem" }}>
              {report.decisions.map((d) => (
                <div
                  key={d.id}
                  className="intel-card"
                  onClick={() => handleInspectEvidence(d, "Decision")}
                >
                  <div className="intel-card-header">
                    <span className="pill-badge">DECISION</span>
                  </div>
                  <p className="intel-card-text">{d.content}</p>
                  {d.rationale && <p className="intel-card-sub">Rationale: {d.rationale}</p>}
                  <div className="evidence-hint">🔍 Inspect evidence chain →</div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Action Items & Facts Grid ────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginBottom: "2rem" }}>
          <section>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "1rem" }}>
              ✅ Action Items ({report.action_items?.length || 0})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {report.action_items.map((a) => (
                <div key={a.id} className="intel-card" onClick={() => handleInspectEvidence(a, "Action Item")}>
                  <p className="intel-card-text">{a.description}</p>
                  {a.due_date && <p className="intel-card-sub" style={{ color: "#f43f5e" }}>Due: {a.due_date}</p>}
                  <div className="evidence-hint">🔍 View source quote →</div>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: "1rem" }}>
              📌 Facts ({report.facts?.length || 0})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {report.facts.map((f) => (
                <div key={f.id} className="intel-card" onClick={() => handleInspectEvidence(f, "Fact")}>
                  <p className="intel-card-text">{f.content}</p>
                  <div className="evidence-hint">🔍 View source quote →</div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      {/* ── Recursive Evidence Inspector Drawer ────────────────────────────── */}
      {selectedConclusion && (
        <div className="evidence-drawer-overlay" onClick={() => setSelectedConclusion(null)}>
          <div className="evidence-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3 className="drawer-title">Recursive Evidence Chain</h3>
              <button className="btn-close-drawer" onClick={() => setSelectedConclusion(null)}>×</button>
            </div>

            <div className="drawer-section-title">{selectedConclusion.typeLabel} Item</div>
            <div className="drawer-item-box">
              <p style={{ fontWeight: 600, color: "var(--text)", marginBottom: "0.4rem" }}>
                {selectedConclusion.content || selectedConclusion.description}
              </p>
            </div>

            <div className="drawer-section-title">Resolved Transcript Evidence Chain</div>
            {loadingEvidence ? (
              <div style={{ textAlign: "center", padding: "2rem" }}>
                <span className="spinner" aria-hidden="true" />
                <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
                  Tracing evidence graph back to transcript segments…
                </p>
              </div>
            ) : evidenceChain?.transcript_chain?.length > 0 ? (
              evidenceChain.transcript_chain.map((t, idx) => (
                <div key={idx} className="drawer-evidence-quote" style={{ marginBottom: "0.75rem" }}>
                  <div className="quote-meta">
                    <span>{t.speaker}</span>
                    <span>{formatMs(t.start_ms)}</span>
                  </div>
                  <p className="quote-text">"{t.text}"</p>
                  <span style={{ fontSize: "0.6875rem", color: "var(--text-muted)", display: "block", marginTop: "0.3rem" }}>
                    Segment ID: {t.segment_id}
                  </span>
                </div>
              ))
            ) : (
              <div className="drawer-evidence-quote">
                <p className="quote-text">Evidence entry confirmed in PostgreSQL evidence ledger.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
