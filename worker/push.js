/**
 * Web push for tracked picks and slips (21 Oct 2026, roadmap item 3).
 *
 * Runs on the Worker's existing 10-minute cron. Reads push subscriptions and
 * tracked picks/slips from Supabase with the SERVICE key (bypasses row-level
 * security - it has to read every user's rows), and grades against the SAME
 * API endpoints My Record uses, so a push can never disagree with the page.
 *
 * Two notifications, each sent at most once per pick/slip (push_sent table):
 *   kickoff  - a tracked single pick kicks off within the next hour
 *   morning  - once a day, ~07:00 Lagos, when today's picks are out: how many,
 *              the strongest one, and the user's starred leagues on Matches
 *   price    - the bookmaker's price on a tracked pick has moved against it
 *              before kickoff (the same "price moved" warning the Picks card
 *              shows; past warned picks won less often than claimed)
 *   settled  - a tracked pick has a final score, or a tracked slip is decided
 *              (any leg lost, or every leg won/void)
 *
 * No library: VAPID (RFC 8292) is an ES256 JWT and the payload encryption
 * (RFC 8291, aes128gcm) is ~40 lines of WebCrypto. push.test.mjs checks the
 * encryption against the RFC's own test vector.
 *
 * SECRETS: VAPID_PRIVATE_KEY (the key's "d", base64url), SUPABASE_SERVICE_KEY.
 * VARS: VAPID_PUBLIC_KEY, SUPABASE_URL (wrangler.toml).
 *
 * THE TRAP (CLAUDE.md item 7): subscriptions expire silently. A 404/410 from
 * the push service deletes the subscription, and every run logs its counts,
 * so "0 delivered" is visible rather than looking like a quiet day.
 */

const enc = new TextEncoder();

export const b64u = {
  enc: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  dec: (s) => Uint8Array.from(
    atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)),
    (c) => c.charCodeAt(0)),
};

const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
};

// P-256 private key as JWK from its raw d + the 65-byte uncompressed public key.
const ecJwk = (d, pub) => ({
  kty: "EC", crv: "P-256", ext: true,
  x: b64u.enc(pub.slice(1, 33)), y: b64u.enc(pub.slice(33, 65)), d,
});

async function hkdf(salt, ikm, info, len) {
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt, info }, key, len * 8));
}

/* RFC 8291 aes128gcm body. `fixed` ({salt, asPrivate: d, asPublic}) exists only
   for the test vector; production generates a fresh key pair and salt per message. */
export async function encrypt(payload, p256dh, authSecret, fixed) {
  const uaPublic = b64u.dec(p256dh);
  let asKeys, asPublic, salt;
  if (fixed) {
    asPublic = b64u.dec(fixed.asPublic);
    asKeys = { privateKey: await crypto.subtle.importKey(
      "jwk", ecJwk(fixed.asPrivate, asPublic), { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]) };
    salt = b64u.dec(fixed.salt);
  } else {
    asKeys = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    asPublic = new Uint8Array(await crypto.subtle.exportKey("raw", asKeys.publicKey));
    salt = crypto.getRandomValues(new Uint8Array(16));
  }
  const uaKey = await crypto.subtle.importKey("raw", uaPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, asKeys.privateKey, 256));
  const ikm = await hkdf(b64u.dec(authSecret), ecdh,
                         concat(enc.encode("WebPush: info\0"), uaPublic, asPublic), 32);
  const cek = await hkdf(salt, ikm, enc.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(salt, ikm, enc.encode("Content-Encoding: nonce\0"), 12);
  const key = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const cipher = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce }, key, concat(enc.encode(payload), new Uint8Array([2]))));
  // header: salt | record size 4096 | key id length 65 | as_public
  return concat(salt, new Uint8Array([0, 0, 16, 0, 65]), asPublic, cipher);
}

