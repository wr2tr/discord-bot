import discord
from discord.ext import commands, tasks
from discord import app_commands
import json, os, random, asyncio, datetime
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

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.reactions       = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# In-memory stores
warnings   = {}   # user_id -> [reasons]
xp_data    = {}   # guild_id -> user_id -> {xp, level}
economy    = {}   # user_id -> {coins, last_daily}
afk_users  = {}   # user_id -> reason
snipe_data = {}   # channel_id -> {content, author, time}
reminders  = []   # [{user_id, channel_id, time, message}]
giveaways  = {}   # message_id -> {prize, winners, end_time, channel_id}

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════

def get_xp(guild_id, user_id):
    return xp_data.setdefault(str(guild_id), {}).setdefault(str(user_id), {"xp": 0, "level": 1})

def xp_for_level(level):
    return level * 100

def get_coins(user_id):
    return economy.setdefault(str(user_id), {"coins": 0, "last_daily": None})

# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    bot.add_view(TicketButton())
    bot.add_view(CloseTicketButton())
    check_reminders.start()
    check_giveaways.start()
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — All systems online!")

@bot.event
async def on_member_join(member):
    guild_cfg = config.get(str(member.guild.id), {})

    autorole_id = os.getenv("AUTOROLE_ID") or guild_cfg.get("autorole")
    if autorole_id:
        role = member.guild.get_role(int(autorole_id))
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except Exception as e:
                print(f"Autorole error: {e}")

    welcome_channel_id = os.getenv("WELCOME_CHANNEL_ID") or guild_cfg.get("welcome_channel")
    welcome_message = os.getenv("WELCOME_MESSAGE") or guild_cfg.get("welcome_message", "Welcome {mention}!")
    if welcome_channel_id:
        channel = member.guild.get_channel(int(welcome_channel_id))
        if channel:
            msg = welcome_message.replace("{mention}", member.mention).replace("{name}", member.display_name).replace("{server}", member.guild.name)
            await channel.send(msg)

@bot.event
async def on_member_remove(member):
    guild_cfg = config.get(str(member.guild.id), {})
    leave_channel_id = guild_cfg.get("leave_channel")
    if leave_channel_id:
        channel = member.guild.get_channel(int(leave_channel_id))
        if channel:
            await channel.send(f"👋 **{member.display_name}** has left the server.")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # AFK check — mention of AFK user
    for mention in message.mentions:
        if mention.id in afk_users:
            await message.channel.send(f"💤 **{mention.display_name}** is AFK: {afk_users[mention.id]}")

    # Remove AFK if user sends a message
    if message.author.id in afk_users:
        del afk_users[message.author.id]
        await message.channel.send(f"✅ Welcome back {message.author.mention}! AFK removed.")

    # XP gain
    if not message.guild:
        return await bot.process_commands(message)
    data = get_xp(message.guild.id, message.author.id)
    data["xp"] += random.randint(5, 15)
    if data["xp"] >= xp_for_level(data["level"]):
        data["xp"] = 0
        data["level"] += 1
        guild_cfg = config.get(str(message.guild.id), {})
        lvl_channel_id = guild_cfg.get("level_channel")
        lvl_channel = message.guild.get_channel(int(lvl_channel_id)) if lvl_channel_id else message.channel
        await lvl_channel.send(f"🎉 {message.author.mention} leveled up to **Level {data['level']}**!")

    await bot.process_commands(message)

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    snipe_data[message.channel.id] = {
        "content": message.content,
        "author": str(message.author),
        "avatar": str(message.author.display_avatar.url),
        "time": discord.utils.utcnow()
    }

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    guild_cfg = config.get(str(reaction.message.guild.id), {})
    rr = guild_cfg.get("reaction_roles", {})
    msg_id = str(reaction.message.id)
    if msg_id in rr:
        emoji_str = str(reaction.emoji)
        if emoji_str in rr[msg_id]:
            role = reaction.message.guild.get_role(int(rr[msg_id][emoji_str]))
            if role:
                await user.add_roles(role)

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    guild_cfg = config.get(str(reaction.message.guild.id), {})
    rr = guild_cfg.get("reaction_roles", {})
    msg_id = str(reaction.message.id)
    if msg_id in rr:
        emoji_str = str(reaction.emoji)
        if emoji_str in rr[msg_id]:
            role = reaction.message.guild.get_role(int(rr[msg_id][emoji_str]))
            if role:
                await user.remove_roles(role)

