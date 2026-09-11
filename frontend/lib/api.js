const API = '';

export function token() {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('rehabai_token');
}

export async function api(path, body, method) {
  const headers = {};
  const t = token();
  if (t) headers.Authorization = 'Bearer ' + t;
  const options = { headers };
  if (body !== undefined || method === 'POST') {
    options.method = method || 'POST';
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body || {});
  }
  const res = await fetch((API || '') + '/api/' + path.replace(/^\//, ''), options);
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed');
  return data;
}
