"""shows the held posts to the staff channel and publishes them when their time comes."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from traceback import format_exc

import discord
from discord import AllowedMentions, ButtonStyle, Interaction, ui
from discord.ext import commands, tasks

from roles import roles
from server import config
from server.enums import Authority, Role
from tournament import posts

CHECK_SECONDS = 20


def owner_ids() -> list[int]:
    """the owners: the discord ids in the leaders role, and the configured owner."""
    ids = []
    configured = config.discord.get("owner_id", 0)
    if configured:
        ids.append(int(configured))
    for member in roles.read().get(Role.LEADER, []):
        if str(member).isdigit() and int(member) not in ids:
            ids.append(int(member))
    return ids


def is_owner(user_id: int) -> bool:
    return user_id == config.discord.get("owner_id", 0) or (
        roles.get_authority_level(user_id) >= Authority.LEADER
    )


def staff_content(record: dict, status: str | None = None) -> str:
    """the text of the staff message. only the owners are tagged (the post's own role pings
    are inside a code block, they do not ping anyone here)."""
    when = int(record["publish_at"])
    lines = []
    if status is None:
        lines.append(" ".join(f"<@{owner}>" for owner in owner_ids()) or "Owners")
        lines.append(
            f"**{record['label']}** is held for review. It goes public <t:{when}:R> (<t:{when}:f>)."
        )
        lines.append("Use the buttons to send it now or cancel it.")
    else:
        lines.append(f"**{record['label']}**: {status}")

    if record.get("content"):
        text = record["content"].replace("```", "'''")
        lines.append(f"```\n{text[:1500]}\n```")
    return "\n".join(lines)[:2000]


class HeldPostView(ui.View):
    """send now / cancel buttons of a held post, only the owners can use them."""

    def __init__(self, post_id: str) -> None:
        super().__init__(timeout=None)
        send = ui.Button(
            label="Send now", style=ButtonStyle.success, custom_id=f"post;send;{post_id}"
        )
        send.callback = self.send_now
        cancel = ui.Button(
            label="Cancel", style=ButtonStyle.danger, custom_id=f"post;cancel;{post_id}"
        )
        cancel.callback = self.cancel
        self.add_item(send)
        self.add_item(cancel)
        self.post_id = post_id

    async def _resolve(self, interaction: Interaction, action) -> None:
        if not is_owner(interaction.user.id):
            await interaction.response.send_message(
                "Only the owners can do this.", ephemeral=True
            )
            return
        await interaction.response.defer()
        record = await asyncio.to_thread(posts.get, self.post_id)
        done = await asyncio.to_thread(action, self.post_id)
        if record is None:
            return
        if done:
            status = ACTIONS[action]
            status = f"{status} by {interaction.user.mention}"
        else:
            status = f"already {record['state'].lower()}"
        await interaction.edit_original_response(
            content=staff_content(record, status),
            view=None,
            allowed_mentions=AllowedMentions.none(),
        )

    async def send_now(self, interaction: Interaction) -> None:
        await self._resolve(interaction, posts.publish)

    async def cancel(self, interaction: Interaction) -> None:
        await self._resolve(interaction, posts.cancel)


ACTIONS = {posts.publish: "sent early", posts.cancel: "cancelled"}


class HeldPosts(commands.Cog):
    """watches the queue of held posts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # the buttons of the posts that were already shown keep working after a restart.
        for record in posts.load_pending():
            if record.get("staff_message_id"):
                self.bot.add_view(
                    HeldPostView(record["id"]), message_id=record["staff_message_id"]
                )
        self.watch.start()

    async def cog_unload(self) -> None:
        self.watch.cancel()

    @tasks.loop(seconds=CHECK_SECONDS)
    async def watch(self) -> None:
        for record in await asyncio.to_thread(posts.load_pending):
            try:
                await self.handle(record)
            except Exception:
                print(f"Discord: could not handle the held post {record['id']}:\n{format_exc()}")

    @watch.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()

    async def handle(self, record: dict) -> None:
        if time.time() >= record["publish_at"]:
            # its time. (a failed publish is retried on the next check)
            if await asyncio.to_thread(posts.publish, record["id"]):
                await self.update_staff_message(record, "published automatically")
            return

        if record.get("staff_message_id") is None:
            await self.show(record)
        elif record["synced_revision"] != record["revision"]:
            await self.show(record, edit=True)

    async def channel(self):
        channel_id = posts.staff_channel_id()
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    def files(self, record: dict) -> list[discord.File]:
        image = posts.image_bytes(record)
        if image is None:
            return []
        import io

        return [discord.File(io.BytesIO(image), filename=record.get("filename") or "post.png")]

    async def show(self, record: dict, edit: bool = False) -> None:
        """sends (or refreshes, when a newer version came in) the message in the staff channel."""
        channel = await self.channel()
        content = staff_content(record)
        mentions = AllowedMentions(
            users=[discord.Object(id=owner) for owner in owner_ids()],
            roles=False,
            everyone=False,
        )
        view = HeldPostView(record["id"])
        if edit:
            message = await channel.fetch_message(record["staff_message_id"])
            await message.edit(
                content=content,
                attachments=self.files(record),
                view=view,
                allowed_mentions=mentions,
            )
        else:
            message = await channel.send(
                content,
                files=self.files(record),
                view=view,
                allowed_mentions=mentions,
            )
        await asyncio.to_thread(
            posts.update,
            record["id"],
            staff_message_id=message.id,
            synced_revision=record["revision"],
        )

    async def update_staff_message(self, record: dict, status: str) -> None:
        """marks the staff message as done, and takes the buttons away."""
        if not record.get("staff_message_id"):
            return
        try:
            channel = await self.channel()
            message = await channel.fetch_message(record["staff_message_id"])
            await message.edit(
                content=staff_content(record, status),
                view=None,
                allowed_mentions=AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass
