import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';

export function AnalysisPage() {
  const [tab, setTab] = useState('conversations');
  const [conversations, setConversations] = useState(null);
  const [objectives, setObjectives] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.finalConversations().then(setConversations).catch((e) => setErr(e.message));
    api.finalObjectives().then(setObjectives).catch((e) => setErr(e.message));
  }, []);

  return (
    <>
      <div className="panel-header">
        <h2>Analysis</h2>
        <div className="tab-strip">
          <button className={`tab ${tab === 'conversations' ? 'active' : ''}`} onClick={() => setTab('conversations')}>
            Conversations {conversations ? `(${conversations.length})` : ''}
          </button>
          <button className={`tab ${tab === 'objectives' ? 'active' : ''}`} onClick={() => setTab('objectives')}>
            Objectives {objectives ? `(${objectives.length})` : ''}
          </button>
          <div style={{ width: 1, height: 20, background: 'var(--border)', margin: '0 8px' }} />
          <a
            className="primary-btn download-btn"
            href={api.downloadBundleUrl}
            style={{ textDecoration: 'none' }}
            title="Both JSON + CSV datasets, per-stage logs, prompts used, and a cost report — all in one .zip"
          >
            ↓ Download bundle (.zip)
          </a>
        </div>
      </div>
      <div className="panel-body">
        {err && <div className="error-banner">{err}</div>}
        {tab === 'conversations' && <ConversationsTable rows={conversations} objectives={objectives} />}
        {tab === 'objectives' && <ObjectivesTable rows={objectives} />}
      </div>
    </>
  );
}


// ---------------- conversations ---------------- //

function ConversationsTable({ rows, objectives }) {
  const [query, setQuery] = useState('');
  const [workFilter, setWorkFilter] = useState('all');
  const [expanded, setExpanded] = useState(null);

  const reportsByConv = useMemo(() => {
    const m = {};
    for (const o of (objectives || [])) {
      (m[o.conversation_id] ||= []).push(o);
    }
    return m;
  }, [objectives]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (workFilter === 'work' && !r.work_related) return false;
      if (workFilter === 'personal' && r.work_related) return false;
      if (!q) return true;
      const hay = [
        r.conversation_id, r.name,
        ...(r.objectives || []).map((o) => o.description),
        ...(r.tools_used || []),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [rows, query, workFilter]);

  if (!rows) return <div className="empty-state">No conversations.json yet. Finish Stage 2.</div>;

  return (
    <>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <input
          type="text"
          placeholder="Search name / objective / tool…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, padding: '6px 10px', fontSize: 13 }}
        />
        <select value={workFilter} onChange={(e) => setWorkFilter(e.target.value)}>
          <option value="all">All</option>
          <option value="work">Work-related only</option>
          <option value="personal">Personal only</option>
        </select>
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{filtered.length} / {rows.length}</span>
      </div>

      <table className="analysis-table">
        <thead>
          <tr>
            <th style={{ width: '40%' }}>Conversation</th>
            <th>Work?</th>
            <th>Turns</th>
            <th>Dur (s)</th>
            <th>Objectives</th>
            <th>Tools</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r) => (
            <ConversationRow
              key={r.conversation_id}
              row={r}
              reports={reportsByConv[r.conversation_id] || []}
              expanded={expanded === r.conversation_id}
              onToggle={() => setExpanded(expanded === r.conversation_id ? null : r.conversation_id)}
            />
          ))}
        </tbody>
      </table>
    </>
  );
}


function ConversationRow({ row, reports, expanded, onToggle }) {
  return (
    <>
      <tr className="analysis-row" onClick={onToggle}>
        <td>
          <span style={{ color: 'var(--text-faint)', marginRight: 6 }}>{expanded ? '▾' : '▸'}</span>
          {row.name || row.conversation_id.slice(0, 8)}
        </td>
        <td>{row.work_related === true ? 'yes' : row.work_related === false ? 'no' : '—'}</td>
        <td style={{ fontFamily: 'var(--mono)' }}>{row.num_turns ?? '—'}</td>
        <td style={{ fontFamily: 'var(--mono)' }}>{row.conversation_duration_sec ?? '—'}</td>
        <td style={{ fontFamily: 'var(--mono)' }}>{(row.objectives || []).length}</td>
        <td style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          {(row.tools_used || []).join(', ') || '—'}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} style={{ background: 'var(--bg-elev-2)', padding: 12 }}>
            <ConversationDetail row={row} reports={reports} />
          </td>
        </tr>
      )}
    </>
  );
}


