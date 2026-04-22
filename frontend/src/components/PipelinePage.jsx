import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { RunBar, ModelSelect, LimitInput } from './RunBar';

const HAIKU = 'claude-haiku-4-5-20251001';
const SONNET = 'claude-sonnet-4-6';
const PREVIEW_N = 5;

export function PipelinePage({ substantiveCount, status, onAnyRunDone, onGoToAnalysis }) {
  return (
    <>
      <div className="panel-header">
        <h2>Pipeline</h2>
        <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          PII redaction → Prompt 1 (conversation features + objectives) → Prompt 2 (per-objective interview).
        </div>
      </div>
      <div className="panel-body">
        <StagePII
          substantiveCount={substantiveCount}
          status={status}
          onDone={onAnyRunDone}
          onGoToAnalysis={onGoToAnalysis}
        />
        <StagePrompt1
          enabled={!!status?.has_redacted}
          onDone={onAnyRunDone}
          onGoToAnalysis={onGoToAnalysis}
        />
        <StagePrompt2
          enabled={!!status?.has_features}
          onDone={onAnyRunDone}
          onGoToAnalysis={onGoToAnalysis}
        />
      </div>
    </>
  );
}


function StageCard({ title, subtitle, done, locked, children }) {
  const borderColor = done ? 'var(--success)' : locked ? 'var(--border)' : 'var(--accent)';
  return (
    <div className="summary-card" style={{ borderLeft: `3px solid ${borderColor}`, padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 8 }}>
        <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>{subtitle}</div>
        {done && <span className="status-chip ok" style={{ marginLeft: 'auto' }}>✓ complete</span>}
        {locked && <span className="status-chip missing" style={{ marginLeft: 'auto' }}>locked</span>}
      </div>
      {children}
    </div>
  );
}


function useJobPoll(jobId, onDone) {
  const [job, setJob] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stopped) return;
        setJob(j);
        if (j.status === 'done') onDone?.();
        else if (j.status === 'running') pollRef.current = setTimeout(tick, 800);
      } catch (e) { /* ignore */ }
    };
    tick();
    return () => { stopped = true; if (pollRef.current) clearTimeout(pollRef.current); };
  }, [jobId]);
  return job;
}


function PreviewHeader({ summary, count, onViewAll }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 12, color: 'var(--text-dim)', margin: '12px 0 6px' }}>
      <span>{summary}</span>
      {onViewAll && count > PREVIEW_N && (
        <button
          className="ghost-btn"
          onClick={onViewAll}
          style={{ marginLeft: 'auto', padding: '2px 10px', fontSize: 11 }}
        >
          View all {count} →
        </button>
      )}
    </div>
  );
}


// -------- Stage 1: PII -------- //

