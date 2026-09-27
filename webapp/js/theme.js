/* Tailwind design tokens — the single source for all five pages.

   This block was inline in every page: 2.4KB × 5, byte-identical copies of 47
   colour tokens plus the spacing, radius and type scales. They were still
   identical only because nobody had touched them; the first colour change would
   have been made in one file, and the drift would have been invisible until
   someone happened to compare two pages side by side. That is exactly how the
   desktop nav ended up spaced three different ways.

   Must load AFTER cdn.tailwindcss.com (it assigns onto the `tailwind` global)
   and BEFORE </head>. Both are head-blocking scripts, so they finish before
   <body> is parsed and the Play CDN's observer never sees content styled under a
   default config — no flash of unstyled content.

   Stamped with a content hash by .github/workflows/deploy-webapp.yml, same as
   api.js. A shared file served from a stale cache is the "Couldn't book" bug
   class that workflow exists to prevent; if you add another shared file, stamp
   it there too.

   The guard is for the case where the CDN itself is blocked or fails: without it
   this line throws an uncaught ReferenceError and the console shows a confusing
   error about our own file, not about the CDN that actually failed. */
if (typeof tailwind === "undefined") {
    console.warn("Tailwind CDN did not load — design tokens not applied.");
} else {
tailwind.config = {
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
            "display-lg":["48px",{"lineHeight":"56px","letterSpacing":"-0.02em","fontWeight":"800"}],
            "headline-lg-mobile":["24px",{"lineHeight":"32px","fontWeight":"700"}],
            "data-tabular":["14px",{"lineHeight":"20px","letterSpacing":"0.02em","fontWeight":"600"}]
        }
    }}
};
}
