// Dashboard home: today's progress (SCRUM-53), current plan (SCRUM-48),
// activity guidance (SCRUM-50) and the weekly water chart.
// Needs api.js loaded first (API_BASE_URL).

(() => {
  "use strict";

  const TARGETS = { water_ml: 2000, meals: 4, activity_min: 30 };
  const RING = 2 * Math.PI * 52;
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function pad(n) { return String(n).padStart(2, "0"); }
  function nowTime() { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function fmt(n) { return (Number(n) || 0).toLocaleString(undefined, { maximumFractionDigits: 1 }); }
  function clamp01(x) { return Math.max(0, Math.min(Number(x) || 0, 1)); }
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  async function request(path, options = {}) {
    const token = localStorage.getItem("access_token");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    } catch {
      throw new Error("Can't reach the DietRx server.");
    }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (res.status === 401) throw new Error("Your session has expired. Please log in again.");
    if (!res.ok) throw new Error((data && typeof data.detail === "string" && data.detail) || "Something went wrong.");
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("db-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  function errorBox(message) {
    return `<div class="db-empty"><span>😕</span><p>${escapeHtml(message)}</p></div>`;
  }

  // ---------- hero ----------
  function renderHero() {
    const now = new Date();
    const h = now.getHours();
    $("db-greeting").textContent = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    $("db-date").textContent = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

    let name = "";
    for (const key of ["dietrx_user", "user"]) {
      try {
        const u = JSON.parse(localStorage.getItem(key) || "null");
        if (u) name = u.name || u.full_name || u.username || (u.email ? String(u.email).split("@")[0] : "");
      } catch { /* ignore */ }
      if (name) break;
    }
    $("db-name").textContent = name ? String(name).split(" ")[0] : "there";
  }

  // ---------- today's stats ----------
  function setBar(id, ratio) { $(id).style.width = `${clamp01(ratio) * 100}%`; }

  function renderToday(s) {
    s = s || {};
    const waterL = (s.water_ml || 0) / 1000;
    $("stat-water").textContent = `${fmt(Math.round(waterL * 100) / 100)} L`;
    $("stat-meals").textContent = s.meals || 0;
    $("stat-activity").textContent = s.activity_sessions || 0;
    $("stat-activity-sub").textContent = `${fmt(s.activity_min)} of ${TARGETS.activity_min} min`;

    setBar("bar-water", (s.water_ml || 0) / TARGETS.water_ml);
    setBar("bar-meals", (s.meals || 0) / TARGETS.meals);
    setBar("bar-activity", (s.activity_min || 0) / TARGETS.activity_min);

    const parts = [
      clamp01((s.water_ml || 0) / TARGETS.water_ml),
      clamp01((s.meals || 0) / TARGETS.meals),
      clamp01((s.activity_min || 0) / TARGETS.activity_min),
    ];
    if (s.meds_total) {
      $("stat-med").textContent = `${s.meds_taken}/${s.meds_total}`;
      $("stat-med-sub").textContent = "doses taken";
      setBar("bar-med", s.meds_taken / s.meds_total);
      parts.push(clamp01(s.meds_taken / s.meds_total));
    } else {
      $("stat-med").textContent = "—";
      $("stat-med-sub").textContent = "none logged";
      setBar("bar-med", 0);
    }

    const score = Math.round((parts.reduce((a, b) => a + b, 0) / parts.length) * 100);
    $("db-score").textContent = `${score}%`;
    $("db-score-bar").style.strokeDashoffset = String(RING * (1 - score / 100));
  }

  async function loadToday() {
    try {
      const data = await request("/api/daily-log");
      renderToday(data.summary);
    } catch {
      renderToday(null);
    }
  }

  async function quickWater(btn) {
    btn.disabled = true;
    try {
      await request("/api/daily-log", {
        method: "POST",
        body: JSON.stringify({ log_type: "WATER", quantity: 250, log_time: nowTime() }),
      });
      toast("💧 +250 ml added. Nice!");
      await Promise.all([loadToday(), loadWeek()]);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- current plan ----------
  function todaysMeals(plan) {
    const days = Array.isArray(plan.days) ? plan.days : [];
    if (!days.length) return [];
    let index = 0;
    const start = plan.generated_at ? new Date(plan.generated_at) : null;
    if (start && !Number.isNaN(start.getTime())) {
      index = Math.max(0, Math.floor((Date.now() - start.getTime()) / 86400000)) % days.length;
    }
    const meals = days[index] && Array.isArray(days[index].meals) ? days[index].meals : [];
    return meals.slice(0, 4).map((m) => ({
      when: m.meal_type || m.type || m.slot || "",
      what: m.name || m.dish || m.title || (Array.isArray(m.items) ? m.items.join(", ") : ""),
    })).filter((m) => m.what);
  }

  async function loadPlan() {
    const box = $("db-plan-body");
    try {
      const plan = await request("/api/plans/me");
      if (!plan || !plan.found) {
        box.innerHTML = `
          <div class="db-empty">
            <span>🥗</span>
            <p>You don't have a diet plan yet.</p>
            <a class="db-btn db-btn-primary" href="diet_plan.html">Generate my plan</a>
          </div>`;
        return;
      }
      const meals = todaysMeals(plan);
      const review = plan.requires_review
        ? `<span class="db-badge is-warn">Needs professional review</span>`
        : `<span class="db-badge is-good">Ready to follow</span>`;
      box.innerHTML = `
        <div class="db-plan-top">
          <div>
            <span class="db-muted">Plan #${escapeHtml(plan.plan_id)}</span>
            <div class="db-big">${plan.calorie_target ? fmt(plan.calorie_target) : "—"}<small> kcal / day</small></div>
          </div>
          ${review}
        </div>
        <div class="db-chips">
          <span>📅 ${(plan.days || []).length}-day plan</span>
          <span>🏃 ${(plan.activities || []).length} activities</span>
          ${(plan.conflicts || []).length ? `<span>⚠️ ${(plan.conflicts || []).length} conflicts</span>` : ""}
        </div>
        ${meals.length ? `
          <p class="db-mini-title">Today's menu</p>
          <ul class="db-menu">
            ${meals.map((m) => `<li><span>${escapeHtml(m.when)}</span>${escapeHtml(m.what)}</li>`).join("")}
          </ul>` : ""}`;
    } catch (err) {
      box.innerHTML = errorBox(err.message);
    }
  }

  // ---------- activity guidance (SCRUM-50) ----------
  async function loadGuidance() {
    const box = $("db-guidance-body");
    try {
      const data = await request("/api/health-profile/activity-guidance");
      const tips = (data.guidance || []).map((t) => `<li class="is-tip"><span>✅</span>${escapeHtml(t)}</li>`);
      const warns = (data.warnings || []).map((t) => `<li class="is-warn"><span>⚠️</span>${escapeHtml(t)}</li>`);
      box.innerHTML = tips.length || warns.length
        ? `<ul class="db-tips">${tips.join("")}${warns.join("")}</ul>`
        : errorBox("No guidance yet. Complete your health profile below.");
    } catch (err) {
      box.innerHTML = errorBox(err.message);
    }
  }

  // ---------- weekly water ----------
  async function loadWeek() {
    const chart = $("db-week-chart");
    try {
      const trend = await request("/api/daily-log/trend?days=7");
      const points = trend.points || [];
      const values = points.map((p) => Number(p.water_ml) || 0);
      const max = Math.max(TARGETS.water_ml * 1.15, ...values, 1);
      chart.innerHTML = points.map((p, i) => {
        const [y, m, d] = String(p.log_date).split("-").map(Number);
        const day = new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short" });
        const v = values[i];
        const hit = v >= TARGETS.water_ml ? " is-hit" : "";
        return `<div class="db-col${hit}" title="${day}: ${fmt(v)} ml">
                  <span class="db-col-val" style="bottom:calc(${(v / max) * 100}% + 6px)">${v ? fmt(v / 1000) + "L" : ""}</span>
                  <span class="db-col-bar" data-h="${(v / max) * 100}"></span>
                  <span class="db-col-day">${day}</span>
                </div>`;
      }).join("") +
        `<div class="db-goal-line" style="bottom:calc(22px + (100% - 40px) * ${TARGETS.water_ml / max})"><span>2 L goal</span></div>`;
      requestAnimationFrame(() => {
        chart.querySelectorAll(".db-col-bar").forEach((b) => { b.style.height = `${b.dataset.h}%`; });
      });
      const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
      $("db-week-avg").textContent = `avg ${fmt(Math.round(avg))} ml / day`;
    } catch (err) {
      chart.innerHTML = errorBox(err.message);
    }
  }

  // ---------- start ----------
  document.addEventListener("DOMContentLoaded", () => {
    renderHero();
    renderToday(null);
    $("db-quick-water").addEventListener("click", (e) => quickWater(e.currentTarget));
    loadToday();
    loadPlan();
    loadGuidance();
    loadWeek();
  });
})();