# ══════════════════════════════════════════════
#  BACKGROUND TASKS
# ══════════════════════════════════════════════

@tasks.loop(seconds=30)
async def check_reminders():
    now = discord.utils.utcnow()
    done = []
    for r in reminders:
        if now >= r["time"]:
            channel = bot.get_channel(r["channel_id"])
            if channel:
                await channel.send(f"⏰ <@{r['user_id']}> Reminder: **{r['message']}**")
            done.append(r)
    for r in done:
        reminders.remove(r)

@tasks.loop(seconds=15)
async def check_giveaways():
    now = discord.utils.utcnow()
    ended = []
    for msg_id, g in giveaways.items():
        if now >= g["end_time"]:
            channel = bot.get_channel(g["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(int(msg_id))
                    reaction = discord.utils.get(msg.reactions, emoji="🎉")
                    if reaction:
                        users = [u async for u in reaction.users() if not u.bot]
                        if users:
                            winners = random.sample(users, min(g["winners"], len(users)))
                            winner_mentions = ", ".join(w.mention for w in winners)
                            await channel.send(f"🎉 Giveaway ended! Congratulations {winner_mentions}! You won **{g['prize']}**!")
                        else:
                            await channel.send(f"🎉 Giveaway ended but nobody entered for **{g['prize']}**.")
                except:
                    pass
            ended.append(msg_id)
    for m in ended:
        del giveaways[m]

# ══════════════════════════════════════════════
#  TICKET SYSTEM
# ══════════════════════════════════════════════

class CloseTicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        is_admin = interaction.user.guild_permissions.administrator
        is_creator = str(interaction.user.id) in (channel.topic or "")
        if not is_admin and not is_creator:
            return await interaction.response.send_message("Only the ticket creator or an admin can close this.", ephemeral=True)
        await interaction.response.send_message("🔒 Closing in 5 seconds...")
        await asyncio.sleep(5)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")

class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        guild_cfg = config.setdefault(str(guild.id), {})
        ticket_count = guild_cfg.get("ticket_count", 0) + 1
        guild_cfg["ticket_count"] = ticket_count
        save_config(config)

        existing = discord.utils.get(guild.text_channels, topic=f"ticket-owner-{interaction.user.id}")
        if existing:
            return await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            category = await guild.create_category("Tickets", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })

        channel = await guild.create_text_channel(
            name=f"ticket-{ticket_count:04d}",
            category=category,
            overwrites=overwrites,
            topic=f"ticket-owner-{interaction.user.id}"
        )
        embed = discord.Embed(title=f"🎫 Ticket #{ticket_count:04d}", description=f"Welcome {interaction.user.mention}!\n\nDescribe your issue and staff will assist shortly.\n\nClick below to close the ticket.", color=discord.Color.green())
        embed.set_footer(text=f"Ticket by {interaction.user.display_name}")
        await channel.send(embed=embed, view=CloseTicketButton())
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

# ══════════════════════════════════════════════
#  SLASH COMMANDS
# ══════════════════════════════════════════════

# ── TICKET SETUP ──────────────────────────────
@bot.tree.command(name="ticketsetup", description="Set up the ticket panel")
@app_commands.describe(channel="Channel for the panel", title="Panel title", description="Panel description")
@app_commands.checks.has_permissions(administrator=True)
async def slash_ticketsetup(interaction: discord.Interaction, channel: discord.TextChannel, title: str = "🎫 Support Tickets", description: str = "Click the button below to open a support ticket!"):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.set_footer(text=interaction.guild.name)
    await channel.send(embed=embed, view=TicketButton())
    await interaction.response.send_message(f"✅ Ticket panel sent to {channel.mention}!", ephemeral=True)

# ── MODERATION ────────────────────────────────
@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Kicked **{member.display_name}**. Reason: {reason}")

@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason, delete_message_days=0)
    await interaction.response.send_message(f"🔨 Banned **{member.display_name}**. Reason: {reason}")

