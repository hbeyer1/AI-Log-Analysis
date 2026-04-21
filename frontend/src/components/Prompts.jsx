import { useEffect, useState } from 'react';
import { api } from '../api';
import { PromptEditor } from './PromptEditor';

const LABELS = {
  c_open_code: 'Open coding — extract atomic task codes per session',
  c_assign: 'Clustering — assign a code to a cluster (L0 and L1)',
  c_update_leader: 'Clustering — refresh a cluster label from its members',
  c_split: 'Clustering — split an oversized root into sub-types',
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
