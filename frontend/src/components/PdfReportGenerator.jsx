import React, { useState, useRef } from "react";
import html2pdf from "html2pdf.js";

export default function PdfReportGenerator({ report, approvals, onClose }) {
  const [sections, setSections] = useState({
    summary: true,
    timeline: true,
    facts: true,
    hypotheses: true,
    evidence: true,
    decisions: true,
    actions: true,
    approvals: true,
    assessment: true,
  });

  const [isGenerating, setIsGenerating] = useState(false);
  const pdfRef = useRef(null);

  const handleToggle = (key) => {
    setSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleGenerate = async () => {
    setIsGenerating(true);
    const element = pdfRef.current;
    
    // We create a clone to avoid messing with the UI layout, 
    // or just generate directly from the hidden ref.
    const opt = {
      margin:       0.5,
      filename:     `Incident_Report_${report.id || "Sentinel"}.pdf`,
      image:        { type: 'jpeg', quality: 0.98 },
      html2canvas:  { scale: 2, useCORS: true },
      jsPDF:        { unit: 'in', format: 'letter', orientation: 'portrait' }
    };

    try {
      // Unhide temporarily if display: none is used
      element.style.display = "block";
      await html2pdf().set(opt).from(element).save();
      element.style.display = "none";
      onClose();
    } catch (err) {
      console.error("PDF generation failed", err);
    } finally {
      setIsGenerating(false);
    }
  };

  // Safe data extraction
  const timeline = report.timeline_events || [];
  const facts = report.facts || [];
  const hypotheses = report.hypotheses || []; // Note: in DB it's assumptions, but we map it if available
  const conflicts = report.conflicts || [];
  const decisions = report.decisions || [];
  const actionItems = report.action_items || [];

  return (
    <div className="join-overlay">
      <div className="join-card" style={{ maxWidth: 600, width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
          <h2 className="join-heading" style={{ marginBottom: 0 }}>Report Configuration</h2>
          <button onClick={onClose} className="btn-close-drawer">×</button>
        </div>

        <p className="join-sub">Select the sections to include in the Technical Incident Report.</p>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "2rem" }}>
          {Object.keys(sections).map((key) => (
            <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
              <input 
                type="checkbox" 
                checked={sections[key]} 
                onChange={() => handleToggle(key)} 
                style={{ width: 16, height: 16, accentColor: "var(--accent)" }}
              />
              <span style={{ textTransform: "capitalize", fontSize: "0.9rem" }}>
                {key === "hypotheses" ? "Hypotheses" : key}
              </span>
            </label>
          ))}
        </div>

        <div style={{ display: "flex", gap: "1rem" }}>
          <button className="btn-primary" onClick={handleGenerate} disabled={isGenerating}>
            {isGenerating ? "Generating PDF..." : "Generate & Download PDF"}
          </button>
        </div>
      </div>

      {/* Hidden PDF Template */}
      <div 
        ref={pdfRef} 
        style={{ 
          display: "none", 
          background: "#fff", 
          color: "#000", 
          padding: "2rem", 
          width: "800px",
          fontFamily: "Helvetica, Arial, sans-serif" 
        }}
      >
        {/* Cover / Header */}
        <div style={{ borderBottom: "2px solid #1e293b", paddingBottom: "1rem", marginBottom: "2rem" }}>
          <h1 style={{ fontSize: "24px", color: "#1e293b", marginBottom: "0.5rem" }}>SENTINEL REAL-TIME INCIDENT REPORT</h1>
          <table style={{ width: "100%", fontSize: "12px", borderCollapse: "collapse" }}>
            <tbody>
              <tr>
                <td style={{ padding: "4px 0", fontWeight: "bold", width: "20%" }}>Incident Title:</td>
                <td>{report.title || "N/A"}</td>
              </tr>
              <tr>
                <td style={{ padding: "4px 0", fontWeight: "bold" }}>Status:</td>
                <td>{report.status?.toUpperCase() || "N/A"}</td>
              </tr>
              <tr>
                <td style={{ padding: "4px 0", fontWeight: "bold" }}>Generated At:</td>
                <td>{new Date().toLocaleString()}</td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* 1. Summary */}
        {sections.summary && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>1. INCIDENT SUMMARY</h2>
            <p style={{ fontSize: "12px", lineHeight: "1.6" }}>{report.executive_summary || "No summary available."}</p>
          </div>
        )}

        {/* 2. Timeline */}
        {sections.timeline && timeline.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>2. INCIDENT TIMELINE</h2>
            <table style={{ width: "100%", fontSize: "11px", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Time</th>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Event Type</th>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Description</th>
                </tr>
              </thead>
              <tbody>
                {timeline.map(t => (
                  <tr key={t.id}>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0", width: "20%" }}>{new Date(t.occurred_at).toLocaleTimeString()}</td>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0", width: "20%" }}>{t.event_type}</td>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0" }}>{t.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 3. Facts */}
        {sections.facts && facts.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>3. FACTS</h2>
            <ul style={{ fontSize: "12px", paddingLeft: "1.5rem" }}>
              {facts.map(f => (
                <li key={f.id} style={{ marginBottom: "0.5rem" }}>{f.content}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 4. Evidence & Contradictions */}
        {sections.evidence && conflicts.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>4. CONTRADICTIONS & CONFLICTS</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              {conflicts.map(c => (
                <div key={c.id} style={{ padding: "10px", background: "#fef2f2", borderLeft: "4px solid #ef4444", fontSize: "12px" }}>
                  <strong>Status:</strong> {c.status} <br/>
                  <strong>Description:</strong> {c.description}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 5. Decisions */}
        {sections.decisions && decisions.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>5. DECISIONS</h2>
            <ul style={{ fontSize: "12px", paddingLeft: "1.5rem" }}>
              {decisions.map(d => (
                <li key={d.id} style={{ marginBottom: "0.5rem" }}>
                  <strong>Decision:</strong> {d.content} <br/>
                  {d.rationale && <span><strong>Rationale:</strong> {d.rationale}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 6. Action Items */}
        {sections.actions && actionItems.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>6. ACTION ITEMS</h2>
            <table style={{ width: "100%", fontSize: "11px", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Description</th>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Status</th>
                  <th style={{ padding: "8px", border: "1px solid #e2e8f0" }}>Due Date</th>
                </tr>
              </thead>
              <tbody>
                {actionItems.map(a => (
                  <tr key={a.id}>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0" }}>{a.description}</td>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0", width: "15%" }}>{a.status || "Open"}</td>
                    <td style={{ padding: "8px", border: "1px solid #e2e8f0", width: "20%" }}>{a.due_date || "N/A"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 7. Approvals */}
        {sections.approvals && approvals.length > 0 && (
          <div style={{ marginBottom: "2rem" }}>
            <h2 style={{ fontSize: "18px", color: "#4f46e5", borderBottom: "1px solid #cbd5e1", paddingBottom: "0.25rem", marginBottom: "1rem" }}>7. HUMAN APPROVAL & EXECUTION</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              {approvals.map(a => (
                <div key={a.id} style={{ padding: "10px", background: "#f8fafc", border: "1px solid #e2e8f0", fontSize: "12px" }}>
                  <strong>{a.action_type?.toUpperCase()} Action:</strong> {a.title} <br/>
                  <strong>Status:</strong> {a.status.toUpperCase()} <br/>
                  {a.description && <span><strong>Description:</strong> {a.description}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ marginTop: "3rem", fontSize: "10px", textAlign: "center", color: "#94a3b8" }}>
          -- End of Report -- <br/>
          Generated by Sentinel Incident Commander
        </div>
      </div>
    </div>
  );
}
