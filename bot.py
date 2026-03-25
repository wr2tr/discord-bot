import discord
from discord.ext import commands, tasks
from discord import app_commands
import json, os, random, re, datetime
from datetime import timedelta
import asyncio

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
intents.voice_states    = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# In-memory stores
warnings    = {}   # user_id -> [reasons]
xp_data     = {}   # guild_id -> user_id -> {xp, level}
spam_track  = {}   # user_id -> [timestamps]
recent_join = []   # list of join timestamps for anti-raid
voice_track = {}   # user_id -> join datetime

BAD_WORDS = ["badword1", "badword2", "badword3"]  # ← Add your bad words here

def get_xp(guild_id, user_id):
    return xp_data.setdefault(str(guild_id), {}).setdefault(str(user_id), {"xp": 0, "level": 1})

def xp_for_level(level):
    # Starts easy, gets exponentially harder
    return int(100 * (1.4 ** (level - 1)))

# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    bot.add_view(TicketButton())
    bot.add_view(CloseTicketButton())
    update_stats.start()
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — All systems online!")
@tasks.loop(minutes=10)
async def update_stats():
    for guild in bot.guilds:
        guild_cfg = config.get(str(guild.id), {})
        channels = guild_cfg.get("stats_channels", {})
        if not channels:
            continue
        try:
            total   = guild.member_count
            humans  = sum(1 for m in guild.members if not m.bot)
            bots    = sum(1 for m in guild.members if m.bot)
            online  = sum(1 for m in guild.members if m.status != discord.Status.offline)

            if channels.get("members"):
                ch = guild.get_channel(int(channels["members"]))
                if ch: await ch.edit(name=f"👥 Members: {total}")
            if channels.get("humans"):
                ch = guild.get_channel(int(channels["humans"]))
                if ch: await ch.edit(name=f"👤 Humans: {humans}")
            if channels.get("bots"):
                ch = guild.get_channel(int(channels["bots"]))
                if ch: await ch.edit(name=f"🤖 Bots: {bots}")
            if channels.get("online"):
                ch = guild.get_channel(int(channels["online"]))
                if ch: await ch.edit(name=f"🟢 Online: {online}")
        except Exception as e:
            print(f"Stats update error: {e}")



