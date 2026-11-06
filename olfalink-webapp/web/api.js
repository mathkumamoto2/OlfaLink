async function asJson(response) {
  if (!response.ok) {
    let message = `HTTP ${response.status}: サーバーから応答がありません。`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === 'string') message = payload.detail;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}
export async function getDashboard({ signal } = {}) {
  return asJson(await fetch('/api/v1/dashboard', {
    signal, credentials:'same-origin',cache:'no-store'
  }));
}
export async function inferNow({ signal } = {}) {
  return asJson(await fetch('/api/v1/inference/on-demand', {
    method:'POST', signal, credentials:'same-origin',cache:'no-store',
    headers:{'Accept':'application/json','Content-Type':'application/json'},
    body:'{}'
  }));
}
