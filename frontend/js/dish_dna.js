// SCRUM-47: Dish DNA page logic
// apiRequest() comes from api.js (loaded before this file in dish_dna.html)

const searchInput = document.getElementById("dish-search");
const searchBtn = document.getElementById("search-btn");
const dishList = document.getElementById("dish-list");
const dishDetail = document.getElementById("dish-detail");

// Health status label map (matches backend derive_health_statuses).
// Friendly names can be added here, e.g. { high_protein: "High Protein" }
const HEALTH_STATUS_LABELS = {};

const DEFAULT_DISCLAIMER =
  "DietRx is a decision-support system and does not replace professional medical advice.";
const DEMO_NOTE =
  "Demo data: nutrition values are for development only and are not clinically verified.";

let inDetailView = false;

// ---------- Small helpers ----------

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (Number.isNaN(num)) return "—";
  return num.toFixed(digits);
}

function toList(value) {
  if (Array.isArray(value)) {
    return value.filter((s) => s && String(s).trim().toLowerCase() !== "none");
  }
  if (typeof value === "string" && value.trim()) {
    // "none" is a placeholder, not a real allergen/tag, so drop it
    return value
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s && s.toLowerCase() !== "none");
  }
  return [];
}

// ---------- Dish list ----------

// Load dishes with optional search filter
async function loadDishes(filters = {}) {
  let path = "/api/dish-dna";
  const params = new URLSearchParams();
  if (filters.search) params.append("search", filters.search.trim().toLowerCase());
  if (params.toString()) path += "?" + params.toString();

  try {
    const data = await apiRequest(path);
    console.log("API response data:", data);

    renderDishes(data.items || data.dishes || [], !!filters.search);

    // Only fill the disclaimer if the page has a .disclaimer element
    const disclaimerEl = document.querySelector(".disclaimer");
    if (disclaimerEl && data.disclaimer) {
      disclaimerEl.textContent = data.disclaimer;
    }
  } catch (err) {
    dishList.innerHTML = `<p class="error">Error loading dishes: ${escapeHtml(err.message)}</p>`;
  }
}

// Render dish cards in the list
function renderDishes(dishes, isSearch = false) {
  if (!dishes || dishes.length === 0) {
    dishList.innerHTML = isSearch
      ? `<p class="no-results">No dishes found matching your search.</p>`
      : `<p class="no-results">No dishes available in database.</p>`;
    return;
  }

  dishList.innerHTML =
    `<p class="demo-note">${DEMO_NOTE}</p>` +
    dishes
      .map(
        (dish) => `
      <div class="dish-card" data-dish-id="${escapeHtml(dish.id || dish.dish_id)}" tabindex="0">
        <h3>${escapeHtml(dish.name)}</h3>
        <p class="dish-cuisine">Cuisine: ${escapeHtml(dish.cuisine || "—")}</p>
        <p class="dish-category">Category: ${escapeHtml(dish.category || "—")}</p>
      </div>`
      )
      .join("");

  dishList.querySelectorAll(".dish-card").forEach((card) => {
    card.addEventListener("click", () => loadDishDetail(card.dataset.dishId));
    card.addEventListener("keypress", (e) => {
      if (e.key === "Enter") loadDishDetail(card.dataset.dishId);
    });
  });
}

// ---------- Dish detail ----------

// Load and display a single dish's DNA details
async function loadDishDetail(dishId) {
  showLoading(true);
  try {
    const data = await apiRequest(`/api/dish-dna/${dishId}`);
    renderDishDetail(data);
  } catch (err) {
    dishDetail.hidden = false;
    dishDetail.innerHTML = `<p class="error">Dish not found: ${escapeHtml(err.message)}</p>`;
  } finally {
    showLoading(false);
  }
}

