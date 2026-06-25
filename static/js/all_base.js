

function showNotif(duration = 5000) {
            const el = document.querySelector(".block-notif");

	    if(!el) return;

            requestAnimationFrame(() => {
                el.classList.add("show");
            });
            clearTimeout(el._timer);
            el._timer = setTimeout(() => {
                el.classList.remove("show");
            }, duration);
        }
        document.addEventListener("DOMContentLoaded", () => {
            showNotif();
        });


function viewPass(btn) {
    const input = document.getElementById(btn.dataset.input);
    const icon = btn.querySelector("i");
    if (input.type === "password") {
        input.type = "text";
        icon.classList.remove("fa-eye-slash");
        icon.classList.add("fa-eye");
    } else {
        input.type = "password";
        icon.classList.remove("fa-eye");
        icon.classList.add("fa-eye-slash");
    }
}

