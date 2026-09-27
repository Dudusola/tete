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
        if (!client || !user) return;
        const { data, error } = await client.from("tracked_picks")
            .select("*").order("created_at", { ascending: false });
        if (error) { console.error("tracked_picks:", error.message); return; }
        (data || []).forEach(r => tracked.set(`${r.match_id}|${r.selection}`, r));
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
            white-space:nowrap; text-decoration:none; }
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
        signOut: async () => { await ready; await client.auth.signOut(); },
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
        deleteAccount: async () => {
            await ready;
            const { error } = await client.rpc("delete_my_account");
            if (error) throw error;
            await client.auth.signOut();
        },
    };

    ready = init().catch(err => {
        console.warn("PPAuth unavailable:", err.message);
        document.querySelectorAll(".pp-account-label").forEach(l => { l.textContent = "Sign-in unavailable"; });
    });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", injectButtons);
    else injectButtons();
})();
