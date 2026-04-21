import { useState } from 'react';
import { api } from '../api';

export function Upload({ onLoaded }) {
  const [dragging, setDragging] = useState(false);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleFile = async (file) => {
    setErr(null);
    setLoading(true);
    try {
      const result = await api.upload(file);
      onLoaded(result);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  return (
    <div className="empty-state">
      <div
        className={`upload-zone ${dragging ? 'dragging' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        {loading ? (
          <div>Parsing...</div>
        ) : (
          <>
            <div style={{ fontSize: 16, marginBottom: 8 }}>
              Drop a <code>conversations.json</code> file here
            </div>
            <div style={{ fontSize: 13, marginBottom: 16 }}>or</div>
            <label className="primary-btn" style={{ display: 'inline-block' }}>
              Choose file
              <input
                type="file"
                accept="application/json"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFile(f);
                }}
              />
            </label>
          </>
        )}
      </div>
      {err && <div className="error-banner" style={{ marginTop: 16 }}>{err}</div>}
    </div>
  );
}
