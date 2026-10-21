/* auth.js — sign-in and per-user "My Record", on every page.

   TWO RECORDS, KEPT APART ON PURPOSE (CLAUDE.md "What's next" item 8):
   the MODEL's track record is global and lives in the ledger; a USER's record
   is the picks THEY chose to follow, and lives in Supabase. Nothing here can
   change a model number, and no user row ever enters the committed ledger.

   SIGN-IN IS GOOGLE ONLY, and that is a finding, not a preference: Supabase's
   built-in email sender delivers only to members of the project's own team
   ("Email address not authorized" for anyone else) and is capped at 2/hour,
   so magic links would have failed for every tester. Google needs no mail.

   The publishable key below is PUBLIC by design. What protects user data is
   row-level security on tracked_picks (db/supabase/001_tracked_picks.sql):
   every row is readable and writable only by the signed-in user who owns it.
   Never put a service_role / secret key in this file.

   Loads supabase-js from jsDelivr only when this script runs; if that CDN or
   Supabase is unreachable, every page still works exactly as before — the
   account button just says sign-in is unavailable. */
(function () {
    const SUPABASE_URL = "https://vtltrjsjhfdhlrqrswvt.supabase.co";
    const SUPABASE_KEY = "sb_publishable_fzynYbZXgkAzWyHOuI-98w_ALnV0Bvj";
    const SDK = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.js";

    let client = null, user = null, ready = null;
    const listeners = [];
    const tracked = new Map();          // "match_id|selection" -> row
    let slips = [];                     // tracked_slips rows, newest first
    let slipsError = null;              // set when the table can't be read (e.g. 002 not run yet)

    function loadSdk() {
        if (window.supabase && window.supabase.createClient) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = SDK; s.async = true;
            s.onload = resolve; s.onerror = () => reject(new Error("sign-in library failed to load"));
            document.head.appendChild(s);
        });
    }

    async function init() {
        await loadSdk();
        client = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
            auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
        });
        const { data } = await client.auth.getSession();
        user = data.session ? data.session.user : null;
        client.auth.onAuthStateChange((_event, session) => {
            const was = user && user.id;
            user = session ? session.user : null;
            if ((user && user.id) !== was) refreshTracked().finally(notify);
        });
        await refreshTracked();
        notify();
    }

    function notify() {
        renderAccountButtons();
        listeners.forEach(fn => { try { fn(user); } catch (e) { console.error(e); } });
    }

    async function refreshTracked() {
        tracked.clear();
        slips = []; slipsError = null;
        if (!client || !user) return;
        const [picks, slipRes] = await Promise.all([
            client.from("tracked_picks").select("*").order("created_at", { ascending: false }),
            client.from("tracked_slips").select("*").order("created_at", { ascending: false }),
        ]);
        if (picks.error) console.error("tracked_picks:", picks.error.message);
        else (picks.data || []).forEach(r => tracked.set(`${r.match_id}|${r.selection}`, r));
        // Kept separate so a missing slips table can't take single picks down with it.
        if (slipRes.error) { slipsError = slipRes.error.message; console.error("tracked_slips:", slipsError); }
        else slips = slipRes.data || [];
    }

    // ── Account button in the nav (desktop link row + mobile brand bar) ──────
    function renderAccountButtons() {
        const label = user ? "My Record" : "Sign in";
        const title = user ? `Signed in as ${user.email || "you"} — your tracked picks` : "Sign in to track picks";
        document.querySelectorAll(".pp-account-btn").forEach(a => {
            a.querySelector(".pp-account-label").textContent = label;
            a.title = title;
        });
    }
    function makeAccountLink() {
        const a = document.createElement("a");
        a.href = "account.html";
        a.className = "pp-account-btn";
        a.innerHTML = `<span class="material-symbols-outlined" aria-hidden="true">account_circle</span><span class="pp-account-label">Sign in</span>`;
        return a;
    }
    function injectButtons() {
        const style = document.createElement("style");
        style.textContent = `
          .pp-account-btn { display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:600;
            color:#45464d; padding:6px 10px; border:1px solid #c6c6cd; border-radius:9999px; margin-left:12px;
            white-space:nowrap; text-decoration:none; min-height:36px; }
          .pp-account-btn .material-symbols-outlined { font-size:18px; }
          .pp-account-btn:hover { color:#416900; border-color:#416900; }
          html.dark .pp-account-btn { color:#c1c7ce; border-color:#42474a; }
          html.dark .pp-account-btn:hover { color:#91db2a; border-color:#91db2a; }
          .pp-track-btn[aria-pressed="true"] { background:#acf847; color:#457000; border-color:#acf847; }`;
        document.head.appendChild(style);
        const topNav = document.querySelector("nav[class*='md:flex']");
        const linkRow = topNav && topNav.lastElementChild;
        if (linkRow && !linkRow.querySelector(".pp-account-btn")) linkRow.appendChild(makeAccountLink());
        const brandBar = [...document.querySelectorAll("header")].find(h => h.classList.contains("md:hidden"));
        if (brandBar && !brandBar.querySelector(".pp-account-btn")) {
            const toggle = brandBar.querySelector(".theme-toggle");
            const link = makeAccountLink();
            if (toggle) brandBar.insertBefore(link, toggle); else brandBar.appendChild(link);
        }
        renderAccountButtons();
    }

    // ── Web push (worker/push.js sends; db/supabase/003_push.sql stores) ────
    // Public half of the Worker's VAPID key (worker/wrangler.toml). Not a secret.
    const VAPID_PUBLIC_KEY = "BF66BNM2qZtaEP5NjU53DrKo4bhuevSs2h6XIoT8HWzGbMSGr4eDSvPciibRc3unS8ZhMnKFxmoV88RQi-FjFoY";
    const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    const keyBytes = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
    async function currentSub() {
        if (!pushSupported()) return null;
        const reg = await navigator.serviceWorker.getRegistration();
        return reg ? reg.pushManager.getSubscription() : null;
    }
    async function pushOff() {
        const sub = await currentSub();
        if (!sub) return;
        if (client && user) await client.from("push_subscriptions").delete().eq("endpoint", sub.endpoint);
        await sub.unsubscribe();
    }

    // ── Public API ───────────────────────────────────────────────────────────
    window.PPAuth = {
        ready: () => ready,
        user: () => user,
        onChange: fn => { listeners.push(fn); },
        signInWithGoogle: async () => {
            await ready;
            // Keep the query (?next=...) so a Track tap resumes where it started.
            const back = location.origin + location.pathname + location.search;
            const { error } = await client.auth.signInWithOAuth({ provider: "google", options: { redirectTo: back } });
            if (error) throw error;
        },
        // A shared phone shouldn't keep getting the last user's alerts.
        signOut: async () => { await ready; await pushOff().catch(() => {}); await client.auth.signOut(); },
        isTracked: (matchId, selection) => tracked.has(`${matchId}|${selection}`),
        trackedRows: () => [...tracked.values()],
        track: async (pick) => {
            await ready;
            if (!user) throw Object.assign(new Error("Sign in to track picks"), { needsSignIn: true });
            const row = {
                match_id: pick.match_id, home_team: pick.home_team, away_team: pick.away_team,
                league: pick.league_display || pick.league || null, kickoff_utc: pick.kickoff_utc || null,
                selection: pick.best_bet, model_prob: pick.best_bet_prob ?? null,
                odds: pick.book_odds && pick.book_odds > 1 ? pick.book_odds : null,
            };
            const { data, error } = await client.from("tracked_picks").insert(row).select().single();
            if (error) throw error;
            tracked.set(`${data.match_id}|${data.selection}`, data);
            notify();
            return data;
        },
        untrack: async (matchId, selection) => {
            await ready;
            const row = tracked.get(`${matchId}|${selection}`);
            if (!row) return;
            const { error } = await client.from("tracked_picks").delete().eq("id", row.id);
            if (error) throw error;
            tracked.delete(`${matchId}|${selection}`);
            notify();
        },
        setStake: async (id, stake) => {
            await ready;
            const { data, error } = await client.from("tracked_picks")
                .update({ stake: stake === "" || stake == null ? null : Number(stake) }).eq("id", id).select().single();
            if (error) throw error;
            tracked.set(`${data.match_id}|${data.selection}`, data);
            return data;
        },
        // ── Accumulators: one slip = one bet = one row (002_tracked_slips.sql) ──
        slips: () => slips,
        slipsError: () => slipsError,
        // legs: [{match_id, home_team, away_team, league, kickoff_utc, market,
        //         selection, probability, odds}]; odds = SportyBet price or null.
        trackSlip: async (legs, source) => {
            await ready;
            if (!user) throw Object.assign(new Error("Sign in to track slips"), { needsSignIn: true });
            const clean = legs.map(l => ({
                match_id: l.match_id, home_team: l.home_team, away_team: l.away_team,
                league: l.league_display || l.league || null, kickoff_utc: l.kickoff_utc || null,
                market: l.market, selection: l.selection,
                probability: l.probability ?? null,
                odds: l.odds && l.odds > 1 ? Number(l.odds) : null,
            }));
            // A total only when EVERY leg is priced: a product over some legs
            // would understate the slip's real odds and overstate nothing useful.
            const total = clean.every(l => l.odds)
                ? Math.round(clean.reduce((t, l) => t * l.odds, 1) * 100) / 100 : null;
            const { data, error } = await client.from("tracked_slips")
                .insert({ source, legs: clean, total_odds: total }).select().single();
            if (error) throw error;
            slips = [data, ...slips];
            notify();
            return data;
        },
        untrackSlip: async (id) => {
            await ready;
            const { error } = await client.from("tracked_slips").delete().eq("id", id);
            if (error) throw error;
            slips = slips.filter(s => s.id !== id);
            notify();
        },
        setSlipStake: async (id, stake) => {
            await ready;
            const { data, error } = await client.from("tracked_slips")
                .update({ stake: stake === "" || stake == null ? null : Number(stake) }).eq("id", id).select().single();
            if (error) throw error;
            slips = slips.map(s => s.id === id ? data : s);
            return data;
        },
        push: {
            // "unsupported" (e.g. iPhone outside an installed app) | "denied" | "on" | "off"
            state: async () => {
                if (!pushSupported()) return "unsupported";
                if (Notification.permission === "denied") return "denied";
                return (await currentSub()) ? "on" : "off";
            },
            // Must run from a click: browsers block permission prompts that aren't.
            enable: async () => {
                await ready;
                if (!user) throw Object.assign(new Error("Sign in first"), { needsSignIn: true });
                if (await Notification.requestPermission() !== "granted") throw new Error("permission");
                const reg = await navigator.serviceWorker.ready;
                const sub = await reg.pushManager.getSubscription()
                    || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(VAPID_PUBLIC_KEY) });
                const k = sub.toJSON().keys;
                const { error } = await client.from("push_subscriptions")
                    .upsert({ endpoint: sub.endpoint, p256dh: k.p256dh, auth: k.auth }, { onConflict: "user_id,endpoint" });
                if (error) { await sub.unsubscribe(); throw error; }
            },
            disable: async () => { await ready; await pushOff(); },
        },
        deleteAccount: async () => {
            await ready;
            const { error } = await client.rpc("delete_my_account");
            if (error) throw error;
            await client.auth.signOut();
        },
    };

    // ── "Track this slip" (Builder + Checker share it) ───────────────────────
    // Signed out, the slip is parked in sessionStorage and saved by
    // account.html straight after Google sign-in, so signing in doesn't
    // throw away the slip the user just built.
    window.ppTrackSlipBlock = () => `
      <div class="mt-sm pt-sm border-t border-outline-variant flex flex-wrap items-center gap-sm">
        <button type="button" id="track-slip-btn" class="inline-flex items-center gap-xs px-md py-sm rounded-full border border-outline-variant text-label-sm font-bold text-on-surface-variant hover:border-secondary">
          <span class="material-symbols-outlined text-[18px]" aria-hidden="true">bookmark_add</span>Track this slip in My Record</button>
        <span id="track-slip-msg" class="text-label-sm text-on-surface-variant" aria-live="polite">Graded as one bet: it wins only if every leg does.</span>
      </div>`;
    window.ppWireTrackSlip = (getLegs, source, { fetchOdds = false } = {}) => {
        const btn = document.getElementById("track-slip-btn");
        const msg = document.getElementById("track-slip-msg");
        if (!btn || btn.dataset.wired) return;
        btn.dataset.wired = "1";
        btn.addEventListener("click", async () => {
            const legs = getLegs();
            if (!legs.length) return;
            btn.disabled = true;
            msg.textContent = "Saving…";
            try {
                await ready;
                if (fetchOdds && window.PotentialProAPI) {
                    try {
                        const o = await PotentialProAPI.slipOdds(legs);
                        legs.forEach((l, i) => { l.odds = (o.odds || [])[i] || null; });
                    } catch (_) { /* odds are optional; the slip still tracks */ }
                }
                if (!user) {
                    try { sessionStorage.setItem("pp-pending-slip", JSON.stringify({ legs, source })); } catch (_) {}
                    location.href = "account.html";
                    return;
                }
                await window.PPAuth.trackSlip(legs, source);
                btn.setAttribute("aria-pressed", "true");
                btn.classList.add("pp-track-btn");
                btn.lastChild.textContent = "Tracked";
                msg.innerHTML = `Saved. <a href="account.html" class="text-secondary font-semibold underline underline-offset-2">See it in My Record</a>`;
            } catch (err) {
                console.error(err);
                btn.disabled = false;
                msg.textContent = "Couldn't save this slip. Please try again.";
            }
        });
    };

    ready = init().catch(err => {
        console.warn("PPAuth unavailable:", err.message);
        document.querySelectorAll(".pp-account-label").forEach(l => { l.textContent = "Sign-in unavailable"; });
    });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", injectButtons);
    else injectButtons();
})();
