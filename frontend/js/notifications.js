// SCRUM-51: Reminders & Alerts page (SRS 3.1.3.4)
// Needs api.js loaded first (API_BASE_URL + the access_token convention).

(() => {
  "use strict";

  const ICONS = { MEAL: "🍽️", WATER: "💧", MEDICATION: "💊", EXERCISE: "🏃", GOAL: "🎯" };
  const TYPE_LABELS = { MEAL: "Meal", WATER: "Water", MEDICATION: "Medicine", EXERCISE: "Exercise", GOAL: "Goal" };
  const DAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const DAY_SHORT = { MON: "Mon", TUE: "Tue", WED: "Wed", THU: "Thu", FRI: "Fri", SAT: "Sat", SUN: "Sun" };
  const POLL_MS = 60000;
  const SEEN_KEY = "dietrx_seen_alerts";

  const state = { reminders: [], editingId: null, feed: [] };
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function pad(n) { return String(n).padStart(2, "0"); }
  function localDate() { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function localTime() { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function daysLabel(days) {
    if (!days || days.length === 7) return "Every day";
    const set = new Set(days);
    if (days.length === 5 && !set.has("SAT") && !set.has("SUN")) return "Weekdays";
    if (days.length === 2 && set.has("SAT") && set.has("SUN")) return "Weekend";
    return days.map((d) => DAY_SHORT[d] || d).join(", ");
  }
  function timeOf(stamp) {
    if (!stamp) return "";
    const d = new Date(stamp);
    return Number.isNaN(d.getTime()) ? "" : `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function errorMessage(data, statusCode) {
    if (data) {
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return String(data.detail[0].msg || "Please check the form.").replace(/^Value error,\s*/i, "");
      }
    }
    if (statusCode === 404) return "That item no longer exists.";
    return "Something went wrong. Please try again.";
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
    if (!res.ok) throw new Error(errorMessage(data, res.status));
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("nt-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
  }

  // ---------- feed ----------
  function feedItemHtml(item) {
    const icon = item.category === "SAFETY" ? (item.severity === "HIGH" ? "⚠️" : "🛡️") : (ICONS[item.reminder_type] || "🔔");
    const done = !!item.acknowledged_at;
    const sevBadge = item.category === "SAFETY"
      ? `<span class="nt-badge is-${item.severity}">${item.severity === "HIGH" ? "IMPORTANT" : "CHECK"}</span>` : "";
    const ackBadge = done
      ? `<span class="nt-badge is-${item.ack_action === "DONE" ? "done" : "dismissed"}">${item.ack_action === "DONE" ? "Done" : "Dismissed"} ${escapeHtml(timeOf(item.acknowledged_at))}</span>` : "";
    const doneLabel = item.category === "SAFETY" ? "Got it" : "Done";
    const logLink = !done && item.category === "REMINDER"
      ? `<a class="nt-mini" href="daily_log.html">Log it</a>` : "";
    const actions = done ? "" : `
      <div class="nt-item-actions">
        <button type="button" class="nt-mini is-primary" data-ack="${item.event_id}" data-action="DONE">✓ ${doneLabel}</button>
        ${logLink}
        <button type="button" class="nt-mini" data-ack="${item.event_id}" data-action="DISMISSED">Dismiss</button>
      </div>`;
    return `
      <li class="nt-item nt-t-${escapeHtml(item.reminder_type || "GOAL")} is-${item.category === "SAFETY" ? item.severity : "INFO"}${done ? " is-done" : ""}">
        <span class="nt-item-icon" aria-hidden="true">${icon}</span>
        <div class="nt-item-body">
          <div class="nt-item-top"><strong>${escapeHtml(item.title)}</strong>${sevBadge}${ackBadge}</div>
          ${item.message ? `<p>${escapeHtml(item.message)}</p>` : ""}
          ${actions}
        </div>
      </li>`;
  }

  function renderFeed(data) {
    const items = data.items || [];
    state.feed = items;
    $("nt-unread").textContent = data.unread || 0;
    document.querySelector(".nt-bell").classList.toggle("has-unread", (data.unread || 0) > 0);
    document.title = data.unread ? `(${data.unread}) Reminders | DietRx` : "Reminders | DietRx";

    if (!items.length) {
      $("nt-feed").innerHTML = `
        <div class="nt-empty">
          <span>🌿</span>
          <strong>All clear for now</strong>
          Reminders appear here when their time comes.
        </div>`;
      return;
    }
    const safety = items.filter((i) => i.category === "SAFETY");
    const reminders = items.filter((i) => i.category !== "SAFETY");
    $("nt-feed").innerHTML =
      (safety.length ? `<p class="nt-section-label">Safety</p><ul class="nt-feed">${safety.map(feedItemHtml).join("")}</ul>` : "") +
      (reminders.length ? `<p class="nt-section-label">Reminders</p><ul class="nt-feed">${reminders.map(feedItemHtml).join("")}</ul>` : "");
  }

  function readSeen() {
    try { return new Set(JSON.parse(sessionStorage.getItem(SEEN_KEY) || "[]")); } catch { return new Set(); }
  }
  function writeSeen(set) {
    try { sessionStorage.setItem(SEEN_KEY, JSON.stringify([...set].slice(-200))); } catch { /* ignore */ }
  }

  function notifyNew(items, firstLoad) {
    const seen = readSeen();
    const fresh = items.filter((i) => !i.acknowledged_at && !seen.has(i.event_id));
    fresh.forEach((i) => seen.add(i.event_id));
    writeSeen(seen);
    if (firstLoad || !fresh.length) return;
    toast(`🔔 ${fresh[0].title}`);
    if ("Notification" in window && Notification.permission === "granted") {
      fresh.slice(0, 3).forEach((i) => {
        try { new Notification(`DietRx: ${i.title}`, { body: i.message || "", tag: `dietrx-${i.event_id}` }); } catch { /* ignore */ }
      });
    }
  }

  let firstFeed = true;
  async function loadFeed() {
    if (firstFeed) $("nt-feed").innerHTML = '<div class="nt-skeleton"></div><div class="nt-skeleton"></div>';
    try {
      const data = await request(`/api/notifications/feed?client_date=${localDate()}&client_time=${localTime()}`);
      renderFeed(data);
      notifyNew(data.items || [], firstFeed);
    } catch (err) {
      $("nt-feed").innerHTML = `<div class="nt-empty"><span>😕</span><strong>Couldn't load notifications</strong>${escapeHtml(err.message)}</div>`;
    }
    firstFeed = false;
  }

  async function acknowledge(btn) {
    btn.disabled = true;
    try {
      await request(`/api/notifications/events/${btn.dataset.ack}/ack`, {
        method: "POST",
        body: JSON.stringify({ action: btn.dataset.action }),
      });
      toast(btn.dataset.action === "DONE" ? "Marked as done ✓" : "Dismissed");
      await Promise.all([loadFeed(), loadStats()]);
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
    }
  }

  // ---------- stats ----------
  async function loadStats() {
    try {
      const s = await request("/api/notifications/stats?days=7");
      $("st-delivered").textContent = s.delivered;
      $("st-rate").textContent = `${s.done_rate}%`;
      $("st-pending").textContent = s.pending;
      $("st-safety").textContent = s.safety_alerts;
    } catch { /* stats are optional */ }
  }

  // ---------- reminders list ----------
  function renderList() {
    const list = state.reminders;
    $("nt-count").textContent = list.length;
    if (!list.length) {
      $("nt-list").innerHTML = `
        <div class="nt-empty">
          <span>⏰</span>
          <strong>No reminders yet</strong>
          Add your own above, or start with a ready-made daily routine.
          <br><button type="button" class="nt-btn" id="nt-starter">✨ Add starter reminders</button>
        </div>`;
      $("nt-starter").addEventListener("click", addStarter);
      return;
    }
    $("nt-list").innerHTML = `<div class="nt-list">${list.map((r) => {
      const [hh, mm] = r.remind_time.split(":");
      return `
        <div class="nt-rem nt-t-${escapeHtml(r.reminder_type)}${r.enabled ? "" : " is-off"}">
          <div class="nt-rem-time">${escapeHtml(hh)}:${escapeHtml(mm)}<small>${ICONS[r.reminder_type] || "🔔"}</small></div>
          <div class="nt-rem-body">
            <strong>${escapeHtml(r.title)}</strong>
            <small>${escapeHtml(TYPE_LABELS[r.reminder_type] || r.reminder_type)} · ${escapeHtml(daysLabel(r.repeat_days))}</small>
          </div>
          <div class="nt-rem-actions">
            <label class="nt-switch" title="${r.enabled ? "Turn off" : "Turn on"}">
              <input type="checkbox" data-toggle="${r.notification_id}" ${r.enabled ? "checked" : ""} aria-label="Reminder on or off">
              <span></span>
            </label>
            <button type="button" class="nt-icon-btn" data-edit="${r.notification_id}" aria-label="Edit reminder" title="Edit">✏️</button>
            <button type="button" class="nt-icon-btn" data-delete="${r.notification_id}" aria-label="Delete reminder" title="Delete">🗑️</button>
          </div>
        </div>`;
    }).join("")}</div>`;
  }

  async function loadReminders() {
    $("nt-list").innerHTML = '<div class="nt-skeleton"></div>';
    try {
      const data = await request("/api/notifications/reminders");
      state.reminders = data.reminders || [];
      renderList();
    } catch (err) {
      $("nt-list").innerHTML = `<div class="nt-empty"><span>😕</span><strong>Couldn't load reminders</strong>${escapeHtml(err.message)}</div>`;
    }
  }
  async function addStarter(event) {
    event.currentTarget.disabled = true;
    try {
      const data = await request("/api/notifications/reminders/starter", { method: "POST" });
      state.reminders = data.reminders || [];
      renderList();
      toast("✨ Starter reminders added");
      loadFeed();
    } catch (err) {
      toast(err.message, true);
    }
  }

  function payloadFrom(r, overrides = {}) {
    return {
      reminder_type: r.reminder_type,
      title: r.title,
      message: r.message || null,
      remind_time: r.remind_time,
      repeat_days: r.repeat_days,
      enabled: r.enabled,
      ...overrides,
    };
  }

  async function toggle(input) {
    const id = Number(input.dataset.toggle);
    const r = state.reminders.find((x) => x.notification_id === id);
    if (!r) return;
    input.disabled = true;
    try {
      const updated = await request(`/api/notifications/reminders/${id}`, {
        method: "PUT",
        body: JSON.stringify(payloadFrom(r, { enabled: input.checked })),
      });
      Object.assign(r, updated);
      renderList();
      toast(updated.enabled ? `🔔 “${updated.title}” is on` : `🔕 “${updated.title}” is off`);
    } catch (err) {
      input.checked = !input.checked;
      input.disabled = false;
      toast(err.message, true);
    }
  }

  const pendingDelete = new Map();
  async function onDelete(btn) {
    const id = Number(btn.dataset.delete);
    if (!pendingDelete.has(id)) {
      btn.classList.add("is-confirm");
      btn.textContent = "Delete?";
      pendingDelete.set(id, setTimeout(() => {
        pendingDelete.delete(id);
        btn.classList.remove("is-confirm");
        btn.textContent = "🗑️";
      }, 3000));
      return;
    }
    clearTimeout(pendingDelete.get(id));
    pendingDelete.delete(id);
    btn.disabled = true;
    try {
      await request(`/api/notifications/reminders/${id}`, { method: "DELETE" });
      state.reminders = state.reminders.filter((r) => r.notification_id !== id);
      if (state.editingId === id) cancelEdit();
      renderList();
      toast("Reminder deleted");
    } catch (err) {
      btn.disabled = false;
      toast(err.message, true);
    }
  }

  // ---------- form ----------
  function setDays(days) {
    document.querySelectorAll("#nt-days input").forEach((box) => { box.checked = days.includes(box.value); });
  }
  function checkedDays() {
    return [...document.querySelectorAll("#nt-days input:checked")].map((b) => b.value);
  }
  function showError(msg) { $("nt-form-error").textContent = msg; $("nt-form-error").hidden = false; }
  function hideError() { $("nt-form-error").hidden = true; }

  function startEdit(id) {
    const r = state.reminders.find((x) => x.notification_id === id);
    if (!r) return;
    state.editingId = id;
    const radio = document.querySelector(`input[name="rtype"][value="${r.reminder_type}"]`);
    if (radio) radio.checked = true;
    $("nt-title").value = r.title;
    $("nt-time").value = r.remind_time;
    $("nt-message").value = r.message || "";
    setDays(r.repeat_days);
    $("nt-form-title").textContent = "Edit reminder";
    $("nt-submit").textContent = "Update reminder";
    $("nt-cancel-edit").hidden = false;
    $("nt-form-card").classList.add("is-editing");
    $("nt-form-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function cancelEdit() {
    state.editingId = null;
    $("nt-form").reset();
    setDays(DAY_CODES);
    $("nt-form-title").textContent = "New reminder";
    $("nt-submit").textContent = "Save reminder";
    $("nt-cancel-edit").hidden = true;
    $("nt-form-card").classList.remove("is-editing");
    hideError();
  }

  async function onSubmit(event) {
    event.preventDefault();
    hideError();
    const type = document.querySelector('input[name="rtype"]:checked').value;
    const title = $("nt-title").value.trim() || TYPE_LABELS[type];
    const days = checkedDays();
    if (!$("nt-time").value) return showError("Pick a time for this reminder.");
    if (!days.length) return showError("Pick at least one day.");

    const existing = state.reminders.find((x) => x.notification_id === state.editingId);
    const body = {
      reminder_type: type,
      title,
      message: $("nt-message").value.trim() || null,
      remind_time: $("nt-time").value.slice(0, 5),
      repeat_days: days,
      enabled: existing ? existing.enabled : true,
    };
    const btn = $("nt-submit");
    btn.disabled = true;
    try {
      if (state.editingId) {
        await request(`/api/notifications/reminders/${state.editingId}`, { method: "PUT", body: JSON.stringify(body) });
        toast("Reminder updated ✓");
      } else {
        await request("/api/notifications/reminders", { method: "POST", body: JSON.stringify(body) });
        toast(`${ICONS[type]} Reminder saved for ${body.remind_time}`);
      }
      cancelEdit();
      await Promise.all([loadReminders(), loadFeed()]);
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- desktop alerts ----------
  function renderDesktopBtn() {
    const btn = $("nt-desktop-btn");
    if (!("Notification" in window)) { btn.hidden = true; return; }
    if (Notification.permission === "granted") { btn.textContent = "✅ Desktop alerts on"; btn.disabled = true; }
    else if (Notification.permission === "denied") { btn.textContent = "🔕 Alerts blocked in browser"; btn.disabled = true; }
  }

  // ---------- start ----------
  function init() {
    $("nt-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

    $("nt-form").addEventListener("submit", onSubmit);
    $("nt-form").addEventListener("input", hideError);
    $("nt-cancel-edit").addEventListener("click", cancelEdit);
    $("nt-refresh").addEventListener("click", () => { loadFeed(); loadStats(); });

    document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
      const v = b.dataset.days;
      setDays(v === "WEEKDAYS" ? DAY_CODES.slice(0, 5) : v === "WEEKEND" ? ["SAT", "SUN"] : DAY_CODES);
    }));
    document.querySelectorAll('input[name="rtype"]').forEach((radio) => radio.addEventListener("change", () => {
      if (!$("nt-title").value.trim() || Object.values(TYPE_LABELS).includes($("nt-title").value.trim())) {
        $("nt-title").value = TYPE_LABELS[radio.value];
      }
    }));

    $("nt-feed").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-ack]");
      if (btn) acknowledge(btn);
    });
    $("nt-list").addEventListener("click", (e) => {
      const edit = e.target.closest("[data-edit]");
      const del = e.target.closest("[data-delete]");
      if (edit) startEdit(Number(edit.dataset.edit));
      if (del) onDelete(del);
    });
    $("nt-list").addEventListener("change", (e) => {
      if (e.target.matches("[data-toggle]")) toggle(e.target);
    });

    const desktopBtn = $("nt-desktop-btn");
    desktopBtn.addEventListener("click", async () => {
      try { await Notification.requestPermission(); } catch { /* ignore */ }
      renderDesktopBtn();
      if (Notification.permission === "granted") toast("🔔 Desktop alerts turned on while this page is open");
    });
    renderDesktopBtn();

    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.href = "login.html";
    });

    $("nt-title").value = TYPE_LABELS.MEAL;
    loadReminders();
    loadFeed();
    loadStats();
    setInterval(() => { if (!document.hidden) loadFeed(); }, POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
})();