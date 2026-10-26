/* SCRUM-56: Progress sharing -- the patient side (SRS 3.2.3.5).
 *
 *   GET /api/sharing/doctors   doctors you can share with
 *   GET /api/sharing/me        what you share + review status of your plan
 *   PUT /api/sharing/me        share / change / turn off (all three off)
 */
(() => {
  "use strict";

  const ITEMS = [["daily_log", "📒 Daily Log"], ["plan", "🍽️ Diet plan"], ["progress", "📈 Progress"]];
  const REVIEW = {
    pending: ["Waiting for review", "is-pending"],
    approved: ["Approved", "is-approved"],
    changes_needed: ["Changes needed", "is-changes"],
    not_needed: ["Not needed", "is-none"],
  };
  const state = { doctors: [], data: null, busy: false };
  const $ = (id) => document.getElementById(id);

  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function niceTime(value) {
    if (!value) return "";
    const text = String(value);
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`); // server sends UTC
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function initials(name) {
    return String(name || "?").replace(/^Dr\.?\s*/i, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
  }

  async function request(path, options = {}) {
    const token = localStorage.getItem("access_token");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    } catch {
      throw new Error("Can't reach the DietRx server. Check that the backend is running.");
    }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (res.status === 401) {
      setTimeout(() => { window.location.href = "login.html"; }, 1800);
      throw new Error("Your session has expired. Please log in again.");
    }
    if (res.status === 403) throw new Error("This page is for patients. Please log in with your patient account.");
    if (!res.ok) {
      let msg = "Something went wrong. Please try again.";
      if (data && typeof data.detail === "string") msg = data.detail;
      else if (data && Array.isArray(data.detail) && data.detail[0]) msg = String(data.detail[0].msg).replace(/^Value error, /, "");
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("sh-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3600);
  }
  function setStatus(msg, isError) {
    $("sh-status").textContent = msg || "";
    $("sh-status").className = "sh-status-line" + (isError ? " is-error" : "");
  }

  // ---------- render ----------
  function renderDoctors() {
    const select = $("sh-doctor");
    if (!state.doctors.length) {
      select.innerHTML = '<option value="">No doctors are registered yet</option>';
      return;
    }
    const chosen = select.value;
    select.innerHTML = '<option value="">Choose a doctor…</option>' + state.doctors.map((d) =>
      `<option value="${d.doctor_id}">${escapeHtml(d.name)}${d.specialization ? ` – ${escapeHtml(d.specialization)}` : ""}</option>`).join("");
    if (chosen) select.value = chosen;
  }

  function fillForm(doctorId) {
    const row = (state.data?.shares || []).find((s) => String(s.doctor.doctor_id) === String(doctorId));
    ITEMS.forEach(([key]) => { $(`sh-${key}`).checked = Boolean(row && row.status === "ACTIVE" && row.shares[key]); });
    $("sh-save").textContent = row && row.status === "ACTIVE" ? "Update sharing" : "Save sharing";
  }

  function renderShares() {
    const shares = state.data?.shares || [];
    const active = shares.filter((s) => s.status === "ACTIVE").length;
    $("sh-count").textContent = String(active);
    $("sh-count-label").textContent = active === 1 ? "doctor" : "doctors";
    $("sh-list-count").textContent = String(active);
    if (!shares.length) {
      $("sh-list").innerHTML = '<p class="sh-empty">You are not sharing anything yet. Pick a doctor above.</p>';
      return;
    }
    $("sh-list").innerHTML = shares.map((s) => {
      const off = s.status !== "ACTIVE";
      const chips = ITEMS.map(([key, label]) =>
        `<span class="sh-chip${s.shares[key] && !off ? "" : " is-off"}">${label}</span>`).join("");
      return `<div class="sh-row${off ? " is-off" : ""}">
          <div class="sh-who"><span class="sh-avatar" aria-hidden="true">${escapeHtml(initials(s.doctor.name))}</span>
            <div><b>${escapeHtml(s.doctor.name)}</b><small>${off ? "Sharing turned off" : escapeHtml(s.doctor.specialization || "Doctor")}</small>
            <div class="sh-chips">${chips}</div></div></div>
          <div class="sh-actions">
            <button type="button" class="sh-btn sh-btn-soft" data-edit="${s.doctor.doctor_id}">${off ? "Share again" : "Change"}</button>
            ${off ? "" : `<button type="button" class="sh-btn sh-btn-ghost" data-off="${s.doctor.doctor_id}">Turn off</button>`}
          </div></div>`;
    }).join("");
  }

  function reviewItem(r) {
    const [label, cls] = r.decision === "APPROVED" ? REVIEW.approved : REVIEW.changes_needed;
    return `<li><b>${escapeHtml(r.doctor.name)}</b> <span class="sh-review-status ${cls}">${label}</span>
      <span class="sh-when">${escapeHtml(niceTime(r.reviewed_at))}</span>
      ${r.note ? `<p>${escapeHtml(r.note)}</p>` : ""}</li>`;
  }

  function renderReview() {
    const review = state.data?.plan_review;
    const pill = $("sh-review-pill");
    if (!review) {
      pill.textContent = "No plan";
      pill.className = "sh-review-status is-none";
      $("sh-review").innerHTML = '<p class="sh-muted">You don\'t have a diet plan yet. <a href="diet_plan.html">Create one</a> first.</p>';
    } else {
      const [label, cls] = REVIEW[review.status] || REVIEW.not_needed;
      pill.textContent = label;
      pill.className = `sh-review-status ${cls}`;
      let html = "";
      if (review.requires_review) {
        html += '<p class="sh-muted">Your current plan has a dietary conflict that a professional should check.</p>';
        if (review.conflicts.length) html += `<ul class="sh-conflicts">${review.conflicts.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`;
        if (review.status === "pending") {
          html += review.shared_with_a_doctor
            ? '<p class="sh-band is-info">⏳ Your doctor can see this plan and will review it.</p>'
            : '<p class="sh-band is-alert">Share your <b>diet plan</b> with a doctor so it can be reviewed.</p>';
        }
      } else {
        html += '<p class="sh-muted">Your current plan does not need a professional review. You can still share it with your doctor.</p>';
      }
      const latest = review.reviews[0];
      if (latest) html += `<ul class="sh-timeline">${reviewItem(latest)}</ul>`;
      $("sh-review").innerHTML = html;
    }
    const history = state.data?.history || [];
    $("sh-history").innerHTML = history.length
      ? `<ul class="sh-timeline">${history.map(reviewItem).join("")}</ul>`
      : '<p class="sh-muted">No reviews yet.</p>';
  }

  function render() {
    renderDoctors();
    renderShares();
    renderReview();
    if ($("sh-doctor").value) fillForm($("sh-doctor").value);
  }

  // ---------- actions ----------
  async function load() {
    setStatus("Loading your sharing settings…");
    try {
      const [doctors, data] = await Promise.all([request("/api/sharing/doctors"), request("/api/sharing/me")]);
      state.doctors = doctors.doctors || [];
      state.data = data;
      render();
      setStatus("");
    } catch (err) {
      setStatus(err.message, true);
      $("sh-list").innerHTML = `<p class="sh-empty">${escapeHtml(err.message)}</p>`;
      $("sh-review").innerHTML = "";
    }
  }

  async function save(doctorId, flags) {
    if (state.busy) return;
    state.busy = true;
    $("sh-save").disabled = true;
    try {
      const data = await request("/api/sharing/me", {
        method: "PUT",
        body: JSON.stringify({ doctor_id: Number(doctorId), ...flags }),
      });
      state.data = data;
      render();
      toast(data.message || "Saved.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.busy = false;
      $("sh-save").disabled = false;
    }
  }

  function init() {
    $("sh-doctor").addEventListener("change", (e) => fillForm(e.target.value));
    $("sh-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const doctorId = $("sh-doctor").value;
      if (!doctorId) { toast("Please choose a doctor first.", true); return; }
      const flags = Object.fromEntries(ITEMS.map(([key]) => [key, $(`sh-${key}`).checked]));
      save(doctorId, flags);
    });
    $("sh-list").addEventListener("click", (e) => {
      const edit = e.target.closest("[data-edit]");
      const off = e.target.closest("[data-off]");
      if (edit) {
        $("sh-doctor").value = edit.dataset.edit;
        fillForm(edit.dataset.edit);
        $("sh-form").scrollIntoView({ behavior: "smooth", block: "center" });
      } else if (off) {
        save(off.dataset.off, { daily_log: false, plan: false, progress: false });
      }
    });
    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.replace("login.html");
    });
    load();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
