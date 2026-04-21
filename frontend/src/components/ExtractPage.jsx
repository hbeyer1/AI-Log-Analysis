import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { PromptEditor } from './PromptEditor';
import { RunBar, ModelSelect, LimitInput } from './RunBar';

export function ExtractPage({ substantiveCount }) {
  const [tab, setTab] = useState('output');
  const [estimate, setEstimate] = useState(null);
  const [limit, setLimit] = useState(5);
  const [model, setModel] = useState('claude-sonnet-4-6');
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [objectives, setObjectives] = useState(null);
  const [log, setLog] = useState(null);
  const [rejected, setRejected] = useState(null);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);

  const loadAll = async () => {
    try {
      const r = await api.extractResult();
      setObjectives(r.objectives);
      setLog(r.log);
    } catch { /* no objectives yet */ }
    try {
      const r = await api.extractRejected();
      setRejected(r.rejected);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadAll(); }, []);

  useEffect(() => {
    if (!substantiveCount) return;
    api.extractEstimate({ limit: limit || null, model })
      .then(setEstimate).catch((e) => setErr(e.message));
  }, [limit, model, substantiveCount]);

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stopped) return;
        setJob(j);
        if (j.status === 'done') {
          await loadAll();
        } else if (j.status === 'running') {
          pollRef.current = setTimeout(tick, 800);
        }
      } catch (e) { setErr(e.message); }
    };
    tick();
    return () => { stopped = true; if (pollRef.current) clearTimeout(pollRef.current); };
  }, [jobId]);

  const run = async () => {
    setErr(null); setJob(null);
    try {
      const r = await api.extractRun({ limit: limit || null, model, concurrency: 5 });
      setJobId(r.job_id);
    } catch (e) { setErr(e.message); }
  };

  const running = job?.status === 'running';

  return (
    <>
      <div className="panel-header">
        <h2>1 · Extract objectives (knowledge-free)</h2>
        <div className="tab-strip">
          <button className={`tab ${tab === 'prompt' ? 'active' : ''}`} onClick={() => setTab('prompt')}>Prompt</button>
          <button className={`tab ${tab === 'output' ? 'active' : ''}`} onClick={() => setTab('output')}>
            Objectives {objectives ? `(${objectives.length})` : ''}
          </button>
          <button className={`tab ${tab === 'log' ? 'active' : ''}`} onClick={() => setTab('log')}>
            Per-session log {log ? `(${log.length})` : ''}
          </button>
          <button className={`tab ${tab === 'rejected' ? 'active' : ''}`} onClick={() => setTab('rejected')}>
            Rejected {rejected ? `(${rejected.length})` : ''}
          </button>
        </div>
      </div>

      <div className="panel-body">
        {err && <div className="error-banner">{err}</div>}
        <RunBar estimate={estimate} running={running} job={job} onRun={run}>
          <LimitInput value={limit} onChange={setLimit} disabled={running} max={substantiveCount} />
          <ModelSelect value={model} onChange={setModel} disabled={running} />
        </RunBar>

        <div style={{ fontSize: 12, color: 'var(--text-faint)', margin: '4px 0 12px' }}>
          One LLM call per session. The LLM sees only the raw transcript — no prior categories. Each objective includes a 4-dimension resolution summary (initial framing · interaction pattern · user effort · outcome). Validation drops objectives whose source quote isn't literally present in a user turn.
        </div>

        {tab === 'prompt' && <PromptEditor name="extract" />}
        {tab === 'output' && <ObjectiveList objectives={objectives} />}
        {tab === 'log' && <PerSessionLog log={log} />}
        {tab === 'rejected' && <RejectedList rejected={rejected} />}
      </div>
    </>
  );
}

function ObjectiveList({ objectives }) {
  if (!objectives) return <div className="empty-state">No objectives yet. Hit <strong>Run phase</strong>.</div>;
  if (!objectives.length) return <div className="empty-state">Empty result set.</div>;
  return (
    <div>
      {objectives.map((o) => (
        <ObjectiveCard key={o.id} obj={o} />
      ))}
    </div>
  );
}

function ObjectiveCard({ obj }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="code-card" style={{ cursor: 'pointer' }} onClick={() => setOpen(!open)}>
      <div className="top">
        <span className="name">{obj.objective}</span>
      </div>
      <div className="memo" style={{ whiteSpace: 'pre-wrap' }}>{obj.resolution_summary}</div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <Field label="Source quote"><em>"{obj.source_quote}"</em></Field>
          <Field label="Turn indices">{(obj.turn_indices || []).join(', ')}</Field>
          <Field label="Timestamp">{obj.timestamp}</Field>
          <Field label="ID / Session">
            <code>{obj.id}</code> · <code>{obj.session_id}</code>
          </Field>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 6, fontSize: 13 }}>
      <span style={{ color: 'var(--text-dim)', marginRight: 8 }}>{label}:</span>
      <span>{children}</span>
    </div>
  );
}

function PerSessionLog({ log }) {
  if (!log) return <div className="empty-state">No run log yet.</div>;
  if (!log.length) return <div className="empty-state">Empty log.</div>;
  return (
    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
          <th style={{ padding: 6 }}>Session</th>
          <th style={{ padding: 6 }}>Chunks</th>
          <th style={{ padding: 6 }}>Objectives</th>
          <th style={{ padding: 6 }}>Rejected</th>
          <th style={{ padding: 6 }}>Tokens</th>
          <th style={{ padding: 6 }}>Cost</th>
          <th style={{ padding: 6 }}>Dur (s)</th>
          <th style={{ padding: 6 }}>Error</th>
        </tr>
      </thead>
      <tbody>
        {log.map((r) => (
          <tr key={r.session_id} style={{ borderTop: '1px solid var(--border)' }}>
            <td style={{ padding: 6 }}>{r.name || r.session_id.slice(0, 8)}</td>
            <td style={{ padding: 6 }}>{r.n_chunks}</td>
            <td style={{ padding: 6 }}>{r.n_objectives}</td>
            <td style={{ padding: 6 }}>{r.n_rejected}</td>
            <td style={{ padding: 6 }}>{r.input_tokens}→{r.output_tokens}</td>
            <td style={{ padding: 6 }}>${(r.cost_usd || 0).toFixed(4)}</td>
            <td style={{ padding: 6 }}>{r.duration_s}</td>
            <td style={{ padding: 6, color: r.error ? 'crimson' : 'inherit' }}>{r.error || ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RejectedList({ rejected }) {
  if (!rejected) return <div className="empty-state">Nothing rejected (or not run yet).</div>;
  if (!rejected.length) return <div className="empty-state">No rejections.</div>;
  return (
    <div>
      {rejected.map((r, i) => (
        <div className="code-card" key={i}>
          <div className="top">
            <span className="code-tag">{r.reason}</span>
            <span className="name">{r.raw?.objective || '(no objective)'}</span>
          </div>
          <div className="memo" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            session <code>{r.session_id}</code>
          </div>
          {r.raw?.source_quote && (
            <div className="memo" style={{ fontSize: 12 }}>
              quote: <em>"{r.raw.source_quote}"</em>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
