import React from 'react';
import { ShieldCheck, Lock, Pill, UploadCloud, RefreshCw, Sparkles, CheckCircle2 } from 'lucide-react';
import { SAMPLE_PRESETS } from '../sample_rx';

export default function Header({
  activePresetId,
  onSelectPreset,
  onOpenUpload,
  jwtToken,
  prescriptionStatus,
}) {
  return (
    <header className="glass-panel" style={{ padding: '16px 24px', marginBottom: '24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
      {/* Brand & Title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        <div style={{
          width: '44px',
          height: '44px',
          borderRadius: '12px',
          background: 'var(--primary-gradient)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 0 20px rgba(14, 165, 233, 0.4)'
        }}>
          <Pill size={24} color="#ffffff" />
        </div>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <h1 style={{ fontSize: '1.4rem', fontWeight: 800, letterSpacing: '-0.02em', background: 'linear-gradient(to right, #ffffff, #94a3b8)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              Dosely
            </h1>
            <span className="badge badge-sky" style={{ fontSize: '0.7rem' }}>Clinical Studio</span>
            {prescriptionStatus === 'confirmed' ? (
              <span className="badge badge-emerald" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <CheckCircle2 size={12} /> Confirmed &amp; Scheduled
              </span>
            ) : (
              <span className="badge badge-amber" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                Pending Review
              </span>
            )}
          </div>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            Never-Guess Prescription Review, At-Rest Encryption &amp; Automated Dosing
          </p>
        </div>
      </div>

      {/* Security & Action Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        {/* Security badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(15, 23, 42, 0.6)', padding: '6px 12px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
          <div title="HIPAA-strict zero-content logging enforced" style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: '#38bdf8' }}>
            <ShieldCheck size={14} />
            <span>Zero-PHI Logs</span>
          </div>
          <span style={{ color: 'var(--border-medium)' }}>•</span>
          <div title="Images encrypted at rest via Fernet AES" style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: '#34d399' }}>
            <Lock size={14} />
            <span>Encrypted at Rest</span>
          </div>
          <span style={{ color: 'var(--border-medium)' }}>•</span>
          <div style={{ fontSize: '0.75rem', color: jwtToken ? '#a7f3d0' : '#f87171' }}>
            JWT: {jwtToken ? 'Authenticated' : 'Offline'}
          </div>
        </div>

        {/* Preset Selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <select
            value={activePresetId}
            onChange={(e) => onSelectPreset(e.target.value)}
            style={{
              padding: '8px 12px',
              fontSize: '0.85rem',
              fontWeight: 500,
              cursor: 'pointer',
              background: 'rgba(30, 41, 59, 0.8)',
              border: '1px solid var(--border-medium)',
              borderRadius: '8px',
            }}
          >
            {SAMPLE_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        {/* Upload Button */}
        <button
          onClick={onOpenUpload}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 16px',
            borderRadius: '8px',
            background: 'linear-gradient(135deg, #0284c7 0%, #2563eb 100%)',
            color: '#fff',
            fontWeight: 600,
            fontSize: '0.85rem',
            boxShadow: '0 4px 12px rgba(2, 132, 199, 0.35)',
          }}
        >
          <UploadCloud size={16} />
          <span>Upload Prescription</span>
        </button>
      </div>
    </header>
  );
}
