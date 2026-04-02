import discord
from discord.ext import commands, tasks
from discord import app_commands
import json, os, random, re, datetime, asyncio
from datetime import timedelta
from aiohttp import web

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TOKEN         = os.getenv("TOKEN")
NATIVE_SECRET = 0xA3F7_C291_5E6B_D840
OWNER_ID      = 1096099089076203530
CONFIG_FILE   = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f: return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f: json.dump(data, f, indent=2)

config = load_config()

def get_setting(guild_id, key):
    return config.get(str(guild_id), {}).get(key)

def set_setting(guild_id, key, value):
    config.setdefault(str(guild_id), {})[key] = value
    save_config(config)

# ══════════════════════════════════════════════════════════════════════════════
#  KEY SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
def fnv64(data: bytes) -> int:
    h, prime, mask = 0xcbf29ce484222325, 0x00000001000000b3, 0xFFFFFFFFFFFFFFFF
    for b in data: h = ((h ^ b) * prime) & mask
    return h

def derive_key(hw_id: int) -> str:
    mask = 0xFFFFFFFFFFFFFFFF
    def xb(n): return (n & mask).to_bytes(8, "little")
    a = fnv64(xb(hw_id ^ NATIVE_SECRET))
    b = fnv64(xb(hw_id ^ NATIVE_SECRET ^ 0x1234567890abcdef))
    c = fnv64(xb(hw_id ^ NATIVE_SECRET ^ 0xfedcba9876543210))
    return (f"NTVE-{(a>>48)&0xFFFF:04X}-{(a>>32)&0xFFFF:04X}"
            f"-{(b>>48)&0xFFFF:04X}-{(b>>32)&0xFFFF:04X}"
            f"-{(c>>48)&0xFFFF:04X}")

def parse_duration(s: str):
    if not s or s.lower() in ("permanent","perm","forever","0"): return None
    m = re.fullmatch(r"(\d+)(m|h|d)", s.lower().strip())
    if not m: return None
    return int(m.group(1)) * {"m":60,"h":3600,"d":86400}[m.group(2)]

def fmt_duration(secs: int) -> str:
    if secs < 3600: return f"{secs//60} minutes"
    if secs < 86400:
        h = secs//3600; m = (secs%3600)//60
        return f"{h}h {m}m" if m else f"{h} hours"
    d = secs//86400; h = (secs%86400)//3600
    return f"{d}d {h}h" if h else f"{d} days"

# ══════════════════════════════════════════════════════════════════════════════
#  BAN SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
banned_hwids: set = set()

def load_bans():
    try:
        with open("banned_hwids.txt") as f:
            return set(l.strip().upper() for l in f if l.strip())
    except: return set()

def save_bans():
    with open("banned_hwids.txt", "w") as f:
        f.write("\n".join(sorted(banned_hwids)))

banned_hwids = load_bans()

# ══════════════════════════════════════════════════════════════════════════════
#  BOT SETUP
# ══════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.voice_states    = True

bot          = commands.Bot(command_prefix="!", intents=intents)
warnings     = {}
spam_track   = {}
recent_join  = []
snipe_data   = {}
perm_counter = {}

# ══════════════════════════════════════════════════════════════════════════════
#  STAFF CHECK
# ══════════════════════════════════════════════════════════════════════════════
def is_staff():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator: return True
        role = discord.utils.get(interaction.guild.roles, name="Staff")
        if role and role in interaction.user.roles: return True
        await interaction.response.send_message("❌ You need the **Staff** role.", ephemeral=True)
        return False
    return app_commands.check(predicate)

