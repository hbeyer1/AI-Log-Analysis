import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { PromptEditor } from './PromptEditor';
import { RunBar, ModelSelect } from './RunBar';

export function CleanPage({ hasRawObjectives }) {
  const [tab, setTab] = useState('output');
  const [model, setModel] = useState('claude-sonnet-4-6');
  const [estimate, setEstimate] = useState(null);
  const [kb, setKb] = useState(null);
  const [kept, setKept] = useState(null);
  const [cleanLog, setCleanLog] = useState(null);
  const [excluded, setExcluded] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);

  const loadAll = async () => {
    try {
      const r = await api.cleanResult();
      setKept(r.kept);
      setCleanLog(r.log);
    } catch { /* no run yet */ }
    try {
      const r = await api.cleanExcluded();
      setExcluded(r.excluded);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    api.getKb().then(setKb).catch(() => setKb(null));
    loadAll();
  }, []);

  useEffect(() => {
    if (!hasRawObjectives) return;
    api.cleanEstimate({ model }).then(setEstimate).catch((e) => setErr(e.message));
  }, [model, kb, hasRawObjectives]);

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
      const r = await api.cleanRun({ model });
      setJobId(r.job_id);
    } catch (e) { setErr(e.message); }
  };

  const savePatterns = async (patterns) => {
    setErr(null);
    try {
      const r = await api.saveKb({ exclusion_patterns: patterns });
      setKb(r);
    } catch (e) { setErr(e.message); }
  };

  if (!hasRawObjectives) {
    return (
      <>
        <div className="panel-header"><h2>2 · Clean objectives</h2></div>
        <div className="panel-body">
          <div className="empty-state">Run <strong>Extract</strong> first.</div>
        </div>
      </>
    );
  }

  const running = job?.status === 'running';
  const patterns = kb?.exclusion_patterns || [];

  return (
    <>
      <div className="panel-header">
        <h2>2 · Clean objectives (knowledge-informed)</h2>
        <div className="tab-strip">
          <button className={`tab ${tab === 'kb' ? 'active' : ''}`} onClick={() => setTab('kb')}>
            Exclusion patterns ({patterns.length})
          </button>
          <button className={`tab ${tab === 'output' ? 'active' : ''}`} onClick={() => setTab('output')}>
            Kept {kept ? `(${kept.length})` : ''}
          </button>
          <button className={`tab ${tab === 'excluded' ? 'active' : ''}`} onClick={() => setTab('excluded')}>
            Excluded {excluded ? `(${excluded.length})` : ''}
          </button>
          <button className={`tab ${tab === 'prompt' ? 'active' : ''}`} onClick={() => setTab('prompt')}>
            Prompt
          </button>
        </div>
      </div>

      <div className="panel-body">
        {err && <div className="error-banner">{err}</div>}
        <RunBar estimate={estimate} running={running} job={job} onRun={run}>
          <ModelSelect value={model} onChange={setModel} disabled={running} />
        </RunBar>

        <div style={{ fontSize: 12, color: 'var(--text-faint)', margin: '4px 0 12px' }}>
          Applies exclusion patterns from the knowledge base to raw objectives. Empty-pattern runs only apply a minimal heuristic
          (drops objectives with missing/trivial content). With patterns defined, a single LLM judge decides per objective. Excluded items are logged for audit — nothing is lost.
        </div>

        {cleanLog && (
          <div className="stat-grid" style={{ marginBottom: 12 }}>
            <Stat label="Total" value={cleanLog.n_total} />
            <Stat label="Kept" value={cleanLog.n_kept} />
            <Stat label="Excluded" value={cleanLog.n_excluded} />
            <Stat label="Patterns applied" value={cleanLog.n_patterns} />
            <Stat label="Cost" value={`$${(cleanLog.cost_usd || 0).toFixed(4)}`} />
            <Stat label="Duration" value={`${cleanLog.duration_s}s`} />
          </div>
        )}

        {tab === 'kb' && <PatternEditor patterns={patterns} onSave={savePatterns} />}
        {tab === 'output' && <ObjectiveList objectives={kept} emptyMsg="No clean run yet." />}
        {tab === 'excluded' && <ExcludedList excluded={excluded} />}
        {tab === 'prompt' && <PromptEditor name="clean_judge" />}
      </div>
    </>
  );
}

