export function RunBar({
  children,
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
    { id: 'claude-sonnet-4-6', label: 'Claude · Sonnet 4.6', group: 'Anthropic' },
    { id: 'claude-haiku-4-5-20251001', label: 'Claude · Haiku 4.5', group: 'Anthropic' },
    { id: 'claude-opus-4-7', label: 'Claude · Opus 4.7', group: 'Anthropic' },
    { id: 'gpt-5.2-chat-latest', label: 'OpenAI · GPT-5.2 Chat (Instant)', group: 'OpenAI' },
    { id: 'gpt-4.1', label: 'OpenAI · GPT-4.1', group: 'OpenAI' },
  ];
  const byGroup = opts.reduce((acc, o) => {
    (acc[o.group || 'Other'] ||= []).push(o);
    return acc;
  }, {});
  return (
    <>
      <label style={{ fontSize: 12, color: 'var(--text-dim)', marginRight: 8 }}>Model:</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {Object.entries(byGroup).map(([group, list]) => (
          <optgroup key={group} label={group}>
            {list.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </optgroup>
        ))}
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
