function goToView(btn) {
    document.getElementById("loader_user").style.display = "flex";
    const url = btn.dataset.url;
    if (url) {
        window.location.href = url;
    }
}
