
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

function renderMissingPrices(items = []) {
    const list = document.getElementById("missing-prices");
    clear(list);

    if (!items.length) {
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
            costCell.textContent = money(0);

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
            costCell.textContent = money(meal.cost_bdt);

            row.append(dayCell, mealCell, costCell);
            body.appendChild(row);
        });

        const totalRow = document.createElement("tr");
        const labelCell = document.createElement("td");
        const totalLabelCell = document.createElement("td");
        const totalCostCell = document.createElement("td");

        labelCell.textContent = "";
        totalLabelCell.textContent = "Day total";
        totalCostCell.textContent = money(day.cost_bdt);

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
        message.textContent = "No monthly budget set. Add your budget to compare estimated costs.";
        details.textContent = "";
        return;
    }

    const monthly = Number(budget.monthly_budget_bdt);
    const used = Number(budget.used_percent);

    if (budget.status === "within") {
        message.textContent = "Your estimated monthly cost is within your budget.";
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
    renderMissingPrices(data.missing_prices);

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
