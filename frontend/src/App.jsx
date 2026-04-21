import { useEffect, useState } from 'react';
import { api } from './api';
import { Upload } from './components/Upload';
import { PipelinePage } from './components/PipelinePage';
import { AnalysisPage } from './components/AnalysisPage';
import { PromptsPage } from './components/Prompts';
import './App.css';

export default function App() {
  const [status, setStatus] = useState(null);
  const [phase, setPhase] = useState('pipeline');
  const [dataset, setDataset] = useState(null);

  const refreshStatus = () => api.status().then(setStatus).catch(() => setStatus({}));

  useEffect(() => {
    refreshStatus();
    const timer = setInterval(refreshStatus, 4000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!dataset && status?.dataset_loaded) {
      api.sessions().then((r) => {
        setDataset({
          total_sessions: status.session_count,
          substantive_count: r.substantive_count,
          preview: r.sessions.slice(0, 10),
        });
      });
    }
  }, [status, dataset]);

  const analysisReady = !!(status?.has_features || status?.has_objectives);

  return (
    <div className="app">
      <div className="topbar">
        <h1>AI Log Analysis</h1>
        <span className={`status-chip ${status?.anthropic_configured ? 'ok' : 'missing'}`}>
          {status?.anthropic_configured ? '✓ Anthropic' : '⚠ Anthropic key'}
        </span>
        <span className={`status-chip ${status?.openai_configured ? 'ok' : 'missing'}`}>
          {status?.openai_configured ? '✓ OpenAI' : '⚠ OpenAI key'}
        </span>
        {dataset && (
          <span className="status-chip">
            {dataset.substantive_count} / {dataset.total_sessions} substantive
          </span>
        )}
      </div>

      <div className="main">
        <aside className="sidebar">
          <div className="sidebar-section">Data</div>
          <div
            className={`phase-item ${phase === 'data' ? 'active' : ''}`}
            onClick={() => setPhase('data')}
          >
            Upload & overview
          </div>
          <div className="sidebar-section">Pipeline</div>
          <div
            className={`phase-item ${phase === 'pipeline' ? 'active' : ''}`}
            onClick={() => dataset && setPhase('pipeline')}
            style={!dataset ? { opacity: 0.4, cursor: 'not-allowed' } : {}}
          >
            Run stages
            {!dataset && <span className="badge">locked</span>}
          </div>
          <div
            className={`phase-item ${phase === 'analysis' ? 'active' : ''}`}
            onClick={() => analysisReady && setPhase('analysis')}
            style={!analysisReady ? { opacity: 0.4, cursor: 'not-allowed' } : {}}
          >
            Analysis
            {!analysisReady && <span className="badge">locked</span>}
          </div>
          <div className="sidebar-section">Configuration</div>
          <div
            className={`phase-item ${phase === 'prompts' ? 'active' : ''}`}
            onClick={() => setPhase('prompts')}
          >
            Prompts
          </div>
        </aside>

        <section className="panel">
          {phase === 'data' && <DataPanel dataset={dataset} onLoaded={(d) => { setDataset(d); refreshStatus(); }} />}
          {phase === 'prompts' && <PromptsPage />}
          {phase === 'pipeline' && dataset && (
            <PipelinePage
              substantiveCount={dataset.substantive_count || 0}
              status={status}
              onAnyRunDone={refreshStatus}
              onGoToAnalysis={analysisReady ? () => setPhase('analysis') : null}
            />
          )}
          {phase === 'pipeline' && !dataset && (
            <div className="panel-body">
              <div className="empty-state">
                Upload a <code>conversations.json</code> file to begin.
              </div>
            </div>
          )}
          {phase === 'analysis' && analysisReady && <AnalysisPage />}
          {phase === 'analysis' && !analysisReady && (
            <div className="panel-body">
              <div className="empty-state">Run the pipeline first.</div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function DataPanel({ dataset, onLoaded }) {
  return (
    <>
      <div className="panel-header">
        <h2>Data · Upload & overview</h2>
      </div>
      <div className="panel-body">
        {!dataset ? (
          <Upload onLoaded={onLoaded} />
        ) : (
          <>
            <div className="stat-grid">
              <div className="stat-card">
                <div className="label">Total conversations</div>
                <div className="value">{dataset.total_sessions}</div>
              </div>
              <div className="stat-card">
                <div className="label">Substantive</div>
                <div className="value">{dataset.substantive_count}</div>
              </div>
              <div className="stat-card">
                <div className="label">Filtered out</div>
                <div className="value">{dataset.total_sessions - dataset.substantive_count}</div>
              </div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-faint)', marginBottom: 8 }}>
              Substantive = ≥100 chars total AND ≥2 messages. Preview of first 10:
            </div>
            {dataset.preview?.map((s) => (
              <div className="summary-card" key={s.uuid}>
                <div className="header">
                  <div className="name">{s.name}</div>
                  <div className="date">{s.created_at?.slice(0, 10)}</div>
                </div>
                <div className="meta">
                  {s.message_count} msgs · {s.total_chars.toLocaleString()} chars
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}
