import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import AgoraRTC from "agora-rtc-sdk-ng";
import { avatarColor, getInitials } from "./CreateRoom";
import ModeMuteControls from "../components/ModeMuteControls";

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
  const [activeTab, setActiveTab] = useState("transcript"); // transcript | facts | assumptions | decisions | actions | conflicts | notes

  // ── AI Voice (TTS) state ──────────────────────────────────────────────────
  const [aiVoiceEnabled, setAiVoiceEnabled] = useState(true);
  const aiVoiceEnabledRef = useRef(true);
  const factConflictCountRef = useRef(0); // tracks count for proactive question trigger
  const [voicedQuestion, setVoicedQuestion] = useState(null); // currently spoken question text
  const voicedQuestionTimerRef = useRef(null);
  // live state refs for access inside WS closure
  const factsRef = useRef([]);
  const decisionsRef = useRef([]);
  const actionItemsRef = useRef([]);
  const [transcriptSegments, setTranscriptSegments] = useState([]);
  const [facts, setFacts] = useState([]);
  const [assumptions, setAssumptions] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [actionItems, setActionItems] = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [evidenceList, setEvidenceList] = useState([]);
  const [aiResponses, setAiResponses] = useState([]);
  const [roomNotes, setRoomNotes] = useState([]);
  const [savingNote, setSavingNote] = useState(false);
  const [noteContent, setNoteContent] = useState("");
  const [noteCategory, setNoteCategory] = useState("observation");
  const [slackTestStatus, setSlackTestStatus] = useState(null); // null | 'testing' | 'ok' | 'error'
  const [slackTestMsg, setSlackTestMsg] = useState("");

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

  // ── CSV Upload State ───────────────────────────────────────────────────────
  const fileInputRef = useRef(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setIsUploading(true);
    const reader = new FileReader();
    reader.onload = async (event) => {
      try {
        const text = event.target.result;
        // Simple CSV parser: assumes "Speaker,Text" format (e.g. "Alice,The server is down")
        const lines = text.split('\n').filter(line => line.trim());
        let currentMs = Date.now();
        
        for (const line of lines) {
          // simple split by first comma
          const commaIdx = line.indexOf(',');
          let speaker = "System";
          let content = line;
          
          if (commaIdx > -1) {
            speaker = line.substring(0, commaIdx).trim();
            content = line.substring(commaIdx + 1).trim().replace(/^"(.*)"$/, '$1'); // remove quotes if any
          }

          const payload = {
            type: "transcript_segment",
            id: crypto.randomUUID(),
            meeting_id: id,
            speaker_id: speaker,
            speaker_name: speaker,
            participant_id: "csv-upload",
            text: content,
            start_ms: currentMs,
            end_ms: currentMs + 2000,
            confidence: 1.0,
          };
          
          await fetch(`${API}/meetings/${id}/broadcast`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          
          // wait 3 seconds between each to allow LLM processing to catch up
          await new Promise(res => setTimeout(res, 3000));
          currentMs += 3000;
        }
      } catch (err) {
        console.error("CSV Upload Error:", err);
      } finally {
        setIsUploading(false);
      }
    };
    reader.readAsText(file);
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ── Mic state ────────────────────────────────────────────────────────────
  const [micMuted, setMicMuted] = useState(false);
  const micMutedRef = useRef(micMuted);

  useEffect(() => {
    micMutedRef.current = micMuted;
  }, [micMuted]);

  useEffect(() => {
    aiVoiceEnabledRef.current = aiVoiceEnabled;
  }, [aiVoiceEnabled]);

  // ── AI TTS helper ─────────────────────────────────────────────────────────
  function speakAiResponse(text, prefix = "") {
    if (!aiVoiceEnabledRef.current) return;
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(prefix + text);
    utterance.rate = 0.95;
    utterance.pitch = 1.05;
    utterance.volume = 1.0;
    const voices = window.speechSynthesis.getVoices();
    const preferredVoice = voices.find(
      (v) => v.lang.startsWith("en") && (v.name.includes("Google") || v.name.includes("Microsoft"))
    ) || voices.find((v) => v.lang.startsWith("en"));
    if (preferredVoice) utterance.voice = preferredVoice;
    window.speechSynthesis.speak(utterance);
  }

  // Generate a context-aware question from real meeting facts and decisions
  function generateContextualQuestion() {
    const allFacts = factsRef.current;
    const allDecisions = decisionsRef.current;
    const allActions = actionItemsRef.current;

    // Build context-aware questions from actual data
    const questions = [];

    // Decision-based follow-ups (highest priority)
    allDecisions.forEach((d) => {
      const text = d.content || d.description || "";
      if (text) {
        if (/rollback|revert|undeploy/i.test(text))
          questions.push(`Has the rollback been confirmed as complete? — Referenced decision: "${text.slice(0, 80)}".`);
        else if (/deploy|release|push|hotfix/i.test(text))
          questions.push(`Has the deployment gone out successfully and is it verified in production? — Decision: "${text.slice(0, 80)}".`);
        else if (/assign|owner|escalate/i.test(text))
          questions.push(`Who is following up on this? — Decision: "${text.slice(0, 80)}". Can the owner confirm status?`);
        else
          questions.push(`Has this decision been actioned yet? — "${text.slice(0, 80)}". Can someone confirm?`);
      }
    });

    // Action-item follow-ups
    allActions.forEach((a) => {
      const desc = a.description || "";
      const assignee = a.assignee_name || "the assignee";
      if (desc)
        questions.push(`${assignee}, can you give a status update on: "${desc.slice(0, 80)}"?`);
    });

    // Fact-based probing questions
    allFacts.slice(-3).forEach((f) => {
      const text = f.content || "";
      if (/error|failure|crash|exception|down|outage/i.test(text))
        questions.push(`We noted: "${text.slice(0, 80)}" — has this been resolved or is it still active?`);
      else if (/customer|user|impact/i.test(text))
        questions.push(`Regarding "${text.slice(0, 80)}" — has customer impact been quantified and are they being notified?`);
    });

    // SentinelAI fallback questions
    const fallbacks = [
      "Is the issue fully resolved, or are we still in mitigation mode?",
      "Does everyone have a clear next action? If not, let's assign one now.",
      "Has root cause been identified? If not, who is leading the investigation?",
      "Are there any blockers that need immediate escalation?",
      "What is the estimated time to resolution?",
      "Has the on-call runbook been followed? Are there any gaps?",
      "Are monitoring dashboards showing recovery? Can someone share a screenshot?",
    ];

    const pool = questions.length > 0 ? questions : fallbacks;
    const q = pool[factConflictCountRef.current % pool.length];
    return q;
  }

  function speakProactiveQuestion() {
    const q = generateContextualQuestion();
    // Show the question as a toast
    setVoicedQuestion(q);
    if (voicedQuestionTimerRef.current) clearTimeout(voicedQuestionTimerRef.current);
    voicedQuestionTimerRef.current = setTimeout(() => setVoicedQuestion(null), 18000);
    // Speak it aloud as SentinelAI
    speakAiResponse(q, "SentinelAI: ");
  }

  // ── Agora & Speech refs ───────────────────────────────────────────────────
  const clientRef = useRef(null);
  const micTrackRef = useRef(null);
  const localUidRef = useRef(null);
  const transcriptFeedRef = useRef(null);
  const recognitionRef = useRef(null);

  function startSpeechRecognition(meetingId, userUid, userName, participantUuid) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.log("Browser SpeechRecognition API not supported.");
      return;
    }
    try {
      if (recognitionRef.current) {
        try { recognitionRef.current.stop(); } catch (_) {}
      }
      const recognition = new SpeechRecognition();
      recognitionRef.current = recognition;
      recognition.continuous = true;
      recognition.interimResults = false;
      recognition.lang = "en-US";

      recognition.onresult = (event) => {
        if (micMutedRef.current) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const res = event.results[i];
          if (res.isFinal) {
            const text = res[0].transcript.trim();
            if (text) {
              const now = Date.now();
              const payload = {
                type: "transcript_segment",
                id: crypto.randomUUID(),
                meeting_id: meetingId,
                speaker_id: String(userUid),
                speaker_name: userName,
                participant_id: participantUuid,
                text: text,
                start_ms: now - 3000,
                end_ms: now,
                confidence: res[0].confidence || 0.95,
              };

              fetch(`${API}/meetings/${meetingId}/broadcast`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
              }).catch((err) => console.warn("Failed to broadcast transcript segment:", err));
            }
          }
        }
      };

      recognition.onerror = (err) => {
        if (err.error !== "no-speech") console.warn("SpeechRecognition error:", err.error);
      };

      recognition.onend = () => {
        if (recognitionRef.current === recognition && !micMutedRef.current) {
          try { recognition.start(); } catch (_) {}
        }
      };

      recognition.start();
      console.log("[SpeechRecognition] Started browser live microphone transcription.");
    } catch (err) {
      console.warn("Failed to initialize browser speech recognition:", err);
    }
  }

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

  async function testSlackIntegration() {
    setSlackTestStatus("testing");
    setSlackTestMsg("");
    try {
      const res = await fetch(`${API}/meetings/${id}/test-slack`, { method: "POST" });
      const data = await res.json();
      if (data.status === "posted") {
        setSlackTestStatus("ok");
        setSlackTestMsg(data.mock ? "✅ Mock mode — no Slack credentials configured, but pipeline is functional." : `✅ Message sent to ${data.channel}!`);
      } else {
        setSlackTestStatus("error");
        setSlackTestMsg(`❌ Slack error: ${data.error || "Unknown error"}`);
      }
    } catch (err) {
      setSlackTestStatus("error");
      setSlackTestMsg(`❌ Request failed: ${err.message}`);
    }
  }

  // ── WebSocket live transcript & intelligence stream ───────────────────────
  useEffect(() => {
    if (phase !== "live") return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.port === "5173" ? `${window.location.hostname}:8000` : window.location.host;
    const wsUrl = `${protocol}//${host}/meetings/${id}/live`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[WS] Connected to live incident intelligence stream for meeting", id);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "full_history") {
          const segs = data.segments || [];
          const f = data.facts || [];
          const dec = data.decisions || [];
          const acts = data.action_items || [];
          setTranscriptSegments(segs);
          setFacts(f); factsRef.current = f;
          setAssumptions(data.assumptions || []);
          setDecisions(dec); decisionsRef.current = dec;
          setActionItems(acts); actionItemsRef.current = acts;
          setConflicts(data.conflicts || []);
          setEvidenceList(data.evidence || []);
          setAiResponses(data.ai_responses || []);
        } else if (data.type === "ai_response_created" && data.ai_response) {
          setAiResponses((prev) =>
            prev.some((r) => r.id === data.ai_response.id)
              ? prev
              : [...prev, data.ai_response]
          );
          // Only speak question-type AI responses aloud
          const r = data.ai_response;
          if (r.response_type === "question") {
            setVoicedQuestion(r.response_text);
            if (voicedQuestionTimerRef.current) clearTimeout(voicedQuestionTimerRef.current);
            voicedQuestionTimerRef.current = setTimeout(() => setVoicedQuestion(null), 18000);
            speakAiResponse(r.response_text, "Question: ");
          }
        } else if (data.type === "note_created" && data.note) {
          setRoomNotes((prev) =>
            prev.some((n) => n.id === data.note.id) ? prev : [...prev, data.note]
          );
        } else if (data.type === "transcript_segment") {
          setTranscriptSegments((prev) => {
            if (prev.some((s) => s.id === data.id || (s.start_ms === data.start_ms && s.text === data.text))) return prev;
            return [...prev, data];
          });
        } else if (data.type === "fact_created" && data.item) {
          setFacts((prev) => {
            const updated = prev.some((f) => f.id === data.item.id) ? prev : [...prev, data.item];
            factsRef.current = updated;
            return updated;
          });
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
          // Proactive question every 3rd fact
          factConflictCountRef.current += 1;
          if (factConflictCountRef.current % 3 === 0) {
            setTimeout(() => speakProactiveQuestion(), 2500);
          }
        } else if (data.type === "assumption_created" && data.item) {
          setAssumptions((prev) => (prev.some((a) => a.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "decision_created" && data.item) {
          setDecisions((prev) => {
            const updated = prev.some((d) => d.id === data.item.id) ? prev : [...prev, data.item];
            decisionsRef.current = updated;
            return updated;
          });
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
          // Every new decision triggers an immediate follow-up question
          setTimeout(() => {
            factConflictCountRef.current += 1;
            speakProactiveQuestion();
          }, 3000);
        } else if (data.type === "action_item_created" && data.item) {
          setActionItems((prev) => {
            const updated = prev.some((ai) => ai.id === data.item.id) ? prev : [...prev, data.item];
            actionItemsRef.current = updated;
            return updated;
          });
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "conflict_created" && data.item) {
          setConflicts((prev) => (prev.some((c) => c.id === data.item.id) ? prev : [...prev, data.item]));
          if (data.evidence?.length) setEvidenceList((prev) => [...prev, ...data.evidence]);
        } else if (data.type === "pending_approval_created" && data.approval) {
          setApprovals((prev) => [data.approval, ...prev.filter((a) => a.id !== data.approval.id)]);
          // SentinelAI announces approval requests aloud — most critical TTS
          speakAiResponse(
            `${data.approval.title}. Please review and approve or reject this action in the Approvals tab.`,
            "SentinelAI, action required: "
          );
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
      clientRef.current?.leave().catch(() => { });
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
      const { token, channel_name, uid, app_id, participant_id } = await res.json();
      localUidRef.current = uid;

      if (app_id && app_id.trim()) {
        try {
          const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
          clientRef.current = client;

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

          try {
            const micTrack = await AgoraRTC.createMicrophoneAudioTrack();
            micTrackRef.current = micTrack;
            await client.publish([micTrack]);
          } catch (micErr) {
            console.warn("Microphone creation failed or blocked:", micErr);
          }
        } catch (agoraErr) {
          console.warn("Agora RTC connection bypassed or invalid appid:", agoraErr.message || agoraErr);
        }
      } else {
        console.log("No AGORA_APP_ID configured — running in Browser Speech / Direct Audio mode.");
      }

      setParticipants([{ uid: uid || 1, name: displayName.trim(), isSelf: true }]);
      setPhase("live");
      startSpeechRecognition(id, uid || 1, displayName.trim(), participant_id);
    } catch (err) {
      console.error("Join error:", err);
      setJoinError(err.message || "Failed to join. Check console for details.");
      setPhase("prompt");
      micTrackRef.current?.close();
      micTrackRef.current = null;
      await clientRef.current?.leave().catch(() => { });
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
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (_) {}
    }
    micTrackRef.current?.close();
    micTrackRef.current = null;
    await clientRef.current?.leave().catch(() => {});
    clientRef.current = null;
    navigate(`/room/${id}/report`);
  }

  // ── Leave room ────────────────────────────────────────────────────────────
  async function leaveRoom() {
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (_) {}
    }
    micTrackRef.current?.close();
    micTrackRef.current = null;
    await clientRef.current?.leave().catch(() => {});
    clientRef.current = null;
    navigate("/");
  }

  // ── Toggle mic ────────────────────────────────────────────────────────────
  async function toggleMic() {
    const next = !micMuted;
    if (micTrackRef.current) {
      await micTrackRef.current.setMuted(next);
    }
    
    if (recognitionRef.current) {
      if (next) {
        try { recognitionRef.current.stop(); } catch (_) {}
        console.log("[SpeechRecognition] Paused browser microphone transcription (Muted).");
      } else {
        try { recognitionRef.current.start(); } catch (_) {}
        console.log("[SpeechRecognition] Resumed browser microphone transcription (Unmuted).");
      }
    }
    
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
                : "SentinelAI"}
            </p>
          </div>
        </div>
    <ModeMuteControls meetingId={id} />
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
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>Manual Transcript</span>
                <div>
                  <input 
                    type="file" 
                    accept=".csv" 
                    style={{ display: "none" }} 
                    ref={fileInputRef} 
                    onChange={handleFileUpload} 
                  />
                  <button 
                    className="btn-primary" 
                    style={{ padding: "0.2rem 0.5rem", fontSize: "0.7rem", background: "#4f46e5" }}
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploading}
                  >
                    {isUploading ? "Uploading..." : "Upload CSV"}
                  </button>
                </div>
              </div>
              <form onSubmit={handleAskQuestion} style={{ display: "flex", gap: "0.5rem" }}>
                <div style={{ position: "relative", flex: 1, display: "flex" }}>
                  <input
                    type="text"
                    className="qa-input"
                    style={{ width: "100%", padding: "0.45rem 2rem 0.45rem 0.75rem", fontSize: "0.8125rem" }}
                    placeholder="Ask AI about this call..."
                    value={qaQuestion}
                    onChange={(e) => setQaQuestion(e.target.value)}
                    disabled={qaLoading}
                  />
                  {qaQuestion && (
                    <button
                      type="button"
                      onClick={() => { setQaQuestion(""); setQaResult(null); }}
                      style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)", background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", fontSize: "1rem" }}
                      title="Clear"
                    >
                      ✕
                    </button>
                  )}
                </div>
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
                Hypothesis ({assumptions.length})
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
                className={`intel-tab ${activeTab === "conflicts" ? "intel-tab--active" : ""} ${conflicts.length > 0 ? "intel-tab--has-conflicts" : ""
                  }`}
                onClick={() => setActiveTab("conflicts")}
              >
                Conflicts ({conflicts.length})
              </button>
              <button
                className={`intel-tab ${activeTab === "approvals" ? "intel-tab--active" : ""} ${approvals.some((a) => a.status === "pending") ? "intel-tab--has-conflicts" : ""
                  }`}
                onClick={() => setActiveTab("approvals")}
              >
                Approvals ({approvals.filter((a) => a.status === "pending").length})
              </button>
              <button
                className={`intel-tab ${activeTab === "notes" ? "intel-tab--active" : ""}`}
                onClick={() => setActiveTab("notes")}
              >
                Notes ({roomNotes.length})
              </button>
            </div>

            {/* Tab 7: Approvals Column */}
            {activeTab === "approvals" && (
              <div className="transcript-feed">
                {/* Slack integration test */}
                <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>Slack Integration</span>
                  <button
                    className="btn-primary"
                    style={{ padding: "0.2rem 0.65rem", fontSize: "0.72rem", width: "auto", background: slackTestStatus === "ok" ? "#16a34a" : slackTestStatus === "error" ? "#dc2626" : "#1d4ed8" }}
                    onClick={testSlackIntegration}
                    disabled={slackTestStatus === "testing"}
                    id="slack-test-btn"
                  >
                    {slackTestStatus === "testing" ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Testing…</> : "🔌 Test Slack"}
                  </button>
                  {slackTestMsg && (
                    <span style={{ fontSize: "0.73rem", color: slackTestStatus === "ok" ? "#4ade80" : "#f87171" }}>{slackTestMsg}</span>
                  )}
                </div>

                {approvals.length === 0 ? (
                  <div className="transcript-empty"><p>No pending external action approvals. Everything clear!</p></div>
                ) : (
                  approvals.map((appr) => {
                    const res = appr.result || {};
                    return (
                      <div key={appr.id} className={`approval-card approval-card--${appr.status}`}>
                        <div className="intel-card-header">
                          <span className={`pill-badge approval-badge-${appr.action_type}`}>{appr.action_type.toUpperCase()} ACTION</span>
                          <span className="pill-badge" style={{ color: appr.status === "pending" ? "#eab308" : appr.status === "approved" ? "#22c55e" : "#ef4444" }}>
                            {appr.status.toUpperCase()}
                          </span>
                          {appr.status === "pending" && (
                            <span style={{ fontSize: "0.68rem", color: "#a5b4fc", display: "flex", alignItems: "center", gap: "0.25rem" }}>
                              🔊 Announced aloud
                            </span>
                          )}
                        </div>
                        <p className="intel-card-text" style={{ fontWeight: 600 }}>{appr.title}</p>
                        {appr.description && <p className="intel-card-sub" style={{ whiteSpace: "pre-wrap" }}>{appr.description}</p>}

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
                          <div className="intel-card-sub" style={{ marginTop: "0.5rem" }}>
                            <p style={{ color: appr.status === "approved" ? "#22c55e" : "#ef4444", fontWeight: 500 }}>
                              {appr.status === "approved" ? "✓ Authorized & Executed" : "✕ Rejected by Human"}
                            </p>
                            
                            {/* Execution Result Details */}
                            {appr.status === "approved" && res && (
                              <div style={{ marginTop: "0.5rem", padding: "0.5rem", background: "#f8fafc", borderRadius: "4px", fontSize: "0.8rem", color: "#334155" }}>
                                {res.mock && <span style={{ display: "inline-block", background: "#fef08a", color: "#a16207", padding: "1px 6px", borderRadius: "12px", fontSize: "0.7rem", fontWeight: 600, marginRight: "8px", verticalAlign: "middle" }}>MOCK MODE</span>}
                                {res.url ? (
                                  <a href={res.url} target="_blank" rel="noreferrer" style={{ color: "#3b82f6", textDecoration: "none", fontWeight: 500, verticalAlign: "middle" }}>
                                    View Issue: {res.issue_key} ↗
                                  </a>
                                ) : res.channel ? (
                                  <span style={{ verticalAlign: "middle" }}>Posted to <strong>{res.channel}</strong></span>
                                ) : res.error ? (
                                  <span style={{ color: "#ef4444", verticalAlign: "middle" }}>Error: {res.error}</span>
                                ) : null}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            )}

            {/* SentinelAI tab removed — responses are surfaced via TTS */}

            {/* Tab 9: Notes — quick note-taking during meeting */}
            {activeTab === "notes" && (
              <div className="transcript-feed">
                {/* Quick add note form */}
                <div style={{ padding: "0.75rem", borderBottom: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.4rem" }}>
                    <select
                      className="field-input"
                      value={noteCategory}
                      onChange={(e) => setNoteCategory(e.target.value)}
                      style={{ fontSize: "0.75rem", padding: "0.3rem 0.5rem" }}
                    >
                      {["observation", "decision", "follow_up", "unresolved", "technical", "lesson_learned"].map((c) => (
                        <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
                      ))}
                    </select>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem" }}>
                    <input
                      type="text"
                      className="field-input"
                      placeholder="Quick note…"
                      value={noteContent}
                      onChange={(e) => setNoteContent(e.target.value)}
                      style={{ fontSize: "0.8rem", padding: "0.4rem 0.6rem" }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && noteContent.trim() && !savingNote) {
                          e.preventDefault();
                          setSavingNote(true);
                          fetch(`${API}/meetings/${id}/notes`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ content: noteContent, category: noteCategory, author_name: "SentinelAI" }),
                          })
                            .then((r) => r.json())
                            .then((n) => {
                              setRoomNotes((prev) => [...prev, n]);
                              setNoteContent("");
                            })
                            .catch(console.warn)
                            .finally(() => setSavingNote(false));
                        }
                      }}
                      id="room-note-input"
                    />
                    <button
                      className="btn-primary"
                      style={{ width: "auto", padding: "0.4rem 0.8rem", fontSize: "0.78rem" }}
                      disabled={savingNote || !noteContent.trim()}
                      id="room-note-save-btn"
                      onClick={() => {
                        if (!noteContent.trim()) return;
                        setSavingNote(true);
                        fetch(`${API}/meetings/${id}/notes`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ content: noteContent, category: noteCategory, author_name: "SentinelAI" }),
                        })
                          .then((r) => r.json())
                          .then((n) => {
                            setRoomNotes((prev) => [...prev, n]);
                            setNoteContent("");
                          })
                          .catch(console.warn)
                          .finally(() => setSavingNote(false));
                      }}
                    >
                      {savingNote ? <span className="spinner" style={{ width: 12, height: 12 }} /> : "Add"}
                    </button>
                  </div>
                  <p style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                    Press Enter to save
                  </p>
                </div>

                {/* Notes list */}
                {roomNotes.length === 0 ? (
                  <div className="transcript-empty"><p>No notes yet. Add your first observation.</p></div>
                ) : (
                  [...roomNotes].reverse().map((n) => {
                    const catColors = {
                      observation: "#6366f1", decision: "#22c55e",
                      follow_up: "#3b82f6", unresolved: "#ef4444",
                      technical: "#8b5cf6", lesson_learned: "#f59e0b",
                    };
                    const c = catColors[n.category] || "#6366f1";
                    return (
                      <div key={n.id} className="intel-card" style={{ borderLeft: `3px solid ${c}` }}>
                        <div className="intel-card-header">
                          <span
                            className="pill-badge"
                            style={{ background: `${c}20`, color: c }}
                          >
                            {n.category.replace(/_/g, " ")}
                          </span>
                          <span style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>
                            {n.author_name}
                          </span>
                        </div>
                        <p className="intel-card-text">{n.content}</p>
                      </div>
                    );
                  })
                )}
              </div>
            )}

            {/* Tab 1: Live Transcript Feed */}
            {activeTab === "transcript" && (
              <div className="transcript-feed" ref={transcriptFeedRef}>
                {/* SentinelAI Voiced Question Toast */}
                {voicedQuestion && (
                  <div className="voiced-question-toast">
                    <span className="voiced-question-icon">🎙️</span>
                    <div>
                      <p className="voiced-question-label">SentinelAI is asking:</p>
                      <p className="voiced-question-text">{voicedQuestion}</p>
                    </div>
                    <button
                      className="voiced-question-dismiss"
                      onClick={() => { setVoicedQuestion(null); if (window.speechSynthesis) window.speechSynthesis.cancel(); }}
                    >✕</button>
                  </div>
                )}
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

            {/* Tab 3: Hypothesis Column */}
            {activeTab === "assumptions" && (
              <div className="transcript-feed">
                {assumptions.length === 0 ? (
                  <div className="transcript-empty"><p>No hypotheses detected yet.</p></div>
                ) : (
                  assumptions.map((assump) => (
                    <div key={assump.id} className="intel-card" onClick={() => setSelectedItemForEvidence({ ...assump, itemType: "Hypothesis" })}>
                      <div className="intel-card-header">
                        <span className="pill-badge">HYPOTHESIS</span>
                        <span className="pill-badge" style={{ color: assump.status === "confirmed" ? "#22c55e" : "#eab308" }}>
                          {assump.status === "confirmed" ? "Confirmed" : "Unconfirmed"}
                        </span>
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

          {/* AI Voice On/Off Toggle */}
          <button
            onClick={() => {
              const next = !aiVoiceEnabled;
              setAiVoiceEnabled(next);
              if (!next && window.speechSynthesis) window.speechSynthesis.cancel();
            }}
            className={`btn-control ${!aiVoiceEnabled ? "btn-control--muted" : "btn-control--ai-voice"}`}
            id="ai-voice-btn"
            title={aiVoiceEnabled ? "Turn off AI voice" : "Turn on AI voice"}
          >
            {aiVoiceEnabled ? (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
              </svg>
            ) : (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <line x1="23" y1="9" x2="17" y2="15" />
                <line x1="17" y1="9" x2="23" y2="15" />
              </svg>
            )}
            AI Voice {aiVoiceEnabled ? "On" : "Off"}
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
