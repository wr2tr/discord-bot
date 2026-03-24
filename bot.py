import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import timedelta

# ─────────────────────────────────────────────
#  CONFIG  (edit these before running)
# ─────────────────────────────────────────────
import os
TOKEN = os.getenv("TOKEN")
PREFIX       = "!"          # classic prefix for text commands
CONFIG_FILE  = "config.json"

# ─────────────────────────────────────────────
#  PERSISTENT CONFIG  (saved to config.json)
# ─────────────────────────────────────────────
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
intents.members   = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
warnings: dict[int, list[str]] = {}   # user_id -> list of reasons


# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"    Serving {len(bot.guilds)} guild(s)")


@bot.event
async def on_member_join(member: discord.Member):
    guild_cfg = config.get(str(member.guild.id), {})

    # ── Auto-role ──
    autorole_id = guild_cfg.get("autorole")
    if autorole_id:
        role = member.guild.get_role(int(autorole_id))
        if role:
            await member.add_roles(role, reason="Auto-role on join")

    # ── Welcome message ──
    welcome_channel_id = guild_cfg.get("welcome_channel")
    welcome_message    = guild_cfg.get("welcome_message",
                            "Welcome to the server, {mention}! 🎉")
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
#  SETUP COMMANDS  (admin only)
# ══════════════════════════════════════════════

@bot.command(name="setautorole")
@commands.has_permissions(administrator=True)
async def set_autorole(ctx, role: discord.Role):
    """Set the role given automatically to new members."""
    guild_cfg = config.setdefault(str(ctx.guild.id), {})
    guild_cfg["autorole"] = str(role.id)
    save_config(config)
    await ctx.send(f"✅ Auto-role set to **{role.name}**.")


@bot.command(name="setwelcome")
@commands.has_permissions(administrator=True)
async def set_welcome(ctx, channel: discord.TextChannel, *, message: str = None):
    """Set the welcome channel (and optionally a custom message).
    Use {mention}, {name}, {server} as placeholders."""
    guild_cfg = config.setdefault(str(ctx.guild.id), {})
    guild_cfg["welcome_channel"] = str(channel.id)
    if message:
        guild_cfg["welcome_message"] = message
    save_config(config)
    await ctx.send(f"✅ Welcome channel set to {channel.mention}.")


@bot.command(name="setleave")
@commands.has_permissions(administrator=True)
async def set_leave(ctx, channel: discord.TextChannel):
    """Set the channel for goodbye messages."""
    guild_cfg = config.setdefault(str(ctx.guild.id), {})
    guild_cfg["leave_channel"] = str(channel.id)
    save_config(config)
    await ctx.send(f"✅ Leave channel set to {channel.mention}.")


# ══════════════════════════════════════════════
#  MODERATION COMMANDS
# ══════════════════════════════════════════════

# ── Kick ──────────────────────────────────────
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member.display_name}** has been kicked.\n📝 Reason: {reason}")


# ── Ban ───────────────────────────────────────
@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason, delete_message_days=0)
    await ctx.send(f"🔨 **{member.display_name}** has been banned.\n📝 Reason: {reason}")


# ── Unban ─────────────────────────────────────
@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, user_tag: str):
    """Unban by Username#Discriminator or user ID."""
    banned = [entry async for entry in ctx.guild.bans()]

    # Try by ID first
    if user_tag.isdigit():
        for entry in banned:
            if entry.user.id == int(user_tag):
                await ctx.guild.unban(entry.user)
                return await ctx.send(f"✅ Unbanned **{entry.user}**.")
        return await ctx.send("❌ No banned user found with that ID.")

    # Try by name#discriminator
    name, _, disc = user_tag.partition("#")
    for entry in banned:
        if entry.user.name == name and (not disc or entry.user.discriminator == disc):
            await ctx.guild.unban(entry.user)
            return await ctx.send(f"✅ Unbanned **{entry.user}**.")
    await ctx.send("❌ No banned user found with that tag.")


# ── Timeout / Mute (uses Discord's built-in timeout) ──
@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int = 10,
               *, reason: str = "No reason provided"):
    """Timeout a member for X minutes (default 10)."""
    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await ctx.send(f"🔇 **{member.display_name}** has been muted for **{minutes} min**.\n📝 Reason: {reason}")


@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    """Remove a member's timeout."""
    await member.timeout(None)
    await ctx.send(f"🔊 **{member.display_name}** has been unmuted.")


# ── Warn ──────────────────────────────────────
@bot.command(name="warn")
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    warnings.setdefault(member.id, []).append(reason)
    count = len(warnings[member.id])
    await ctx.send(f"⚠️ **{member.display_name}** has been warned (total: {count}).\n📝 Reason: {reason}")
    try:
        await member.send(f"⚠️ You were warned in **{ctx.guild.name}**: {reason}")
    except discord.Forbidden:
        pass