// Render the detailed DNA card
function renderDishDetail(dish) {
  const tags = toList(dish.health_statuses);
  const tagDisplay = tags.length > 0
    ? tags
        .map((t) => {
          const label = HEALTH_STATUS_LABELS[t] || String(t).replace(/_/g, " ");
          return `<span class="badge ${escapeHtml(String(t).toLowerCase())}">${escapeHtml(label)}</span>`;
        })
        .join("")
    : `<span class="badge">No specific tags</span>`;

  const ingredients = toList(dish.ingredients);
  const ingredientsList = ingredients.length > 0
    ? `<ul>${ingredients.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`
    : "<p>—</p>";

  const allergens = toList(dish.allergens);
  const allergenText = allergens.length > 0 ? allergens.map(escapeHtml).join(", ") : "None listed";

  dishDetail.innerHTML = `
    <h2>${escapeHtml(dish.name)}</h2>
    <p class="demo-note">${DEMO_NOTE}</p>

    <div class="dish-meta">
      <p><strong>Cuisine:</strong> ${escapeHtml(dish.cuisine || "—")}</p>
      <p><strong>Category:</strong> ${escapeHtml(dish.category || "—")}</p>
      <p><strong>Serving size:</strong> ${dish.serving_size_g ? escapeHtml(dish.serving_size_g) + " g" : "—"}</p>
      <p><strong>Allergens:</strong> ${allergenText}</p>
    </div>

    <section class="dish-nutrition-card">
      <h3>Nutritional Breakdown (per serving)</h3>
      <div class="nutrition-grid">
        <div class="nutrition-item"><span>Calories</span><strong>${formatNumber(dish.calories, 0)} kcal</strong></div>
        <div class="nutrition-item"><span>Protein</span><strong>${formatNumber(dish.protein_g)} g</strong></div>
        <div class="nutrition-item"><span>Carbs</span><strong>${formatNumber(dish.carbs_g)} g</strong></div>
        <div class="nutrition-item"><span>Fat</span><strong>${formatNumber(dish.fat_g)} g</strong></div>
        <div class="nutrition-item"><span>Fiber</span><strong>${formatNumber(dish.fiber_g)} g</strong></div>
        <div class="nutrition-item"><span>Sodium</span><strong>${formatNumber(dish.sodium_mg, 0)} mg</strong></div>
      </div>
    </section>

    <section class="dish-ingredients">
      <h3>Ingredients</h3>
      ${ingredientsList}
    </section>

    <section class="dish-badges">
      <h3>Dietary Suitability</h3>
      <div class="badges-chip-group">${tagDisplay}</div>
    </section>

    <p class="disclaimer">${escapeHtml(dish.disclaimer || DEFAULT_DISCLAIMER)}</p>
  `;

  inDetailView = true;
  dishDetail.hidden = false;
  dishList.hidden = true;
  searchInput.disabled = true;
  searchBtn.textContent = "Back to list";
  searchBtn.classList.add("back-btn");
}

// Go back from detail view to the dish list
function showList() {
  inDetailView = false;
  dishDetail.hidden = true;
  dishList.hidden = false;
  searchInput.disabled = false;
  searchBtn.textContent = "Search";
  searchBtn.classList.remove("back-btn");
}

// Show/hide loading state
function showLoading(show) {
  const loading = dishDetail.querySelector(".loader");
  if (loading) loading.hidden = !show;
  searchInput.disabled = show || inDetailView;
  searchBtn.disabled = show;
}

// ---------- Events ----------

// Search button: searches in list view, goes back in detail view
searchBtn.addEventListener("click", () => {
  if (inDetailView) {
    showList();
    return;
  }
  const query = searchInput.value.trim();
  if (query) loadDishes({ search: query });
  else loadDishes();
});

// Enter key in the search box
searchInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter" && !inDetailView) searchBtn.click();
});

// Initial load - show all dishes on DOM mount
document.addEventListener("DOMContentLoaded", () => {
  console.log("Dish DNA page loaded - initiating automatic dish load");
  loadDishes();
});