function StagePII({ substantiveCount, status, onDone, onGoToAnalysis }) {
  const [piiEnabled, setPiiEnabled] = useState(true);
  const [limit, setLimit] = useState(5);
  const [model, setModel] = useState(HAIKU);
  const [jobId, setJobId] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const loadResult = async () => {
    try { setResult(await api.piiResult()); } catch { /* none yet */ }
  };
  useEffect(() => { loadResult(); }, []);

  const job = useJobPoll(jobId, async () => { await loadResult(); onDone?.(); });
  const running = job?.status === 'running';

  const run = async () => {
    setErr(null);
    try {
      const r = await api.piiRun({ limit: limit || null, enabled: piiEnabled, model, concurrency: 5 });
      setJobId(r.job_id);
    } catch (e) { setErr(e.message); }
  };

  return (
    <StageCard
      title="Stage 1 · PII redaction"
      subtitle="Replaces identifiers with [TYPE_N] placeholders. Verifies structural integrity."
      done={!!status?.has_redacted}
    >
      {err && <div className="error-banner">{err}</div>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '8px 0 12px', fontSize: 13 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={piiEnabled} onChange={(e) => setPiiEnabled(e.target.checked)} disabled={running} />
          <span>Run redaction (uncheck to skip for internal/test data)</span>
        </label>
      </div>

      <RunBar running={running} job={job} onRun={run} runLabel={piiEnabled ? 'Run Stage 1' : 'Skip Stage 1'}>
        <LimitInput value={limit} onChange={setLimit} disabled={running} max={substantiveCount} />
        <ModelSelect value={model} onChange={setModel} disabled={running || !piiEnabled} />
      </RunBar>

      {result && (
        <>
          <PreviewHeader
            summary={`${result.n_total} conversations · ${result.n_verified} verified · ${result.n_skipped} skipped · ${result.n_failed} failed`}
            count={result.n_total}
            onViewAll={onGoToAnalysis}
          />
          <PIITable rows={result.rows?.slice(0, PREVIEW_N) || []} />
        </>
      )}
    </StageCard>
  );
}


function PIITable({ rows }) {
  return (
    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
          <th style={{ padding: 6 }}>Conversation</th>
          <th style={{ padding: 6 }}>Status <span style={{ color: 'var(--text-faint)', fontWeight: 400 }}>(basic verification)</span></th>
          <th style={{ padding: 6 }}>Messages</th>
          <th style={{ padding: 6 }}>Failure reason</th>
        </tr>
      </thead>
      <tbody>
        {rows?.map((r) => (
          <tr key={r.uuid} style={{ borderTop: '1px solid var(--border)' }}>
            <td style={{ padding: 6 }}>{r.name || r.uuid.slice(0, 8)}</td>
            <td style={{ padding: 6 }}>
              {r.skipped ? 'skipped' : r.verified ? '✓ verified' : 'failed'}
            </td>
            <td style={{ padding: 6 }}>{r.message_count}</td>
            <td style={{ padding: 6, color: r.failed_reason ? 'crimson' : 'inherit' }}>
              {r.failed_reason || ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


// -------- Stage 2: Prompt 1 -------- //

function StagePrompt1({ enabled, onDone, onGoToAnalysis }) {
  const [model, setModel] = useState(SONNET);
  const [jobId, setJobId] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const loadResult = async () => {
    try { setResult(await api.prompt1Result()); } catch { /* none yet */ }
  };
  useEffect(() => { loadResult(); }, []);

  const job = useJobPoll(jobId, async () => { await loadResult(); onDone?.(); });
  const running = job?.status === 'running';

  const run = async () => {
    setErr(null);
    try {
      const r = await api.prompt1Run({ model, concurrency: 5 });
      setJobId(r.job_id);
    } catch (e) { setErr(e.message); }
  };

  return (
    <StageCard
      title="Stage 2 · Prompt 1 (conversation features + objectives)"
      subtitle="Produces work_related, tools, durations, and an objective segmentation with turn_indices."
      done={!!result}
      locked={!enabled}
    >
      {err && <div className="error-banner">{err}</div>}
      {!enabled && (
        <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>Run Stage 1 first.</div>
      )}
      {enabled && (
        <RunBar running={running} job={job} onRun={run} runLabel="Run Stage 2">
          <ModelSelect value={model} onChange={setModel} disabled={running} />
        </RunBar>
      )}

      {result && (
        <>
          <PreviewHeader
            summary={`${result.n_conversations} conversations · ${result.n_objectives} objectives`}
            count={result.n_conversations}
            onViewAll={onGoToAnalysis}
          />
          <div>
            {(result.rows || []).slice(0, PREVIEW_N).map((r) => (
              <ConversationCard key={r.conversation_id} row={r} />
            ))}
          </div>
        </>
      )}
    </StageCard>
  );
}


function ConversationCard({ row }) {
  const [open, setOpen] = useState(false);
  const f = row.conversation_features || {};
  return (
    <div className="code-card" style={{ cursor: 'pointer' }} onClick={() => setOpen(!open)}>
      <div className="top">
        <span className="name">{row.name || row.conversation_id.slice(0, 8)}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
          {f.num_turns ?? '?'} turns · {(row.objectives || []).length} objectives
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <Field label="Work-related">{String(f.work_related)}</Field>
          <Field label="Duration">{f.conversation_duration_sec ?? '?'}s</Field>
          <Field label="Tools">{(f.tools_used || []).join(', ') || '—'}</Field>
          <Field label="Attachments">{f.attachments?.count ?? 0}</Field>
          <Field label="Artifacts">{f.artifacts_created?.count ?? 0}</Field>
          <div style={{ marginTop: 10 }}>
            {(row.objectives || []).map((o) => (
              <div key={o.objective_id} style={{ padding: '6px 10px', background: 'var(--bg-elev-2)', borderRadius: 4, marginBottom: 4 }}>
                <strong>#{o.objective_id}</strong> {o.description}
                <div style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                  turns {o.turn_indices?.join(', ')}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// -------- Stage 3: Prompt 2 -------- //

function StagePrompt2({ enabled, onDone, onGoToAnalysis }) {
  const [model, setModel] = useState(SONNET);
  const [jobId, setJobId] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const loadResult = async () => {
    try { setResult(await api.prompt2Result()); } catch { /* none yet */ }
  };
  useEffect(() => { loadResult(); }, []);

  const job = useJobPoll(jobId, async () => { await loadResult(); onDone?.(); });
  const running = job?.status === 'running';

  const run = async () => {
    setErr(null);
    try {
      const r = await api.prompt2Run({ model, concurrency: 5 });
      setJobId(r.job_id);
    } catch (e) { setErr(e.message); }
  };

  return (
    <StageCard
      title="Stage 3 · Prompt 2 (per-objective interview)"
      subtitle="One call per objective on the sliced sub-transcript. Nine prose interview fields per objective."
      done={!!result}
      locked={!enabled}
    >
      {err && <div className="error-banner">{err}</div>}
      {!enabled && <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>Run Stage 2 first.</div>}
      {enabled && (
        <RunBar running={running} job={job} onRun={run} runLabel="Run Stage 3">
          <ModelSelect value={model} onChange={setModel} disabled={running} />
        </RunBar>
      )}
      {result && (
        <>
          <PreviewHeader
            summary={`${result.n_objectives} objective reports`}
            count={result.n_objectives}
            onViewAll={onGoToAnalysis}
          />
          <div>
            {(result.rows || []).slice(0, PREVIEW_N).map((r, i) => <ObjectiveReportCard key={i} row={r} />)}
          </div>
        </>
      )}
    </StageCard>
  );
}


function ObjectiveReportCard({ row }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="code-card" style={{ cursor: 'pointer' }} onClick={() => setOpen(!open)}>
      <div className="top">
        <span className="code-tag">{row.domain || '—'}</span>
        <span className="name">{row.description}</span>
      </div>
      <div className="memo">{row.underlying_intent}</div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <Field label="Topic">{row.topic}</Field>
          <Field label="Deliverable">{row.deliverable}</Field>
          <Field label="Workflow"><span style={{ whiteSpace: 'pre-wrap' }}>{row.workflow_and_resolution}</span></Field>
          <Field label="User approach"><span style={{ whiteSpace: 'pre-wrap' }}>{row.user_approach}</span></Field>
          <Field label="User signals"><span style={{ whiteSpace: 'pre-wrap' }}>{row.user_signals}</span></Field>
          <Field label="Language & tone">{row.language_and_tone}</Field>
          {row.additional_notes && <Field label="Notes">{row.additional_notes}</Field>}
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