export async function vapidJwt(audience, env, now = Date.now()) {
  const pub = b64u.dec(env.VAPID_PUBLIC_KEY);
  const key = await crypto.subtle.importKey(
    "jwk", ecJwk(env.VAPID_PRIVATE_KEY, pub), { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const part = (o) => b64u.enc(enc.encode(JSON.stringify(o)));
  const unsigned = `${part({ typ: "JWT", alg: "ES256" })}.${part({
    aud: audience, exp: Math.floor(now / 1000) + 12 * 3600, sub: "mailto:potentialproteam@gmail.com" })}`;
  // WebCrypto's ECDSA signature is already r||s, the form JWS wants.
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, enc.encode(unsigned));
  return `${unsigned}.${b64u.enc(sig)}`;
}

/* One push. Returns the push service's HTTP status (201 = accepted). */
export async function sendPush(env, sub, message) {
  const body = await encrypt(JSON.stringify(message), sub.p256dh, sub.auth);
  const jwt = await vapidJwt(new URL(sub.endpoint).origin, env);
  const res = await fetch(sub.endpoint, {
    method: "POST",
    headers: {
      Authorization: `vapid t=${jwt}, k=${env.VAPID_PUBLIC_KEY}`,
      "Content-Encoding": "aes128gcm",
      "Content-Type": "application/octet-stream",
      TTL: "3600",   // a kickoff alert an hour late is worse than none
      Urgency: "normal",
    },
    body,
  });
  return res.status;
}

// ── Supabase REST with the service key ────────────────────────────────────
async function sb(env, path, init = {}) {
  const key = env.SUPABASE_SERVICE_KEY;
  const headers = { apikey: key, "Content-Type": "application/json", ...(init.headers || {}) };
  // Legacy service_role keys are JWTs and also go in Authorization; new sb_secret_ keys don't.
  if (key.startsWith("eyJ")) headers.Authorization = `Bearer ${key}`;
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`supabase ${path.split("?")[0]}: HTTP ${res.status} ${await res.text()}`);
  const text = await res.text();   // inserts/deletes answer with an empty body
  return text ? JSON.parse(text) : null;
}

