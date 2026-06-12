const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".nav-links");

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
}

document.querySelectorAll("[data-ticket-link]").forEach((link) => {
  link.addEventListener("click", () => {
    const detail = {
      show: link.dataset.show,
      placement: link.dataset.placement,
      ticket_url: link.href,
    };
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ event: "ticket_click", ...detail });
    // GA4 (gtag.js) doesn't auto-read dataLayer events the way GTM does,
    // so fire the event explicitly when the analytics tag is present.
    if (typeof window.gtag === "function") {
      window.gtag("event", "ticket_click", detail);
    }
  });
});
