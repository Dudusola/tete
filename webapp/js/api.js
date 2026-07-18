/* api.js - the glue between the web app and your FastAPI backend.
   Every screen imports this. Change API_BASE in one place if the port/host changes. */

const API_BASE = "http://localhost:8080";

/* ── Theme (dark mode) ────────────────────────────────────────────────
   The Tailwind config bakes fixed light hex values into the token
   utilities, so we remap the semantic tokens to a Material-3 dark palette
   under html.dark. One injection here themes every page that loads api.js. */
(function themeSystem() {
    const DARK_CSS = `
      html.dark body, html.dark .bg-background { background-color:#111416 !important; color:#e1e3e5; }
      html.dark .bg-surface { background-color:#111416 !important; }
      html.dark .bg-surface-container-lowest, html.dark .bg-surface-container-lowest\\/90,
      html.dark .bg-surface-container-lowest\\/80 { background-color:#0c0f10 !important; }
      html.dark .bg-surface-container-low { background-color:#191c1e !important; }
      html.dark .bg-surface-container { background-color:#1d2022 !important; }
      html.dark .bg-surface-container-high { background-color:#272a2c !important; }
      html.dark .bg-surface-container-highest { background-color:#323537 !important; }
      html.dark .bg-surface-variant { background-color:#42474a !important; }
      html.dark .bg-surface-bright { background-color:#37393b !important; }
      html.dark .bg-surface-tint\\/30 { background-color:rgba(190,198,224,0.25) !important; }
      html.dark .bg-error-container { background-color:#93000a !important; }
      html.dark .text-primary { color:#ffffff !important; }
      html.dark .text-on-surface { color:#e1e3e5 !important; }
      html.dark .text-on-background { color:#e1e3e5 !important; }
      html.dark .text-on-surface-variant { color:#c1c7ce !important; }
      html.dark .text-inverse-primary { color:#bec6e0 !important; }
      html.dark .text-secondary { color:#91db2a !important; }
      html.dark .hover\\:text-secondary:hover { color:#91db2a !important; }
      html.dark .text-error { color:#ffb4ab !important; }
      html.dark .text-on-error-container { color:#ffdad6 !important; }
      html.dark .text-on-primary-container { color:#aab2c5 !important; }
      html.dark .text-on-tertiary-container { color:#98a5b8 !important; }
      html.dark .border-outline-variant { border-color:#42474a !important; }
      html.dark .border-outline { border-color:#8c9198 !important; }
      html.dark .border-secondary { border-color:#91db2a !important; }
      html.dark .border-l-secondary { border-left-color:#91db2a !important; }
      html.dark .divide-surface-container-high > * { border-color:#272a2c !important; }
      html.dark .glass-card { background:rgba(29,32,34,0.85) !important; border-color:#42474a !important; }
      html.dark input, html.dark select, html.dark textarea { background-color:#1d2022 !important; color:#e1e3e5 !important; border-color:#42474a !important; }
      .theme-toggle { width:36px; height:36px; border-radius:9999px; display:flex; align-items:center;
        justify-content:center; background:transparent; border:1px solid #c6c6cd; color:#45464d;
        cursor:pointer; margin-left:12px; flex:none; padding:0; }
      html.dark .theme-toggle { border-color:#42474a; color:#c1c7ce; }
      .theme-toggle .material-symbols-outlined { font-size:18px; }
      /* Mobile copy lives inside the "More" sheet; falls back to floating if no bottom nav */
      #theme-toggle-mobile { margin:0; }
      #theme-toggle-mobile.floating { position:fixed; right:16px; bottom:84px; z-index:40;
        background:#f7f9fb; box-shadow:0 2px 8px rgba(0,0,0,0.18); }
      html.dark #theme-toggle-mobile.floating { background:#1d2022; }
      @media (min-width:768px) { #theme-toggle-mobile, #pp-sheet, #pp-backdrop { display:none !important; } }
      /* "More" bottom sheet (mobile) */
      #pp-backdrop { position:fixed; inset:0; z-index:55; background:rgba(0,0,0,0.4); }
      #pp-sheet { position:fixed; left:0; right:0; bottom:0; z-index:56; border-radius:16px 16px 0 0;
        background:#ffffff; box-shadow:0 -6px 24px rgba(0,0,0,0.2); padding:8px 16px 88px; }
      html.dark #pp-sheet { background:#191c1e; }
      #pp-sheet .pp-row { display:flex; align-items:center; gap:12px; width:100%; padding:14px 8px;
        border-radius:12px; color:inherit; text-decoration:none; font:inherit; background:none;
        border:none; cursor:pointer; }
      #pp-sheet .pp-row.active { background:#acf847; color:#457000; }
      html.dark #pp-sheet .pp-row.active { background:#354a12; color:#acf847; }
      #pp-sheet .pp-grab { width:36px; height:4px; border-radius:2px; background:#c6c6cd; margin:6px auto 10px; }
      html.dark #pp-sheet .pp-grab { background:#42474a; }
      /* Shared skeleton + toast */
      .pp-skel { border-radius:12px; background:#e6e8ea; animation:pp-pulse 1.4s ease-in-out infinite; }
      html.dark .pp-skel { background:#272a2c; }
      @keyframes pp-pulse { 0%,100% { opacity:1; } 50% { opacity:0.45; } }
      #pp-toast { position:fixed; left:50%; transform:translateX(-50%); bottom:92px; z-index:80;
        background:#2d3133; color:#eff1f3; padding:10px 18px; border-radius:9999px;
        font-size:14px; box-shadow:0 4px 16px rgba(0,0,0,0.3); opacity:0; transition:opacity .25s;
        pointer-events:none; max-width:85vw; text-align:center; }
      #pp-toast.show { opacity:1; }
      @media (min-width:768px) { #pp-toast { bottom:24px; } }`;
    const style = document.createElement("style");
    style.textContent = DARK_CSS;
    document.head.appendChild(style);

    const saved = localStorage.getItem("pp-theme");
    if (saved === "dark") document.documentElement.classList.add("dark");

    // Keep the phone status bar / PWA titlebar color in sync with the theme
    function syncThemeColor() {
        const meta = document.getElementById("meta-theme-color");
        if (meta) meta.content = document.documentElement.classList.contains("dark") ? "#0c0f10" : "#f7f9fb";
    }
    document.addEventListener("DOMContentLoaded", syncThemeColor);

    window.toggleTheme = function () {
        const dark = document.documentElement.classList.toggle("dark");
        localStorage.setItem("pp-theme", dark ? "dark" : "light");
        document.querySelectorAll(".theme-toggle .material-symbols-outlined")
            .forEach(icon => icon.textContent = dark ? "light_mode" : "dark_mode");
        syncThemeColor();
    };

    document.addEventListener("DOMContentLoaded", function () {
        const isDark = document.documentElement.classList.contains("dark");
        function makeBtn(id) {
            const btn = document.createElement("button");
            btn.id = id;
            btn.className = "theme-toggle";
            btn.title = "Toggle dark mode";
            btn.innerHTML = `<span class="material-symbols-outlined">${isDark ? "light_mode" : "dark_mode"}</span>`;
            btn.addEventListener("click", window.toggleTheme);
            return btn;
        }
        // Desktop: in-flow at the end of the top nav's link row, so it can never
        // overlap content. That nav is display:none on mobile, so a second copy
        // floats above the bottom nav there (hidden again at md+ by the CSS).
        const topNav = document.querySelector("nav[class*='md:flex']");
        const linkRow = topNav && topNav.lastElementChild;
        if (linkRow) {
            linkRow.style.display = "flex";
            linkRow.style.alignItems = "center";
            linkRow.appendChild(makeBtn("theme-toggle"));
        }
        // ── Brand → home: the logo + name on every page links back to Picks ──
        document.querySelectorAll("img[alt='PotentialPro']").forEach(img => {
            const row = img.parentElement;
            if (!row || row.closest("a")) return;
            const link = document.createElement("a");
            link.href = "index.html";
            link.className = row.className;
            row.replaceWith(link);
            while (row.firstChild) link.appendChild(row.firstChild);
        });

        // ── Mobile nav declutter: Consensus + Checker + theme toggle move into
        //    a "More" bottom sheet behind a hamburger (4 items instead of 6) ──
        const bottomNav = document.querySelector("nav[class*='md:hidden']");
        const mobileBtn = makeBtn("theme-toggle-mobile");
        if (!bottomNav) {
            mobileBtn.classList.add("floating");
            document.body.appendChild(mobileBtn);
            return;
        }
        const here = location.pathname.split("/").pop() || "index.html";
        const extras = [...bottomNav.querySelectorAll("a")]
            .filter(a => /consensus\.html|record\.html/.test(a.getAttribute("href")));
        const onExtraPage = /consensus\.html|record\.html/.test(here);

        const backdrop = document.createElement("div");
        backdrop.id = "pp-backdrop";
        const sheet = document.createElement("div");
        sheet.id = "pp-sheet";
        sheet.innerHTML = `<div class="pp-grab"></div>`;
        extras.forEach(a => {
            const href = a.getAttribute("href");
            const icon = a.querySelector(".material-symbols-outlined");
            const label = a.querySelector(".text-label-sm");
            const row = document.createElement("a");
            row.href = href;
            row.className = "pp-row text-body-md" + (here === href ? " active" : "");
            row.innerHTML = `<span class="material-symbols-outlined">${icon ? icon.textContent : "circle"}</span>
              <span>${label ? label.textContent : href}</span>`;
            sheet.appendChild(row);
            a.remove();
        });
        const themeRow = document.createElement("div");
        themeRow.className = "pp-row";
        themeRow.style.justifyContent = "space-between";
        themeRow.innerHTML = `<span style="display:flex;align-items:center;gap:12px;">
          <span class="material-symbols-outlined">contrast</span><span>Theme</span></span>`;
        themeRow.appendChild(mobileBtn);
        sheet.appendChild(themeRow);

        function setSheet(open) {
            backdrop.style.display = open ? "block" : "none";
            sheet.style.display = open ? "block" : "none";
        }
        setSheet(false);
        backdrop.addEventListener("click", () => setSheet(false));
        sheet.addEventListener("click", e => { if (e.target.closest("a.pp-row")) setSheet(false); });
        document.body.append(backdrop, sheet);

        const moreBtn = document.createElement("button");
        moreBtn.className = "flex flex-col items-center justify-center p-2 min-w-[64px] " +
            (onExtraPage ? "bg-secondary-container text-on-secondary-container rounded-xl" : "text-on-surface-variant");
        moreBtn.innerHTML = `<span class="material-symbols-outlined mb-1">menu</span><span class="text-label-sm">More</span>`;
        moreBtn.addEventListener("click", () => setSheet(sheet.style.display !== "block"));
        bottomNav.appendChild(moreBtn);
    });
})();

