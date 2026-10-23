/* Tailwind build config for the web app -> webapp/css/app.css.

   WHY THIS EXISTS. Every page used to load the Tailwind Play CDN: a 419 KB
   script (127 KB gzipped) that compiles CSS IN THE BROWSER on every page view,
   and blocks rendering until it has. Measured 7.9 s to download on 28 Sep 2026.
   Tailwind's own docs say the Play CDN is not for production. For a phone on
   Nigerian mobile data that was the single slowest thing on every page.
   Now the CSS is compiled once, here, and pages load a small static file.

   The design tokens below MOVED here from webapp/js/theme.js (deleted), which
   assigned them to the Play CDN's runtime global. This file is now the single
   source for colours, spacing, radius and type.

   BUILD:  cd tools/tailwind && npm ci && npm run build
   The deploy workflow (.github/workflows/deploy-webapp.yml) ALWAYS rebuilds
   before publishing, so production can never ship a stale stylesheet. The
   committed webapp/css/app.css is for local dev (python -m http.server) and the
   Render fallback; rebuild it when you change classes, or a new class simply
   has no style locally. The workflow warns when the committed copy is stale.

   THE ONE RULE: Tailwind finds classes by scanning the files in `content` for
   complete class names. Never assemble a class name from pieces
   (`"text-" + colour`) — it is invisible to the scan and ships unstyled. Write
   the whole name out (`on ? "bg-primary" : "bg-surface"`). */
module.exports = {
    content: [
        "../../webapp/*.html",
        "../../webapp/js/*.js",
        "../../webapp/config.js",
    ],
    darkMode: "class",
    // Compile every hover: utility inside @media (hover: hover). Without it a
    // phone applies :hover on tap and leaves it there — a tapped chip or card
    // kept its hover colour/shadow until the next tap somewhere else.
    future: { hoverOnlyWhenSupported: true },
    theme: { extend: {
        "colors": {
            "inverse-surface": "#2d3133","primary-container": "#131b2e","surface-container-high": "#e6e8ea",
            "on-error-container": "#93000a","on-secondary-fixed-variant": "#304f00","background": "#f7f9fb",
            "on-tertiary-fixed-variant": "#38485d","on-error": "#ffffff","on-primary-container": "#7c839b",
            "secondary-fixed-dim": "#91db2a","on-background": "#191c1e","surface-container-lowest": "#ffffff",
            "surface": "#f7f9fb","tertiary-fixed-dim": "#b7c8e1","on-primary": "#ffffff",
            "secondary-container": "#acf847","on-secondary-container": "#457000","inverse-primary": "#bec6e0",
            "on-secondary-fixed": "#102000","primary-fixed-dim": "#bec6e0","inverse-on-surface": "#eff1f3",
            "secondary-fixed": "#acf847","on-primary-fixed": "#131b2e","primary-fixed": "#dae2fd",
            "tertiary-container": "#0b1c30","primary": "#000000","surface-container-highest": "#e0e3e5",
            "tertiary": "#000000","on-surface": "#191c1e","surface-variant": "#e0e3e5",
            "on-tertiary-fixed": "#0b1c30","error": "#ba1a1a","surface-container": "#eceef0",
            "on-secondary": "#ffffff","tertiary-fixed": "#d3e4fe","surface-dim": "#d8dadc",
            "outline": "#76777d","secondary": "#416900","on-tertiary": "#ffffff",
            "surface-container-low": "#f2f4f6","outline-variant": "#c6c6cd","error-container": "#ffdad6",
            "on-surface-variant": "#45464d","surface-bright": "#f7f9fb","surface-tint": "#565e74",
            "on-tertiary-container": "#75859d","on-primary-fixed-variant": "#3f465c"
        },
        "borderRadius": { "DEFAULT": "0.25rem","lg": "0.5rem","xl": "0.75rem","full": "9999px" },
        "spacing": { "sm":"12px","gutter":"16px","xs":"8px","md":"16px","xl":"32px","container-max":"1280px","base":"4px","lg":"24px" },
        "fontFamily": { "body-lg":["Inter"],"label-sm":["Inter"],"body-md":["Inter"],"headline-lg":["Inter"],"headline-md":["Inter"],"display-lg":["Inter"],"headline-lg-mobile":["Inter"],"data-tabular":["Inter"] },
        "fontSize": {
            "body-lg":["18px",{"lineHeight":"28px","fontWeight":"400"}],
            "label-sm":["12px",{"lineHeight":"16px","fontWeight":"500"}],
            "body-md":["16px",{"lineHeight":"24px","fontWeight":"400"}],
            "headline-lg":["32px",{"lineHeight":"40px","letterSpacing":"-0.01em","fontWeight":"700"}],
            "headline-md":["24px",{"lineHeight":"32px","fontWeight":"600"}],
            // Card titles ("Team A vs Team B"): headline-md wrapped long names to three lines on a phone.
            "title-md":["19px",{"lineHeight":"25px","letterSpacing":"-0.01em","fontWeight":"600"}],
            "display-lg":["48px",{"lineHeight":"56px","letterSpacing":"-0.02em","fontWeight":"800"}],
            "headline-lg-mobile":["24px",{"lineHeight":"32px","fontWeight":"700"}],
            "data-tabular":["14px",{"lineHeight":"20px","letterSpacing":"0.02em","fontWeight":"600"}]
        }
    }},
    plugins: [require("@tailwindcss/forms")],
};