@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str):
    banned = [entry async for entry in interaction.guild.bans()]
    if user_id.isdigit():
        for entry in banned:
            if entry.user.id == int(user_id):
                await interaction.guild.unban(entry.user)
                return await interaction.response.send_message(f"✅ Unbanned **{entry.user}**.")
    await interaction.response.send_message("❌ Not found.")

@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(member="Member", minutes="Minutes (default 10)", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason"):
    await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"🔇 Muted **{member.display_name}** for {minutes} min. Reason: {reason}")

@bot.tree.command(name="unmute", description="Remove a member timeout")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 Unmuted **{member.display_name}**.")

@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    warnings.setdefault(member.id, []).append(reason)
    count = len(warnings[member.id])
    await interaction.response.send_message(f"⚠️ Warned **{member.display_name}** ({count} total). Reason: {reason}")
    try:
        await member.send(f"⚠️ You were warned in **{interaction.guild.name}**: {reason}")
    except:
        pass

@bot.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    user_warns = warnings.get(member.id, [])
    if not user_warns:
        return await interaction.response.send_message(f"✅ {member.display_name} has no warnings.")
    embed = discord.Embed(title=f"Warnings for {member.display_name}", color=discord.Color.orange())
    for i, r in enumerate(user_warns, 1):
        embed.add_field(name=f"Warning {i}", value=r, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarnings", description="Clear warnings for a member")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    warnings.pop(member.id, None)
    await interaction.response.send_message(f"✅ Cleared warnings for **{member.display_name}**.")

@bot.tree.command(name="purge", description="Delete messages")
@app_commands.describe(amount="Amount (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    amount = min(amount, 100)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.describe(seconds="Seconds (0 = off)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message("✅ Slowmode disabled." if seconds == 0 else f"✅ Slowmode set to {seconds}s.")

@bot.tree.command(name="lock", description="Lock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_lock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("🔒 Channel locked.")

@bot.tree.command(name="unlock", description="Unlock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_unlock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("🔓 Channel unlocked.")

@bot.tree.command(name="nick", description="Change a member's nickname")
@app_commands.describe(member="Member", nickname="New nickname (leave empty to reset)")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def slash_nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    await member.edit(nick=nickname)
    await interaction.response.send_message(f"✅ Nickname {'reset' if not nickname else f'changed to **{nickname}**'} for {member.display_name}.")

# ── ROLES ─────────────────────────────────────
@bot.tree.command(name="addrole", description="Give a role to a member")
@app_commands.describe(member="Member", role="Role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ Added **{role.name}** to **{member.display_name}**.")

@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(member="Member", role="Role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ Removed **{role.name}** from **{member.display_name}**.")

# ── SETUP ─────────────────────────────────────
@bot.tree.command(name="setautorole", description="Auto-give a role to new members")
@app_commands.describe(role="Role to auto-give")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setautorole(interaction: discord.Interaction, role: discord.Role):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["autorole"] = str(role.id)
    save_config(config)
    embed = discord.Embed(title="✅ Auto-Role Set!", color=discord.Color.green())
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.add_field(name="Make it permanent on Railway", value=f"Add variable: `AUTOROLE_ID` = `{role.id}`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setwelcome", description="Set welcome channel and message")
@app_commands.describe(channel="Channel", message="Message ({mention} {name} {server})")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["welcome_channel"] = str(channel.id)
    if message:
        guild_cfg["welcome_message"] = message
    save_config(config)
    await interaction.response.send_message(f"✅ Welcome channel set to {channel.mention}.")

@bot.tree.command(name="setleave", description="Set leave channel")
@app_commands.describe(channel="Channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setleave(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["leave_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Leave channel set to {channel.mention}.")

@bot.tree.command(name="setlevelchannel", description="Set the channel for level up messages")
@app_commands.describe(channel="Channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setlevelchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["level_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Level up messages will appear in {channel.mention}.")

# ── INFO ──────────────────────────────────────
@bot.tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! **{round(bot.latency * 1000)}ms**")

@bot.tree.command(name="userinfo", description="Get info about a member")
@app_commands.describe(member="Member")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"User Info — {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",      value=member.id,                              inline=True)
    embed.add_field(name="Joined",  value=member.joined_at.strftime("%Y-%m-%d"),  inline=True)
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Get server info")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",    value=g.owner.mention,  inline=True)
    embed.add_field(name="Members",  value=g.member_count,   inline=True)
    embed.add_field(name="Channels", value=len(g.channels),  inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),     inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.describe(member="Member")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="snipe", description="Show the last deleted message in this channel")
async def slash_snipe(interaction: discord.Interaction):
    data = snipe_data.get(interaction.channel.id)
    if not data:
        return await interaction.response.send_message("❌ Nothing to snipe!")
    embed = discord.Embed(description=data["content"], color=discord.Color.red(), timestamp=data["time"])
    embed.set_author(name=data["author"], icon_url=data["avatar"])
    embed.set_footer(text="Deleted message")
    await interaction.response.send_message(embed=embed)

# ── LEVELING ──────────────────────────────────
@bot.tree.command(name="level", description="Check your or someone's level")
@app_commands.describe(member="Member")
async def slash_level(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    data = get_xp(interaction.guild.id, member.id)
    needed = xp_for_level(data["level"])
    embed = discord.Embed(title=f"Level — {member.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=data["level"], inline=True)
    embed.add_field(name="XP",    value=f"{data['xp']} / {needed}", inline=True)
    bar_filled = int((data["xp"] / needed) * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    embed.add_field(name="Progress", value=f"`{bar}`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the XP leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    guild_xp = xp_data.get(str(interaction.guild.id), {})
    sorted_users = sorted(guild_xp.items(), key=lambda x: (x[1]["level"], x[1]["xp"]), reverse=True)[:10]
    embed = discord.Embed(title=f"🏆 XP Leaderboard — {interaction.guild.name}", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, d) in enumerate(sorted_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        embed.add_field(name=f"{medal} {name}", value=f"Level {d['level']} • {d['xp']} XP", inline=False)
    await interaction.response.send_message(embed=embed)

# ── ECONOMY ───────────────────────────────────
@bot.tree.command(name="balance", description="Check your coin balance")
@app_commands.describe(member="Member")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    data = get_coins(member.id)
    embed = discord.Embed(title=f"💰 Balance — {member.display_name}", color=discord.Color.yellow())
    embed.add_field(name="Coins", value=f"🪙 {data['coins']}", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Claim your daily coins")
async def slash_daily(interaction: discord.Interaction):
    data = get_coins(interaction.user.id)
    now = discord.utils.utcnow()
    last = data.get("last_daily")
    if last:
        last_dt = datetime.datetime.fromisoformat(last)
        diff = now - last_dt.replace(tzinfo=datetime.timezone.utc)
        if diff.total_seconds() < 86400:
            remaining = 86400 - diff.total_seconds()
            h, m = int(remaining // 3600), int((remaining % 3600) // 60)
            return await interaction.response.send_message(f"⏰ Come back in **{h}h {m}m** for your daily!")
    reward = random.randint(100, 500)
    data["coins"] += reward
    data["last_daily"] = now.isoformat()
    await interaction.response.send_message(f"✅ You claimed your daily reward of **🪙 {reward} coins**! Total: {data['coins']}")

@bot.tree.command(name="givemoney", description="Give coins to another member")
@app_commands.describe(member="Member", amount="Amount")
async def slash_givemoney(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive.")
    sender = get_coins(interaction.user.id)
    if sender["coins"] < amount:
        return await interaction.response.send_message("❌ You don't have enough coins!")
    sender["coins"] -= amount
    get_coins(member.id)["coins"] += amount
    await interaction.response.send_message(f"✅ Gave **🪙 {amount}** to {member.mention}!")

@bot.tree.command(name="gamble", description="Gamble your coins — double or nothing!")
@app_commands.describe(amount="Amount to gamble")
async def slash_gamble(interaction: discord.Interaction, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive.")
    data = get_coins(interaction.user.id)
    if data["coins"] < amount:
        return await interaction.response.send_message("❌ Not enough coins!")
    win = random.random() > 0.5
    if win:
        data["coins"] += amount
        await interaction.response.send_message(f"🎰 You won! **+🪙 {amount}**! New balance: {data['coins']}")
    else:
        data["coins"] -= amount
        await interaction.response.send_message(f"🎰 You lost! **-🪙 {amount}**! New balance: {data['coins']}")

@bot.tree.command(name="work", description="Work to earn some coins")
async def slash_work(interaction: discord.Interaction):
    data = get_coins(interaction.user.id)
    jobs = ["programmer", "chef", "plumber", "streamer", "driver", "teacher", "doctor"]
    job = random.choice(jobs)
    earned = random.randint(20, 100)
    data["coins"] += earned
    await interaction.response.send_message(f"💼 You worked as a **{job}** and earned **🪙 {earned} coins**! Total: {data['coins']}")

# ── FUN ───────────────────────────────────────
@bot.tree.command(name="8ball", description="Ask the magic 8ball a question")
@app_commands.describe(question="Your question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.",
        "Yes definitely.", "You may rely on it.", "As I see it, yes.",
        "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
        "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
        "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.",
        "Outlook not so good.", "Very doubtful."
    ]
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer",   value=random.choice(responses), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="coinflip", description="Flip a coin")
async def slash_coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads 🪙", "Tails 🦅"])
    await interaction.response.send_message(f"🪙 The coin landed on **{result}**!")

@bot.tree.command(name="dice", description="Roll a dice")
@app_commands.describe(sides="Number of sides (default 6)")
async def slash_dice(interaction: discord.Interaction, sides: int = 6):
    result = random.randint(1, sides)
    await interaction.response.send_message(f"🎲 You rolled a **{result}** (d{sides})!")

@bot.tree.command(name="rps", description="Play Rock Paper Scissors")
@app_commands.describe(choice="Your choice")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock 🪨", value="rock"),
    app_commands.Choice(name="Paper 📄", value="paper"),
    app_commands.Choice(name="Scissors ✂️", value="scissors"),
])
async def slash_rps(interaction: discord.Interaction, choice: str):
    bot_choice = random.choice(["rock", "paper", "scissors"])
    emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if choice == bot_choice:
        result = "It's a tie! 🤝"
    elif wins[choice] == bot_choice:
        result = "You win! 🎉"
    else:
        result = "You lose! 😔"
    await interaction.response.send_message(f"You: {emojis[choice]} vs Bot: {emojis[bot_choice]}\n**{result}**")

@bot.tree.command(name="joke", description="Get a random joke")
async def slash_joke(interaction: discord.Interaction):
    jokes = [
        ("Why don't scientists trust atoms?", "Because they make up everything!"),
        ("Why did the scarecrow win an award?", "Because he was outstanding in his field!"),
        ("I told my wife she was drawing her eyebrows too high.", "She looked surprised."),
        ("Why don't eggs tell jokes?", "They'd crack each other up!"),
        ("What do you call a fish without eyes?", "A fsh!"),
        ("Why can't you give Elsa a balloon?", "Because she'll let it go!"),
        ("What's a computer's favorite snack?", "Microchips!"),
    ]
    setup, punchline = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke", color=discord.Color.yellow())
    embed.add_field(name=setup, value=f"||{punchline}||", inline=False)
    embed.set_footer(text="Click the spoiler to reveal the punchline!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="fact", description="Get a random fun fact")
async def slash_fact(interaction: discord.Interaction):
    facts = [
        "Honey never spoils. Archaeologists have found 3000-year-old honey in Egyptian tombs!",
        "A group of flamingos is called a flamboyance.",
        "Octopuses have three hearts and blue blood.",
        "Bananas are berries, but strawberries are not.",
        "A day on Venus is longer than a year on Venus.",
        "The shortest war in history was between Britain and Zanzibar in 1896 — it lasted 38 minutes.",
        "Cows have best friends and get stressed when separated.",
        "A snail can sleep for 3 years.",
        "There are more stars in the universe than grains of sand on Earth.",
    ]
    embed = discord.Embed(title="🧠 Fun Fact", description=random.choice(facts), color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roast", description="Roast a member (all in good fun!)")
@app_commands.describe(member="Member to roast")
async def slash_roast(interaction: discord.Interaction, member: discord.Member):
    roasts = [
        "is so slow, they make a sloth look like a Formula 1 car.",
        "tried to enter a smart room and the door wouldn't open.",
        "has a face only a mother could love — and even she needs glasses.",
        "is the reason they put instructions on shampoo bottles.",
        "would need a map to find their way out of an empty room.",
        "brings so little to the table, they need a booster seat.",
    ]
    await interaction.response.send_message(f"🔥 {member.mention} {random.choice(roasts)}")

@bot.tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Member to compliment")
async def slash_compliment(interaction: discord.Interaction, member: discord.Member):
    compliments = [
        "is an absolute legend!",
        "brings sunshine to even the cloudiest days! ☀️",
        "is one of the most genuinely awesome people around!",
        "makes this server 10x better just by being here!",
        "has an amazing vibe and we're lucky to have them!",
        "is the definition of cool. Facts only. 😎",
    ]
    await interaction.response.send_message(f"💖 {member.mention} {random.choice(compliments)}")

@bot.tree.command(name="howcool", description="Check how cool someone is")
@app_commands.describe(member="Member")
async def slash_howcool(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    score = random.randint(0, 100)
    await interaction.response.send_message(f"😎 {member.mention} is **{score}% cool!**")

@bot.tree.command(name="ship", description="Ship two people together")
@app_commands.describe(member1="First person", member2="Second person")
async def slash_ship(interaction: discord.Interaction, member1: discord.Member, member2: discord.Member):
    score = random.randint(0, 100)
    hearts = "❤️" * (score // 20)
    await interaction.response.send_message(f"💘 **{member1.display_name}** + **{member2.display_name}** = **{score}% compatible!** {hearts}")

# ── UTILITY ───────────────────────────────────
@bot.tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question", option1="Option 1", option2="Option 2", option3="Option 3 (optional)", option4="Option 4 (optional)")
async def slash_poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    embed = discord.Embed(title=f"📊 {question}", color=discord.Color.blurple())
    for i, opt in enumerate(options):
        embed.add_field(name=f"{emojis[i]} {opt}", value="\u200b", inline=False)
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])

@bot.tree.command(name="announce", description="Send an announcement embed")
@app_commands.describe(channel="Channel", title="Title", message="Message", color="Color (red/green/blue/yellow/default)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_announce(interaction: discord.Interaction, channel: discord.TextChannel, title: str, message: str, color: str = "blue"):
    colors = {"red": discord.Color.red(), "green": discord.Color.green(), "blue": discord.Color.blue(), "yellow": discord.Color.yellow(), "default": discord.Color.blurple()}
    c = colors.get(color.lower(), discord.Color.blurple())
    embed = discord.Embed(title=title, description=message, color=c, timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"Announced by {interaction.user.display_name}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Announcement sent to {channel.mention}!", ephemeral=True)

@bot.tree.command(name="remind", description="Set a reminder")
@app_commands.describe(minutes="Minutes from now", message="What to remind you about")
async def slash_remind(interaction: discord.Interaction, minutes: int, message: str):
    remind_time = discord.utils.utcnow() + timedelta(minutes=minutes)
    reminders.append({"user_id": interaction.user.id, "channel_id": interaction.channel.id, "time": remind_time, "message": message})
    await interaction.response.send_message(f"⏰ I'll remind you about **{message}** in {minutes} minute(s)!", ephemeral=True)

@bot.tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(reason="Reason for being AFK")
async def slash_afk(interaction: discord.Interaction, reason: str = "AFK"):
    afk_users[interaction.user.id] = reason
    await interaction.response.send_message(f"💤 {interaction.user.mention} is now AFK: **{reason}**")

@bot.tree.command(name="giveaway", description="Start a giveaway")
@app_commands.describe(channel="Channel", prize="What you're giving away", minutes="Duration in minutes", winners="Number of winners")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_giveaway(interaction: discord.Interaction, channel: discord.TextChannel, prize: str, minutes: int, winners: int = 1):
    end_time = discord.utils.utcnow() + timedelta(minutes=minutes)
    embed = discord.Embed(title="🎉 GIVEAWAY!", description=f"**Prize:** {prize}\n\n**Winners:** {winners}\n**Ends in:** {minutes} minute(s)\n\nReact with 🎉 to enter!", color=discord.Color.gold(), timestamp=end_time)
    embed.set_footer(text=f"Ends at")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaways[str(msg.id)] = {"prize": prize, "winners": winners, "end_time": end_time, "channel_id": channel.id}
    await interaction.response.send_message(f"✅ Giveaway started in {channel.mention}!", ephemeral=True)

@bot.tree.command(name="reactionrole", description="Add a reaction role to a message")
@app_commands.describe(message_id="Message ID", emoji="Emoji to react with", role="Role to give")
@app_commands.checks.has_permissions(administrator=True)
async def slash_reactionrole(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    rr = guild_cfg.setdefault("reaction_roles", {})
    rr.setdefault(message_id, {})[emoji] = str(role.id)
    save_config(config)
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except:
        pass
    await interaction.response.send_message(f"✅ Reaction role set! React with {emoji} to get **{role.name}**.", ephemeral=True)

@bot.tree.command(name="embed", description="Send a custom embed message")
@app_commands.describe(channel="Channel", title="Title", description="Description", color="Color (red/green/blue/yellow)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_embed(interaction: discord.Interaction, channel: discord.TextChannel, title: str, description: str, color: str = "blue"):
    colors = {"red": discord.Color.red(), "green": discord.Color.green(), "blue": discord.Color.blue(), "yellow": discord.Color.yellow()}
    c = colors.get(color.lower(), discord.Color.blue())
    embed = discord.Embed(title=title, description=description, color=c)
    await channel.send(embed=embed)
    await interaction.response.send_message("✅ Embed sent!", ephemeral=True)

@bot.tree.command(name="say", description="Make the bot say something")
@app_commands.describe(channel="Channel", message="Message")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_say(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    await channel.send(message)
    await interaction.response.send_message("✅ Message sent!", ephemeral=True)

# ── HELP ──────────────────────────────────────
@bot.tree.command(name="help", description="Show all commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 All Commands", color=discord.Color.green(), description="Type `/` to see all commands with descriptions!")
    embed.add_field(name="🎫 Tickets",     value="`/ticketsetup`", inline=False)
    embed.add_field(name="🔨 Moderation",  value="`/kick` `/ban` `/unban` `/mute` `/unmute` `/warn` `/warnings` `/clearwarnings` `/purge` `/slowmode` `/lock` `/unlock` `/nick`", inline=False)
    embed.add_field(name="🏷️ Roles",       value="`/addrole` `/removerole` `/reactionrole`", inline=False)
    embed.add_field(name="⚙️ Setup",       value="`/setautorole` `/setwelcome` `/setleave` `/setlevelchannel`", inline=False)
    embed.add_field(name="📊 Leveling",    value="`/level` `/leaderboard`", inline=False)
    embed.add_field(name="💰 Economy",     value="`/balance` `/daily` `/work` `/gamble` `/givemoney`", inline=False)
    embed.add_field(name="🎉 Giveaways",   value="`/giveaway`", inline=False)
    embed.add_field(name="📢 Utility",     value="`/poll` `/announce` `/remind` `/afk` `/snipe` `/embed` `/say`", inline=False)
    embed.add_field(name="😂 Fun",         value="`/8ball` `/coinflip` `/dice` `/rps` `/joke` `/fact` `/roast` `/compliment` `/howcool` `/ship`", inline=False)
    embed.add_field(name="ℹ️ Info",        value="`/userinfo` `/serverinfo` `/avatar` `/ping`", inline=False)
    await interaction.response.send_message(embed=embed)

# ══════════════════════════════════════════════
#  ERROR HANDLING
# ══════════════════════════════════════════════

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)
    else:
        try:
            await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)
        except:
            pass

bot.run(TOKEN)
