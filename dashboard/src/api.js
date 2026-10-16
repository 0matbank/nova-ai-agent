// The dashboard token arrives once in the URL fragment (#token=…, never sent to a
// server), is kept for this browser tab only, and goes out as a Bearer header — a
// cross-site page cannot read it, so it cannot call the API (no CSRF).
const KEY = "nova-dashboard-token";

export function takeToken() {
  const m = window.location.hash.match(/token=([A-Za-z0-9_-]+)/);
  if (m) {
    sessionStorage.setItem(KEY, m[1]);
    history.replaceState(null, "", window.location.pathname);
  }
  return sessionStorage.getItem(KEY);
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const token = sessionStorage.getItem(KEY) || "";
  const res = await fetch(`/api/${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || j.error || detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}
