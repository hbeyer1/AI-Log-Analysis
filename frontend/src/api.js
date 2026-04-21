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

  // Extract (Part 1a)
  extractEstimate: (body) => jsonPost('/api/extract/estimate', body),
  extractRun: (body) => jsonPost('/api/extract/run', body),
  extractResult: () => get('/api/extract/result'),
  extractRejected: () => get('/api/extract/rejected'),

  // Knowledge base
  getKb: () => get('/api/knowledge-base'),
  saveKb: (body) => jsonPut('/api/knowledge-base', body),

  // Clean (Part 1b)
  cleanEstimate: (body) => jsonPost('/api/clean/estimate', body),
  cleanRun: (body) => jsonPost('/api/clean/run', body),
  cleanResult: () => get('/api/clean/result'),
  cleanExcluded: () => get('/api/clean/excluded'),

  job: (id) => get(`/api/jobs/${id}`),
};
