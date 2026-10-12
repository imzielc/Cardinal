# Cardinal Bombsquad Server Scripts
- **Script version**: 1.7.61
- **Protocol version**: 36/editable (game versions below script version won't be allowed to join)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![Discord](https://img.shields.io/badge/Discord-Join%20Chat-7289DA?style=for-the-badge&logo=discord&logoColor=white&labelColor=7289DA)](https://discord.gg/yrYqbSU7wT)

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
- Runs the Discord bot as an isolated process (ensures zero game server lag).
- Communicates with the game server via a local socket tunnel.
- Configure bot settings (`token`, `owner_id`, `port`, `webhooks`) in `mods_config.json` under the `discord` section.

### General Bot Commands
- `/cmd <command>`: Sends a chat command to the game server (executes according to user authority level).
- `/say <message>`: Broadcasts a chat message directly into the game.
- `/owner <user-mention>`: Grants or revokes server owner permissions.
- `/admin <user-mention>`: Grants or revokes server admin permissions.
- `/list`: Displays the active players currently inside the server.

---

## Tournament System

Cardinal provides an automated, high-stability tournament system managed via Discord bot commands.

### Tournament Setup & Webhooks
Create five designated Discord channels and add their webhook URLs to `mods_config.json` under `discord.webhooks`:
1. **`brackets`**: Displays the active tournament bracket images.
2. **`results`**: Posts match completion announcements and score summaries.
3. **`dashboard`**: Posts player rankings, leaderboards, and group standings.
4. **`registrations`**: Announcement and status channel for player/team signups.
5. **`announcements`**: Tournament announcements and team captain lists.

*Note: In `dist/ba_root/mods/tournament/graphics/generator.py`, replace the title and logo URLs with your own assets (raw image URLs).*

### Staff Review (Held Posts)
Brackets and team announcements can be held for staff review before going public:
- Generated brackets (group stage, main stage, double elimination) and captain announcements appear in your designated staff channel first, tagging only owners/leaders.
- Staff messages include interactive **"Send now"** and **"Cancel"** buttons for immediate publishing or discarding.
- Posts are automatically published after `hold_hours` (defaults to 3 hours).
- If match results update a bracket while it is currently held, the held post updates in-place rather than creating duplicate spam.
- **Configuration**: Set `staff_channel_id` (channel ID) and `hold_hours` in `mods_config.json` under `discord`.
- **Instant Posting**: Set `staff_channel_id: 0` or `hold_hours: 0` to post everything publicly immediately.
- Results, dashboards, and live match announcements are never held and post in real-time.

---

## Tournament Modes

### 1. Single Elimination (Default)
- **Optional Group Stage**: If teams are grouped, generates round-robin groups. The top qualifying teams advance to the knockout main stage.
- **Main Stage**: Knocks out losing teams, concluding with a Final match (1st/2nd place) and a Third-Place match (3rd/4th place).
- **Team Requirements**: Number of teams must be $\ge 4$ and divisible by 4 if using groups.

### 2. Double Elimination
- **Structure**: Every team begins in the Winners bracket. A team is only eliminated upon their second loss.
- **Byes & Seeding**: Supports any number of teams $\ge 4$. If team count is not a power of 2, standard seeded byes are automatically assigned in Round 1.
- **Grand Final & Reset**: The Winners bracket champion plays the Losers bracket champion. If the Losers champion wins match 1, an automatic reset match (Match 2) is scheduled.
- **Placements**: Final team rankings (1st through last) are determined by elimination round order and rendered directly on the bracket graphic.

---

## Tournament Commands

### Discord Slash Commands (Leader / Admin)
- `/tournament create <type> <series> [mode]`: Creates a tournament season.
  - `type`: `SOLO` or `TEAM`
  - `series`: `BO1`, `BO3`, `BO5`, or `BO7`
  - `mode`: `SINGLE_ELIMINATION` (default) or `DOUBLE_ELIMINATION`
- `/tournament registrations <open/close>`: Opens or closes player registrations.
- `/tournament start`: Starts the tournament and generates initial brackets.
- `/tournament list`: Lists all playable matches in active rounds with their match keys.
- `/tournament win <match-index> <team-index>`: Manually awards a match win to a team.
- `/tournament cancel`: Cancels the active season after a confirmation dialog (closes registrations, drops held posts, stops any running in-game match, and restarts the game server session).
- `/tournament uuid <user-mention> <uuid>`: Updates the registered device UUID for a player.

### Player Commands
- `/tournament register` (Discord):
  - **Solo**: Instantly registers the player and provides a verification code.
  - **Team**: Prompts the captain to invite teammates; sends verification codes once all members accept.
- `/verify <code>` (In-Game): Verifies a player and binds their Discord account, Bombsquad account ID, and device UUID.
- `/ready` (In-Game): Signals that a player is ready for their pending match. When all players in a match are ready, the match launches automatically.

---

## Tournament Security
- The device UUID and account ID captured during `/verify` are locked to the player for the season.
- If a registered player tries to enter a match from an unauthorized device or account ID, they will be blocked from joining.
- Server admins can update a player's registered UUID using `/tournament uuid` if a legitimate device change is required.
