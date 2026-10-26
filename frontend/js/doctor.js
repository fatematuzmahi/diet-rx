/* SCRUM-56: Doctor view + professional review (SRS 3.2.3.5.2 - 3.2.3.5.4).
 *
 *   POST /auth/doctor/login                         doctor login (existing, SCRUM-44)
 *   GET  /api/doctor/reviews                        plans waiting for review
 *   GET  /api/doctor/patients                       patients who share with me
 *   GET  /api/doctor/patients/{id}/plan|daily-log|progress
 *   POST /api/doctor/reviews                        save a review
 *
 * The server decides what the doctor may see (403 for anything not shared);
 * this page only hides tabs the patient did not switch on.
 */
(() => {
  "use strict";

  const TABS = { plan: "diet plan", daily_log: "Daily Log", progress: "progress" };
  const CHIPS = { plan: "Diet plan", daily_log: "Daily Log", progress: "Progress" };
  const REVIEW = {
    pending: ["Waiting", "is-pending"],
    approved: ["Approved", "is-approved"],
    changes_needed: ["Changes needed", "is-changes"],
    not_needed: ["No review needed", "is-none"],
  };
  const LOG_ICON = { MEAL: "🍽️", WATER: "💧", ACTIVITY: "🏃", MEDICATION: "💊", GOAL: "🎯" };
  const state = { queue: [], patients: [], open: null, tab: null, busy: false };
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
  function niceDay(value) {
    const d = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }
  function initials(name) {
    return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
  }
  function tokenRole() {
    const token = localStorage.getItem("access_token");
    if (!token) return null;
    try {
      const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(part)).role || null;
    } catch { return null; }
  }
  function statusPill(status) {
    const [label, cls] = REVIEW[status] || REVIEW.not_needed;
    return `<span class="sh-review-status ${cls}">${label}</span>`;
  }

  async function request(path, options = {}) {
    const token = localStorage.getItem("access_token");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token && !options.noAuth) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    } catch {
      throw new Error("Can't reach the DietRx server. Check that the backend is running.");
    }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (res.status === 401 && !options.noAuth) {
      localStorage.removeItem("access_token");
      showLogin("Your session has expired. Please log in again.");
      throw new Error("Your session has expired. Please log in again.");
    }
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
    $("dc-status").textContent = msg || "";
    $("dc-status").className = "sh-status-line" + (isError ? " is-error" : "");
  }

  // ---------- login ----------
  function showLogin(message) {
    $("dc-portal").hidden = true;
    $("dc-login").hidden = false;
    $("logoutBtn").hidden = true;
    if (message) toast(message, true);
  }

  async function login(e) {
    e.preventDefault();
    const btn = $("dc-login-btn");
    btn.disabled = true;
    try {
      const data = await request("/auth/doctor/login", {
        method: "POST",
        noAuth: true,
        body: JSON.stringify({ email: $("dc-email").value.trim(), password: $("dc-password").value }),
      });
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
      localStorage.removeItem("session_token"); // a doctor session is not a patient session
      $("dc-password").value = "";
      showPortal();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- lists ----------
  function renderQueue() {
    const pending = state.queue.filter((q) => q.review_status === "pending").length;
    $("dc-pending").textContent = String(pending);
    $("dc-queue-count").textContent = String(state.queue.length);
    $("dc-queue").innerHTML = state.queue.length ? state.queue.map((q) => `
      <div class="sh-queue-item">
        <div class="sh-who"><span class="sh-avatar" aria-hidden="true">${escapeHtml(initials(q.patient.name))}</span>
          <div><b>${escapeHtml(q.patient.name)}</b><small>Plan #${q.plan_id} · ${escapeHtml(niceTime(q.generated_at))}</small>
          <div class="sh-chips">${q.conflicts.slice(0, 2).map((c) => `<span class="sh-chip">${escapeHtml(c)}</span>`).join("")}</div></div></div>
        <div class="sh-actions">${statusPill(q.review_status)}
          <button type="button" class="sh-btn sh-btn-soft" data-open="${q.patient.patient_id}" data-tab="plan">Review</button></div>
      </div>`).join("")
      : '<p class="sh-muted">No plans are waiting for you. 🎉</p>';
  }

  function renderPatients() {
    $("dc-patient-count").textContent = String(state.patients.length);
    $("dc-patients-count").textContent = `${state.patients.length} ${state.patients.length === 1 ? "patient shares" : "patients share"} with you`;
    $("dc-patients").innerHTML = state.patients.length ? state.patients.map((p) => {
      const chips = Object.entries(CHIPS).map(([key, label]) =>
        `<span class="sh-chip${p.shares[key] ? "" : " is-off"}">${label}</span>`).join("");
      const isOpen = state.open && state.open.patient_id === p.patient_id;
      return `<div class="sh-patient${isOpen ? " is-open" : ""}">
          <div class="sh-who"><span class="sh-avatar" aria-hidden="true">${escapeHtml(initials(p.name))}</span>
            <div><b>${escapeHtml(p.name)}</b><div class="sh-chips">${chips}</div></div></div>
          <button type="button" class="sh-btn sh-btn-soft" data-open="${p.patient_id}">Open</button></div>`;
    }).join("")
      : '<p class="sh-muted">No patient is sharing with you yet. Patients choose this on their Sharing page.</p>';
  }

  async function loadLists() {
    const [reviews, patients] = await Promise.all([request("/api/doctor/reviews"), request("/api/doctor/patients")]);
    state.queue = reviews.reviews || [];
    state.patients = patients.patients || [];
    renderQueue();
    renderPatients();
  }

  // ---------- patient detail ----------
  function renderTabs() {
    const p = state.open;
    $("dc-tabs").hidden = !p;
    document.querySelectorAll(".sh-tab").forEach((btn) => {
      const key = btn.dataset.tab;
      const allowed = Boolean(p && p.shares[key]);
      btn.disabled = !allowed;
      btn.title = allowed ? "" : `${p ? p.name : "This patient"} has not shared their ${TABS[key]} with you`;
      btn.textContent = btn.textContent.replace(/ 🔒$/, "") + (allowed ? "" : " 🔒");
      btn.setAttribute("aria-selected", String(state.tab === key));
    });
  }

  function planHtml(data) {
    if (!data.found) return `<p class="sh-empty">${escapeHtml(data.message)}</p>`;
    const plan = data.plan;
    let html = `<div class="sh-actions" style="margin-bottom:12px">${statusPill(data.review_status)}
      <span class="sh-muted">Plan #${plan.plan_id}${plan.calorie_target ? ` · about ${plan.calorie_target} kcal a day` : ""}</span></div>`;
    if (plan.conflicts.length) {
      html += `<div class="sh-band ${plan.requires_review ? "is-alert" : "is-info"}"><b>Dietary conflicts</b><ul class="sh-conflicts">${plan.conflicts.map((c) =>
        `<li><b>${escapeHtml(c.title)}</b> – ${escapeHtml(c.explanation)}${c.resolution ? ` <i>(${escapeHtml(c.resolution)})</i>` : ""}</li>`).join("")}</ul></div>`;
    }
    if (plan.restrictions_applied.length) {
      html += `<p class="sh-muted" style="margin-top:12px"><b>Restrictions applied:</b> ${plan.restrictions_applied.map(escapeHtml).join(", ")}</p>`;
    }
    html += '<div style="margin-top:14px">' + plan.days.slice(0, 7).map((d) => `
      <div class="sh-day"><h3>${escapeHtml(d.label || `Day ${d.day}`)}${d.daily_calories ? ` · ${d.daily_calories} kcal` : ""}</h3>
      <ul>${d.meals.map((m) => `<li><b>${escapeHtml(m.meal_type)}</b> (${escapeHtml(m.time)}): ${m.items.map(escapeHtml).join(", ")}</li>`).join("")}</ul></div>`).join("") + "</div>";
    if (data.reviews.length) {
      html += `<h3 style="margin:18px 0 0">Reviews</h3><ul class="sh-timeline">${data.reviews.map((r) => `
        <li><b>${escapeHtml(r.doctor.name)}</b> ${statusPill(r.decision === "APPROVED" ? "approved" : "changes_needed")}
        <span class="sh-when">${escapeHtml(niceTime(r.reviewed_at))}</span>${r.note ? `<p>${escapeHtml(r.note)}</p>` : ""}</li>`).join("")}</ul>`;
    }
    html += `<form id="dc-review-form" class="sh-form" style="margin-top:18px" data-plan="${plan.plan_id}">
      <h3 style="margin:0">Your review</h3>
      <div class="sh-decisions" role="radiogroup" aria-label="Decision">
        <label class="sh-decision"><input type="radio" name="decision" value="APPROVED" required> ✅ Approve plan</label>
        <label class="sh-decision"><input type="radio" name="decision" value="CHANGES_NEEDED"> ✏️ Changes needed</label>
      </div>
      <label class="sh-label" for="dc-note">Note for the patient
        <textarea id="dc-note" maxlength="1000" placeholder="Needed when you ask for changes"></textarea></label>
      <div class="sh-actions"><button type="submit" class="sh-btn sh-btn-primary" id="dc-review-btn">Save review</button></div>
    </form>`;
    return html;
  }

  function dailyLogHtml(data) {
    const s = data.summary;
    let html = `<div class="sh-stats">
      <div class="sh-stat"><strong>${s.meals}</strong><small>meals logged</small></div>
      <div class="sh-stat"><strong>${Math.round(s.water_ml)}</strong><small>ml water</small></div>
      <div class="sh-stat"><strong>${Math.round(s.activity_min)}</strong><small>active minutes</small></div>
      <div class="sh-stat"><strong>${s.meds_taken}/${s.meds_total}</strong><small>medicines taken</small></div></div>`;
    if (!data.entries.length) return html + '<p class="sh-empty">Nothing logged in the last 7 days.</p>';
    html += `<div class="sh-table-wrap"><table class="sh-table"><thead><tr><th>Day</th><th>Type</th><th>What</th><th>Amount</th><th>Status</th></tr></thead><tbody>
      ${data.entries.map((e) => `<tr><td>${escapeHtml(niceDay(e.log_date))}${e.log_time ? ` ${escapeHtml(e.log_time)}` : ""}</td>
        <td>${LOG_ICON[e.log_type] || ""} ${escapeHtml(e.log_type.toLowerCase())}</td><td>${escapeHtml(e.title || "")}</td>
        <td>${e.quantity ?? ""} ${escapeHtml(e.unit || "")}</td><td>${escapeHtml((e.status || "").toLowerCase())}</td></tr>`).join("")}
      </tbody></table></div>`;
    return html;
  }

  function progressHtml(data) {
    const t = data.totals;
    const max = Math.max(2000, ...data.points.map((p) => p.water_ml));
    let html = `<div class="sh-stats">
      <div class="sh-stat"><strong>${data.days_logged}/${data.days}</strong><small>days logged</small></div>
      <div class="sh-stat"><strong>${t.meals}</strong><small>meals</small></div>
      <div class="sh-stat"><strong>${Math.round(t.activity_min)}</strong><small>active minutes</small></div>
      <div class="sh-stat"><strong>${t.meds_taken}/${t.meds_total}</strong><small>medicines taken</small></div></div>
      <p class="sh-muted" style="margin-bottom:10px">💧 Water each day (target 2000 ml)</p><div class="sh-bars">`;
    html += data.points.map((p) => `<div class="sh-bar-row"><span>${escapeHtml(niceDay(p.log_date))}</span>
      <div class="sh-bar" role="img" aria-label="${Math.round(p.water_ml)} ml"><span style="width:${Math.min(100, (p.water_ml / max) * 100)}%"></span></div>
      <span>${Math.round(p.water_ml)} ml</span></div>`).join("");
    return html + "</div>";
  }

  async function showTab(tab) {
    const p = state.open;
    if (!p || !p.shares[tab]) return;
    state.tab = tab;
    renderTabs();
    const panel = $("dc-panel");
    panel.innerHTML = '<p class="sh-muted">Loading…</p>';
    try {
      const base = `/api/doctor/patients/${p.patient_id}`;
      if (tab === "plan") panel.innerHTML = planHtml(await request(`${base}/plan`));
      else if (tab === "daily_log") panel.innerHTML = dailyLogHtml(await request(`${base}/daily-log?days=7`));
      else panel.innerHTML = progressHtml(await request(`${base}/progress?days=7`));
    } catch (err) {
      panel.innerHTML = `<p class="sh-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  function openPatient(patientId, preferred) {
    const p = state.patients.find((x) => String(x.patient_id) === String(patientId));
    if (!p) return;
    state.open = p;
    $("dc-detail-title").textContent = p.name;
    $("dc-detail-pill").textContent = "Shared with you";
    renderPatients();
    const first = [preferred, "plan", "daily_log", "progress"].find((k) => k && p.shares[k]);
    state.tab = first;
    renderTabs();
    showTab(first);
    $("dc-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function saveReview(form) {
    if (state.busy) return;
    const decision = form.querySelector('input[name="decision"]:checked');
    if (!decision) { toast("Choose Approve or Changes needed.", true); return; }
    state.busy = true;
    $("dc-review-btn").disabled = true;
    try {
      const data = await request("/api/doctor/reviews", {
        method: "POST",
        body: JSON.stringify({ plan_id: Number(form.dataset.plan), decision: decision.value, note: $("dc-note").value || null }),
      });
      toast(data.message || "Review saved.");
      await loadLists();
      const again = state.patients.find((x) => x.patient_id === state.open.patient_id);
      if (again) state.open = again;
      await showTab("plan");
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.busy = false;
      const btn = $("dc-review-btn");
      if (btn) btn.disabled = false;
    }
  }

  async function showPortal() {
    $("dc-login").hidden = true;
    $("dc-portal").hidden = false;
    $("logoutBtn").hidden = false;
    let user = {};
    try { user = JSON.parse(localStorage.getItem("user") || "{}") || {}; } catch { user = {}; }
    $("dc-hello").textContent = user.name ? `Welcome, ${user.name}` : "Doctor view";
    setStatus("Loading…");
    try {
      await loadLists();
      setStatus("");
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  function init() {
    $("dc-login-form").addEventListener("submit", login);
    document.addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]");
      if (open) openPatient(open.dataset.open, open.dataset.tab);
      const tab = e.target.closest(".sh-tab");
      if (tab && !tab.disabled) showTab(tab.dataset.tab);
    });
    document.addEventListener("submit", (e) => {
      if (e.target.id === "dc-review-form") { e.preventDefault(); saveReview(e.target); }
    });
    $("logoutBtn").addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      state.open = null;
      showLogin();
    });
    if (tokenRole() === "DOCTOR") showPortal(); else showLogin();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