/* PWA: register the service worker (no-op on browsers without support) */
if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

/* Shared display helper: turn scraped league slugs like
   "world-cup-2026-last-" into "World Cup 2026 Last". Pages that render
   in ALL CAPS keep doing so via their `uppercase` CSS class. */
/* Forebet's short league codes -> human names. Codes not listed fall through
   to the slug prettifier below. */
const LEAGUE_CODES = {
    Us1: "MLS", Us2: "USL Championship", Us4: "USL League Two",
    Mx1: "Liga MX", Br1: "Brazil Serie A", Br2: "Brazil Serie B",
    Br3: "Brazil Serie C", Br4: "Brazil Serie D", BrC: "Copa do Brasil",
    BrM: "Brazil Mineiro", BrP: "Brazil Paulista", BrB: "Brazil Baiano",
    BrW: "Brazil Women", Ar1: "Argentina Primera", Ar2: "Argentina Nacional B",
    Ar3: "Argentina Metro B", ArR: "Argentina Regional", ArC: "Copa Argentina",
    No1: "Eliteserien", No2: "Norway OBOS", Se1: "Allsvenskan",
    Se2: "Superettan", Fi1: "Veikkausliiga", Fi2: "Finland Ykkonen",
    Fi3: "Finland Kakkonen", Dk1: "Danish Superliga", Ir1: "Ireland Premier",
    Ir2: "Ireland First Div", Is1: "Iceland Besta", Cn1: "Chinese Super League",
    Cn2: "China League One", Cn3: "China League Two", Jp1: "J1 League",
    Jp2: "J2 League", Kr1: "K League 1", KrC: "Korea Cup",
    In3: "India I-League", Ec1: "Ecuador LigaPro", Co1: "Colombia Primera",
    Ur1: "Uruguay Primera", Pa1: "Paraguay Primera", Ch1: "Chile Primera",
    Pe1: "Peru Liga 1", Bo1: "Bolivia Primera", Uz1: "Uzbekistan Super",
    Kz1: "Kazakhstan Premier", Kz2: "Kazakhstan First", Ru1: "Russian Premier",
    RuY: "Russia Youth", So1: "Somalia League", Mz1: "Mozambique",
    Au1: "A-League", AuQ: "Australia Queensland", AuW: "Australia West",
    AuV: "Australia Victoria", AuN: "Australia NSW", AuS: "Australia South",
    AuT: "Australia Tasmania", Sc1: "Scottish Premiership", ScC: "Scottish Cup",
    Nz2: "New Zealand", UCL: "Champions League", ECL: "Conference League",
};