# ══════════════════════════════════════════════════════════════════════════════
#  VIEWS
# ══════════════════════════════════════════════════════════════════════════════
class VerifyButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="✅  Verify", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_id = get_setting(interaction.guild_id, "verify_role")
        if not role_id:
            return await interaction.response.send_message("❌ Verify role not set. Run /setverifyrole.", ephemeral=True)
        role = interaction.guild.get_role(int(role_id))
        if not role:
            return await interaction.response.send_message("❌ Role not found.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("✅ Already verified!", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="Verified via button")
            await interaction.response.send_message(f"✅ Verified! You now have the **{role.name}** role.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to give that role.", ephemeral=True)

class CloseTicketButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel    = interaction.channel
        is_admin   = interaction.user.guild_permissions.administrator
        is_creator = str(interaction.user.id) in (channel.topic or "")
        staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
        is_staff_r = staff_role and staff_role in interaction.user.roles
        if not (is_admin or is_creator or is_staff_r):
            return await interaction.response.send_message("❌ Only the ticket owner or Staff can close this.", ephemeral=True)
        await interaction.response.send_message("🔒 Closing ticket...")
        await channel.delete(reason=f"Closed by {interaction.user}")

class TicketButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild     = interaction.guild
        guild_cfg = config.setdefault(str(guild.id), {})
        count     = guild_cfg.get("ticket_count", 0) + 1
        guild_cfg["ticket_count"] = count
        save_config(config)
        existing = discord.utils.get(guild.text_channels, topic=f"ticket-owner-{interaction.user.id}")
        if existing:
            return await interaction.response.send_message(f"You already have a ticket: {existing.mention}", ephemeral=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        staff_role = discord.utils.get(guild.roles, name="Staff")
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        category = discord.utils.get(guild.categories, name="Tickets") or \
                   await guild.create_category("Tickets", overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)})
        channel = await guild.create_text_channel(
            name=f"ticket-{count:04d}", category=category,
            overwrites=overwrites, topic=f"ticket-owner-{interaction.user.id}"
        )
        embed = discord.Embed(
            title=f"🎫 Ticket #{count:04d}",
            description=f"Welcome {interaction.user.mention}!\n\nDescribe your issue and staff will assist shortly.\n\nClick below to close.",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=CloseTicketButton())
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    bot.add_view(VerifyButton())
    bot.add_view(TicketButton())
    bot.add_view(CloseTicketButton())
    update_stats.start()
    await bot.tree.sync()
    print(f"✅ {bot.user} online!")

@tasks.loop(minutes=10)
async def update_stats():
    for guild in bot.guilds:
        channels = config.get(str(guild.id), {}).get("stats_channels", {})
        if not channels: continue
        try:
            if channels.get("members"):
                ch = guild.get_channel(int(channels["members"]))
                if ch: await ch.edit(name=f"👥 Members: {guild.member_count}")
            if channels.get("humans"):
                ch = guild.get_channel(int(channels["humans"]))
                if ch: await ch.edit(name=f"👤 Humans: {sum(1 for m in guild.members if not m.bot)}")
            if channels.get("bots"):
                ch = guild.get_channel(int(channels["bots"]))
                if ch: await ch.edit(name=f"🤖 Bots: {sum(1 for m in guild.members if m.bot)}")
        except: pass

@bot.event
async def on_member_join(member):
    guild_cfg = config.get(str(member.guild.id), {})
    if guild_cfg.get("automod_antiraid"):
        now = datetime.datetime.utcnow()
        recent_join.append(now)
        recent_join[:] = [t for t in recent_join if (now-t).total_seconds() < 10]
        if len(recent_join) >= 5:
            try: await member.kick(reason="Anti-raid"); return
            except: pass
    wc = guild_cfg.get("welcome_channel")
    wm = guild_cfg.get("welcome_message", "Welcome {mention}!")
    if wc:
        ch = member.guild.get_channel(int(wc))
        if ch:
            await ch.send(wm.replace("{mention}", member.mention)
                           .replace("{name}", member.display_name)
                           .replace("{server}", member.guild.name))

