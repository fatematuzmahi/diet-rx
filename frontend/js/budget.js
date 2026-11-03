
"use strict";

const statusElement = document.getElementById("budget-status");
const contentElement = document.getElementById("budget-content");

function money(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }

    return "৳" + Math.round(Number(value)).toLocaleString("en-BD");
}

function addText(parent, tag, text, className) {
    const element = document.createElement(tag);
    element.textContent = text ?? "—";

    if (className) {
        element.className = className;
    }

    parent.appendChild(element);
    return element;
}

function clear(element) {
    element.replaceChildren();
}

function showStatus(message, type = "") {
    statusElement.textContent = message;
    statusElement.className = `status ${type}`.trim();
}

function renderTopItems(items = []) {
    const list = document.getElementById("top-items");
    clear(list);

    if (!items.length) {
        addText(list, "li", "No priced items to display yet.");
        return;
    }

    items.forEach((item) => {
        addText(
            list,
            "li",
            `${item.name} — ${item.times} time(s), total ${money(item.total_bdt)}`
        );
    });
}

// Meal / day cost: a missing price is never shown as ৳0.
function setCost(cell, value, complete) {
    clear(cell);
    if (value === null || value === undefined) {
        addText(cell, "span", "No price yet", "cost-missing");
        return;
    }
    cell.append(money(value));
    if (complete === false) {
        addText(cell, "span", " · partial", "cost-partial");
    }
}

function renderMissingPrices(items = [], partly = []) {
    const list = document.getElementById("missing-prices");
    clear(list);

    partly.forEach((item) => {
        addText(
            list,
            "li",
            `${item.name} — partly priced (${item.occurrences} occurrence(s))`
        );
    });

    if (!items.length && !partly.length) {
        addText(list, "li", "All plan items have recorded prices.");
        return;
    }

    items.forEach((item) => {
        addText(
            list,
            "li",
            `${item.name} — missing price (${item.occurrences} occurrence(s))`
        );
    });
}

function renderWarnings(warnings = []) {
    const list = document.getElementById("warnings");
    clear(list);

    if (!warnings.length) {
        addText(list, "li", "No additional warnings.");
        return;
    }

    warnings.forEach((warning) => addText(list, "li", warning));
}

function renderDays(days = []) {
    const body = document.getElementById("day-costs");
    clear(body);

    if (!days.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 3;
        cell.textContent = "No day-by-day cost data is available.";
        row.appendChild(cell);
        body.appendChild(row);
        return;
    }

    days.forEach((day, index) => {
        const meals = day.meals || [];

        if (!meals.length) {
            const row = document.createElement("tr");
            const dayCell = document.createElement("td");
            const mealCell = document.createElement("td");
            const costCell = document.createElement("td");

            dayCell.textContent = day.label || `Day ${day.day ?? index + 1}`;
            mealCell.textContent = "No meals";
            costCell.textContent = "—";

            row.append(dayCell, mealCell, costCell);
            body.appendChild(row);
            return;
        }

        meals.forEach((meal, mealIndex) => {
            const row = document.createElement("tr");
            const dayCell = document.createElement("td");
            const mealCell = document.createElement("td");
            const costCell = document.createElement("td");

            dayCell.textContent = mealIndex === 0
                ? (day.label || `Day ${day.day ?? index + 1}`)
                : "";

            mealCell.textContent = meal.meal_type || "Meal";
            setCost(costCell, meal.cost_bdt, meal.complete);

            row.append(dayCell, mealCell, costCell);
            body.appendChild(row);
        });

        const totalRow = document.createElement("tr");
        const labelCell = document.createElement("td");
        const totalLabelCell = document.createElement("td");
        const totalCostCell = document.createElement("td");

        labelCell.textContent = "";
        totalLabelCell.textContent = "Day total";
        setCost(totalCostCell, day.cost_bdt, day.complete);

        totalLabelCell.style.fontWeight = "700";
        totalCostCell.style.fontWeight = "700";

        totalRow.append(labelCell, totalLabelCell, totalCostCell);
        body.appendChild(totalRow);
    });
}

