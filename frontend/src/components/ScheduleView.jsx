import React, { useState } from 'react';
import {
  Calendar,
  Clock,
  AlertCircle,
  CheckCircle,
  FileText,
  Printer,
  ArrowLeft,
  ShieldCheck,
  Utensils,
  Sun,
  Coffee,
  Moon,
  Sparkles,
} from 'lucide-react';

export default function ScheduleView({
  prescriptionId,
  scheduleData,
  onBackToReview,
}) {
  const [activeTab, setActiveTab] = useState('timeline'); // 'timeline' | 'summary'

  const timeline = scheduleData?.timeline || [];
  const warnings = scheduleData?.warnings_and_conflicts || [];

  const getTimeIcon = (timeStr) => {
    if (timeStr === 'as needed') return <Clock size={16} color="#fbbf24" />;
    const hour = parseInt(timeStr.split(':')[0], 10);
    if (hour < 11) return <Coffee size={16} color="#38bdf8" />;
    if (hour < 17) return <Sun size={16} color="#f59e0b" />;
    return <Moon size={16} color="#818cf8" />;
  };

  return (
    <div className="glass-panel" style={{ padding: '28px', maxWidth: '1000px', margin: '0 auto' }}>
      {/* Top Banner */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '16px',
        paddingBottom: '20px',
        borderBottom: '1px solid var(--border-subtle)',
        marginBottom: '24px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <button
            onClick={onBackToReview}
            style={{
              padding: '8px 12px',
              borderRadius: '8px',
              background: 'rgba(30, 41, 59, 0.6)',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.85rem',
              fontWeight: 600,
            }}
          >
            <ArrowLeft size={16} />
            <span>Review Studio</span>
          </button>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <h2 style={{ fontSize: '1.4rem', fontWeight: 800 }}>Daily Medication Schedule</h2>
              <span className="badge badge-emerald">Active Schedule</span>
            </div>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Prescription <span style={{ fontFamily: 'var(--font-mono)' }}>{prescriptionId}</span> • Generated post-confirmation
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={() => window.print()}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 16px',
              borderRadius: '8px',
              background: 'rgba(30, 41, 59, 0.8)',
              border: '1px solid var(--border-medium)',
              color: '#fff',
              fontSize: '0.85rem',
              fontWeight: 600,
            }}
          >
            <Printer size={15} />
            <span>Print Patient Schedule</span>
          </button>
        </div>
      </div>

      {/* Warnings & Conflicts Alert (if any) */}
      {warnings.length > 0 && (
        <div style={{
          padding: '14px 18px',
          borderRadius: '12px',
          background: 'rgba(245, 158, 11, 0.12)',
          border: '1px solid var(--amber-border)',
          marginBottom: '24px',
          display: 'flex',
          alignItems: 'flex-start',
          gap: '12px',
        }}>
          <AlertCircle size={22} color="var(--amber-text)" style={{ flexShrink: 0, marginTop: '2px' }} />
          <div>
            <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--amber-text)', marginBottom: '4px' }}>
              Clinical Timing Considerations &amp; Conflict Checks
            </div>
            <ul style={{ paddingLeft: '18px', fontSize: '0.85rem', color: '#cbd5e1', lineHeight: '1.5' }}>
              {warnings.map((w, idx) => (
                <li key={idx}>{w}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Timeline List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {timeline.map((entry, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              alignItems: 'stretch',
              gap: '16px',
              borderRadius: '12px',
              background: 'rgba(15, 23, 42, 0.6)',
              border: '1px solid var(--border-subtle)',
              overflow: 'hidden',
              transition: 'all 0.2s ease',
            }}
          >
            {/* Time Column */}
            <div style={{
              width: '120px',
              background: 'rgba(30, 41, 59, 0.5)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '16px 12px',
              borderRight: '1px solid var(--border-subtle)',
              flexShrink: 0,
            }}>
              <div style={{ marginBottom: '6px' }}>{getTimeIcon(entry.time)}</div>
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: entry.time === 'as needed' ? '0.85rem' : '1.15rem',
                fontWeight: 700,
                color: entry.time === 'as needed' ? '#fbbf24' : '#f8fafc',
                textTransform: 'uppercase',
                textAlign: 'center',
              }}>
                {entry.time}
              </div>
            </div>

            {/* Medicine & Instruction Details */}
            <div style={{ flex: 1, padding: '16px 20px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px', flexWrap: 'wrap', gap: '8px' }}>
                <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span>{entry.drug}</span>
                  {entry.drug_class && (
                    <span style={{ fontSize: '0.72rem', background: 'rgba(14, 165, 233, 0.15)', color: '#38bdf8', border: '1px solid rgba(14, 165, 233, 0.3)', padding: '2px 8px', borderRadius: '6px', fontWeight: 600 }}>
                      {entry.drug_class}
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {entry.suggested ? (
                    <span className="badge badge-amber" style={{ fontSize: '0.7rem' }}>
                      Suggested by Default Rule
                    </span>
                  ) : (
                    <span className="badge badge-sky" style={{ fontSize: '0.7rem' }}>
                      From Prescription
                    </span>
                  )}
                </div>
              </div>

              {/* Instruction Sentence */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#94a3b8', fontSize: '0.9rem' }}>
                <Utensils size={15} color="var(--primary-glow)" />
                <span style={{ color: '#e2e8f0', fontWeight: 500 }}>{entry.instruction}</span>
              </div>

              {/* Entry specific warnings */}
              {entry.warnings && entry.warnings.length > 0 && (
                <div style={{ marginTop: '8px', fontSize: '0.8rem', color: '#fbbf24', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <AlertCircle size={14} />
                  <span>{entry.warnings.join(', ')}</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Footer Info */}
      <div style={{
        marginTop: '28px',
        padding: '16px',
        borderRadius: '10px',
        background: 'rgba(15, 23, 42, 0.4)',
        border: '1px solid var(--border-subtle)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '12px',
        fontSize: '0.8rem',
        color: 'var(--text-muted)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ShieldCheck size={16} color="#34d399" />
          <span>Deterministic scheduling produced strictly after human clinician confirmation.</span>
        </div>
        <div>
          Timetable Anchors: Breakfast 08:00 • Lunch 13:00 • Dinner 20:00 • Sleep 22:30
        </div>
      </div>
    </div>
  );
}
