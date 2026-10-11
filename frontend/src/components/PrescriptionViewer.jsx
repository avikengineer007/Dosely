import React, { useState, useRef } from 'react';
import { ZoomIn, ZoomOut, RotateCw, RefreshCw, Eye, Lock, FileText, Sparkles, Contrast } from 'lucide-react';

export default function PrescriptionViewer({
  imageUrl,
  imageQuality = "good",
  prescriptionId,
  fileName = "prescription.png",
}) {
  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [highContrast, setHighContrast] = useState(false);

  const containerRef = useRef(null);

  const handleZoomIn = () => setScale((prev) => Math.min(prev + 0.25, 3.5));
  const handleZoomOut = () => setScale((prev) => Math.max(prev - 0.25, 0.5));
  const handleRotate = () => setRotation((prev) => (prev + 90) % 360);
  const handleReset = () => {
    setScale(1);
    setRotation(0);
    setPosition({ x: 0, y: 0 });
    setHighContrast(false);
  };

  const handleMouseDown = (e) => {
    if (scale <= 1) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e) => {
    if (!isDragging) return;
    setPosition({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y,
    });
  };

  const handleMouseUp = () => setIsDragging(false);

  const getQualityBadge = () => {
    const q = imageQuality.toLowerCase();
    if (q === 'good') {
      return <span className="badge badge-emerald">Quality: Good</span>;
    }
    if (q === 'fair') {
      return <span className="badge badge-amber">Quality: Fair (Review Advised)</span>;
    }
    return <span className="badge badge-rose">Quality: Poor (Low OCR Variance)</span>;
  };

  return (
    <div className="glass-panel" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Top Bar / Inspector Controls */}
      <div style={{
        padding: '12px 18px',
        borderBottom: '1px solid var(--border-subtle)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '8px',
        background: 'rgba(15, 23, 42, 0.4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <FileText size={18} color="var(--primary-glow)" />
          <div>
            <div style={{ fontSize: '0.88rem', fontWeight: 700, color: '#f8fafc' }}>
              Original Prescription Document
            </div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              Decrypted stream: <span style={{ fontFamily: 'var(--font-mono)' }}>{prescriptionId}</span>
            </div>
          </div>
        </div>

        {/* Toolbar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {getQualityBadge()}

          <button
            onClick={handleZoomIn}
            title="Zoom In"
            style={{ padding: '6px 10px', background: 'rgba(30, 41, 59, 0.7)', borderRadius: '6px', color: '#fff' }}
          >
            <ZoomIn size={15} />
          </button>
          <button
            onClick={handleZoomOut}
            title="Zoom Out"
            style={{ padding: '6px 10px', background: 'rgba(30, 41, 59, 0.7)', borderRadius: '6px', color: '#fff' }}
          >
            <ZoomOut size={15} />
          </button>
          <button
            onClick={handleRotate}
            title="Rotate 90°"
            style={{ padding: '6px 10px', background: 'rgba(30, 41, 59, 0.7)', borderRadius: '6px', color: '#fff' }}
          >
            <RotateCw size={15} />
          </button>
          <button
            onClick={() => setHighContrast(!highContrast)}
            title="Toggle High Contrast for Handwriting"
            style={{
              padding: '6px 10px',
              background: highContrast ? 'rgba(56, 189, 248, 0.25)' : 'rgba(30, 41, 59, 0.7)',
              border: highContrast ? '1px solid var(--primary-glow)' : 'none',
              borderRadius: '6px',
              color: highContrast ? 'var(--primary-glow)' : '#fff'
            }}
          >
            <Contrast size={15} />
          </button>
          <button
            onClick={handleReset}
            title="Reset Zoom &amp; Pan"
            style={{ padding: '6px 10px', background: 'rgba(30, 41, 59, 0.7)', borderRadius: '6px', color: '#fff' }}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {/* Interactive Image Canvas */}
      <div
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{
          flex: 1,
          minHeight: '520px',
          maxHeight: '750px',
          overflow: 'hidden',
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#090d16',
          cursor: scale > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default',
          userSelect: 'none',
        }}
      >
        {/* Subtle coordinate grid backdrop */}
        <div style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          pointerEvents: 'none',
        }} />

        {/* Prescription Image */}
        {imageUrl ? (
          <img
            src={imageUrl}
            alt="Original Prescription"
            draggable={false}
            style={{
              maxWidth: '92%',
              maxHeight: '92%',
              objectFit: 'contain',
              borderRadius: '6px',
              boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
              transform: `translate(${position.x}px, ${position.y}px) scale(${scale}) rotate(${rotation}deg)`,
              transition: isDragging ? 'none' : 'transform 0.2s cubic-bezier(0.2, 0, 0, 1)',
              filter: highContrast ? 'contrast(160%) brightness(95%) grayscale(40%)' : 'none',
            }}
          />
        ) : (
          <div style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
            <FileText size={48} style={{ opacity: 0.4, marginBottom: '12px' }} />
            <p>No prescription loaded</p>
          </div>
        )}

        {/* Zoom Level Indicator */}
        <div style={{
          position: 'absolute',
          bottom: '14px',
          left: '14px',
          background: 'rgba(15, 23, 42, 0.8)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '6px',
          padding: '4px 10px',
          fontSize: '0.72rem',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-muted)',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          pointerEvents: 'none',
        }}>
          <span>Zoom: {Math.round(scale * 100)}%</span>
          {rotation > 0 && <span>• Rot: {rotation}°</span>}
          {highContrast && <span style={{ color: 'var(--primary-glow)' }}>• High-Contrast ON</span>}
        </div>

        {/* Security watermark */}
        <div style={{
          position: 'absolute',
          bottom: '14px',
          right: '14px',
          background: 'rgba(15, 23, 42, 0.8)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '6px',
          padding: '4px 10px',
          fontSize: '0.72rem',
          color: '#34d399',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          pointerEvents: 'none',
        }}>
          <Lock size={12} />
          <span>At-Rest Encrypted (Fernet)</span>
        </div>
      </div>
    </div>
  );
}
