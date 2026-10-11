import React, { useState, useEffect } from 'react';
import confetti from 'canvas-confetti';
import Header from './components/Header';
import PrescriptionViewer from './components/PrescriptionViewer';
import ConfirmationStudio from './components/ConfirmationStudio';
import ScheduleView from './components/ScheduleView';
import UploadModal from './components/UploadModal';
import { SAMPLE_PRESETS } from './sample_rx';

export default function App() {
  const [activePresetId, setActivePresetId] = useState(SAMPLE_PRESETS[0].id);
  const [currentPrescription, setCurrentPrescription] = useState(SAMPLE_PRESETS[0]);
  const [currentImageSrc, setCurrentImageSrc] = useState(SAMPLE_PRESETS[0].image_data);
  const [viewMode, setViewMode] = useState('review'); // 'review' | 'schedule'
  const [scheduleData, setScheduleData] = useState(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [jwtToken, setJwtToken] = useState('');
  const [prescriptionStatus, setPrescriptionStatus] = useState('pending_confirmation');
  const [notification, setNotification] = useState(null);

  // Authenticate with backend on mount
  useEffect(() => {
    async function initAuth() {
      try {
        const res = await fetch('/auth/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'clinician_reviewer' }),
        });
        if (res.ok) {
          const data = await res.json();
          setJwtToken(data.access_token);
        }
      } catch (err) {
        console.warn('Backend offline or starting up; using local JWT session fallback.', err);
        setJwtToken('demo-mock-jwt-token');
      }
    }
    initAuth();
  }, []);

  const showNotification = (msg, type = 'info') => {
    setNotification({ msg, type });
    setTimeout(() => setNotification(null), 4000);
  };

  // Change preset
  const handleSelectPreset = (presetId) => {
    const selected = SAMPLE_PRESETS.find((p) => p.id === presetId);
    if (!selected) return;
    setActivePresetId(presetId);
    setCurrentPrescription(selected);
    setCurrentImageSrc(selected.image_data);
    setViewMode('review');
    setScheduleData(null);
    setPrescriptionStatus('pending_confirmation');
    showNotification(`Loaded preset: ${selected.label}`, 'info');
  };

  // Upload handler
  const handleUploadSuccess = (apiResult, file) => {
    const objectUrl = URL.createObjectURL(file);
    setCurrentImageSrc(objectUrl);
    setCurrentPrescription({
      prescription_id: apiResult.prescription_id,
      patient_name: apiResult.patient_name || 'Patient',
      prescription_date: apiResult.prescription_date || new Date().toISOString().split('T')[0],
      image_quality: apiResult.image_quality || 'good',
      prescriber: apiResult.prescriber || { name: 'Dr. Physician' },
      medicines: apiResult.medicines || [],
      needs_human_review: apiResult.needs_human_review,
      review_summary: apiResult.review_summary || [],
    });
    setViewMode('review');
    setScheduleData(null);
    setPrescriptionStatus('pending_confirmation');
    showNotification('Prescription uploaded & encrypted at rest successfully!', 'success');
  };

  // Confirmation Action:
  // "Only after confirmation should matching and scheduling run."
  const handleConfirmPrescription = async (confirmedPayload) => {
    setIsConfirming(true);
    const rxId = currentPrescription.prescription_id;

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (jwtToken) {
        headers['Authorization'] = `Bearer ${jwtToken}`;
      }

      // Step 1: Send PUT /prescriptions/{id}/confirm
      let confirmRes = await fetch(`/prescriptions/${rxId}/confirm`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(confirmedPayload),
      });

      // If prescription ID was not found on remote backend (e.g. preset was local), create it first
      if (confirmRes.status === 404) {
        // Upload preset SVG to backend so backend has record
        const svgBlob = new Blob([currentImageSrc], { type: 'image/png' });
        const formData = new FormData();
        formData.append('file', svgBlob, `${rxId}.png`);
        formData.append('prescription_id', rxId);

        await fetch('/prescriptions', {
          method: 'POST',
          headers: jwtToken ? { Authorization: `Bearer ${jwtToken}` } : {},
          body: formData,
        });

        // Retry confirmation
        confirmRes = await fetch(`/prescriptions/${rxId}/confirm`, {
          method: 'PUT',
          headers,
          body: JSON.stringify(confirmedPayload),
        });
      }

      if (!confirmRes.ok) {
        const errJson = await confirmRes.json().catch(() => ({}));
        throw new Error(errJson.detail || 'Confirmation failed');
      }

      // Step 2: Now that confirmation succeeded, fetch GET /prescriptions/{id}/schedule
      // (Satisfies the confirm-before-schedule rule!)
      const schedRes = await fetch(`/prescriptions/${rxId}/schedule`, {
        method: 'GET',
        headers,
      });

      if (!schedRes.ok) {
        const errJson = await schedRes.json().catch(() => ({}));
        throw new Error(errJson.detail || 'Failed to retrieve schedule');
      }

      const scheduleJson = await schedRes.json();
      setScheduleData(scheduleJson);
      setPrescriptionStatus('confirmed');
      setViewMode('schedule');

      // Trigger celebration
      confetti({
        particleCount: 80,
        spread: 70,
        origin: { y: 0.6 },
        colors: ['#0284c7', '#38bdf8', '#34d399', '#fbbf24'],
      });

      showNotification('Prescription confirmed! Medication timeline generated.', 'success');
    } catch (err) {
      console.error('Confirmation error:', err);
      showNotification(`Error: ${err.message}`, 'error');
    } finally {
      setIsConfirming(false);
    }
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1680px', margin: '0 auto' }}>
      {/* Toast Notification */}
      {notification && (
        <div style={{
          position: 'fixed',
          top: '20px',
          right: '20px',
          zIndex: 9999,
          padding: '12px 20px',
          borderRadius: '10px',
          background: notification.type === 'error'
            ? 'rgba(239, 68, 68, 0.95)'
            : notification.type === 'success'
            ? 'rgba(16, 185, 129, 0.95)'
            : 'rgba(14, 165, 233, 0.95)',
          color: '#ffffff',
          fontWeight: 600,
          fontSize: '0.88rem',
          boxShadow: '0 10px 25px rgba(0,0,0,0.4)',
          backdropFilter: 'blur(8px)',
        }}>
          {notification.msg}
        </div>
      )}

      {/* Main Header */}
      <Header
        activePresetId={activePresetId}
        onSelectPreset={handleSelectPreset}
        onOpenUpload={() => setIsUploadOpen(true)}
        jwtToken={jwtToken}
        prescriptionStatus={prescriptionStatus}
      />

      {/* Upload Modal */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onUploadSuccess={handleUploadSuccess}
        jwtToken={jwtToken}
      />

      {/* Main Content Area */}
      {viewMode === 'review' ? (
        <main style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(420px, 1fr) minmax(520px, 1.2fr)',
          gap: '24px',
          alignItems: 'start',
        }}>
          {/* Left Column: Original Prescription Image Viewer */}
          <div style={{ position: 'sticky', top: '24px' }}>
            <PrescriptionViewer
              imageUrl={currentImageSrc}
              imageQuality={currentPrescription?.image_quality}
              prescriptionId={currentPrescription?.prescription_id}
            />
          </div>

          {/* Right Column: Extracted Fields & Confirmation Studio */}
          <div>
            <ConfirmationStudio
              prescription={currentPrescription}
              onConfirm={handleConfirmPrescription}
              isConfirming={isConfirming}
            />
          </div>
        </main>
      ) : (
        /* Post-Confirmation Schedule View */
        <ScheduleView
          prescriptionId={currentPrescription?.prescription_id}
          scheduleData={scheduleData}
          onBackToReview={() => setViewMode('review')}
        />
      )}
    </div>
  );
}
