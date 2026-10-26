const API_BASE_URL = "http://127.0.0.1:8000";

async function apiRequest(path, options = {}) {
  // SCRUM-44: attach the verifiable access token when present so RBAC-protected
  // endpoints can enforce 401/403. Falls back to no auth header for public
  // endpoints (register/verify/login/forgot/reset), which stay unchanged.
  const token = localStorage.getItem("access_token");
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(`${API_BASE_URL}${path}`, { headers, ...options });
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    // SCRUM-69: FastAPI sends the reason in "detail" (a text, or a list of
    // validation errors). Show it, so the user sees e.g. "Invalid email or
    // password." instead of only "Something went wrong."
    let message = data.message;
    if (!message && typeof data.detail === "string") message = data.detail;
    if (!message && Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) {
      message = String(data.detail[0].msg).replace(/^Value error, /, "");
    }
    throw new Error(message || "Something went wrong.");
  }
  return data;
}
