import React, { useState, useEffect } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Lock,
  ArrowRight,
  Edit3,
  Clock,
  Sparkles,
  Info,
  ChevronDown,
  ChevronUp,
  ShieldAlert,
  Utensils
} from 'lucide-react';

export default function ConfirmationStudio({
  prescription,
  onConfirm,
  isConfirming,
}) {
  // Local editable copy of medicines
  const [medicines, setMedicines] = useState([]);
  // Track resolution state for each medicine id: { [id]: { isEdited: bool, isAccepted: bool } }
  const [resolutionState, setResolutionState] = useState({});
  const [patientName, setPatientName] = useState('');
  const [prescriptionDate, setPrescriptionDate] = useState('');
  const [notes, setNotes] = useState('');

  // Sync state whenever input prescription changes
  useEffect(() => {
    if (!prescription) return;
    const initialMeds = (prescription.medicines || []).map((m, idx) => ({
      ...m,
      localId: m.id || `med_${idx}`,
      originalValues: { ...m },
    }));
    setMedicines(initialMeds);
    setPatientName(prescription.patient_name || '');
    setPrescriptionDate(prescription.prescription_date || '');

    const initialResolutions = {};
    initialMeds.forEach((m) => {
      // If needs_review is false from OCR, it's pre-cleared
      initialResolutions[m.localId] = {
        isEdited: false,
        isAccepted: !m.needs_review,
      };
    });
    setResolutionState(initialResolutions);
  }, [prescription]);

  // Handle field change
  const handleFieldChange = (localId, field, value) => {
    setMedicines((prev) =>
      prev.map((med) => {
        if (med.localId !== localId) return med;
        const updated = { ...med, [field]: value };
        return updated;
      })
    );

    // Mark as edited
    setResolutionState((prev) => ({
      ...prev,
      [localId]: {
        ...prev[localId],
        isEdited: true,
      },
    }));
  };

  // Toggle explicit acceptance
  const handleToggleAcceptance = (localId) => {
    setResolutionState((prev) => {
      const current = prev[localId] || {};
      return {
        ...prev,
        [localId]: {
          ...current,
          isAccepted: !current.isAccepted,
        },
      };
    });
  };

  // Determine if a medicine is resolved
  const isMedResolved = (med) => {
    if (!med.needs_review) return true;
    const state = resolutionState[med.localId];
    if (!state) return false;
    return state.isEdited || state.isAccepted;
  };

  // Count unaddressed review fields
  const flaggedMeds = medicines.filter((m) => m.needs_review);
  const unresolvedMeds = flaggedMeds.filter((m) => !isMedResolved(m));
  const unresolvedCount = unresolvedMeds.length;
  const isConfirmBlocked = unresolvedCount > 0;

  const handleConfirmClick = () => {
    if (isConfirmBlocked || isConfirming) return;

    // Build payload of verified items
    const payload = {
      medicines: medicines.map((m) => {
        const state = resolutionState[m.localId] || {};
        return {
          name_raw: m.name_raw,
          name_generic: m.name_generic || m.name_raw,
          drug_class: m.drug_class || 'unknown',
          purpose: m.purpose || '',
          strength: m.strength || '',
          form: m.form || 'tablet',
          frequency_raw: m.frequency_raw || '',
          frequency_normalized: m.frequency_normalized || m.frequency_raw || '',
          timing_slots: m.timing_slots || [],
          duration_days: m.duration_days ? parseInt(m.duration_days, 10) : 5,
          food_instruction: m.food_instruction || 'after_food',
          special_instructions: m.special_instructions || '',
          user_reviewed: true,
          user_accepted: Boolean(state.isAccepted || state.isEdited),
        };
      }),
      patient_name: patientName,
      prescription_date: prescriptionDate,
      notes: notes,
    };

    onConfirm(payload);
  };

  return (
    <div className="glass-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Studio Header */}
      <div style={{
        padding: '16px 20px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'rgba(15, 23, 42, 0.5)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <div>
            <span style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Confirmation &amp; Clinical Safety Gate
            </span>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 800 }}>Extracted Prescription Data</h2>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="badge badge-sky">
              Total Drugs: {medicines.length}
            </span>
            {flaggedMeds.length > 0 && (
              <span className={`badge ${unresolvedCount > 0 ? 'badge-amber animate-pulse-amber' : 'badge-emerald'}`}>
                {unresolvedCount > 0 ? `⚠️ ${unresolvedCount} Pending Review` : '✓ All Reviewed'}
              </span>
            )}
          </div>
        </div>

        {/* Patient / Metadata Bar */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: '10px',
          background: 'rgba(15, 23, 42, 0.6)',
          padding: '10px 14px',
          borderRadius: '10px',
          border: '1px solid var(--border-subtle)',
          fontSize: '0.82rem',
        }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>PATIENT NAME</label>
            <input
              type="text"
              value={patientName}
              onChange={(e) => setPatientName(e.target.value)}
              placeholder="e.g. Rahul Verma"
              style={{ width: '100%', padding: '4px 8px', marginTop: '3px' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>PRESCRIPTION DATE</label>
            <input
              type="text"
              value={prescriptionDate}
              onChange={(e) => setPrescriptionDate(e.target.value)}
              placeholder="YYYY-MM-DD"
              style={{ width: '100%', padding: '4px 8px', marginTop: '3px' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>PRESCRIBING PHYSICIAN</label>
            <div style={{ padding: '6px 8px', color: '#cbd5e1', fontWeight: 600 }}>
              {prescription?.prescriber?.name || 'Dr. S. K. Gupta, MD'}
            </div>
          </div>
        </div>
      </div>

      {/* Amber Review Alert / Safety Banner */}
      {flaggedMeds.length > 0 && (
        <div style={{
          padding: '12px 20px',
          background: unresolvedCount > 0 ? 'rgba(245, 158, 11, 0.14)' : 'rgba(16, 185, 129, 0.1)',
          borderBottom: `1px solid ${unresolvedCount > 0 ? 'var(--amber-border)' : 'var(--emerald-border)'}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '10px',
          transition: 'all 0.3s ease',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {unresolvedCount > 0 ? (
              <AlertTriangle size={20} color="var(--amber-text)" />
            ) : (
              <CheckCircle2 size={20} color="var(--emerald-text)" />
            )}
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 700, color: unresolvedCount > 0 ? 'var(--amber-text)' : 'var(--emerald-text)' }}>
                {unresolvedCount > 0
                  ? `Safety Review Required (${unresolvedCount} remaining)`
                  : 'All flagged items verified and resolved!'}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                {unresolvedCount > 0
                  ? "Rule: Confirmation is blocked until every highlighted field is edited or explicitly accepted."
                  : "All Never-Guess flags satisfied. The Confirm button is unlocked."}
              </div>
            </div>
          </div>

          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: unresolvedCount > 0 ? 'var(--amber-text)' : 'var(--emerald-text)' }}>
            {flaggedMeds.length - unresolvedCount} of {flaggedMeds.length} Flags Resolved
          </div>
        </div>
      )}

      {/* Medicines List (Scrollable) */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {medicines.map((med, index) => {
          const isFlagged = med.needs_review;
          const resolved = isMedResolved(med);
          const resState = resolutionState[med.localId] || {};

          return (
            <div
              key={med.localId}
              style={{
                borderRadius: '12px',
                padding: '16px',
                background: isFlagged && !resolved
                  ? 'rgba(30, 24, 15, 0.7)'
                  : 'rgba(17, 24, 39, 0.65)',
                border: isFlagged
                  ? (!resolved
                      ? '1.5px solid var(--amber-border)'
                      : '1.5px solid var(--emerald-border)')
                  : '1px solid var(--border-subtle)',
                boxShadow: isFlagged && !resolved
                  ? '0 0 20px rgba(245, 158, 11, 0.2)'
                  : 'none',
                transition: 'all 0.25s ease',
              }}
            >
              {/* Card Header */}
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px', flexWrap: 'wrap', gap: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div style={{
                    width: '28px',
                    height: '28px',
                    borderRadius: '8px',
                    background: isFlagged ? (!resolved ? 'var(--amber-bg)' : 'var(--emerald-bg)') : 'var(--sky-bg)',
                    border: `1px solid ${isFlagged ? (!resolved ? 'var(--amber-border)' : 'var(--emerald-border)') : 'var(--sky-border)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '0.8rem',
                    fontWeight: 700,
                    color: isFlagged ? (!resolved ? 'var(--amber-text)' : 'var(--emerald-text)') : 'var(--sky-text)',
                  }}>
                    {index + 1}
                  </div>
                  <div>
                    <div style={{ fontSize: '1rem', fontWeight: 700, color: '#f8fafc' }}>
                      {med.name_raw}
                    </div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                      Generic: <strong style={{ color: '#38bdf8' }}>{med.name_generic || 'Unresolved'}</strong> • Class: <span style={{ textTransform: 'capitalize' }}>{med.drug_class || 'Unknown'}</span>
                    </div>
                  </div>
                </div>

                {/* Status Badges */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {isFlagged ? (
                    !resolved ? (
                      <span className="badge badge-amber" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <AlertTriangle size={12} /> Needs Review
                      </span>
                    ) : (
                      <span className="badge badge-emerald" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <CheckCircle2 size={12} />
                        {resState.isEdited ? 'Resolved (Edited)' : 'Resolved (Accepted)'}
                      </span>
                    )
                  ) : (
                    <span className="badge badge-sky" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <CheckCircle2 size={12} /> Verified (OCR {Math.round((med.confidence || 0.95) * 100)}%)
                    </span>
                  )}
                </div>
              </div>

              {/* Review Reasons Banner (if flagged) */}
              {isFlagged && (
                <div style={{
                  padding: '8px 12px',
                  borderRadius: '8px',
                  background: !resolved ? 'rgba(245, 158, 11, 0.15)' : 'rgba(16, 185, 129, 0.1)',
                  border: `1px solid ${!resolved ? 'rgba(245, 158, 11, 0.3)' : 'rgba(16, 185, 129, 0.2)'}`,
                  marginBottom: '14px',
                  fontSize: '0.8rem',
                }}>
                  <div style={{ fontWeight: 700, color: !resolved ? 'var(--amber-text)' : 'var(--emerald-text)', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Info size={14} />
                    <span>Flag Reasons Under Never-Guess Protocol:</span>
                  </div>
                  <ul style={{ paddingLeft: '20px', color: '#cbd5e1', lineHeight: '1.4' }}>
                    {(med.review_reasons && med.review_reasons.length > 0
                      ? med.review_reasons
                      : ['Low confidence or missing critical dosing fields.']
                    ).map((reason, rIdx) => (
                      <li key={rIdx}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Editable Fields Grid */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
                {/* Drug Name / Generic */}
                <div style={{ gridColumn: 'span 2' }}>
                  <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>
                    DRUG / BRAND NAME
                  </label>
                  <input
                    type="text"
                    value={med.name_raw || ''}
                    onChange={(e) => handleFieldChange(med.localId, 'name_raw', e.target.value)}
                    style={{ width: '100%', marginTop: '3px' }}
                  />
                </div>

                {/* Strength */}
                <div>
                  <label style={{
                    display: 'block',
                    fontSize: '0.72rem',
                    color: !med.strength && isFlagged ? 'var(--amber-text)' : 'var(--text-dim)',
                    fontWeight: 600
                  }}>
                    STRENGTH {!med.strength && isFlagged && '⚠️'}
                  </label>
                  <input
                    type="text"
                    value={med.strength || ''}
                    placeholder="e.g. 500 mg"
                    onChange={(e) => handleFieldChange(med.localId, 'strength', e.target.value)}
                    style={{
                      width: '100%',
                      marginTop: '3px',
                      borderColor: !med.strength && isFlagged && !resolved ? 'var(--amber-border)' : undefined,
                      backgroundColor: !med.strength && isFlagged && !resolved ? 'rgba(245, 158, 11, 0.08)' : undefined,
                    }}
                  />
                </div>

                {/* Dose Form */}
                <div>
                  <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>FORM</label>
                  <select
                    value={med.form || 'tablet'}
                    onChange={(e) => handleFieldChange(med.localId, 'form', e.target.value)}
                    style={{ width: '100%', marginTop: '3px' }}
                  >
                    <option value="tablet">Tablet</option>
                    <option value="capsule">Capsule</option>
                    <option value="syrup">Syrup</option>
                    <option value="injection">Injection</option>
                    <option value="drops">Drops</option>
                    <option value="ointment">Ointment</option>
                  </select>
                </div>

                {/* Frequency */}
                <div>
                  <label style={{
                    display: 'block',
                    fontSize: '0.72rem',
                    color: !med.frequency_raw && isFlagged ? 'var(--amber-text)' : 'var(--text-dim)',
                    fontWeight: 600
                  }}>
                    FREQUENCY {!med.frequency_raw && isFlagged && '⚠️'}
                  </label>
                  <input
                    type="text"
                    value={med.frequency_raw || ''}
                    placeholder="e.g. TDS, BD, OD"
                    onChange={(e) => handleFieldChange(med.localId, 'frequency_raw', e.target.value)}
                    style={{
                      width: '100%',
                      marginTop: '3px',
                      borderColor: !med.frequency_raw && isFlagged && !resolved ? 'var(--amber-border)' : undefined,
                      backgroundColor: !med.frequency_raw && isFlagged && !resolved ? 'rgba(245, 158, 11, 0.08)' : undefined,
                    }}
                  />
                </div>

                {/* Food Relation */}
                <div>
                  <label style={{ display: 'block', fontSize: '0.72rem', color: 'var(--text-dim)', fontWeight: 600 }}>FOOD INSTRUCTION</label>
                  <select
                    value={med.food_instruction || 'after_food'}
                    onChange={(e) => handleFieldChange(med.localId, 'food_instruction', e.target.value)}
                    style={{ width: '100%', marginTop: '3px' }}
                  >
                    <option value="after_food">After Food (PC)</option>
                    <option value="before_food">Before Food (AC)</option>
                    <option value="with_food">With Food</option>
                    <option value="empty_stomach">Empty Stomach</option>
                    <option value="any_time">Any Time</option>
                  </select>
                </div>

                {/* Duration */}
                <div>
                  <label style={{
                    display: 'block',
                    fontSize: '0.72rem',
                    color: !med.duration_days && isFlagged ? 'var(--amber-text)' : 'var(--text-dim)',
                    fontWeight: 600
                  }}>
                    DURATION (DAYS) {!med.duration_days && isFlagged && '⚠️'}
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={med.duration_days ?? ''}
                    placeholder="e.g. 5"
                    onChange={(e) => handleFieldChange(med.localId, 'duration_days', e.target.value)}
                    style={{
                      width: '100%',
                      marginTop: '3px',
                      borderColor: !med.duration_days && isFlagged && !resolved ? 'var(--amber-border)' : undefined,
                      backgroundColor: !med.duration_days && isFlagged && !resolved ? 'rgba(245, 158, 11, 0.08)' : undefined,
                    }}
                  />
                </div>
              </div>

              {/* Resolution Controls for Flagged items */}
              {isFlagged && (
                <div style={{
                  marginTop: '14px',
                  paddingTop: '12px',
                  borderTop: '1px solid var(--border-subtle)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexWrap: 'wrap',
                  gap: '10px',
                }}>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    Action required: Edit values above, or explicitly accept extracted text:
                  </div>

                  <button
                    onClick={() => handleToggleAcceptance(med.localId)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '6px 14px',
                      borderRadius: '8px',
                      fontSize: '0.8rem',
                      fontWeight: 600,
                      background: resState.isAccepted
                        ? 'rgba(16, 185, 129, 0.2)'
                        : 'rgba(245, 158, 11, 0.15)',
                      border: `1px solid ${resState.isAccepted ? 'var(--emerald-border)' : 'var(--amber-border)'}`,
                      color: resState.isAccepted ? 'var(--emerald-text)' : 'var(--amber-text)',
                    }}
                  >
                    <CheckCircle2 size={14} />
                    <span>{resState.isAccepted ? 'Accepted As-Is' : 'Explicitly Accept As-Is'}</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Confirmation Action Footer */}
      <div style={{
        padding: '16px 20px',
        borderTop: '1px solid var(--border-subtle)',
        background: 'rgba(15, 23, 42, 0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '12px',
      }}>
        {/* Helper info */}
        <div>
          {isConfirmBlocked ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--amber-text)', fontSize: '0.85rem', fontWeight: 600 }}>
              <Lock size={16} />
              <span>Button blocked: {unresolvedCount} highlighted field{unresolvedCount > 1 ? 's' : ''} require edit or acceptance</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--emerald-text)', fontSize: '0.85rem', fontWeight: 600 }}>
              <CheckCircle2 size={16} />
              <span>All fields verified! Ready to run drug matching &amp; scheduling.</span>
            </div>
          )}
          <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: '2px' }}>
            Schedule engine will strictly run after confirmation.
          </div>
        </div>

        {/* The CONFIRM BUTTON */}
        <button
          onClick={handleConfirmClick}
          disabled={isConfirmBlocked || isConfirming}
          className={!isConfirmBlocked ? 'animate-pulse-ready' : ''}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '12px 28px',
            borderRadius: '10px',
            fontSize: '0.95rem',
            fontWeight: 700,
            background: isConfirmBlocked
              ? 'rgba(71, 85, 105, 0.4)'
              : 'var(--primary-gradient)',
            color: isConfirmBlocked ? 'var(--text-dim)' : '#ffffff',
            boxShadow: isConfirmBlocked
              ? 'none'
              : '0 4px 20px rgba(2, 132, 199, 0.45)',
            border: isConfirmBlocked
              ? '1px solid rgba(255, 255, 255, 0.05)'
              : '1px solid rgba(255, 255, 255, 0.2)',
          }}
        >
          {isConfirming ? (
            <span>Running Matching &amp; Scheduling...</span>
          ) : isConfirmBlocked ? (
            <>
              <Lock size={16} />
              <span>Confirm Blocked ({unresolvedCount} Left)</span>
            </>
          ) : (
            <>
              <span>Confirm &amp; Run Scheduling</span>
              <ArrowRight size={18} />
            </>
          )}
        </button>
      </div>
    </div>
  );
}