function ConversationDetail({ row, reports }) {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 10, fontSize: 12 }}>
        <Stat label="Initial prompt length">{row.initial_prompt_length ?? '—'}</Stat>
        <Stat label="Avg user msg">{row.avg_message_length_user ?? '—'}</Stat>
        <Stat label="Avg assistant msg">{row.avg_message_length_assistant ?? '—'}</Stat>
        <Stat label="Attachments">{row.attachments?.count ?? 0} {(row.attachments?.types || []).length ? `(${row.attachments.types.join(', ')})` : ''}</Stat>
        <Stat label="Artifacts">{row.artifacts_created?.count ?? 0} {(row.artifacts_created?.types || []).length ? `(${row.artifacts_created.types.join(', ')})` : ''}</Stat>
        <Stat label="Models">{(row.models_used || []).join(', ') || '—'}</Stat>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: 6 }}>
        Objectives
      </div>
      {(row.objectives || []).map((o) => {
        const rep = reports.find((r) => r.objective_id === o.objective_id);
        return (
          <div key={o.objective_id} style={{ padding: 10, background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 4, marginBottom: 6, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: 'var(--text)' }}>
              #{o.objective_id} · {o.description}
            </div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-faint)', marginTop: 2 }}>
              turns {o.turn_indices?.join(', ')}
              {rep?.domain && ` · ${rep.domain}`}
            </div>
            {rep?.underlying_intent && (
              <div style={{ marginTop: 6, color: 'var(--text-dim)' }}>{rep.underlying_intent}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}


function Stat({ label, children }) {
  return (
    <div style={{ padding: 8, background: 'var(--bg-elev)', borderRadius: 4, border: '1px solid var(--border)' }}>
      <div style={{ fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 13, color: 'var(--text)', marginTop: 2 }}>{children}</div>
    </div>
  );
}


// ---------------- objectives ---------------- //

function ObjectivesTable({ rows }) {
  const [query, setQuery] = useState('');
  const [domainFilter, setDomainFilter] = useState('');
  const [expanded, setExpanded] = useState(null);

  const domains = useMemo(() => {
    if (!rows) return [];
    const set = new Set();
    for (const r of rows) if (r.domain) set.add(r.domain);
    return [...set].sort();
  }, [rows]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (domainFilter && r.domain !== domainFilter) return false;
      if (!q) return true;
      const hay = [
        r.description, r.topic, r.underlying_intent, r.deliverable, r.domain,
      ].filter(Boolean).join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [rows, query, domainFilter]);

  if (!rows) return <div className="empty-state">No objectives.json yet. Finish Stage 3.</div>;

  return (
    <>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <input
          type="text"
          placeholder="Search description / topic / intent…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, padding: '6px 10px', fontSize: 13 }}
        />
        <select value={domainFilter} onChange={(e) => setDomainFilter(e.target.value)}>
          <option value="">All domains</option>
          {domains.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{filtered.length} / {rows.length}</span>
      </div>

      <table className="analysis-table">
        <thead>
          <tr>
            <th style={{ width: '40%' }}>Objective</th>
            <th>Domain</th>
            <th style={{ width: '35%' }}>Topic</th>
            <th>Conv</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r, i) => {
            const key = `${r.conversation_id}:${r.objective_id}`;
            return (
              <ObjectiveRow
                key={key}
                row={r}
                expanded={expanded === key}
                onToggle={() => setExpanded(expanded === key ? null : key)}
              />
            );
          })}
        </tbody>
      </table>
    </>
  );
}


function ObjectiveRow({ row, expanded, onToggle }) {
  return (
    <>
      <tr className="analysis-row" onClick={onToggle}>
        <td>
          <span style={{ color: 'var(--text-faint)', marginRight: 6 }}>{expanded ? '▾' : '▸'}</span>
          {row.description}
        </td>
        <td><span className="code-tag" style={{ fontSize: 10 }}>{row.domain || '—'}</span></td>
        <td style={{ color: 'var(--text-dim)', fontSize: 12 }}>{row.topic || '—'}</td>
        <td style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)' }}>
          {row.conversation_id.slice(0, 8)}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={4} style={{ background: 'var(--bg-elev-2)', padding: 14 }}>
            <ObjectiveDetail row={row} />
          </td>
        </tr>
      )}
    </>
  );
}


function ObjectiveDetail({ row }) {
  return (
    <div style={{ fontSize: 13, lineHeight: 1.55 }}>
      <Section label="Underlying intent">{row.underlying_intent}</Section>
      <Section label="Deliverable">{row.deliverable}</Section>
      <Section label="Workflow & resolution">{row.workflow_and_resolution}</Section>
      <Section label="User approach">{row.user_approach}</Section>
      <Section label="User signals">{row.user_signals}</Section>
      <Section label="Language & tone">{row.language_and_tone}</Section>
      {row.additional_notes && <Section label="Additional notes">{row.additional_notes}</Section>}
    </div>
  );
}


function Section({ label, children }) {
  if (!children) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ whiteSpace: 'pre-wrap', color: 'var(--text)' }}>{children}</div>
    </div>
  );
}
