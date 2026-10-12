/* SCRUM-45: Patient Health Profile UI wiring.
 *
 * Uses the shared apiRequest() from api.js, which automatically attaches the
 * Bearer access_token (SCRUM-44). GET populates the form read-only; PUT saves
 * it. The patient is identified solely by the server from their token, so this
 * script never sends a patient id -- it just displays and submits field values.
 */
(function () {
    const form = document.getElementById("health-profile-form");
    const status = document.getElementById("hp-status");
    const saveBtn = document.getElementById("hp-save");
    const refreshBtn = document.getElementById("hp-refresh");

    // Field names the backend HealthProfileUpdate/HealthProfileResponse accept.
    // Base fields (age/gender/weight/height) -> PATIENT; the rest -> HEALTH_PROFILE.
    const FIELDS = [
        "age",
        "gender",
        "weight",
        "height",
        "conditions",
        "allergies",
        "activity_level",
        "food_preference",
        "goals",
        "physical_limitations",
    ];

    function setStatus(message, kind) {
        if (!status) return;
        status.textContent = message || "";
        status.classList.remove("is-success", "is-error", "is-loading");
        if (kind) status.classList.add("is-" + kind);
    }

    function setBusy(busy) {
        if (saveBtn) saveBtn.disabled = busy;
        if (refreshBtn) refreshBtn.disabled = busy;
    }

    function fillForm(profile) {
        FIELDS.forEach((field) => {
            const input = form.elements[field];
            if (!input) return;
            const value = profile ? profile[field] : null;
            input.value = value === null || value === undefined ? "" : value;
        });
    }

    function collectPayload() {
        const payload = {};
        FIELDS.forEach((field) => {
            const input = form.elements[field];
            if (!input) return;
            let value = input.value;
            if (typeof value === "string") value = value.trim();

            if (field === "age") {
                payload.age = value === "" ? null : Number(value);
            } else if (field === "weight" || field === "height") {
                payload[field] = value === "" ? null : Number(value);
            } else {
                // Empty text/select clears the field on the server (nullable).
                payload[field] = value === "" ? null : value;
            }
        });
        return payload;
    }

    async function loadProfile() {
        setBusy(true);
        setStatus("Loading your health profile…", "loading");
        try {
            const profile = await apiRequest("/api/health-profile/me", { method: "GET" });
            fillForm(profile);
            if (profile && profile.profile_exists === false) {
                setStatus("No saved profile yet — fill in your details and save.", "loading");
            } else {
                setStatus("Health profile loaded.", "success");
            }
        } catch (error) {
            // 401/403 bubble up from apiRequest; surface the server message.
            setStatus(error.message || "Could not load your health profile.", "error");
        } finally {
            setBusy(false);
        }
    }

    async function saveProfile(event) {
        event.preventDefault();
        setBusy(true);
        setStatus("Saving your health profile…", "loading");
        try {
            const saved = await apiRequest("/api/health-profile/me", {
                method: "PUT",
                body: JSON.stringify(collectPayload()),
            });
            fillForm(saved);
            setStatus("Health profile saved successfully.", "success");
        } catch (error) {
            setStatus(error.message || "Could not save your health profile.", "error");
        } finally {
            setBusy(false);
        }
    }

    if (form) form.addEventListener("submit", saveProfile);
    if (refreshBtn) refreshBtn.addEventListener("click", loadProfile);

    // Load on first view so the form reflects any saved data.
    document.addEventListener("DOMContentLoaded", loadProfile);
    if (document.readyState !== "loading") loadProfile();
})();
