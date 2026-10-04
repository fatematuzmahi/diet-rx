const message = document.querySelector("#form-message");
document.querySelector("#register-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try { const result = await apiRequest("/auth/register", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = result.message; window.location.href = "verify-email.html"; }
  catch (error) { message.textContent = error.message; }
});
document.querySelector("#verify-email-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try { const result = await apiRequest("/auth/verify-email", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = result.message; window.location.href = "login.html"; }
  catch (error) { message.textContent = error.message; }
});
document.querySelector("#login-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();

    try {
        const result = await apiRequest("/auth/login", {
            method: "POST",
            body: JSON.stringify(Object.fromEntries(new FormData(event.target)))
        });

        localStorage.setItem("session_token", result.session_token);
        localStorage.setItem("user", JSON.stringify(result.user));

        window.location.href = "dashboard.html";

    } catch (error) {
        alert(error.message);
    }
});
const logoutBtn = document.getElementById("logoutBtn");

if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
        try {
            await apiRequest("/auth/logout", {
                method: "POST"
            });
        } catch (error) {
            console.error("Logout API error:", error);
        }

        localStorage.removeItem("session_token");
        localStorage.removeItem("user");

        window.location.href = "login.html";
    });
}