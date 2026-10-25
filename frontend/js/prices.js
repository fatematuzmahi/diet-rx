/* SCRUM-57: Budget & local prices (SRS 3.3.3.1).
 *
 *   GET /api/budget/me    the patient's budget
 *   PUT /api/budget/me    save it (DAILY / WEEKLY / MONTHLY / PLAN)
 *   GET /api/prices       local prices per serving, with source and date
 */
(() => {
  "use strict";

  const MODE_LABEL = { DAILY: "per day", WEEKLY: "per week", MONTHLY: "per month", PLAN: "for the whole plan" };
  const SEASON_LABEL = { ALL_YEAR: "All year", VARIES: "Changes by season", WINTER: "Winter", SUMMER: "Summer", MONSOON: "Monsoon" };
  const state = { prices: [], category: "All", filter: "" };
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
      else if (data && Array.isArray(data.detail) && data.detail[0]) msg = String(data.detail[0].msg).replace(/^Value error,\s*/i, "");
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("pb-toast");
    el.textContent = msg;
    el.classList.toggle("is-error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }
  function setStatus(msg, kind) {
    $("pb-status").textContent = msg || "";
    $("pb-status").className = "pb-status" + (kind ? " is-" + kind : "");
  }

  // ---------- budget form ----------
  function currentMode() { return document.querySelector('input[name="mode"]:checked').value; }

  // Same rules as the server (price_service.normalise_budget), for a live preview only.
  function normalise(mode, amount, days) {
    if (!(amount > 0)) return null;
    let perDay;
    if (mode === "DAILY") perDay = amount;
    else if (mode === "WEEKLY") perDay = amount / 7;
    else if (mode === "MONTHLY") perDay = amount / 30;
    else { if (!(days >= 1 && days <= 31)) return null; perDay = amount / days; }
    return { per_day: perDay, per_week: perDay * 7, per_month: perDay * 30 };
  }

  function renderPreview() {
    const mode = currentMode();
    $("pb-days-wrap").hidden = mode !== "PLAN";
    $("pb-amount-label").textContent = mode === "PLAN" ? "Amount for the whole plan (BDT)" : `Amount ${MODE_LABEL[mode]} (BDT)`;
    const n = normalise(mode, Number($("pb-amount").value), Number($("pb-days").value));
    $("pb-preview").innerHTML = n
      ? `<div><small>Per day</small><strong>${taka(n.per_day)}</strong></div>
         <div><small>Per week</small><strong>${taka(n.per_week)}</strong></div>
         <div><small>Per month</small><strong>${taka(n.per_month)}</strong></div>`
      : "";
  }

  function renderBudget(b) {
    if (!b || !b.found) {
      $("pb-budget-pill").textContent = "Not set";
      $("pb-hero-day").textContent = "—";
      $("pb-hero-month").textContent = "Not set yet";
      return;
    }
    $("pb-budget-pill").textContent = `${taka(b.amount)} ${MODE_LABEL[b.mode] || ""}`;
    $("pb-hero-day").textContent = taka(b.per_day);
    $("pb-hero-month").textContent = `${taka(b.per_month)} a month · ${taka(b.per_week)} a week`;
    const radio = document.querySelector(`input[name="mode"][value="${b.mode}"]`);
    if (radio) radio.checked = true;
    $("pb-amount").value = b.amount;
    if (b.plan_days) $("pb-days").value = b.plan_days;
    $("pb-market").value = b.preferred_market || "";
    renderPreview();
  }

  async function saveBudget(e) {
    e.preventDefault();
    const err = $("pb-error");
    err.hidden = true;
    const mode = currentMode();
    const amount = Number($("pb-amount").value);
    if (!(amount > 0)) { err.textContent = "Enter a budget amount above 0."; err.hidden = false; return; }
    const body = { mode, amount, preferred_market: $("pb-market").value.trim() || null };
    if (mode === "PLAN") body.plan_days = Number($("pb-days").value);
    $("pb-save").disabled = true;
    try {
      const saved = await request("/api/budget/me", { method: "PUT", body: JSON.stringify(body) });
      renderBudget(saved);
      toast("💾 Budget saved. Your grocery list now uses it.");
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      $("pb-save").disabled = false;
    }
  }

  // ---------- prices ----------
  function renderChips() {
    const cats = ["All", ...new Set(state.prices.map((p) => p.category).filter(Boolean))];
    $("pb-chips").innerHTML = cats.map((c) =>
      `<button type="button" class="pb-chip" role="tab" data-cat="${escapeHtml(c)}" aria-selected="${c === state.category}">${escapeHtml(c)}</button>`).join("");
  }

  function renderRows() {
    const q = state.filter.trim().toLowerCase();
    const rows = state.prices.filter((p) =>
      (state.category === "All" || p.category === state.category) &&
      (!q || p.item_name.toLowerCase().includes(q) || String(p.market_item || "").toLowerCase().includes(q)));
    $("pb-count").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"}`;
    if (!rows.length) {
      $("pb-rows").innerHTML = `<tr><td colspan="6" class="pb-empty">${state.prices.length ? "No food matches your search." : "No prices recorded yet."}</td></tr>`;
      return;
    }
    $("pb-rows").innerHTML = rows.map((p) => {
      const range = p.market_price_low === null || p.market_price_low === undefined ? "—"
        : (p.market_price_low === p.market_price_high ? taka(p.market_price_low) : `${taka(p.market_price_low)}–${taka(p.market_price_high).replace("৳", "")}`);
      const url = safeUrl(p.source_url);
      const season = SEASON_LABEL[p.season] || p.season || "—";
      return `<tr>
        <td><div class="pb-name">${escapeHtml(p.item_name)}</div><span class="pb-cat">${escapeHtml(p.category || "")}</span></td>
        <td>${escapeHtml(p.unit)}</td>
        <td class="r"><span class="pb-price">${taka(p.price_bdt, 2)}</span></td>
        <td>${range}<div class="pb-muted">per ${escapeHtml(p.market_unit || "unit")} · ${escapeHtml(p.market_item || "")}</div></td>
        <td><span class="pb-tag${p.season === "VARIES" ? " is-varies" : ""}" title="${escapeHtml(p.season_note || "")}">${escapeHtml(season)}</span></td>
        <td>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.source)}</a>` : escapeHtml(p.source)}
            <div class="pb-muted">${escapeHtml(niceDate(p.price_date))}${p.stale ? '<span class="pb-tag is-stale">May be outdated</span>' : ""}</div></td>
      </tr>`;
    }).join("");
  }

  function renderSources(data) {
    $("pb-sources").innerHTML = (data.sources || []).map((s) => {
      const url = safeUrl(s.source_url);
      const inner = `${escapeHtml(s.source)}<small>${escapeHtml(niceDate(s.price_date))}</small>`;
      return url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">🔗 ${inner}</a>` : `<a>${inner}</a>`;
    }).join("");
    const badges = [`<span class="pb-badge">🏷️ ${data.total} local prices</span>`];
    if (data.newest_price_date) badges.push(`<span class="pb-badge">📅 Prices as of ${escapeHtml(niceDate(data.newest_price_date))}</span>`);
    if (data.stale_count) badges.push(`<span class="pb-badge is-warn">⏳ ${data.stale_count} may be outdated</span>`);
    $("pb-badges").innerHTML = badges.join("");
    if (data.disclaimer) $("pb-disclaimer").textContent = `${data.disclaimer} DietRx is a decision-support system and does not replace professional medical advice.`;
  }

  async function load() {
    setStatus("Loading your budget and local prices…", "loading");
    const [budget, prices] = await Promise.allSettled([request("/api/budget/me"), request("/api/prices")]);
    const errors = [];
    if (budget.status === "fulfilled") renderBudget(budget.value); else errors.push(budget.reason.message);
    if (prices.status === "fulfilled") {
      state.prices = prices.value.prices || [];
      renderChips();
      renderRows();
      renderSources(prices.value);
    } else {
      errors.push(prices.reason.message);
      $("pb-rows").innerHTML = `<tr><td colspan="6" class="pb-empty">${escapeHtml(prices.reason.message)}</td></tr>`;
    }
    setStatus(errors.join(" "), errors.length ? "error" : "");
  }

  function init() {
    $("pb-form").addEventListener("submit", saveBudget);
    document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", renderPreview));
    $("pb-amount").addEventListener("input", renderPreview);
    $("pb-days").addEventListener("input", renderPreview);
    $("pb-filter").addEventListener("input", (e) => { state.filter = e.target.value; renderRows(); });
    $("pb-chips").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-cat]");
      if (!chip) return;
      state.category = chip.dataset.cat;
      renderChips();
      renderRows();
    });
    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.replace("login.html");
    });
    renderPreview();
    load();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
