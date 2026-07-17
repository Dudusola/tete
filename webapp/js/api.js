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
      html.dark #pp-sheet .pp-grab { background:#42474a; }`;
    const style = document.createElement("style");
    style.textContent = DARK_CSS;
    document.head.appendChild(style);

    const saved = localStorage.getItem("pp-theme");
    if (saved === "dark") document.documentElement.classList.add("dark");

    window.toggleTheme = function () {
        const dark = document.documentElement.classList.toggle("dark");
        localStorage.setItem("pp-theme", dark ? "dark" : "light");
        document.querySelectorAll(".theme-toggle .material-symbols-outlined")
            .forEach(icon => icon.textContent = dark ? "light_mode" : "dark_mode");
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
            .filter(a => /consensus\.html|checker\.html/.test(a.getAttribute("href")));
        const onExtraPage = /consensus\.html|checker\.html/.test(here);

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
    slip:        (risk, n)   => apiGet(`/api/slip?risk=${risk}&games=${n}`),
    consensus:   ()          => apiGet("/api/consensus"),
    stats:       ()          => apiGet("/api/stats"),
    statsTiers:  ()          => apiGet("/api/stats/tiers"),
    scoredPicks: (tier)      => apiGet(`/api/stats/picks${tier ? `?tier=${tier}` : ""}`),
    fixtures:    ()          => apiGet("/api/fixtures"),
    checkSlip:   (legs)      => apiPost("/api/slip/check", {legs}),
    decodeCode:  (provider, code) => apiPost("/api/booking/decode", {provider, code}),
    generateCode: (legs, provider) => apiPost("/api/booking/generate", {legs, provider: provider || "sportybet"}),
};