function renderBudget(budget = {}) {
    const message = document.getElementById("budget-message");
    const details = document.getElementById("budget-details");
    const bar = document.getElementById("budget-bar");

    bar.style.width = "0%";
    bar.classList.remove("above");

    if (budget.status === "not_set" || budget.monthly_budget_bdt == null) {
        message.textContent = "No budget set. Set your budget on the Budget page to compare estimated costs.";
        details.textContent = "";
        return;
    }

    if (budget.status === "unknown") {
        message.textContent = "No plan item has a recorded price yet, so the cost can't be compared with your budget.";
        details.textContent = `Budget: ${money(budget.monthly_budget_bdt)} a month.`;
        return;
    }

    const monthly = Number(budget.monthly_budget_bdt);
    const used = Number(budget.used_percent);

    if (budget.status === "within") {
        message.textContent = "Your estimated monthly cost is within your budget.";
    } else if (budget.status === "within_so_far") {
        message.textContent =
            "Within your budget so far. Some items have no price yet, so the real cost will be higher.";
    } else {
        message.textContent = "Your estimated monthly cost is above your budget.";
        bar.classList.add("above");
    }

    if (Number.isFinite(used)) {
        bar.style.width = `${Math.max(0, Math.min(used, 100))}%`;
        details.textContent =
            `${used}% of your monthly budget used. ` +
            `Budget: ${money(monthly)}. ` +
            `Difference: ${money(budget.difference_bdt)}. ` +
            `Daily budget: ${money(budget.budget_per_day_bdt)}.`;
    } else {
        details.textContent =
            `Budget: ${money(monthly)}. Difference: ${money(budget.difference_bdt)}.`;
    }
}

function renderEstimate(data) {
    document.getElementById("per-day").textContent = money(data.per_day_bdt);
    document.getElementById("per-week").textContent = money(data.per_week_bdt);
    document.getElementById("per-month").textContent = money(data.per_month_bdt);

    renderBudget(data.budget);
    renderDays(data.days);
    renderTopItems(data.top_items);
    renderMissingPrices(data.missing_prices, data.partly_priced);

    const note = document.getElementById("cost-note");
    note.hidden = data.complete !== false;
    note.textContent = data.per_day_bdt === null
        ? "No plan item has a recorded price yet, so the cost can't be estimated."
        : `Only ${data.priced_items} of ${data.total_items} plan items have a full recorded price, so these totals are lower than the real cost.`;

    document.getElementById("coverage").textContent =
        `Price coverage: ${Number(data.coverage_percent ?? 0)}% of plan items matched recorded prices.`;

    document.getElementById("prices-date").textContent = data.prices_as_of
        ? `Oldest recorded price used: ${data.prices_as_of}.`
        : "No recorded price date is available for the priced items.";

    renderWarnings(data.warnings);

    document.getElementById("disclaimer").textContent =
        data.disclaimer || "This is an estimate. Actual food costs may vary.";

    contentElement.hidden = false;
    showStatus(
        data.plan_source === "current"
            ? "Showing the current meal plan because no accepted plan was found."
            : "Cost estimate loaded.",
        "success"
    );
}

async function loadBudgetEstimate() {
    showStatus("Loading your estimate…");

    try {
        const data = await apiRequest("/api/cost/estimate");

        if (!data.found) {
            contentElement.hidden = true;
            showStatus(data.message || "Accept a meal plan first.", "error");
            return;
        }

        renderEstimate(data);
    } catch (error) {
        contentElement.hidden = true;
        showStatus(
            error?.message || "Could not load the cost estimate. Please try again.",
            "error"
        );
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const logoutButton = document.getElementById("logoutBtn");

    if (logoutButton) {
        logoutButton.addEventListener("click", (event) => {
            event.preventDefault();

            if (typeof logout === "function") {
                logout();
            } else {
                localStorage.removeItem("access_token");
                localStorage.removeItem("session_token");
                window.location.replace("login.html");
            }
        });
    }

    loadBudgetEstimate();
});
