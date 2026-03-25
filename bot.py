import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import timedelta

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

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
warnings: dict[int, list[str]] = {}

@bot.event
async def on_ready():
    bot.add_view(TicketButton())
    bot.add_view(CloseTicketButton())
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.event
async def on_member_join(member):
    guild_cfg = config.get(str(member.guild.id), {})

    # Check env var first (persists on Railway), then fall back to config file
    autorole_id = os.getenv("AUTOROLE_ID") or guild_cfg.get("autorole")
    if autorole_id:
        role = member.guild.get_role(int(autorole_id))
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
                print(f"Gave role {role.name} to {member.display_name}")
            except discord.Forbidden:
                print(f"ERROR: Bot does not have permission to assign role {role.name}!")
            except Exception as e:
                print(f"ERROR giving autorole: {e}")
        else:
            print(f"ERROR: Role ID {autorole_id} not found in server!")

    welcome_channel_id = os.getenv("WELCOME_CHANNEL_ID") or guild_cfg.get("welcome_channel")
    welcome_message = os.getenv("WELCOME_MESSAGE") or guild_cfg.get("welcome_message", "Welcome to the server, {mention}!")
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
            await channel.send(f"Goodbye {member.display_name}!")

class CloseTicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Lock Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        is_admin = interaction.user.guild_permissions.administrator
        is_creator = str(interaction.user.id) in (channel.topic or "")
        if not is_admin and not is_creator:
            return await interaction.response.send_message("Only the ticket creator or an admin can close this.", ephemeral=True)
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=5))
        await channel.delete(reason=f"Ticket closed by {interaction.user}")

class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket")
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

        embed = discord.Embed(
            title=f"Ticket #{ticket_count:04d}",
            description=f"Welcome {interaction.user.mention}!\n\nPlease describe your issue and staff will assist you shortly.\n\nClick the button below to close this ticket.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Ticket by {interaction.user.display_name}")
        await channel.send(embed=embed, view=CloseTicketButton())
        await interaction.response.send_message(f"Your ticket has been created: {channel.mention}", ephemeral=True)

@bot.tree.command(name="ticketsetup", description="Set up the ticket panel in a channel")
@app_commands.describe(channel="Channel to send the ticket panel", title="Title of the panel", description="Message shown on the panel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_ticketsetup(interaction: discord.Interaction, channel: discord.TextChannel, title: str = "Support Tickets", description: str = "Click the button below to open a support ticket!"):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.set_footer(text=interaction.guild.name)
    await channel.send(embed=embed, view=TicketButton())
    await interaction.response.send_message(f"Ticket panel sent to {channel.mention}!", ephemeral=True)

@bot.tree.command(name="ping", description="Check the bot latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")

@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"Kicked {member.display_name}. Reason: {reason}")

@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason, delete_message_days=0)
    await interaction.response.send_message(f"Banned {member.display_name}. Reason: {reason}")

@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID to unban")
@app_commands.checks.has_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str):
    banned = [entry async for entry in interaction.guild.bans()]
    if user_id.isdigit():
        for entry in banned:
            if entry.user.id == int(user_id):
                await interaction.guild.unban(entry.user)
                return await interaction.response.send_message(f"Unbanned {entry.user}.")
    await interaction.response.send_message("No banned user found with that ID.")

@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.describe(member="Member to mute", minutes="Minutes (default 10)", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason"):
    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await interaction.response.send_message(f"Muted {member.display_name} for {minutes} min. Reason: {reason}")

@bot.tree.command(name="unmute", description="Remove a member timeout")
@app_commands.describe(member="Member to unmute")
@app_commands.checks.has_permissions(moderate_members=True)
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"Unmuted {member.display_name}.")

@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    warnings.setdefault(member.id, []).append(reason)
    count = len(warnings[member.id])
    await interaction.response.send_message(f"Warned {member.display_name} ({count} total). Reason: {reason}")
    try:
        await member.send(f"You were warned in {interaction.guild.name}: {reason}")
    except discord.Forbidden:
        pass

