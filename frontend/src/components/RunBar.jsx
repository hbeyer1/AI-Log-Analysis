export function RunBar({
  children,
  estimate,
  running,
  job,
  onRun,
  runLabel = 'Run phase',
  runningLabel = 'Running...',
  disabled = false,
}) {
  const progressPct = job ? Math.round((100 * job.done) / Math.max(1, job.total)) : 0;
  return (
    <div className="run-bar">
      {children}
      {estimate && (
        <span className="cost">
          ~${estimate.estimated_cost_usd?.toFixed(3)}
          {estimate.sessions !== undefined ? ` · ${estimate.sessions} sessions` : ''}
          {estimate.dimensions !== undefined ? ` · ${estimate.dimensions} dims` : ''}
          {estimate.estimated_input_tokens !== undefined
            ? ` · ${estimate.estimated_input_tokens.toLocaleString()} in / ${estimate.estimated_output_tokens.toLocaleString()} out`
            : ''}
        </span>
      )}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        {running && (
          <>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              {job.done}/{job.total}
            </span>
            <div className="progress">
              <div className="progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
          </>
        )}
        <button className="primary-btn" onClick={onRun} disabled={running || disabled}>
          {running ? runningLabel : runLabel}
        </button>
      </div>
    </div>
  );
}

export function useJobPoll(jobId, onDone) {
  // Simple hook would be nicer but we're keeping components self-contained.
  // Left as a no-op helper; use the setTimeout pattern in each phase.
  return null;
}

export function ModelSelect({ value, onChange, disabled, options }) {
  const opts = options ?? [
    { id: 'claude-sonnet-4-6', label: 'Sonnet 4.6' },
    { id: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5' },
    { id: 'claude-opus-4-7', label: 'Opus 4.7' },
  ];
  return (
    <>
      <label style={{ fontSize: 12, color: 'var(--text-dim)', marginRight: 8 }}>Model:</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    </>
  );
}

export function LimitInput({ value, onChange, disabled, max, suffix = '(0 = all)' }) {
  return (
    <div>
      <label style={{ fontSize: 12, color: 'var(--text-dim)', marginRight: 8 }}>Limit:</label>
      <input
        type="number" min={0} max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
        disabled={disabled}
      />
      <span style={{ fontSize: 12, color: 'var(--text-faint)', marginLeft: 6 }}>{suffix}</span>
    </div>
  );
}
