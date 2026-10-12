const message = document.querySelector("#form-message");
document.querySelector("#register-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = Object.fromEntries(new FormData(event.target));
  if (formData.password !== formData.confirm_password) {
    message.textContent = "Passwords do not match.";
    return;
  }
  try { const result = await apiRequest("/auth/register", { method: "POST", body: JSON.stringify(formData) }); message.textContent = result.message; window.location.href = "verify-email.html"; }
  catch (error) { message.textContent = error.message; }
});
document.querySelector("#send-code-button")?.addEventListener("click", async () => {
  const emailInput = document.querySelector("#verify-email-form input[name='email']");
  const sendMessage = document.querySelector("#send-code-message");
  if (!emailInput || !sendMessage) return;
  const email = emailInput.value.trim();
  if (!email) { sendMessage.textContent = "Please enter your email address."; sendMessage.classList.add("error"); return; }
  sendMessage.classList.remove("error");
  try { const result = await apiRequest("/auth/resend-verification-code", { method: "POST", body: JSON.stringify({ email }) }); sendMessage.textContent = result.message || "Verification code sent to your email."; }
  catch (error) { sendMessage.textContent = error.message; sendMessage.classList.add("error"); }
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
