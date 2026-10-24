// SCRUM-47: Dish DNA page (SRS 3.1.3.7)
// Needs api.js loaded first (API_BASE_URL + the access_token convention).

(() => {
  "use strict";

  const CATEGORY_ICONS = { main: "🍛", soup: "🥣", side: "🥗", snack: "🍪", breakfast: "🍳", dessert: "🍮", drink: "🥤" };
  const FIT_LABELS = { SAFE: "Safe", CAUTION: "Caution", AVOID: "Avoid" };
  const MAX_COMPARE = 2;

  // Allergy word -> words that mean a dish contains it.
  const ALLERGEN_FAMILIES = {
    peanut: ["peanut", "chikki"],
    nut: ["nut", "peanut", "almond", "cashew", "walnut", "pistachio"],
    egg: ["egg"],
    milk: ["milk", "curd", "yogurt", "yoghurt", "paneer", "ghee", "butter", "cream", "doi", "cheese"],
    dairy: ["milk", "curd", "yogurt", "yoghurt", "paneer", "ghee", "butter", "cream", "doi", "cheese"],
    lactose: ["milk", "curd", "yogurt", "yoghurt", "paneer", "cream", "doi", "cheese"],
    fish: ["fish", "rui", "ilish", "hilsa"],
    seafood: ["fish", "prawn", "shrimp", "crab"],
    shellfish: ["prawn", "shrimp", "crab"],
    wheat: ["wheat", "roti", "paratha", "bread", "flour", "atta", "maida"],
    gluten: ["wheat", "roti", "paratha", "bread", "flour", "atta", "maida"],
    soy: ["soy", "soya", "tofu"],
  };
  const NO_ALLERGY = ["none", "no", "nil", "n/a", "na", "no allergy", "no allergies"];

  const state = {
    dishes: [],
    category: "",
    cuisine: "",
    search: "",
    profile: null,
    compare: [],
    current: null,
  };
  const $ = (id) => document.getElementById(id);

  // ---------- helpers ----------
  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function num(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  function fmt(v, digits = 1) {
    const n = num(v);
    if (n === null) return "—";
    return Number.isInteger(n) ? String(n) : n.toFixed(digits);
  }
  function toList(value) {
    const items = Array.isArray(value) ? value : String(value || "").split(",");
    return items.map((s) => String(s).trim()).filter((s) => s && !NO_ALLERGY.includes(s.toLowerCase()));
  }
  function singular(word) {
    if (word.length > 4 && word.endsWith("ies")) return word.slice(0, -3) + "y";
    if (word.length > 3 && word.endsWith("s") && !word.endsWith("ss")) return word.slice(0, -1);
    return word;
  }
  function words(text) {
    return String(text || "").toLowerCase().replace(/[^a-z]+/g, " ").split(" ").filter(Boolean).map(singular);
  }
  function iconFor(dish) {
    return CATEGORY_ICONS[String(dish.category || "").toLowerCase()] || "🍽️";
  }
  function macroKcal(dish) {
    const p = (num(dish.protein_g) || 0) * 4;
    const c = (num(dish.carbs_g) || 0) * 4;
    const f = (num(dish.fat_g) || 0) * 9;
    const total = p + c + f;
    if (!total) return null;
    return { p: (p / total) * 100, c: (c / total) * 100, f: (f / total) * 100 };
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
    if (!res.ok) {
      const detail = data && typeof data.detail === "string" ? data.detail : null;
      const err = new Error(detail || "Something went wrong. Please try again.");
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- personal fit (Safe / Caution / Avoid) ----------
  function fitFor(dish) {
    const profile = state.profile;
    if (!profile) return null;
    const avoid = [];
    const caution = [];
    const good = [];

    const ingWords = new Set(words(dish.ingredients));
    const dishWords = new Set([...words(toList(dish.allergens).join(" ")), ...ingWords]);
    toList(profile.allergies).forEach((allergy) => {
      const family = words(allergy).flatMap((w) => ALLERGEN_FAMILIES[w] || [w]);
      // Prefer the ingredient that causes it, so the user sees WHY.
      const hit = family.find((w) => ingWords.has(w)) || family.find((w) => dishWords.has(w));
      if (hit) avoid.push(`Contains ${hit}, and you are allergic to ${allergy.toLowerCase()}.`);
    });

    const pref = String(profile.food_preference || "").toLowerCase();
    const isNonVeg = toList(dish.diet_tags).some((t) => /non[\s-]?veg/i.test(t));
    const isVegan = pref.includes("vegan");
    const isVegetarian = pref.includes("vegetarian") && !/non[\s-]?veg/.test(pref);
    if ((isVegetarian || isVegan) && isNonVeg) {
      avoid.push(`Has meat or fish, and your food preference is ${profile.food_preference}.`);
    } else if (isVegan && ["milk", "curd", "yogurt", "paneer", "ghee", "egg"].some((w) => dishWords.has(w))) {
      avoid.push("Has dairy or egg, and your food preference is Vegan.");
    }

    const cond = String(profile.conditions || "").toLowerCase();
    const gi = num(dish.glycemic_index);
    const sugar = num(dish.sugar_g);
    const sodium = num(dish.sodium_mg);
    const satFat = num(dish.saturated_fat_g);
    const iron = num(dish.iron_mg);
    if (/diabet/.test(cond)) {
      if (gi !== null && gi >= 70) caution.push(`High glycemic index (${fmt(gi, 0)}) can raise blood sugar quickly. Keep the portion small.`);
      if (sugar !== null && sugar >= 15) caution.push(`${fmt(sugar)} g sugar per serving. Watch this with diabetes.`);
    }
    if (/hypertens|blood pressure|kidney|renal/.test(cond) && sodium !== null && sodium >= 600) {
      caution.push(`${fmt(sodium, 0)} mg sodium per serving is high for your condition.`);
    }
    if (/heart|cardiac|cholesterol/.test(cond) && satFat !== null && satFat >= 5) {
      caution.push(`${fmt(satFat)} g saturated fat per serving. Choose a smaller portion.`);
    }
    if (/anemi|anaemi|iron/.test(cond) && iron !== null && iron >= 3) {
      good.push(`Good iron source (${fmt(iron)} mg), helpful for anaemia.`);
    }

    const status = avoid.length ? "AVOID" : caution.length ? "CAUTION" : "SAFE";
    const reasons = [...avoid, ...caution];
    if (!reasons.length) reasons.push("No conflict found with your recorded allergies, preference or conditions.");
    return { status, reasons, good };
  }

  function fitPill(fit) {
    if (!fit) return "";
    return `<span class="dd-fit is-${fit.status}">${FIT_LABELS[fit.status]}</span>`;
  }

  // ---------- profile ----------
  async function loadProfile() {
    try {
      const p = await request("/api/health-profile/me");
      if (!p || p.profile_exists === false) throw new Error("no profile");
      state.profile = p;
      const parts = [
        ...toList(p.allergies).map((a) => `🚫 ${a}`),
        p.food_preference && !/no restriction/i.test(p.food_preference) ? `🥗 ${p.food_preference}` : "",
        ...toList(p.conditions).map((c) => `🩺 ${c}`),
      ].filter(Boolean);
      $("dd-profile-text").innerHTML = parts.length
        ? `Every dish is checked against: <span class="dd-profile-tags">${parts.map((t) => `<em>${escapeHtml(t)}</em>`).join("")}</span>`
        : "Your profile has no allergies or conditions yet, so dishes are not flagged.";
    } catch {
      state.profile = null;
      $("dd-profile-text").innerHTML = 'Add your allergies and conditions on the <a href="dashboard.html">Dashboard</a> to see Safe / Caution / Avoid for each dish.';
    }
  }

  // ---------- facets ----------
  async function loadFacets() {
    try {
      const f = await request("/api/dish-dna/facets");
      const cats = f.categories || [];
      $("dd-category-chips").innerHTML = ["", ...cats].map((c) => `
        <button type="button" class="dd-chip${c === state.category ? " is-active" : ""}" data-category="${escapeHtml(c)}">
          ${c ? `${CATEGORY_ICONS[c.toLowerCase()] || "🍽️"} ${escapeHtml(c)}` : "✨ All"}
        </button>`).join("");
      $("dd-cuisine").innerHTML = '<option value="">All cuisines</option>' +
        (f.cuisines || []).map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    } catch { /* filters are optional */ }
  }

  // ---------- list ----------
  async function loadDishes() {
    showView("list");
    $("dish-list").innerHTML = '<div class="dd-skeleton"></div>'.repeat(6);
    const params = new URLSearchParams({ page_size: "100" });
    if (state.search) params.set("search", state.search.toLowerCase());
    if (state.category) params.set("category", state.category);
    if (state.cuisine) params.set("cuisine", state.cuisine);
    try {
      const data = await request(`/api/dish-dna?${params}`);
      state.dishes = data.dishes || data.items || [];
      $("dd-count").textContent = `${data.total ?? state.dishes.length} dish${(data.total ?? state.dishes.length) === 1 ? "" : "es"}`;
      renderDishes();
    } catch (err) {
      $("dish-list").innerHTML = `<div class="dd-empty"><span>😕</span><strong>Couldn't load dishes</strong>${escapeHtml(err.message)}</div>`;
    }
  }

  function renderDishes() {
    const list = state.dishes;
    if (!list.length) {
      $("dish-list").innerHTML = `
        <div class="dd-empty">
          <span>🍽️</span>
          <strong>${state.search || state.category || state.cuisine ? "No dishes match your search" : "No dishes yet"}</strong>
          ${state.search || state.category || state.cuisine ? "Try another word or clear the filters." : "Dishes will appear here once they are added."}
        </div>`;
      return;
    }
    $("dish-list").innerHTML = list.map((dish) => {
      const id = dish.dish_id ?? dish.id;
      const macro = macroKcal(dish);
      const fit = fitFor(dish);
      const badges = (dish.health_status_labels || []).slice(0, 3)
        .map((b) => `<span class="dd-badge">${escapeHtml(b)}</span>`).join("");
      const picked = state.compare.includes(id);
      return `
        <article class="dd-card${picked ? " is-picked" : ""}" data-id="${escapeHtml(id)}" tabindex="0" aria-label="${escapeHtml(dish.name)}">
          <div class="dd-card-top">
            <span class="dd-card-icon" aria-hidden="true">${iconFor(dish)}</span>
            ${fitPill(fit)}
          </div>
          <h3>${escapeHtml(dish.name)}</h3>
          <p class="dd-card-meta">${escapeHtml(dish.cuisine || "—")} · ${escapeHtml(dish.category || "—")}</p>
          <div class="dd-card-kcal"><strong>${fmt(dish.calories, 0)}</strong> kcal <small>per ${escapeHtml(dish.serving_description || "serving")}</small></div>
          ${macro ? `
          <div class="dd-macro-bar" aria-label="Energy from protein, carbs and fat">
            <span class="is-p" style="width:${macro.p.toFixed(1)}%"></span>
            <span class="is-c" style="width:${macro.c.toFixed(1)}%"></span>
            <span class="is-f" style="width:${macro.f.toFixed(1)}%"></span>
          </div>
          <div class="dd-macro-legend">
            <span><i class="is-p"></i>P ${fmt(dish.protein_g)}g</span>
            <span><i class="is-c"></i>C ${fmt(dish.carbs_g)}g</span>
            <span><i class="is-f"></i>F ${fmt(dish.fat_g)}g</span>
          </div>` : ""}
          <div class="dd-badges">${badges}</div>
          <div class="dd-card-actions">
            <button type="button" class="dd-link" data-open="${escapeHtml(id)}">View DNA →</button>
            <label class="dd-compare-toggle"><input type="checkbox" data-compare="${escapeHtml(id)}" ${picked ? "checked" : ""}> Compare</label>
          </div>
        </article>`;
    }).join("");
  }


  // ---------- detail ----------
  async function openDish(id) {
    showView("detail");
    $("dish-detail").innerHTML = '<div class="dd-skeleton is-tall"></div>';
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const dish = await request(`/api/dish-dna/${encodeURIComponent(id)}`);
      state.current = dish;
      renderDetail(dish);
    } catch (err) {
      $("dish-detail").innerHTML = `
        <button type="button" class="dd-back" data-back>← All dishes</button>
        <div class="dd-empty"><span>😕</span><strong>Couldn't open this dish</strong>${escapeHtml(err.message)}</div>`;
    }
  }

  function row(label, value, unit) {
    if (num(value) === null) return "";
    return `<div class="dd-row"><span>${label}</span><strong>${fmt(value)} ${unit}</strong></div>`;
  }

  function renderDetail(dish) {
    const fit = fitFor(dish);
    const macro = macroKcal(dish);
    const allergens = toList(dish.allergens);
    const flagged = new Set();
    if (fit) {
      fit.reasons.forEach((r) => { const m = r.match(/^Contains (\w+)/); if (m) flagged.add(m[1]); });
    }
    const ingredients = toList(dish.ingredients).map((ing) => {
      const hit = words(ing).some((w) => flagged.has(w));
      return `<span class="dd-ing${hit ? " is-flag" : ""}">${hit ? "⚠️ " : ""}${escapeHtml(ing)}</span>`;
    }).join("") || "<p class=\"dd-muted\">No ingredients recorded.</p>";

    const vitamins = [
      row("Vitamin A", dish.vitamin_a_mcg, "mcg"), row("Vitamin C", dish.vitamin_c_mg, "mg"),
      row("Vitamin D", dish.vitamin_d_mcg, "mcg"), row("Vitamin K", dish.vitamin_k_mcg, "mcg"),
      row("B vitamins", dish.b_vitamin_mg, "mg"), row("Calcium", dish.calcium_mg, "mg"),
      row("Iron", dish.iron_mg, "mg"), row("Potassium", dish.potassium_mg, "mg"),
    ].join("");

    $("dish-detail").innerHTML = `
      <button type="button" class="dd-back" data-back>← All dishes</button>

      <div class="dd-detail-head">
        <span class="dd-detail-icon" aria-hidden="true">${iconFor(dish)}</span>
        <div>
          <p class="dd-eyebrow">${escapeHtml(dish.cuisine || "—")} · ${escapeHtml(dish.category || "—")}</p>
          <h2>${escapeHtml(dish.name)}</h2>
          ${dish.description ? `<p class="dd-desc">${escapeHtml(dish.description)}</p>` : ""}
        </div>
        <label class="dd-compare-toggle is-big"><input type="checkbox" data-compare="${escapeHtml(dish.dish_id)}" ${state.compare.includes(dish.dish_id) ? "checked" : ""}> Compare</label>
      </div>

      ${fit ? `
      <div class="dd-fit-panel is-${fit.status}">
        <div class="dd-fit-title">${fitPill(fit)} <strong>For you</strong></div>
        <ul>${fit.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}${fit.good.map((g) => `<li class="is-good">✅ ${escapeHtml(g)}</li>`).join("")}</ul>
      </div>` : `
      <div class="dd-fit-panel is-NONE"><p>Add your health profile on the <a href="dashboard.html">Dashboard</a> to see if this dish suits you.</p></div>`}

      <div class="dd-stats">
        <div class="dd-stat is-kcal"><strong>${fmt(dish.calories, 0)}</strong><small>kcal</small></div>
        <div class="dd-stat is-p"><strong>${fmt(dish.protein_g)}g</strong><small>Protein</small></div>
        <div class="dd-stat is-c"><strong>${fmt(dish.carbs_g)}g</strong><small>Carbs</small></div>
        <div class="dd-stat is-f"><strong>${fmt(dish.fat_g)}g</strong><small>Fat</small></div>
      </div>
      <p class="dd-serving">Per ${escapeHtml(dish.serving_description || "serving")}${num(dish.serving_size_g) !== null ? ` (${fmt(dish.serving_size_g, 0)} g)` : ""}</p>

      ${macro ? `
      <div class="dd-card-box">
        <h3>Where the energy comes from</h3>
        <div class="dd-macro-bar is-big">
          <span class="is-p" style="width:${macro.p.toFixed(1)}%">${macro.p >= 12 ? Math.round(macro.p) + "%" : ""}</span>
          <span class="is-c" style="width:${macro.c.toFixed(1)}%">${macro.c >= 12 ? Math.round(macro.c) + "%" : ""}</span>
          <span class="is-f" style="width:${macro.f.toFixed(1)}%">${macro.f >= 12 ? Math.round(macro.f) + "%" : ""}</span>
        </div>
        <div class="dd-macro-legend"><span><i class="is-p"></i>Protein</span><span><i class="is-c"></i>Carbs</span><span><i class="is-f"></i>Fat</span></div>
      </div>` : ""}

      <div class="dd-detail-grid">
        <div class="dd-card-box">
          <h3>Nutrition details</h3>
          ${row("Fiber", dish.fiber_g, "g")}${row("Sugar", dish.sugar_g, "g")}
          ${row("Sodium", dish.sodium_mg, "mg")}${row("Saturated fat", dish.saturated_fat_g, "g")}
          ${num(dish.glycemic_index) !== null ? `<div class="dd-row"><span>Glycemic index</span><strong>${fmt(dish.glycemic_index, 0)}</strong></div>` : ""}
        </div>
        <div class="dd-card-box">
          <h3>Vitamins &amp; minerals</h3>
          ${vitamins || '<p class="dd-muted">Not recorded for this dish.</p>'}
        </div>
      </div>

      <div class="dd-card-box">
        <h3>Ingredients</h3>
        <div class="dd-ings">${ingredients}</div>
        <div class="dd-tag-rows">
          <p><span>Allergens</span>${allergens.length ? allergens.map((a) => `<em class="dd-tag is-allergen">${escapeHtml(a)}</em>`).join("") : '<em class="dd-tag">None listed</em>'}</p>
          <p><span>Diet tags</span>${toList(dish.diet_tags).map((t) => `<em class="dd-tag">${escapeHtml(t)}</em>`).join("") || '<em class="dd-tag">—</em>'}</p>
          <p><span>Health badges</span>${(dish.health_status_labels || []).map((b) => `<em class="dd-tag is-good">${escapeHtml(b)}</em>`).join("") || '<em class="dd-tag">—</em>'}</p>
        </div>
      </div>`;
  }

  // ---------- compare ----------
  function toggleCompare(id, on) {
    id = Number(id);
    if (on && !state.compare.includes(id)) {
      if (state.compare.length >= MAX_COMPARE) state.compare.shift();
      state.compare.push(id);
    }
    if (!on) state.compare = state.compare.filter((x) => x !== id);
    renderTray();
    if (!$("dish-list").hidden) renderDishes();
  }

  function renderTray() {
    const n = state.compare.length;
    $("dd-tray").hidden = n === 0;
    $("dd-tray-text").textContent = n === 1 ? "1 dish selected. Pick one more to compare." : `${n} dishes selected`;
    $("dd-tray-compare").disabled = n < 2;
  }

  async function openCompare() {
    showView("compare");
    $("dd-compare").innerHTML = '<div class="dd-skeleton is-tall"></div>';
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const dishes = await Promise.all(state.compare.map((id) => request(`/api/dish-dna/${id}`)));
      const rows = [
        ["Calories", "calories", "kcal", 0], ["Protein", "protein_g", "g", 1], ["Carbs", "carbs_g", "g", 1],
        ["Fat", "fat_g", "g", 1], ["Fiber", "fiber_g", "g", 1], ["Sugar", "sugar_g", "g", 1],
        ["Sodium", "sodium_mg", "mg", 0], ["Saturated fat", "saturated_fat_g", "g", 1],
        ["Glycemic index", "glycemic_index", "", 0], ["Iron", "iron_mg", "mg", 1], ["Calcium", "calcium_mg", "mg", 0],
      ];
      const body = rows.map(([label, key, unit, d]) => {
        const vals = dishes.map((x) => num(x[key]));
        if (vals.every((v) => v === null)) return "";
        const max = Math.max(...vals.map((v) => v || 0)) || 1;
        return `<div class="dd-cmp-row"><span class="dd-cmp-label">${label}</span>${dishes.map((x, i) => `
          <div class="dd-cmp-cell"><strong>${vals[i] === null ? "—" : fmt(vals[i], d) + (unit ? " " + unit : "")}</strong>
            <span class="dd-cmp-bar"><i class="is-${i}" style="width:${((vals[i] || 0) / max * 100).toFixed(1)}%"></i></span></div>`).join("")}</div>`;
      }).join("");
      $("dd-compare").innerHTML = `
        <button type="button" class="dd-back" data-back>← All dishes</button>
        <div class="dd-card-box">
          <h2 class="dd-cmp-title">Side by side</h2>
          <div class="dd-cmp-row is-head"><span></span>${dishes.map((x, i) => `
            <div class="dd-cmp-cell"><span class="dd-cmp-dot is-${i}"></span>${iconFor(x)} <strong>${escapeHtml(x.name)}</strong> ${fitPill(fitFor(x))}</div>`).join("")}</div>
          ${body}
          <p class="dd-muted">Values are per serving. Compare portion sizes too: ${dishes.map((x) => `${escapeHtml(x.name)} is ${escapeHtml(x.serving_description || "1 serving")}${num(x.serving_size_g) !== null ? ` (${fmt(x.serving_size_g, 0)} g)` : ""}`).join(", ")}.</p>
        </div>`;
    } catch (err) {
      $("dd-compare").innerHTML = `<button type="button" class="dd-back" data-back>← All dishes</button><div class="dd-empty"><span>😕</span><strong>Couldn't compare</strong>${escapeHtml(err.message)}</div>`;
    }
  }

  // ---------- views ----------
  function showView(view) {
    $("dish-list").hidden = view !== "list";
    document.querySelector(".dd-filters").hidden = view !== "list";
    $("dish-detail").hidden = view !== "detail";
    $("dd-compare").hidden = view !== "compare";
    if (view === "compare") $("dd-tray").hidden = true;
    else renderTray();
  }

  // ---------- start ----------
  function init() {
    $("dd-search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      state.search = $("dish-search").value.trim();
      loadDishes();
    });
    $("dish-search").addEventListener("search", () => {
      if (!$("dish-search").value) { state.search = ""; loadDishes(); }
    });
    $("dd-category-chips").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-category]");
      if (!chip) return;
      state.category = chip.dataset.category;
      document.querySelectorAll(".dd-chip").forEach((c) => c.classList.toggle("is-active", c === chip));
      loadDishes();
    });
    $("dd-cuisine").addEventListener("change", (e) => { state.cuisine = e.target.value; loadDishes(); });

    document.addEventListener("click", (e) => {
      if (e.target.closest("[data-back]")) { showView("list"); renderDishes(); return; }
      const open = e.target.closest("[data-open]");
      if (open) { openDish(open.dataset.open); return; }
      if (e.target.closest(".dd-compare-toggle")) return;
      const card = e.target.closest(".dd-card");
      if (card) openDish(card.dataset.id);
    });
    document.addEventListener("keydown", (e) => {
      const card = e.target.closest && e.target.closest(".dd-card");
      if (card && e.key === "Enter" && e.target === card) openDish(card.dataset.id);
    });
    document.addEventListener("change", (e) => {
      if (e.target.matches("[data-compare]")) toggleCompare(e.target.dataset.compare, e.target.checked);
    });
    $("dd-tray-compare").addEventListener("click", openCompare);
    $("dd-tray-clear").addEventListener("click", () => {
      state.compare = [];
      renderTray();
      document.querySelectorAll("[data-compare]").forEach((b) => { b.checked = false; });
      if (!$("dish-list").hidden) renderDishes();
    });

    const logout = $("logoutBtn");
    if (logout) logout.addEventListener("click", (e) => {
      e.preventDefault();
      ["access_token", "session_token", "user", "dietrx_user"].forEach((k) => localStorage.removeItem(k));
      window.location.href = "login.html";
    });

    loadFacets();
    loadProfile().then(loadDishes);
  }

  document.addEventListener("DOMContentLoaded", init);
})();