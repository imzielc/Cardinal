# Cardinal Bombsquad Server Scripts
- **Script version**: 1.7.61
- **Protocol version**: 36/editable (game versions below script version won't be allowed to join)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![Discord](https://img.shields.io/badge/Discord-Join%20Chat-7289DA?style=for-the-badge&logo=discord&logoColor=white&labelColor=7289DA)](https://discord.gg/uGqhNWpWPY)

---

## Installation

### Quick one-line installation
```bash
curl -fsSL https://install.thecardinal.workers.dev | bash
```

### Manual setup
1. Change into the directory:
   ```bash
   cd Cardinal
   ```
2. Configure settings in `mods_config.json` to your liking.
3. Open a tmux session (recommended for persistent background execution):
   ```bash
   tmux
   ```
4. Run the server:
   ```bash
   ./bombsquad_server
   ```

---

## Discord Bot Integration
- Runs the Discord bot as an isolated process, so it never touches the game's frame budget.
- Communicates with the game server via a local socket tunnel, in both directions.
- Configure the bot under the `discord` section of `mods_config.json`:

| Key | What it does |
|---|---|
| `enable` | Turns the bot on or off |
| `token` | Discord bot token |
| `owner_id` | Bot owner's Discord user ID (top authority, can't be revoked) |
| `port` | Game server's socket port (default `9001`) |
| `bot_port` | Bot's socket port for receiving game events (default `9002`) |
| `staff_channel_id` | Channel where held posts wait for review |
| `brackets_channel_id` | Parent channel where match threads are created |
| `hold_hours` | Auto-publish delay for held posts (default `3`) |
| `invite_expire_hours` | Registration invitation expiry window |
| `webhooks.*` | Webhook URLs for the five tournament channels |

### Bot Permissions
The bot needs the following, depending on what you're using:

- **Guild-level**: `Manage Roles`, `Manage Channels`, `Kick Members`, `Ban Members`, `Read Message History`, `Send Messages`, `Attach Files`, `Embed Links`, `Use Application Commands`.
- **Registration channel** (where the panel is posted): `Manage Threads`, `Create Private Threads`, `Send Messages in Threads`, `Send Messages`.
- **Brackets channel** (where match threads are created): `Create Private Threads`, `Send Messages in Threads`, `Manage Threads`.
- **Staff channel**: `Send Messages`, `Read Message History`, `Attach Files`.

The bot's own role needs to be positioned above any role it creates (Participant, Team, Group).

---

