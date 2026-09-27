const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".nav-links");

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
}

// Analytics notice (opt-out). The inline GA snippet grants analytics_storage
// by default and replays a stored "denied" before init, so here we only handle
// the notice: show it once to new visitors, reopen it from any
// [data-cookie-settings] button, and record the visitor's choice.
const CONSENT_KEY = "lcg-analytics-consent";
const NOTICE_KEY = "lcg-analytics-notice";
const banner = document.getElementById("consent-banner");

const readStore = (key) => {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
};
const writeStore = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch (e) {}
};

// gtag stops writing cookies once consent is denied, but the ones it already
// set stay behind, so remove _ga and _ga_<id> on every domain they could use.
const deleteAnalyticsCookies = () => {
  const host = location.hostname;
  const domains = ["", host, "." + host, "." + host.split(".").slice(-2).join(".")];
  document.cookie.split(";").forEach((part) => {
    const name = part.split("=")[0].trim();
    if (name !== "_ga" && !name.startsWith("_ga_")) return;
    domains.forEach((domain) => {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/${domain ? `; domain=${domain}` : ""}`;
    });
  });
};

if (banner) {
  const optOut = banner.querySelector('[data-consent="denied"]');
  const accept = banner.querySelector('[data-consent="granted"]');

  const showNotice = () => {
    const optedOut = readStore(CONSENT_KEY) === "denied";
    optOut.textContent = optedOut ? "Keep it off" : "Opt out";
    accept.textContent = optedOut ? "Turn analytics on" : "OK";
    banner.hidden = false;
  };

  if (!readStore(NOTICE_KEY)) {
    showNotice();
  }

  document.querySelectorAll("[data-cookie-settings]").forEach((btn) => {
    btn.addEventListener("click", () => {
      showNotice();
      accept.focus();
    });
  });

  [optOut, accept].forEach((btn) => {
    btn.addEventListener("click", () => {
      const choice = btn.dataset.consent;
      writeStore(CONSENT_KEY, choice);
      writeStore(NOTICE_KEY, "seen");
      if (typeof window.gtag === "function") {
        window.gtag("consent", "update", { analytics_storage: choice });
      }
      if (choice === "denied") {
        deleteAnalyticsCookies();
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
