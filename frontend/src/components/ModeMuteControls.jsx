import React, { useState, useEffect } from 'react';

/**
 * Mode and mute controls for Sentinel AI Incident Commander.
 * Props:
 *   meetingId: string – the UUID of the current meeting.
 */
export default function ModeMuteControls({ meetingId }) {
  const [mode, setMode] = useState('frequent'); // 'frequent' | 'occasional'
  const [muted, setMuted] = useState(false);
  const apiBase = '/api';

  // Load current flags on mount
  useEffect(() => {
    async function fetchState() {
      try {
        const res = await fetch(`${apiBase}/meetings/${meetingId}`);
        if (!res.ok) return;
        const data = await res.json();
        setMode(data.default_mode ?? 'frequent');
        setMuted(Boolean(data.is_muted));
      } catch (e) {
        console.error('Failed to load meeting flags', e);
      }
    }
    fetchState();
  }, [meetingId]);

  // Handlers to update backend
  async function updateMode(newMode) {
    try {
      const res = await fetch(`${apiBase}/meetings/${meetingId}/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode }),
      });
      if (res.ok) setMode(newMode);
    } catch (e) {
      console.error('Failed to update mode', e);
    }
  }

  async function toggleMute() {
    const newMuted = !muted;
    try {
      const res = await fetch(`${apiBase}/meetings/${meetingId}/mute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_muted: newMuted }),
      });
      if (res.ok) setMuted(newMuted);
    } catch (e) {
      console.error('Failed to update mute', e);
    }
  }

  return (
    <div className="mode-mute-controls" style={{ display: 'flex', gap: '1rem', marginBottom: '0.5rem' }}>
      <label>
        Mode:{' '}
        <select value={mode} onChange={e => updateMode(e.target.value)}>
          <option value="frequent">Frequent</option>
          <option value="occasional">Occasional</option>
        </select>
      </label>
      <button onClick={toggleMute} style={{ padding: '0.2rem 0.5rem' }}>
        {muted ? 'Unmute' : 'Mute'}
      </button>
    </div>
  );
}
