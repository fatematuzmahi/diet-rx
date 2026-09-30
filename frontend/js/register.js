const registerForm = document.getElementById("registerForm"); const registerMessage = document.getElementById("registerMessage");
registerForm.addEventListener("submit", async function (event) {event.preventDefault();
const name = document.getElementById("name").value.trim();
const email = document.getElementById("email").value.trim();
const password = document.getElementById("password").value;
const confirmPassword = document.getElementById("confirmPassword").value;

registerMessage.textContent = "";

if (password !== confirmPassword) {
    registerMessage.textContent = "Passwords do not match.";
    registerMessage.style.color = "red";
    return;
}

const registrationData = {
    name: name,
    email: email,
    password: password
};

console.log("Registration data ready:", registrationData);
const response = await fetch("http://127.0.0.1:8000/auth/register", {
    method: "POST",
    headers: {
        "Content-Type": "application/json"
    },
    body: JSON.stringify(registrationData)
});

const result = await response.json();

if (!response.ok) {
    registerMessage.textContent = result.detail || "Registration failed.";
    registerMessage.style.color = "red";
    return;
}

registerMessage.textContent = result.message;
registerMessage.style.color = "green";
});