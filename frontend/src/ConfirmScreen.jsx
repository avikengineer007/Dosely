import React, { useState, useEffect } from "react";
import "./ConfirmScreen.css";

/**
 * Matra — Prescription Assistant Confirm Screen
 * 
 * Rules:
 * 1. Original image beside extracted fields (side-by-side view).
 * 2. needs_review fields highlighted in amber.
 * 3. Confirm button remains disabled until every highlighted/review field
 *    is edited or explicitly accepted by the user.
 * 4. Shows prominent medical disclaimer: "This is not medical advice."
 * 5. Matching & scheduling run ONLY after confirmation via PUT /prescriptions/:id/confirm.
 */
export default function ConfirmScreen({
  prescriptionId,
  apiBaseUrl = "http://localhost:8000",
  authToken,
  onConfirmed,
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [prescription, setPrescription] = useState(null);
  const [medicines, setMedicines] = useState([]);
  const [imageUrl, setImageUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Fetch prescription details and image
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        setError(null);

        // Fetch prescription metadata
        const rxRes = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}`, {
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
        });

        if (!rxRes.ok) {
          throw new Error(`Failed to load prescription (HTTP ${rxRes.status})`);
        }

        const rxData = await rxRes.json();
        setPrescription(rxData);

        const initialMeds = (rxData.extracted_data?.medicines || []).map((m, idx) => ({
          id: `med-${idx}`,
          name_raw: m.name_raw || "",
          name_generic: m.name_generic || "",
          strength: m.strength || "",
          form: m.form || "unknown",
          frequency_raw: m.frequency_raw || "",
          duration_days: m.duration_days || "",
          food_instruction: m.food_instruction || "unknown",
          confidence: m.confidence || 0,
          needs_review: Boolean(m.needs_review),
          review_reasons: m.review_reasons || [],
          // Review state
          user_accepted: false,
          user_edited: false,
        }));

        setMedicines(initialMeds);
        setImageUrl(`${apiBaseUrl}/prescriptions/${prescriptionId}/image`);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }

    if (prescriptionId) {
      loadData();
    }
  }, [prescriptionId, apiBaseUrl, authToken]);

  // Handle field edits
  const handleFieldChange = (id, field, value) => {
    setMedicines((prev) =>
      prev.map((med) => {
        if (med.id !== id) return med;
        return {
          ...med,
          [field]: value,
          user_edited: true, // Marked edited
        };
      })
    );
  };

  // Toggle explicit user acceptance of flagged medicine
  const handleToggleAccept = (id) => {
    setMedicines((prev) =>
      prev.map((med) => {
        if (med.id !== id) return med;
        return {
          ...med,
          user_accepted: !med.user_accepted,
        };
      })
    );
  };

  // Verify whether all needs_review medicines are resolved (edited or accepted)
  const unconfirmedCount = medicines.filter(
    (m) => m.needs_review && !m.user_accepted && !m.user_edited
  ).length;

  const canConfirm = medicines.length > 0 && unconfirmedCount === 0 && !submitting;

  // Submit confirmation
  const handleConfirm = async () => {
    if (!canConfirm) return;

    try {
      setSubmitting(true);
      setError(null);

      const payload = {
        medicines: medicines.map((m) => ({
          name_raw: m.name_raw,
          name_generic: m.name_generic || null,
          strength: m.strength || null,
          form: m.form,
          frequency_raw: m.frequency_raw || null,
          duration_days: m.duration_days ? parseInt(m.duration_days, 10) : null,
          food_instruction: m.food_instruction,
          user_accepted: true,
        })),
      };

      const res = await fetch(`${apiBaseUrl}/prescriptions/${prescriptionId}/confirm`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Confirmation failed with status ${res.status}`);
      }

      const result = await res.json();
      if (onConfirmed) {
        onConfirmed(result);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="confirm-loading-container">
        <div className="spinner"></div>
        <p>Loading prescription image and extraction data...</p>
      </div>
    );
  }

  return (
    <div className="confirm-screen-root">
      {/* Top Banner: Medical Advice Disclaimer */}
      <header className="confirm-header">
        <div className="title-row">
          <h1>Confirm Extracted Prescription</h1>
          <span className="badge-status">
            {prescription?.status === "confirmed" ? "Confirmed" : "Action Required"}
          </span>
        </div>
        <div className="disclaimer-banner">
          ⚠️ <strong>Notice:</strong> This is not medical advice. Always verify extracted medicine
          names, dosages, and schedules with a qualified doctor or pharmacist.
        </div>
        {error && <div className="error-alert">Error: {error}</div>}
      </header>

      {/* Main Split-Screen View */}
      <div className="confirm-split-container">
        {/* Left Side: Original Prescription Image */}
        <section className="left-panel">
          <div className="panel-title">Original Prescription</div>
          <div className="image-viewer-card">
            {imageUrl ? (
              <img
                src={imageUrl}
                alt="Original prescription"
                className="prescription-preview-img"
              />
            ) : (
              <div className="no-image">No preview available</div>
            )}
          </div>
          <div className="image-quality-note">
            Image Quality Assessment:{" "}
            <strong>{prescription?.image_quality?.toUpperCase() || "UNKNOWN"}</strong>
          </div>
        </section>

        {/* Right Side: Extracted Fields Review & Edit */}
        <section className="right-panel">
          <div className="panel-title">
            Extracted Medicines ({medicines.length})
            {unconfirmedCount > 0 && (
              <span className="amber-pill">
                {unconfirmedCount} need{unconfirmedCount === 1 ? "s" : ""} review
              </span>
            )}
          </div>

          <div className="medicines-list">
            {medicines.map((med, index) => {
              const isFlagged = med.needs_review;
              const isResolved = med.user_accepted || med.user_edited;

              return (
                <div
                  key={med.id}
                  className={`medicine-card ${isFlagged && !isResolved ? "amber-flagged" : ""}`}
                >
                  <div className="medicine-card-header">
                    <span className="med-number">#{index + 1}</span>
                    {isFlagged && (
                      <span className={`review-badge ${isResolved ? "badge-resolved" : "badge-amber"}`}>
                        {isResolved ? "✓ Addressed" : "⚠️ Needs Review"}
                      </span>
                    )}
                    <span className="confidence-chip">
                      Confidence: {Math.round(med.confidence * 100)}%
                    </span>
                  </div>

                  {/* Amber alert reasons */}
                  {isFlagged && (
                    <div className="amber-alert-box">
                      <strong>Reasons for review:</strong>
                      <ul>
                        {med.review_reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Editable input fields */}
                  <div className="field-grid">
                    <div className="field-group">
                      <label>Medicine Name (as read):</label>
                      <input
                        type="text"
                        value={med.name_raw}
                        onChange={(e) => handleFieldChange(med.id, "name_raw", e.target.value)}
                        className={isFlagged && !med.name_raw ? "input-amber" : ""}
                      />
                    </div>

                    <div className="field-group">
                      <label>Strength / Dose:</label>
                      <input
                        type="text"
                        placeholder="e.g. 500 mg, 40 mg"
                        value={med.strength}
                        onChange={(e) => handleFieldChange(med.id, "strength", e.target.value)}
                        className={isFlagged && !med.strength ? "input-amber" : ""}
                      />
                    </div>

                    <div className="field-group">
                      <label>Frequency (e.g. 1-0-1, OD, BD, TDS):</label>
                      <input
                        type="text"
                        placeholder="e.g. BD, 1-0-1"
                        value={med.frequency_raw}
                        onChange={(e) => handleFieldChange(med.id, "frequency_raw", e.target.value)}
                        className={isFlagged && !med.frequency_raw ? "input-amber" : ""}
                      />
                    </div>

                    <div className="field-group">
                      <label>Duration (days):</label>
                      <input
                        type="number"
                        min="1"
                        placeholder="e.g. 5"
                        value={med.duration_days}
                        onChange={(e) => handleFieldChange(med.id, "duration_days", e.target.value)}
                      />
                    </div>

                    <div className="field-group">
                      <label>Food Instruction:</label>
                      <select
                        value={med.food_instruction}
                        onChange={(e) => handleFieldChange(med.id, "food_instruction", e.target.value)}
                      >
                        <option value="unknown">Unknown</option>
                        <option value="before_food">Before food (30m)</option>
                        <option value="after_food">After food (30m)</option>
                        <option value="with_food">With food</option>
                        <option value="empty_stomach">Empty stomach</option>
                        <option value="any_time">Any time</option>
                      </select>
                    </div>
                  </div>

                  {/* Flagged Item Action: Explicit Acceptance */}
                  {isFlagged && (
                    <div className="accept-action-row">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={med.user_accepted}
                          onChange={() => handleToggleAccept(med.id)}
                        />
                        <span>I have verified and explicitly accept these values as correct</span>
                      </label>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Confirm Button Footer */}
          <footer className="confirm-footer">
            <div className="confirm-status-text">
              {!canConfirm ? (
                <span className="status-blocked">
                  ⚠️ Please edit or check "I have verified and explicitly accept" for all amber
                  highlighted items before confirming.
                </span>
              ) : (
                <span className="status-ready">
                  ✓ All fields verified. You can now confirm to generate your schedule.
                </span>
              )}
            </div>

            <button
              className="confirm-btn"
              disabled={!canConfirm}
              onClick={handleConfirm}
            >
              {submitting ? "Confirming & Scheduling..." : "Confirm & Generate Schedule"}
            </button>
          </footer>
        </section>
      </div>
    </div>
  );
}
