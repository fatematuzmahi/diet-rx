/* SCRUM-59: Smart swaps -- budget-aware Bengali food optimizer (SRS 3.3.3.2, 3.3.3.5).
 *
 *   GET    /api/optimizer/summary            plan cost vs budget + swap ideas
 *   GET    /api/optimizer/alternatives?item= cheaper safe options for one food
 *   PUT    /api/optimizer/swaps              use a swap
 *   DELETE /api/optimizer/swaps/{id}         undo a swap
 */
(() => {
  "use strict";

  const SEASON = { ALL_YEAR: "All year", VARIES: "Changes by season", WINTER: "Winter", SUMMER: "Summer", MONSOON: "Monsoon" };
  const STATUS = {
    above: ["Above budget", "is-above"], within: ["Within budget", "is-within"],
    within_so_far: ["Within so far", "is-partial"], unknown: ["Needs prices", ""], not_set: ["No budget set", ""],
  };
  const state = { data: null, busy: false };
  const $ = (id) => document.getElementById(id);

  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function taka(n, digits = 0) {
    return n === null || n === undefined ? "—" : `৳${Number(n).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })}`;
  }
  function niceDate(value) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function safeUrl(url) {
    try { const u = new URL(url); return u.protocol === "https:" ? u.href : null; } catch { return null; }
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
    if (!res.ok) {
      let msg = "Something went wrong. Please try again.";
      if (data && typeof data.detail === "string") msg = data.detail;
      else if (data && Array.isArray(data.detail) && data.detail[0]) msg = String(data.detail[0].msg);
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("sw-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3600);
  }
  function setStatus(msg, isError) {
    $("sw-status").textContent = msg || "";
    $("sw-status").className = "sw-status-line" + (isError ? " is-error" : "");
  }

  // ---------- pieces ----------
  function altHtml(fromKey, a, active) {
    const url = safeUrl(a.source_url);
    const isUsed = active && active.to_key === a.key;
    const saving = a.saving_per_week !== null && a.saving_per_week !== undefined
      ? `<strong>${taka(a.saving_per_week)}</strong><small>saved a week</small>`
      : a.saving_per_serving !== null && a.saving_per_serving !== undefined
        ? `<strong>${taka(a.saving_per_serving, 2)}</strong><small>saved per serving</small>`
        : `<strong>${taka(a.price_per_serving, 2)}</strong><small>per serving</small>`;
    return `<div class="sw-alt">
      <div>
        <div class="sw-alt-name">${escapeHtml(a.name)}</div>
        <div class="sw-alt-portion">${escapeHtml(a.portion)} · ${taka(a.cost_per_original_serving, 2)} instead of ${escapeHtml(a.compare_label || "one serving")}</div>
      </div>
      <div class="sw-save">${saving}</div>
      <details><summary>Why this swap?</summary><ul class="sw-why">${(a.why || []).map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></details>
      <div class="sw-alt-foot">
        <span class="sw-source"><span class="sw-chip${a.season === "VARIES" ? " is-warn" : ""}" title="${escapeHtml(a.season_note || "")}">${escapeHtml(SEASON[a.season] || a.season || "")}</span>
          ${a.stale ? '<span class="sw-chip is-red">May be outdated</span>' : ""}
          ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.source || "Source")}</a>` : escapeHtml(a.source || "")}
          ${a.price_date ? ` · ${escapeHtml(niceDate(a.price_date))}` : ""}</span>
        <button type="button" class="sw-btn ${isUsed ? "is-done" : "sw-btn-primary"}" data-from="${escapeHtml(fromKey)}" data-to="${escapeHtml(a.key)}" ${isUsed ? "disabled" : ""}>
          ${isUsed ? "✓ In use" : "Use this swap"}</button>
      </div>
    </div>`;
  }

  function notSuggestedHtml(list) {
    if (!list || !list.length) return "";
    return `<details class="sw-not"><summary>Not suggested for you (${list.length})</summary><ul>
      ${list.map((n) => `<li><b>${escapeHtml(n.name)}</b> – ${escapeHtml(n.reason)}</li>`).join("")}</ul></details>`;
  }

  function ideaHtml(idea, activeByFrom) {
    const active = activeByFrom[idea.key];
    const meta = [
      idea.servings_per_week ? `${idea.servings_per_week} serving${idea.servings_per_week === 1 ? "" : "s"} a week` : null,
      idea.price_per_serving !== null ? `${taka(idea.price_per_serving, 2)} each` : "no recorded price",
      idea.cost_per_week !== null && idea.cost_per_week !== undefined ? `${taka(idea.cost_per_week)} a week` : null,
    ].filter(Boolean).join(" · ");
    const compare = idea.unit ? idea.unit.toLowerCase() : "one serving";
    return `<article class="sw-card">
      <div class="sw-idea-top">
        <div><h3>${escapeHtml(idea.name)}</h3><p class="sw-idea-meta">${escapeHtml(meta)}</p></div>
        <span class="sw-chip">${escapeHtml(idea.role_label)}</span>
      </div>
      ${idea.suitable ? "" : `<p class="sw-warning">⚠️ ${escapeHtml(idea.unsuitable_reason || "This food may not suit you.")} A safe swap is shown below.</p>`}
      <div class="sw-alts">${idea.alternatives.map((a) => altHtml(idea.key, { ...a, compare_label: compare }, active)).join("")}</div>
      ${notSuggestedHtml(idea.not_suggested)}
    </article>`;
  }

  // ---------- render ----------
  function render() {
    const d = state.data;
    if (!d.found) {
      $("sw-ideas").innerHTML = `<div class="sw-card sw-empty">${escapeHtml(d.message)}<br><br><a class="sw-btn sw-btn-soft" href="meal-plan.html">🍽️ Open my meal plan</a></div>`;
      $("sw-cost").textContent = "—";
      $("sw-status-pill").textContent = "No plan yet";
      renderFoods(d.foods);
      return;
    }
    const [label, cls] = STATUS[d.status] || ["", ""];
    $("sw-cost").textContent = taka(d.cost_per_week);
    $("sw-budget").textContent = d.budget_per_week ? `Budget: ${taka(d.budget_per_week)} a week` : "Budget: not set";
    $("sw-status-pill").textContent = label;
    $("sw-status-pill").className = `sw-status ${cls}`;

    const badges = [`<span class="sw-badge">💡 Save up to ${taka(d.possible_saving_per_week)} a week</span>`];
    if (d.saved_per_week_by_swaps) badges.push(`<span class="sw-badge">✅ Swaps already save ${taka(d.saved_per_week_by_swaps)} a week</span>`);
    badges.push(`<span class="sw-badge">🏷️ ${d.price_coverage_percent}% of items priced</span>`);
    $("sw-badges").innerHTML = badges.join("");

    const band = $("sw-band");
    band.hidden = false;
    if (d.status === "above") {
      band.className = "sw-band is-above";
      band.innerHTML = `Your plan is <b>${taka(d.over_budget_by)}</b> a week over your budget. The swaps below can save up to <b>${taka(d.possible_saving_per_week)}</b> a week.`;
    } else if (d.status === "not_set") {
      band.className = "sw-band is-info";
      band.innerHTML = `You have not set a food budget yet. <a href="prices.html">Set your budget</a> to compare your plan with it.`;
    } else if (d.status === "unknown") {
      band.className = "sw-band is-info";
      band.textContent = "Prices are not recorded yet, so the plan cannot be compared with your budget.";
    } else {
      band.className = "sw-band";
      band.innerHTML = d.possible_saving_per_week
        ? `You are within your budget. These swaps can still save up to <b>${taka(d.possible_saving_per_week)}</b> a week.`
        : "You are within your budget. Your plan already uses the cheapest safe choices we know.";
    }

    const activeByFrom = Object.fromEntries((d.active_swaps || []).map((s) => [s.from_key, s]));
    $("sw-count").textContent = `${d.ideas.length} idea${d.ideas.length === 1 ? "" : "s"}`;
    $("sw-ideas").innerHTML = d.ideas.length
      ? d.ideas.map((i) => ideaHtml(i, activeByFrom)).join("")
      : `<div class="sw-card sw-empty">No cheaper food that is safe for you was found for your plan right now.</div>`;

    renderSwaps(d.active_swaps || [], d.paused_swaps || []);
    renderFoods(d.foods);
    const c = d.checked_against || {};
    $("sw-checked").textContent = `Checked against: allergies (${c.allergies || "none saved"}), conditions (${c.conditions || "none saved"}), diet (${c.food_preference || "not set"}), ${c.prescriptions || 0} prescription(s).`;
    if (d.disclaimer) $("sw-disclaimer").textContent = d.disclaimer;
  }

  function renderSwaps(active, paused) {
    $("sw-active-count").textContent = String(active.length);
    if (!active.length && !paused.length) {
      $("sw-active").innerHTML = '<p class="sw-muted">No swaps yet. Pick one from the ideas.</p>';
      return;
    }
    $("sw-active").innerHTML = active.map((s) => `<div class="sw-swap">
        <div><b>${escapeHtml(s.from_name)} → ${escapeHtml(s.to_name)}</b><small>${escapeHtml(s.portion || "")}</small></div>
        <button type="button" class="sw-btn sw-btn-ghost" data-undo="${s.swap_id}">Undo</button></div>`).join("")
      + paused.map((s) => `<div class="sw-swap is-paused">
        <div><b>⏸️ ${escapeHtml(s.from_name)} → ${escapeHtml(s.to_name)}</b><small>Paused: ${escapeHtml(s.reason)}</small></div>
        <button type="button" class="sw-btn sw-btn-ghost" data-undo="${s.swap_id}">Remove</button></div>`).join("")
      + '<p class="sw-muted" style="margin-top:10px">Your <a href="grocery.html">grocery list</a> already uses these swaps.</p>';
  }

  function renderFoods(foods) {
    const select = $("sw-food");
    if (!foods || select.options.length > 1) return;
    const groups = {};
    foods.forEach((f) => { (groups[f.role_label] = groups[f.role_label] || []).push(f); });
    select.innerHTML = '<option value="">Choose a food…</option>' + Object.entries(groups).map(([g, list]) =>
      `<optgroup label="${escapeHtml(g)}">${list.map((f) => `<option value="${escapeHtml(f.key)}">${escapeHtml(f.name)}</option>`).join("")}</optgroup>`).join("");
  }

  // ---------- actions ----------
  async function load() {
    setStatus("Checking your plan for safe savings…");
    try {
      state.data = await request("/api/optimizer/summary");
      render();
      setStatus("");
    } catch (e) {
      setStatus(e.message, true);
      $("sw-ideas").innerHTML = `<div class="sw-card sw-empty">${escapeHtml(e.message)}</div>`;
    }
  }

  async function useSwap(btn) {
    if (state.busy) return;
    state.busy = true;
    btn.disabled = true;
    try {
      const res = await request("/api/optimizer/swaps", {
        method: "PUT", body: JSON.stringify({ from_item: btn.dataset.from, to_item: btn.dataset.to }),
      });
      toast(`🔄 ${res.message}`);
      await load();
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
    } finally {
      state.busy = false;
    }
  }

  async function undoSwap(btn) {
    if (state.busy) return;
    state.busy = true;
    btn.disabled = true;
    try {
      const res = await request(`/api/optimizer/swaps/${encodeURIComponent(btn.dataset.undo)}`, { method: "DELETE" });
      toast(`↩️ ${res.message}`);
      await load();
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
    } finally {
      state.busy = false;
    }
  }

  async function checkFood(e) {
    e.preventDefault();
    const key = $("sw-food").value;
    if (!key) return;
    const box = $("sw-check-result");
    box.innerHTML = '<p class="sw-muted">Checking…</p>';
    try {
      const r = await request(`/api/optimizer/alternatives?item=${encodeURIComponent(key)}`);
      const active = (state.data?.active_swaps || []).find((s) => s.from_key === r.key);
      const compare = r.unit ? r.unit.toLowerCase() : "one serving";
      box.innerHTML = `<p class="sw-muted"><b>${escapeHtml(r.name)}</b>${r.price_per_serving !== null ? ` · ${taka(r.price_per_serving, 2)} per serving` : " · no recorded price"}</p>
        ${r.suitable ? "" : `<p class="sw-warning">⚠️ ${escapeHtml(r.unsuitable_reason)}</p>`}
        ${r.alternatives.length ? `<div class="sw-alts">${r.alternatives.map((a) => altHtml(r.key, { ...a, compare_label: compare }, active)).join("")}</div>`
          : `<p class="sw-muted">${escapeHtml(r.message || "No cheaper safe option found.")}</p>`}
        ${notSuggestedHtml(r.not_suggested)}`;
    } catch (err) {
      box.innerHTML = `<p class="sw-warning">${escapeHtml(err.message)}</p>`;
    }
  }

  function init() {
    document.addEventListener("click", (e) => {
      const use = e.target.closest("button[data-to]");
      if (use) return useSwap(use);
      const undo = e.target.closest("button[data-undo]");
      if (undo) return undoSwap(undo);
    });
    $("sw-check").addEventListener("submit", checkFood);
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
