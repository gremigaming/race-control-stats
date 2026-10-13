# race-control-stats notes

Short project context. Server design details are in DISCORD_SERVER.md: read that only when a task touches server structure, roles or the real-server rollout.

## What this is
Repo for the owner of GreMi_Gaming (Twitch, YouTube, TikTok streamer; F1 and Le Mans Ultimate; Discord with about 250 members). A GitHub Actions workflow runs `update_stats.py` every 5 minutes (`*/5 * * * *`, plus manual run; free because the repo is public since 2026-10-06, keep it public or go back to 30 minutes) and renames locked voice channels in the Discord server so they show live numbers. The Discord bot is **Race Control**, an AI assistant account that acts only when an AI session or this workflow runs it. It does not answer members.

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
- Secrets (GitHub Actions, private repo): `DISCORD_BOT_TOKEN`, `GUILD_ID` (no longer used: stats.yml now sets the real server ID directly, 2026-10-06), `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_LOGIN`, `YOUTUBE_API_KEY`, `YOUTUBE_CHANNEL_ID`. The Discord token has Administrator. Worth suggesting a separate bot with only Manage Channels at the real-server rollout.

## Source status
- Members (`approximate_member_count`, includes bots): working.
- Twitch live status (app token, `/helix/streams`): working. `LIVE NOW` unproven until a real stream.
- Live role: while live on Twitch the server owner gets the `LIVE RIGHT NOW` role (`update_live_role`), removed when offline. Needs the Race Control role above `LIVE RIGHT NOW` in the role list. Runs every 5 minutes (GitHub can delay scheduled runs at busy times).
- YouTube subscribers: working. Rounded to 3 significant figures above 1,000; hidden counts return nothing.
- Twitch followers: working with the app token (`total` from `/helix/channels/followers`), verified 2026-10-06 (1,639). If it ever returns 401, switch to the authorization code flow (confidential client, redirect `http://localhost`, scope `moderator:read:followers`); Twitch refresh tokens may change on refresh, so store the new one each time.
- TikTok: official API (Login Kit, sandbox app with the owner's account as target user, scopes `user.info.basic,user.info.stats`) via `TIKTOK_CLIENT_KEY` (repo variable), `TIKTOK_CLIENT_SECRET` and `TIKTOK_REFRESH_TOKEN` (secrets). The one-time `TikTok login` workflow creates the refresh token (redirect `https://github.com/gremigaming/race-control-stats/`). TikTok can rotate the refresh token on each refresh, so `stats.yml` saves the new one with `gh secret set` using `GH_SECRETS_TOKEN` (fine-grained token, this repo, Secrets read/write). Refresh tokens last 365 days. Without the API it falls back to the hand edited `tiktok.json` (`null` skips). Verified 2026-10-06 (3,904 followers). The client key may be a variable or a secret; use a variable so GitHub does not mask it in logs.
- Twitch schedule edits: `twitch_edit.py` via the manual `Twitch schedule` workflow (input `login`, or one change as JSON: list/add/edit/cancel/uncancel/delete/vacation/vacation-off, times Europe/Amsterdam). `login` uses Twitch's device code flow (code shown in the login-code job log, owner enters it at twitch.tv/activate, scope `channel:manage:schedule`) and saves `TWITCH_REFRESH_TOKEN` with `GH_SECRETS_TOKEN`. The stats run picks up changes for the Discord events and schedule picture on its own.
- Twitch chat and ads (2026-10-12): `bot/twitch_chat.py` on the VM next to the Discord bot. Chat account racecontroll (renamed from gremi_bot; `login` in the manual `Twitch chat` workflow, scopes `user:read:chat user:write:chat`); ads need GreMi_Gaming (`login-owner`, scopes `channel:edit:commercial channel:read:ads channel:manage:ads`). Logins are sealed with a key made from the Discord bot token and committed as `bot/twitch_chat.sealed` / `bot/twitch_owner.sealed`; the bot restarts on those commits and keeps renewed tokens in `/var/lib/race-control/twitch_chat.json`. It reads chat (EventSub websocket): timed messages need `min_chat` viewer messages since the last one; ads (GreMi 2026-10-12): 3 minutes per stream hour; 30 s ads during races when chat is quiet, a lobby top-up right after a race (new `race:` commit in race-stats from the uploader) if the hour is short, Twitch's own ads count toward the hour and are snoozed when chat is busy, `!ad` from GreMi or mods. Settings in `bot/twitch_timers.json`. Workflow inputs: `ad`, `ad 120`, `ads`, or a chat message. If the Discord bot token is reset, both logins must be redone. Timers can have a `command` (Simgasm: `!simgasm`) that GreMi or a mod types to send it now, on Twitch and YouTube. The bot posts its Twitch/YouTube status in the staff LOGS channel.
- YouTube chat (2026-10-12): `bot/youtube_chat.py` on the VM. Login: `YouTube` workflow input `login` (Google device code at google.com/device, scope `youtube`; needs secrets `YOUTUBE_CLIENT_ID`/`YOUTUBE_CLIENT_SECRET` from a "TVs and Limited Input devices" OAuth client, app published so the login doesn't expire after 7 days). Sealed as `bot/youtube.sealed`. While live it reads the live chat (counts toward chat activity), posts the Twitch timers there too, and answers timer commands from the owner or YouTube mods on both platforms. It posts as the channel that logged in. Quota: 10,000 units a day; chat read 5, live check 1, message 50.
- Giveaway and roles (2026-10-13): `bot/giveaway.py` (+ `giveaway.json`, read live) runs the weekly giveaway on the VM: panel with Enter/My entries/Leave in GIVEAWAYS, new subs from `twitch_chat.listen_subs` (owner login needs `channel:read:subscriptions`), draw 10 min after the 5th sub (chat message on Twitch/YouTube + Discord), weighted by roles at draw time, next round at midnight Amsterdam the day after. State `/var/lib/race-control/giveaway.json`. OBS counter `http://<VM IP>/giveaway` (aiohttp on port 80, needs "Allow HTTP traffic" on the VM; the address is posted in LOGS). Timer text `{giveaway}` is filled with the live status; `!giveaway` sends it. `bot/award_roles.py`: the 5 award roles from race-stats `league/awards.json` (best linked driver over the last 5 streams, never GreMi, held at least 7 days, changes posted in F1-CHAT) and the VIP role from Twitch VIPs by name (add only, differences in LOGS; owner login needs `channel:read:vips`).
- Later: Pits n' Giggles race files turned into stats. Ask for a sample first. Files likely contain other drivers' names, so they live only in the private gremigaming/Pits-n-Giggles repo, never in this one (GreMi is making this repo public for free Actions minutes, 2026-10-06). Warn members before publishing.

## First tasks
Done 2026-10-06: fixed the truncated `update_stats.py`, confirmed Twitch followers, added `tiktok.json` support and tests.

## Discord access from Claude Code
Never put the bot token in an environment variable, file, commit or chat (environment variables are readable by anyone using the environment). On Pro and Max, the owner stores it as an API credential on the environment: host `discord.com`, header `Authorization`, prefix `Bot`. The proxy attaches it, so call `https://discord.com/api/v10/...` with no Authorization header. Verify with `GET /users/@me`. This comes from the docs and is untested; if it fails, tell the owner instead of improvising.

## Server safety
- TEST server (`1532364671288475688`) may be changed freely.
- The REAL server has history that cannot be recovered. No deleting, wiping or bulk renaming without explicit approval for that specific action. List first, show a table of proposed changes, wait for approval, then apply.
- Scripts must be idempotent (check names before creating). Deleting a channel deletes its messages permanently; prefer rename, move, or a hidden Archive category.

## Discord requests

Race Control runs always-online on a free Google Cloud server (`bot/`, installed with `bot/setup_vm.sh`, guide in the project files). When the owner or a moderator listed in `bot/staff.json` tags it, it answers within seconds and wakes the Claude Code routine "Race Control: Discord tag alert". Claude answers questions directly. When staff ask for a change (server, or the bot itself), the bot posts a "📋 Change request" in the staff-only change-requests channel (1557354121743306772) and Claude carries it out only after the owner reacts ✅ there. Approved changes to the bot's Python code go live by themselves: the bot checks main every 5 minutes and restarts to load new code. Schedule questions are answered from the Discord events the stats updater makes from the Twitch schedule. Every request and change is logged in `discord/change-log.md`. Administrator grants, changes to the bot's own role, bans, kicks and mass deletes are always refused.

Quick answers: with `ANTHROPIC_API_KEY` on the server, the bot answers questions itself through the Claude API (`bot/brain.py`) with read-only lookups and the background in `bot/briefing.md`. **Update `bot/briefing.md` after every server change** (it's public, so nothing private). Change requests still go to Claude Code.
