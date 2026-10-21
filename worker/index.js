/**
 * PotentialPro Telegram webhook — always-on interactive commands.
 *
 * WHY A WEBHOOK: the polling bot (bot/telegram_bot.py) needs a process running
 * 24/7. That process lived on a laptop that was frequently off, so /picks and
 * /stats worked only when someone had started it by hand. Telegram webhooks
 * invert the model: Telegram POSTs each message here, and this only has to
 * answer for a few milliseconds. Cloudflare Workers run that free, always-on.
 *
 * DATA: this Worker holds no database. The daily pipeline publishes
 * feed/bot_feed.json to the repo and this fetches it via the GitHub Contents
 * API. All numbers are computed by the pipeline — nothing is recalculated
 * here, so the bot can never disagree with the ledger.
 *
 * SECRETS (set with `wrangler secret put <NAME>`, never in this file or
 * wrangler.toml — that file is committed):
 *   BOT_TOKEN        Telegram bot token
 *   GITHUB_TOKEN     fine-grained PAT, read-only Contents on the repo
 *   WEBHOOK_SECRET   random string; Telegram echoes it back in a header so we
 *                    can reject forged requests (the URL alone is not a secret)
 *   DISPATCH_TOKEN   fine-grained PAT, Actions: Read and write on the repo.
 *                    Used ONLY by the cron below to start daily.yml. Separate
 *                    from GITHUB_TOKEN on purpose: the bot's token stays
 *                    read-only. Set via the deploy-worker.yml workflow.
 *   VAPID_PRIVATE_KEY, SUPABASE_SERVICE_KEY   web push - see push.js.
 *
 * CRON (wrangler.toml [triggers]): starts the daily pipeline on time.
 *   GitHub's own schedule for daily.yml ('50 8 * * *') was firing 4-6 HOURS
 *   late every day (19-26 Sep 2026: 12:41-15:20 UTC), so the day's picks and
 *   the Telegram push arrived mid-afternoon Lagos, after many kickoffs.
 *   Cloudflare cron triggers fire on time, so this asks GitHub to run the
 *   workflow NOW (workflow_dispatch). GitHub's schedule stays as a backup;
 *   daily.yml's guard job skips it if this already produced today's run.
 *
 * VARS (plain config, safe to commit — see wrangler.toml):
 *   REPO             "owner/name"
 *   FEED_PATH        "feed/bot_feed.json"
 */

import { runPush } from "./push.js";

const HELP = [
  "*PotentialPro* ⚽",
  "AI football predictions with an honest, published track record.",
  "",
  "*What you can do*",
  "",
  "📋 /picks — today's HIGH and MEDIUM confidence picks",
  "🎯 /slip — build an accumulator *with a ready booking code*",
  "📊 /stats — our real record on matches that have finished",
  "❓ /help — this message",
  "",
  "*Slip options* (both optional, any order)",
  "`/slip` → 3 games, MEDIUM risk",
  "`/slip 5` → 5 games",
  "`/slip 5 high` → 5 games, HIGH confidence only",
  "",
  "Or just tap a button below — no commands to remember.",
  "",
  "_Predictions are model output, not guarantees._",
  "_18+. Bet responsibly, and never stake what you can't afford to lose._",
].join("\n");

/* Buttons, so a new user never has to know the commands exist. Telegram sends
   callback_data back when one is tapped, and handleCommand treats it exactly
   like typed text — one code path, no duplicate command handling. */
const MAIN_KEYBOARD = {
  inline_keyboard: [
    [{ text: "📋 Today's picks", callback_data: "/picks" },
     { text: "📊 Track record", callback_data: "/stats" }],
    [{ text: "🎯 3-game slip", callback_data: "/slip 3" },
     { text: "🎯 5-game slip", callback_data: "/slip 5" }],
    [{ text: "🔒 5-game HIGH only", callback_data: "/slip 5 high" }],
  ],
};

const SLIP_KEYBOARD = {
  inline_keyboard: [
    [{ text: "🔁 Another 3", callback_data: "/slip 3" },
     { text: "🔁 Another 5", callback_data: "/slip 5" }],
    [{ text: "🔒 HIGH only", callback_data: "/slip 5 high" },
     { text: "📋 Picks", callback_data: "/picks" }],
  ],
};

