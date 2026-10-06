# GreMi_Gaming Discord: design, current state, plans

Everything decided and built so far. Written for Claude Code, from the earlier planning conversation.

## Goal
Redesign the server into a clean, professional, premium feeling sim racing community that showcases the owner's content, with the main focus on F1 and Le Mans Ultimate. Priorities: simplicity, professionalism, and making it obvious where new members start and what the server is about. The original server felt cluttered: duplicate categories (two Content: Twitch, two Content: YouTube), mixed decorative-font channel names, and a pile of generic roles.

## Servers
- **TEST server:** "gremi_gaming's backup server", ID `1532364671288475688`. About 3 members. The owner explicitly allowed any change here, including wiping. It was a copy of the old real server.
- **REAL server:** about 250 members, Community features enabled. ID not yet provided. The owner will give it after inviting the bot.
- **Bot:** application **Race Control**, bot ID `1532679389429502012`. Invited with Administrator. Its managed role on the test server was named `ClaudeTemplate` (it may have been renamed). Description: "Race Control is an AI assistant for the GreMi_Gaming community. It helps with the behind-the-scenes work: setting up channels and roles, keeping things tidy, and making sure everyone lands in the right place. Flags out, lights out, let's race." About me: "AI assistant for GreMi_Gaming. Keeping the paddock tidy." Icon: a small robot marshal in a red hi-vis vest holding a chequered flag, with circuit traces coming off the flag, inside a red ring.

## Naming style (applies to everything)
- Text and voice channels: `emoji┃NAME` in bold sans-serif unicode capitals, words joined by hyphens, for example `👋┃START-HERE`. The bold letters bypass Discord's forced lowercase. Digits use the bold digit block too.
- Categories: `『 emoji NAME 』` in the same bold capitals, for example `『 📌 START HERE 』`.
- Names below are written in plain ASCII for readability. Convert with the bold mapping when creating.

## Built structure (test server), in order
1. **📌 START HERE:** `📜┃RULES` (protected channel, see below), `👋┃START-HERE`, `🔗┃SOCIALS`, `👤┃INTRODUCTIONS`
2. **📊 STATS** (voice, locked, scoreboard): `🟣┃TWITCH`, `▶️┃YOUTUBE`, `🎵┃TIKTOK`, `👥┃MEMBERS`, `🔴┃STATUS`
3. **🏁 COMMUNITY HUB:** `📢┃ANNOUNCEMENTS`, `💬┃GENERAL`, `💡┃FEEDBACK-AND-SUGGESTIONS`, `⭐┃SUBSCRIBER-CHAT`, `🎬┃CLIPS-AND-HIGHLIGHTS`
4. **📺 CONTENT & STREAMS:** `🗓️┃STREAM-SCHEDULE`, `📣┃SELF-PROMOTION`, `🔴┃NEW-CONTENT`
5. **🏎️ RACING:** forum `🛠️┃SETUPS` (F1 and LMU together, filtered by tags), forum `🏆┃COMMUNITY-LEAGUES`, `🏎️┃RACING-CHAT`, `📰┃F1-NEWS`
6. **🎙️ VOICE CHANNELS:** `🎙️┃PADDOCK`, `🏁┃RACE-ROOM-1`, `🏁┃RACE-ROOM-2`, `📺┃STREAM-WATCH-PARTY`, `💤┃AFK`
7. **🛠️ SUPPORT:** `🆘┃SERVER-SUPPORT`, `🤖┃BOT-COMMANDS`
8. **🔒 STAFF ONLY:** `🛡️┃MOD-CHAT` (protected channel), `🚩┃REPORTS`, `📋┃LOGS`

Consolidations made to keep the channel count down, and why:
- f1-discussion and lmu-discussion became `RACING-CHAT`.
- announcements and stream-announcements became one `ANNOUNCEMENTS`, moved out of Start Here into Community Hub.
- suggestions and content-feedback became `FEEDBACK-AND-SUGGESTIONS`.
- race-results and looking-for-racers are folded into the `COMMUNITY-LEAGUES` forum.
- youtube-uploads and tiktok-clips became one `NEW-CONTENT`.
- Setups is one forum for both games. Default tags (F1, LMU, and for leagues Recruiting, Results) have NOT been added yet.

