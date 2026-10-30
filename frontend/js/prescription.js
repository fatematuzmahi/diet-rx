/* SCRUM-46: Patient Prescription Management UI wiring.
 *
 * Uses the shared apiRequest() from api.js, which attaches the Bearer
 * access_token (SCRUM-44). The owner is resolved by the server from the token,
 * so this script NEVER sends a patient_id or doctor_id. A patient may add /
 * view / edit only their own self-submitted prescriptions; doctor-issued
 * prescriptions are not returned by these endpoints.
 */
(function () {
    "use strict";

    const listEl = document.getElementById("rx-list");
    const statusEl = document.getElementById("rx-status");
    const addForm = document.getElementById("rx-add-form");
    const addBtn = document.getElementById("rx-add-btn");
    const refreshBtn = document.getElementById("rx-refresh");

    const STATUTES = ["ACTIVE", "COMPLETED", "CANCELLED"];

    function setStatus(message, kind) {
        if (!statusEl) return;
        statusEl.textContent = message || "";
        statusEl.classList.remove("is-success", "is-error", "is-loading");
        if (kind) statusEl.classList.add("is-" + kind);
    }

    function setBusy(busy) {
        if (addBtn) addBtn.disabled = busy;
        if (refreshBtn) refreshBtn.disabled = busy;
        document.querySelectorAll("[data-rx-edit-btn]").forEach((b) => (b.disabled = busy));
    }

    function statusBadge(status) {
        const s = (status || "ACTIVE").toLowerCase();
        return '<span class="rx-badge status-' + s + '">' + escapeHtml(status || "ACTIVE") + "</span>";
    }

    function validatedBadge(validated) {
        return validated
            ? '<span class="rx-badge validated-yes">Validated</span>'
            : '<span class="rx-badge validated-no">Unvalidated</span>';
    }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function formatDate(value) {
        if (!value) return "";
        const d = new Date(value);
        return isNaN(d.getTime()) ? "" : d.toLocaleString();
    }


    async function loadList() {
        setStatus("Loading your prescriptions…", "loading");
        try {
            const data = await apiRequest("/api/prescriptions/me", { method: "GET" });
            const items = data.prescriptions || [];
            renderList(items);
            setStatus(items.length ? "Loaded " + items.length + " prescription" + (items.length === 1 ? "" : "s") + "." : "", "success");
        } catch (error) {
            setStatus(error.message || "Could not load your prescriptions.", "error");
        }
    }

    function setCount(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value);
    }

    function renderCounts(items) {
        setCount("rx-count-total", items.length);
        setCount("rx-count-active", items.filter((i) => (i.status || "ACTIVE") === "ACTIVE").length);
        setCount("rx-count-pending", items.filter((i) => !i.validated).length);
    }

    function renderList(items) {
        renderCounts(items);
        if (!listEl) return;
        if (!items.length) {
            listEl.innerHTML = '<li class="rx-empty-state"><strong>No prescriptions yet</strong><span>Add your first one with the form.</span></li>';
            return;
        }
        listEl.innerHTML = "";
        items.forEach((item) => listEl.appendChild(renderItem(item)));
    }

    function renderItem(item) {
        const li = document.createElement("li");
        li.className = "rx-item";
        li.dataset.id = item.prescription_id;

        const restrictions = item.restrictions ? escapeHtml(item.restrictions) : '<em class="rx-empty">No restrictions recorded.</em>';
        const fileBit = item.has_file
            ? '<button type="button" class="rx-link" data-rx-download="' + item.prescription_id + '">Download file' +
              (item.file_name ? " (" + escapeHtml(item.file_name) + ")" : "") + "</button>"
            : "";

        li.innerHTML =
            '<div class="rx-item-top">' +
                '<span class="rx-item-title">Prescription #' + escapeHtml(item.prescription_id) + "</span>" +
                '<span class="rx-badges">' + statusBadge(item.status) + validatedBadge(item.validated) + "</span>" +
            "</div>" +
            '<p class="rx-item-restrictions">' + restrictions + "</p>" +
            '<div class="rx-item-meta">' +
                "<span>Uploaded: " + escapeHtml(formatDate(item.upload_date)) + "</span>" +
                (item.updated_at ? "<span>Updated: " + escapeHtml(formatDate(item.updated_at)) + "</span>" : "") +
            "</div>" +
            '<div class="rx-item-actions">' +
                '<button type="button" class="rx-link" data-rx-edit-btn="' + item.prescription_id + '">Edit</button>' +
                fileBit +
            "</div>";

        li.querySelector("[data-rx-edit-btn]").addEventListener("click", () => openEdit(li, item));
        const dl = li.querySelector("[data-rx-download]");
        if (dl) dl.addEventListener("click", () => downloadFile(item.prescription_id, item.file_name));
        return li;
    }

    function openEdit(li, item) {
        if (li.querySelector(".rx-edit")) return; // already editing
        const wrap = document.createElement("div");
        wrap.className = "rx-edit";
        const options = STATUTES.map((s) =>
            '<option value="' + s + '"' + (s === item.status ? " selected" : "") + ">" + s + "</option>"
        ).join("");
        wrap.innerHTML =
            '<form class="rx-form" novalidate>' +
                '<div class="rx-grid">' +
                    '<div class="rx-field rx-field-wide">' +
                        "<label>Restrictions / notes</label>" +
                        '<textarea name="restrictions" rows="3" maxlength="4000">' + escapeHtml(item.restrictions || "") + "</textarea>" +
                    "</div>" +
                    '<div class="rx-field">' +
                        "<label>Status</label>" +
                        '<select name="status">' + options + "</select>" +
                    "</div>" +
                "</div>" +
                '<div class="rx-actions">' +
                    '<button type="submit" class="rx-btn">Save changes</button>' +
                    '<button type="button" class="rx-btn rx-btn-ghost" data-rx-cancel>Cancel</button>' +
                "</div>" +
            "</form>";
        li.appendChild(wrap);
        wrap.querySelector("[data-rx-cancel]").addEventListener("click", () => wrap.remove());
        wrap.querySelector("form").addEventListener("submit", (event) => submitEdit(event, item.prescription_id));
    }

    async function submitEdit(event, id) {
        event.preventDefault();
        const form = event.target;
        const restrictions = form.elements.restrictions.value.trim();
        const statusValue = form.elements.status.value;
        try {
            await apiRequest("/api/prescriptions/" + id, {
                method: "PUT",
                body: JSON.stringify({
                    restrictions: restrictions === "" ? null : restrictions,
                    status: statusValue,
                }),
            });
            setStatus("Prescription updated.", "success");
            await loadList();
        } catch (error) {
            setStatus(error.message || "Could not update the prescription.", "error");
        }
    }

    async function downloadFile(id, fileName) {
        setStatus("Preparing download…", "loading");
        try {
            const token = localStorage.getItem("access_token");
            const res = await fetch(
                API_BASE_URL + "/api/prescriptions/" + id + "/file",
                { headers: token ? { Authorization: "Bearer " + token } : {} }
            );
            if (!res.ok) throw new Error("Could not download the file.");
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = fileName || "prescription";
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            setStatus("Download started.", "success");
        } catch (error) {
            setStatus(error.message || "Could not download the file.", "error");
        }
    }

    async function submitAdd(event) {
        event.preventDefault();
        if (addBtn) addBtn.disabled = true;
        setStatus("Saving your prescription…", "loading");
        // Multipart so the optional attachment is validated + stored securely
        // server-side. The server derives owner/source from the token.
        const formData = new FormData(addForm);
        try {
            await apiRequest("/api/prescriptions/me", { method: "POST", body: formData });
            addForm.reset();
            setStatus("Prescription added and awaiting validation.", "success");
            await loadList();
        } catch (error) {
            setStatus(error.message || "Could not save the prescription.", "error");
        } finally {
            if (addBtn) addBtn.disabled = false;
        }
    }

    const fileInput = document.getElementById("rx-file");
    const fileName = document.getElementById("rx-file-name");
    const fileHint = fileName ? fileName.textContent : "";
    function showFileName() {
        if (!fileName || !fileInput) return;
        fileName.textContent = fileInput.files && fileInput.files[0] ? fileInput.files[0].name : fileHint;
    }
    if (fileInput) fileInput.addEventListener("change", showFileName);
    if (addForm) addForm.addEventListener("reset", () => setTimeout(showFileName, 0));

    if (addForm) addForm.addEventListener("submit", submitAdd);
    if (refreshBtn) refreshBtn.addEventListener("click", loadList);

    document.addEventListener("DOMContentLoaded", loadList);
    if (document.readyState !== "loading") loadList();
})();