function prettyLeague(s) {
    if (LEAGUE_CODES[s]) return LEAGUE_CODES[s];
    const cleaned = (s || "").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
    if (!cleaned) return "Football";
    return cleaned.replace(/\b[a-z]/g, c => c.toUpperCase());
}

/* ── Kickoff time helpers ─────────────────────────────────────────────
   match_date is "YYYY-MM-DD", kickoff_time is "HH:MM" (may be missing). */
function kickoffDate(matchDate, kickoffTime) {
    if (!matchDate) return null;
    const t = /^\d{1,2}:\d{2}/.test(kickoffTime || "") ? kickoffTime : "00:00";
    const d = new Date(`${matchDate}T${t.padStart(5, "0")}`);
    return isNaN(d) ? null : d;
}

function kickoffLabel(matchDate, kickoffTime) {
    const d = kickoffDate(matchDate, kickoffTime);
    if (!d) return matchDate || "";
    const now = new Date();
    const day = new Date(d); day.setHours(0, 0, 0, 0);
    const today = new Date(now); today.setHours(0, 0, 0, 0);
    const diff = Math.round((day - today) / 86400000);
    const time = /^\d{1,2}:\d{2}/.test(kickoffTime || "") ? " " + kickoffTime : "";
    if (diff === 0) return "Today" + time;
    if (diff === 1) return "Tomorrow" + time;
    if (diff === -1) return "Yesterday" + time;
    return d.toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"}) + time;
}