## Permissions (test server, current)
Permission bits: VIEW_CHANNEL 1024, SEND_MESSAGES 2048, CONNECT 1048576, ADMINISTRATOR 8. Administrator bypasses all channel overwrites.
- **Staff Only category:** @everyone deny VIEW_CHANNEL, Moderator allow VIEW_CHANNEL. The children sync from the category. Before this, `REPORTS` and `LOGS` were visible to everyone.
- **Stats category and its voice channels:** @everyone allow VIEW_CHANNEL, deny CONNECT.
- **Rules channel:** @everyone cannot send (Discord default for the rules channel).
- Everything else is open at the default level.
- **Not done:** `SUBSCRIBER-CHAT` is not gated yet because the Subscriber role does not exist yet. It will be created when the owner connects Twitch (native Discord Twitch integration, already set up on the owner's main server).

## Roles
Hierarchy on the test server, top to bottom:
`.` (blank role that gives bots all permissions; grouped with Bots at the bottom of the member list on purpose, do not touch), `LIVE RIGHT NOW` (auto role, should be bot driven), `GreMi_Gaming` (owner), `Lead Moderator` (Administrator), `Moderator` (kick, ban, timeout, manage messages, nicknames, roles, move members; no Administrator), the Race Control bot role (Administrator), the five Race Legends award titles, color roles, `F1`, `LMU`, `VIP`, `OG`, `Streamer`, `Bots`, `GiveAway Winner`, `Member`, `TidyCord` (an existing third party bot's role, leave alone), then the new tag roles.

- **Race Legends (kept, cosmetic, hoisted, meant to be reassigned each season):** `Smooth Operator | Most Cleanest Driver`, `Minister of Attack | Most Overtakes` (typo fixed), `Guenther's Rockstar | Most DOTD`, `Mr. Saturday | Most FL`, `Simply Lovely | Most Wins`.
- **Color roles trimmed to four:** Red, Orange, Blue, Silver. Removed Yellow, Green, Purple, Pink.
- **Removed:** the separate Twitch, YouTube and TikTok roles. Replaced by one `Notify Me`.
- **New tag roles, zero permissions, not hoisted:** Verified Racer, League Racer, Looking for League, Casual Racer, Experienced Racer, New to Sim Racing, PC, Xbox, PlayStation, Wheel User, Controller/Gamepad User, Notify Me.
- `F1` and `LMU` were un-hoisted so they stop showing as separate groups in the member list.

Who assigns what:
- **Self-assigned through Discord's native Onboarding:** F1, LMU, PC, Xbox, PlayStation, New to Sim Racing, Experienced Racer, League Racer, Looking for League, Casual Racer, Wheel User, Controller/Gamepad User, Notify Me, colors.
- **Automatic:** Subscriber (Twitch sync), Member (on rules acceptance), LIVE RIGHT NOW (stream bot), Bots.
- **Manual and rare by design:** staff roles, VIP, OG, Race Legends, GiveAway Winner, Verified Racer (could be automated later from a league bot).

## Onboarding
The owner will configure Discord's native Welcome Screen and Onboarding questions in Server Settings. Discord does not expose that through the bot API, so it cannot be scripted. Planned question groups: game (F1, LMU, both), platform (PC, Xbox, PlayStation), experience (new, experienced), league interest (League Racer, Looking for League, Casual), hardware (wheel, controller). Keep it to these five groups so it does not feel like a form. The next useful step is drafting the exact questions and mapping each answer to a role.

## Open decisions
- Per-game channel visibility. Merging the F1 and LMU channels means the game roles only filter forum tags, not whole channels. Truly different channel lists per game would mean splitting channels again. Not decided.
- Fun roles (DNF Club, Wall Magnet, Backmarker, Formation Lap Enjoyer, Clip Machine and similar). Only brainstormed. The owner wants any role that can be self-assigned or automatic, not something they hand out manually to 250 people. Keep to two to four at most if added.
- A separate low-permission stats bot versus reusing Race Control.

## Real server rollout plan (agreed approach)
1. Owner invites Race Control to the real server with Administrator and drags its role up to just under Lead Moderator. Without that, the bot can create roles but cannot edit, move or delete existing ones.
2. **Read-only pass first.** List the real channels and roles and give the owner a table of old name to new name, what is missing, and what would go to an Archive category. Change nothing until approved.
3. **Rename and move in place** so message history, pins and permissions survive. A text channel cannot become a forum, so the Setups and Community Leagues forums are new channels. Where two old channels merge into one, keep one and move the other into a hidden **Archive** category rather than deleting it.
4. Create only what is missing. Create or rename roles to match. Do not delete roles without approval.
5. Existing members do not retroactively get tag roles. Post an announcement pointing them at Onboarding or a roles panel so they opt in once. New joiners get it automatically.
6. Choose a quiet time and give members a short heads-up in announcements first.
7. Add the STATS category and point the stats updater at the real server by changing the `GUILD_ID` secret.

## Technical gotchas learned (Discord API)
- **Role hierarchy.** A bot can only edit, delete or reorder roles below its own highest role, and Administrator does not bypass this. Creating roles works regardless and lands at the bottom. A `PATCH` that tries to move a role beyond what the hierarchy allows can return success while silently changing nothing, so always read the position back. Only a person above the bot's role, in practice the owner, can drag the bot role up.
- **Protected community channels.** With Community enabled, the rules channel and the public updates channel cannot be deleted (HTTP 400, code 50074). Find them via `rules_channel_id` and `public_updates_channel_id` on the guild object. Rename and move them instead. On the test server these are the rules channel `1532683442486251551` and the mod chat channel `1532683442486251554`.
- **Idempotency.** Re-running a role script after a partial failure created 13 duplicate roles. Always check names first.
- **Category order.** Creating a category with a `position` can leave ties. Set explicit positions for all categories with one bulk `PATCH /guilds/{id}/channels`.
- **Channel types.** Text 0, voice 2, category 4, forum 15. Text channel names cannot contain spaces. Voice channel names can.
- **Overwrites.** Channels created under a category with overwrites end up synced with it. Always verify with a `GET`.
- **Reliability.** Expect occasional 503 errors and 429 rate limits. Retry with backoff and honor `retry_after`.
- **Shell.** Environment variables do not persist between separate shell calls in some sandboxes. Export them in the same command.
- **Rename limit.** About 2 renames per 10 minutes per channel.
