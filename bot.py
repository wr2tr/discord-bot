import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import timedelta

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
TOKEN       = os.getenv("TOKEN")
PREFIX      = "!"
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

config = load_config()

# ─────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
warnings: dict[int, list[str]] = {}


# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"    Slash commands synced! Type / in Discord to see them.")


@bot.event
async def on_member_join(member: discord.Member):
    guild_cfg = config.get(str(member.guild.id), {})

    autorole_id = guild_cfg.get("autorole")
    if autorole_id:
        role = member.guild.get_role(int(autorole_id))
        if role:
            await member.add_roles(role, reason="Auto-role on join")

    welcome_channel_id = guild_cfg.get("welcome_channel")
    welcome_message    = guild_cfg.get("welcome_message", "Welcome to the server, {mention}! 🎉")
    if welcome_channel_id:
        channel = member.guild.get_channel(int(welcome_channel_id))
        if channel:
            msg = welcome_message.replace("{mention}", member.mention) \
                                 .replace("{name}", member.display_name) \
                                 .replace("{server}", member.guild.name)
            await channel.send(msg)


@bot.event
async def on_member_remove(member: discord.Member):
    guild_cfg = config.get(str(member.guild.id), {})
    leave_channel_id = guild_cfg.get("leave_channel")
    if leave_channel_id:
        channel = member.guild.get_channel(int(leave_channel_id))
        if channel:
            await channel.send(f"👋 **{member.display_name}** has left the server.")


# ══════════════════════════════════════════════
#  SLASH COMMANDS
# ══════════════════════════════════════════════

@bot.tree.command(name="ping", description="Check the bot's latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latency: **{round(bot.latency * 1000)} ms**")


@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="The member to kick", reason="Reason for the kick")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 **{member.display_name}** has been kicked.\n📝 Reason: {reason}")


@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="The member to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason, delete_message_days=0)
    await interaction.response.send_message(f"🔨 **{member.display_name}** has been banned.\n📝 Reason: {reason}")


@bot.tree.command(name="unban", description="Unban a user by their ID")
@app_commands.describe(user_id="The user's ID to unban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str):
    banned = [entry async for entry in interaction.guild.bans()]
    if user_id.isdigit():
        for entry in banned:
            if entry.user.id == int(user_id):
                await interaction.guild.unban(entry.user)
                return await interaction.response.send_message(f"✅ Unbanned **{entry.user}**.")
    await interaction.response.send_message("❌ No banned user found with that ID.")


@bot.tree.command(name="mute", description="Timeout (mute) a member")
@app_commands.describe(member="The member to mute", minutes="How many minutes (default 10)", reason="Reason for the mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason provided"):
    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await interaction.response.send_message(f"🔇 **{member.display_name}** has been muted for **{minutes} min**.\n📝 Reason: {reason}")


@bot.tree.command(name="unmute", description="Remove a member's timeout/mute")
@app_commands.describe(member="The member to unmute")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 **{member.display_name}** has been unmuted.")


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="The member to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    warnings.setdefault(member.id, []).append(reason)
    count = len(warnings[member.id])
    await interaction.response.send_message(f"⚠️ **{member.display_name}** has been warned (total: {count}).\n📝 Reason: {reason}")
    try:
        await member.send(f"⚠️ You were warned in **{interaction.guild.name}**: {reason}")
    except discord.Forbidden:
        pass


@bot.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="The member to check warnings for")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    user_warns = warnings.get(member.id, [])
    if not user_warns:
        return await interaction.response.send_message(f"✅ **{member.display_name}** has no warnings.")
    embed = discord.Embed(title=f"Warnings for {member.display_name}", color=discord.Color.orange())
    for i, reason in enumerate(user_warns, 1):
        embed.add_field(name=f"Warning {i}", value=reason, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member")
@app_commands.describe(member="The member to clear warnings for")
@app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    warnings.pop(member.id, None)
    await interaction.response.send_message(f"✅ Cleared all warnings for **{member.display_name}**.")


@bot.tree.command(name="purge", description="Delete a number of messages in this channel")
@app_commands.describe(amount="Number of messages to delete (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    amount = min(amount, 100)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)


@bot.tree.command(name="slowmode", description="Set slowmode for this channel")
@app_commands.describe(seconds="Seconds between messages (0 to disable)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await interaction.response.send_message("✅ Slowmode disabled.")
    else:
        await interaction.response.send_message(f"✅ Slowmode set to **{seconds} seconds**.")


@bot.tree.command(name="lock", description="Lock this channel so nobody can send messages")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_lock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔒 Channel locked.")


@bot.tree.command(name="unlock", description="Unlock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_unlock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔓 Channel unlocked.")


@bot.tree.command(name="addrole", description="Give a role to a member")
@app_commands.describe(member="The member to give the role to", role="The role to give")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ Added **{role.name}** to **{member.display_name}**.")


@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(member="The member to remove the role from", role="The role to remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ Removed **{role.name}** from **{member.display_name}**.")


@bot.tree.command(name="userinfo", description="Get info about a member")
@app_commands.describe(member="The member to look up (leave empty for yourself)")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"User Info – {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",              value=member.id,                              inline=True)
    embed.add_field(name="Nickname",        value=member.nick or "None",                  inline=True)
    embed.add_field(name="Joined Server",   value=member.joined_at.strftime("%Y-%m-%d"),  inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Get info about this server")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",    value=g.owner.mention,                   inline=True)
    embed.add_field(name="Members",  value=g.member_count,                    inline=True)
    embed.add_field(name="Channels", value=len(g.channels),                   inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),                      inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.describe(member="The member whose avatar to show (leave empty for yourself)")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setautorole", description="Set a role to auto-give to new members")
@app_commands.describe(role="The role to give new members")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setautorole(interaction: discord.Interaction, role: discord.Role):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["autorole"] = str(role.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Auto-role set to **{role.name}**.")


@bot.tree.command(name="setwelcome", description="Set the welcome channel and message")
@app_commands.describe(channel="The channel for welcome messages", message="Custom message (use {mention}, {name}, {server})")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["welcome_channel"] = str(channel.id)
    if message:
        guild_cfg["welcome_message"] = message
    save_config(config)
    await interaction.response.send_message(f"✅ Welcome channel set to {channel.mention}.")


@bot.tree.command(name="setleave", description="Set the channel for goodbye messages")
@app_commands.describe(channel="The channel for goodbye messages")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setleave(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["leave_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Leave channel set to {channel.mention}.")


@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Bot Commands", color=discord.Color.green(),
                          description="All commands work with `/`")
    embed.add_field(name="⚙️ Setup (Admin)",
        value="`/setautorole` · `/setwelcome` · `/setleave`", inline=False)
    embed.add_field(name="🔨 Moderation",
        value="`/kick` · `/ban` · `/unban` · `/mute` · `/unmute`\n`/warn` · `/warnings` · `/clearwarnings`\n`/purge` · `/slowmode` · `/lock` · `/unlock`",
        inline=False)
    embed.add_field(name="🏷️ Roles",
        value="`/addrole` · `/removerole`", inline=False)
    embed.add_field(name="ℹ️ Info",
        value="`/userinfo` · `/serverinfo` · `/avatar` · `/ping`", inline=False)
    await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════
#  ERROR HANDLING
# ══════════════════════════════════════════════

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)


# ══════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════

bot.run(TOKEN)