@bot.event
async def on_member_remove(member):
    lc = get_setting(member.guild.id, "leave_channel")
    if lc:
        ch = member.guild.get_channel(int(lc))
        if ch: await ch.send(f"👋 **{member.display_name}** left the server.")

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    snipe_data[message.channel.id] = {
        "content": message.content or "[no text]",
        "author":  str(message.author),
        "avatar":  str(message.author.display_avatar.url),
        "time":    datetime.datetime.utcnow(),
    }

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message); return
    guild_cfg = config.get(str(message.guild.id), {})
    member    = message.author
    if guild_cfg.get("automod_badwords"):
        if any(w in message.content.lower() for w in guild_cfg.get("bad_words", [])):
            try:
                await message.delete()
                await message.channel.send(f"🚫 {member.mention} Watch your language!", delete_after=5)
            except: pass
    if guild_cfg.get("automod_antilink"):
        if re.search(r"(https?://|discord\.gg/|www\.)\S+", message.content):
            if not member.guild_permissions.administrator:
                try:
                    await message.delete()
                    await message.channel.send(f"🔗 {member.mention} Links not allowed!", delete_after=5)
                except: pass
    if guild_cfg.get("automod_antispam"):
        now = datetime.datetime.utcnow()
        uid = member.id
        spam_track.setdefault(uid, [])
        spam_track[uid] = [t for t in spam_track[uid] if (now-t).total_seconds() < 5]
        spam_track[uid].append(now)
        if len(spam_track[uid]) >= 5:
            try:
                await member.timeout(discord.utils.utcnow() + timedelta(minutes=2), reason="Anti-spam")
                await message.channel.send(f"🛑 {member.mention} muted for spamming!", delete_after=5)
                spam_track[uid] = []
            except: pass
    await bot.process_commands(message)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — OWNER
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="sync", description="Sync slash commands")
async def slash_sync(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await bot.tree.sync()
    await interaction.response.send_message("✅ Synced!", ephemeral=True)

@bot.tree.command(name="key", description="Generate a NATIVE license key")
@app_commands.describe(machine_id="16-char Machine ID", duration="10m / 2h / 7d / permanent", user="Send key to user")
async def slash_key(interaction: discord.Interaction, machine_id: str, duration: str = "permanent", user: discord.Member = None):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    cleaned = machine_id.strip().upper().replace("-","").replace(" ","")
    if len(cleaned) != 16 or not all(c in "0123456789ABCDEF" for c in cleaned):
        return await interaction.response.send_message(f"❌ Invalid Machine ID: `{machine_id}`", ephemeral=True)
    hw_id   = int(cleaned, 16)
    key     = derive_key(hw_id)
    secs    = parse_duration(duration)
    is_perm = secs is None
    if is_perm:
        expiry_label = "Never (Permanent)"
        paste_key    = key
    else:
        expiry_ts    = int(datetime.datetime.utcnow().timestamp()) + secs
        expiry_label = f"<t:{expiry_ts}:F> (<t:{expiry_ts}:R>)"
        paste_key    = f"{key}:{expiry_ts}"
    embed = discord.Embed(
        title="🔑 NATIVE License Key",
        color=discord.Color.from_rgb(0,185,255) if is_perm else discord.Color.orange()
    )
    embed.add_field(name="Machine ID",  value=f"`{cleaned}`",       inline=False)
    embed.add_field(name="License Key", value=f"```{paste_key}```", inline=False)
    embed.add_field(name="Expires",     value=expiry_label,         inline=False)
    embed.set_footer(text="Key only works on that machine.")
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=embed, ephemeral=True)
    if user:
        try:
            await user.send(f"🔑 Your NATIVE key from {interaction.user.display_name}:", embed=embed)
            await interaction.followup.send(f"✅ Sent to {user.mention} via DM!", ephemeral=True)
        except discord.Forbidden:
            msg = await interaction.channel.send(f"{user.mention} — your NATIVE key:", embed=embed)
            await interaction.followup.send("⚠️ DMs disabled — posted in channel, deletes in 30s.", ephemeral=True)
            await asyncio.sleep(30)
            try: await msg.delete()
            except: pass

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — BAN SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="banmachineid", description="Ban a machine ID — kicks them out within 10 seconds")
@app_commands.describe(machine_id="16-character machine ID")
@is_staff()
async def slash_banmachineid(interaction: discord.Interaction, machine_id: str):
    hwid = machine_id.strip().upper().replace("-","").replace(" ","")
    if len(hwid) != 16:
        return await interaction.response.send_message("❌ Must be 16 hex characters.", ephemeral=True)
    banned_hwids.add(hwid)
    save_bans()
    await interaction.response.send_message(f"✅ `{hwid}` banned — they will be kicked within 10 seconds.", ephemeral=True)

@bot.tree.command(name="unbanmachineid", description="Unban a machine ID")
@app_commands.describe(machine_id="Machine ID to unban")
@is_staff()
async def slash_unbanmachineid(interaction: discord.Interaction, machine_id: str):
    hwid = machine_id.strip().upper().replace("-","").replace(" ","")
    if hwid in banned_hwids:
        banned_hwids.discard(hwid); save_bans()
        await interaction.response.send_message(f"✅ `{hwid}` unbanned.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ `{hwid}` is not banned.", ephemeral=True)

@bot.tree.command(name="listbans", description="List all banned machine IDs")
@is_staff()
async def slash_listbans(interaction: discord.Interaction):
    if not banned_hwids:
        return await interaction.response.send_message("No banned machine IDs.", ephemeral=True)
    embed = discord.Embed(title="🔨 Banned Machine IDs", color=discord.Color.red())
    embed.description = "\n".join(f"`{h}`" for h in sorted(banned_hwids))
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — PERM COUNTER
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="add", description="Add to the perm counter")
@app_commands.describe(amount="Number to add")
@is_staff()
async def slash_add(interaction: discord.Interaction, amount: int):
    gid = str(interaction.guild_id)
    perm_counter[gid] = perm_counter.get(gid, 0) + amount
    await interaction.response.send_message(f"✅ Added **{amount}** — total: **{perm_counter[gid]}**", ephemeral=True)

@bot.tree.command(name="remove", description="Remove from the perm counter")
@app_commands.describe(amount="Number to remove")
@is_staff()
async def slash_remove(interaction: discord.Interaction, amount: int):
    gid = str(interaction.guild_id)
    perm_counter[gid] = perm_counter.get(gid, 0) - amount
    await interaction.response.send_message(f"✅ Removed **{amount}** — total: **{perm_counter[gid]}**", ephemeral=True)