// Telegram rejects anything over 4096 chars. Same limit the daily push hit.
const TELEGRAM_LIMIT = 3900;

function splitMessage(text) {
  if (text.length <= TELEGRAM_LIMIT) return [text];
  const chunks = [];
  let current = "";
  // Split between picks (blank lines) so Markdown is never cut mid-emphasis.
  for (const block of text.split("\n\n")) {
    const candidate = current ? `${current}\n\n${block}` : block;
    if (candidate.length > TELEGRAM_LIMIT && current) {
      chunks.push(current);
      current = block;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

async function sendMessage(env, chatId, text, keyboard) {
  const parts = splitMessage(text);
  for (let i = 0; i < parts.length; i++) {
    const body = {
      chat_id: chatId,
      text: parts[i],
      parse_mode: "Markdown",
      disable_web_page_preview: true,
    };
    // Buttons only on the LAST chunk, otherwise they'd appear mid-message.
    if (keyboard && i === parts.length - 1) body.reply_markup = keyboard;
    await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
}

async function loadFeed(env) {
  // Contents API works for private repos; raw.githubusercontent does not
  // without extra auth dancing. Returns base64 unless we ask for raw.
  const url = `https://api.github.com/repos/${env.REPO}/contents/${env.FEED_PATH}`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw",
      "User-Agent": "potentialpro-bot",
    },
    // The feed changes once a day; caching keeps us far inside GitHub's
    // rate limit even if someone spams /picks.
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!res.ok) throw new Error(`feed fetch failed: HTTP ${res.status}`);
  return await res.json();
}

/* /slip calls the real API rather than rebuilding a slip here.

   build_slip does actual work — dedupe, market derivation, back-fill when a leg
   is dropped — and the whole reason the API is hosted (rather than ported to
   JavaScript) is to avoid a second implementation drifting from the engine.
   The bot must not become that second implementation either. */
async function handleSlip(env, chatId, args) {
  // "/slip 5 high" — order-insensitive, both optional.
  let games = 3;
  let risk = "MEDIUM";
  for (const a of args) {
    const n = parseInt(a, 10);
    if (!isNaN(n) && n >= 2 && n <= 10) games = n;
    const up = a.toUpperCase();
    if (["HIGH", "MEDIUM", "LOW"].includes(up)) risk = up;
  }

  const url = `${env.API_BASE}/api/slip?risk=${risk}&games=${games}`;
  let data;
  try {
    // The API sleeps on the free tier and can take ~50s to wake, which is why
    // this is generous. Telegram has already had its 200 by now (see fetch()),
    // so a slow reply delays the message, it doesn't retry the update.
    const res = await fetch(url, { signal: AbortSignal.timeout(75000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    return sendMessage(
      env, chatId,
      "⚠️ Couldn't build a slip just now — the API may be waking up. " +
      "Give it a minute and try again."
    );
  }

  const legs = data.legs || data.slip || [];
  if (!legs.length) {
    return sendMessage(
      env, chatId,
      `📭 No ${risk} slip available — not enough qualifying matches left today.`
    );
  }

  const lines = [`🎯 *${legs.length}-game ${risk} slip*`, ""];
  for (const l of legs) {
    lines.push(`*${l.home_team} vs ${l.away_team}*`);
    // Field names verified against a live /api/slip response: the leg carries
    // sporty_odds (may be null when SportyBet doesn't price it) and probability.
    lines.push(`   ${l.market}: ${l.selection}` +
               (l.sporty_odds ? `  @ ${l.sporty_odds}` : "") +
               (l.probability ? `  (${Math.round(l.probability * 100)}%)` : ""));
    // league_display is the API's readable form of the label ("Russia First
    // League" rather than Forebet's "Fnl"). Telegram used to print the raw
    // code while the web app printed the real name — the same fixture
    // described two different ways depending on where you looked. Falls back
    // to the raw label so an older API response still renders something.
    const league = l.league_display || l.league;
    if (league) lines.push(`   _${league}_`);
    lines.push("");
  }
  if (data.sporty_total_odds) {
    lines.push(`Total odds: *${data.sporty_total_odds}*`);
  }
  if (data.accumulator_probability) {
    lines.push(`Model probability all land: *${Math.round(data.accumulator_probability * 100)}%*`);
  }

  // A slip without a booking code means the user has to retype every leg by
  // hand — the whole point is that they don't. Generated here so the code
  // arrives in the same message as the picks.
  try {
    const legPayload = legs.map((l) => ({
      home_team: l.home_team,
      away_team: l.away_team,
      market: l.market,
      selection: l.selection,
    }));
    const res = await fetch(`${env.API_BASE}/api/booking/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ legs: legPayload, provider: "sportybet" }),
      signal: AbortSignal.timeout(75000),
    });
    const book = await res.json();
    if (book.ok && book.code) {
      lines.push("");
      lines.push(`🎟 *SportyBet code:* \`${book.code}\``);
      lines.push("_Tap to copy, then paste into SportyBet → Book a Bet._");
      const missed = (book.unmapped || []).length;
      if (missed) {
        // Say so rather than letting the user discover a short slip at the
        // bookmaker: unmapped legs are ones SportyBet doesn't list.
        lines.push(`⚠️ _${missed} leg${missed > 1 ? "s" : ""} not on SportyBet — ` +
                   `the code has ${legs.length - missed} of ${legs.length} games._`);
      }
    } else {
      lines.push("");
      lines.push("_Booking code unavailable right now — the slip above is still valid._");
    }
  } catch (err) {
    lines.push("");
    lines.push("_Booking code timed out — try /slip again in a moment._");
  }

  lines.push("");
  lines.push("_More options on the web app: potentialpro.pages.dev_");

  return sendMessage(env, chatId, lines.join("\n"), SLIP_KEYBOARD);
}

function formatStats(feed) {
  const s = feed.stats || {};
  if (!s.total) {
    return "📊 No scored picks yet — the ledger is still filling up.";
  }
  return [
    "📊 *Track record* (HIGH + MEDIUM picks with a final score)",
    "",
    `Scored picks: *${s.total}*`,
    `Correct: *${s.correct}*`,
    `Accuracy: *${s.accuracy}%*`,
    "",
    "_Only matches with a verified final result are counted._",
  ].join("\n");
}

function staleness(feed) {
  if (!feed.generated_at) return "";
  const ageHours = (Date.now() - Date.parse(feed.generated_at)) / 3.6e6;
  // Say so rather than serving yesterday's picks as if they were today's —
  // silent staleness is how this project lost two weeks of ledger before.
  if (ageHours > 30) {
    return `\n\n⚠️ _Data is ${Math.round(ageHours)}h old — the daily pipeline may not have run._`;
  }
  return "";
}

async function handleCommand(env, chatId, text) {
  const parts = text.trim().split(/\s+/);
  const cmd = parts[0].split("@")[0].toLowerCase();

  if (cmd === "/slip") {
    return handleSlip(env, chatId, parts.slice(1));
  }

  if (cmd === "/start" || cmd === "/help") {
    return sendMessage(env, chatId, HELP, MAIN_KEYBOARD);
  }

  if (cmd === "/picks" || cmd === "/stats") {
    let feed;
    try {
      feed = await loadFeed(env);
    } catch (err) {
      return sendMessage(
        env,
        chatId,
        "⚠️ Couldn't reach the picks feed just now. Try again shortly."
      );
    }
    const body =
      cmd === "/picks"
        ? feed.picks_message || "📭 No picks published yet."
        : formatStats(feed);
    return sendMessage(env, chatId, body + staleness(feed), MAIN_KEYBOARD);
  }

  // Unknown input: show what IS possible rather than just rejecting it.
  return sendMessage(
    env, chatId,
    "I didn't understand that. Here's what I can do:\n\n" + HELP,
    MAIN_KEYBOARD
  );
}

/* Start a workflow via workflow_dispatch. GitHub answers 204 on success.
   Never throws: a failed trigger is logged, and GitHub's own (late) schedule
   still runs the workflow, so this degrades to the old behaviour rather than
   to no run at all. `inputs` must match what the workflow declares - GitHub
   rejects (422) inputs a workflow does not define, so closing.yml gets none. */
async function dispatchWorkflow(env, file, inputs) {
  if (!env.DISPATCH_TOKEN) {
    console.log(`cron: DISPATCH_TOKEN not set - leaving ${file} to GitHub's schedule`);
    return;
  }
  const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/${file}/dispatches`;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "potentialpro-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(inputs ? { ref: "main", inputs } : { ref: "main" }),
    });
    console.log(res.status === 204
      ? `cron: ${file} dispatched`
      : `cron: ${file} dispatch FAILED ${res.status} ${await res.text()}`);
  } catch (err) {
    console.log(`cron: ${file} dispatch error ${err}`);
  }
}

export default {
  async scheduled(controller, env, ctx) {
    // Three crons share this handler (wrangler.toml); controller.cron says which.
    if (controller.cron === "20 * * * *") {
      // closing.yml records the last pre-kickoff price (CLV) and every NHL /
      // NFL / MLB line change. Its GitHub schedule says hourly but ran every
      // 3-6 HOURS on 1-2 Oct 2026 (23:13, 02:16, 08:34, then nothing), so
      // closes were captured hours early or not at all - and ESPN deletes
      // odds once a game ends, so those are lost for good. Same fix as
      // daily.yml: Cloudflare's cron is on time, GitHub's stays as a backup
      // (repeat runs only store prices that changed).
      ctx.waitUntil(dispatchWorkflow(env, "closing.yml"));
      return;
    }
    if (controller.cron === "*/10 * * * *") {
      ctx.waitUntil(
        fetch(`${env.API_BASE}/healthz`, { headers: { "User-Agent": "potentialpro-keepwarm" } })
          .then((r) => { if (!r.ok) console.log(`keep-warm: healthz ${r.status}`); })
          .catch((err) => console.log(`keep-warm: ${err}`))
      );
      // Web push for tracked picks/slips rides the same 10-minute tick.
      ctx.waitUntil(runPush(env).catch((err) => console.log(`push: ${err}`)));
      return;
    }
    ctx.waitUntil(dispatchWorkflow(env, "daily.yml", { trigger: "worker-cron" }));
  },

  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("PotentialPro webhook is up.", { status: 200 });
    }

    // Telegram echoes the secret we registered with setWebhook. Without this
    // check, anyone who guessed the URL could puppet the bot.
    // Fail CLOSED: an unset secret used to skip the check entirely, so a
    // dropped secret would have opened the bot to anyone (verified set and
    // enforced - 403 - on 8 Oct 2026 before this was tightened).
    if (
      !env.WEBHOOK_SECRET ||
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET
    ) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }

    // A tapped button arrives as callback_query, not a message. Its
    // callback_data is a command string, so it routes through exactly the same
    // handler as typed text.
    const cb = update.callback_query;
    if (cb) {
      // Telegram shows a loading spinner on the button until this is answered.
      ctx.waitUntil(
        fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/answerCallbackQuery`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ callback_query_id: cb.id }),
        }).catch(() => {})
      );
      const cbChat = cb.message?.chat?.id;
      if (cbChat && cb.data) {
        ctx.waitUntil(
          handleCommand(env, cbChat, cb.data).catch((err) =>
            console.log(`callback error: ${err}`)
          )
        );
      }
      return new Response("ok");
    }

    const msg = update.message || update.edited_message;
    const text = msg?.text;
    const chatId = msg?.chat?.id;

    // Always 200: a non-2xx makes Telegram retry the same update repeatedly.
    if (!text || !chatId) return new Response("ok");

    // Answer Telegram NOW and do the work in the background. /slip calls the
    // API, which can take ~50s to wake on the free tier — longer than
    // Telegram's webhook timeout, which would make it retry the same update
    // and send the user duplicate slips.
    ctx.waitUntil(
      handleCommand(env, chatId, text).catch((err) =>
        console.log(`handler error: ${err}`)
      )
    );
    return new Response("ok");
  },
};
