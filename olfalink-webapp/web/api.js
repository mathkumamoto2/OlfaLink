/** Single-origin HTTP adapter. All network reads go through this module. */
export async function getDashboard({ signal } = {}) {
  const response = await fetch('/api/v1/dashboard', { signal, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error(`Dashboard API returned HTTP ${response.status}`);
  return response.json();
}

export async function getReadings(limit = 120, { signal } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 240) throw new RangeError('limit must be 1..240');
  const response = await fetch(`/api/v1/readings?limit=${limit}`, { signal, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error(`Readings API returned HTTP ${response.status}`);
  return response.json();
}
