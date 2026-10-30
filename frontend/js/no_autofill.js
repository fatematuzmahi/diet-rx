/* SCRUM-69 (round 3): sign-in forms open empty.
   Browsers fill saved e-mails and passwords into a login form on load and on
   refresh. A field that is read-only is skipped by that autofill, so the fields
   start read-only and become editable the moment the user clicks or tabs in.
   The form is also cleared when the page is shown again (refresh or Back). */
(function () {
    "use strict";

    function unlock(event) {
        event.target.removeAttribute("readonly");
    }

    function prepare(form) {
        form.querySelectorAll("input[type=email], input[type=password], input[type=text]").forEach(function (input) {
            input.setAttribute("readonly", "");
            input.value = "";
            input.addEventListener("focus", unlock);
            input.addEventListener("pointerdown", unlock);
        });
    }

    function run() {
        document.querySelectorAll("form[data-no-autofill]").forEach(prepare);
    }

    window.addEventListener("pageshow", run);
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", run);
    } else {
        run();
    }
})();
