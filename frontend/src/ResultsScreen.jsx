import React, { useState, useEffect } from "react";
import "./ResultsScreen.css";

/**
 * Matra — Results Screen & Daily Timeline
 * 
 * Rules:
 * 1. A card per medicine with name, plain-language purpose, strength, and schedule.
 * 2. Suggested timings carry the visible suggestion label ("Suggested, confirm with your doctor or pharmacist").
 * 3. Daily timeline with taken/skipped/snooze actions stored per dose.
 * 4. Reminders via web push / FCM generated from confirmed timeline, supporting timezones & snoozing.
 * 5. Button to return to confirm step to regenerate the schedule.
 * 6. Always displays: "This is not medical advice."
 */
export default function ResultsScreen({
  prescriptionId,
  apiBaseUrl = "http://localhost:8000",
  authToken,
  onBackToConfirm,
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [prescriptionData, setPrescriptionData] = useState(null);
  const [schedule, setSchedule] = useState([]);
  const [matchedDrugs, setMatchedDrugs] = useState([]);
  const [doseLogs, setDoseLogs] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [selectedTz, setSelectedTz] = useState("Asia/Kolkata");
  const [actionMessage, setActionMessage] = useState("");
  const [feedbackState, setFeedbackState] = useState({}); // {drugName: 'idle'|'sending'|'sent'|'error'}

  // Load prescription details, schedule, doses, and reminders
  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Fetch confirmed prescription details
      const rxRes = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!rxRes.ok) throw new Error(`Failed to load prescription (HTTP ${rxRes.status})`);
      const rxData = await rxRes.json();
      setPrescriptionData(rxData);
      setMatchedDrugs(rxData.matched_drugs || []);

      // Fetch schedule
      const schedRes = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/schedule`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!schedRes.ok) throw new Error(`Failed to load schedule (HTTP ${schedRes.status})`);
      const schedData = await schedRes.json();
      setSchedule(schedData.schedule || []);

      // Fetch dose action history
      const dosesRes = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/doses`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (dosesRes.ok) {
        const dosesData = await dosesRes.json();
        setDoseLogs(dosesData.dose_logs || []);
      }

      // Fetch reminders
      const remRes = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/reminders`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (remRes.ok) {
        const remData = await remRes.json();
        setReminders(remData.reminders || []);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (prescriptionId) {
      fetchData();
    }
  }, [prescriptionId, apiBaseUrl, authToken]);

  // Log Taken / Skipped / Snooze action
  const handleDoseAction = async (drugName, scheduledTime, action, snoozeMinutes = null) => {
    try {
      const res = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/doses/log`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          drug_name: drugName,
          scheduled_time: scheduledTime,
          action: action,
          snooze_minutes: snoozeMinutes,
        }),
      });

      if (!res.ok) throw new Error("Failed to log dose action");
      const data = await res.json();
      setDoseLogs((prev) => [data.dose_log, ...prev]);
      setActionMessage(`Marked ${drugName} (${scheduledTime}) as ${action.toUpperCase()}`);
      setTimeout(() => setActionMessage(""), 3500);
    } catch (err) {
      setError(err.message);
    }
  };

  // Generate web push / FCM reminders
  const handleGenerateReminders = async () => {
    try {
      const res = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/reminders/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({ timezone: selectedTz }),
      });

      if (!res.ok) throw new Error("Failed to generate reminders");
      const data = await res.json();
      setReminders(data.reminders || []);
      setActionMessage(`✓ Scheduled ${data.reminders_count} reminders for ${selectedTz}`);
      setTimeout(() => setActionMessage(""), 4000);
    } catch (err) {
      setError(err.message);
    }
  };

  // Snooze a reminder
  const handleSnoozeReminder = async (reminderId) => {
    try {
      const res = await fetch(
        `${apiBaseUrl}/prescriptions/${prescriptionId}/reminders/${reminderId}/snooze`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ snooze_minutes: 15 }),
        }
      );

      if (!res.ok) throw new Error("Failed to snooze reminder");
      const data = await res.json();
      setReminders((prev) =>
        prev.map((r) => (r.id === reminderId ? data.reminder : r))
      );
      setActionMessage(`Snoozed reminder for 15 minutes.`);
      setTimeout(() => setActionMessage(""), 3500);
    } catch (err) {
      setError(err.message);
    }
  };

  // Simulate fire reminder
  const handleFireReminder = async (reminderId) => {
    try {
      const res = await fetch(
        `${apiBaseUrl}/prescriptions/${prescriptionId}/reminders/${reminderId}/fire`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${authToken}` },
        }
      );

      if (!res.ok) throw new Error("Failed to fire reminder");
      const data = await res.json();
      setReminders((prev) =>
        prev.map((r) => (r.id === reminderId ? data.reminder : r))
      );
      setActionMessage(`🔔 Push Notification Fired: ${data.notification?.title}`);
      setTimeout(() => setActionMessage(""), 4500);
    } catch (err) {
      setError(err.message);
    }
  };

  // Submit 'this is wrong' feedback (Rule 5: no PII logged server-side)
  const handleSubmitFeedback = async (drugName, fieldName = null) => {
    setFeedbackState((prev) => ({ ...prev, [drugName]: "sending" }));
    try {
      const res = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          drug_name: drugName,
          field_name: fieldName,
          issue_type: "incorrect_info",
          user_comment: null,
        }),
      });
      if (!res.ok) throw new Error("Feedback submission failed");
      setFeedbackState((prev) => ({ ...prev, [drugName]: "sent" }));
      setActionMessage(`✓ Feedback recorded for ${drugName}. Thank you!`);
      setTimeout(() => setActionMessage(""), 4000);
    } catch (err) {
      setFeedbackState((prev) => ({ ...prev, [drugName]: "error" }));
      setError(err.message);
    }
  };

  if (loading) {
    return (
      <div className="results-loading-container">
        <div className="spinner"></div>
        <p>Loading confirmed medicine cards and daily schedule...</p>
      </div>
    );
  }

  return (
    <div className="results-screen-root">
      {/* Top Header & Medical Disclaimer */}
      <header className="results-header">
        <div className="header-row">
          <div>
            <h1>Medication Schedule & Reminders</h1>
            <p className="subtitle">
              Confirmed prescription schedule with plain-language explanations.
            </p>
          </div>
          <button className="back-confirm-btn" onClick={onBackToConfirm}>
            ← Back to Confirm Step (Edit & Regenerate)
          </button>
        </div>

        {/* Global Safety Rule 6: Medical Advice Disclaimer */}
        <div className="disclaimer-banner">
          ⚠️ <strong>Medical Disclaimer:</strong> This is not medical advice. Always confirm with
          your doctor or pharmacist before making changes to your medication regimen.
        </div>

        {error && <div className="error-alert">Error: {error}</div>}
        {actionMessage && <div className="action-toast">{actionMessage}</div>}
      </header>

      {/* Main Content Sections */}
      <main className="results-content">
        {/* Section 1: Medicine Cards */}
        <section className="medicine-cards-section">
          <h2>Medicines Explained in Plain Language</h2>
          <div className="cards-grid">
            {matchedDrugs.map((item, idx) => {
              const matching = item.matching || {};
              const genericName = matching.generic || item.name_raw;
              const purpose =
                matching.purpose_plain || "Medication prescribed by your doctor.";
              const drugClass = matching.class || "Prescription drug";

              return (
                <div key={idx} className="drug-card">
                  <div className="drug-card-top">
                    <h3 className="drug-name">{item.name_raw}</h3>
                    {matching.generic && (
                      <span className="generic-name">({matching.generic})</span>
                    )}
                  </div>

                  <div className="drug-class-badge">{drugClass.replace("_", " ").toUpperCase()}</div>

                  <div className="purpose-box">
                    <strong>Purpose:</strong> {purpose}
                  </div>

                  <div className="card-details-row">
                    <span>
                      <strong>Strength:</strong> {item.strength || "Not specified"}
                    </span>
                    <span>
                      <strong>Frequency:</strong> {item.frequency_raw || "As directed"}
                    </span>
                    <span>
                      <strong>Duration:</strong>{" "}
                      {item.duration_days ? `${item.duration_days} days` : "As directed"}
                    </span>
                  </div>

                  {/* Phase 6: Feedback button */}
                  <div className="drug-card-footer">
                    {feedbackState[item.name_raw] === "sent" ? (
                      <span className="feedback-sent">✓ Feedback sent</span>
                    ) : (
                      <button
                        className="feedback-btn"
                        id={`feedback-btn-${idx}`}
                        disabled={feedbackState[item.name_raw] === "sending"}
                        onClick={() => handleSubmitFeedback(item.name_raw)}
                        title="Report incorrect information"
                      >
                        {feedbackState[item.name_raw] === "sending" ? "Sending…" : "⚑ This is wrong"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Section 2: Daily Timeline with Taken / Skipped Actions */}
        <section className="timeline-section">
          <div className="section-header-row">
            <h2>Today's Medication Timeline</h2>
            <div className="tz-controls">
              <label>Timezone:</label>
              <select value={selectedTz} onChange={(e) => setSelectedTz(e.target.value)}>
                <option value="Asia/Kolkata">Asia/Kolkata (IST)</option>
                <option value="UTC">UTC</option>
                <option value="America/New_York">America/New_York (EST)</option>
                <option value="Europe/London">Europe/London (GMT)</option>
              </select>
              <button className="sync-reminders-btn" onClick={handleGenerateReminders}>
                🔔 Generate Web Push Reminders
              </button>
            </div>
          </div>

          <div className="timeline-container">
            {schedule.map((slot, index) => {
              const isSuggested = slot.suggested || slot.source === "default";
              const latestLog = doseLogs.find(
                (l) => l.drug_name === slot.drug && l.scheduled_time === slot.time
              );

              return (
                <div
                  key={index}
                  className={`timeline-item ${isSuggested ? "timeline-suggested" : ""}`}
                >
                  <div className="timeline-time-badge">{slot.time}</div>

                  <div className="timeline-body">
                    <div className="timeline-drug-title">
                      <strong>{slot.drug}</strong>
                      {isSuggested && (
                        <span className="suggestion-pill">
                          Suggested Timing (No frequency in prescription)
                        </span>
                      )}
                    </div>

                    <div className="timeline-instruction">{slot.instruction}</div>

                    {/* Global Safety Rule 3: Suggestion Label */}
                    {isSuggested && (
                      <div className="suggested-disclaimer-text">
                        📌 {slot.suggestion_text || "Suggested, confirm with your doctor or pharmacist"}
                      </div>
                    )}

                    {/* Conflict Warnings */}
                    {slot.warnings && slot.warnings.length > 0 && (
                      <div className="timeline-warnings">
                        {slot.warnings.map((w, wIdx) => (
                          <div key={wIdx} className="warning-item">
                            ⚠️ {w}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Dose Actions: Taken / Skipped / Snooze */}
                    <div className="timeline-actions-row">
                      {latestLog ? (
                        <span className={`log-status-badge log-${latestLog.action}`}>
                          Status: {latestLog.action.toUpperCase()}
                        </span>
                      ) : (
                        <>
                          <button
                            className="btn-action btn-taken"
                            onClick={() => handleDoseAction(slot.drug, slot.time, "taken")}
                          >
                            ✓ Mark Taken
                          </button>
                          <button
                            className="btn-action btn-skip"
                            onClick={() => handleDoseAction(slot.drug, slot.time, "skipped")}
                          >
                            ✕ Mark Skipped
                          </button>
                          <button
                            className="btn-action btn-snooze"
                            onClick={() => handleDoseAction(slot.drug, slot.time, "snoozed", 15)}
                          >
                            ⏰ Snooze 15m
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Section 3: Web Push & Reminders Status */}
        {reminders.length > 0 && (
          <section className="reminders-section">
            <h2>Active Web Push Reminders ({reminders.length})</h2>
            <div className="reminders-table-container">
              <table className="reminders-table">
                <thead>
                  <tr>
                    <th>Medication</th>
                    <th>Scheduled Time</th>
                    <th>Fire UTC Timestamp</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {reminders.map((r) => (
                    <tr key={r.id}>
                      <td>{r.drug_name}</td>
                      <td>{r.scheduled_time}</td>
                      <td>{r.fire_at_timestamp}</td>
                      <td>
                        <span className={`rem-status-chip rem-${r.status}`}>
                          {r.status.toUpperCase()}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn-sm"
                          onClick={() => handleSnoozeReminder(r.id)}
                        >
                          Snooze (+15m)
                        </button>
                        <button
                          className="btn-sm btn-fire"
                          onClick={() => handleFireReminder(r.id)}
                        >
                          Simulate Fire (Push)
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
