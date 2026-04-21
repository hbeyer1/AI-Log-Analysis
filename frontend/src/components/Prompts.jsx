import { useEffect, useState } from 'react';
import { api } from '../api';
import { PromptEditor } from './PromptEditor';

const LABELS = {
  pii_redact: 'Stage 1 — PII redaction (replace identifiers with [TYPE_N] placeholders)',
  prompt1: 'Stage 2 — Conversation features + objective segmentation',
  prompt2: 'Stage 3 — Per-objective structured interview',
};

export function PromptsPage() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.listPrompts()
      .then((r) => {
        setItems(r.prompts);
        if (r.prompts.length) setSelected(r.prompts[0].name);
      })
      .catch((e) => setErr(e.message));
  }, []);

  return (
    <>
      <div className="panel-header">
        <h2>Prompts</h2>
        <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          Edit the prompts used by each phase. Saved to <code>backend/prompts/*.txt</code>.
        </div>
      </div>

      <div className="panel-body">
        {err && <div className="error-banner">{err}</div>}
        <div className="two-col" style={{ minHeight: 'calc(100vh - 200px)' }}>
          <div className="list">
            {items.map((p) => (
              <div
                key={p.name}
                className={`prompt-list-item ${selected === p.name ? 'active' : ''}`}
                onClick={() => setSelected(p.name)}
              >
                <div>
                  <div className="name">{p.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                    {LABELS[p.name] || ''}
                  </div>
                </div>
                <div className="size">{p.size}b</div>
              </div>
            ))}
          </div>
          <div className="detail">
            {selected
              ? <PromptEditor key={selected} name={selected} />
              : <div className="empty-state">Select a prompt to edit.</div>}
          </div>
        </div>
      </div>
    </>
  );
}
