/* SCRUM-55: Today's balance - dietary drift + adaptive compensator (SRS 3.2.3.4).
 *
 *   GET /api/drift/today?client_date=YYYY-MM-DD&client_time=HH:MM
 *
 * Recalculated from the Daily Log every time, so it is always up to date.
 */
(() => {
  "use strict";

  const MEAL_ICONS = { breakfast: "🍳", "mid-morning snack": "🍎", lunch: "🍛", "afternoon snack": "🥜", dinner: "🌙" };
  const STATE_LABEL = { eaten: "Eaten", substituted: "Swapped", skipped: "Skipped", pending: "Coming up" };
  const EVENT_ICON = { skipped: "⏭️", substituted: "🔄", extra: "➕", allergy: "⚠️" };
  const VERDICT = {
    on_track: { cls: "", icon: "✅", title: "On track" },
    adjusted: { cls: "is-adjusted", icon: "⚖️", title: "Rebalanced for you" },
    suggestion_only: { cls: "is-suggestion", icon: "💡", title: "Suggestion only" },
    professional_review: { cls: "is-review", icon: "🩺", title: "Check with your doctor" },
    day_complete: { cls: "", icon: "🌙", title: "Day complete" },
  };

  const $ = (id) => document.getElementById(id);

  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function pad(n) { return String(n).padStart(2, "0"); }
  function localDate() { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function localTime() { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function fmt(n) { return Number(n || 0).toLocaleString(); }

  async function request(path) {
    const token = localStorage.getItem("access_token");
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`${API_BASE_URL}${path}`, { headers });
    } catch {
      throw new Error("Can't reach the DietRx server. Check that the backend is running.");
    }
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (res.status === 401) {
      setTimeout(() => { window.location.href = "login.html"; }, 1800);
      throw new Error("Your session has expired. Please log in again.");
    }
    if (!res.ok) throw new Error((data && typeof data.detail === "string") ? data.detail : "Something went wrong. Please try again.");
    return data;
  }

  function setStatus(msg, kind) {
    $("dr-status-line").textContent = msg || "";
    $("dr-status-line").className = "dr-status-line" + (kind ? " is-" + kind : "");
  }

  function renderHero(d) {
    const pct = d.calorie_target ? (d.consumed_kcal / d.calorie_target) * 100 : 0;
    $("dr-eaten").textContent = fmt(d.consumed_kcal);
    $("dr-target").textContent = fmt(d.calorie_target);
    $("dr-ring").style.setProperty("--pct", pct.toFixed(1));
    $("dr-ring").classList.toggle("is-over", d.over_target_kcal > 0);
    $("dr-left").textContent = d.over_target_kcal > 0
      ? `${fmt(d.over_target_kcal)} kcal over today`
      : `${fmt(d.remaining_kcal)} kcal left today`;
    $("dr-eyebrow").textContent = `Today's balance · ${d.now}`;
    const badges = [`<span class="dr-badge is-ok">📅 Plan day ${d.day_number}</span>`];
    const counts = {};
    (d.events || []).forEach((e) => { counts[e.type] = (counts[e.type] || 0) + 1; });
    if (counts.skipped) badges.push(`<span class="dr-badge">⏭️ ${counts.skipped} skipped</span>`);
    if (counts.substituted) badges.push(`<span class="dr-badge">🔄 ${counts.substituted} swapped</span>`);
    if (counts.extra) badges.push(`<span class="dr-badge">➕ ${counts.extra} extra</span>`);
    if (counts.allergy) badges.push(`<span class="dr-badge">⚠️ allergy alert</span>`);
    $("dr-badges").innerHTML = badges.join("");
  }

  function renderVerdict(d) {
    const v = VERDICT[d.status] || VERDICT.on_track;
    const box = $("dr-verdict");
    box.className = "dr-verdict " + v.cls;
    box.hidden = false;
    $("dr-verdict-icon").textContent = v.icon;
    $("dr-verdict-title").textContent = v.title;
    let text = d.message || "";
    if (d.status === "suggestion_only" || d.status === "professional_review") {
      if (d.needed_change_percent && d.applied_change_percent !== d.needed_change_percent) {
        text += ` Your remaining meals would need ${d.needed_change_percent > 0 ? "+" : ""}${d.needed_change_percent}% to match your target exactly; the safe limit is ±${d.limit_percent}%.`;
      }
    }
    $("dr-verdict-text").textContent = text;
  }

  function mealHtml(m) {
    const icon = MEAL_ICONS[String(m.meal_type).toLowerCase()] || "🍽️";
    const adjusted = m.adjusted_kcal !== null && m.adjusted_kcal !== undefined;
    let kcal;
    if (adjusted) {
      const up = m.change_percent > 0;
      kcal = `<s>${fmt(m.planned_kcal)}</s> → <b>${fmt(m.adjusted_kcal)} kcal</b><span class="dr-delta ${up ? "is-up" : "is-down"}">${up ? "+" : ""}${m.change_percent}%</span>`;
    } else if (m.state === "eaten" || m.state === "substituted") {
      kcal = m.actual_kcal === null || m.actual_kcal === undefined
        ? "Calories unknown"
        : `<b>${fmt(m.actual_kcal)} kcal</b> eaten · planned ${fmt(m.planned_kcal)}`;
    } else {
      kcal = `${m.state === "skipped" ? "Planned" : "Plan"}: <b>${fmt(m.planned_kcal)} kcal</b>`;
    }
    const logged = m.logged && m.logged.length && m.state === "substituted"
      ? `<div class="dr-logged">You had: ${escapeHtml(m.logged.join(", "))}</div>` : "";
    return `
      <li class="dr-step is-${m.state}${adjusted ? " is-adjusted" : ""}">
        <span class="dr-dot" aria-hidden="true">${icon}</span>
        <div class="dr-body">
          <div class="dr-top">
            <div><span class="dr-time">${escapeHtml(m.time || "")}</span><h3>${escapeHtml(m.meal_type)}</h3></div>
            <span class="dr-tag is-${m.state}">${STATE_LABEL[m.state] || m.state}</span>
          </div>
          <div class="dr-items">${(m.items || []).map((it) => `<span>${escapeHtml(it)}</span>`).join("")}</div>
          <div class="dr-kcal">${kcal}</div>
          ${logged}
          ${m.explanation ? `<p class="dr-why">💬 ${escapeHtml(m.explanation)}</p>` : ""}
        </div>
      </li>`;
  }

  function renderEvents(d) {
    const events = d.events || [];
    $("dr-event-count").textContent = events.length;
    $("dr-events").innerHTML = events.length
      ? events.map((e) => {
          const cls = e.severity === "HIGH" ? "is-high" : (e.severity === "CAUTION" ? "is-caution" : "");
          return `<li class="${cls}"><span>${EVENT_ICON[e.type] || "•"}</span><div>${escapeHtml(e.message)}</div></li>`;
        }).join("")
      : `<li class="dr-none">Nothing has drifted from your plan so far today. 🌿</li>`;
    $("dr-limit").textContent = d.limit_percent;
    $("dr-notes").innerHTML = (d.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
    if (d.disclaimer) $("dr-disclaimer").textContent = `${d.disclaimer} DietRx is a decision-support system and does not replace professional medical advice.`;
  }

  async function load() {
    setStatus("Checking today's meals…", "loading");
    try {
      const d = await request(`/api/drift/today?client_date=${localDate()}&client_time=${localTime()}`);
      if (!d.found) {
        $("dr-content").hidden = true;
        $("dr-verdict").hidden = true;
        $("dr-empty").hidden = false;
        $("dr-empty-text").textContent = d.message || $("dr-empty-text").textContent;
        $("dr-left").textContent = "No plan yet";
        setStatus("");
        return;
      }
      $("dr-empty").hidden = true;
      $("dr-content").hidden = false;
      $("dr-day-pill").textContent = `Day ${d.day_number}`;
      renderHero(d);
      renderVerdict(d);
      $("dr-timeline").innerHTML = (d.meals || []).map(mealHtml).join("");
      renderEvents(d);
      setStatus("");
    } catch (err) {
      setStatus(err.message, "error");
      $("dr-left").textContent = "Not loaded";
    }
  }

  function init() {
    $("dr-refresh").addEventListener("click", load);
    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.replace("login.html");
    });
    // Re-check every 5 minutes so a meal time passing is picked up.
    setInterval(load, 5 * 60 * 1000);
    load();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
