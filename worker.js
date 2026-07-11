/**
 * Bursa Screener — Telegram POLLING relay (NO webhook version)
 *
 * How it works:
 *   A Cron Trigger runs this Worker every minute. Each run, the Worker asks
 *   Telegram "any new messages for my bot?" (getUpdates). If it finds /run
 *   or /scan from YOUR chat id, it starts the GitHub Actions workflow.
 *
 *   You /run → (up to ~1 min later) Worker polls Telegram → sees /run
 *            → dispatches GitHub → GitHub runs main.py → results to Telegram
 *
 * No webhook, no setWebhook call, no secret path. The only moving parts are
 * the cron schedule and the variables below.
 *
 * IMPORTANT ONE-TIME STEP: if a webhook was EVER set on this bot, Telegram
 * refuses getUpdates until it's removed. Open this once in your browser:
 *   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/deleteWebhook
 * You should see {"ok":true,...,"description":"Webhook was deleted"}.
 *
 * Required variables (dashboard: Worker → Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN  (encrypt)  — your BotFather token
 *   GITHUB_TOKEN        (encrypt)  — CLASSIC PAT (ghp_...) with `workflow` scope
 *   ALLOWED_CHAT_ID     (encrypt)  — your Telegram chat id; only you can trigger
 *   GITHUB_OWNER        (plain)    — your GitHub username (from your repo URL)
 *   GITHUB_REPO         (plain)    — the repo name (from your repo URL)
 *   WORKFLOW_FILE       (plain)    — e.g. daily-screener.yml (filename only)
 *   GITHUB_BRANCH       (plain, optional) — defaults to "main"
 *
 * Cron Trigger (dashboard: Worker → Settings → Triggers → Cron Triggers):
 *   * * * * *        (every minute)
 */

export default {
  // Cron entry point — this is what actually does the work.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(pollAndHandle(env));
  },

  // Plain HTTP entry point — just a health check + manual poll for testing.
  // Visiting the worker URL in your browser forces one poll immediately,
  // so you don't have to wait for the next cron tick while testing.
  async fetch(request, env) {
    const result = await pollAndHandle(env);
    return new Response(
      "Bursa Screener polling relay is alive.\n" + result,
      { status: 200 }
    );
  },
};

/** Poll Telegram for new messages and handle any commands found. */
async function pollAndHandle(env) {
  const api = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}`;

  // 1. Fetch unconfirmed updates
  let updates;
  try {
    const res = await fetch(`${api}/getUpdates?timeout=0&allowed_updates=%5B%22message%22%5D`);
    const data = await res.json();
    if (!data.ok) {
      // Most common cause: a webhook is still set on this bot.
      console.log("getUpdates failed:", JSON.stringify(data));
      return `getUpdates failed: ${data.description || "unknown"}`;
    }
    updates = data.result || [];
  } catch (e) {
    console.log("getUpdates error:", e.message);
    return "getUpdates error: " + e.message;
  }

  if (updates.length === 0) return "no new messages";

  let handled = 0;
  let lastId = 0;

  // 2. Process each message
  for (const u of updates) {
    lastId = Math.max(lastId, u.update_id);
    const msg = u.message || u.edited_message;
    if (!msg || !msg.text) continue;

    const chatId = String(msg.chat.id);
    const text = msg.text.trim().toLowerCase();

    // Only obey YOUR chat id
    if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
      continue; // silently ignore strangers
    }

    if (text === "/run" || text === "/scan") {
      const { ok, detail } = await triggerWorkflow(env, "scan");
      if (ok) {
        await sendTelegram(env, chatId,
          "✅ Scan started. Results will arrive here in ~10–15 minutes.");
      } else {
        await sendTelegram(env, chatId,
          "⚠️ Could not start the scan.\n<code>" + detail + "</code>");
      }
      handled++;
    } else if (text === "/review") {
      const { ok, detail } = await triggerWorkflow(env, "review");
      if (ok) {
        await sendTelegram(env, chatId,
          "📋 Signal review started. Report arrives in a few minutes.");
      } else {
        await sendTelegram(env, chatId,
          "⚠️ Could not start the review.\n<code>" + detail + "</code>");
      }
      handled++;
    } else if (text === "/start" || text === "/help") {
      await sendTelegram(env, chatId,
        "🇲🇾 <b>Bursa Screener bot</b>\n\n" +
        "/run — run the screener now\n" +
        "/scan — same as /run\n" +
        "/review — how did past signals perform?\n\n" +
        "Note: commands are picked up within ~1 minute.\n" +
        "The daily automatic scan still runs on schedule as well.");
      handled++;
    }
  }

  // 3. Acknowledge everything we just read so Telegram doesn't resend it.
  //    (Calling getUpdates with offset = lastId + 1 confirms all older updates.)
  if (lastId > 0) {
    try {
      await fetch(`${api}/getUpdates?offset=${lastId + 1}&timeout=0&limit=1`);
    } catch (e) {
      console.log("ack failed (may reprocess next tick):", e.message);
    }
  }

  return `processed ${updates.length} update(s), handled ${handled} command(s)`;
}

/** Fire the GitHub Actions workflow_dispatch event. Returns {ok, detail}. */
async function triggerWorkflow(env, mode = "scan") {
  const branch = env.GITHUB_BRANCH || "main";
  const url =
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}` +
    `/actions/workflows/${env.WORKFLOW_FILE}/dispatches`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `token ${env.GITHUB_TOKEN}`, // classic-token style auth
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "bursa-screener-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: branch, inputs: { mode } }),
  });

  if (res.status === 204) return { ok: true, detail: "" };

  const body = await res.text();
  const detail = `HTTP ${res.status} on ${env.GITHUB_OWNER}/${env.GITHUB_REPO}/` +
                 `${env.WORKFLOW_FILE}@${branch}: ${body.slice(0, 200)}`;
  console.log("GitHub dispatch failed:", detail);
  return { ok: false, detail };
}

/** Send a message back to the user via the Telegram Bot API. */
async function sendTelegram(env, chatId, text) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}
