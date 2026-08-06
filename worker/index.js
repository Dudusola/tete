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
 *
 * VARS (plain config, safe to commit — see wrangler.toml):
 *   REPO             "owner/name"
 *   FEED_PATH        "feed/bot_feed.json"
 */

const HELP = [
  "*PotentialPro* ⚽",
  "",
  "/picks — today's HIGH and MEDIUM confidence picks",
  "/stats — honest track record on scored picks",
  "/help — this message",
  "",
  "_Predictions are model output, not guarantees. Bet responsibly._",
].join("\n");

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

async function sendMessage(env, chatId, text) {
  for (const part of splitMessage(text)) {
    await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: part,
        parse_mode: "Markdown",
        disable_web_page_preview: true,
      }),
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
  const cmd = text.trim().split(/\s+/)[0].split("@")[0].toLowerCase();

  if (cmd === "/start" || cmd === "/help") {
    return sendMessage(env, chatId, HELP);
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
    return sendMessage(env, chatId, body + staleness(feed));
  }

  return sendMessage(env, chatId, "Unknown command. Try /help");
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("PotentialPro webhook is up.", { status: 200 });
    }

    // Telegram echoes the secret we registered with setWebhook. Without this
    // check, anyone who guessed the URL could puppet the bot.
    if (
      env.WEBHOOK_SECRET &&
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

    const msg = update.message || update.edited_message;
    const text = msg?.text;
    const chatId = msg?.chat?.id;

    // Always 200: a non-2xx makes Telegram retry the same update repeatedly.
    if (!text || !chatId) return new Response("ok");

    try {
      await handleCommand(env, chatId, text);
    } catch (err) {
      console.log(`handler error: ${err}`);
    }
    return new Response("ok");
  },
};