async function api(env, path, body) {
  const res = await fetch(`${env.API_BASE}${path}`, body === undefined
    ? { signal: AbortSignal.timeout(30000) }
    : { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

const OUTCOME = { "Home Win": "H", "Draw": "D", "Away Win": "A" };
const kickoffMs = (k) => (k ? Date.parse(k.replace(" ", "T") + ":00Z") : NaN);
const fmt = (ms) => new Date(ms).toISOString().slice(0, 16).replace("T", " ");

/* Same rule as account.html: an NHL/NFL moneyline on a tie is VOID, football keeps its Draw. */
export function pickStatus(pick, res) {
  if (!res) return "pending";
  if (res.outcome === "D" && /^(NFL|NHL)$/.test(pick.league || "") && pick.selection !== "Draw") return "void";
  return res.outcome === OUTCOME[pick.selection] ? "won" : "lost";
}

/* A slip is decided as soon as any leg loses; otherwise only when every leg is won or void. */
export function slipStatus(legStatuses) {
  if (legStatuses.includes("lost")) return "lost";
  if (legStatuses.length && legStatuses.every((s) => s === "won" || s === "void")) {
    return legStatuses.every((s) => s === "void") ? "void" : "won";
  }
  return "pending";
}

/* Price alerts for tracked picks that haven't kicked off. `served` maps
   match_id -> {selection, warnings} from the Picks feeds; a warning is about
   OUR pick, so it only applies when the user tracked that same side. */
export function priceAlerts(picks, served, now, fresh) {
  const out = [];
  for (const p of picks) {
    const s = served.get(p.match_id);
    const w = s && s.selection === p.selection && (s.warnings || []).find((x) => x.type === "price");
    if (w && kickoffMs(p.kickoff_utc) > now && fresh(p.user_id, "price", p.id)) {
      out.push({ user_id: p.user_id, kind: "price", ref: p.id,
                 title: "Price moved against your pick",
                 body: `${p.home_team} v ${p.away_team} - ${p.selection}. ${w.text}.` });
    }
  }
  return out;
}

/* Settings (db/supabase/005_push_prefs.sql). No row = everything on, no leagues. */
export const PREF_DEFAULTS = { morning: true, leagues_on: true, kickoff: true, price: true, results: true, leagues: [] };
const PREF_OF_KIND = { kickoff: "kickoff", price: "price", settled: "results" };
export const wants = (prefs, kind) => !PREF_OF_KIND[kind] || prefs[PREF_OF_KIND[kind]] !== false;

// Lagos is UTC+1 all year (no daylight saving).
const LAGOS_MS = 3600e3;
export const lagosDay = (ms) => new Date(ms + LAGOS_MS).toISOString().slice(0, 10);
// push_sent.ref is a uuid: one per user per Lagos day for the morning alert.
export const dayRef = (day) => `00000000-0000-0000-0000-0000${day.replace(/-/g, "")}`;

/* The morning alert for one user. `today` = served football picks kicking off
   later today (Lagos); `leagueToday` = league -> {name, n, picks} for today's
   matches. Returns null when there is nothing worth a buzz. */
export function morningMessage(user_id, prefs, today, leagueToday, day) {
  const parts = [];
  let title = null;
  if (prefs.morning && today.length) {
    const high = today.filter((p) => p.confidence === "HIGH").length;
    const top = today.reduce((a, b) => ((b.best_bet_prob || 0) > (a.best_bet_prob || 0) ? b : a));
    const team = top.best_bet === "Home Win" ? top.home_team : top.best_bet === "Away Win" ? top.away_team : null;
    title = "Today's picks are out";
    parts.push(`${today.length} ${today.length === 1 ? "pick" : "picks"} today` +
      (high ? `, ${high} high confidence` : "") + "." +
      (team ? ` Strongest: ${team} to win (${Math.round(top.best_bet_prob * 100)}%).` : ""));
  }
  if (prefs.leagues_on && prefs.leagues.length) {
    const mine = prefs.leagues.map((lg) => leagueToday.get(lg)).filter(Boolean);
    if (mine.length) {
      const bits = mine.slice(0, 3).map((x) => `${x.name} ${x.n} ${x.n === 1 ? "match" : "matches"}` +
        (x.picks ? `, ${x.picks} ${x.picks === 1 ? "pick" : "picks"}` : ""));
      parts.push(`Your leagues: ${bits.join("; ")}${mine.length > 3 ? ` +${mine.length - 3} more` : ""}.`);
      title = title || "Your leagues today";
    }
  }
  if (!title) return null;
  return { user_id, kind: "morning", ref: dayRef(day), title, body: parts.join(" "),
           url: title === "Your leagues today" ? "index.html" : "picks.html" };
}

const VERDICT = { won: "Won ✅", lost: "Lost ❌", void: "Void (stake back)" };

/* Build {user_id, kind, ref, title, body} for everything due now. */
async function dueMessages(env, users, now) {
  const inUsers = `user_id=in.(${users.join(",")})`;
  const [picks, slips, sent, prefRows] = await Promise.all([
    // settle window (kicked off in the last 3 days) up to the price-alert window (next 24 h)
    sb(env, `tracked_picks?select=id,user_id,match_id,home_team,away_team,league,kickoff_utc,selection` +
            `&${inUsers}&kickoff_utc=gte.${encodeURIComponent(fmt(now - 3 * 864e5))}` +
            `&kickoff_utc=lte.${encodeURIComponent(fmt(now + 24 * 3600e3))}`),
    sb(env, `tracked_slips?select=id,user_id,legs&${inUsers}` +
            `&created_at=gte.${new Date(now - 10 * 864e5).toISOString()}`),
    sb(env, `push_sent?select=user_id,kind,ref&${inUsers}` +
            `&sent_at=gte.${new Date(now - 14 * 864e5).toISOString()}`),
    // Missing table (005 not run yet) = everyone on defaults, never a failed run.
    sb(env, `push_prefs?select=*&${inUsers}`).catch(() => []),
  ]);
  const prefs = new Map(users.map((u) => [u, { ...PREF_DEFAULTS }]));
  for (const r of prefRows) prefs.set(r.user_id, { ...PREF_DEFAULTS, ...r, leagues: r.leagues || [] });
  const done = new Set(sent.map((s) => `${s.user_id}|${s.kind}|${s.ref}`));
  const fresh = (user_id, kind, ref) => !done.has(`${user_id}|${kind}|${ref}`);
  const out = [];

  for (const p of picks) {
    const mins = Math.round((kickoffMs(p.kickoff_utc) - now) / 60000);
    if (mins > 0 && mins <= 60 && fresh(p.user_id, "kickoff", p.id)) {
      out.push({ user_id: p.user_id, kind: "kickoff", ref: p.id,
                 title: `Kicks off in ${mins} min`,
                 body: `${p.home_team} v ${p.away_team} - your pick: ${p.selection}` });
    }
  }

  // The Picks feed is fetched at most once a run, and only when something needs it.
  let fbPicks = null;
  const footballPicks = async () => (fbPicks = fbPicks || await api(env, "/api/picks"));

  // Morning: 07:00-12:00 Lagos, once the pipeline has published today's picks.
  const day = lagosDay(now), hour = new Date(now + LAGOS_MS).getUTCHours();
  const morningUsers = users.filter((u) => fresh(u, "morning", dayRef(day)) &&
    (prefs.get(u).morning || (prefs.get(u).leagues_on && prefs.get(u).leagues.length)));
  if (hour >= 7 && hour < 12 && morningUsers.length) {
    const fb = await footballPicks();
    // generated_at is when the pipeline last wrote predictions (UTC). Before
    // today's run it is yesterday's, and "today's picks are out" would be false.
    if ((fb.generated_at || "").slice(0, 10) >= new Date(now).toISOString().slice(0, 10)) {
      const today = fb.picks.filter((p) => p.kickoff_utc && lagosDay(kickoffMs(p.kickoff_utc)) === day
        && kickoffMs(p.kickoff_utc) > now && ["HIGH", "MEDIUM"].includes(p.confidence));
      const leagueToday = new Map();
      if (morningUsers.some((u) => prefs.get(u).leagues_on && prefs.get(u).leagues.length)) {
        for (const m of (await api(env, "/api/matches")).matches) {
          if (!m.kickoff_utc || lagosDay(kickoffMs(m.kickoff_utc)) !== day) continue;
          const x = leagueToday.get(m.league) || { name: m.league_display || m.league, n: 0, picks: 0 };
          x.n++;
          if (["HIGH", "MEDIUM"].includes(m.confidence)) x.picks++;
          leagueToday.set(m.league, x);
        }
      }
      for (const u of morningUsers) {
        const msg = morningMessage(u, prefs.get(u), today, leagueToday, day);
        if (msg) out.push(msg);
      }
    }
  }

  const upcoming = picks.filter((p) => kickoffMs(p.kickoff_utc) > now && fresh(p.user_id, "price", p.id));
  if (upcoming.length) {
    // Two cached GETs (the Picks feeds), only when someone has an upcoming tracked pick.
    const [fb, us] = await Promise.all([footballPicks(), api(env, "/api/picks/sports?sports=nhl,nfl,nba")]);
    const served = new Map([
      ...fb.picks.map((x) => [x.match_id, { selection: x.best_bet, warnings: x.warnings }]),
      ...us.picks.map((x) => [x.match_id, { selection: x.track_selection, warnings: x.warnings }]),
    ]);
    out.push(...priceAlerts(upcoming, served, now, fresh));
  }

  // Settle only what has kicked off (90 min ago at least) and isn't announced yet.
  const toSettle = picks.filter((p) => kickoffMs(p.kickoff_utc) < now - 90 * 60e3 && fresh(p.user_id, "settled", p.id));
  if (toSettle.length) {
    const { results } = await api(env, "/api/results/lookup", { match_ids: [...new Set(toSettle.map((p) => p.match_id))] });
    for (const p of toSettle) {
      const r = results[p.match_id];
      const st = pickStatus(p, r);
      if (st === "pending") continue;
      out.push({ user_id: p.user_id, kind: "settled", ref: p.id,
                 title: `${VERDICT[st]}: your pick ${p.selection}`,
                 body: `${p.home_team} ${r.home_goals}-${r.away_goals} ${p.away_team}` });
    }
  }

  const openSlips = slips.filter((s) => fresh(s.user_id, "settled", s.id)
    && s.legs.some((l) => !(kickoffMs(l.kickoff_utc) >= now - 90 * 60e3)));  // no kickoff stored: ask anyway
  if (openSlips.length) {
    const legs = openSlips.flatMap((s) => s.legs);
    // shortcut: one settle call caps at 40 legs (api/security.py); fine for a handful of testers, batch when slips grow.
    const { legs: graded } = await api(env, "/api/slip/settle", { legs: legs.slice(0, 40) });
    let i = 0;
    for (const s of openSlips) {
      const st = graded.slice(i, i + s.legs.length).map((g) => g.status);
      i += s.legs.length;
      if (st.length < s.legs.length) break;   // beyond the 40-leg cap: next run
      const verdict = slipStatus(st);
      if (verdict === "pending") continue;
      const won = st.filter((x) => x === "won").length;
      out.push({ user_id: s.user_id, kind: "settled", ref: s.id,
                 title: `${VERDICT[verdict]}: your ${s.legs.length}-leg slip`,
                 body: `${won} of ${s.legs.length} legs won` +
                       (verdict === "lost" ? " - settled as soon as one leg lost." : ".") });
    }
  }
  // Settings: drop the kinds this user switched off (morning was decided above).
  return out.filter((m) => wants(prefs.get(m.user_id) || PREF_DEFAULTS, m.kind));
}

export async function runPush(env) {
  if (!env.SUPABASE_SERVICE_KEY || !env.VAPID_PRIVATE_KEY) {
    console.log("push: SUPABASE_SERVICE_KEY or VAPID_PRIVATE_KEY not set - skipping");
    return;
  }
  const subs = await sb(env, "push_subscriptions?select=id,user_id,endpoint,p256dh,auth");
  if (!subs.length) return;
  const now = Date.now();
  const messages = await dueMessages(env, [...new Set(subs.map((s) => s.user_id))], now);

  // Free Workers allow 50 subrequests per invocation; ~10 are used above.
  // Anything over budget simply stays unsent and goes out on the next run.
  let budget = 40, delivered = 0, failed = 0, expired = 0;
  const sentRows = [];
  // Kinds added after 003 are recorded BEFORE sending, one row each: a database
  // that has not had their migration run (004 price, 005 morning) rejects the
  // row, and the alert is skipped - never re-sent every 10 minutes, and never
  // able to sink the batch insert of kickoff/settled rows below.
  const PRE_RECORD = new Set(["price", "morning"]);
  const blocked = new Set();
  for (const m of messages) {
    const mine = subs.filter((s) => s.user_id === m.user_id);
    if (mine.length + 1 > budget) break;
    if (PRE_RECORD.has(m.kind)) {
      if (blocked.has(m.kind)) continue;
      budget--;
      try {
        await sb(env, "push_sent", { method: "POST", body: JSON.stringify([{ user_id: m.user_id, kind: m.kind, ref: m.ref }]),
                                     headers: { Prefer: "resolution=ignore-duplicates,return=minimal" } });
      } catch (err) {
        blocked.add(m.kind);
        console.log(`push: ${m.kind} alerts off - ${err.message.slice(0, 160)}`);
        continue;
      }
    }
    for (const s of mine) {
      budget--;
      try {
        const status = await sendPush(env, s, { title: m.title, body: m.body, tag: `${m.kind}-${m.ref}`, url: m.url || "account.html" });
        if (status === 404 || status === 410) {
          expired++;
          await sb(env, `push_subscriptions?id=eq.${s.id}`, { method: "DELETE" });
          budget--;
        } else if (status >= 200 && status < 300) delivered++;
        else { failed++; console.log(`push: ${new URL(s.endpoint).host} answered ${status}`); }
      } catch (err) { failed++; console.log(`push: send error ${err}`); }
    }
    // Marked sent even if a device failed: retrying a broken endpoint every
    // 10 minutes would spam the working ones the user also has.
    if (!PRE_RECORD.has(m.kind)) sentRows.push({ user_id: m.user_id, kind: m.kind, ref: m.ref });
  }
  if (sentRows.length) {
    await sb(env, "push_sent", { method: "POST", body: JSON.stringify(sentRows),
                                 headers: { Prefer: "resolution=ignore-duplicates,return=minimal" } });
  }
  console.log(`push: ${subs.length} subscriptions, ${messages.length} due, ` +
              `${delivered} delivered, ${failed} failed, ${expired} expired+removed`);
}