function PatternEditor({ patterns, onSave }) {
  const [rows, setRows] = useState(() => patterns.map(normalizeRow));
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setRows(patterns.map(normalizeRow));
    setDirty(false);
  }, [patterns]);

  const update = (i, key, val) => {
    const next = rows.map((r, idx) => (idx === i ? { ...r, [key]: val } : r));
    setRows(next);
    setDirty(true);
  };

  const add = () => {
    setRows([...rows, { id: newId(rows), pattern: '', reason: '' }]);
    setDirty(true);
  };

  const remove = (i) => {
    setRows(rows.filter((_, idx) => idx !== i));
    setDirty(true);
  };

  const save = () => {
    const cleaned = rows
      .map((r) => ({
        id: (r.id || '').trim() || newId(rows),
        pattern: (r.pattern || '').trim(),
        reason: (r.reason || '').trim(),
      }))
      .filter((r) => r.pattern);
    onSave(cleaned);
  };

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--text-faint)', marginBottom: 10 }}>
        Each pattern describes a type of objective the LLM should exclude during cleaning.
        Examples: <em>"Objective is a greeting or pleasantry"</em>, <em>"Objective is testing whether the LLM is working"</em>.
      </div>
      {rows.length === 0 && (
        <div className="empty-state">No patterns yet — on this run only the minimal heuristic will apply.</div>
      )}
      {rows.map((r, i) => (
        <div key={i} className="code-card" style={{ marginBottom: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 40px', gap: 8, alignItems: 'start' }}>
            <input
              type="text" value={r.id} placeholder="id"
              onChange={(e) => update(i, 'id', e.target.value)}
              style={{ fontSize: 12 }}
            />
            <div>
              <input
                type="text" value={r.pattern} placeholder="pattern description"
                onChange={(e) => update(i, 'pattern', e.target.value)}
                style={{ width: '100%', fontSize: 13, marginBottom: 4 }}
              />
              <input
                type="text" value={r.reason} placeholder="reason (optional, for humans)"
                onChange={(e) => update(i, 'reason', e.target.value)}
                style={{ width: '100%', fontSize: 12 }}
              />
            </div>
            <button onClick={() => remove(i)} style={{ fontSize: 11 }}>✕</button>
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button onClick={add}>+ Add pattern</button>
        <button onClick={save} disabled={!dirty}>Save knowledge base</button>
        {dirty && <span style={{ fontSize: 12, color: 'var(--warn)', alignSelf: 'center' }}>unsaved changes</span>}
      </div>
    </div>
  );
}

function normalizeRow(p) {
  return {
    id: p.id || '',
    pattern: p.pattern || '',
    reason: p.reason || '',
  };
}

function newId(rows) {
  const ids = rows.map((r) => r.id);
  let i = 1;
  while (ids.includes(`p${i}`)) i += 1;
  return `p${i}`;
}

function ObjectiveList({ objectives, emptyMsg }) {
  if (!objectives) return <div className="empty-state">{emptyMsg}</div>;
  if (!objectives.length) return <div className="empty-state">Empty result set.</div>;
  return (
    <div>
      {objectives.map((o) => (
        <div className="code-card" key={o.id}>
          <div className="top">
            <span className="name">{o.objective}</span>
          </div>
          <div className="memo" style={{ whiteSpace: 'pre-wrap' }}>{o.resolution_summary}</div>
        </div>
      ))}
    </div>
  );
}

function ExcludedList({ excluded }) {
  if (!excluded) return <div className="empty-state">No exclusions logged yet.</div>;
  if (!excluded.length) return <div className="empty-state">Nothing excluded.</div>;
  return (
    <div>
      {excluded.map((e, i) => (
        <div className="code-card" key={i}>
          <div className="top">
            <span className="code-tag">{e.stage}</span>
            {e.matched_pattern && <span className="code-tag">{e.matched_pattern}</span>}
            <span className="name">{e.objective?.objective || '(no objective)'}</span>
          </div>
          <div className="memo" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            reason: {e.reason}
          </div>
          {e.objective?.resolution_summary && (
            <div className="memo" style={{ fontSize: 12 }}>{e.objective.resolution_summary}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 14 }}>{value ?? '—'}</div>
    </div>
  );
}
