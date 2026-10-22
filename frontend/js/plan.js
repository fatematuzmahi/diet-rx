/* SCRUM-48: Personalized Diet + Activity Plan UI wiring (SRS 3.3.6).
 *
 * Uses the shared apiRequest() from api.js, which automatically attaches the
 * Bearer access_token (SCRUM-44). The patient is identified solely by the
 * server from their token, so this script never sends a patient id -- it only
 * sends the optional overrides the user typed.
 *
 * Endpoints used:
 *   POST /api/plans/me          generate (or regenerate) the plan
 *   GET  /api/plans/me          read the stored CURRENT plan
 *   GET  /api/plans/me/history  list previous + current plans
 */
(function () {
    const status = document.getElementById("plan-status");
    const generateBtn = document.getElementById("plan-generate");
    const regenerateBtn = document.getElementById("plan-regenerate");
    const historyBtn = document.getElementById("plan-history");

    const resultSection = document.getElementById("plan-result");
    const metaEl = document.getElementById("plan-meta");
    const reviewEl = document.getElementById("plan-review");
    const reviewTextEl = document.getElementById("plan-review-text");
    const conflictsEl = document.getElementById("plan-conflicts");
    const daysEl = document.getElementById("plan-meal-days");
    const activitiesEl = document.getElementById("plan-activities");
    const budgetNotesEl = document.getElementById("plan-budget-notes");
    const notesEl = document.getElementById("plan-notes");
    const disclaimerEl = document.getElementById("plan-disclaimer");
    const cautionEl = document.getElementById("plan-activity-caution");

    const historySection = document.getElementById("plan-history-section");
    const historyListEl = document.getElementById("plan-history-list");

    function setStatus(message, kind) {
        if (!status) return;
        status.textContent = message || "";
        status.classList.remove("is-success", "is-error", "is-loading");
        if (kind) status.classList.add("is-" + kind);
    }

    function setBusy(busy) {
        [generateBtn, regenerateBtn, historyBtn].forEach(function (btn) {
            if (btn) btn.disabled = busy;
        });
    }

    function valueOf(id) {
        const el = document.getElementById(id);
        return el ? el.value.trim() : "";
    }

    function numberOrNull(text) {
        if (text === "") return null;
        const parsed = Number(text);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function collectPayload(regenerate) {
        const payload = { regenerate: regenerate };
        const days = numberOrNull(valueOf("plan-days"));
        if (days !== null) payload.days = days;
        const goals = valueOf("plan-goals");
        if (goals) payload.goals = goals;
        const activityLevel = valueOf("plan-activity-level");
        if (activityLevel) payload.activity_level = activityLevel;
        const foodPreference = valueOf("plan-food-preference");
        if (foodPreference) payload.food_preference = foodPreference;
        const budget = numberOrNull(valueOf("plan-budget"));
        if (budget !== null) payload.monthly_budget = budget;
        return payload;
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function makeElement(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }

    function renderMeta(plan) {
        clear(metaEl);
        const parts = [];
        if (plan.plan_id) parts.push("Plan #" + plan.plan_id);
        if (plan.calorie_target) parts.push(plan.calorie_target + " kcal/day");
        if (plan.generated_at) parts.push("Generated " + plan.generated_at);
        if (plan.restrictions_applied && plan.restrictions_applied.length) {
            parts.push("Restrictions applied: " + plan.restrictions_applied.join(", "));
        }
        metaEl.appendChild(makeElement("p", "plan-meta-line", parts.join("  |  ")));
    }

    function renderReview(plan) {
        const needsReview = plan.requires_review === true;
        reviewEl.hidden = !needsReview;
        if (!needsReview) {
            reviewTextEl.textContent = "";
            return;
        }
        const conflicts = (plan.conflicts || [])
            .filter(function (c) { return c.requires_review; })
            .map(function (c) { return c.title; });
        reviewTextEl.textContent = conflicts.length
            ? " " + conflicts.join("; ") +
              ". Please have a doctor or nutritionist check this plan before you start it."
            : " Please have a doctor or nutritionist check this plan before you start it.";
    }


    function renderConflicts(plan) {
        clear(conflictsEl);
        const conflicts = plan.conflicts || [];
        if (!conflicts.length) return;
        conflictsEl.appendChild(makeElement("h3", null, "Detected conflicts"));
        const list = document.createElement("ul");
        conflicts.forEach(function (conflict) {
            const item = document.createElement("li");
            item.className = "plan-conflict plan-conflict-" + (conflict.severity || "medium");
            item.appendChild(makeElement("strong", null, conflict.title));
            item.appendChild(makeElement("span", "plan-conflict-text", " " + conflict.explanation));
            if (conflict.resolution) {
                item.appendChild(makeElement("span", "plan-conflict-resolution", " " + conflict.resolution));
            }
            list.appendChild(item);
        });
        conflictsEl.appendChild(list);
    }

    function renderDays(plan) {
        clear(daysEl);
        const days = plan.days || [];
        if (!days.length) {
            daysEl.appendChild(makeElement("p", null, "No meals were generated."));
            return;
        }
        days.forEach(function (day) {
            const block = makeElement("article", "plan-day");
            block.appendChild(makeElement("h4", null, "Day " + day.day +
                (day.daily_calories ? "  (" + day.daily_calories + " kcal)" : "")));
            const list = document.createElement("ul");
            (day.meals || []).forEach(function (meal) {
                const item = document.createElement("li");
                item.appendChild(makeElement("strong", null, meal.meal_type + " (" + meal.time + ")"));
                item.appendChild(makeElement("span", "plan-meal-items", ": " + (meal.items || []).join(", ")));
                if (meal.calories) {
                    item.appendChild(makeElement("span", "plan-meal-calories", " — " + meal.calories + " kcal"));
                }
                list.appendChild(item);
            });
            block.appendChild(list);
            daysEl.appendChild(block);
        });
    }

    function renderActivities(plan) {
        clear(activitiesEl);
        const activities = plan.activities || [];
        if (!activities.length) {
            activitiesEl.appendChild(makeElement("p", null, "No activities were generated."));
            return;
        }
        const list = document.createElement("ul");
        activities.forEach(function (activity) {
            const item = document.createElement("li");
            item.appendChild(makeElement("strong", null, activity.name));
            const detail = [];
            if (activity.description) detail.push(activity.description);
            if (activity.duration_minutes) detail.push(activity.duration_minutes + " minutes");
            if (activity.intensity) detail.push(activity.intensity + " intensity");
            if (activity.frequency) detail.push(activity.frequency);
            if (detail.length) {
                item.appendChild(makeElement("span", "plan-activity-detail", " — " + detail.join(", ")));
            }
            list.appendChild(item);
        });
        activitiesEl.appendChild(list);
    }

    function renderList(node, entries) {
        clear(node);
        (entries || []).forEach(function (entry) {
            node.appendChild(makeElement("li", null, entry));
        });
        if (!(entries || []).length) {
            node.appendChild(makeElement("li", "plan-note-empty", "None."));
        }
    }

    function renderPlan(plan) {
        renderMeta(plan);
        renderReview(plan);
        renderConflicts(plan);
        renderDays(plan);
        renderActivities(plan);
        renderList(budgetNotesEl, plan.budget_notes);
        renderList(notesEl, plan.notes);
        disclaimerEl.textContent = plan.disclaimer || "";
        cautionEl.textContent = plan.activity_caution || "";
        resultSection.hidden = false;
    }

    async function generate(regenerate) {
        setBusy(true);
        setStatus(regenerate ? "Regenerating your plan…" : "Generating your plan…", "loading");
        try {
            const plan = await apiRequest("/api/plans/me", {
                method: "POST",
                body: JSON.stringify(collectPayload(regenerate)),
            });
            renderPlan(plan);
            setStatus(
                plan.requires_review
                    ? "Plan generated. Some conflicts need a professional's review."
                    : "Plan generated successfully.",
                plan.requires_review ? "loading" : "success"
            );
        } catch (error) {
            setStatus(error.message || "Could not generate your plan.", "error");
        } finally {
            setBusy(false);
        }
    }

    async function loadCurrent() {
        setStatus("Loading your current plan…", "loading");
        try {
            const plan = await apiRequest("/api/plans/me", { method: "GET" });
            if (!plan || plan.found !== true) {
                setStatus("You have no plan yet — generate one to get started.", "loading");
                return;
            }
            renderPlan(plan);
            setStatus("Showing your current plan.", "success");
        } catch (error) {
            setStatus(error.message || "Could not load your current plan.", "error");
        }
    }

    async function loadHistory() {
        setStatus("Loading your plan history…", "loading");
        try {
            const history = await apiRequest("/api/plans/me/history", { method: "GET" });
            clear(historyListEl);
            const plans = (history && history.plans) || [];
            if (!plans.length) {
                historyListEl.appendChild(makeElement("p", null, "No previous plans yet."));
            } else {
                const list = document.createElement("ul");
                plans.forEach(function (entry) {
                    const item = document.createElement("li");
                    const parts = ["Plan #" + entry.plan_id, entry.status];
                    if (entry.calorie_target) parts.push(entry.calorie_target + " kcal/day");
                    if (entry.created_at) parts.push(entry.created_at);
                    if (entry.conflict_count) parts.push(entry.conflict_count + " conflict(s)");
                    if (entry.requires_review) parts.push("needs review");
                    item.textContent = parts.join(" — ");
                    list.appendChild(item);
                });
                historyListEl.appendChild(list);
            }
            historySection.hidden = false;
            setStatus("Plan history loaded.", "success");
        } catch (error) {
            setStatus(error.message || "Could not load your plan history.", "error");
        }
    }

    if (generateBtn) generateBtn.addEventListener("click", function () { generate(true); });
    if (regenerateBtn) regenerateBtn.addEventListener("click", function () { generate(true); });
    if (historyBtn) historyBtn.addEventListener("click", loadHistory);

    // Show the stored plan on first view so the patient sees their current plan.
    document.addEventListener("DOMContentLoaded", loadCurrent);
    if (document.readyState !== "loading") loadCurrent();
})();