@bot.tree.command(name="show", description="Show the perm counter")
@is_staff()
async def slash_show(interaction: discord.Interaction):
    total = perm_counter.get(str(interaction.guild_id), 0)
    embed = discord.Embed(title="🔢 Perm Counter", description=f"**{total}**", color=discord.Color.from_rgb(0,185,255))
    embed.set_footer(text="NATIVE • Staff only")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="resetcounter", description="Reset the perm counter to 0")
@is_staff()
async def slash_resetcounter(interaction: discord.Interaction):
    perm_counter[str(interaction.guild_id)] = 0
    await interaction.response.send_message("✅ Counter reset to **0**.", ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="setverifyrole", description="Set the role given on verify")
@app_commands.describe(role="Role to give")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_setverifyrole(interaction: discord.Interaction, role: discord.Role):
    set_setting(interaction.guild_id, "verify_role", str(role.id))
    await interaction.response.send_message(f"✅ Verify role set to **{role.name}**.", ephemeral=True)

@bot.tree.command(name="sendverify", description="Send the verify embed in this channel")
@app_commands.checks.has_permissions(manage_guild=True)
async def slash_sendverify(interaction: discord.Interaction):
    role_id = get_setting(interaction.guild_id, "verify_role")
    if not role_id:
        return await interaction.response.send_message("❌ Set a verify role first with /setverifyrole.", ephemeral=True)
    embed = discord.Embed(
        title="🔒  Welcome to NATIVE",
        description="Click the button below to verify yourself and get access to the server.",
        color=discord.Color.from_rgb(0,185,255)
    )
    embed.set_footer(text="NATIVE • Click once to verify")
    await interaction.channel.send(embed=embed, view=VerifyButton())
    await interaction.response.send_message("✅ Verify message sent!", ephemeral=True)

@bot.tree.command(name="setupverify", description="Create Member role and send verify message")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setupverify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    role  = discord.utils.get(guild.roles, name="Member")
    if not role:
        role = await guild.create_role(name="Member", color=discord.Color.from_rgb(0,185,255))
        msg  = "✅ Created **Member** role"
    else:
        msg = "✅ Found existing **Member** role"
    set_setting(guild.id, "verify_role", str(role.id))
    embed = discord.Embed(
        title="🔒  Welcome to NATIVE",
        description="Click the button below to get access to the server.",
        color=discord.Color.from_rgb(0,185,255)
    )
    embed.set_footer(text="NATIVE • Click once to verify")
    await interaction.channel.send(embed=embed, view=VerifyButton())
    await interaction.followup.send(
        f"{msg}\n✅ Verify role saved\n✅ Verify message sent\n\n"
        "⚠️ Make sure the bot role is **above** Member in Server Settings → Roles.",
        ephemeral=True
    )

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — TICKETS
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="ticketsetup", description="Send the ticket panel")
@app_commands.describe(channel="Channel for the panel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_ticketsetup(interaction: discord.Interaction, channel: discord.TextChannel):
    embed = discord.Embed(title="🎫 Support Tickets", description="Click below to open a ticket!", color=discord.Color.blurple())
    await channel.send(embed=embed, view=TicketButton())
    await interaction.response.send_message(f"✅ Ticket panel sent to {channel.mention}!", ephemeral=True)

@bot.tree.command(name="close", description="Close the current ticket")
async def slash_close(interaction: discord.Interaction):
    channel = interaction.channel
    if not (channel.topic and channel.topic.startswith("ticket-owner-")):
        return await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
    owner_id   = int(channel.topic.replace("ticket-owner-",""))
    is_owner   = interaction.user.id == owner_id
    is_admin   = interaction.user.guild_permissions.administrator
    staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
    is_staff_r = staff_role and staff_role in interaction.user.roles
    if not (is_owner or is_admin or is_staff_r):
        return await interaction.response.send_message("❌ Only the ticket owner or Staff can close this.", ephemeral=True)
    await interaction.response.send_message("🔒 Closing ticket...")
    await channel.delete(reason=f"Closed by {interaction.user}")

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — MODERATION
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Kicked **{member.display_name}**. Reason: {reason}")

@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member", reason="Reason")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason, delete_message_days=0)
    await interaction.response.send_message(f"🔨 Banned **{member.display_name}**. Reason: {reason}")

@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str):
    async for entry in interaction.guild.bans():
        if str(entry.user.id) == user_id:
            await interaction.guild.unban(entry.user)
            return await interaction.response.send_message(f"✅ Unbanned **{entry.user}**.")
    await interaction.response.send_message("❌ Not found.")

@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(member="Member", minutes="Minutes", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason"):
    await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"🔇 Muted **{member.display_name}** for {minutes}min.")

@bot.tree.command(name="unmute", description="Remove timeout")
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
    await interaction.response.send_message(f"⚠️ Warned **{member.display_name}** ({len(warnings[member.id])} total). Reason: {reason}")
    try: await member.send(f"⚠️ Warned in **{interaction.guild.name}**: {reason}")
    except: pass

