# race-control-stats notes

Short project context. Server design details are in DISCORD_SERVER.md: read that only when a task touches server structure, roles or the real-server rollout.

## What this is
Repo for the owner of GreMi_Gaming (Twitch, YouTube, TikTok streamer; F1 and Le Mans Ultimate; Discord with about 250 members). A GitHub Actions workflow runs `update_stats.py` hourly (`11 * * * *`, plus manual run) and renames locked voice channels in the Discord server so they show live numbers. The Discord bot is **Race Control**, an AI assistant account that acts only when an AI session or this workflow runs it. It does not answer members.

## Working style
- Direct, natural tone, polished but not overly formal. No em dashes.
- Show code in chat in full inline, not as a downloadable file.
- The owner is not a developer by trade: exact click paths, small steps. Push changes yourself instead of asking them to paste or upload code (a pasted line break, and once a truncated upload, caused a SyntaxError).
- Never ask for tokens or keys in chat.
- Git: push every change straight to `main` and keep `main` as the only branch (owner's choice, 2026-10-06). No pull requests or extra branches.

## Updater rules
- Python standard library only.
- Stat channels are voice channels in the category whose name contains bold-unicode STATS, matched by leading emoji: members 👥, status 🔴, Twitch 🟣, YouTube ▶️, TikTok 🎵.
- Names are `emoji┃TEXT` in bold sans-serif unicode capitals (`bold_caps`: A-Z from U+1D5D4, digits from U+1D7EC).
- Rename only when the name changed (Discord allows about 2 renames per 10 minutes per channel).
- Each source runs in its own try block so one failure never blocks others; exit non-zero if any failed.
- Never print keys or tokens, including in errors. `http()` strips query strings because the YouTube key is in the query.
- Send a `DiscordBot (url, version)` User-Agent. Retry on 429 (`retry_after`), 5xx and network errors.
- Tests use simulated responses only (no network): `python3 -m unittest discover -s tests -t .`. They run on every pull request via `tests.yml`.
- Secrets (GitHub Actions, private repo): `DISCORD_BOT_TOKEN`, `GUILD_ID` (currently the TEST server), `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_LOGIN`, `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID`. The Discord token has Administrator. Worth suggesting a separate bot with only Manage Channels at the real-server rollout.

## Source status
- Members (`approximate_member_count`, includes bots): working.
- Twitch live status (app token, `/helix/streams`): working. `LIVE NOW` unproven until a real stream.
- YouTube subscribers: working. Rounded to 3 significant figures above 1,000; hidden counts return nothing.
- Twitch followers: working with the app token (`total` from `/helix/channels/followers`), verified 2026-10-06 (1,639). If it ever returns 401, switch to the authorization code flow (confidential client, redirect `http://localhost`, scope `moderator:read:followers`); Twitch refresh tokens may change on refresh, so store the new one each time.
- TikTok: official API (Login Kit, sandbox app with the owner's account as target user, scopes `user.info.basic,user.info.stats`) via `TIKTOK_CLIENT_KEY` (repo variable), `TIKTOK_CLIENT_SECRET` and `TIKTOK_REFRESH_TOKEN` (secrets). The one-time `TikTok login` workflow creates the refresh token (redirect `https://github.com/gremigaming/race-control-stats/`). TikTok can rotate the refresh token on each refresh, so `stats.yml` saves the new one with `gh secret set` using `GH_SECRETS_TOKEN` (fine-grained token, this repo, Secrets read/write). Refresh tokens last 365 days. Without the API it falls back to the hand edited `tiktok.json` (`null` skips). Not yet verified end to end.
- Later: Pits n' Giggles race files turned into stats. Ask for a sample first. Files likely contain other drivers' names, so keep the repo private and warn members before publishing.

## First tasks
Done 2026-10-06: fixed the truncated `update_stats.py`, confirmed Twitch followers, added `tiktok.json` support and tests.

## Discord access from Claude Code
Never put the bot token in an environment variable, file, commit or chat (environment variables are readable by anyone using the environment). On Pro and Max, the owner stores it as an API credential on the environment: host `discord.com`, header `Authorization`, prefix `Bot`. The proxy attaches it, so call `https://discord.com/api/v10/...` with no Authorization header. Verify with `GET /users/@me`. This comes from the docs and is untested; if it fails, tell the owner instead of improvising.

## Server safety
- TEST server (`1532364671288475688`) may be changed freely.
- The REAL server has history that cannot be recovered. No deleting, wiping or bulk renaming without explicit approval for that specific action. List first, show a table of proposed changes, wait for approval, then apply.
- Scripts must be idempotent (check names before creating). Deleting a channel deletes its messages permanently; prefer rename, move, or a hidden Archive category.
