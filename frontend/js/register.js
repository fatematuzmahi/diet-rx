const registerForm = document.getElementById("registerForm"); const registerMessage = document.getElementById("registerMessage");
registerForm.addEventListener("submit", function (event) { event.preventDefault();
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

registerMessage.textContent =
    "Registration information is ready to be submitted.";
registerMessage.style.color = "green";
});