@bot.tree.command(name="warnings", description="View warnings")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    w = warnings.get(member.id, [])
    if not w: return await interaction.response.send_message(f"✅ {member.display_name} has no warnings.")
    embed = discord.Embed(title=f"Warnings — {member.display_name}", color=discord.Color.orange())
    for i, r in enumerate(w, 1): embed.add_field(name=f"Warning {i}", value=r, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarnings", description="Clear warnings")
@app_commands.describe(member="Member")
@app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    warnings.pop(member.id, None)
    await interaction.response.send_message(f"✅ Cleared warnings for **{member.display_name}**.")

@bot.tree.command(name="purge", description="Delete messages")
@app_commands.describe(amount="Amount (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=min(amount, 100))
    await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set slowmode")
@app_commands.describe(seconds="Seconds (0=off)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"✅ Slowmode {'disabled' if seconds==0 else f'set to {seconds}s'}.")

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

@bot.tree.command(name="nick", description="Change nickname")
@app_commands.describe(member="Member", nickname="New nickname (blank to reset)")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def slash_nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    await member.edit(nick=nickname)
    await interaction.response.send_message(f"✅ Nickname {'reset' if not nickname else f'set to **{nickname}**'}.")

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

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — SETUP
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="setwelcome", description="Set welcome channel and message")
@app_commands.describe(channel="Channel", message="Use {mention} {name} {server}")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = "Welcome {mention}! 🎉"):
    set_setting(interaction.guild.id, "welcome_channel", str(channel.id))
    set_setting(interaction.guild.id, "welcome_message", message)
    await interaction.response.send_message(f"✅ Welcome channel set to {channel.mention}.")

@bot.tree.command(name="setleave", description="Set leave channel")
@app_commands.describe(channel="Channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setleave(interaction: discord.Interaction, channel: discord.TextChannel):
    set_setting(interaction.guild.id, "leave_channel", str(channel.id))
    await interaction.response.send_message(f"✅ Leave channel set to {channel.mention}.")

@bot.tree.command(name="setupstats", description="Create stat channels")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setupstats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    cat   = discord.utils.get(guild.categories, name="📊 Server Stats") or \
            await guild.create_category("📊 Server Stats", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
                guild.me:           discord.PermissionOverwrite(view_channel=True, manage_channels=True)
            })
    mc = await guild.create_voice_channel(f"👥 Members: {guild.member_count}", category=cat)
    hc = await guild.create_voice_channel(f"👤 Humans: {sum(1 for m in guild.members if not m.bot)}", category=cat)
    bc = await guild.create_voice_channel(f"🤖 Bots: {sum(1 for m in guild.members if m.bot)}", category=cat)
    config.setdefault(str(guild.id), {})["stats_channels"] = {"members":str(mc.id),"humans":str(hc.id),"bots":str(bc.id)}
    save_config(config)
    await interaction.followup.send("✅ Stats channels created — updates every 10 min.", ephemeral=True)

@bot.tree.command(name="removestats", description="Remove stat channels")
@app_commands.checks.has_permissions(administrator=True)
async def slash_removestats(interaction: discord.Interaction):
    channels = config.get(str(interaction.guild.id), {}).pop("stats_channels", {})
    save_config(config)
    for cid in channels.values():
        ch = interaction.guild.get_channel(int(cid))
        if ch: await ch.delete()
    await interaction.response.send_message("✅ Stats channels removed.")

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — AUTO-MOD
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="automod", description="Toggle auto-mod features")
@app_commands.describe(feature="Feature", enabled="On/off")
@app_commands.choices(feature=[
    app_commands.Choice(name="Bad Word Filter", value="automod_badwords"),
    app_commands.Choice(name="Anti-Link",       value="automod_antilink"),
    app_commands.Choice(name="Anti-Spam",       value="automod_antispam"),
    app_commands.Choice(name="Anti-Raid",       value="automod_antiraid"),
])
@app_commands.checks.has_permissions(administrator=True)
async def slash_automod(interaction: discord.Interaction, feature: str, enabled: bool):
    config.setdefault(str(interaction.guild.id), {})[feature] = enabled
    save_config(config)
    names = {"automod_badwords":"Bad Word Filter","automod_antilink":"Anti-Link","automod_antispam":"Anti-Spam","automod_antiraid":"Anti-Raid"}
    await interaction.response.send_message(f"{'✅ Enabled' if enabled else '❌ Disabled'} **{names[feature]}**.")

