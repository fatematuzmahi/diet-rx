document.querySelector("#safety-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const resultElement = document.querySelector("#safety-result");
  try { const result = await apiRequest("/safety/check", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.target))) }); resultElement.textContent = `${result.status.toUpperCase()}: ${result.message}`; }
  catch (error) { resultElement.textContent = error.message; }
});
