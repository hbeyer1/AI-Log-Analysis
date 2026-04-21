import { useEffect, useState } from 'react';
import { api } from '../api';

export function PromptEditor({ name }) {
  const [content, setContent] = useState('');
  const [original, setOriginal] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    api.getPrompt(name).then((r) => {
      if (!alive) return;
      setContent(r.content);
      setOriginal(r.content);
    }).catch((e) => setErr(e.message));
    return () => { alive = false; };
  }, [name]);

  const dirty = content !== original;

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      await api.savePrompt(name, content);
      setOriginal(content);
    } catch (e) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {err && <div className="error-banner">{err}</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <code style={{ fontSize: 12 }}>{name}.txt</code>
        <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
          Variables in {'{braces}'} are substituted at run time.
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button
            className="ghost-btn"
            disabled={!dirty}
            onClick={() => setContent(original)}
          >
            Revert
          </button>
          <button
            className="primary-btn"
            disabled={!dirty || saving}
            onClick={save}
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
      <textarea
        className="prompt-editor"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
    </div>
  );
}
