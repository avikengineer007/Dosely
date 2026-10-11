import React, { useState } from 'react';
import { UploadCloud, X, FileText, CheckCircle2, AlertCircle, Lock } from 'lucide-react';

export default function UploadModal({
  isOpen,
  onClose,
  onUploadSuccess,
  jwtToken,
}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [dragActive, setDragActive] = useState(false);

  if (!isOpen) return null;

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelected = (file) => {
    // Validate size and format
    if (file.size > 20 * 1024 * 1024) {
      setErrorMessage('File exceeds maximum size limit of 20 MB.');
      return;
    }
    const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf'];
    if (!validTypes.includes(file.type)) {
      setErrorMessage('Unsupported file format. Please upload JPEG, PNG, or PDF.');
      return;
    }
    setErrorMessage('');
    setSelectedFile(file);
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setIsUploading(true);
    setErrorMessage('');

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const headers = {};
      if (jwtToken) {
        headers['Authorization'] = `Bearer ${jwtToken}`;
      }

      const response = await fetch('/prescriptions', {
        method: 'POST',
        headers,
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Upload failed with status ${response.status}`);
      }

      const result = await response.json();
      onUploadSuccess(result, selectedFile);
      onClose();
    } catch (err) {
      setErrorMessage(err.message || 'Error uploading prescription.');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.75)',
      backdropFilter: 'blur(8px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
      padding: '20px',
    }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '540px', padding: '24px', position: 'relative' }}>
        {/* Close Button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '18px',
            right: '18px',
            background: 'none',
            color: 'var(--text-muted)',
            padding: '4px',
            borderRadius: '6px',
          }}
        >
          <X size={20} />
        </button>

        <h3 style={{ fontSize: '1.25rem', fontWeight: 800, marginBottom: '6px' }}>
          Upload Prescription Document
        </h3>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '18px' }}>
          Files are encrypted at rest with AES-256 before disk storage.
        </p>

        {/* Drop Zone */}
        <div
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
          style={{
            border: `2px dashed ${dragActive ? 'var(--primary-glow)' : 'var(--border-medium)'}`,
            borderRadius: '12px',
            padding: '36px 20px',
            textAlign: 'center',
            backgroundColor: dragActive ? 'rgba(14, 165, 233, 0.08)' : 'rgba(15, 23, 42, 0.5)',
            cursor: 'pointer',
            transition: 'all 0.2s ease',
            marginBottom: '16px',
          }}
          onClick={() => document.getElementById('file-upload-input').click()}
        >
          <input
            id="file-upload-input"
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            style={{ display: 'none' }}
            onChange={(e) => e.target.files && handleFileSelected(e.target.files[0])}
          />

          <UploadCloud size={40} color="var(--primary-glow)" style={{ margin: '0 auto 12px' }} />
          <div style={{ fontSize: '0.95rem', fontWeight: 700, color: '#f8fafc', marginBottom: '4px' }}>
            {selectedFile ? selectedFile.name : 'Drag & drop prescription image or PDF here'}
          </div>
          <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            Supports JPG, PNG, WEBP, PDF up to 20 MB
          </p>
        </div>

        {/* Selected File Details */}
        {selectedFile && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 14px',
            background: 'rgba(30, 41, 59, 0.6)',
            borderRadius: '8px',
            marginBottom: '16px',
            fontSize: '0.82rem',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <FileText size={16} color="var(--primary-glow)" />
              <span style={{ fontWeight: 600 }}>{selectedFile.name}</span>
            </div>
            <span style={{ color: 'var(--text-muted)' }}>
              {(selectedFile.size / 1024).toFixed(1)} KB
            </span>
          </div>
        )}

        {/* Error Alert */}
        {errorMessage && (
          <div style={{
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'var(--rose-bg)',
            border: '1px solid var(--rose-border)',
            color: 'var(--rose-text)',
            fontSize: '0.82rem',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '16px',
          }}>
            <AlertCircle size={16} />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Action Buttons */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          <button
            onClick={onClose}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              background: 'rgba(30, 41, 59, 0.6)',
              color: 'var(--text-muted)',
              fontSize: '0.85rem',
              fontWeight: 600,
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleUpload}
            disabled={!selectedFile || isUploading}
            style={{
              padding: '8px 20px',
              borderRadius: '8px',
              background: 'var(--primary-gradient)',
              color: '#fff',
              fontSize: '0.85rem',
              fontWeight: 700,
            }}
          >
            {isUploading ? 'Encrypting & Uploading...' : 'Upload & Extract'}
          </button>
        </div>
      </div>
    </div>
  );
}
