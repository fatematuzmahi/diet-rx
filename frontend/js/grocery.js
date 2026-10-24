/* SCRUM-60: Grocery list + purchase links (SRS 3.3.3.3, 3.3.3.4).
 *
 *   GET /api/grocery/list   list built from the ACCEPTED meal plan, with
 *                           estimated costs, budget status and shop links
 *
 * Ticked items are remembered in this browser only (per plan).
 */
(() => {
  "use strict";

  const CATEGORY_ICONS = {
    "Grains & bread": "🌾", "Pulses & protein": "🥚", "Dairy": "🥛",
    "Vegetables": "🥦", "Fruits": "🍌", "Drinks & snacks": "🥥", "Other": "🧺",
  };
  // Same allow-list as the backend: a link to any other site is never shown.
  const ALLOWED_HOSTS = ["chaldal.com", "www.daraz.com.bd"];

  const state = { data: null, done: new Set(), filter: "", hideDone: false };
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function taka(n) { return n === null || n === undefined ? "—" : `৳${Number(n).toLocaleString()}`; }
  function niceDate(stamp) {
    const d = new Date(stamp);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }
  function safeUrl(url) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" && ALLOWED_HOSTS.includes(u.hostname) ? u.href : null;
    } catch { return null; }
  }
  function storageKey() { return `dietrx_grocery_done_${state.data ? state.data.plan_id : "none"}`; }
  function loadDone() {
    try { state.done = new Set(JSON.parse(localStorage.getItem(storageKey()) || "[]")); }
    catch { state.done = new Set(); }
  }
  function saveDone() {
    try { localStorage.setItem(storageKey(), JSON.stringify([...state.done])); } catch { /* private mode */ }
  }

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

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("gr-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }
  function setStatus(msg, kind) {
    $("gr-status").textContent = msg || "";
    $("gr-status").className = "gr-status" + (kind ? " is-" + kind : "");
  }

  function allItems() {
    return (state.data?.categories || []).flatMap((c) => c.items);
  }

  // ---------- hero + summary ----------
  function renderHero() {
    const d = state.data;
    const s = d.summary;
    const b = s.budget || {};
    $("gr-eyebrow").textContent = `Grocery list · ${d.plan_days} days`;
    $("gr-sub").textContent = d.accepted_at
      ? `Built from the meal plan you accepted on ${niceDate(d.accepted_at)}. It updates by itself when you accept a new plan.`
      : "Built from your accepted meal plan.";
    const badges = [
      `<span class="gr-badge is-ok">🛒 ${d.item_count} items</span>`,
      `<span class="gr-badge">📅 ${d.plan_days}-day plan</span>`,
    ];
    if (s.prices_as_of) badges.push(`<span class="gr-badge">🏷️ Prices from ${escapeHtml(niceDate(s.prices_as_of))}</span>`);
    $("gr-badges").innerHTML = badges.join("");

    // Ring: estimated monthly cost as a share of the monthly budget.
    const ring = $("gr-ring");
    ring.classList.remove("is-above", "is-partial");
    let value = "—", label = "of monthly budget", status = "";
    if (b.status === "not_set") {
      label = "no budget saved"; status = "Budget not set";
    } else if (b.status === "unknown") {
      label = "prices needed"; status = "Can't compare yet";
    } else {
      value = `${b.used_percent}%`;
      if (b.status === "above") { ring.classList.add("is-above"); status = "⚠️ Above budget"; }
      else if (b.status === "within_so_far") { ring.classList.add("is-partial"); status = "Within so far"; }
      else status = "✅ Within budget";
    }
    ring.style.setProperty("--pct", Number(b.used_percent) || 0);
    $("gr-ring-value").textContent = value;
    $("gr-ring-label").textContent = label;
    $("gr-ring-status").textContent = status;

    $("gr-total-label").textContent = `Estimated cost (${d.plan_days} days)`;
    $("gr-total").textContent = taka(s.total_bdt);
    $("gr-day").textContent = taka(s.per_day_bdt);
    $("gr-month").textContent = taka(s.per_month_bdt);
    $("gr-coverage").textContent = `${s.priced_items} / ${d.item_count}`;

    $("gr-new-plan").hidden = !d.updated_for_new_plan;
    const notes = d.notes || [];
    $("gr-notes").hidden = notes.length === 0;
    $("gr-notes-text").textContent = notes.join(" ");
    if (d.disclaimer) $("gr-disclaimer").textContent = `${d.disclaimer} DietRx is a decision-support system and does not replace professional medical advice.`;
  }

  function renderBudget() {
    const s = state.data.summary;
    const b = s.budget || {};
    const pill = $("gr-budget-pill");
    pill.className = "gr-pill is-soft";
    if (b.status === "not_set") {
      pill.textContent = "Not set";
      $("gr-budget").innerHTML = `<p class="gr-budget-note">No monthly food budget is saved yet. Once you save one, this card shows whether your list is within or above it.</p>`;
      return;
    }
    const rows = [
      `<div class="gr-row"><span>Monthly budget</span><b>${taka(b.monthly_budget)}</b></div>`,
      `<div class="gr-row"><span>Budget for ${state.data.plan_days} days</span><b>${taka(b.budget_for_plan_days)}</b></div>`,
      `<div class="gr-row"><span>Estimated monthly cost</span><b>${taka(s.per_month_bdt)}</b></div>`,
    ];
    let note = "";
    let barClass = "";
    if (b.status === "unknown") {
      pill.textContent = "Needs prices";
      note = "Prices are not recorded yet, so the list can't be compared with your budget.";
    } else if (b.status === "above") {
      pill.textContent = "Above"; pill.className = "gr-pill is-above"; barClass = "is-above";
      rows.push(`<div class="gr-row"><span>Over by</span><b class="is-above">${taka(Math.abs(b.difference_bdt))} / month</b></div>`);
      note = "Your list costs more than your budget. Cheaper local alternatives can help (budget optimizer).";
    } else {
      const partial = b.status === "within_so_far";
      pill.textContent = partial ? "Within so far" : "Within";
      pill.className = partial ? "gr-pill is-partial" : "gr-pill is-soft";
      barClass = partial ? "is-partial" : "";
      rows.push(`<div class="gr-row"><span>Left over</span><b class="is-within">${taka(b.difference_bdt)} / month</b></div>`);
      note = partial ? "Some items have no price yet, so the real cost will be a little higher." : "Your list fits your monthly budget.";
    }
    const pct = Math.min(Number(b.used_percent) || 0, 100);
    $("gr-budget").innerHTML = `
      <div class="gr-budget-figures">${rows.join("")}</div>
      ${b.used_percent !== undefined ? `<div class="gr-budget-bar"><i class="${barClass}" style="width:${pct}%"></i></div>
      <div class="gr-row"><span>Used</span><b>${b.used_percent}%</b></div>` : ""}
      <p class="gr-budget-note">${escapeHtml(note)}</p>`;
  }

  // ---------- list ----------
  function buyHtml(item) {
    const p = item.purchase || {};
    const links = (p.links || []).map((l) => ({ store: l.store, url: safeUrl(l.url) })).filter((l) => l.url);
    if (!p.available || links.length === 0) {
      return `<span class="gr-local">🧺 ${escapeHtml(p.reason || "Buy at your local market.")}</span>`;
    }
    return `
      <details class="gr-buy">
        <summary>Buy ▾</summary>
        <div class="gr-buy-menu">
          ${links.map((l) => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer" data-store="${escapeHtml(l.store)}">${escapeHtml(l.store)} <span>↗</span></a>`).join("")}
          <p>Opens the shop's search. Only "${escapeHtml(item.name)}" is shared. Shop prices may differ.</p>
        </div>
      </details>`;
  }

  function costHtml(item) {
    if (item.estimated_cost_bdt !== null && item.estimated_cost_bdt !== undefined) {
      return `<span class="gr-cost">${taka(item.estimated_cost_bdt)}</span>`;
    }
    return `<span class="gr-cost is-none">${item.cost_note ? "Included" : "No price yet"}</span>`;
  }

  function itemHtml(item) {
    const done = state.done.has(item.key);
    const chips = [`<span class="gr-chip is-qty">× ${escapeHtml(item.quantity_label)}</span>`];
    if (item.unit) chips.push(`<span class="gr-chip">1 serving = ${escapeHtml(item.unit)}</span>`);
    if (item.price_status === "partial") chips.push(`<span class="gr-chip is-partial">Some prices missing</span>`);
    if (item.price_status === "missing" && !item.cost_note) chips.push(`<span class="gr-chip is-missing">Price not available</span>`);
    const used = item.cost_note || `For: ${item.used_in.join(", ")}`;
    return `
      <div class="gr-item${done ? " is-done" : ""}" data-key="${escapeHtml(item.key)}">
        <label class="gr-check" title="In my basket">
          <input type="checkbox" ${done ? "checked" : ""} aria-label="${escapeHtml(item.name)} is in my basket">
          <span>✓</span>
        </label>
        <div class="gr-info">
          <div class="gr-name">${escapeHtml(item.name)}</div>
          <div class="gr-meta">${chips.join("")}</div>
          <div class="gr-used">${escapeHtml(used)}</div>
        </div>
        <div class="gr-right">${costHtml(item)}${buyHtml(item)}</div>
      </div>`;
  }

  function renderList() {
    const q = state.filter.trim().toLowerCase();
    const html = (state.data.categories || []).map((cat) => {
      const items = cat.items.filter((i) =>
        (!q || i.name.toLowerCase().includes(q) || i.used_in.some((u) => u.toLowerCase().includes(q))) &&
        !(state.hideDone && state.done.has(i.key)));
      if (!items.length) return "";
      return `
        <section class="gr-category">
          <h3><span>${CATEGORY_ICONS[cat.name] || "🧺"}</span>${escapeHtml(cat.name)} <em>${items.length}</em></h3>
          <div class="gr-items">${items.map(itemHtml).join("")}</div>
        </section>`;
    }).join("");
    $("gr-list").innerHTML = html || `<p class="gr-no-match">No items match. ${state.hideDone ? "Everything may already be in your basket 🎉" : ""}</p>`;
    renderProgress();
  }

  function renderProgress() {
    const total = allItems().length;
    const done = allItems().filter((i) => state.done.has(i.key)).length;
    $("gr-progress").textContent = `${done} / ${total} in basket`;
    $("gr-progress-bar").style.width = total ? `${(done / total) * 100}%` : "0";
  }

  // ---------- actions ----------
  function listAsText() {
    const d = state.data;
    const lines = [`DietRx grocery list (${d.plan_days} days)`, ""];
    (d.categories || []).forEach((cat) => {
      lines.push(cat.name.toUpperCase());
      cat.items.forEach((i) => {
        const cost = i.estimated_cost_bdt !== null && i.estimated_cost_bdt !== undefined ? ` - about ${taka(i.estimated_cost_bdt)}` : "";
        lines.push(`${state.done.has(i.key) ? "[x]" : "[ ]"} ${i.name} (${i.quantity_label})${cost}`);
      });
      lines.push("");
    });
    if (d.summary.total_bdt !== null) lines.push(`Estimated total: ${taka(d.summary.total_bdt)} (estimate, shop prices may differ)`);
    return lines.join("\n");
  }

  async function copyList() {
    if (!state.data) return;
    try {
      await navigator.clipboard.writeText(listAsText());
      toast("📋 List copied. Paste it in WhatsApp or Notes.");
    } catch {
      toast("Couldn't copy here. Use Print instead.", true);
    }
  }

  function onListClick(e) {
    const box = e.target.closest(".gr-check input");
    if (box) {
      const row = box.closest(".gr-item");
      const key = row.dataset.key;
      if (box.checked) state.done.add(key); else state.done.delete(key);
      row.classList.toggle("is-done", box.checked);
      saveDone();
      renderProgress();
      if (state.hideDone && box.checked) setTimeout(renderList, 250);
      return;
    }
    const link = e.target.closest(".gr-buy-menu a");
    if (link) {
      if (!navigator.onLine) {
        e.preventDefault();
        toast("You seem to be offline. Try the shop link again when you are connected.", true);
        return;
      }
      const opened = window.open(link.href, "_blank", "noopener,noreferrer");
      e.preventDefault();
      if (opened === null) {
        // Pop-up blocked: still let the user reach the shop.
        toast(`Your browser blocked the new tab. Allow pop-ups, or long-press "${link.dataset.store}" to open it.`, true);
      }
      link.closest("details").open = false;
    }
  }

  // Close an open "Buy" menu when clicking elsewhere.
  function closeMenus(e) {
    document.querySelectorAll(".gr-buy[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  }

  // ---------- load ----------
  async function load() {
    setStatus("Building your grocery list…", "loading");
    try {
      const data = await request("/api/grocery/list");
      if (!data.found) {
        $("gr-content").hidden = true;
        $("gr-empty").hidden = false;
        $("gr-empty-text").textContent = data.message || $("gr-empty-text").textContent;
        $("gr-ring-status").textContent = "No list yet";
        setStatus("");
        return;
      }
      state.data = data;
      loadDone();
      $("gr-empty").hidden = true;
      $("gr-content").hidden = false;
      renderHero();
      renderBudget();
      renderList();
      setStatus("");
      if (data.updated_for_new_plan) toast("🔄 Your list was updated for your new meal plan.");
    } catch (err) {
      setStatus(err.message, "error");
      $("gr-ring-status").textContent = "Not loaded";
    }
  }

  function init() {
    $("gr-list").addEventListener("click", onListClick);
    document.addEventListener("click", closeMenus);
    $("gr-filter").addEventListener("input", (e) => { state.filter = e.target.value; renderList(); });
    $("gr-hide-done").addEventListener("change", (e) => { state.hideDone = e.target.checked; renderList(); });
    $("gr-copy").addEventListener("click", copyList);
    $("gr-print").addEventListener("click", () => window.print());
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
