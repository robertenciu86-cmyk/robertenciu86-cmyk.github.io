const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".nav-links");

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
}

// Cookie consent. The inline GA snippet defaults analytics_storage to "denied"
// and replays a stored "granted" choice before init, so here we only handle the
// banner: show it when no choice exists, then record the visitor's decision.
const CONSENT_KEY = "lcg-analytics-consent";
const banner = document.getElementById("consent-banner");

if (banner) {
  let stored = null;
  try {
    stored = localStorage.getItem(CONSENT_KEY);
  } catch (e) {}

  if (stored !== "granted" && stored !== "denied") {
    banner.hidden = false;
  }

  banner.querySelectorAll("[data-consent]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const choice = btn.dataset.consent;
      try {
        localStorage.setItem(CONSENT_KEY, choice);
      } catch (e) {}
      if (choice === "granted" && typeof window.gtag === "function") {
        window.gtag("consent", "update", { analytics_storage: "granted" });
      }
      banner.hidden = true;
    });
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