@bot.tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
@app_commands.checks.has_permissions(kick_members=True)
async def slash_warnings(interaction: discord.Interaction, member: discord.Member):
    user_warns = warnings.get(member.id, [])
    if not user_warns:
        return await interaction.response.send_message(f"{member.display_name} has no warnings.")
    embed = discord.Embed(title=f"Warnings for {member.display_name}", color=discord.Color.orange())
    for i, reason in enumerate(user_warns, 1):
        embed.add_field(name=f"Warning {i}", value=reason, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarnings", description="Clear warnings for a member")
@app_commands.describe(member="Member to clear warnings for")
@app_commands.checks.has_permissions(administrator=True)
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    warnings.pop(member.id, None)
    await interaction.response.send_message(f"Cleared warnings for {member.display_name}.")

@bot.tree.command(name="purge", description="Delete messages")
@app_commands.describe(amount="Amount to delete (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    amount = min(amount, 100)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.describe(seconds="Seconds (0 to disable)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message("Slowmode disabled." if seconds == 0 else f"Slowmode set to {seconds}s.")

@bot.tree.command(name="lock", description="Lock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_lock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("Channel locked.")

@bot.tree.command(name="unlock", description="Unlock this channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slash_unlock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("Channel unlocked.")

@bot.tree.command(name="addrole", description="Give a role to a member")
@app_commands.describe(member="Member", role="Role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"Added {role.name} to {member.display_name}.")

@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.describe(member="Member", role="Role")
@app_commands.checks.has_permissions(manage_roles=True)
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"Removed {role.name} from {member.display_name}.")

@bot.tree.command(name="userinfo", description="Get info about a member")
@app_commands.describe(member="Member to look up")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"User Info - {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Get server info")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner", value=g.owner.mention, inline=True)
    embed.add_field(name="Members", value=g.member_count, inline=True)
    embed.add_field(name="Channels", value=len(g.channels), inline=True)
    embed.add_field(name="Roles", value=len(g.roles), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member avatar")
@app_commands.describe(member="Member")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.display_name} Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setautorole", description="Auto-give a role to new members")
@app_commands.describe(role="Role to auto-give")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setautorole(interaction: discord.Interaction, role: discord.Role):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["autorole"] = str(role.id)
    save_config(config)
    embed = discord.Embed(title="Auto-Role Set!", color=discord.Color.green())
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.add_field(name="Role ID", value=str(role.id), inline=True)
    embed.add_field(
        name="Important - Make it permanent!",
        value=f"To make this survive bot restarts, add this to Railway Variables:\n`AUTOROLE_ID` = `{role.id}`",
        inline=False
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setwelcome", description="Set welcome channel and message")
@app_commands.describe(channel="Welcome channel", message="Message (use {mention} {name} {server})")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setwelcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["welcome_channel"] = str(channel.id)
    if message:
        guild_cfg["welcome_message"] = message
    save_config(config)
    await interaction.response.send_message(f"Welcome channel set to {channel.mention}.")

@bot.tree.command(name="setleave", description="Set leave channel")
@app_commands.describe(channel="Leave channel")
@app_commands.checks.has_permissions(administrator=True)
async def slash_setleave(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_cfg = config.setdefault(str(interaction.guild.id), {})
    guild_cfg["leave_channel"] = str(channel.id)
    save_config(config)
    await interaction.response.send_message(f"Leave channel set to {channel.mention}.")

@bot.tree.command(name="help", description="Show all commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.green())
    embed.add_field(name="Tickets", value="`/ticketsetup`", inline=False)
    embed.add_field(name="Setup", value="`/setautorole` `/setwelcome` `/setleave`", inline=False)
    embed.add_field(name="Moderation", value="`/kick` `/ban` `/unban` `/mute` `/unmute` `/warn` `/warnings` `/clearwarnings` `/purge` `/slowmode` `/lock` `/unlock`", inline=False)
    embed.add_field(name="Roles", value="`/addrole` `/removerole`", inline=False)
    embed.add_field(name="Info", value="`/userinfo` `/serverinfo` `/avatar` `/ping`", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

bot.run(TOKEN)