@bot.tree.command(name="addbadword", description="Add a bad word")
@app_commands.describe(word="Word to block")
@app_commands.checks.has_permissions(administrator=True)
async def slash_addbadword(interaction: discord.Interaction, word: str):
    words = config.setdefault(str(interaction.guild.id), {}).setdefault("bad_words", [])
    if word.lower() not in words: words.append(word.lower()); save_config(config)
    await interaction.response.send_message(f"✅ Added **{word}**.", ephemeral=True)

@bot.tree.command(name="removebadword", description="Remove a bad word")
@app_commands.describe(word="Word to remove")
@app_commands.checks.has_permissions(administrator=True)
async def slash_removebadword(interaction: discord.Interaction, word: str):
    words = config.get(str(interaction.guild.id), {}).get("bad_words", [])
    if word.lower() in words:
        words.remove(word.lower()); save_config(config)
        await interaction.response.send_message(f"✅ Removed **{word}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Not in filter.", ephemeral=True)

@bot.tree.command(name="automodstatus", description="Show auto-mod status")
@app_commands.checks.has_permissions(administrator=True)
async def slash_automodstatus(interaction: discord.Interaction):
    gc  = config.get(str(interaction.guild.id), {})
    def s(k): return "✅ On" if gc.get(k) else "❌ Off"
    embed = discord.Embed(title="🛡️ Auto-Mod Status", color=discord.Color.blue())
    embed.add_field(name="Bad Words", value=s("automod_badwords"), inline=True)
    embed.add_field(name="Anti-Link", value=s("automod_antilink"), inline=True)
    embed.add_field(name="Anti-Spam", value=s("automod_antispam"), inline=True)
    embed.add_field(name="Anti-Raid", value=s("automod_antiraid"), inline=True)
    cw = gc.get("bad_words", [])
    embed.add_field(name="Custom Words", value=", ".join(cw) if cw else "None", inline=False)
    await interaction.response.send_message(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS — INFO / FUN
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! **{round(bot.latency*1000)}ms**")

@bot.tree.command(name="userinfo", description="Get info about a member")
@app_commands.describe(member="Member")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    roles = [r.mention for r in m.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"User Info — {m}", color=m.color)
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="ID",      value=m.id,                              inline=True)
    embed.add_field(name="Joined",  value=m.joined_at.strftime("%Y-%m-%d"),  inline=True)
    embed.add_field(name="Created", value=m.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) or "None", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Get server info")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",    value=g.owner.mention,                  inline=True)
    embed.add_field(name="Members",  value=g.member_count,                   inline=True)
    embed.add_field(name="Channels", value=len(g.channels),                  inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),                     inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"),inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.describe(member="Member")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    embed = discord.Embed(title=f"{m.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=m.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="snipe", description="Show last deleted message")
async def slash_snipe(interaction: discord.Interaction):
    data = snipe_data.get(interaction.channel.id)
    if not data: return await interaction.response.send_message("❌ Nothing to snipe!")
    embed = discord.Embed(description=data["content"], color=discord.Color.red(), timestamp=data["time"])
    embed.set_author(name=data["author"], icon_url=data["avatar"])
    embed.set_footer(text="Deleted message")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="8ball", description="Ask the magic 8ball")
@app_commands.describe(question="Your question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question,                       inline=False)
    embed.add_field(name="Answer",   value=random.choice(["Yes.", "No."]), inline=False)
    await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
#  POLL SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
active_polls: dict = {}  # message_id -> poll data

@bot.tree.command(name="poll", description="Create a poll with up to 4 options")
@app_commands.describe(
    question="The poll question",
    option1="First option",
    option2="Second option",
    option3="Third option (optional)",
    option4="Fourth option (optional)",
    duration="Duration in minutes (0 = no end, default 0)"
)
async def slash_poll(interaction: discord.Interaction,
                     question: str,
                     option1: str,
                     option2: str,
                     option3: str = None,
                     option4: str = None,
                     duration: int = 0):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis  = ["🔵", "🟢", "🟡", "🔴"]
    
    embed = discord.Embed(
        title=f"📊  {question}",
        color=discord.Color.from_rgb(0, 185, 255)
    )
    embed.set_footer(text=f"NATIVE Poll • React to vote{f' • Ends in {duration}m' if duration else ''}")
    
    desc = ""
    for i, opt in enumerate(options):
        desc += f"{emojis[i]}  **{opt}**\n\n"
    embed.description = desc.strip()
    
    if duration:
        ends = discord.utils.utcnow() + datetime.timedelta(minutes=duration)
        embed.add_field(name="⏱️ Ends", value=f"<t:{int(ends.timestamp())}:R>", inline=False)
    
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])
    
    poll_data = {
        "question": question,
        "options": options,
        "emojis": emojis[:len(options)],
        "channel_id": interaction.channel_id,
        "guild_id": interaction.guild_id,
        "ends": (discord.utils.utcnow() + datetime.timedelta(minutes=duration)).isoformat() if duration else None
    }
    active_polls[msg.id] = poll_data
    
    if duration:
        await asyncio.sleep(duration * 60)
        if msg.id in active_polls:
            await end_poll(msg.id, interaction.channel)

