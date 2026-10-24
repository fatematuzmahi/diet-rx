// SCRUM-53: Daily Log page (SRS 3.2.3.2)
// Needs api.js loaded first (API_BASE_URL + the access_token convention).

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // Configuration
  // ---------------------------------------------------------------------------
  const TYPES = {
    MEAL: {
      icon: "🍽️", accent: "meal",
      titleLabel: "What did you eat?", titlePh: "Start typing, e.g. Rui Fish Curry",
      qtyLabel: "Servings", unit: "serving", qtyPh: "1",
    },
    WATER: {
      icon: "💧", accent: "water",
      qtyLabel: "Amount", unit: "ml", qtyPh: "250",
    },
    ACTIVITY: {
      icon: "🏃", accent: "activity",
      titleLabel: "Activity", titlePh: "e.g. Brisk walking",
      qtyLabel: "Duration", unit: "min", qtyPh: "30",
      statusLegend: "How did it go?",
      statuses: [["COMPLETED", "✅ Completed"], ["MODIFIED", "✏️ Modified"], ["SKIPPED", "⏭️ Skipped"]],
    },
    MEDICATION: {
      icon: "💊", accent: "med",
      titleLabel: "Medicine", titlePh: "e.g. Metformin 500 mg",
      qtyLabel: "Doses", unit: "dose", qtyPh: "1",
      statusLegend: "Did you take it?",
      statuses: [["TAKEN", "✅ Taken"], ["MISSED", "⚠️ Missed"], ["SKIPPED", "⏭️ Skipped"]],
    },
    GOAL: {
      icon: "🎯", accent: "goal",
      titleLabel: "Goal", titlePh: "e.g. No sugary drinks today",
      statusLegend: "Progress",
      statuses: [["ACHIEVED", "🏆 Achieved"], ["IN_PROGRESS", "⏳ In progress"], ["MISSED", "❌ Missed"]],
    },
  };

  const SLOT_LABELS = { BREAKFAST: "Breakfast", LUNCH: "Lunch", SNACK: "Snack", DINNER: "Dinner" };
  const STATUS_LABELS = {
    COMPLETED: ["Completed", "is-good"], MODIFIED: ["Modified", "is-warn"], SKIPPED: ["Skipped", "is-bad"],
    TAKEN: ["Taken", "is-good"], MISSED: ["Missed", "is-bad"],
    ACHIEVED: ["Achieved", "is-good"], IN_PROGRESS: ["In progress", "is-warn"],
  };
  const TARGETS = { water_ml: 2000, meals: 4, activity_min: 30 };
  const METRICS = {
    water_ml: { label: "Water", unit: "ml", accent: "water", target: TARGETS.water_ml },
    activity_min: { label: "Activity", unit: "min", accent: "activity", target: TARGETS.activity_min },
    meals: { label: "Meals", unit: "meals", accent: "meal", target: TARGETS.meals },
  };
  const MAX_BACKFILL_DAYS = 30;
  const RING_CIRCUMFERENCE = 2 * Math.PI * 34;

  const ICON_EDIT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>';
  const ICON_DELETE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>';

  const state = {
    date: todayStr(),
    type: "MEAL",
    editingId: null,
    editable: true,
    entries: [],
    dishes: new Map(),
    trendDays: 7,
    trendMetric: "water_ml",
    trend: null,
  };

  const $ = (id) => document.getElementById(id);
  const el = {};

  // ---------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------
  function pad(n) { return String(n).padStart(2, "0"); }
  // Local date (NOT toISOString, which is UTC and is a day behind in Dhaka before 6 am).
  function toDateStr(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function todayStr() { return toDateStr(new Date()); }
  function parseDate(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
  function addDays(s, n) { const d = parseDate(s); d.setDate(d.getDate() + n); return toDateStr(d); }
  function minDateStr() { return addDays(todayStr(), -MAX_BACKFILL_DAYS); }
  function nowTime() { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }

  function prettyDate(s) {
    if (s === todayStr()) return "Today";
    if (s === addDays(todayStr(), -1)) return "Yesterday";
    return parseDate(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmt(n) {
    const num = Number(n) || 0;
    return Number.isInteger(num) ? num.toLocaleString() : num.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }

  function guessSlot() {
    const h = new Date().getHours();
    if (h < 11) return "BREAKFAST";
    if (h < 15) return "LUNCH";
    if (h < 18) return "SNACK";
    return "DINNER";
  }

  // ---------------------------------------------------------------------------
  // API (same auth convention as api.js, but keeps the server's error message)
  // ---------------------------------------------------------------------------
  function errorMessage(data, statusCode) {
    if (data) {
      if (typeof data.message === "string") return data.message;
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return String(data.detail[0].msg || "Please check the form.").replace(/^Value error,\s*/i, "");
      }
    }
    if (statusCode === 404) return "That entry no longer exists.";
    return "Something went wrong. Please try again.";
  }

  async function request(path, options = {}) {
    const token = localStorage.getItem("access_token");
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;

    let response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
    } catch {
      throw new Error("Can't reach the DietRx server. Check that the backend is running.");
    }

    let data = null;
    try { data = await response.json(); } catch { data = null; }

    if (response.status === 401) {
      setTimeout(() => { window.location.href = "login.html"; }, 1800);
      throw new Error("Your session has expired. Please log in again.");
    }
    if (!response.ok) throw new Error(errorMessage(data, response.status));
    return data;
  }

  // ---------------------------------------------------------------------------
  // Toast
  // ---------------------------------------------------------------------------
  let toastTimer = null;
  function toast(message, isError = false) {
    el.toast.textContent = message;
    el.toast.classList.toggle("is-error", isError);
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2800);
  }

  // ---------------------------------------------------------------------------
  // Date navigation
  // ---------------------------------------------------------------------------
  function renderDateNav() {
    el.dateLabel.textContent = prettyDate(state.date);
    el.dateInput.value = state.date;
    el.dateInput.max = todayStr();
    el.dateInput.min = minDateStr();
    el.next.disabled = state.date >= todayStr();
    el.prev.disabled = state.date <= minDateStr();
  }

  function goToDate(dateStr) {
    if (!dateStr || dateStr > todayStr() || dateStr < minDateStr()) return;
    state.date = dateStr;
    cancelEdit();
    renderDateNav();
    el.time.value = state.date === todayStr() ? nowTime() : "";
    loadDay();
    if (state.trend) renderChart();
  }

  // ---------------------------------------------------------------------------
  // Form: entry type switching
  // ---------------------------------------------------------------------------
  function setType(type) {
    state.type = type;
    const cfg = TYPES[type];

    el.tabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.type === type)));
    document.querySelectorAll("#dl-form [data-for]").forEach((node) => {
      node.hidden = !node.dataset.for.split(" ").includes(type);
    });

    if (cfg.titleLabel) {
      el.titleLabel.textContent = cfg.titleLabel;
      el.title.placeholder = cfg.titlePh;
    }
    if (type === "MEAL") el.title.setAttribute("list", "dl-dish-options");
    else el.title.removeAttribute("list");

    if (cfg.qtyLabel) {
      el.qtyLabel.textContent = cfg.qtyLabel;
      el.qty.placeholder = cfg.qtyPh;
      el.unit.textContent = cfg.unit;
    }

    if (cfg.statuses) {
      el.statusLegend.textContent = cfg.statusLegend;
      el.statusOptions.innerHTML = cfg.statuses.map(([value, label], i) =>
        `<label class="dl-chip"><input type="radio" name="status" value="${value}" ${i === 0 ? "checked" : ""}><span>${label}</span></label>`
      ).join("");
    }

    updateTitleHint();
    hideFormError();
  }

  function resetForm() {
    el.form.reset();
    setType(state.type);
    const slot = el.form.querySelector(`input[name="slot"][value="${guessSlot()}"]`);
    if (slot) slot.checked = true;
    el.time.value = state.date === todayStr() ? nowTime() : "";
    updateTitleHint();
  }

  function updateTitleHint() {
    const dishId = currentDishId();
    if (state.type === "MEAL" && dishId) {
      el.titleHint.textContent = "✓ Linked to Dish DNA — nutrition comes from the dish record.";
      el.titleHint.hidden = false;
    } else {
      el.titleHint.hidden = true;
    }
  }

  function currentDishId() {
    if (state.type !== "MEAL") return null;
    return state.dishes.get(el.title.value.trim().toLowerCase()) || null;
  }

  function showFormError(message) {
    el.formError.textContent = message;
    el.formError.hidden = false;
  }
  function hideFormError() { el.formError.hidden = true; }

  function checkedValue(name) {
    const input = el.form.querySelector(`input[name="${name}"]:checked`);
    return input ? input.value : null;
  }

  function buildPayload() {
    const type = state.type;
    const title = el.title.value.trim();
    const qtyRaw = el.qty.value.trim();
    const quantity = qtyRaw === "" ? null : Number(qtyRaw);
    const payload = {
      log_type: type,
      log_date: state.date,
      log_time: el.time.value || null,
      notes: el.notes.value.trim() || null,
    };

    // Quick client-side checks; the server re-validates everything.
    if (type !== "WATER" && !title) return { error: `Please fill in "${TYPES[type].titleLabel}".` };
    if (quantity !== null && (Number.isNaN(quantity) || quantity < 0)) return { error: "Please enter a valid number." };

    if (type === "MEAL") {
      Object.assign(payload, { slot: checkedValue("slot"), title, dish_id: currentDishId(), quantity });
      if (!payload.slot) return { error: "Choose which meal this was." };
    } else if (type === "WATER") {
      if (quantity === null) return { error: "Enter how much water you drank." };
      payload.quantity = quantity;
    } else if (type === "ACTIVITY") {
      Object.assign(payload, { title, quantity, status: checkedValue("status") });
      if (payload.status !== "SKIPPED" && quantity === null) return { error: "Enter how many minutes it lasted." };
    } else if (type === "MEDICATION") {
      Object.assign(payload, { title, quantity, status: checkedValue("status") });
    } else if (type === "GOAL") {
      Object.assign(payload, { title, status: checkedValue("status") });
    }
    return { payload };
  }

  async function onSubmit(event) {
    event.preventDefault();
    hideFormError();
    const { payload, error } = buildPayload();
    if (error) { showFormError(error); return; }

    const editing = state.editingId;
    el.submit.disabled = true;
    el.submit.textContent = editing ? "Updating…" : "Saving…";
    try {
      if (editing) {
        await request(`/api/daily-log/${editing}`, { method: "PUT", body: JSON.stringify(payload) });
        toast("Entry updated ✓");
      } else {
        await request("/api/daily-log", { method: "POST", body: JSON.stringify(payload) });
        toast(`${TYPES[payload.log_type].icon} Saved ✓`);
      }
      cancelEdit();
      await Promise.all([loadDay(), loadTrend()]);
    } catch (err) {
      showFormError(err.message);
    } finally {
      el.submit.disabled = false;
      el.submit.textContent = state.editingId ? "Update entry" : "Save entry";
    }
  }

  // ---------------------------------------------------------------------------
  // Editing & deleting
  // ---------------------------------------------------------------------------
  function startEdit(id) {
    const entry = state.entries.find((e) => e.log_id === id);
    if (!entry) return;
    state.editingId = id;
    setType(entry.log_type);

    el.title.value = entry.title && entry.log_type !== "WATER" ? entry.title : "";
    el.qty.value = entry.quantity ?? "";
    el.time.value = entry.log_time || "";
    el.notes.value = entry.notes || "";
    if (entry.slot) {
      const slot = el.form.querySelector(`input[name="slot"][value="${entry.slot}"]`);
      if (slot) slot.checked = true;
    }
    if (entry.status) {
      const status = el.form.querySelector(`input[name="status"][value="${entry.status}"]`);
      if (status) status.checked = true;
    }
    updateTitleHint();

    el.formTitle.textContent = "Edit entry";
    el.submit.textContent = "Update entry";
    el.cancelEdit.hidden = false;
    el.formCard.classList.add("is-editing");
    el.formCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function cancelEdit() {
    state.editingId = null;
    el.formTitle.textContent = "Add an entry";
    el.submit.textContent = "Save entry";
    el.cancelEdit.hidden = true;
    el.formCard.classList.remove("is-editing");
    resetForm();
  }

  const pendingDelete = new Map();
  async function onDeleteClick(button, id) {
    // Two-step delete: first click asks, second click (within 3 s) deletes.
    if (!pendingDelete.has(id)) {
      button.classList.add("is-confirm");
      button.textContent = "Delete?";
      pendingDelete.set(id, setTimeout(() => {
        pendingDelete.delete(id);
        button.classList.remove("is-confirm");
        button.innerHTML = ICON_DELETE;
      }, 3000));
      return;
    }
    clearTimeout(pendingDelete.get(id));
    pendingDelete.delete(id);
    button.disabled = true;
    try {
      await request(`/api/daily-log/${id}`, { method: "DELETE" });
      if (state.editingId === id) cancelEdit();
      toast("Entry deleted");
      await Promise.all([loadDay(), loadTrend()]);
    } catch (err) {
      toast(err.message, true);
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Quick water
  // ---------------------------------------------------------------------------
  async function quickWater(ml, button) {
    button.disabled = true;
    try {
      await request("/api/daily-log", {
        method: "POST",
        body: JSON.stringify({
          log_type: "WATER",
          log_date: state.date,
          log_time: state.date === todayStr() ? nowTime() : null,
          quantity: ml,
        }),
      });
      toast(`💧 +${ml} ml added`);
      await Promise.all([loadDay(), loadTrend()]);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = !state.editable;
    }
  }

  // ---------------------------------------------------------------------------
  // Rendering: rings, banner, timeline
  // ---------------------------------------------------------------------------
  function setRing(id, ratio) {
    const bar = $(id);
    const clamped = Math.max(0, Math.min(ratio || 0, 1));
    bar.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - clamped));
    bar.closest(".dl-ring-card").classList.toggle("is-done", clamped >= 1);
  }

  function renderRings(summary) {
    const s = summary || {};
    $("val-water").textContent = fmt(s.water_ml);
    $("val-meal").textContent = fmt(s.meals);
    $("val-activity").textContent = fmt(s.activity_min);
    setRing("ring-water", (s.water_ml || 0) / TARGETS.water_ml);
    setRing("ring-meal", (s.meals || 0) / TARGETS.meals);
    setRing("ring-activity", (s.activity_min || 0) / TARGETS.activity_min);

    if (s.meds_total) {
      $("val-med").textContent = `${s.meds_taken}/${s.meds_total}`;
      $("val-med-sub").textContent = " taken";
      setRing("ring-med", s.meds_taken / s.meds_total);
    } else {
      $("val-med").textContent = "—";
      $("val-med-sub").textContent = " none logged";
      setRing("ring-med", 0);
    }
  }

  function renderBanner(planId, editable) {
    if (!editable) {
      el.banner.className = "dl-banner is-warning";
      el.banner.textContent = `🔒 Entries older than ${MAX_BACKFILL_DAYS} days are read-only.`;
      el.banner.hidden = false;
    } else if (planId) {
      el.banner.className = "dl-banner";
      el.banner.innerHTML = `📋 New entries are linked to your current plan <strong>#${planId}</strong>.`;
      el.banner.hidden = false;
    } else {
      el.banner.className = "dl-banner is-warning";
      el.banner.innerHTML = `⚠️ You don't have a diet plan yet, so entries won't be linked to one. <a href="diet_plan.html">Create a plan</a>`;
      el.banner.hidden = false;
    }
  }

  function entryMeta(entry) {
    const parts = [];
    if (entry.log_time) parts.push(entry.log_time);
    if (entry.slot) parts.push(SLOT_LABELS[entry.slot] || entry.slot);
    if (entry.quantity !== null && entry.quantity !== undefined) {
      const unit = entry.unit === "serving" || entry.unit === "dose"
        ? `${entry.unit}${Number(entry.quantity) === 1 ? "" : "s"}`
        : entry.unit || "";
      parts.push(`${fmt(entry.quantity)} ${unit}`.trim());
    }
    if (entry.dish_id) parts.push("Dish DNA");
    return parts.join(" · ");
  }

  function renderTimeline() {
    el.count.textContent = state.entries.length;

    if (!state.entries.length) {
      el.timeline.innerHTML = `
        <div class="dl-empty">
          <span class="dl-empty-emoji" aria-hidden="true">📝</span>
          <strong>Nothing logged for ${escapeHtml(prettyDate(state.date).toLowerCase() === "today" ? "today" : prettyDate(state.date))} yet</strong>
          Use the form or a quick-add button to record your first entry.
        </div>`;
      return;
    }

    el.timeline.innerHTML = `<ul class="dl-timeline">${state.entries.map((entry, i) => {
      const cfg = TYPES[entry.log_type] || TYPES.MEAL;
      const status = entry.status && STATUS_LABELS[entry.status];
      const badge = status ? `<span class="dl-badge ${status[1]}">${status[0]}</span>` : "";
      const actions = state.editable ? `
        <div class="dl-entry-actions">
          <button type="button" data-edit="${entry.log_id}" aria-label="Edit entry" title="Edit">${ICON_EDIT}</button>
          <button type="button" data-delete="${entry.log_id}" aria-label="Delete entry" title="Delete">${ICON_DELETE}</button>
        </div>` : "";
      return `
        <li class="dl-entry dl-c-${cfg.accent}" style="animation-delay:${Math.min(i, 8) * 40}ms">
          <span class="dl-entry-icon" aria-hidden="true">${cfg.icon}</span>
          <div class="dl-entry-body">
            <div class="dl-entry-top"><strong>${escapeHtml(entry.title || "Entry")}</strong>${badge}</div>
            <p class="dl-entry-meta">${escapeHtml(entryMeta(entry))}</p>
            ${entry.notes ? `<p class="dl-entry-notes">“${escapeHtml(entry.notes)}”</p>` : ""}
          </div>
          ${actions}
        </li>`;
    }).join("")}</ul>`;
  }

  function renderTimelineLoading() {
    el.timeline.innerHTML = '<div class="dl-skeleton"></div><div class="dl-skeleton"></div><div class="dl-skeleton"></div>';
  }

  function renderTimelineError(message) {
    el.count.textContent = "–";
    el.timeline.innerHTML = `
      <div class="dl-error-state">
        <span class="dl-empty-emoji" aria-hidden="true">😕</span>
        <strong>Couldn't load your entries</strong>
        <div>${escapeHtml(message)}</div>
        <button type="button" id="dl-retry">Try again</button>
      </div>`;
    $("dl-retry").addEventListener("click", loadDay);
  }

  function setEditable(editable) {
    state.editable = editable;
    el.submit.disabled = !editable;
    document.querySelectorAll("[data-water], [data-quick-tab]").forEach((b) => { b.disabled = !editable; });
  }

  // ---------------------------------------------------------------------------
  // Rendering: trend chart
  // ---------------------------------------------------------------------------
  function renderChart() {
    const metric = METRICS[state.trendMetric];
    el.chart.className = `dl-chart dl-c-${metric.accent}`;

    if (!state.trend) return;
    const points = state.trend.points || [];
    const values = points.map((p) => Number(p[state.trendMetric]) || 0);
    const max = Math.max(metric.target * 1.15, ...values, 1);
    const many = points.length > 14;

    const bars = points.map((p, i) => {
      const value = values[i];
      const d = parseDate(p.log_date);
      let label = many ? String(d.getDate()) : d.toLocaleDateString(undefined, { weekday: "short" });
      if (many && i % 5 !== 0 && i !== points.length - 1) label = "";
      const selected = p.log_date === state.date ? " is-selected" : "";
      const empty = value === 0 ? " is-empty" : "";
      const full = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
      return `
        <button type="button" class="dl-bar-col${selected}${empty}" data-date="${p.log_date}"
                title="${full}: ${fmt(value)} ${metric.unit}" aria-label="${full}: ${fmt(value)} ${metric.unit}">
          <span class="dl-bar-value" style="bottom:${(value / max) * 100}%">${value ? fmt(value) : ""}</span>
          <span class="dl-bar" data-h="${(value / max) * 100}"></span>
          <span class="dl-bar-label">${label}</span>
        </button>`;
    }).join("");

    const targetPct = (metric.target / max) * 100;
    el.chart.innerHTML = `${bars}
      <div class="dl-target-line" style="bottom:calc(28px + (100% - 40px) * ${targetPct / 100})">
        <span>Target ${fmt(metric.target)} ${metric.unit}</span>
      </div>`;

    // Animate bars from zero.
    requestAnimationFrame(() => {
      el.chart.querySelectorAll(".dl-bar").forEach((bar) => { bar.style.height = `${bar.dataset.h}%`; });
    });

    const total = values.reduce((a, b) => a + b, 0);
    const avg = points.length ? total / points.length : 0;
    el.trendNote.textContent =
      `Average ${fmt(Math.round(avg * 10) / 10)} ${metric.unit} per day · ` +
      `you logged something on ${state.trend.days_logged} of ${state.trend.days} days.`;

    // Streak: consecutive days with at least one entry, ending today (or yesterday).
    let i = points.length - 1;
    if (i >= 0 && points[i].entries === 0) i -= 1;
    let streak = 0;
    while (i >= 0 && points[i].entries > 0) { streak += 1; i -= 1; }
    el.streak.hidden = streak < 2;
    el.streak.textContent = `🔥 ${streak}-day logging streak`;
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------
  let dayRequest = 0;
  async function loadDay() {
    const ticket = ++dayRequest;
    renderTimelineLoading();
    try {
      const data = await request(`/api/daily-log?log_date=${state.date}`);
      if (ticket !== dayRequest) return; // a newer day was requested meanwhile
      state.entries = data.entries || [];
      setEditable(data.editable !== false);
      renderRings(data.summary);
      renderBanner(data.plan_id, data.editable !== false);
      renderTimeline();
    } catch (err) {
      if (ticket !== dayRequest) return;
      state.entries = [];
      renderRings(null);
      renderTimelineError(err.message);
    }
  }

  async function loadTrend() {
    try {
      state.trend = await request(`/api/daily-log/trend?days=${state.trendDays}`);
      renderChart();
    } catch (err) {
      el.chart.innerHTML = `<div class="dl-error-state" style="width:100%">${escapeHtml(err.message)}</div>`;
      el.trendNote.textContent = "";
    }
  }

  async function loadDishes() {
    try {
      const data = await request("/api/dish-dna");
      const dishes = data.items || data.dishes || [];
      state.dishes.clear();
      el.dishOptions.innerHTML = dishes.map((dish) => {
        const id = dish.dish_id ?? dish.id;
        if (dish.name && id) state.dishes.set(String(dish.name).toLowerCase(), Number(id));
        return `<option value="${escapeHtml(dish.name)}"></option>`;
      }).join("");
    } catch {
      // Dish DNA suggestions are optional; typing a food name still works.
    }
  }

  // ---------------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------------
  function init() {
    Object.assign(el, {
      dateLabel: $("dl-date-label"), dateInput: $("dl-date"), prev: $("dl-prev"), next: $("dl-next"),
      banner: $("dl-plan-banner"), form: $("dl-form"), formCard: $("dl-form-card"), formTitle: $("dl-form-title"),
      cancelEdit: $("dl-cancel-edit"), title: $("dl-title"), titleLabel: $("dl-title-label"), titleHint: $("dl-title-hint"),
      qty: $("dl-quantity"), qtyLabel: $("dl-quantity-label"), unit: $("dl-unit"),
      statusLegend: $("dl-status-legend"), statusOptions: $("dl-status-options"),
      time: $("dl-time"), notes: $("dl-notes"), formError: $("dl-form-error"), submit: $("dl-submit"),
      timeline: $("dl-timeline"), count: $("dl-count"), chart: $("dl-chart"), trendNote: $("dl-trend-note"),
      streak: $("dl-streak"), toast: $("dl-toast"), dishOptions: $("dl-dish-options"),
      tabs: Array.from(document.querySelectorAll(".dl-tab")),
    });

    renderDateNav();
    el.prev.addEventListener("click", () => goToDate(addDays(state.date, -1)));
    el.next.addEventListener("click", () => goToDate(addDays(state.date, 1)));
    el.dateInput.addEventListener("change", () => goToDate(el.dateInput.value));

    el.tabs.forEach((tab) => tab.addEventListener("click", () => {
      if (state.editingId) cancelEdit();
      setType(tab.dataset.type);
      resetForm();
    }));

    el.form.addEventListener("submit", onSubmit);
    el.cancelEdit.addEventListener("click", cancelEdit);
    el.title.addEventListener("input", updateTitleHint);
    el.form.addEventListener("input", hideFormError);

    document.querySelectorAll("[data-preset]").forEach((btn) =>
      btn.addEventListener("click", () => { el.qty.value = btn.dataset.preset; hideFormError(); })
    );
    document.querySelectorAll("[data-water]").forEach((btn) =>
      btn.addEventListener("click", () => quickWater(Number(btn.dataset.water), btn))
    );
    document.querySelectorAll("[data-quick-tab]").forEach((btn) =>
      btn.addEventListener("click", () => {
        if (state.editingId) cancelEdit();
        setType(btn.dataset.quickTab);
        resetForm();
        el.formCard.scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(() => el.title.focus(), 350);
      })
    );

    el.timeline.addEventListener("click", (event) => {
      const editBtn = event.target.closest("[data-edit]");
      const deleteBtn = event.target.closest("[data-delete]");
      if (editBtn) startEdit(Number(editBtn.dataset.edit));
      if (deleteBtn) onDeleteClick(deleteBtn, Number(deleteBtn.dataset.delete));
    });

    el.chart.addEventListener("click", (event) => {
      const col = event.target.closest("[data-date]");
      if (col) goToDate(col.dataset.date);
    });
    document.querySelectorAll("[data-metric]").forEach((btn) => btn.addEventListener("click", () => {
      document.querySelectorAll("[data-metric]").forEach((b) => b.classList.toggle("is-active", b === btn));
      state.trendMetric = btn.dataset.metric;
      renderChart();
    }));
    document.querySelectorAll("[data-days]").forEach((btn) => btn.addEventListener("click", () => {
      document.querySelectorAll("[data-days]").forEach((b) => b.classList.toggle("is-active", b === btn));
      state.trendDays = Number(btn.dataset.days);
      loadTrend();
    }));

    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (event) => {
      event.preventDefault();
      localStorage.removeItem("access_token");
      window.location.href = "login.html";
    });

    setType("MEAL");
    resetForm();
    renderChart();
    loadDay();
    loadTrend();
    loadDishes();
  }

  document.addEventListener("DOMContentLoaded", init);
})();