@bot.command(name="warnings")
@commands.has_permissions(kick_members=True)
async def show_warnings(ctx, member: discord.Member):
    user_warns = warnings.get(member.id, [])
    if not user_warns:
        return await ctx.send(f"✅ **{member.display_name}** has no warnings.")
    embed = discord.Embed(title=f"Warnings for {member.display_name}",
                          color=discord.Color.orange())
    for i, reason in enumerate(user_warns, 1):
        embed.add_field(name=f"Warning {i}", value=reason, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="clearwarnings")
@commands.has_permissions(administrator=True)
async def clear_warnings(ctx, member: discord.Member):
    warnings.pop(member.id, None)
    await ctx.send(f"✅ Cleared all warnings for **{member.display_name}**.")


# ── Purge / Clear messages ────────────────────
@bot.command(name="purge", aliases=["clear"])
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """Delete the last X messages in this channel (max 100)."""
    amount = min(amount, 100)
    deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to include the command msg
    msg = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** messages.")
    await msg.delete(delay=4)


# ── Slowmode ──────────────────────────────────
@bot.command(name="slowmode")
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    """Set channel slowmode (0 to disable)."""
    await ctx.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await ctx.send("✅ Slowmode disabled.")
    else:
        await ctx.send(f"✅ Slowmode set to **{seconds} seconds**.")


# ── Lock / Unlock channel ─────────────────────
@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔒 Channel locked.")


@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔓 Channel unlocked.")


# ══════════════════════════════════════════════
#  ROLE MANAGEMENT
# ══════════════════════════════════════════════

@bot.command(name="addrole")
@commands.has_permissions(manage_roles=True)
async def add_role(ctx, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"✅ Added **{role.name}** to **{member.display_name}**.")


@bot.command(name="removerole")
@commands.has_permissions(manage_roles=True)
async def remove_role(ctx, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await ctx.send(f"✅ Removed **{role.name}** from **{member.display_name}**.")


# ══════════════════════════════════════════════
#  INFO COMMANDS
# ══════════════════════════════════════════════

@bot.command(name="userinfo")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"User Info – {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",          value=member.id,                          inline=True)
    embed.add_field(name="Nickname",    value=member.nick or "None",              inline=True)
    embed.add_field(name="Joined Server",
                    value=member.joined_at.strftime("%Y-%m-%d"),                  inline=True)
    embed.add_field(name="Account Created",
                    value=member.created_at.strftime("%Y-%m-%d"),                 inline=True)
    embed.add_field(name=f"Roles ({len(roles)})",
                    value=" ".join(roles) if roles else "None",                   inline=False)
    await ctx.send(embed=embed)


@bot.command(name="serverinfo")
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",    value=g.owner.mention,         inline=True)
    embed.add_field(name="Members",  value=g.member_count,          inline=True)
    embed.add_field(name="Channels", value=len(g.channels),         inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),            inline=True)
    embed.add_field(name="Created",
                    value=g.created_at.strftime("%Y-%m-%d"),        inline=True)
    await ctx.send(embed=embed)


@bot.command(name="avatar")
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}'s Avatar",
                          color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)


# ══════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"🏓 Pong! Latency: **{round(bot.latency * 1000)} ms**")


@bot.command(name="help2")          # renamed so it doesn't clash with built-in
async def help_cmd(ctx):
    embed = discord.Embed(title="📖 Bot Commands", color=discord.Color.green())
    embed.add_field(name="⚙️ Setup (Admin)",
        value="`!setautorole <role>` · `!setwelcome <#channel> [msg]` · `!setleave <#channel>`",
        inline=False)
    embed.add_field(name="🔨 Moderation",
        value=("`!kick <member> [reason]`\n"
               "`!ban <member> [reason]`\n"
               "`!unban <name#disc or id>`\n"
               "`!mute <member> [minutes] [reason]`\n"
               "`!unmute <member>`\n"
               "`!warn <member> [reason]`\n"
               "`!warnings <member>`\n"
               "`!clearwarnings <member>`\n"
               "`!purge <amount>`\n"
               "`!slowmode <seconds>`\n"
               "`!lock` · `!unlock`"),
        inline=False)
    embed.add_field(name="🏷️ Roles",
        value="`!addrole <member> <role>` · `!removerole <member> <role>`",
        inline=False)
    embed.add_field(name="ℹ️ Info",
        value="`!userinfo [member]` · `!serverinfo` · `!avatar [member]` · `!ping`",
        inline=False)
    await ctx.send(embed=embed)


# ══════════════════════════════════════════════
#  ERROR HANDLING
# ══════════════════════════════════════════════

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `!help2` for usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.")
    elif isinstance(error, commands.RoleNotFound):
        await ctx.send("❌ Role not found.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}")
    else:
        raise error


# ══════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════

bot.run(TOKEN)