async def end_poll(msg_id: int, channel):
    try:
        data = active_polls.pop(msg_id, None)
        if not data: return
        msg = await channel.fetch_message(msg_id)
        
        # Count votes
        results = []
        total = 0
        for i, emoji in enumerate(data["emojis"]):
            for r in msg.reactions:
                if str(r.emoji) == emoji:
                    count = r.count - 1  # subtract bot reaction
                    results.append((data["options"][i], count, emoji))
                    total += count
                    break
            else:
                results.append((data["options"][i], 0, emoji))
        
        results.sort(key=lambda x: x[1], reverse=True)
        winner = results[0]
        
        embed = discord.Embed(
            title=f"📊  Poll Ended — {data['question']}",
            color=discord.Color.from_rgb(0, 220, 100)
        )
        desc = ""
        for opt, count, emoji in results:
            pct = round((count / total * 100) if total else 0)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            bold = "**" if opt == winner[0] else ""
            desc += f"{emoji}  {bold}{opt}{bold}\n`{bar}` {pct}% ({count} votes)\n\n"
        embed.description = desc.strip()
        embed.set_footer(text=f"NATIVE Poll • {total} total votes • Winner: {winner[0]}")
        
        await msg.edit(embed=embed)
        await channel.send(f"📊 Poll ended! **{winner[0]}** wins with **{winner[1]} votes**!")
    except Exception as e:
        print(f"Poll end error: {e}")

@bot.tree.command(name="endpoll", description="Manually end a poll by message ID")
@is_staff()
async def slash_endpoll(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
        if mid not in active_polls:
            return await interaction.response.send_message("❌ No active poll with that ID.", ephemeral=True)
        await interaction.response.send_message("✅ Ending poll...", ephemeral=True)
        await end_poll(mid, interaction.channel)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  GIVEAWAY SYSTEM
# ══════════════════════════════════════════════════════════════════════════════
active_giveaways: dict = {}  # message_id -> giveaway data

@bot.tree.command(name="giveaway", description="Start a giveaway")
@app_commands.describe(
    prize="What are you giving away?",
    winners="Number of winners",
    duration="Duration: 10m, 2h, 1d",
    channel="Channel to host it in (default: current)",
    description="Extra description or requirements (optional)"
)
@is_staff()
async def slash_giveaway(interaction: discord.Interaction,
                          prize: str,
                          duration: str,
                          winners: int = 1,
                          channel: discord.TextChannel = None,
                          description: str = None):
    secs = parse_duration(duration)
    if not secs:
        return await interaction.response.send_message(
            "❌ Invalid duration. Use: `10m`, `2h`, `1d`", ephemeral=True)
    
    ch = channel or interaction.channel
    ends_ts = int(discord.utils.utcnow().timestamp()) + secs
    
    embed = discord.Embed(
        title=f"🎉  GIVEAWAY  🎉",
        description=(
            f"**{prize}**\n\n"
            f"{f'> {description}\n\n' if description else ''}"
            f"React with 🎉 to enter!\n\n"
            f"**Winners:** {winners}\n"
            f"**Ends:** <t:{ends_ts}:R> (<t:{ends_ts}:F>)"
        ),
        color=discord.Color.from_rgb(255, 185, 0)
    )
    embed.set_footer(text=f"NATIVE Giveaway • Hosted by {interaction.user.display_name}")
    embed.set_thumbnail(url="https://twemoji.maxcdn.com/v/latest/72x72/1f389.png")
    
    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)
    msg = await ch.send(embed=embed)
    await msg.add_reaction("🎉")
    
    active_giveaways[msg.id] = {
        "prize": prize,
        "winners": winners,
        "ends_ts": ends_ts,
        "channel_id": ch.id,
        "guild_id": interaction.guild_id,
        "host_id": interaction.user.id,
        "description": description,
    }
    
    await asyncio.sleep(secs)
    if msg.id in active_giveaways:
        await end_giveaway(msg.id, ch)

