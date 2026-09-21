const message = document.querySelector("#form-message");
document.querySelector("#register-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try { const result = await apiRequest("/auth/register", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); message.textContent = result.message; }
  catch (error) { message.textContent = error.message; }
});
document.querySelector("#login-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try { const result = await apiRequest("/auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); localStorage.setItem("dietrx_token", result.access_token); window.location.href = "dashboard.html"; }
  catch (error) { message.textContent = error.message; }
});
