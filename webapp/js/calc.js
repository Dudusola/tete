/* calc.js — betting calculators on the Checker's Tools tab (21 Oct 2026).

   Returns, odds converter, hedge and dutching: the free half of Puntrr's
   "SureTOOLS". Pure arithmetic on numbers the user types; nothing is sent
   anywhere and no model number is involved, so these can never disagree with
   the record. The maths lives in PPCalc (tested by tools/calc.test.mjs with node);
   PPCalc.mount() draws the four panels into a container. */
(function () {
    // "2.5", "3/2", "+150", "-200" -> decimal odds, or NaN. Decimal odds must be
    // above 1: at 1.00 a bet returns only the stake, below it is not a price.
    function toDecimal(input) {
        const s = String(input ?? "").trim().replace(/\s+/g, "");
        let d = NaN;
        if (/^\d+(\.\d+)?\/\d+(\.\d+)?$/.test(s)) {
            const [a, b] = s.split("/").map(Number);
            d = b > 0 ? 1 + a / b : NaN;
        } else if (/^[+-]\d+(\.\d+)?$/.test(s)) {
            const n = Number(s);
            d = n >= 100 ? 1 + n / 100 : n <= -100 ? 1 + 100 / -n : NaN;
        } else if (/^\d+(\.\d+)?$/.test(s)) {
            d = Number(s);
        }
        return d > 1 ? d : NaN;
    }

    // Smallest fraction within 0.005 of (d - 1), denominators up to 100.
    function fractional(d) {
        const x = d - 1;
        for (let den = 1; den <= 100; den++) {
            const num = Math.round(x * den);
            if (num > 0 && Math.abs(num / den - x) < 0.005) return `${num}/${den}`;
        }
        return `${Math.round(x * 100)}/100`;
    }

    function american(d) {
        return d >= 2 ? `+${Math.round((d - 1) * 100)}` : `${Math.round(-100 / (d - 1))}`;
    }

    function convert(input) {
        const d = toDecimal(input);
        if (isNaN(d)) return null;
        return {decimal: d, fractional: fractional(d), american: american(d), implied: 1 / d};
    }

    // Accumulator: every leg must win, so the odds multiply.
    function returns(stake, oddsList) {
        const odds = oddsList.map(toDecimal);
        if (!(stake > 0) || !odds.length || odds.some(isNaN)) return null;
        const total = odds.reduce((p, o) => p * o, 1);
        return {totalOdds: total, payout: stake * total, profit: stake * total - stake};
    }

    // Back `stake` at `odds1`; how much on the other side at `odds2` makes the
    // payout the same whichever way it goes? The result is the same either way:
    // a locked profit when 1/odds1 + 1/odds2 < 1, a locked loss otherwise.
    function hedge(stake, odds1, odds2) {
        const o1 = toDecimal(odds1), o2 = toDecimal(odds2);
        if (!(stake > 0) || isNaN(o1) || isNaN(o2)) return null;
        const hedgeStake = stake * o1 / o2;
        const payout = stake * o1;
        return {hedgeStake, payout, profit: payout - stake - hedgeStake};
    }

    // Split `total` across outcomes so each pays the same: stake ∝ 1/odds.
    // If the 1/odds sum is over 1 (the bookmaker's margin), every result loses.
    function dutch(total, oddsList) {
        const odds = oddsList.map(toDecimal);
        if (!(total > 0) || odds.length < 2 || odds.some(isNaN)) return null;
        const book = odds.reduce((s, o) => s + 1 / o, 0);
        const stakes = odds.map(o => total * (1 / o) / book);
        const payout = total / book;
        return {stakes, payout, profit: payout - total, book};
    }

    const PPCalc = {toDecimal, convert, returns, hedge, dutch};

    // ── UI ─────────────────────────────────────────────────────────────────
    const naira = v => "₦" + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const oddsOf = s => String(s || "").split(/[,;\s]+/).filter(Boolean);
    const field = (id, label, ph, mode = "decimal") => `
      <label class="block text-label-sm text-on-surface-variant">${label}
        <input id="${id}" type="text" inputmode="${mode}" autocomplete="off" placeholder="${ph}"
               class="mt-1 w-full rounded-lg border border-outline-variant px-md py-sm text-body-md bg-surface"/></label>`;
    const panel = (icon, title, note, body) => `
      <section class="pt-md mt-md border-t border-outline-variant first:mt-0 first:pt-0 first:border-0">
        <h3 class="flex items-center gap-xs text-body-md font-semibold text-primary">
          <span class="material-symbols-outlined text-[18px] text-secondary" aria-hidden="true">${icon}</span>${title}</h3>
        <p class="text-label-sm text-on-surface-variant mb-sm">${note}</p>
        ${body}
      </section>`;
    const out = id => `<p id="${id}" class="mt-sm text-body-md text-on-surface min-h-[1.5rem]" aria-live="polite"></p>`;

    PPCalc.mount = function (el) {
        el.innerHTML =
            panel("payments", "Returns", "What a slip pays if every leg wins.",
                `<div class="grid grid-cols-1 sm:grid-cols-2 gap-sm">${field("cr-stake", "Stake (₦)", "1000")}${field("cr-odds", "Odds, one per leg", "1.50, 2.10, 1.80", "text")}</div>${out("cr-out")}`) +
            panel("swap_horiz", "Odds converter", "Decimal, fractional (5/2) or American (+150, -200).",
                `${field("cc-odds", "Odds", "2.50 or 3/2 or +150", "text")}${out("cc-out")}`) +
            panel("balance", "Hedge", "Back the other side so you get the same whichever way it ends.",
                `<div class="grid grid-cols-1 sm:grid-cols-3 gap-sm">${field("ch-stake", "Original stake (₦)", "1000")}${field("ch-o1", "Original odds", "3.00", "text")}${field("ch-o2", "Odds on the other side now", "1.60", "text")}</div>${out("ch-out")}`) +
            panel("call_split", "Dutching", "Spread one stake over several outcomes so each pays the same.",
                `<div class="grid grid-cols-1 sm:grid-cols-2 gap-sm">${field("cd-total", "Total stake (₦)", "1000")}${field("cd-odds", "Odds of each outcome", "2.40, 3.20, 4.00", "text")}</div>${out("cd-out")}`);

        const v = id => el.querySelector("#" + id).value;
        const num = id => Number(v(id).replace(/,/g, ""));
        const show = (id, html) => { el.querySelector("#" + id).innerHTML = html; };
        const hint = "Enter a stake and valid odds.";

        const update = {
            cr() {
                if (!v("cr-stake") && !v("cr-odds")) return show("cr-out", "");
                const r = returns(num("cr-stake"), oddsOf(v("cr-odds")));
                show("cr-out", r ? `Total odds <b>${r.totalOdds.toFixed(2)}</b> · pays <b>${naira(r.payout)}</b> · profit <b>${naira(r.profit)}</b>` : hint);
            },
            cc() {
                if (!v("cc-odds")) return show("cc-out", "");
                const r = convert(v("cc-odds"));
                show("cc-out", r ? `Decimal <b>${r.decimal.toFixed(2)}</b> · fractional <b>${r.fractional}</b> · American <b>${r.american}</b> · implied chance <b>${(r.implied * 100).toFixed(1)}%</b>` : "Not a price we can read. Decimal odds must be above 1.00.");
            },
            ch() {
                if (!v("ch-stake") && !v("ch-o1") && !v("ch-o2")) return show("ch-out", "");
                const r = hedge(num("ch-stake"), v("ch-o1"), v("ch-o2"));
                show("ch-out", r ? `Stake <b>${naira(r.hedgeStake)}</b> on the other side. Either way you get ${naira(r.payout)}: ` +
                    (r.profit >= 0 ? `a locked profit of <b>${naira(r.profit)}</b>.` : `a locked loss of <b>${naira(-r.profit)}</b>, because these two prices add up to more than 100% (the bookmaker's margin). Hedging still caps the loss.`) : hint);
            },
            cd() {
                if (!v("cd-total") && !v("cd-odds")) return show("cd-out", "");
                const odds = oddsOf(v("cd-odds"));
                const r = dutch(num("cd-total"), odds);
                show("cd-out", r ? r.stakes.map((s, i) => `${odds[i]}: <b>${naira(s)}</b>`).join(" · ") +
                    `<br>Any of them pays ${naira(r.payout)}: ` + (r.profit >= 0 ? `profit <b>${naira(r.profit)}</b>.` :
                    `a loss of <b>${naira(-r.profit)}</b> whichever wins, because these odds add up to ${(r.book * 100).toFixed(0)}% (the bookmaker's margin).`)
                    : "Enter a stake and at least two odds.");
            },
        };
        Object.keys(update).forEach(k => el.querySelectorAll(`[id^="${k}-"]`).forEach(i =>
            i.tagName === "INPUT" && i.addEventListener("input", update[k])));
    };

    if (typeof window !== "undefined") window.PPCalc = PPCalc;
    if (typeof module !== "undefined") module.exports = PPCalc;
})();
