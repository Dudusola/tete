/* api.js - the glue between the web app and your FastAPI backend.
   Every screen imports this. Change API_BASE in one place if the port/host changes. */

/* API location, in priority order:
   1. ?api=<url> in the page URL (handy for testing a deployed API from local)
   2. window.PP_API_BASE — set by config.js when the PWA is deployed
   3. same-origin :8080 on localhost (the dev default)
   4. same origin (when API is reverse-proxied under the web host)  */
const API_BASE = (function () {
    const q = new URLSearchParams(location.search).get("api");
    if (q) { try { localStorage.setItem("pp-api", q); } catch (e) {} return q.replace(/\/$/, ""); }
    try { const saved = localStorage.getItem("pp-api"); if (saved) return saved.replace(/\/$/, ""); } catch (e) {}
    if (window.PP_API_BASE) return window.PP_API_BASE.replace(/\/$/, "");
    if (["localhost", "127.0.0.1"].includes(location.hostname)) return "http://localhost:8080";
    return location.origin;   // deployed same-origin fallback
})();

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
      @media (min-width:768px) { #theme-toggle-mobile { display:none !important; } }
      /* Mobile brand bar. Scoped to the mobile breakpoint so it can never
         override the element's own md:hidden and duplicate the desktop logo. */
      @media (max-width:767px) {
        header.pp-brand-bar { display:flex; align-items:center;
                              justify-content:space-between; }
      }
      /* Mobile bottom-nav active state (replaces the old "More" sheet) */
      nav[class*="md:hidden"] a.pp-nav-on { position:relative; }
      nav[class*="md:hidden"] a.pp-nav-on::before { content:""; position:absolute; top:0;
        left:22%; right:22%; height:3px; border-radius:0 0 3px 3px; background:#416900; }
      html.dark nav[class*="md:hidden"] a.pp-nav-on::before { background:#91db2a; }
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

    // ── Logo ────────────────────────────────────────────────────────────────
    // Two problems with using logo.png in the header:
    //   1. It is the full LOCKUP — ball plus the words "PotentialPro" — and it
    //      sits right next to a text element that also says "PotentialPro".
    //      The name was rendered twice, and at 32-40px the baked-in wordmark
    //      is an illegible smudge.
    //   2. Its ball has dark navy panels. On the dark theme those panels are
    //      within a few points of the page background, so the mark dissolves
    //      into a faint green outline.
    // logo-mark.png is the ball alone, trimmed and squared. logo-mark-dark.png
    // is the same artwork with ONLY the navy panels lifted to the light
    // surface tone — the green identity is untouched — so the ball reads as a
    // ball in both themes instead of half-vanishing in one.
    function syncLogo() {
        const dark = document.documentElement.classList.contains("dark");
        const src = dark ? "logo-mark-dark.png" : "logo-mark.png";
        document.querySelectorAll("img[alt='PotentialPro']").forEach(img => {
            if (!img.getAttribute("src").startsWith("logo")) return;  // not ours
            if (img.getAttribute("src") !== src) img.setAttribute("src", src);
        });
    }
    document.addEventListener("DOMContentLoaded", syncLogo);

    window.toggleTheme = function () {
        const dark = document.documentElement.classList.toggle("dark");
        localStorage.setItem("pp-theme", dark ? "dark" : "light");
        document.querySelectorAll(".theme-toggle .material-symbols-outlined")
            .forEach(icon => icon.textContent = dark ? "light_mode" : "dark_mode");
        syncThemeColor();
        syncLogo();
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
        // ── Mobile brand bar ────────────────────────────────────────────────
        // This used to inject the PAGE TITLE on any page lacking a mobile
        // header — but it never hid the page's OWN title, so phones showed the
        // heading twice (literally "Slip Builder" above "Slip Builder", and
        // "Slip Checker" above "Check a Slip"). Meanwhile index.html's
        // hand-written mobile header showed the BRAND, so "Today's Picks"
        // appeared nowhere on a phone, and record.html buried its title inside
        // a mobile-only header so DESKTOP had no title at all. Five pages,
        // five different behaviours for the same thing.
        //
        // Now there is one rule: the brand bar is the brand (mobile only —
        // desktop has the top nav for that), and the page's own <h1> is the
        // title at every width. Nothing is duplicated and nothing is missing.
        // Keep new pages to that shape rather than adding a title here.
        if (!document.querySelector("header.md\\:hidden")) {
            const hdr = document.createElement("header");
            hdr.className = "md:hidden px-gutter pb-sm";
            hdr.style.paddingTop = "max(12px, env(safe-area-inset-top))";
            hdr.innerHTML =
                '<div class="flex items-center gap-2">' +
                '<img src="logo-mark.png" alt="PotentialPro" class="h-8 w-8 object-contain"/>' +
                '<span class="text-headline-lg-mobile font-headline-lg-mobile text-primary">' +
                'PotentialPro</span></div>';
            document.body.insertBefore(hdr, document.body.firstChild);
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

        // ── Mobile bottom nav: all five, no "More" ──────────────────────────
        //
        // This used to hide two of the five sections behind a hamburger and a
        // bottom sheet. The reason given was decluttering, but the arithmetic
        // never supported it: five items on the narrowest phone we care about
        // (320px) is 64px each, comfortably past the 44px tap-target floor.
        // So the sheet bought nothing and cost a tap, a second interaction
        // model to learn, and two features that looked second-class.
        //
        // Everything is now one tap from everywhere, which is also what the
        // desktop nav does — one mental model instead of two.
        const bottomNav = document.querySelector("nav[class*='md:hidden']");
        if (!bottomNav) return;

        // The theme toggle moves to the mobile BRAND BAR at the top. It is a
        // setting, not a destination, so it does not belong in a navigation
        // bar competing with the five sections.
        //
        // The class is literally "md:hidden" — a colon that CSS treats as a
        // pseudo-selector, so it must be escaped as "md\:hidden" in a
        // querySelector string. Getting that wrong makes the selector INVALID
        // and querySelector THROWS, which silently killed the rest of this
        // handler. Matching on classList avoids the trap entirely.
        const brandBar = [...document.querySelectorAll("header")]
            .find(h => h.classList.contains("md:hidden"));
        if (brandBar && !document.getElementById("theme-toggle-mobile")) {
            // Appended to the HEADER, never to its first child — that child is
            // the brand link, so a toggle inside it would also navigate home.
            //
            // Layout comes from a CLASS, never an inline style. Setting
            // `style.display = "flex"` here beat the element's own md:hidden
            // rule — inline styles outrank stylesheet rules — so this mobile
            // header stayed visible on desktop and the page showed TWO logos:
            // this one and the desktop nav's.
            brandBar.classList.add("pp-brand-bar");
            brandBar.appendChild(makeBtn("theme-toggle-mobile"));
        }

        // Active section gets a filled pill AND a top rule, so the current
        // page is identifiable without relying on colour alone.
        //
        // Compare basenames with the extension stripped. Cloudflare Pages
        // serves these as EXTENSIONLESS urls — /checker, not /checker.html —
        // so a raw comparison against the href matches locally and never on
        // production, which is the worst kind of bug to ship: it looks correct
        // on every machine you test it on. An empty path is the index.
        const norm = u => (u || "").split("/").pop().replace(/\.html$/, "") || "index";
        const here = norm(location.pathname);
        bottomNav.querySelectorAll("a").forEach(a => {
            a.classList.toggle("pp-nav-on", norm(a.getAttribute("href")) === here);
        });
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

    // ── Second pass (Aug 2026) ───────────────────────────────────────────
    // 109 code labels were still reaching the UI raw ("Sy1", "Fnl", "KW"),
    // across 633 matches. Forebet's scheme is <Country><Tier>, with C = cup,
    // W = women. Each entry below was identified from the CLUBS actually
    // recorded under it, not from guessing the abbreviation.
    //
    // Codes I could NOT identify with confidence are deliberately left out —
    // prettyLeague() then shows the raw code, which is ugly but honest.
    // Labelling a league wrongly is the same class of error as the fake
    // scores: a confident-looking string that isn't true.
    WC: "World Cup", UEL: "Europa League", CLW: "Women's Champions League",
    CS: "Copa Sudamericana", LC: "Leagues Cup",

    // Europe
    Upl: "Ukrainian Premier League", Ua2: "Ukraine First League",
    By1: "Belarus Premier League", By2: "Belarus First League",
    ByC: "Belarus Cup", ByW: "Belarus Women",
    Ge1: "Georgia Erovnuli Liga", Ge2: "Georgia Erovnuli Liga 2",
    GeC: "Georgia Cup", Am1: "Armenia Premier League",
    Lv1: "Latvia Virsliga", Lv2: "Latvia First League", LvC: "Latvia Cup",
    Lt1: "Lithuania A Lyga", Lt2: "Lithuania First League",
    Ee1: "Estonia Meistriliiga", Ee2: "Estonia Esiliiga", EeC: "Estonia Cup",
    Fo1: "Faroe Islands Premier", Fo2: "Faroe Islands First Division",
    Is2: "Iceland 1. deild", Is3: "Iceland 2. deild", IsC: "Iceland Cup",
    IsW: "Iceland Women", NIW: "Northern Ireland Women",
    Ie1: "Ireland Premier Division", Ie2: "Ireland First Division",
    Se4: "Sweden Division 2", SeC: "Svenska Cupen",
    SeW2: "Sweden Elitettan", NoW: "Norway Toppserien",
    No3: "Norway Second Division", FiW: "Finland Kansallinen Liiga",
    Fi4: "Finland Kolmonen", DkC: "Denmark Cup",
    PlC: "Polish Cup", IlC: "Israel Cup", KzC: "Kazakhstan Cup",
    Ru2: "Russia First League", Fnl: "Russia First League",
    Ru3: "Russia Second League", RuW: "Russia Women",
    Ma1: "Morocco Botola Pro",

    // Asia & Oceania
    Kr2: "K League 2", Kr3: "K3 League", KrW: "Korea WK League",
    CnC: "Chinese FA Cup", CnW: "China Women's Super League",
    JpW2: "Japan Nadeshiko 2", JpW3: "Japan Nadeshiko 3",
    Sy1: "Syrian Premier League", Lb: "Lebanese Premier League",
    KW: "Kuwait Premier League", Kg1: "Kyrgyzstan Premier League",
    Tj1: "Tajikistan Higher League", Tm1: "Turkmenistan Yokary Liga",
    Mn1: "Mongolia Premier League", Mo1: "Macau Liga de Elite",
    Vn1: "Vietnam V.League 1", VnW: "Vietnam Women",
    Fj1: "Fiji Premier League", AuA: "Australia Capital Territory",
    AuC: "Australia Cup",

    // Africa
    Et1: "Ethiopia Premier League", Tz1: "Tanzania Premier League",
    Zw1: "Zimbabwe Premier League", Mw1: "Malawi Super League",
    Cm1: "Cameroon Elite One", Cm2: "Cameroon Ligue 2",
    Gm1: "Gambia GFA League", Sl1: "Sierra Leone Premier League",
    CD1: "DR Congo Linafoot", Dj1: "Djibouti Premier League",

    // Americas
    Ca1: "Canadian Premier League", CaC: "Canadian Championship",
    Mx2: "Liga de Expansion MX", Gt1: "Guatemala Liga Nacional",
    Cr1: "Costa Rica Primera", Ni1: "Nicaragua Primera",
    Aw1: "Aruba Division di Honor",
    Cl1: "Chile Primera", ClC: "Copa Chile",
    Co2: "Colombia Primera B", CoC: "Copa Colombia",
    Ec2: "Ecuador Serie B", Pe2: "Peru Liga 2",
    Py2: "Paraguay Intermedia", PyC: "Paraguay Cup",
    Uy1: "Uruguay Primera", Uy2: "Uruguay Segunda",
    BrG: "Brazil Gaucho",
    // Youth and lower tiers where the clubs pin it down unambiguously.
    CzU: "Czech U19", De6: "Germany U19",
    De5: "Germany Oberliga",              // Ammerthal, Kirchanschoring, Cham
    Es3: "Spain Primera Federacion",      // CE Sabadell
    Es4: "Spain Segunda Federacion",      // Pena Deportiva, Caudal Deportivo

    // STILL UNMAPPED ON PURPOSE (8 labels, ~40 matches): AtL, Ar4, Ru4, Cz3,
    // Cz4, De4, Pl3, Sv1. Each is a lower tier whose exact competition the
    // clubs do not settle — De4 holds Erzgebirge Aue, who play in 3. Liga,
    // which contradicts the <Country><Tier> reading, and Sv1 holds an Aruban
    // club. Guessing a plausible name for these would put a confident,
    // unverified label in front of users. They render as the raw code until
    // someone confirms them against Forebet directly.
};

function prettyLeague(s) {
    if (LEAGUE_CODES[s]) return LEAGUE_CODES[s];
    const cleaned = (s || "").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
    if (!cleaned) return "Football";
    return cleaned.replace(/\b[a-z]/g, c => c.toUpperCase());
}

/* ── Kickoff time helpers ─────────────────────────────────────────────
   match_date is "YYYY-MM-DD", kickoff_time is "HH:MM" (may be missing). */
/* Kickoff handling.

   kickoff_utc (set by scrapers/kickoff_times.py from ESPN) is AUTHORITATIVE:
   a full UTC timestamp. kickoff_time is a bare 'HH:MM' with no timezone that
   PredictZ leaves at '00:00' for ~88% of fixtures, meaning UNKNOWN — treating
   that as midnight marked every fixture as already kicked off. Prefer the UTC
   value; fall back only when it is absent, and never invent a time. */
function kickoffDate(matchDate, kickoffTime, kickoffUtc) {
    if (kickoffUtc) {
        const d = new Date(kickoffUtc.replace(" ", "T") + "Z");
        if (!isNaN(d)) return d;
    }
    if (!matchDate) return null;
    // No usable time: return the DATE only, and let callers treat the time as
    // unknown rather than midnight.
    if (!/^\d{1,2}:\d{2}/.test(kickoffTime || "") || kickoffTime === "00:00") {
        const d = new Date(`${matchDate}T12:00`);   // midday: a neutral anchor
        return isNaN(d) ? null : d;
    }
    const d = new Date(`${matchDate}T${kickoffTime.padStart(5, "0")}`);
    return isNaN(d) ? null : d;
}

/* Do we actually KNOW the kickoff time? '00:00' from PredictZ does not count. */
function hasKnownKickoff(p) {
    return !!(p && (p.kickoff_utc ||
        (/^\d{1,2}:\d{2}/.test(p.kickoff_time || "") && p.kickoff_time !== "00:00")));
}

function kickoffLabel(matchDate, kickoffTime, kickoffUtc) {
    const d = kickoffDate(matchDate, kickoffTime, kickoffUtc);
    if (!d) return matchDate || "";
    const now = new Date();
    const day = new Date(d); day.setHours(0, 0, 0, 0);
    const today = new Date(now); today.setHours(0, 0, 0, 0);
    const diff = Math.round((day - today) / 86400000);
    // Show the time in the VIEWER's timezone when we have a real UTC kickoff;
    // say "time TBC" rather than displaying a fake 00:00.
    let time = "";
    if (kickoffUtc) {
        time = " " + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    } else if (/^\d{1,2}:\d{2}/.test(kickoffTime || "") && kickoffTime !== "00:00") {
        time = " " + kickoffTime;
    } else {
        // No time text at all. The card adds a quiet "time TBC" marker itself —
        // baking it in here rendered as a shouty "TIME TBC", because the meta
        // line is styled uppercase.
        time = "";
    }
    if (diff === 0) return "Today" + time;
    if (diff === 1) return "Tomorrow" + time;
    if (diff === -1) return "Yesterday" + time;
    return d.toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"}) + time;
}

/* Has this match started? Takes the whole pick so it can use the authoritative
   fields. ESPN's live_state wins when present ('in'/'post' = under way or done)
   because it also catches delays and reschedules a timestamp cannot. Unknown
   stays FALSE: hiding a match we merely failed to time is worse than showing it. */
function hasKickedOff(p) {
    if (!p) return false;
    if (p.live_state === "in" || p.live_state === "post") return true;
    if (!hasKnownKickoff(p)) return false;
    const d = kickoffDate(p.match_date, p.kickoff_time, p.kickoff_utc);
    return d ? d < new Date() : false;
}

/* Is it being played right now? Drives the LIVE badge.

   live_state is a SNAPSHOT the pipeline wrote when it last scraped, not a live
   feed — a match in play at scrape time stays 'in' until something re-scrapes
   it. That put a pulsing red LIVE badge on matches that had finished 12 hours
   earlier. The API now ages the field (effective_live_state), but this guard
   stays: a cached/offline payload from the service worker can still carry a
   stale 'in', and the badge is the most confidently wrong thing on the page.
   3.5h covers 90 min + half time + stoppage + extra time + penalties. */
const LIVE_MAX_MS = 3.5 * 60 * 60 * 1000;
function isLive(p) {
    if (!p || p.live_state !== "in") return false;
    const ko = kickoffDate(p.match_date, p.kickoff_time, p.kickoff_utc);
    if (!ko) return true;              // no kickoff time — can't age it
    return (Date.now() - ko.getTime()) < LIVE_MAX_MS;
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
    // Draw-leaning fixtures. The response carries its own `disclosure` string
    // — render it with the list, never the list alone (see models/draws.py).
    drawPicks:   ()          => apiGet("/api/picks/draws"),
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
    statsTrend:  ()          => apiGet("/api/stats/trend"),
    scoredPicks: (tier)      => apiGet(`/api/stats/picks${tier ? `?tier=${tier}` : ""}`),
    fixtures:    ()          => apiGet("/api/fixtures"),
    checkSlip:   (legs)      => apiPost("/api/slip/check", {legs}),
    decodeCode:  (provider, code) => apiPost("/api/booking/decode", {provider, code}),
    // Slip tools. convert and merge existed in the API for weeks with no way
    // to reach them from the app — built, shipped, and invisible.
    splitCode:   (provider, code, parts, to_provider) =>
                     apiPost("/api/booking/split", {provider, code, parts, to_provider}),
    optimizeCode: (provider, code, min_probability, to_provider) =>
                     apiPost("/api/booking/optimize",
                             {provider, code, min_probability, to_provider}),
    convertCode: (from_provider, code, to_provider) =>
                     apiPost("/api/booking/convert", {from_provider, code, to_provider}),
    mergeCodes:  (sources, to_provider) =>
                     apiPost("/api/booking/merge", {sources, to_provider}),
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

    /* ── Cross-device layout fixes ────────────────────────────────────────
       Injected here rather than edited into five HTML files, so the rules
       can't drift page to page. Each one fixes a concrete failure, not a
       hypothetical: verified against the actual markup.                    */
    const RESPONSIVE_CSS = `
      /* 1. The mobile bottom nav is position:fixed with py-2 and no safe-area
            padding, so on iPhones with a home indicator (and Android gesture
            bars) the last few pixels sit under the system UI and taps land on
            the wrong thing. */
      nav.md\:hidden.fixed.bottom-0 {
        padding-bottom: max(8px, env(safe-area-inset-bottom)) !important;
      }

      /* 2. That same fixed nav overlaps the end of the page — the last card was
            unreachable behind it. Reserve its height plus the safe area. */
      @media (max-width: 767px) {
        body { padding-bottom: calc(76px + env(safe-area-inset-bottom)); }
      }

      /* 3. Never scroll the page sideways. Long team names and league slugs
            ("world-cup-2026-last-16") force grid children wider than their
            track unless min-width is cleared — the classic CSS-grid overflow. */
      html, body { overflow-x: hidden; max-width: 100%; }
      main, main * { min-width: 0; }

      /* 4. Touch targets — on TOUCH devices. 44px is the accessibility floor
            on iOS and Android, and the mobile bottom-nav items are only ~40px
            tall without it.

            Applying it to every pointer made the DESKTOP nav look wrong: each
            link became a 44px box with the label at the top and the active
            underline stranded near the bottom, so the row read as
            top-aligned even though every box was perfectly centred (measured:
            nav, logo, links and toggle all had mid = 32px). A mouse does not
            need a 44px target, so the floor now applies where it is actually
            for, and desktop links hug their text with the underline directly
            beneath it. */
      /* Scoped by BREAKPOINT, not by pointer type. "pointer: coarse" looked
         like the right test — the floor exists for fingers — but it reports
         TRUE on any touchscreen laptop, so the desktop nav kept the 44px boxes
         on exactly the machines being complained about. The mobile bottom nav
         only exists below md and the desktop top nav only above it, so the
         breakpoint says precisely what is meant and is testable. */
      @media (max-width: 767px) {
        nav a, nav button, .theme-toggle { min-height: 44px; }
      }
      /* Keep the toggle circular at every pointer type — its size is a visual
         decision, not a touch one. */
      .theme-toggle { min-height: 36px; }

      /* 4b. ...but the nav was the only thing covered, and the nav is not what
             people actually tap. The Checker's market chips (.add-leg) are the
             PRIMARY interaction on that page and were ~26px tall — 56 of them
             under the floor on a 390px phone, sitting a few pixels apart. Same
             for the remove-leg × and the tier chips.
             Scoped to pointer:coarse so a mouse keeps the compact layout;
             inline-flex because min-height does nothing on a plain inline box. */
      @media (pointer: coarse) {
        .add-leg, .rm, .tier-chip, .pin-leg-btn, .swap-leg-btn {
          min-height: 44px; min-width: 44px;
          display: inline-flex; align-items: center; justify-content: center;
        }
      }

      /* 5. Very small phones (360px and below are still common in Nigeria):
            stop headline text forcing horizontal overflow. */
      @media (max-width: 380px) {
        .text-headline-md { font-size: 17px !important; line-height: 22px !important; }
        .text-display-sm  { font-size: 24px !important; line-height: 30px !important; }
      }

      /* 6. Large screens: the container maxed out narrow, leaving desktop users
            with a thin column of cards and a lot of empty space. */
      @media (min-width: 1280px) {
        .max-w-container { max-width: 1200px !important; }
      }

      /* 7. Landscape phones are short: the sticky header ate a third of the
            viewport. */
      @media (max-height: 480px) and (orientation: landscape) {
        header.md\:hidden { position: static !important; }
      }

      /* 8. ONE page shell for all five pages.
            They had drifted apart: index used py-md, slip pt-8/md:pt-24,
            consensus body md:pt-16, record no horizontal padding at all on
            mobile. Same app, five different amounts of breathing room
            depending which tab you were on. Normalised here (one place, not
            five files) so a phone, tablet and desktop each get a consistent
            rhythm — and so a future page inherits it for free. */
      main {
        max-width: 1280px;
        margin-inline: auto;
        padding-left: 16px;
        padding-right: 16px;
        padding-top: 16px;
      }
      @media (min-width: 768px) {
        main { padding-left: 24px; padding-right: 24px; padding-top: 32px; }
        /* Desktop has a fixed 64px top nav; every page must clear it. */
        body { padding-top: 64px; }
      }
      @media (max-width: 767px) {
        /* Mobile uses the bottom nav instead, so no top offset — that was
           dead space on pages that set md:pt-16 unconditionally. */
        body { padding-top: 0; }
      }

      /* 9. LIVE badge dot — pulses so an in-play match is obvious at a glance. */
      .pp-live-dot {
        width: 7px; height: 7px; border-radius: 50%;
        background: currentColor; display: inline-block;
        animation: pp-pulse 1.4s ease-in-out infinite;
      }
      @keyframes pp-pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
      /* Respect users who ask for less motion. */
      @media (prefers-reduced-motion: reduce) {
        .pp-live-dot { animation: none; }
      }`;
    const rs = document.createElement("style");
    rs.textContent = RESPONSIVE_CSS;
    document.head.appendChild(rs);

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
