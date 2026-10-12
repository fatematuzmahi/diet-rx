const API_BASE_URL = "http://127.0.0.1:8000";

async function apiRequest(path, options = {}) {
  // SCRUM-44: attach the verifiable access token when present so RBAC-protected
  // endpoints can enforce 401/403. Falls back to no auth header for public
  // endpoints (register/verify/login/forgot/reset), which stay unchanged.
  const token = localStorage.getItem("access_token");
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(`${API_BASE_URL}${path}`, { headers, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Something went wrong.");
  return data;
}
