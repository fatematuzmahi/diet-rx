/* SCRUM-49: Today's meal plan + accept the plan (SRS 3.1.3.8).
 *
 *   GET  /api/meal-plan/today     today's meals from the CURRENT plan
 *   POST /api/meal-plan/accept    accept the CURRENT plan
 *   GET  /api/daily-log           what was already eaten today (SCRUM-53)
 *   POST /api/daily-log           "I ate this" -> a MEAL entry in the Daily Log
 *   DELETE /api/daily-log/{id}    undo
 */
(() => {
  "use strict";

  const MEAL_ICONS = { breakfast: "🍳", "mid-morning snack": "🍎", lunch: "🍛", "afternoon snack": "🥜", dinner: "🌙" };
  const SLOT_OF = { breakfast: "BREAKFAST", lunch: "LUNCH", dinner: "DINNER" };
  const RULE_LABELS = {
    low_sodium: "🧂 Low sodium", low_sugar: "🍬 Low sugar", low_fat: "🥑 Low fat", low_carbs: "🍞 Low carb",
    high_protein: "💪 High protein", low_protein: "Low protein", low_purine: "Low purine",
    vegetarian: "🥗 Vegetarian", vegan: "🌱 Vegan", gluten_free: "🌾 Gluten-free", lactose_free: "🥛 Lactose-free",
    avoid_exercise: "🛑 Limit exercise", increase_calories: "⬆️ More calories", reduce_calories: "⬇️ Fewer calories",
    increase_fluid: "💧 More fluids", avoid_sugar_alcohol: "🚫 No alcohol", iron_rich: "🩸 Iron-rich",
  };

  const state = { data: null, logged: {} };
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function round(n) { return Math.round((Number(n) || 0) * 10) / 10; }
  function pad(n) { return String(n).padStart(2, "0"); }
  function localDate() { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function niceDate(stamp) {
    const d = new Date(stamp);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function mealKey(meal) { return `From today's plan: ${meal.meal_type}`; }

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
    if (!res.ok) {
      let msg = "Something went wrong. Please try again.";
      if (data && typeof data.detail === "string") msg = data.detail;
      else if (data && Array.isArray(data.detail) && data.detail[0]) msg = String(data.detail[0].msg).replace(/^Value error,\s*/i, "");
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("mp-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
  }
  function setStatus(msg, kind) {
    $("mp-status").textContent = msg || "";
    $("mp-status").className = "mp-status" + (kind ? " is-" + kind : "");
  }

  // ---------- render ----------
  function today() {
    const d = state.data;
    return d.plan.days[d.today_index] || d.plan.days[0] || { meals: [] };
  }

  function renderHero() {
    const d = state.data;
    const plan = d.plan;
    const total = plan.days.length;
    $("mp-date").textContent = new Date(d.today_date + "T00:00:00")
      .toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
    $("mp-sub").textContent = `Day ${d.today_index + 1} of ${total} from Plan #${plan.plan_id}. Tick each meal when you eat it; it goes straight to your Daily Log.`;

    const badges = [];
    if (d.accepted) badges.push(`<span class="mp-badge is-ok">✅ Accepted ${escapeHtml(niceDate(d.accepted_at))}</span>`);
    else if (!d.can_accept) badges.push('<span class="mp-badge is-warn">⚠️ Needs review before accepting</span>');
    else badges.push('<span class="mp-badge is-soft">Not accepted yet</span>');
    badges.push(`<span class="mp-badge is-soft">🎯 ${escapeHtml(plan.calorie_target || "—")} kcal/day</span>`);
    $("mp-badges").innerHTML = badges.join("");

    const btn = $("mp-accept");
    btn.hidden = d.accepted || !d.can_accept;

    $("mp-review").hidden = !!d.can_accept;
    const older = !d.accepted && d.accepted_plan_id && d.accepted_plan_id !== plan.plan_id;
    $("mp-older-accepted").hidden = !older;
    if (older) {
      $("mp-older-text").textContent = `Plan #${plan.plan_id} is your newest plan, but the plan you accepted is #${d.accepted_plan_id}. Accept the new one if you want to follow it.`;
    }
  }

  function renderProgress() {
    const meals = today().meals || [];
    const target = meals.reduce((s, m) => s + (Number(m.calories) || 0), 0);
    let eaten = 0, done = 0;
    meals.forEach((m) => { if (state.logged[mealKey(m)]) { eaten += Number(m.calories) || 0; done += 1; } });
    const pct = target ? Math.min(100, (eaten / target) * 100) : 0;
    $("mp-ring").style.setProperty("--pct", pct.toFixed(1));
    $("mp-eaten").textContent = eaten.toLocaleString();
    $("mp-target").textContent = target.toLocaleString();
    $("mp-meals-done").textContent = `${done} of ${meals.length} meals eaten`;
  }

  function renderMeals() {
    const day = today();
    $("mp-day-pill").textContent = `Day ${day.day || state.data.today_index + 1}`;
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const meals = day.meals || [];
    let nextMarked = false;

    $("mp-meals").innerHTML = meals.map((m) => {
      const key = String(m.meal_type || "").toLowerCase();
      const log = state.logged[mealKey(m)];
      const [h, mi] = String(m.time || "00:00").split(":").map(Number);
      const isNext = !log && !nextMarked && (h * 60 + mi) >= nowMin - 60;
      if (isNext) nextMarked = true;
      return `
        <article class="mp-meal${log ? " is-done" : ""}${isNext ? " is-next" : ""}">
          <div class="mp-meal-icon" aria-hidden="true">${MEAL_ICONS[key] || "🍽️"}</div>
          <div class="mp-meal-body">
            <div class="mp-meal-top">
              <div>
                <span class="mp-time">${escapeHtml(m.time || "")}</span>
                <h3>${escapeHtml(m.meal_type)}</h3>
              </div>
              ${isNext ? '<span class="mp-next">Up next</span>' : ""}
              ${m.calories ? `<span class="mp-kcal">${escapeHtml(m.calories)} kcal</span>` : ""}
            </div>
            <div class="mp-items">${(m.items || []).map((it) => `<span>${escapeHtml(it)}</span>`).join("")}</div>
            <div class="mp-meal-foot">
              <span class="mp-mini-macros">P ${round(m.protein_g)}g · C ${round(m.carbs_g)}g · F ${round(m.fat_g)}g</span>
              ${log
                ? `<button type="button" class="mp-ate is-done" data-undo="${escapeHtml(log)}">✓ Eaten · Undo</button>`
                : `<button type="button" class="mp-ate" data-eat="${escapeHtml(m.meal_type)}">I ate this</button>`}
            </div>
          </div>
        </article>`;
    }).join("") || '<p class="mp-muted">No meals for today.</p>';
  }

  function renderSide() {
    const d = state.data;
    const meals = today().meals || [];
    const t = meals.reduce((a, m) => ({
      p: a.p + (Number(m.protein_g) || 0), c: a.c + (Number(m.carbs_g) || 0), f: a.f + (Number(m.fat_g) || 0),
    }), { p: 0, c: 0, f: 0 });
    const max = Math.max(t.p, t.c, t.f, 1);
    $("mp-macros").innerHTML = [["Protein", t.p, "is-p"], ["Carbs", t.c, "is-c"], ["Fat", t.f, "is-f"]].map(([n, v, cls]) => `
      <div class="mp-macro ${cls}">
        <div class="mp-macro-top"><span>${n}</span><strong>${round(v)} g</strong></div>
        <span class="mp-bar"><i style="width:${(v / max * 100).toFixed(1)}%"></i></span>
      </div>`).join("");

    const rules = d.plan.restrictions_applied || [];
    $("mp-rules").innerHTML = rules.length
      ? rules.map((r) => `<span class="mp-rule">${escapeHtml(RULE_LABELS[r] || r.replace(/_/g, " "))}</span>`).join("")
      : '<p class="mp-muted">No special restrictions.</p>';

    const days = d.plan.days;
    const next = days[(d.today_index + 1) % days.length];
    $("mp-tomorrow-pill").textContent = `Day ${next ? next.day : 1}`;
    $("mp-tomorrow").innerHTML = (next?.meals || []).map((m) => `
      <li><span>${MEAL_ICONS[String(m.meal_type).toLowerCase()] || "🍽️"}</span><b>${escapeHtml(m.meal_type)}</b><em>${escapeHtml((m.items || []).join(", "))}</em></li>`).join("");
  }

  function renderAll() {
    renderHero();
    renderMeals();
    renderProgress();
    renderSide();
    $("mp-empty").hidden = true;
    $("mp-content").hidden = false;
  }

  // ---------- data ----------
  async function loadLogged() {
    state.logged = {};
    try {
      const day = await request(`/api/daily-log?log_date=${state.data.today_date}`);
      (day.entries || []).forEach((e) => {
        if (e.log_type === "MEAL" && e.notes && e.notes.startsWith("From today's plan: ")) state.logged[e.notes] = e.log_id;
      });
    } catch { /* the plan still shows without the log */ }
  }

  async function load() {
    setStatus("Loading today's meals…", "loading");
    try {
      const data = await request(`/api/meal-plan/today?client_date=${localDate()}`);
      setStatus("");
      if (!data.found) {
        $("mp-empty").hidden = false;
        $("mp-content").hidden = true;
        $("mp-meals-done").textContent = "No plan yet";
        return;
      }
      state.data = data;
      await loadLogged();
      renderAll();
    } catch (err) {
      setStatus(err.message, "error");
    }
  }

  async function accept() {
    const btn = $("mp-accept");
    btn.disabled = true;
    try {
      await request("/api/meal-plan/accept", { method: "POST" });
      toast("✅ Plan accepted. Grocery and safety checks will use this plan.");
      await load();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function eat(btn) {
    const meal = (today().meals || []).find((m) => m.meal_type === btn.dataset.eat);
    if (!meal) return;
    btn.disabled = true;
    const slot = SLOT_OF[String(meal.meal_type).toLowerCase()] || "SNACK";
    try {
      const entry = await request("/api/daily-log", {
        method: "POST",
        body: JSON.stringify({
          log_type: "MEAL",
          log_date: state.data.today_date,
          log_time: meal.time,
          slot,
          title: (meal.items || []).join(", ").slice(0, 150) || meal.meal_type,
          quantity: 1,
          notes: mealKey(meal),
        }),
      });
      state.logged[mealKey(meal)] = entry.log_id;
      renderMeals();
      renderProgress();
      toast(`🍽️ ${meal.meal_type} added to your Daily Log`);
    } catch (err) {
      btn.disabled = false;
      toast(err.message, true);
    }
  }

  async function undo(btn) {
    btn.disabled = true;
    try {
      await request(`/api/daily-log/${btn.dataset.undo}`, { method: "DELETE" });
      Object.keys(state.logged).forEach((k) => { if (String(state.logged[k]) === btn.dataset.undo) delete state.logged[k]; });
      renderMeals();
      renderProgress();
      toast("Removed from your Daily Log");
    } catch (err) {
      btn.disabled = false;
      toast(err.message, true);
    }
  }

  // ---------- start ----------
  function init() {
    $("mp-accept").addEventListener("click", accept);
    $("mp-meals").addEventListener("click", (e) => {
      const eatBtn = e.target.closest("[data-eat]");
      const undoBtn = e.target.closest("[data-undo]");
      if (eatBtn) eat(eatBtn);
      if (undoBtn) undo(undoBtn);
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