@bot.event
async def on_member_join(member):
    # ── Anti-Raid ────────────────────────────
    guild_cfg_r = config.get(str(member.guild.id), {})
    if guild_cfg_r.get("automod_antiraid", False):
        now = datetime.datetime.utcnow()
        recent_join.append(now)
        recent_join[:] = [t for t in recent_join if (now - t).total_seconds() < 10]
        if len(recent_join) >= 5:
            try:
                await member.kick(reason="Auto-mod: Possible raid detected")
                log_ch_id = guild_cfg_r.get("log_channel")
                if log_ch_id:
                    lch = member.guild.get_channel(int(log_ch_id))
                    if lch:
                        await lch.send(f"🚨 Anti-raid kicked **{member}** — too many joins in 10 seconds!")
                return
            except:
                pass
    # ── Account age check (anti-raid) ──
    if guild_cfg_r.get("automod_antiraid", False):
        age = (datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
        min_age = guild_cfg_r.get("min_account_age", 7)
        if age < min_age:
            try:
                await member.kick(reason=f"Auto-mod: Account too new ({age} days old)")
                return
            except:
                pass

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
#  AUTO-MOD + XP ON MESSAGE
# ══════════════════════════════════════════════

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    guild_cfg = config.get(str(message.guild.id), {})
    member = message.author

    # ── Bad Word Filter ──────────────────────
    if guild_cfg.get("automod_badwords", False):
        content_lower = message.content.lower()
        custom_words = guild_cfg.get("bad_words", [])
        all_bad = BAD_WORDS + custom_words
        if any(word in content_lower for word in all_bad):
            try:
                await message.delete()
                warn_msg = await message.channel.send(f"🚫 {member.mention} Watch your language!", delete_after=5)
                warnings.setdefault(member.id, []).append("Auto-mod: Bad word")
            except:
                pass
            await bot.process_commands(message)
            return

    # ── Anti-Link ────────────────────────────
    if guild_cfg.get("automod_antilink", False):
        url_pattern = re.compile(r"(https?://|discord\.gg/|www\.)\S+")
        if url_pattern.search(message.content):
            if not member.guild_permissions.administrator:
                try:
                    await message.delete()
                    await message.channel.send(f"🔗 {member.mention} Links are not allowed here!", delete_after=5)
                except:
                    pass
                await bot.process_commands(message)
                return

    # ── Anti-Spam ────────────────────────────
    if guild_cfg.get("automod_antispam", False):
        now = datetime.datetime.utcnow()
        uid = member.id
        spam_track.setdefault(uid, [])
        spam_track[uid] = [t for t in spam_track[uid] if (now - t).total_seconds() < 5]
        spam_track[uid].append(now)
        if len(spam_track[uid]) >= 5:
            try:
                await member.timeout(discord.utils.utcnow() + timedelta(minutes=2), reason="Auto-mod: Spamming")
                await message.channel.send(f"🛑 {member.mention} has been muted for spamming!", delete_after=5)
                spam_track[uid] = []
            except:
                pass
            await bot.process_commands(message)
            return

    # ── XP Gain (1 XP per message, random bonus) ──
    data = get_xp(message.guild.id, member.id)
    gained = random.randint(1, 3)
    data["xp"] += gained
    needed = xp_for_level(data["level"])
    if data["xp"] >= needed:
        data["xp"] -= needed
        data["level"] += 1
        lvl_channel_id = guild_cfg.get("level_channel")
        lvl_channel = message.guild.get_channel(int(lvl_channel_id)) if lvl_channel_id else message.channel
        try:
            await lvl_channel.send(f"🎉 {member.mention} leveled up to **Level {data['level']}**! Next level needs **{xp_for_level(data['level'])} XP**.")
        except:
            pass

    await bot.process_commands(message)


@bot.event
async def on_member_join_antiraid(member):
    pass  # handled inside on_member_join already



# ══════════════════════════════════════════════
#  VOICE XP
# ══════════════════════════════════════════════

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    uid = member.id
    now = datetime.datetime.utcnow()

    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        voice_track[uid] = now

    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        if uid in voice_track:
            joined_at = voice_track.pop(uid)
            minutes = (now - joined_at).total_seconds() / 60

            # 1 XP per minute in voice, min 1 minute to count
            if minutes >= 1 and member.guild:
                earned = max(1, int(minutes * 1))
                data = get_xp(member.guild.id, member.id)
                data["xp"] += earned
                needed = xp_for_level(data["level"])

                guild_cfg = config.get(str(member.guild.id), {})

                # Check level up
                if data["xp"] >= needed:
                    data["xp"] -= needed
                    data["level"] += 1
                    lvl_channel_id = guild_cfg.get("level_channel")
                    if lvl_channel_id:
                        lvl_channel = member.guild.get_channel(int(lvl_channel_id))
                        if lvl_channel:
                            try:
                                await lvl_channel.send(f"🎉 {member.mention} leveled up to **Level {data['level']}** (from voice chat)!")
                            except:
                                pass

                # Log to voice XP log channel if set
                log_ch_id = guild_cfg.get("voice_xp_log")
                if log_ch_id:
                    log_ch = member.guild.get_channel(int(log_ch_id))
                    if log_ch:
                        try:
                            await log_ch.send(
                                f"🎙️ **{member.display_name}** spent **{int(minutes)}m** in voice and earned **+{earned} XP** (Level {data['level']})"
                            )
                        except:
                            pass

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


# ── AUTO-MOD SETUP ────────────────────────────
@bot.tree.command(name="automod", description="Toggle auto-mod features on or off")
@app_commands.describe(
    feature="Which feature to toggle",
    enabled="Turn on or off"
)
@app_commands.choices(feature=[
    app_commands.Choice(name="Bad Word Filter", value="automod_badwords"),
    app_commands.Choice(name="Anti-Link",       value="automod_antilink"),
    app_commands.Choice(name="Anti-Spam",       value="automod_antispam"),
    app_commands.Choice(name="Anti-Raid",       value="automod_antiraid"),
])
@app_commands.checks.has_permissions(administrator=True)
async def slash_automod(interaction: discord.Interaction, feature: str, enabled: bool):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg[feature] = enabled
    save_config(config)
    status = "✅ Enabled" if enabled else "❌ Disabled"
    names = {"automod_badwords": "Bad Word Filter", "automod_antilink": "Anti-Link", "automod_antispam": "Anti-Spam", "automod_antiraid": "Anti-Raid"}
    await interaction.response.send_message(f"{status} **{names[feature]}**!")

@bot.tree.command(name="addbadword", description="Add a word to the bad word filter")
@app_commands.describe(word="Word to block")
@app_commands.checks.has_permissions(administrator=True)
async def slash_addbadword(interaction: discord.Interaction, word: str):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    bad = guild_cfg.setdefault("bad_words", [])
    if word.lower() not in bad:
        bad.append(word.lower())
        save_config(config)
    await interaction.response.send_message(f"✅ Added **{word}** to the bad word filter.", ephemeral=True)

@bot.tree.command(name="removebadword", description="Remove a word from the bad word filter")
@app_commands.describe(word="Word to remove")
@app_commands.checks.has_permissions(administrator=True)
async def slash_removebadword(interaction: discord.Interaction, word: str):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    bad = guild_cfg.get("bad_words", [])
    if word.lower() in bad:
        bad.remove(word.lower())
        save_config(config)
        await interaction.response.send_message(f"✅ Removed **{word}** from the filter.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ **{word}** is not in the filter.", ephemeral=True)

@bot.tree.command(name="setminaccountage", description="Set minimum account age in days to join (anti-raid)")
@app_commands.describe(days="Minimum age in days")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setminaccountage(interaction: discord.Interaction, days: int):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["min_account_age"] = days
    save_config(config)
    await interaction.response.send_message(f"✅ Minimum account age set to **{days} days**.")

@bot.tree.command(name="automodstatus", description="Show current auto-mod settings")
@app_commands.checks.has_permissions(administrator=True)
async def slash_automodstatus(interaction: discord.Interaction):
    guild_cfg = config.get(str(interaction.guild.id), {})
    def status(key): return "✅ On" if guild_cfg.get(key, False) else "❌ Off"
    embed = discord.Embed(title="🛡️ Auto-Mod Status", color=discord.Color.blue())
    embed.add_field(name="Bad Word Filter", value=status("automod_badwords"), inline=True)
    embed.add_field(name="Anti-Link",       value=status("automod_antilink"), inline=True)
    embed.add_field(name="Anti-Spam",       value=status("automod_antispam"), inline=True)
    embed.add_field(name="Anti-Raid",       value=status("automod_antiraid"), inline=True)
    embed.add_field(name="Min Account Age", value=f"{guild_cfg.get('min_account_age', 7)} days", inline=True)
    custom_words = guild_cfg.get("bad_words", [])
    embed.add_field(name="Custom Bad Words", value=", ".join(custom_words) if custom_words else "None", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setlogchannel", description="Set a channel for auto-mod logs")
@app_commands.describe(channel="Log channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setlogchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["log_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}.")

# ── XP / LEVELING ─────────────────────────────
@bot.tree.command(name="level", description="Check your or someone's level and XP")
@app_commands.describe(member="Member to check")
async def slash_level(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    data = get_xp(interaction.guild.id, member.id)
    needed = xp_for_level(data["level"])
    bar_filled = int((data["xp"] / needed) * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    embed = discord.Embed(title=f"📈 Level — {member.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level",    value=f"**{data['level']}**",          inline=True)
    embed.add_field(name="XP",       value=f"{data['xp']} / {needed}",      inline=True)
    embed.add_field(name="Progress", value=f"`{bar}`",                      inline=False)
    embed.add_field(name="Next level needs", value=f"{needed - data['xp']} more XP", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the XP leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    guild_xp = xp_data.get(str(interaction.guild.id), {})
    if not guild_xp:
        return await interaction.response.send_message("❌ No XP data yet!")
    sorted_users = sorted(guild_xp.items(), key=lambda x: (x[1]["level"], x[1]["xp"]), reverse=True)[:10]
    embed = discord.Embed(title=f"🏆 XP Leaderboard — {interaction.guild.name}", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, d) in enumerate(sorted_users):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        m = interaction.guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        embed.add_field(name=f"{medal} {name}", value=f"Level {d['level']} • {d['xp']} XP", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setlevelchannel", description="Set the channel for level-up messages")
@app_commands.describe(channel="Channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setlevelchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["level_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Level-up messages will appear in {channel.mention}.")

@bot.tree.command(name="resetxp", description="Reset XP for a member")
@app_commands.describe(member="Member to reset")
@app_commands.checks.has_permissions(administrator=True)
async def slash_resetxp(interaction: discord.Interaction, member: discord.Member):
    xp_data.get(str(interaction.guild.id), {}).pop(str(member.id), None)
    await interaction.response.send_message(f"✅ Reset XP for **{member.display_name}**.")


# ── SLOT MACHINE ──────────────────────────────
@bot.tree.command(name="slots", description="Play the slot machine! (costs 10 coins to play)")
@app_commands.describe(bet="How many coins to bet (min 10)")
async def slash_slots(interaction: discord.Interaction, bet: int = 10):
    from discord.ext import commands
    if bet < 10:
        return await interaction.response.send_message("❌ Minimum bet is **10 coins**!", ephemeral=True)

    # Simple coin store per user
    coins_store = xp_data.setdefault("coins", {})
    uid = str(interaction.user.id)
    coins_store.setdefault(uid, 100)  # start with 100 coins

    if coins_store[uid] < bet:
        return await interaction.response.send_message(f"❌ You only have **🪙 {coins_store[uid]} coins**!", ephemeral=True)

    slots = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎", "7️⃣"]
    weights = [30, 25, 20, 15, 5, 3, 2]  # rarer symbols = lower weight

    import random as _r
    reel1 = _r.choices(slots, weights=weights)[0]
    reel2 = _r.choices(slots, weights=weights)[0]
    reel3 = _r.choices(slots, weights=weights)[0]

    display = f"┃ {reel1} ┃ {reel2} ┃ {reel3} ┃"

    multipliers = {
        "💎": 50, "7️⃣": 25, "⭐": 10,
        "🍇": 5,  "🍊": 4,  "🍋": 3, "🍒": 2
    }

    coins_store[uid] -= bet

    if reel1 == reel2 == reel3:
        mult = multipliers.get(reel1, 2)
        winnings = bet * mult
        coins_store[uid] += winnings
        result = f"🎉 **JACKPOT! {reel1}{reel1}{reel1}** — You won **🪙 {winnings} coins** (x{mult})!"
        color = discord.Color.gold()
    elif reel1 == reel2 or reel2 == reel3:
        winnings = bet * 2
        coins_store[uid] += winnings
        result = f"✨ **Two of a kind!** — You won **🪙 {winnings} coins** (x2)!"
        color = discord.Color.green()
    elif reel1 == "🍒" or reel2 == "🍒" or reel3 == "🍒":
        winnings = int(bet * 0.5)
        coins_store[uid] += winnings
        result = f"🍒 **Cherry bonus!** — You got back **🪙 {winnings} coins**."
        color = discord.Color.orange()
    else:
        result = f"😔 **No match!** — You lost **🪙 {bet} coins**."
        color = discord.Color.red()

    embed = discord.Embed(title="🎰 Slot Machine", color=color)
    embed.add_field(name="Reels", value=f"```{display}```", inline=False)
    embed.add_field(name="Result", value=result, inline=False)
    embed.add_field(name="Balance", value=f"🪙 {coins_store[uid]} coins", inline=False)
    embed.set_footer(text="💎=x50 | 7️⃣=x25 | ⭐=x10 | 🍇=x5 | 🍊=x4 | 🍋=x3 | 🍒=x2")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="coinbalance", description="Check your slot machine coin balance")
async def slash_coinbalance(interaction: discord.Interaction):
    coins_store = xp_data.setdefault("coins", {})
    uid = str(interaction.user.id)
    coins_store.setdefault(uid, 100)
    await interaction.response.send_message(f"🪙 You have **{coins_store[uid]} coins**!
Play `/slots` to win more or lose them all 😅")

@bot.tree.command(name="givecoin", description="Give coins to another member")
@app_commands.describe(member="Who to give to", amount="How many coins")
async def slash_givecoin(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
    coins_store = xp_data.setdefault("coins", {})
    giver = str(interaction.user.id)
    receiver = str(member.id)
    coins_store.setdefault(giver, 100)
    coins_store.setdefault(receiver, 100)
    if coins_store[giver] < amount:
        return await interaction.response.send_message(f"❌ You only have **🪙 {coins_store[giver]}**!", ephemeral=True)
    coins_store[giver] -= amount
    coins_store[receiver] += amount
    await interaction.response.send_message(f"✅ Gave **🪙 {amount} coins** to {member.mention}! They now have {coins_store[receiver]} coins.")

@bot.tree.command(name="daily", description="Claim your daily coins (resets every 24h)")
async def slash_daily(interaction: discord.Interaction):
    coins_store = xp_data.setdefault("coins", {})
    daily_store = xp_data.setdefault("daily", {})
    uid = str(interaction.user.id)
    coins_store.setdefault(uid, 100)
    now = datetime.datetime.utcnow()
    last = daily_store.get(uid)
    if last:
        last_dt = datetime.datetime.fromisoformat(last)
        diff = (now - last_dt).total_seconds()
        if diff < 86400:
            h = int((86400 - diff) // 3600)
            m = int(((86400 - diff) % 3600) // 60)
            return await interaction.response.send_message(f"⏰ Come back in **{h}h {m}m** for your daily coins!", ephemeral=True)
    reward = random.randint(50, 200)
    coins_store[uid] += reward
    daily_store[uid] = now.isoformat()
    await interaction.response.send_message(f"✅ You claimed **🪙 {reward} daily coins**! Balance: {coins_store[uid]}")

# ── SERVER STATS ──────────────────────────────
@bot.tree.command(name="setupstats", description="Create auto-updating server stat channels")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setupstats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # Create or find Stats category
    category = discord.utils.get(guild.categories, name="📊 Server Stats")
    if not category:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)
        }
        category = await guild.create_category("📊 Server Stats", overwrites=overwrites)

    # Create stat channels
    members_ch   = await guild.create_voice_channel(f"👥 Members: {guild.member_count}", category=category)
    humans_ch    = await guild.create_voice_channel(f"👤 Humans: {sum(1 for m in guild.members if not m.bot)}", category=category)
    bots_ch      = await guild.create_voice_channel(f"🤖 Bots: {sum(1 for m in guild.members if m.bot)}", category=category)
    online_ch    = await guild.create_voice_channel(f"🟢 Online: {sum(1 for m in guild.members if m.status != discord.Status.offline)}", category=category)

    # Save channel IDs to config
    guild_cfg = config.setdefault(str(guild.id), {})
    guild_cfg["stats_channels"] = {
        "members": str(members_ch.id),
        "humans":  str(humans_ch.id),
        "bots":    str(bots_ch.id),
        "online":  str(online_ch.id),
    }
    save_config(config)

    await interaction.followup.send("✅ Server stats channels created! They update every 10 minutes.", ephemeral=True)

@bot.tree.command(name="removestats", description="Remove the server stats channels")
@app_commands.checks.has_permissions(administrator=True)
async def slash_removestats(interaction: discord.Interaction):
    guild_cfg = config.get(str(interaction.guild.id), {})
    channels = guild_cfg.get("stats_channels", {})
    for ch_id in channels.values():
        ch = interaction.guild.get_channel(int(ch_id))
        if ch:
            await ch.delete()
    guild_cfg.pop("stats_channels", None)
    save_config(config)
    await interaction.response.send_message("✅ Stats channels removed.")


# ── VOICE XP SETTINGS ─────────────────────────
@bot.tree.command(name="setvoicexplog", description="Set a channel to log voice XP gains")
@app_commands.describe(channel="Log channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setvoicexplog(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["voice_xp_log"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"✅ Voice XP logs will appear in {channel.mention}.")

@bot.tree.command(name="voicexp", description="Check how much XP you earned from voice today")
async def slash_voicexp(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid in voice_track:
        now = datetime.datetime.utcnow()
        minutes = (now - voice_track[uid]).total_seconds() / 60
        potential = max(1, int(minutes * 1))
        embed = discord.Embed(title="🎙️ Voice XP", color=discord.Color.purple())
        embed.add_field(name="Currently in voice", value=f"**{int(minutes)} minutes**", inline=True)
        embed.add_field(name="XP on leaving", value=f"**+{potential} XP**", inline=True)
        embed.set_footer(text="XP is awarded when you leave the voice channel")
        await interaction.response.send_message(embed=embed)
    else:
        data = get_xp(interaction.guild.id, uid)
        embed = discord.Embed(title="🎙️ Voice XP", color=discord.Color.purple())
        embed.description = "You're not currently in a voice channel."
        embed.add_field(name="Your Level", value=data["level"], inline=True)
        embed.add_field(name="Your XP",    value=data["xp"],   inline=True)
        embed.set_footer(text="Join a voice channel to earn XP over time!")
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="whoinvoice", description="See who is currently in voice channels")
async def slash_whoinvoice(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"🎙️ Voice Channels — {guild.name}", color=discord.Color.purple())
    any_found = False
    for vc in guild.voice_channels:
        if vc.members:
            any_found = True
            now = datetime.datetime.utcnow()
            member_list = []
            for m in vc.members:
                if m.id in voice_track:
                    mins = int((now - voice_track[m.id]).total_seconds() / 60)
                    member_list.append(f"{m.display_name} ({mins}m)")
                else:
                    member_list.append(m.display_name)
            embed.add_field(
                name=f"🔊 {vc.name} ({len(vc.members)})",
                value="
".join(member_list),
                inline=False
            )
    if not any_found:
        embed.description = "Nobody is in any voice channels right now."
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show all commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 All Commands", color=discord.Color.green(), description="Type `/` to see all commands with descriptions!")
    embed.add_field(name="🎫 Tickets",    value="`/ticketsetup`", inline=False)
    embed.add_field(name="🔨 Moderation", value="`/kick` `/ban` `/unban` `/mute` `/unmute` `/warn` `/warnings` `/clearwarnings` `/purge` `/slowmode` `/lock` `/unlock` `/nick`", inline=False)
    embed.add_field(name="🏷️ Roles",      value="`/addrole` `/removerole`", inline=False)
    embed.add_field(name="⚙️ Setup",      value="`/setautorole` `/setwelcome` `/setleave`", inline=False)
    embed.add_field(name="🛡️ Auto-Mod",   value="`/automod` `/automodstatus` `/addbadword` `/removebadword` `/setminaccountage` `/setlogchannel`", inline=False)
    embed.add_field(name="📈 Leveling",   value="`/level` `/leaderboard` `/setlevelchannel` `/resetxp`", inline=False)
    embed.add_field(name="🎙️ Voice XP",   value="`/voicexp` `/whoinvoice` `/setvoicexplog`", inline=False)
    embed.add_field(name="🎰 Slots",       value="`/slots` `/coinbalance` `/daily` `/givecoin`", inline=False)
    embed.add_field(name="📊 Stats",       value="`/setupstats` `/removestats`", inline=False)
    embed.add_field(name="😂 Fun",        value="`/8ball`", inline=False)
    embed.add_field(name="ℹ️ Info",       value="`/userinfo` `/serverinfo` `/avatar` `/ping`", inline=False)
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