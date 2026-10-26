
"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const statusMessage = document.getElementById("statusMessage");
    const usersTableBody = document.getElementById("usersTableBody");
    const usersSummary = document.getElementById("usersSummary");
    const pageInfo = document.getElementById("pageInfo");
    const previousPageButton = document.getElementById("previousPageButton");
    const nextPageButton = document.getElementById("nextPageButton");

    const PAGE_SIZE = 50;
    let currentPage = 1;
    let totalUsers = 0;
    let loadingUsers = false;

    function showMessage(message, isError = false) {
        if (!statusMessage) return;
        statusMessage.textContent = message;
        statusMessage.style.color = isError ? "#b42318" : "#176b55";
    }

    function showFormMessage(elementId, message, isError = false) {
        const element = document.getElementById(elementId);
        if (!element) return;
        element.textContent = message;
        element.style.color = isError ? "#b42318" : "#176b55";
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        })[character]);
    }

    function getErrorMessage(error) {
        return error?.message || "Something went wrong.";
    }

    function redirectToLogin() {
        showMessage("Admin access only. Please sign in with an admin account.", true);
        localStorage.removeItem("access_token");
        window.setTimeout(() => {
            window.location.href = "../index.html";
        }, 1200);
    }

    async function request(path, options = {}) {
        if (typeof apiRequest !== "function") {
            throw new Error("API helper apiRequest() was not found.");
        }

        try {
            return await apiRequest(path, options);
        } catch (error) {
            const message = getErrorMessage(error);

            if (/401|unauthorized/i.test(message)) {
                localStorage.removeItem("access_token");
                window.location.href = "../index.html";
            }

            if (/403|forbidden|admin access only/i.test(message)) {
                redirectToLogin();
            }

            throw error;
        }
    }

    function getUsersPayload(data) {
        if (Array.isArray(data?.items)) {
            return data.items;
        }
        if (Array.isArray(data?.users)) {
            return data.users;
        }
        return [];
    }

    async function loadUsers() {
        if (loadingUsers) return;
        loadingUsers = true;

        usersTableBody.innerHTML =
            '<tr><td colspan="5">Loading users...</td></tr>';

        const search = document.getElementById("userSearch")?.value.trim() || "";
        const role = document.getElementById("roleFilter")?.value || "";

        const params = new URLSearchParams({
            limit: String(PAGE_SIZE),
            offset: String((currentPage - 1) * PAGE_SIZE)
        });

        if (search) params.set("q", search);
        if (role) params.set("role", role);

        try {
            const data = await request(`/api/admin/users?${params.toString()}`);
            const users = getUsersPayload(data);

            totalUsers = Number(data?.total ?? users.length);

            if (users.length === 0) {
                usersTableBody.innerHTML =
                    '<tr><td colspan="5">No users found.</td></tr>';
            } else {
                usersTableBody.innerHTML = users.map((user) => `
                    <tr>
                        <td>${escapeHtml(user.user_id)}</td>
                        <td>${escapeHtml(user.name)}</td>
                        <td>${escapeHtml(user.email)}</td>
                        <td>${escapeHtml(user.role)}</td>
                        <td>${escapeHtml(user.created_at || "—")}</td>
                    </tr>
                `).join("");
            }

            const first = totalUsers === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
            const last = Math.min(currentPage * PAGE_SIZE, totalUsers);

            usersSummary.textContent =
                `Showing ${first}–${last} of ${totalUsers} users`;

            pageInfo.textContent = `Page ${currentPage}`;
            previousPageButton.disabled = currentPage <= 1;
            nextPageButton.disabled = currentPage * PAGE_SIZE >= totalUsers;

            showMessage("Users loaded.");
        } catch (error) {
            usersTableBody.innerHTML =
                '<tr><td colspan="5">Unable to load users.</td></tr>';
            usersSummary.textContent = "";
            showMessage(getErrorMessage(error), true);
        } finally {
            loadingUsers = false;
        }
    }

    document.getElementById("searchUsersButton")?.addEventListener("click", () => {
        currentPage = 1;
        loadUsers();
    });

    document.getElementById("userSearch")?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            currentPage = 1;
            loadUsers();
        }
    });

    document.getElementById("roleFilter")?.addEventListener("change", () => {
        currentPage = 1;
        loadUsers();
    });

    previousPageButton?.addEventListener("click", () => {
        if (currentPage > 1) {
            currentPage--;
            loadUsers();
        }
    });

    nextPageButton?.addEventListener("click", () => {
        if (currentPage * PAGE_SIZE < totalUsers) {
            currentPage++;
            loadUsers();
        }
    });

    document.querySelectorAll(".tab[data-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            const selectedTab = button.dataset.tab;

            document.querySelectorAll(".tab[data-tab]").forEach((tab) => {
                tab.classList.toggle("active", tab === button);
            });

            document.querySelectorAll(".panel").forEach((panel) => {
                panel.classList.toggle("hidden", panel.id !== selectedTab);
            });

            if (selectedTab === "users") loadUsers();
            if (selectedTab === "dish-dna") loadDishes();
            if (selectedTab === "food-prices") loadPrices();
        });
    });

    function getDishList(data) {
        return Array.isArray(data?.dishes) ? data.dishes : [];
    }

    async function loadDishes() {
        const message = document.getElementById("dishDnaMessage");
        showFormMessage("dishDnaMessage", "Loading Dish DNA...");

        try {
            const data = await request("/api/dish-dna?page=1&page_size=100");
            const dishes = getDishList(data);

            showFormMessage(
                "dishDnaMessage",
                `Loaded ${dishes.length} dishes. Enter a Dish ID to edit an existing dish, or leave it blank to create one.`
            );

            renderRecordList("dishDnaMessage", dishes, "dish_id", "name");
        } catch (error) {
            showFormMessage("dishDnaMessage", getErrorMessage(error), true);
        }
    }

    async function loadPrices() {
        showFormMessage("foodPriceMessage", "Loading food prices...");

        try {
            const data = await request("/api/prices");
            const prices = Array.isArray(data?.prices) ? data.prices : [];

            showFormMessage(
                "foodPriceMessage",
                `Loaded ${prices.length} prices. Enter a Price ID to edit an existing price, or leave it blank to create one.`
            );

            renderRecordList("foodPriceMessage", prices, "price_id", "item_name");
        } catch (error) {
            showFormMessage("foodPriceMessage", getErrorMessage(error), true);
        }
    }

    function renderRecordList(messageId, records, idKey, nameKey) {
        const message = document.getElementById(messageId);
        if (!message) return;

        let list = document.getElementById(`${messageId}List`);

        if (!list) {
            list = document.createElement("div");
            list.id = `${messageId}List`;
            list.className = "record-list";
            message.insertAdjacentElement("afterend", list);
        }

        if (!records.length) {
            list.textContent = "No records found.";
            return;
        }

        list.replaceChildren();

        const heading = document.createElement("h3");
        heading.textContent = "Existing records";
        list.appendChild(heading);

        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headRow = document.createElement("tr");

        [idKey === "dish_id" ? "Dish ID" : "Price ID", "Name"].forEach((text) => {
            const th = document.createElement("th");
            th.textContent = text;
            headRow.appendChild(th);
        });

        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");

        records.forEach((record) => {
            const row = document.createElement("tr");
            const idCell = document.createElement("td");
            const nameCell = document.createElement("td");

            idCell.textContent = record[idKey] ?? "";
            nameCell.textContent = record[nameKey] ?? "";

            row.append(idCell, nameCell);
            tbody.appendChild(row);
        });

        table.appendChild(tbody);
        list.appendChild(table);
    }

    function parseJsonPayload(text, label) {
        try {
            const parsed = JSON.parse(text);
            if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
                throw new Error("JSON must be an object.");
            }
            return parsed;
        } catch (error) {
            throw new Error(`${label}: ${error.message}`);
        }
    }

    document.getElementById("dishDnaForm")?.addEventListener("submit", async (event) => {
        event.preventDefault();

        try {
            const id = document.getElementById("dishDnaId").value.trim();
            const payload = parseJsonPayload(
                document.getElementById("dishDnaPayload").value,
                "Invalid Dish DNA JSON"
            );

            const path = id
                ? `/api/dish-dna/${encodeURIComponent(id)}`
                : "/api/dish-dna";

            const result = await request(path, {
                method: id ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });

            showFormMessage(
                "dishDnaMessage",
                id
                    ? `Dish DNA ${id} updated successfully.`
                    : `Dish DNA created successfully${result?.dish_id ? ` (ID ${result.dish_id})` : ""}.`
            );

            document.getElementById("dishDnaForm").reset();
            await loadDishes();
        } catch (error) {
            showFormMessage("dishDnaMessage", getErrorMessage(error), true);
        }
    });

    document.getElementById("foodPriceForm")?.addEventListener("submit", async (event) => {
        event.preventDefault();

        try {
            const id = document.getElementById("foodPriceId").value.trim();
            const payload = parseJsonPayload(
                document.getElementById("foodPricePayload").value,
                "Invalid food price JSON"
            );

            const path = id
                ? `/api/prices/${encodeURIComponent(id)}`
                : "/api/prices";

            const result = await request(path, {
                method: id ? "PUT" : "POST",
                body: JSON.stringify(payload)
            });

            showFormMessage(
                "foodPriceMessage",
                id
                    ? `Food price ${id} updated successfully.`
                    : `Food price created successfully${result?.price_id ? ` (ID ${result.price_id})` : ""}.`
            );

            document.getElementById("foodPriceForm").reset();
            await loadPrices();
        } catch (error) {
            showFormMessage("foodPriceMessage", getErrorMessage(error), true);
        }
    });

    document.getElementById("logoutButton")?.addEventListener("click", () => {
        localStorage.removeItem("access_token");
        window.location.href = "../index.html";
    });

    loadUsers();
});
