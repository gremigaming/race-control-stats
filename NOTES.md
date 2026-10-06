# race-control-stats notes

Short project context. Server design details are in DISCORD_SERVER.md: read that only when a task touches server structure, roles or the real-server rollout.

## What this is
Repo for the owner of GreMi_Gaming (Twitch, YouTube, TikTok streamer; F1 and Le Mans Ultimate; Discord with about 250 members). A GitHub Actions workflow runs `update_stats.py` hourly (`11 * * * *`, plus manual run) and renames locked voice channels in the Discord server so they show live numbers. The Discord bot is **Race Control**, an AI assistant account that acts only when an AI session or this workflow runs it. It does not answer members.

## Working style
- Direct, natural tone, polished but not overly formal. No em dashes.
- Show code in chat in full inline, not as a downloadable file.
- The owner is not a developer by trade: exact click paths, small steps. Open pull requests instead of asking them to paste code (a pasted line break once caused a SyntaxError).
- Never ask for tokens or keys in chat.

## Updater rules
- Python standard library only.
- Stat channels are voice channels in the category whose name contains bold-unicode STATS, matched by leading emoji: members 👥, status 🔴, Twitch 🟣, YouTube ▶️, TikTok 🎵.
- Names are `emoji┃TEXT` in bold sans-serif unicode capitals (`bold_caps`: A-Z from U+1D5D4, digits from U+1D7EC).
- Rename only when the name changed (Discord allows about 2 renames per 10 minutes per channel).
- Each source runs in its own try block so one failure never blocks others; exit non-zero if any failed.
- Never print keys or tokens, including in errors. `http()` strips query strings because the YouTube key is in the query.
- Send a `DiscordBot (url, version)` User-Agent. Retry on 429 (`retry_after`), 5xx and network errors.
- Secrets (GitHub Actions, private repo): `DISCORD_BOT_TOKEN`, `GUILD_ID` (currently the TEST server), `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_LOGIN`, `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID`. The Discord token has Administrator. Worth suggesting a separate bot with only Manage Channels at the real-server rollout.

## Source status
- Members (`approximate_member_count`, includes bots): working.
- Twitch live status (app token, `/helix/streams`): working. `LIVE NOW` unproven until a real stream.
- YouTube subscribers: working. Rounded to 3 significant figures above 1,000; hidden counts return nothing.
- Twitch followers: just added, UNVERIFIED. Reads `total` from `/helix/channels/followers` with the app token. Docs say a user token with `moderator:read:followers` is needed; forum reports say the total works without. If a run shows 401, switch to the authorization code flow (confidential client, redirect `http://localhost`). Twitch refresh tokens may change on refresh, so store the new one each time.
- TikTok: official API needs app review. Start with a hand edited `tiktok.json` the script reads.
- Later: Pits n' Giggles race files turned into stats. Ask for a sample first. Files likely contain other drivers' names, so keep the repo private and warn members before publishing.

## First tasks
1. The last reported run failed with `SyntaxError: unterminated f-string literal` on the `members` line (pasted line break). Confirm the file compiles and the workflow is green.
2. Confirm Twitch followers, or implement the user-token fallback.
3. Add `tiktok.json` support.
4. Add tests using simulated responses (patch `update_stats.discord` and `update_stats.http`): normal values, hidden YouTube count, wrong channel ID, API outage, unconfigured source, Twitch 401, unknown Twitch login.

## Discord access from Claude Code
Never put the bot token in an environment variable, file, commit or chat (environment variables are readable by anyone using the environment). On Pro and Max, the owner stores it as an API credential on the environment: host `discord.com`, header `Authorization`, prefix `Bot`. The proxy attaches it, so call `https://discord.com/api/v10/...` with no Authorization header. Verify with `GET /users/@me`. This comes from the docs and is untested; if it fails, tell the owner instead of improvising.

## Server safety
- TEST server (`1532364671288475688`) may be changed freely.
- The REAL server has history that cannot be recovered. No deleting, wiping or bulk renaming without explicit approval for that specific action. List first, show a table of proposed changes, wait for approval, then apply.
- Scripts must be idempotent (check names before creating). Deleting a channel deletes its messages permanently; prefer rename, move, or a hidden Archive category.
