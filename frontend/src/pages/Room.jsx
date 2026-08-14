import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import AgoraRTC from "agora-rtc-sdk-ng";
import { avatarColor, getInitials } from "./CreateRoom";

// Suppress verbose Agora SDK logs in the browser console
AgoraRTC.setLogLevel(3); // 3 = WARN

const API = "/api";

export default function Room() {
  const { id } = useParams();
  const navigate = useNavigate();

  // ── Meeting metadata ─────────────────────────────────────────────────────
  const [meeting, setMeeting] = useState(null);
  const [metaError, setMetaError] = useState("");

  // ── Join flow ────────────────────────────────────────────────────────────
  const [phase, setPhase] = useState("prompt"); // prompt | joining | live | error
  const [displayName, setDisplayName] = useState("");
  const [joinError, setJoinError] = useState("");

  // ── Participant state ─────────────────────────────────────────────────────
  const [participants, setParticipants] = useState([]);
  const [speakingUids, setSpeakingUids] = useState(new Set());

  // ── Real-time Feed & Intelligence States ──────────────────────────────────
  const [activeTab, setActiveTab] = useState("transcript"); // transcript | facts | assumptions | decisions | actions | conflicts
  const [transcriptSegments, setTranscriptSegments] = useState([]);
  const [facts, setFacts] = useState([]);
  const [assumptions, setAssumptions] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [actionItems, setActionItems] = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [evidenceList, setEvidenceList] = useState([]);

  // ── Evidence Inspector Drawer State ───────────────────────────────────────
  const [selectedItemForEvidence, setSelectedItemForEvidence] = useState(null);

  // ── Q&A State ─────────────────────────────────────────────────────────────
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

  // ── Mic state ────────────────────────────────────────────────────────────
  const [micMuted, setMicMuted] = useState(false);

  // ── Agora refs ───────────────────────────────────────────────────────────
  const clientRef = useRef(null);
  const micTrackRef = useRef(null);
  const localUidRef = useRef(null);
  const transcriptFeedRef = useRef(null);

  // ── Load meeting metadata ─────────────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/meetings/${id}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setMeeting)
      .catch(() => setMetaError("Meeting not found or server unavailable."));
  }, [id]);

  // ── Approvals State ──────────────────────────────────────────────────────
  const [approvals, setApprovals] = useState([]);

  function fetchApprovals() {
    fetch(`${API}/meetings/${id}/approvals`)
      .then((r) => r.json())
      .then((data) => setApprovals(data.approvals || []))
      .catch((err) => console.warn("Failed to fetch approvals:", err));
  }

  useEffect(() => {
    if (phase === "live") fetchApprovals();
  }, [id, phase]);

  async function handleApproveAction(apprId) {
    try {
      const res = await fetch(`${API}/meetings/${id}/approvals/${apprId}/approve`, { method: "POST" });
      const data = await res.json();
      setApprovals((prev) => prev.map((a) => (a.id === apprId ? { ...a, status: "approved", result: data.execution_result } : a)));
    } catch (err) {
      console.error("Approve action error:", err);
    }
  }

  async function handleRejectAction(apprId) {
    try {
      await fetch(`${API}/meetings/${id}/approvals/${apprId}/reject`, { method: "POST" });
      setApprovals((prev) => prev.map((a) => (a.id === apprId ? { ...a, status: "rejected" } : a)));
    } catch (err) {
      console.error("Reject action error:", err);
    }
  }

  // ── WebSocket live transcript & intelligence stream ───────────────────────
  useEffect(() => {
    if (phase !== "live") return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/meetings/${id}/live`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[WS] Connected to live incident intelligence stream for meeting", id);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "full_history") {
          setTranscriptSegments(data.segments || []);
          setFacts(data.facts || []);
          setAssumptions(data.assumptions || []);
          setDecisions(data.decisions || []);
          setActionItems(data.action_items || []);
          setConflicts(data.conflicts || []);
          setEvidenceList(data.evidence || []);
        } else if (data.type === "transcript_segment") {
          setTranscriptSegments((prev) => {
            if (prev.some((s) => s.id === data.id || (s.start_ms === data.start_ms && s.text === data.text))) return prev;
            return [...prev, data];
          });
        } else if (data.type === "fact_created" && data.item) {
          setFacts((prev) => (prev.some((f) => f.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "assumption_created" && data.item) {
          setAssumptions((prev) => (prev.some((a) => a.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "decision_created" && data.item) {
          setDecisions((prev) => (prev.some((d) => d.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "action_item_created" && data.item) {
          setActionItems((prev) => (prev.some((ai) => ai.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "conflict_created" && data.item) {
          setConflicts((prev) => (prev.some((c) => c.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "pending_approval_created" && data.approval) {
          setApprovals((prev) => [data.approval, ...prev.filter((a) => a.id !== data.approval.id)]);
        } else if (data.type === "approval_updated" && data.approval) {
          setApprovals((prev) => prev.map((a) => (a.id === data.approval.id ? { ...a, ...data.approval } : a)));
        }
      } catch (err) {
        console.error("[WS] Error parsing websocket message:", err);
      }
    };

    ws.onerror = (err) => console.warn("[WS] Websocket error:", err);
    ws.onclose = () => console.log("[WS] Live intelligence websocket closed");

    return () => ws.close();
  }, [id, phase]);

  // ── Auto-scroll transcript feed ───────────────────────────────────────────
  useEffect(() => {
    if (activeTab === "transcript" && transcriptFeedRef.current) {
      transcriptFeedRef.current.scrollTop = transcriptFeedRef.current.scrollHeight;
    }
  }, [transcriptSegments, activeTab]);

  // ── Cleanup on unmount ────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      micTrackRef.current?.close();
      clientRef.current?.leave().catch(() => {});
    };
  }, []);

  // ── Join room ─────────────────────────────────────────────────────────────
  async function joinRoom(e) {
    e.preventDefault();
    if (!displayName.trim()) return;
    setPhase("joining");
    setJoinError("");

    try {
      const res = await fetch(`${API}/meetings/${id}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Server error ${res.status}`);
      }
      const { token, channel_name, uid, app_id } = await res.json();

      const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      clientRef.current = client;
      localUidRef.current = uid;

      client.on("user-published", async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === "audio") user.audioTrack.play();
        setParticipants((prev) =>
          prev.find((p) => p.uid === user.uid)
            ? prev
            : [...prev, { uid: user.uid, name: `Guest-${String(user.uid).slice(-4)}`, isSelf: false }]
        );
      });

      client.on("user-unpublished", (user, mediaType) => {
        if (mediaType === "audio") user.audioTrack?.stop();
      });

      client.on("user-left", (user) => {
        setParticipants((prev) => prev.filter((p) => p.uid !== user.uid));
        setSpeakingUids((prev) => {
          const next = new Set(prev);
          next.delete(user.uid);
          return next;
        });
      });

      client.enableAudioVolumeIndicator();
      client.on("volume-indicator", (volumes) => {
        const active = new Set(volumes.filter((v) => v.level > 8).map((v) => v.uid));
        setSpeakingUids(active);
      });

      await client.join(app_id, channel_name, token ?? null, uid);

      const micTrack = await AgoraRTC.createMicrophoneAudioTrack();
      micTrackRef.current = micTrack;
      await client.publish([micTrack]);

      setParticipants([{ uid, name: displayName.trim(), isSelf: true }]);
      setPhase("live");
    } catch (err) {
      console.error("Join error:", err);
      setJoinError(err.message || "Failed to join. Check console for details.");
      setPhase("prompt");
      micTrackRef.current?.close();
      micTrackRef.current = null;
      await clientRef.current?.leave().catch(() => {});
      clientRef.current = null;
    }
  }

  // ── End meeting ───────────────────────────────────────────────────────────
  async function endMeeting() {
    try {
      await fetch(`${API}/meetings/${id}/end`, { method: "POST" });
    } catch (err) {
      console.warn("End meeting error:", err);
    }
    micTrackRef.current?.close();
    micTrackRef.current = null;
    await clientRef.current?.leave().catch(() => {});
    clientRef.current = null;
    navigate(`/room/${id}/report`);
  }

  // ── Leave room ────────────────────────────────────────────────────────────
  async function leaveRoom() {
    micTrackRef.current?.close();
    micTrackRef.current = null;
    await clientRef.current?.leave().catch(() => {});
    clientRef.current = null;
    navigate("/");
  }

  // ── Toggle mic ────────────────────────────────────────────────────────────
  async function toggleMic() {
    if (!micTrackRef.current) return;
    const next = !micMuted;
    await micTrackRef.current.setMuted(next);
    setMicMuted(next);
  }

  // ── Format helpers ────────────────────────────────────────────────────────
  function formatMs(ms) {
    if (!ms && ms !== 0) return "";
    const totalSec = Math.floor(ms / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }

  // Find evidence for selected item
  function getLinkedEvidence(item) {
    if (!item) return [];
    const itemId = item.id || item.source_segment_id;
    return evidenceList.filter((e) => e.source_id === itemId || e.content?.includes(item.content || item.description));
  }

  function getMatchingSegment(item) {
    const segId = item.source_segment_id || item.source_id;
    if (segId) {
      const match = transcriptSegments.find((s) => s.id === segId);
      if (match) return match;
    }
    // Fallback: return latest or matching text
    return transcriptSegments.find((s) => s.text?.includes(item.content || item.description)) || transcriptSegments[0];
  }

  if (metaError) {
    return (
      <div className="room-root room-root--center">
        <div className="room-error-card">
          <p className="room-error-msg">{metaError}</p>
          <button onClick={() => navigate("/")} className="btn-primary">
            Back to home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="room-root">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="room-header">
        <div className="room-header-left">
          <div className="room-logo-dot" aria-hidden="true" />
          <div>
            <p className="room-header-title">{meeting?.title ?? "Loading…"}</p>
            <p className="room-header-sub">
              {phase === "live"
                ? `${participants.length} participant${participants.length !== 1 ? "s" : ""} · Live`
                : "Incident Commander"}
            </p>
          </div>
        </div>
        {phase === "live" && (
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button onClick={endMeeting} className="btn-leave" id="end-btn" style={{ background: "rgba(99,102,241,0.2)", color: "#a5b4fc", borderColor: "rgba(99,102,241,0.4)" }}>
              End Meeting &amp; Report →
            </button>
            <button onClick={leaveRoom} className="btn-leave" id="leave-btn">
              Leave
            </button>
          </div>
        )}
      </header>

      {/* ── Main Body ────────────────────────────────────────────────────── */}
      <div className="room-body">
        <main className="room-main">
          {/* Join prompt modal */}
          {(phase === "prompt" || phase === "joining") && (
            <div className="join-overlay" role="dialog" aria-modal="true" aria-label="Join room">
              <div className="join-card">
                <h2 className="join-heading">Join the room</h2>
                <p className="join-sub">{meeting ? `"${meeting.title}"` : "Connecting to meeting…"}</p>

                <form onSubmit={joinRoom} className="join-form">
                  <label htmlFor="display-name" className="field-label">
                    Your name
                  </label>
                  <input
                    id="display-name"
                    type="text"
                    placeholder="e.g. Alex Chen"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    className="field-input"
                    maxLength={100}
                    autoFocus
                    disabled={phase === "joining"}
                  />

                  {joinError && <p className="error-msg">{joinError}</p>}

                  <button
                    type="submit"
                    className="btn-primary"
                    id="join-btn"
                    disabled={phase === "joining" || !displayName.trim()}
                  >
                    {phase === "joining" ? (
                      <>
                        <span className="spinner" aria-hidden="true" />
                        Connecting…
                      </>
                    ) : (
                      "Join with Microphone →"
                    )}
                  </button>
                </form>
              </div>
            </div>
          )}

          {/* Live participant grid */}
          {phase === "live" && (
            <div className="participant-grid">
              {participants.map((p) => {
                const isSpeaking = speakingUids.has(p.uid);
                const color = avatarColor(p.uid);
                return (
                  <div
                    key={p.uid}
                    className={`participant-card ${isSpeaking ? "participant-card--speaking" : ""}`}
                    style={{ "--p-color": color }}
                  >
                    <div className="participant-avatar" style={{ background: color }} aria-hidden="true">
                      {getInitials(p.name)}
                      {isSpeaking && <span className="speaking-rings" aria-hidden="true" />}
                    </div>
                    <p className="participant-name">{p.name}</p>
                    {p.isSelf && <span className="participant-you-badge">You</span>}
                    {micMuted && p.isSelf && <span className="participant-muted-badge">Muted</span>}
                  </div>
                );
              })}
            </div>
          )}
        </main>

        {/* ── Live Intelligence & Transcript Sidebar ─────────────────────── */}
        {phase === "live" && (
          <aside className="transcript-panel" aria-label="Live Intelligence Panel">
            <div className="transcript-header">
              <h3 className="transcript-title">
                <span>AI Intelligence</span>
                <span className="transcript-badge">LangGraph</span>
              </h3>
            </div>

            {/* Q&A Ask Box */}
            <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid var(--border)" }}>
              <form onSubmit={handleAskQuestion} style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  type="text"
                  className="qa-input"
                  style={{ padding: "0.45rem 0.75rem", fontSize: "0.8125rem" }}
                  placeholder="Ask AI about this call..."
                  value={qaQuestion}
                  onChange={(e) => setQaQuestion(e.target.value)}
                  disabled={qaLoading}
                />
                <button type="submit" className="btn-primary" style={{ width: "auto", padding: "0.45rem 0.85rem", fontSize: "0.8125rem" }} disabled={qaLoading || !qaQuestion.trim()}>
                  {qaLoading ? <span className="spinner" aria-hidden="true" style={{ width: 14, height: 14 }} /> : "Ask"}
                </button>
              </form>

              {qaResult && (
                <div className="qa-answer-card" style={{ marginTop: "0.75rem", padding: "0.75rem" }}>
                  <span className="qa-answer-title">🤖 Answer</span>
                  <p className="qa-answer-text" style={{ fontSize: "0.8125rem" }}>{qaResult.answer}</p>
                  {qaResult.citations?.length > 0 && (
                    <div className="citations-wrap">
                      {qaResult.citations.map((c, idx) => (
                        <button
                          key={idx}
                          className="citation-badge"
                          onClick={() => setSelectedItemForEvidence({ id: c.source_id, content: c.text, description: c.text, itemType: c.source_type || "Citation" })}
                        >
                          📎 {c.source_type?.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Navigation Tabs */}
            <div className="intel-tabs" role="tablist">
              <button
                className={`intel-tab ${activeTab === "transcript" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("transcript")}
              >
                Transcript ({transcriptSegments.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "facts" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("facts")}
              >
                Facts ({facts.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "assumptions" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("assumptions")}
              >
                Assumptions ({assumptions.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "decisions" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("decisions")}
              >
                Decisions ({decisions.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "actions" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("actions")}
              >
                Actions ({actionItems.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "conflicts" ? "intel-tab--active" : ""} ${
                  conflicts.length > 0 ? "intel-tab--has-conflicts" : ""
                }`}
                onClick={() => setActiveTab("conflicts")}
              >
                Conflicts ({conflicts.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "approvals" ? "intel-tab--active" : ""} ${
                  approvals.some((a) => a.status === "pending") ? "intel-tab--has-conflicts" : ""
                }`}
                onClick={() => setActiveTab("approvals")}
              >
                Approvals ({approvals.filter((a) => a.status === "pending").length})
              </button>
            </div>

            {/* Tab 7: Approvals Column */}
            {activeTab === "approvals" && (
              <div className="transcript-feed">
                {approvals.length === 0 ? (
                  <div className="transcript-empty"><p>No pending external action approvals. Everything clear!</p></div>
                ) : (
                  approvals.map((appr) => (
                    <div key={appr.id} className={`approval-card approval-card--${appr.status}`}>
                      <div className="intel-card-header">
                        <span className={`pill-badge approval-badge-${appr.action_type}`}>{appr.action_type.toUpperCase()} ACTION</span>
                        <span className="pill-badge" style={{ color: appr.status === "pending" ? "#eab308" : appr.status === "approved" ? "#22c55e" : "#ef4444" }}>
                          {appr.status.toUpperCase()}
                        </span>
                      </div>
                      <p className="intel-card-text" style={{ fontWeight: 600 }}>{appr.title}</p>
                      {appr.description && <p className="intel-card-sub">{appr.description}</p>}

                      {appr.status === "pending" ? (
                        <div className="approval-actions">
                          <button className="btn-approve" onClick={() => handleApproveAction(appr.id)}>
                            ✓ Approve &amp; Execute
                          </button>
                          <button className="btn-reject" onClick={() => handleRejectAction(appr.id)}>
                            ✕ Reject
                          </button>
                        </div>
                      ) : (
                        <p className="intel-card-sub" style={{ color: "var(--text-muted)", marginTop: "0.2rem" }}>
                          {appr.status === "approved" ? "✓ Authorized & Executed" : "✕ Rejected by Human"}
                        </p>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 1: Live Transcript Feed */}
            {activeTab === "transcript" && (
              <div className="transcript-feed" ref={transcriptFeedRef}>
                {transcriptSegments.length === 0 ? (
                  <div className="transcript-empty">
                    <p>Listening for spoken audio…</p>
                  </div>
                ) : (
                  transcriptSegments.map((seg) => (
                    <div key={seg.id || `${seg.start_ms}-${seg.text}`} className="transcript-card">
                      <div className="transcript-card-meta">
                        <span className="transcript-speaker">{seg.speaker_name || `Speaker ${seg.speaker_id || ""}`}</span>
                        <span className="transcript-time">{formatMs(seg.start_ms)}</span>
                      </div>
                      <p className="transcript-text">{seg.text}</p>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 2: Facts Column */}
            {activeTab === "facts" && (
              <div className="transcript-feed">
                {facts.length === 0 ? (
                  <div className="transcript-empty"><p>No facts extracted yet.</p></div>
                ) : (
                  facts.map((fact) => (
                    <div key={fact.id} className="intel-card" onClick={() => setSelectedItemForEvidence({ ...fact, itemType: "Fact" })}>
                      <div className="intel-card-header">
                        <span className="pill-badge">FACT</span>
                        <span className="pill-confidence">{Math.round((fact.confidence || 0.9) * 100)}% confidence</span>
                      </div>
                      <p className="intel-card-text">{fact.content}</p>
                      <div className="evidence-hint">🔍 Click to inspect evidence line →</div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 3: Assumptions Column */}
            {activeTab === "assumptions" && (
              <div className="transcript-feed">
                {assumptions.length === 0 ? (
                  <div className="transcript-empty"><p>No assumptions detected yet.</p></div>
                ) : (
                  assumptions.map((assump) => (
                    <div key={assump.id} className="intel-card" onClick={() => setSelectedItemForEvidence({ ...assump, itemType: "Assumption" })}>
                      <div className="intel-card-header">
                        <span className="pill-badge">ASSUMPTION</span>
                        <span className="pill-badge" style={{ color: "#eab308" }}>{assump.status || "pending"}</span>
                      </div>
                      <p className="intel-card-text">{assump.content}</p>
                      <div className="evidence-hint">🔍 Click to inspect evidence line →</div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 4: Decisions Column */}
            {activeTab === "decisions" && (
              <div className="transcript-feed">
                {decisions.length === 0 ? (
                  <div className="transcript-empty"><p>No decisions recorded yet.</p></div>
                ) : (
                  decisions.map((dec) => (
                    <div key={dec.id} className="intel-card" onClick={() => setSelectedItemForEvidence({ ...dec, itemType: "Decision" })}>
                      <div className="intel-card-header">
                        <span className="pill-badge">DECISION</span>
                        {dec.owner_name && <span className="intel-card-sub">By {dec.owner_name}</span>}
                      </div>
                      <p className="intel-card-text">{dec.content}</p>
                      {dec.rationale && <p className="intel-card-sub">Rationale: {dec.rationale}</p>}
                      <div className="evidence-hint">🔍 Click to inspect evidence line →</div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 5: Action Items Column */}
            {activeTab === "actions" && (
              <div className="transcript-feed">
                {actionItems.length === 0 ? (
                  <div className="transcript-empty"><p>No action items assigned yet.</p></div>
                ) : (
                  actionItems.map((act) => (
                    <div key={act.id} className="intel-card" onClick={() => setSelectedItemForEvidence({ ...act, itemType: "Action Item" })}>
                      <div className="intel-card-header">
                        <span className="pill-badge">ACTION ITEM</span>
                        <span className="pill-badge" style={{ color: "#38bdf8" }}>{act.status || "open"}</span>
                      </div>
                      <p className="intel-card-text">{act.description}</p>
                      <div className="intel-card-header" style={{ marginTop: "0.2rem" }}>
                        <span className="intel-card-sub">Assignee: {act.assignee_name || "Unassigned"}</span>
                        {act.due_date && <span className="intel-card-sub" style={{ color: "#f43f5e" }}>Due: {act.due_date}</span>}
                      </div>
                      <div className="evidence-hint">🔍 Click to inspect evidence line →</div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab 6: Conflicts Column (Visually Highlighted Warning Cards) */}
            {activeTab === "conflicts" && (
              <div className="transcript-feed">
                {conflicts.length === 0 ? (
                  <div className="transcript-empty"><p>No conflicts or contradictions detected. Everything aligned! ✅</p></div>
                ) : (
                  conflicts.map((conf) => (
                    <div key={conf.id} className="intel-card intel-card--conflict" onClick={() => setSelectedItemForEvidence({ ...conf, itemType: "Conflict" })}>
                      <div className="intel-card-header">
                        <span className="pill-conflict-tag">⚠️ CONFLICT FLAGGED</span>
                        <span className="pill-badge" style={{ color: "#ef4444" }}>{conf.status || "open"}</span>
                      </div>
                      <p className="intel-card-text" style={{ fontWeight: 600, color: "#fca5a5" }}>
                        {conf.description}
                      </p>
                      <div className="evidence-hint" style={{ color: "#f87171" }}>🔍 Click to view evidence trail for both claims →</div>
                    </div>
                  ))
                )}
              </div>
            )}
          </aside>
        )}
      </div>

      {/* ── Slide-over Evidence Inspector Drawer ─────────────────────────── */}
      {selectedItemForEvidence && (
        <div className="evidence-drawer-overlay" onClick={() => setSelectedItemForEvidence(null)}>
          <div className="evidence-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <h3 className="drawer-title">Evidence Inspector</h3>
              <button className="btn-close-drawer" onClick={() => setSelectedItemForEvidence(null)}>×</button>
            </div>

            <div className="drawer-section-title">{selectedItemForEvidence.itemType} Claim</div>
            <div className="drawer-item-box">
              <p style={{ fontWeight: 600, color: "var(--text)", marginBottom: "0.4rem" }}>
                {selectedItemForEvidence.content || selectedItemForEvidence.description}
              </p>
              {selectedItemForEvidence.category && (
                <p className="intel-card-sub">Category: {selectedItemForEvidence.category}</p>
              )}
            </div>

            <div className="drawer-section-title">Source Transcript Evidence</div>
            {(() => {
              const seg = getMatchingSegment(selectedItemForEvidence);
              if (!seg) {
                return (
                  <div className="drawer-evidence-quote">
                    <p className="quote-text">Evidence line registered in PostgreSQL evidence ledger.</p>
                  </div>
                );
              }
              return (
                <div className="drawer-evidence-quote">
                  <div className="quote-meta">
                    <span>{seg.speaker_name || `Speaker ${seg.speaker_id || ""}`}</span>
                    <span>{formatMs(seg.start_ms)}</span>
                  </div>
                  <p className="quote-text">"{seg.text}"</p>
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* ── Controls bar (visible only when live) ───────────────────────── */}
      {phase === "live" && (
        <footer className="room-controls">
          <button
            onClick={toggleMic}
            className={`btn-control ${micMuted ? "btn-control--muted" : ""}`}
            id="mic-btn"
            title={micMuted ? "Unmute microphone" : "Mute microphone"}
          >
            {micMuted ? (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="1" y1="1" x2="23" y2="23" />
                <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            ) : (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            )}
            {micMuted ? "Unmute" : "Mute"}
          </button>

          <div className="live-dot-wrap" aria-label="Connected">
            <span className="live-dot" aria-hidden="true" />
            <span className="live-label">Live</span>
          </div>
        </footer>
      )}
    </div>
  );
}