function hasKickedOff(matchDate, kickoffTime) {
    if (!/^\d{1,2}:\d{2}/.test(kickoffTime || "")) return false;
    const d = kickoffDate(matchDate, kickoffTime);
    return d ? d < new Date() : false;
}

/* "x min ago" for SQLite UTC timestamps like "2026-07-17 06:05:12" */
function timeAgo(utcStr) {
    if (!utcStr) return "";
    const d = new Date(utcStr.replace(" ", "T") + "Z");
    if (isNaN(d)) return "";
    const mins = Math.max(0, Math.round((Date.now() - d) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
}

function showToast(msg) {
    let t = document.getElementById("pp-toast");
    if (!t) {
        t = document.createElement("div");
        t.id = "pp-toast";
        document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._hide);
    t._hide = setTimeout(() => t.classList.remove("show"), 2200);
}

function ppSkeleton(heightPx) {
    return `<div class="pp-skel" style="height:${heightPx}px"></div>`;
}

async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`API ${path} returned ${res.status}`);
    return res.json();
}

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API ${path} returned ${res.status}`);
    return res.json();
}

const PotentialProAPI = {
    picks:       ()          => apiGet("/api/picks"),
    allPicks:    ()          => apiGet("/api/picks/all"),
    slip:        (risk, n, leagues, excludePairs, pinnedLegs) => apiGet(`/api/slip?risk=${risk}&games=${n}` +
                     (leagues && leagues.length ? `&leagues=${encodeURIComponent(leagues.join(","))}` : "") +
                     (excludePairs && excludePairs.length
                        ? `&exclude=${encodeURIComponent(excludePairs.map(p => `${p.home}||${p.away}`).join(";;"))}`
                        : "") +
                     (pinnedLegs && pinnedLegs.length
                        ? `&pin=${encodeURIComponent(pinnedLegs.map(p => `${p.home_team}||${p.away_team}||${p.market}||${p.selection}`).join(";;"))}`
                        : "")),
    leagues:     ()          => apiGet("/api/leagues"),
    consensus:   ()          => apiGet("/api/consensus"),
    stats:       ()          => apiGet("/api/stats"),
    statsTiers:  ()          => apiGet("/api/stats/tiers"),
    scoredPicks: (tier)      => apiGet(`/api/stats/picks${tier ? `?tier=${tier}` : ""}`),
    fixtures:    ()          => apiGet("/api/fixtures"),
    checkSlip:   (legs)      => apiPost("/api/slip/check", {legs}),
    decodeCode:  (provider, code) => apiPost("/api/booking/decode", {provider, code}),
    generateCode: (legs, provider) => apiPost("/api/booking/generate", {legs, provider: provider || "sportybet"}),
};
/* ── Motion / UI-consistency layer (July 2026) ────────────────────────────
   One injected stylesheet + tiny helpers so all five pages share the same
   feel: content *arrives* (fade-up, staggered) instead of popping in, cards
   lift subtly on hover, buttons press consistently. Applies automatically to
   static sections on load AND to content pages inject later (slip results,
   pick cards) via a MutationObserver — no per-page code needed.
   Respects prefers-reduced-motion. Keep this in api.js so the whole app
   stays consistent from one place. */
(function () {
    const MOTION_CSS = `
      @media (prefers-reduced-motion: no-preference) {
        .pp-enter { opacity:0; transform:translateY(10px); }
        .pp-enter.pp-in { opacity:1; transform:none;
          transition:opacity .45s cubic-bezier(.2,.7,.3,1), transform .45s cubic-bezier(.2,.7,.3,1); }
        main .rounded-xl.border { transition:box-shadow .25s ease, transform .25s ease; }
        main .rounded-xl.border:hover { transform:translateY(-2px);
          box-shadow:0 6px 20px rgba(0,0,0,0.08); }
        html.dark main .rounded-xl.border:hover { box-shadow:0 6px 20px rgba(0,0,0,0.45); }
        button { transition:transform .12s ease, background-color .2s ease,
                 color .2s ease, box-shadow .2s ease, border-color .2s ease; }
        button:active { transform:scale(.97); }
      }
      button:focus-visible, a:focus-visible {
        outline:2px solid #91db2a; outline-offset:2px; border-radius:6px; }
      /* Density: the config pages had 32px between every section — tighten
         the vertical rhythm so screens feel composed, not sparse. */
      main.space-y-xl > * + * { margin-top:20px !important; }
      main section > h2.text-headline-md { font-size:18px; line-height:24px; margin-bottom:10px; }`;
    const s = document.createElement("style");
    s.textContent = MOTION_CSS;
    document.head.appendChild(s);

    function enter(el, delay) {
        if (el._ppEntered) return;
        el._ppEntered = true;
        el.classList.add("pp-enter");
        requestAnimationFrame(() => requestAnimationFrame(() =>
            setTimeout(() => el.classList.add("pp-in"), delay || 0)));
    }

    document.addEventListener("DOMContentLoaded", () => {
        const main = document.querySelector("main");
        if (!main) return;
        [...main.children].forEach((el, i) => enter(el, i * 70));
        // Content injected later (slips, pick lists, stats) enters the same way
        new MutationObserver(muts => {
            for (const m of muts) {
                for (const n of m.addedNodes) {
                    if (n.nodeType !== 1) continue;
                    if (n.matches && n.matches("section, .rounded-xl")) enter(n, 40);
                    else if (n.querySelectorAll) {
                        let i = 0;
                        for (const c of n.querySelectorAll(":scope > section, :scope > .rounded-xl"))
                            enter(c, 40 + (i++ * 60));
                    }
                }
            }
        }).observe(main, {childList: true, subtree: true});
    });
})();