async def end_giveaway(msg_id: int, channel):
    try:
        data = active_giveaways.pop(msg_id, None)
        if not data: return
        msg = await channel.fetch_message(msg_id)
        
        # Get all entrants
        entrants = []
        for r in msg.reactions:
            if str(r.emoji) == "🎉":
                async for user in r.users():
                    if not user.bot:
                        entrants.append(user)
                break
        
        embed = discord.Embed(color=discord.Color.from_rgb(200, 200, 200))
        
        if not entrants:
            embed.title = "🎉  Giveaway Ended"
            embed.description = f"**{data['prize']}**\n\nNo valid entries."
            embed.set_footer(text="NATIVE Giveaway • No winner")
            await msg.edit(embed=embed)
            await channel.send("😔 No one entered the giveaway!")
            return
        
        import random
        num_winners = min(data["winners"], len(entrants))
        winners = random.sample(entrants, num_winners)
        winner_mentions = ", ".join(w.mention for w in winners)
        
        embed.title = "🎉  Giveaway Ended"
        embed.description = (
            f"**{data['prize']}**\n\n"
            f"🏆 **Winner{'s' if num_winners > 1 else ''}:** {winner_mentions}\n\n"
            f"**Entries:** {len(entrants)}"
        )
        embed.color = discord.Color.from_rgb(255, 185, 0)
        embed.set_footer(text="NATIVE Giveaway • Ended")
        
        await msg.edit(embed=embed)
        await channel.send(
            f"🎉 Congratulations {winner_mentions}! You won **{data['prize']}**!"
        )
    except Exception as e:
        print(f"Giveaway end error: {e}")

@bot.tree.command(name="reroll", description="Reroll a giveaway winner")
@app_commands.describe(message_id="Message ID of the ended giveaway")
@is_staff()
async def slash_reroll(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
        msg = await interaction.channel.fetch_message(mid)
        
        entrants = []
        for r in msg.reactions:
            if str(r.emoji) == "🎉":
                async for user in r.users():
                    if not user.bot:
                        entrants.append(user)
                break
        
        if not entrants:
            return await interaction.response.send_message("❌ No entries found.", ephemeral=True)
        
        import random
        winner = random.choice(entrants)
        await interaction.response.send_message(
            f"🎉 New winner: {winner.mention}! Congratulations!")
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="endgiveaway", description="End a giveaway early")
@app_commands.describe(message_id="Message ID of the giveaway")
@is_staff()
async def slash_endgiveaway(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
        if mid not in active_giveaways:
            return await interaction.response.send_message(
                "❌ No active giveaway with that ID.", ephemeral=True)
        await interaction.response.send_message("✅ Ending giveaway...", ephemeral=True)
        await end_giveaway(mid, interaction.channel)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)

@bot.tree.command(name="editgiveaway", description="Edit an active giveaway")
@app_commands.describe(
    message_id="Message ID of the giveaway",
    prize="New prize name",
    winners="New number of winners",
    description="New description"
)
@is_staff()
async def slash_editgiveaway(interaction: discord.Interaction,
                              message_id: str,
                              prize: str = None,
                              winners: int = None,
                              description: str = None):
    try:
        mid = int(message_id)
        if mid not in active_giveaways:
            return await interaction.response.send_message(
                "❌ No active giveaway with that ID.", ephemeral=True)
        
        data = active_giveaways[mid]
        if prize:     data["prize"]       = prize
        if winners:   data["winners"]     = winners
        if description is not None: data["description"] = description
        
        msg = await interaction.channel.fetch_message(mid)
        ends_ts = data["ends_ts"]
        desc_text = data.get("description")
        
        embed = discord.Embed(
            title="🎉  GIVEAWAY  🎉",
            description=(
                f"**{data['prize']}**\n\n"
                f"{f'> {desc_text}\n\n' if desc_text else ''}"
                f"React with 🎉 to enter!\n\n"
                f"**Winners:** {data['winners']}\n"
                f"**Ends:** <t:{ends_ts}:R> (<t:{ends_ts}:F>)"
            ),
            color=discord.Color.from_rgb(255, 185, 0)
        )
        embed.set_footer(text=f"NATIVE Giveaway • Edited")
        await msg.edit(embed=embed)
        await interaction.response.send_message("✅ Giveaway updated!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="listgiveaways", description="List all active giveaways")
@is_staff()
async def slash_listgiveaways(interaction: discord.Interaction):
    if not active_giveaways:
        return await interaction.response.send_message("No active giveaways.", ephemeral=True)
    embed = discord.Embed(title="🎉 Active Giveaways", color=discord.Color.from_rgb(255,185,0))
    for mid, data in active_giveaways.items():
        embed.add_field(
            name=data["prize"],
            value=f"ID: `{mid}` • {data['winners']} winner(s) • Ends <t:{data['ends_ts']}:R>",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════════════
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        try: await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)
        except: pass
    else:
        try: await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)
        except: pass

# ══════════════════════════════════════════════════════════════════════════════
#  BAN LIST HTTP SERVER  —  macro polls /bans every 10s
# ══════════════════════════════════════════════════════════════════════════════
async def handle_bans(request):
    return web.Response(text="\n".join(sorted(banned_hwids)), content_type="text/plain")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/bans", handle_bans)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Ban server on port {port}")

async def main():
    async with bot:
        await start_http_server()
        await bot.start(TOKEN)

asyncio.run(main())