## General Bot Commands
- `/cmd <command>`: Sends a chat command to the game server (executes according to the user's in-game authority level).
- `/say <message>`: Broadcasts a chat message directly into the game.
- `/list`: Displays the players currently inside the server.
- `/owner <user>`: Grants or revokes server owner permissions.
- `/admin <user>`: Grants or revokes server admin permissions.
- `/announce <channel> <message> [role]`: Sends a bot-authored message to any channel or thread. Optional single role ping.
- `/ssh <command>`: Runs a shell command on the server's host, in the project root (leader only, 30s timeout). Used for `git pull`, logs, and other server maintenance without needing to SSH in.

---

## Account Linking
Before a player can register for a tournament, their Discord account must be linked to their in-game account. This is one-time and carries across seasons.

1. Run `/account link` in Discord. The bot replies with a 6-digit code.
2. Join the game server and run `/link <code>` in chat.
3. Done. The bot now knows which in-game account belongs to which Discord user.

Commands under `/account`:

- `/account link`: Issues a code to link your accounts.
- `/account status`: Shows the linked in-game account (tag, account id, creation date, last seen, active days).
- `/account unlink`: Removes your link. Blocked while you're on a tournament team; use `/tournament leave` first.
- `/account whois <user>` (admin): Shows which in-game account a Discord user has linked.
- `/account set <user> <account_id>` (leader): Replaces a linked user's in-game account. Clears their verification — they'll need to run `/verify` again.

---

## Tournament System

Cardinal provides an automated, high-stability tournament system managed entirely from Discord. Registrations, verification, brackets, match threads, and cleanup all happen without admin intervention once a season is running.

### Channel & Webhook Setup
Create five Discord channels and add their webhook URLs to `mods_config.json` under `discord.webhooks`:

1. **`brackets`**: Bracket images and match threads live here.
2. **`results`**: Match result images and score summaries.
3. **`dashboard`**: Player rankings, leaderboards, and group standings.
4. **`registrations`**: Registration confirmation cards.
5. **`announcements`**: Tournament announcements and captain lists.

Set the brackets channel's ID as `discord.brackets_channel_id` in config — that's where match threads get created. Enable `Send Messages in Threads` on both the brackets and registration channels so teams can chat inside their threads.

The title and logo images used on the bracket graphics can be swapped in `dist/ba_root/mods/tournament/graphics/generator.py`.

### Registration Flow
1. An admin posts a registration panel with `/tournament register-panel`. The panel stays live across the whole season — it updates itself as teams register and verify.
2. Players click **Register** on the panel. Solo seasons register instantly. Team seasons ask the captain to pick their teammates.
3. A private thread is created for each team, with every member invited. They see the current roster, expiry countdown, and Accept/Decline buttons.
4. Once every member accepts, the buttons lock and the roster is fixed.
5. Every member joins the game and runs `/verify` (no code needed — the bot uses their linked account).
6. When everyone on a team has verified, the team is confirmed. A registration card posts to the registrations channel pinging the team's role.

### Invitation Threads
- Each team's invitation lives in its own private thread. Only the team's members (and staff) can see it.
- Captains can invite replacements with `/tournament invite <user>` if someone declines or leaves.
- The thread pings teams about upcoming matches automatically, so teams can coordinate in one place.

### Expiry & Reminders
- Team invitations expire after a configurable window (`invite_expire_hours`). Verified teams never expire.
- If a team's invitation is nearing expiry and still has pending actions (unaccepted invites or unverified members), the bot posts reminders in the team's thread during the final 24 hours. Teams with nothing left to do get no reminders.
- `/tournament extend <hours>` (leader): Extends the expiry window for all pending teams and updates the config default for future seasons. Teams still past deadline after the extension are cleaned up immediately.

### Waitlist
- When registrations are closed, the panel's button becomes **Join Waitlist**.
- Waitlisted teams are queued in order. If a team drops, expires, or is dissolved before the tournament starts, the oldest queued team is promoted into a real slot automatically — a new thread is created for them, roles are assigned, and they get the normal invitation flow.
- `/tournament waitlist` (leader): Lists the queued teams in order.

### Match Threads & Round Starts
- When a new round becomes playable, one private thread per match is created in the brackets channel. Both teams are added, their team roles are pinged, and the match details are posted inside.
- Each team also gets a notification in their own invitation thread listing their upcoming match and a link to the match thread.
- **Delayed releases**: A completed round doesn't immediately announce the next one. Instead, the next round becomes playable but stays quiet until the nightly window (12:00 AM – 8:00 AM IST), at which point it's released and threads are created. Teams wake up to fresh matches without being pinged during the day.
- **Force release**: `/tournament release` (leader) skips the window and releases a pending round immediately. Only works when a round is queued and no match is currently running.

### Tournament Modes

**Single Elimination**
- Optional group stage if team count is divisible into groups. Top qualifying teams advance to the knockout stage.
- Concludes with a final (1st/2nd) and a third-place match (3rd/4th). Both are labelled distinctly on the bracket and in match threads.
- Minimum 4 teams.

**Double Elimination**
- Every team starts in the winners bracket. Two losses eliminate a team.
- Supports any team count ≥ 4; byes are seeded automatically when the count isn't a power of two.
- The grand final pits the winners champion against the losers champion. If the losers champion wins, an automatic reset match decides the tournament.
- Final rankings appear on the bracket graphic once the tournament completes.

### Staff Review (Held Posts)
Bracket images and captain announcements can be held for staff review before going public:
- Held posts appear in the staff channel first, tagging only the owners.
- Each staff message has **Send now** and **Cancel** buttons.
- Posts auto-publish after `hold_hours` (default `3` hours).
- If a bracket updates while it's still held, the held post updates in place rather than spamming a new one.
- Set `staff_channel_id: 0` or `hold_hours: 0` to disable holding — everything posts publicly immediately.
- Match results, dashboards, and live match announcements are never held; they post in real time.

### Tournament Commands (Leader / Admin)

- `/tournament create <type> <series> [mode]`: Creates a tournament season.
  - `type`: `SOLO`, `DUO`, `TRIO`, or `SQUAD`
  - `series`: `BO1`, `BO3`, `BO5`, or `BO7`
  - `mode`: `SINGLE_ELIMINATION` (default) or `DOUBLE_ELIMINATION`
- `/tournament register-panel`: Posts the registration panel in the current channel.
- `/tournament registrations <open/close>`: Opens or closes player registrations.
- `/tournament start`: Starts the tournament and generates the initial brackets.
- `/tournament list`: Lists all playable matches in the active rounds.
- `/tournament teams`: Lists every registered team, their status, and expiry (visible to everyone).
- `/tournament invite <user>`: Invites a new member to the caller's team (captain only).
- `/tournament leave`: Leaves your team. Captains dissolve the whole team; members leave individually.
- `/tournament release`: Releases a pending round start immediately, skipping the night window.
- `/tournament extend <hours>`: Extends the invitation expiry for all pending teams and the config default.
- `/tournament waitlist`: Lists the queued teams in order.
- `/tournament win <match-index> <team-index>`: Manually awards a match to a team.
- `/tournament uuid <user> <uuid>`: Updates a registered player's device UUID.
- `/tournament cancel`: Cancels the active season. Closes registrations, drops held posts, stops any running match, and cleans up roles and threads.

### Player Commands

**Discord**
- `/account link`: Issues a link code.
- `/account status`: Shows your linked in-game account.
- `/tournament register`: (via the registration panel) signs you up for the active season.
- `/tournament invite <user>`: Invites a teammate (captain only).
- `/tournament leave`: Leaves or dissolves your team.
- `/tournament teams`: Lists registered teams and their status.

**In-Game**
- `/link <code>`: Redeems a Discord link code.
- `/verify`: Completes your tournament registration.
- `/ready`: Signals you're ready for your pending match. When every player in the match is ready, the match launches automatically.
- `/mpause`: Requests a pause. Both team captains must agree; the match resumes via `/ready` afterwards.

---

## Tournament Security
- The device UUID and account ID captured during `/verify` are locked to the player for the season.
- Players trying to join a match from a different device or account are blocked.
- Server admins can update a player's registered UUID with `/tournament uuid` if a legitimate device change is required.
- Leaders can update a player's linked account with `/account set` if they relink to a new account. Doing so clears their verification — they'll need to `/verify` again in-game.

---

## Updating the Server
The server pulls the latest `main` automatically, 30 seconds after every start. New code takes effect on the next restart — nothing restarts on its own.

- Toggle via `auto_pull` in `mods_config.json` (top level, defaults to `true`).
- Silent when there's nothing new. One console line when something was pulled or when the pull failed.

For instant testing:

1. `/ssh command: git pull origin main` — pulls immediately.
2. `/cmd command: /quit` — restarts the server with the new code.