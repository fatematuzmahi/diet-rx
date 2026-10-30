/* SCRUM-69 (round 3): behaviour for the shared patient menu.
   - only one menu group is open at a time
   - a click outside or the Escape key closes it
   - every Logout link clears the stored sign-in and returns to the login page */
(function () {
    "use strict";

    function groups() {
        return Array.prototype.slice.call(document.querySelectorAll(".app-nav .nav-group"));
    }

    function closeAll(except) {
        groups().forEach(function (group) {
            if (group !== except) group.removeAttribute("open");
        });
    }

    function init() {
        groups().forEach(function (group) {
            group.addEventListener("toggle", function () {
                if (group.open) closeAll(group);
            });
        });

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".app-nav .nav-group")) closeAll(null);
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") closeAll(null);
        });

        document.querySelectorAll("[data-nav-logout]").forEach(function (link) {
            link.addEventListener("click", function (event) {
                event.preventDefault();
                ["access_token", "session_token", "user", "dietrx_user"].forEach(function (key) {
                    localStorage.removeItem(key);
                });
                window.location.replace("login.html");
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
