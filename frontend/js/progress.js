
// SCRUM-54: Progress Dashboard
// Uses existing daily-log trend and meal-plan endpoints.
// Do not modify api.js or existing dashboard files.

(() => {
  "use strict";

  // Keep these targets aligned with the Dashboard (SCRUM-53).
  const TARGETS = {
    meals: 4,
    water_ml: 2000,
    activity_min: 30,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const state = {
    period: 7,
    points: [],
    plan: null,
    loading: false,
    requestId: 0,
  };

  const METRICS = {
    meals: {
      pointKey: "meals",
      targetKey: "meals",
      label: "Meals",
    },
    water: {
      pointKey: "water_ml",
      targetKey: "water_ml",
      label: "Water",
    },
    activity: {
      pointKey: "activity_min",
      targetKey: "activity_min",
      label: "Activity",
    },
    medication: {
      pointKey: "meds_taken",
      targetKey: "meds_total",
      label: "Medication",
    },
  };

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = value;
  }

  function setError(message = "") {
    const error = $("#progress-error");
    if (!error) return;
    error.textContent = message;
    error.hidden = !message;
  }

  function statusFor(percent) {
    if (percent === null || !Number.isFinite(percent)) {
      return { label: "No data", className: "no-data" };
    }
    if (percent >= 80) {
      return { label: "On track", className: "on-track" };
    }
    if (percent >= 50) {
      return { label: "Needs attention", className: "needs-attention" };
    }
    return { label: "Off track", className: "off-track" };
  }

  function clampPercent(actual, target) {
    if (!Number.isFinite(actual) || !Number.isFinite(target) || target <= 0) {
      return null;
    }
    return Math.min(Math.max(actual, 0) / target, 1) * 100;
  }

  function dayKey(dateString) {
    return String(dateString || "").slice(0, 10);
  }

  function formatDate(dateString) {
    const date = new Date(`${dayKey(dateString)}T12:00:00`);
    if (Number.isNaN(date.getTime())) return dayKey(dateString);

    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
    }).format(date);
  }

  // Match backend today_index(): after the last plan day, repeat from Day 1.
  function getMealsForDate(plan, dateString) {
    if (!plan || !Array.isArray(plan.days) || plan.days.length === 0) {
      return null;
    }

    const key = dayKey(dateString);
    const generated = plan.generated_at ? new Date(plan.generated_at) : null;

    if (!generated || Number.isNaN(generated.getTime())) {
      return null;
    }

    // Use the generated date's calendar day, matching the backend date logic.
    const planStart = new Date(
      generated.getFullYear(),
      generated.getMonth(),
      generated.getDate()
    );
    const targetDate = new Date(`${key}T12:00:00`);

    let offset = Math.floor((targetDate - planStart) / 86400000);

    if (offset < 0) {
      offset = 0;
    }

    const index = offset % plan.days.length;
    const meals = plan.days[index]?.meals;

    return Array.isArray(meals) ? meals.length : null;
  }

  async function loadMealPlan() {
    // Prefer the accepted plan; otherwise fall back to the current plan.
    try {
      const accepted = await apiRequest("/api/meal-plan/accepted");
      if (accepted?.found && accepted.plan) return accepted.plan;
    } catch (error) {
      // Try the current plan if the accepted-plan request fails.
    }

    try {
      const current = await apiRequest("/api/plans/me");
      if (current?.found && Array.isArray(current.days)) return current;
      return null;
    } catch (error) {
      throw new Error(`Could not load your meal plan: ${error.message}`);
    }
  }

  async function loadData() {
    if (state.loading) return;

    state.loading = true;
    const requestId = ++state.requestId;
    const refreshButton = $("#refresh-progress");

    if (refreshButton) refreshButton.disabled = true;

    setText("#progress-status", "Loading your progress…");
    setError("");

    try {
      const [trend, planResult] = await Promise.allSettled([
        apiRequest(`/api/daily-log/trend?days=${state.period}`),
        loadMealPlan(),
      ]);

      if (requestId !== state.requestId) return;

      if (trend.status === "rejected") {
        throw new Error(
          trend.reason?.message || "Could not load your progress."
        );
      }

      state.points = Array.isArray(trend.value.points)
        ? trend.value.points
        : [];
      state.plan =
        planResult.status === "fulfilled" ? planResult.value : null;

      render();

      if (planResult.status === "rejected") {
        setError(
          `${planResult.reason?.message || "Could not load meal plan targets."} Meals may show No data.`
        );
      }

      const loggedDays = Number(trend.value.days_logged) || 0;

      setText(
        "#progress-status",
        loggedDays === 0
          ? "No log entries found in this period."
          : `${loggedDays} of ${state.points.length} days logged`
      );
    } catch (error) {
      if (requestId !== state.requestId) return;

      setError(error.message || "Could not load progress. Please try again.");
      setText("#progress-status", "Progress could not be loaded.");
      setText("#overall-percent", "—");
      setText("#overall-detail", "Unable to calculate adherence.");
      setStatus($("#overall-status"), null);
      renderNoDataCards();
    } finally {
      if (requestId === state.requestId) {
        state.loading = false;
        if (refreshButton) refreshButton.disabled = false;
      }
    }
  }

  function setStatus(element, percent) {
    if (!element) return;

    const status = statusFor(percent);
    element.className = `status-pill ${status.className}`;
    element.textContent = status.label;
  }

  function targetFor(metricKey, point) {
    if (metricKey === "meals") {
      return getMealsForDate(state.plan, point.log_date);
    }

    if (metricKey === "medication") {
      const total = Number(point.meds_total) || 0;
      return total > 0 ? total : null;
    }

    return TARGETS[METRICS[metricKey].targetKey];
  }

  function actualFor(metricKey, point) {
    const raw = Number(point[METRICS[metricKey].pointKey]);
    return Number.isFinite(raw) ? raw : 0;
  }

  // Missing logs count as zero when a valid target exists.
  // Days without a target are excluded from that category's average.
  function calcAdherence(points, metricKey) {
    const daily = points.map((point) => {
      const target = targetFor(metricKey, point);
      const actual = actualFor(metricKey, point);

      if (target === null || !Number.isFinite(target) || target <= 0) {
        return {
          date: point.log_date,
          percent: null,
          actual,
          target: null,
        };
      }

      return {
        date: point.log_date,
        percent: clampPercent(actual, target),
        actual,
        target,
      };
    });

    const validDays = daily.filter((day) => day.percent !== null);

    const percent = validDays.length
      ? Math.round(
          validDays.reduce((sum, day) => sum + day.percent, 0) /
            validDays.length
        )
      : null;

    return {
      percent,
      daily,
      daysCounted: validDays.length,
    };
  }

  function renderMetric(metricKey) {
    const card = $(`.metric-card[data-metric="${metricKey}"]`);
    if (!card) return null;

    const result = calcAdherence(state.points, metricKey);
    const percentElement = $(".metric-percent", card);
    const detailElement = $(".metric-detail", card);
    const countElement = $(".days-count", card);
    const fillElement = $(".metric-fill", card);
    const trendElement = $(".mini-trend", card);

    percentElement.textContent =
      result.percent === null ? "No data" : `${result.percent}%`;

    setStatus($(".metric-status", card), result.percent);

    if (metricKey === "medication") {
      detailElement.textContent = "Taken / scheduled";
    } else if (metricKey === "water") {
      detailElement.textContent =
        `Daily goal: ${TARGETS.water_ml.toLocaleString()} ml`;
    } else if (metricKey === "activity") {
      detailElement.textContent =
        `Daily goal: ${TARGETS.activity_min} minutes`;
    } else {
      detailElement.textContent = "Daily meal-plan target";
    }

    countElement.textContent = `${result.daysCounted} days counted`;

    fillElement.style.width =
      result.percent === null ? "0%" : `${result.percent}%`;
    fillElement.className =
      `metric-fill ${statusFor(result.percent).className}`;

    trendElement.classList.toggle("monthly", state.period === 30);
    trendElement.replaceChildren();

    result.daily.forEach((day) => {
      const bar = document.createElement("span");
      const status = statusFor(day.percent);

      bar.className = `trend-bar ${status.className}`;
      bar.style.height =
        day.percent === null
          ? "3px"
          : `${Math.max(5, day.percent * 0.72)}%`;

      bar.title =
        day.percent === null
          ? `${formatDate(day.date)}: No target/data`
          : `${formatDate(day.date)}: ${Math.round(day.percent)}%`;

      bar.setAttribute("aria-label", bar.title);
      trendElement.appendChild(bar);
    });

    return result.percent;
  }

  function renderNoDataCards() {
    Object.keys(METRICS).forEach((metricKey) => {
      const card = $(`.metric-card[data-metric="${metricKey}"]`);
      if (!card) return;

      $(".metric-percent", card).textContent = "No data";
      $(".metric-detail", card).textContent = "Unable to calculate.";
      $(".days-count", card).textContent = "0 days counted";

      $(".metric-fill", card).style.width = "0%";
      $(".metric-fill", card).className = "metric-fill no-data";

      setStatus($(".metric-status", card), null);

      const trend = $(".mini-trend", card);
      trend.replaceChildren();
    });
  }

  function render() {
    const loggedDays = state.points.filter(
      (point) => Number(point.entries) > 0
    ).length;

    const emptyMessage = $("#progress-empty");
    if (emptyMessage) emptyMessage.hidden = loggedDays !== 0;

    const percentages = Object.keys(METRICS).map(renderMetric);
    const available = percentages.filter((value) => value !== null);

    const overall = available.length
      ? Math.round(
          available.reduce((sum, value) => sum + value, 0) /
            available.length
        )
      : null;

    setText(
      "#overall-percent",
      overall === null ? "No data" : `${overall}%`
    );

    setText(
      "#overall-detail",
      overall === null
        ? "No categories have enough data to calculate adherence."
        : `${available.length} of 4 categories included`
    );

    setStatus($("#overall-status"), overall);
  }

  function selectPeriod(period) {
    state.period = Number(period) === 30 ? 30 : 7;

    $$(".period-btn").forEach((button) => {
      const active = Number(button.dataset.period) === state.period;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    loadData();
  }

  function init() {
    $$(".period-btn").forEach((button) => {
      button.addEventListener("click", () =>
        selectPeriod(button.dataset.period)
      );
    });

    const refreshButton = $("#refresh-progress");
    if (refreshButton) {
      refreshButton.addEventListener("click", loadData);
    }

    // Refresh when the page becomes visible again.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        loadData();
      }
    });

    const logoutButton = $("#logoutBtn");
    if (logoutButton) {
      logoutButton.addEventListener("click", () => {
        localStorage.removeItem("access_token");
      });
    }

    loadData();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
