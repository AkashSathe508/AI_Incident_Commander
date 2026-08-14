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

  // ── Transcript state ─────────────────────────────────────────────────────
  const [transcriptSegments, setTranscriptSegments] = useState([]);
  const transcriptFeedRef = useRef(null);

  // ── Mic state ────────────────────────────────────────────────────────────
  const [micMuted, setMicMuted] = useState(false);

  // ── Agora refs ───────────────────────────────────────────────────────────
  const clientRef = useRef(null);
  const micTrackRef = useRef(null);
  const localUidRef = useRef(null);

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

  // ── WebSocket live transcript connection ──────────────────────────────────
  useEffect(() => {
    if (phase !== "live") return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/meetings/${id}/live`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[WS] Connected to live transcript stream for meeting", id);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "history") {
          setTranscriptSegments(data.segments || []);
        } else if (data.type === "transcript_segment") {
          setTranscriptSegments((prev) => {
            // Deduplicate incoming websocket segment by ID or start_ms
            if (prev.some((s) => s.id === data.id || (s.start_ms === data.start_ms && s.text === data.text))) {
              return prev;
            }
            return [...prev, data];
          });
        }
      } catch (err) {
        console.error("[WS] Error parsing transcript message:", err);
      }
    };

    ws.onerror = (err) => console.warn("[WS] Transcript websocket error:", err);
    ws.onclose = () => console.log("[WS] Live transcript websocket closed");

    return () => {
      ws.close();
    };
  }, [id, phase]);

  // ── Auto-scroll transcript to bottom ──────────────────────────────────────
  useEffect(() => {
    if (transcriptFeedRef.current) {
      transcriptFeedRef.current.scrollTop = transcriptFeedRef.current.scrollHeight;
    }
  }, [transcriptSegments]);

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
        if (mediaType === "audio") {
          user.audioTrack.play();
        }
        setParticipants((prev) =>
          prev.find((p) => p.uid === user.uid)
            ? prev
            : [...prev, { uid: user.uid, name: `Guest-${String(user.uid).slice(-4)}`, isSelf: false }]
        );
      });

      client.on("user-unpublished", (user, mediaType) => {
        if (mediaType === "audio") {
          user.audioTrack?.stop();
        }
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
        const active = new Set(
          volumes.filter((v) => v.level > 8).map((v) => v.uid)
        );
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

  // ── Helper format time ────────────────────────────────────────────────────
  function formatMs(ms) {
    if (!ms && ms !== 0) return "";
    const totalSec = Math.floor(ms / 1000);
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
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
            <p className="room-header-title">
              {meeting?.title ?? "Loading…"}
            </p>
            <p className="room-header-sub">
              {phase === "live"
                ? `${participants.length} participant${participants.length !== 1 ? "s" : ""} · Live`
                : "Incident Commander"}
            </p>
          </div>
        </div>
        {phase === "live" && (
          <button onClick={leaveRoom} className="btn-leave" id="leave-btn">
            Leave
          </button>
        )}
      </header>

      {/* ── Main ────────────────────────────────────────────────────────── */}
      <div className="room-body">
        <main className="room-main">
          {/* Join prompt modal */}
          {(phase === "prompt" || phase === "joining") && (
            <div className="join-overlay" role="dialog" aria-modal="true" aria-label="Join room">
              <div className="join-card">
                <h2 className="join-heading">Join the room</h2>
                <p className="join-sub">
                  {meeting
                    ? `"${meeting.title}"`
                    : "Connecting to meeting…"}
                </p>

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

                {phase === "joining" && (
                  <p className="join-steps-msg">
                    Requesting mic access &amp; joining Agora channel…
                  </p>
                )}
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
                    <div
                      className="participant-avatar"
                      style={{ background: color }}
                      aria-hidden="true"
                    >
                      {getInitials(p.name)}
                      {isSpeaking && (
                        <span className="speaking-rings" aria-hidden="true" />
                      )}
                    </div>
                    <p className="participant-name">{p.name}</p>
                    {p.isSelf && (
                      <span className="participant-you-badge">You</span>
                    )}
                    {micMuted && p.isSelf && (
                      <span className="participant-muted-badge">Muted</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </main>

        {/* ── Live Transcript Sidebar ───────────────────────────────────── */}
        {phase === "live" && (
          <aside className="transcript-panel" aria-label="Live Transcript">
            <div className="transcript-header">
              <h3 className="transcript-title">
                <span>Live Transcript</span>
                <span className="transcript-badge">AI Commander</span>
              </h3>
            </div>

            <div className="transcript-feed" ref={transcriptFeedRef}>
              {transcriptSegments.length === 0 ? (
                <div className="transcript-empty">
                  <p>Listening for spoken audio…</p>
                  <span style={{ fontSize: "0.75rem", opacity: 0.7 }}>
                    Transcripts will scroll here in real time.
                  </span>
                </div>
              ) : (
                transcriptSegments.map((seg) => (
                  <div key={seg.id || `${seg.start_ms}-${seg.text}`} className="transcript-card">
                    <div className="transcript-card-meta">
                      <span className="transcript-speaker">
                        {seg.speaker_name || `Speaker ${seg.speaker_id || ""}`}
                      </span>
                      <span className="transcript-time">{formatMs(seg.start_ms)}</span>
                    </div>
                    <p className="transcript-text">{seg.text}</p>
                  </div>
                ))
              )}
            </div>
          </aside>
        )}
      </div>

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
