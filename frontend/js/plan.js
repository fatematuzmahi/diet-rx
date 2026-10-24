/* SCRUM-48: Personalized Diet + Activity Plan page (SRS 3.1.3.8).
 *
 * The patient is identified only by the access token, so this script never
 * sends a patient id -- just the optional overrides typed in "Customize".
 *
 *   POST /api/plans/me          generate (or regenerate) the plan
 *   GET  /api/plans/me          read the stored CURRENT plan
 *   GET  /api/plans/me/history  previous + current plans
 */
(() => {
  "use strict";

  const MEAL_ICONS = { breakfast: "🍳", "mid-morning snack": "🍎", lunch: "🍛", "afternoon snack": "🥜", dinner: "🌙" };
  const RULE_LABELS = {
    low_sodium: "🧂 Low sodium", high_sodium: "High sodium", low_sugar: "🍬 Low sugar", high_sugar: "High sugar",
    low_fat: "🥑 Low fat", high_fat: "High fat", low_carbs: "🍞 Low carb", high_carbs: "High carb",
    low_protein: "Low protein", high_protein: "💪 High protein", low_purine: "Low purine",
    vegetarian: "🥗 Vegetarian", vegan: "🌱 Vegan", gluten_free: "🌾 Gluten-free", lactose_free: "🥛 Lactose-free",
    avoid_exercise: "🛑 Limit exercise", increase_calories: "⬆️ More calories", reduce_calories: "⬇️ Fewer calories",
    increase_fluid: "💧 More fluids", avoid_sugar_alcohol: "🚫 No alcohol", iron_rich: "🩸 Iron-rich",
  };
  const SEVERITY = { high: "High", medium: "Medium", low: "Info" };

  const state = { plan: null, day: 0 };
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function round(n) { return Math.round((Number(n) || 0) * 10) / 10; }
  function niceDate(stamp) {
    if (!stamp) return "";
    const d = new Date(stamp);
    if (Number.isNaN(d.getTime())) return String(stamp);
    return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function errorMessage(data, statusCode) {
    if (data) {
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return String(data.detail[0].msg || "Please check the form.").replace(/^Value error,\s*/i, "");
      }
      if (typeof data.message === "string") return data.message;
    }
    if (statusCode === 404) return "Not found.";
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
    const el = $("pl-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
  }
  function setStatus(msg, kind) {
    const el = $("plan-status");
    el.textContent = msg || "";
    el.className = "pl-status" + (kind ? " is-" + kind : "");
  }
  function setBusy(busy) {
    ["plan-generate", "plan-regenerate", "plan-history", "pl-empty-generate"].forEach((id) => {
      const b = $(id);
      if (b) b.disabled = busy;
    });
    document.body.classList.toggle("pl-busy", busy);
  }

  // ---------- payload ----------
  function collectPayload(useOptions) {
    const payload = { regenerate: true };
    if (!useOptions) return payload;
    const days = Number($("plan-days").value);
    if (Number.isFinite(days) && days >= 1) payload.days = Math.min(31, Math.round(days));
    const goals = $("plan-goals").value.trim();
    if (goals) payload.goals = goals;
    const level = $("plan-activity-level").value;
    if (level) payload.activity_level = level;
    const pref = $("plan-food-preference").value;
    if (pref) payload.food_preference = pref;
    const budget = Number($("plan-budget").value);
    if ($("plan-budget").value && Number.isFinite(budget) && budget > 0) payload.monthly_budget = budget;
    return payload;
  }

  // ---------- hero ----------
  function dayTotals(day) {
    const t = { kcal: 0, p: 0, c: 0, f: 0 };
    (day?.meals || []).forEach((m) => {
      t.kcal += Number(m.calories) || 0;
      t.p += Number(m.protein_g) || 0;
      t.c += Number(m.carbs_g) || 0;
      t.f += Number(m.fat_g) || 0;
    });
    return t;
  }

  function renderHero(plan) {
    $("pl-kcal").textContent = plan.calorie_target ? plan.calorie_target.toLocaleString() : "—";
    $("pl-eyebrow").textContent = plan.plan_id
      ? `Plan #${plan.plan_id}${plan.generated_at ? " · " + niceDate(plan.generated_at) : ""}`
      : "Personalized plan";

    const rules = plan.restrictions_applied || [];
    $("pl-restrictions").innerHTML = rules.length
      ? rules.map((r) => `<span class="pl-chip">${escapeHtml(RULE_LABELS[r] || r.replace(/_/g, " "))}</span>`).join("")
      : '<span class="pl-chip is-soft">No special restrictions</span>';

    // Ring = where the energy of the selected day comes from.
    const t = dayTotals((plan.days || [])[state.day]);
    const pK = t.p * 4, cK = t.c * 4, fK = t.f * 9, sum = pK + cK + fK;
    const ring = $("pl-ring");
    if (sum > 0) {
      const p = (pK / sum) * 100, c = (cK / sum) * 100;
      ring.style.setProperty("--p", p.toFixed(1));
      ring.style.setProperty("--c", (p + c).toFixed(1));
      $("pl-ring-legend").innerHTML = `
        <span><i class="is-p"></i>Protein ${Math.round(p)}%</span>
        <span><i class="is-c"></i>Carbs ${Math.round(c)}%</span>
        <span><i class="is-f"></i>Fat ${Math.round(100 - p - c)}%</span>`;
    } else {
      ring.style.removeProperty("--p");
      ring.style.removeProperty("--c");
      $("pl-ring-legend").innerHTML = "";
    }
  }

  // ---------- review + conflicts ----------
  function renderReview(plan) {
    const needs = plan.requires_review === true;
    $("plan-review").hidden = !needs;
    if (!needs) return;
    const titles = (plan.conflicts || []).filter((c) => c.requires_review).map((c) => c.title);
    $("plan-review-text").textContent = (titles.length ? titles.join("; ") + ". " : "") +
      "Please have a doctor or nutritionist check this plan before you start it.";
  }

  function renderConflicts(plan) {
    const list = plan.conflicts || [];
    $("pl-conflicts-card").hidden = !list.length;
    $("pl-conflict-count").textContent = list.length;
    $("plan-conflicts").innerHTML = list.map((c) => {
      const sev = String(c.severity || "medium").toLowerCase();
      return `
        <article class="pl-conflict is-${escapeHtml(sev)}">
          <div class="pl-conflict-top">
            <span class="pl-sev is-${escapeHtml(sev)}">${SEVERITY[sev] || escapeHtml(sev)}</span>
            <strong>${escapeHtml(c.title)}</strong>
            ${c.requires_review ? '<span class="pl-sev is-review">Needs review</span>' : ""}
          </div>
          <p>${escapeHtml(c.explanation)}</p>
          ${c.resolution ? `<p class="pl-resolution">✔ ${escapeHtml(c.resolution)}</p>` : ""}
          ${(c.sources || []).length ? `<p class="pl-sources">From: ${c.sources.map(escapeHtml).join(", ")}</p>` : ""}
        </article>`;
    }).join("");
  }

  // ---------- meals ----------
  function renderTabs(plan) {
    const days = plan.days || [];
    $("pl-day-tabs").innerHTML = days.map((d, i) => `
      <button type="button" role="tab" class="pl-tab${i === state.day ? " is-active" : ""}" aria-selected="${i === state.day}" data-day="${i}">
        <small>Day</small><strong>${escapeHtml(d.day)}</strong>
      </button>`).join("");
  }

  function renderDay(plan) {
    const days = plan.days || [];
    const day = days[state.day];
    if (!day) {
      $("plan-meal-days").innerHTML = '<p class="pl-muted">No meals were generated.</p>';
      $("pl-day-summary").innerHTML = "";
      return;
    }
    const t = dayTotals(day);
    $("pl-day-summary").innerHTML = `
      <div><strong>${(day.daily_calories || t.kcal).toLocaleString()}</strong><small>kcal</small></div>
      <div class="is-p"><strong>${round(t.p)}g</strong><small>Protein</small></div>
      <div class="is-c"><strong>${round(t.c)}g</strong><small>Carbs</small></div>
      <div class="is-f"><strong>${round(t.f)}g</strong><small>Fat</small></div>`;

    $("plan-meal-days").innerHTML = (day.meals || []).map((m) => {
      const key = String(m.meal_type || "").toLowerCase();
      return `
        <article class="pl-meal">
          <div class="pl-meal-time">
            <span class="pl-meal-icon" aria-hidden="true">${MEAL_ICONS[key] || "🍽️"}</span>
            <strong>${escapeHtml(m.time || "")}</strong>
          </div>
          <div class="pl-meal-body">
            <div class="pl-meal-top">
              <h3>${escapeHtml(m.meal_type)}</h3>
              ${m.calories ? `<span class="pl-kcal-pill">${escapeHtml(m.calories)} kcal</span>` : ""}
            </div>
            <div class="pl-items">${(m.items || []).map((it) => `<span>${escapeHtml(it)}</span>`).join("")}</div>
            <p class="pl-macros">
              <span><i class="is-p"></i>P ${round(m.protein_g)}g</span>
              <span><i class="is-c"></i>C ${round(m.carbs_g)}g</span>
              <span><i class="is-f"></i>F ${round(m.fat_g)}g</span>
            </p>
            ${m.notes ? `<p class="pl-muted">${escapeHtml(m.notes)}</p>` : ""}
          </div>
        </article>`;
    }).join("");
  }

  // ---------- activities + notes ----------
  function renderActivities(plan) {
    const acts = plan.activities || [];
    $("plan-activities").innerHTML = acts.length ? acts.map((a) => `
      <article class="pl-activity">
        <span class="pl-activity-icon" aria-hidden="true">${/walk/i.test(a.name) ? "🚶" : /stretch/i.test(a.name) ? "🧘" : /cycl/i.test(a.name) ? "🚴" : /run|jog/i.test(a.name) ? "🏃" : "💪"}</span>
        <div>
          <strong>${escapeHtml(a.name)}</strong>
          ${a.description ? `<p>${escapeHtml(a.description)}</p>` : ""}
          <div class="pl-activity-tags">
            ${a.duration_minutes ? `<span>⏱ ${escapeHtml(a.duration_minutes)} min</span>` : ""}
            ${a.intensity ? `<span>⚡ ${escapeHtml(a.intensity)}</span>` : ""}
            ${a.frequency ? `<span>📅 ${escapeHtml(a.frequency)}</span>` : ""}
          </div>
        </div>
      </article>`).join("") : '<p class="pl-muted">No activities were generated.</p>';

    $("pl-cautions").innerHTML = (plan.cautions || [])
      .map((c) => `<p class="pl-caution">⚠️ ${escapeHtml(c)}</p>`).join("");
  }

  function renderNotes(id, entries) {
    $(id).innerHTML = (entries || []).length
      ? entries.map((e) => `<li>${escapeHtml(e)}</li>`).join("")
      : '<li class="pl-muted">None.</li>';
  }

  function renderPlan(plan) {
    state.plan = plan;
    if (state.day >= (plan.days || []).length) state.day = 0;
    renderHero(plan);
    renderReview(plan);
    renderConflicts(plan);
    renderTabs(plan);
    renderDay(plan);
    renderActivities(plan);
    renderNotes("plan-notes", plan.notes);
    renderNotes("plan-budget-notes", plan.budget_notes);
    if (plan.disclaimer) $("plan-disclaimer").textContent = plan.disclaimer;
    $("plan-activity-caution").textContent = plan.activity_caution || "";
    $("plan-meta").textContent = `${(plan.days || []).length} days`;
    $("pl-empty").hidden = true;
    $("plan-result").hidden = false;
  }

  // ---------- API calls ----------
  async function generate(useOptions) {
    setBusy(true);
    setStatus("Building your plan…", "loading");
    try {
      const plan = await request("/api/plans/me", { method: "POST", body: JSON.stringify(collectPayload(useOptions)) });
      state.day = 0;
      renderPlan(plan);
      setStatus("");
      toast(plan.requires_review ? "Plan ready. Some parts need a professional's review." : "✨ Your new plan is ready");
      $("plan-result").scrollIntoView({ behavior: "smooth", block: "start" });
      if (!$("plan-history-section").hidden) loadHistory();
    } catch (err) {
      setStatus(err.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function loadCurrent() {
    setStatus("Loading your plan…", "loading");
    try {
      const plan = await request("/api/plans/me");
      setStatus("");
      if (!plan || plan.found !== true) {
        $("pl-empty").hidden = false;
        $("plan-result").hidden = true;
        $("pl-restrictions").innerHTML = "";
        return;
      }
      renderPlan(plan);
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  async function loadHistory() {
    const section = $("plan-history-section");
    section.hidden = false;
    $("plan-history-list").innerHTML = '<div class="pl-skeleton"></div>';
    try {
      const data = await request("/api/plans/me/history");
      const plans = (data && data.plans) || [];
      $("plan-history-list").innerHTML = plans.length ? plans.map((p) => `
        <div class="pl-history-row${String(p.status).toUpperCase() === "CURRENT" ? " is-current" : ""}">
          <span class="pl-history-id">#${escapeHtml(p.plan_id)}</span>
          <div>
            <strong>${p.calorie_target ? escapeHtml(p.calorie_target) + " kcal/day" : "Plan"}</strong>
            <small>${escapeHtml(niceDate(p.created_at))}</small>
          </div>
          <span class="pl-history-status">${escapeHtml(p.status)}</span>
          ${p.conflict_count ? `<span class="pl-history-tag">${escapeHtml(p.conflict_count)} rule note${p.conflict_count === 1 ? "" : "s"}</span>` : ""}
          ${p.requires_review ? '<span class="pl-history-tag is-review">Needs review</span>' : ""}
        </div>`).join("") : '<p class="pl-muted">No previous plans yet.</p>';
      section.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      $("plan-history-list").innerHTML = `<p class="pl-muted">${escapeHtml(err.message)}</p>`;
    }
  }

  // ---------- start ----------
  function init() {
    $("plan-generate").addEventListener("click", () => generate(false));
    $("pl-empty-generate").addEventListener("click", () => generate(false));
    $("plan-regenerate").addEventListener("click", () => generate(true));
    $("plan-history").addEventListener("click", loadHistory);
    $("pl-history-close").addEventListener("click", () => { $("plan-history-section").hidden = true; });

    $("pl-toggle-options").addEventListener("click", (e) => {
      const panel = $("pl-options");
      panel.hidden = !panel.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    $("pl-day-tabs").addEventListener("click", (e) => {
      const tab = e.target.closest("[data-day]");
      if (!tab || !state.plan) return;
      state.day = Number(tab.dataset.day);
      renderTabs(state.plan);
      renderDay(state.plan);
      renderHero(state.plan);
    });

    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.replace("login.html");
    });

    loadCurrent();
  }

  document.addEventListener("DOMContentLoaded", init);
})();