const BASE = '';

async function handle(res) {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

function jsonPost(path, body) {
  return fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle);
}

function jsonPut(path, body) {
  return fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle);
}

const get = (path) => fetch(`${BASE}${path}`).then(handle);

export const api = {
  status: () => get('/api/status'),

  upload: (file) => {
    const fd = new FormData();
    fd.append('file', file);
    return fetch(`${BASE}/api/upload`, { method: 'POST', body: fd }).then(handle);
  },

  sessions: () => get('/api/sessions'),

  listPrompts: () => get('/api/prompts'),
  getPrompt: (name) => get(`/api/prompts/${name}`),
  savePrompt: (name, content) => jsonPut(`/api/prompts/${name}`, { content }),

  // Stage 1 — PII redaction
  piiRun: (body) => jsonPost('/api/pii/run', body),
  piiResult: () => get('/api/pii/result'),

  // Stage 2 — Prompt 1 (conversation features + objectives)
  prompt1Run: (body) => jsonPost('/api/prompt1/run', body),
  prompt1Result: () => get('/api/prompt1/result'),

  // Stage 3 — Prompt 2 (per-objective interview)
  prompt2Run: (body) => jsonPost('/api/prompt2/run', body),
  prompt2Result: () => get('/api/prompt2/result'),

  // Final published datasets
  finalConversations: () => get('/api/final/conversations'),
  finalObjectives: () => get('/api/final/objectives'),
  downloadBundleUrl: `${BASE}/api/final/bundle`,
  finalCostReport: () => get('/api/final/cost_report'),

  job: (id) => get(`/api/jobs/${id}`),
};
