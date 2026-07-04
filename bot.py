import discord
import random
import datetime
import asyncio
import re
import sqlite3
import os
import aiohttp

# Safe reading of the token from environment variables
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
COMMAND_PREFIX = "v!"

if not BOT_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN variable is missing in environment settings!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
client = discord.Client(intents=intents)

BOT_START_TIME = datetime.datetime.now()

# --- DATABASE SETUP ---
DB_PATH = "database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Warnings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reason TEXT,
            enforcer TEXT,
            timestamp TEXT
        )
    """)
    # AFK table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS afk (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            timestamp TEXT
        )
    """)
    # Settings table (botlock and botisolate)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            vector_id INTEGER PRIMARY KEY,
            type TEXT
        )
    """)
    # Progressive timeout for mention spam
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mention_strikes (
            user_id INTEGER PRIMARY KEY,
            strike_count INTEGER DEFAULT 0
        )
    """)
    # User profiles (bio)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY,
            bio TEXT
        )
    """)
    # Per-server custom command prefix
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS guild_prefixes (
            guild_id INTEGER PRIMARY KEY,
            prefix TEXT
        )
    """)
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# --- DATABASE HELPER FUNCTIONS ---
def get_lock_and_isolate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT vector_id FROM system_settings WHERE type = 'lock'")
    locked = set(row[0] for row in cursor.fetchall())
    cursor.execute("SELECT vector_id FROM system_settings WHERE type = 'isolate'")
    isolated = set(row[0] for row in cursor.fetchall())
    conn.close()
    return locked, isolated

def toggle_setting(vector_id, setting_type):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM system_settings WHERE vector_id = ? AND type = ?", (vector_id, setting_type))
    exists = cursor.fetchone()
    if exists:
        cursor.execute("DELETE FROM system_settings WHERE vector_id = ? AND type = ?", (vector_id, setting_type))
        removed = True
    else:
        cursor.execute("INSERT INTO system_settings (vector_id, type) VALUES (?, ?)", (vector_id, setting_type))
        removed = False
    conn.commit()
    conn.close()
    return removed

def get_and_increment_strike(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT strike_count FROM mention_strikes WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        new_strike = row[0] + 1
        cursor.execute("UPDATE mention_strikes SET strike_count = ? WHERE user_id = ?", (new_strike, user_id))
    else:
        new_strike = 1
        cursor.execute("INSERT INTO mention_strikes (user_id, strike_count) VALUES (?, ?)", (user_id, new_strike))
    conn.commit()
    conn.close()
    return new_strike

def get_guild_prefix(guild_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT prefix FROM guild_prefixes WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else COMMAND_PREFIX

def set_guild_prefix(guild_id, prefix):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO guild_prefixes (guild_id, prefix) VALUES (?, ?)", (guild_id, prefix))
    conn.commit()
    conn.close()

def get_bio(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT bio FROM profiles WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_bio(user_id, bio):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO profiles (user_id, bio) VALUES (?, ?)", (user_id, bio))
    conn.commit()
    conn.close()

# Tracks active recurring v!remindme tasks per user, so they can be cancelled with v!remindme stop
active_reminders = {}

def parse_duration(time_str: str) -> int:
    match = re.match(r"^(\d+)([smhd])$", time_str.lower())
    if not match:
        return 0
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "s": return amount
    if unit == "m": return amount * 60
    if unit == "h": return amount * 3600
    if unit == "d": return amount * 86400
    return 0

async def send_punishment_dm(target_user, guild, action, duration_str=None, reason=None):
    """DMs the punished user with a colored embed. Returns True if delivered, False if their DMs are closed."""
    colors = {
        "mute": 0xF1C40F,      # yellow
        "kick": 0xE67E22,      # orange
        "ban": 0xE74C3C,       # red
        "tempban": 0xE74C3C,   # red
        "mute_expired": 0x2ECC71,     # green
        "tempban_expired": 0x2ECC71,  # green
    }
    titles = {
        "mute": "🤫 You've Been Muted",
        "kick": "👢 You've Been Kicked",
        "ban": "⛔ You've Been Banned",
        "tempban": "⛔ You've Been Temporarily Banned",
        "mute_expired": "🔊 Mute Ended",
        "tempban_expired": "🔓 Ban Ended",
    }
    descriptions = {
        "mute": f"You were muted for **{duration_str}** in **{guild.name}**.",
        "kick": f"You were kicked from **{guild.name}**.",
        "ban": f"You were banned permanently from **{guild.name}**.",
        "tempban": f"You were banned for **{duration_str}** from **{guild.name}**.",
        "mute_expired": f"Your mute in **{guild.name}** has ended. You can speak again.",
        "tempban_expired": f"Your ban in **{guild.name}** has ended. You're welcome to rejoin.",
    }

    embed = discord.Embed(title=titles.get(action, "Notice"), description=descriptions.get(action, ""), color=colors.get(action, 0x3498DB))
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)

    try:
        await target_user.send(embed=embed)
        return True
    except discord.Forbidden:
        return False

# --- INTERACTIVE PAGINATION COMPONENT INTERACTION VIEW ---
class CommandPaginationView(discord.ui.View):
    def __init__(self, author, embeds):
        super().__init__(timeout=60.0)
        self.author = author
        self.embeds = embeds
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.left_button.disabled = self.current_page == 0
        self.right_button.disabled = self.current_page == len(self.embeds) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ This control interface is locked to the operator who initialized the session matrix.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Left", style=discord.ButtonStyle.primary, custom_id="btn_prev")
    async def left_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Right ▶", style=discord.ButtonStyle.primary, custom_id="btn_next")
    async def right_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, 'message'):
                await self.message.edit(view=self)
        except Exception:
            pass

@client.event
async def on_ready():
    print(f"System Matrix Live: Logged in as {client.user} (SQLite Database Connected)")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # BUG FIX: without this guard, a DM to the bot crashes on the next line
    # because message.author.guild_permissions doesn't exist outside a guild.
    if message.guild is None:
        return

    is_admin = message.author.guild_permissions.administrator

    # =========================================================================
    # 🛡️ SECURITY LAYER: ANTI-INVITE & ANTI-MENTION SPAM
    # =========================================================================
    # NOTE: This block is a background security shield. It runs ALWAYS, everywhere,
    # even if the channel is blocked via botlock or the bot is isolated via botisolate.
    # The database, violation logging, and chat protection are always active.
    # Admins have full immunity to all filters.

    if not is_admin:
        # 1. Anti-Invite Filter
        invite_pattern = r"(discord\.(gg|io|me|li)\/.+|discord\.com\/invite\/.+)"
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            try:
                await message.delete()
                alert_embed = discord.Embed(
                    title="🚨 Third-Party Invite Intercepted",
                    description=f"User <@{message.author.id}> attempted to deploy an unauthorized invite link. Transmission purged.",
                    color=15158332
                )
                await message.channel.send(embed=alert_embed, delete_after=10.0)
                return  # Stop execution since message is destroyed
            except discord.Forbidden:
                pass

        # 2. Anti-Mention Spammer (Progressive Scale)
        unique_mentions = set(message.mentions)
        if len(unique_mentions) > 5:
            strikes = get_and_increment_strike(message.author.id)
            
            if strikes == 1:
                duration = datetime.timedelta(minutes=1)
                time_word = "1 minute"
            elif strikes == 2:
                duration = datetime.timedelta(minutes=10)
                time_word = "10 minutes"
            else:
                duration = datetime.timedelta(hours=1)
                time_word = "1 hour"
                
            try:
                await message.author.timeout(duration, reason=f"Mass mention flood (Strike #{strikes})")
                await message.delete()
                
                spam_embed = discord.Embed(
                    title="🤫 Protocol Silence Imposed",
                    description=f"<@{message.author.id}> has been muted for **{time_word}** due to excessive mentions (>5 unique users).\nInfraction level: **Strike #{strikes}**.",
                    color=15158332
                )
                await message.channel.send(embed=spam_embed)
                return
            except discord.Forbidden:
                pass

    # =========================================================================
    # 💤 BACKGROUND REPUTATION / AFK TRACKING LAYER
    # =========================================================================
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (message.author.id,))
    afk_record = cursor.fetchone()
    if afk_record:
        cursor.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
        conn.commit()
        
        saved_time = datetime.datetime.strptime(afk_record[1], "%Y-%m-%d %H:%M:%S")
        duration = datetime.datetime.now() - saved_time
        minutes = round(duration.total_seconds() / 60, 1)
        
        embed = discord.Embed(
            description=f"👋 Welcome back <@{message.author.id}>! I have removed your AFK status. (Away for {minutes}m)",
            color=65280
        )
        await message.channel.send(embed=embed)

    if message.mentions and not message.content.strip().lower().startswith(COMMAND_PREFIX.lower() + "afk"):
        for target in message.mentions:
            cursor.execute("SELECT reason FROM afk WHERE user_id = ?", (target.id,))
            mention_afk = cursor.fetchone()
            if mention_afk:
                embed = discord.Embed(
                    description=f"💤 **{target.name}** is currently AFK: {mention_afk[0]}",
                    color=15844367
                )
                await message.channel.send(embed=embed)
    conn.close()

    # --- PREFIX VALIDATION (supports a custom per-server prefix via v!prefix) ---
    raw_text = message.content.strip()
    guild_prefix = get_guild_prefix(message.guild.id)
    prefix_lower = guild_prefix.lower()
    if not raw_text.lower().startswith(prefix_lower):
        return

    command_body = raw_text[len(guild_prefix):].strip()
    command_lower = command_body.lower()

    # =========================================================================
    #    COMMAND ACCESS CONTROL: BASIC (everyone) VS ADMIN COMMANDS
    # =========================================================================
    command_name = command_lower.split()[0] if command_lower else ""

    ADMIN_COMMANDS = {
        "say", "purge", "botlock", "botisolate", "lockdown", "mute", "kick",
        "ban", "tempban", "unban", "warn", "delwarn", "unwarnall", "warns",
        "botavatar", "botname", "slowmode", "prefix"
    }

    if command_name in ADMIN_COMMANDS and not is_admin:
        await message.channel.send("❌ Access Denied: This command requires Administrator permissions.")
        return

    # =========================================================================
    #     BOTLOCK & BOTISOLATE INTERCEPTOR (COMMAND FILTERS)
    # =========================================================================
    # USAGE GUIDE FOR LOCKS:
    #
    # 1. v!botlock [Channel / Category ID] (BLACKLIST):
    #    Adds a text channel or an entire category ID to the database blacklist. Inside these
    #    zones the bot will fully ignore any command input. Running the command again lifts the lock.
    #
    # 2. v!botisolate [Channel / Category ID] (WHITELIST):
    #    Enables full core isolation mode. If the isolation table contains at least one ID, the bot
    #    will execute commands ONLY inside those specified channels/categories, fully ignoring the rest of the server.

    channel_id = message.channel.id
    category_id = message.channel.category_id if hasattr(message.channel, 'category_id') else None
    locked_channels, isolated_vectors = get_lock_and_isolate()
    
    # Apply Botisolate whitelist filter
    if isolated_vectors:
        if (channel_id not in isolated_vectors) and (category_id not in isolated_vectors):
            return

    # Apply Botlock blacklist filter
    if channel_id in locked_channels or (category_id and category_id in locked_channels):
        return


    # --- COMMAND: PING ---
    if command_lower == "ping":
        latency = round(client.latency * 1000)
        await message.channel.send(f"🏓 **Pong!** Latency: {latency}ms")
        return

    # --- COMMAND: SAY ---
    if command_lower.startswith("say"):
        repeat_text = command_body[3:].strip()
        if not repeat_text:
            await message.channel.send(f"Error: Specify the text to repeat. Usage: `{COMMAND_PREFIX}say <text>`")
            return
        try: await message.delete()
        except discord.Forbidden: pass
        await message.channel.send(repeat_text)
        return

    # --- COMMAND: PURGE ---
    if command_lower.startswith("purge"):
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.channel.send(f"Error: Specify total blocks to flush. Usage: `{COMMAND_PREFIX}purge <number>`")
            return
        
        amount = int(args[1]) + 1
        if amount > 100: amount = 100
            
        try:
            deleted = await message.channel.purge(limit=amount)
            confirm_log = await message.channel.send(f"🗑️ Successfully flushed `{len(deleted)-1}` message clusters from this sector index.")
            await asyncio.sleep(3.0)
            await confirm_log.delete()
        except discord.Forbidden:
            await message.channel.send("Error: Internal core missing message clear permission flags.")
        return

    # --- COMMAND: BOTLOCK ---
    if command_lower.startswith("botlock"):
        args = command_body.split()
        target_id = channel_id

        if message.channel_mentions:
            target_id = message.channel_mentions[0].id
        elif len(args) > 1 and args[1].isdigit():
            target_id = int(args[1])

        removed = toggle_setting(target_id, "lock")
        if removed:
            await message.channel.send(f"🔓 **Botlock Lifted:** Vector node ID `{target_id}` is now accepting user input arrays again.")
        else:
            await message.channel.send(f"🔒 **Botlock Imposed:** Subsystem sector ID `{target_id}` added to blacklist. Commands disabled here.")
        return

    # --- COMMAND: BOTISOLATE ---
    if command_lower.startswith("botisolate"):
        args = command_body.split()
        target_id = channel_id

        if message.channel_mentions:
            target_id = message.channel_mentions[0].id
        elif len(args) > 1 and args[1].isdigit():
            target_id = int(args[1])

        removed = toggle_setting(target_id, "isolate")
        if removed:
            await message.channel.send(f"🔓 **Isolation Reset:** Node ID `{target_id}` pulled from whitelist directory.")
        else:
            await message.channel.send(f"🛡️ **System Isolated:** Core operational array locked! All commands disabled server-wide **EXCEPT** inside ID `{target_id}`.")
        return

    # --- COMMAND: PREFIX ---
    if command_lower.startswith("prefix"):
        args = command_body.split(maxsplit=1)
        if len(args) < 2:
            current = get_guild_prefix(message.guild.id)
            await message.channel.send(f"⚙️ Current prefix for this server: `{current}`")
            return
        new_prefix = args[1].strip()
        if not new_prefix or len(new_prefix) > 5 or " " in new_prefix:
            await message.channel.send("Error: Prefix must be 1-5 characters with no spaces.")
            return
        set_guild_prefix(message.guild.id, new_prefix)
        await message.channel.send(f"✅ Prefix updated to `{new_prefix}` for this server.")
        return

    # --- COMMAND: LOCKDOWN ---
    if command_lower == "lockdown":
        overwrite = message.channel.overwrites_for(message.guild.default_role)
        if overwrite.send_messages is False:
            overwrite.send_messages = None
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            await message.channel.send(embed=discord.Embed(title="🔓 Lockdown Overridden", description="Channel traffic constraints lifted.", color=3066993))
        else:
            overwrite.send_messages = False
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            await message.channel.send(embed=discord.Embed(title="🔒 Emergency Lockdown Imposed", description="Text transmission lines cut for non-admin accounts.", color=15158332))
        return

    # --- COMMAND: MUTE / TIMEOUT ---
    if command_lower.startswith("mute"):
        if not message.mentions:
            await message.channel.send(f"Error: Usage syntax: `{COMMAND_PREFIX}mute @user <duration: 10s/5m/2h> [reason]`")
            return
        args = command_body.split()
        if len(args) < 3:
            await message.channel.send("Error: Please provide a valid duration constraint (e.g., 10m, 1h).")
            return
        target_user = message.mentions[0]
        duration_str = args[2]
        seconds = parse_duration(duration_str)
        if seconds <= 0:
            await message.channel.send("Error: Invalid time configuration format.")
            return
        reason = " ".join(args[3:]) if len(args) > 3 else "Violation of standardized conduct."
        try:
            await target_user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
            dm_sent = await send_punishment_dm(target_user, message.guild, "mute", duration_str=duration_str, reason=reason)
            note = "" if dm_sent else " (DM could not be delivered)"
            await message.channel.send(f"🤫 **{target_user.name}** has been muted for **{duration_str}**. Reason: *{reason}*{note}")

            async def mute_expiry_notice():
                await asyncio.sleep(seconds)
                await send_punishment_dm(target_user, message.guild, "mute_expired")

            asyncio.create_task(mute_expiry_notice())
        except discord.Forbidden:
            await message.channel.send("Error: Missing structural role hierarchy positioning.")
        return

    # --- COMMAND: KICK ---
    if command_lower.startswith("kick"):
        if not message.mentions:
            await message.channel.send(f"Error: Usage syntax: `{COMMAND_PREFIX}kick @user [reason]`")
            return
        target_user = message.mentions[0]
        args = command_body.split(maxsplit=2)
        reason = args[2] if len(args) > 2 else "Kicked by an administrator sequence override."
        try:
            # DM before kicking — once kicked, the bot may lose the ability to message them.
            dm_sent = await send_punishment_dm(target_user, message.guild, "kick", reason=reason)
            await target_user.kick(reason=reason)
            note = "" if dm_sent else " (DM could not be delivered)"
            await message.channel.send(f"👢 **{target_user.name}** has been kicked from the server. Reason: *{reason}*{note}")
        except discord.Forbidden:
            await message.channel.send("Error: Missing structural role hierarchy positioning.")
        return

    # --- COMMAND: BAN ---
    if command_lower.startswith("ban"):
        if not message.mentions:
            await message.channel.send(f"Error: Usage syntax: `{COMMAND_PREFIX}ban @user [reason]`")
            return
        target_user = message.mentions[0]
        args = command_body.split(maxsplit=2)
        reason = args[2] if len(args) > 2 else "Permanent ban penalty override."
        try:
            # DM before banning — once banned, the bot may lose the ability to message them.
            dm_sent = await send_punishment_dm(target_user, message.guild, "ban", reason=reason)
            await message.guild.ban(target_user, reason=reason)
            note = "" if dm_sent else " (DM could not be delivered)"
            await message.channel.send(f"⛔ **{target_user.name}** has been permanently banned. Reason: *{reason}*{note}")
        except discord.Forbidden:
            await message.channel.send("Error: Missing structural role hierarchy positioning.")
        return

    # --- COMMAND: TEMPBAN ---
    if command_lower.startswith("tempban"):
        if not message.mentions:
            await message.channel.send(f"Error: Usage syntax: `{COMMAND_PREFIX}tempban @user <duration: 10s/5m/2h/1d> [reason]`")
            return
        args = command_body.split()
        if len(args) < 3:
            await message.channel.send("Error: Please provide a valid duration constraint.")
            return
        target_user = message.mentions[0]
        duration_str = args[2]
        seconds = parse_duration(duration_str)
        if seconds <= 0:
            await message.channel.send("Error: Invalid time configuration format.")
            return
        reason = " ".join(args[3:]) if len(args) > 3 else "Temporary ban penalty override."
        try:
            # DM before banning — once banned, the bot may lose the ability to message them.
            dm_sent = await send_punishment_dm(target_user, message.guild, "tempban", duration_str=duration_str, reason=reason)
            await message.guild.ban(target_user, reason=f"[Tempban for {duration_str}] {reason}")
            note = "" if dm_sent else " (DM could not be delivered)"
            await message.channel.send(f"⛔ **{target_user.name}** has been banned for **{duration_str}**. Reason: *{reason}*{note}")

            async def tempban_expiry():
                await asyncio.sleep(seconds)
                try:
                    await message.guild.unban(target_user, reason="Temporary ban duration expired.")
                    await message.channel.send(f"🔓 **{target_user.name}**'s tempban duration expired. User unbanned automatically.")
                    await send_punishment_dm(target_user, message.guild, "tempban_expired")
                except discord.NotFound:
                    pass

            asyncio.create_task(tempban_expiry())
        except discord.Forbidden:
            await message.channel.send("Error: Missing structural role hierarchy positioning.")
        return

    # --- COMMAND: UNBAN ---
    if command_lower.startswith("unban"):
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.channel.send(f"Error: Specify target raw user id. Usage: `{COMMAND_PREFIX}unban <user_id>`")
            return
        target_id = int(args[1])
        try:
            await message.guild.unban(discord.Object(id=target_id), reason="Manual unban override sequence initialized.")
            await message.channel.send(f"🔓 Successfully lifted network ban restrictions from profile footprint ID: `{target_id}`.")
        except discord.NotFound:
            await message.channel.send("Error: That ID profile does not exist or was not flagged inside the server ban log files.")
        except discord.Forbidden:
            await message.channel.send("Error: Bot missing necessary role permission settings.")
        return

    # --- COMMAND: RANDOMCOLOR ---
    if command_lower == "randomcolor":
        r, g, b = random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
        hex_code = f"#{r:02x}{g:02x}{b:02x}".upper()
        embed = discord.Embed(title="🎨 Color Matrix Generator", description=f"**Hex Value:** `{hex_code}`\n**RGB Structural Array:** `rgb({r}, {g}, {b})`", color=discord.Color.from_rgb(r, g, b))
        embed.set_thumbnail(url=f"https://singlecolorimage.com/get/{hex_code.replace('#','')}/100x100")
        await message.channel.send(embed=embed)
        return

    # --- COMMAND: REMINDME (one-time, DM delivery, and recurring reminders) ---
    if command_lower.startswith("remindme"):
        args = command_body.split()

        # v!remindme stop -> cancels all your active recurring reminders
        if len(args) == 2 and args[1].lower() == "stop":
            tasks = active_reminders.get(message.author.id, [])
            if not tasks:
                await message.channel.send("You have no active recurring reminders.")
                return
            for task in tasks:
                task.cancel()
            active_reminders[message.author.id] = []
            await message.channel.send("🛑 All your recurring reminders have been stopped.")
            return

        if len(args) < 3:
            await message.channel.send(
                f"Error: Usage:\n"
                f"`{COMMAND_PREFIX}remindme <time> <note>` — one-time reminder in this channel\n"
                f"`{COMMAND_PREFIX}remindme dm <time> <note>` — one-time reminder via DM\n"
                f"`{COMMAND_PREFIX}remindme every <time> <note>` — repeating reminder in this channel\n"
                f"`{COMMAND_PREFIX}remindme every dm <time> <note>` — repeating reminder via DM\n"
                f"`{COMMAND_PREFIX}remindme stop` — cancel your recurring reminders"
            )
            return

        idx = 1
        recurring = False
        use_dm = False

        if args[idx].lower() == "every":
            recurring = True
            idx += 1
        if idx < len(args) and args[idx].lower() == "dm":
            use_dm = True
            idx += 1

        if idx + 1 >= len(args):
            await message.channel.send("Error: Missing duration or note text.")
            return

        time_str = args[idx]
        seconds = parse_duration(time_str)
        if seconds <= 0:
            await message.channel.send("Error: Invalid time format. Use e.g. 10s, 5m, 1h, 1d.")
            return
        note = " ".join(args[idx + 1:]).strip()
        if not note:
            await message.channel.send("Error: Provide a reminder note.")
            return

        destination_text = "your DMs" if use_dm else "this channel"
        timing_text = f"every {time_str}" if recurring else f"in {time_str}"
        await message.channel.send(f"⏰ Reminder registered! I will remind you about **'{note}'** {timing_text}, delivered to {destination_text}.")

        author = message.author
        channel = message.channel

        async def send_reminder():
            try:
                if use_dm:
                    await author.send(f"⏰ Reminder: \"{note}\"")
                else:
                    await channel.send(f"⏰ Hey <@{author.id}>, reminder: \"{note}\"!")
            except discord.Forbidden:
                await channel.send(f"❌ Couldn't DM <@{author.id}> — your DMs may be disabled.")

        async def reminder_task():
            try:
                if recurring:
                    while True:
                        await asyncio.sleep(seconds)
                        await send_reminder()
                else:
                    await asyncio.sleep(seconds)
                    await send_reminder()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(reminder_task())
        if recurring:
            active_reminders.setdefault(author.id, []).append(task)
        return

    # --- UPDATED COMMANDS: EXCLUSIVE DM MANAGEMENT CONSOLE ---
    if command_lower.startswith("dm"):
        dm_payload = command_body[2:].strip()
        if not dm_payload:
            await message.channel.send("Error: Content payload missing.")
            return

        # Variant 1: v!dm @everyone [text]
        if dm_payload.startswith("@everyone"):
            message_content = dm_payload[9:].strip()
            if not message_content:
                await message.channel.send("Error: Broadcast message body cannot be blank.")
                return

            await message.channel.send('Are you sure you want to message everyone? Type "Confirm" to confirm.')

            def check(m):
                return m.author == message.author and m.channel == message.channel

            try:
                confirm_message = await client.wait_for('message', check=check, timeout=15.0)
                if confirm_message.content.strip() == "Confirm":
                    status_msg = await message.channel.send("🔄 Processing broadcast queue...")
                    success_count, fail_count = 0, 0
                    
                    async for member in message.guild.fetch_members(limit=None):
                        if member.bot: continue
                        try:
                            broadcast_embed = discord.Embed(title="📢 Server Announcement", description=message_content, color=0x9B59B6)
                            broadcast_embed.set_footer(text=f"Sent by {message.author.name} in {message.guild.name}")
                            await member.send(embed=broadcast_embed)
                            success_count += 1
                            await asyncio.sleep(0.5)
                        except discord.Forbidden:
                            fail_count += 1
                            
                    await status_msg.edit(content=f"📢 **Broadcast Complete.** Delivered cleanly to `{success_count}` users. Failed: `{fail_count}`.")
                else:
                    await message.channel.send("❌ Broadcast cancelled.")
            except asyncio.TimeoutError:
                await message.channel.send("❌ Broadcast cancelled. Timeout.")
            return

        # Variant 2: v!dm @user [text]
        elif message.mentions:
            target_user = message.mentions[0]
            mention_str = f"<@{target_user.id}>"
            alt_mention_str = f"<@!{target_user.id}>"
            message_content = dm_payload.replace(mention_str, "").replace(alt_mention_str, "").strip()
            
            if not message_content:
                await message.channel.send("Error: Please provide a valid text string after user mention target.")
                return
                
            try:
                dm_embed = discord.Embed(title="📨 You've Received a Message", description=message_content, color=0x3498DB)
                dm_embed.set_footer(text=f"Sent by {message.author.name} in {message.guild.name}")
                await target_user.send(embed=dm_embed)
                await message.channel.send(f"✅ Success! Message delivered to {target_user.name}")
            except discord.Forbidden:
                await message.channel.send(f"❌ Transmission dropped: User has direct messages disabled.")
            return
            
        # Variant 3: v!dm [text]
        else:
            try:
                dm_embed = discord.Embed(title="📨 Message to Yourself", description=dm_payload, color=0x3498DB)
                dm_embed.set_footer(text=f"Requested from {message.guild.name}")
                await message.author.send(embed=dm_embed)
                await message.channel.send("✅ Success! Message mirrored to your direct messages.")
            except discord.Forbidden:
                await message.channel.send("❌ Transmission dropped: Verify your data security settings layout.")
            return

    # --- COMMANDS: WARN SYSTEM (SQLite) ---
    if command_lower.startswith("warns"):
        if not message.mentions:
            await message.channel.send(f"Error: Tag a user. Usage: `{COMMAND_PREFIX}warns @user`")
            return
        target = message.mentions[0]
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT reason, enforcer, timestamp FROM warnings WHERE user_id = ?", (target.id,))
        user_warns = cursor.fetchall()
        conn.close()
        
        embed = discord.Embed(title=f"Infraction History: {target.name}", color=15158332)
        if not user_warns:
            embed.description = "Clean profile ledger. No outstanding structural warnings registered."
        else:
            for idx, warn in enumerate(user_warns, 1):
                embed.add_field(name=f"Case Record #{idx}", value=f"**Reason:** {warn[0]}\n**Enforcer:** {warn[1]}\n**Timestamp:** {warn[2]}", inline=False)
        await message.channel.send(embed=embed)
        return

    # COMMAND: UNWARNALL
    elif command_lower.startswith("unwarnall"):
        if not message.mentions:
            await message.channel.send(f"Error: Specify target user. Usage: `{COMMAND_PREFIX}unwarnall @user`")
            return
        target = message.mentions[0]
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM warnings WHERE user_id = ?", (target.id,))
        conn.commit()
        conn.close()
        
        await message.channel.send(f"✨ Infraction History Cleared: All warning entries dropped from <@{target.id}> footprint ledger.")
        return

    elif command_lower.startswith("delwarn"):
        if not message.mentions:
            await message.channel.send(f"Error: Specify parameter targets. Usage: `{COMMAND_PREFIX}delwarn @user #1`")
            return
        target = message.mentions[0]
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, reason FROM warnings WHERE user_id = ?", (target.id,))
        user_warns = cursor.fetchall()
        if not user_warns:
            await message.channel.send(f"Error: <@{target.id}> possesses an empty history trace matrix.")
            conn.close()
            return
        case_search = re.search(r'#(\d+)', command_body)
        if not case_search:
            await message.channel.send(f"Error: Format syntax: `{COMMAND_PREFIX}delwarn @user #1`")
            conn.close()
            return
        case_num = int(case_search.group(1))
        if case_num < 1 or case_num > len(user_warns):
            await message.channel.send(f"Error: Out-of-bounds target parameter. Total logs count: `{len(user_warns)}`.")
            conn.close()
            return
        db_id_to_delete = user_warns[case_num - 1][0]
        cursor.execute("DELETE FROM warnings WHERE id = ?", (db_id_to_delete,))
        conn.commit()
        conn.close()
        await message.channel.send(f"🗑️ Infraction Entry Cleared: Case Record ID **#{case_num}** successfully purged.")
        return

    elif command_lower.startswith("warn"):
        if not message.mentions:
            await message.channel.send("Error: Specify user parameter.")
            return
        target = message.mentions[0]
        args = command_body.split(maxsplit=2)
        reason = args[2] if len(args) > 2 else "Violation of standardized operational conduct rules."
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO warnings (user_id, reason, enforcer, timestamp) VALUES (?, ?, ?, ?)", (target.id, reason, message.author.name, timestamp))
        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (target.id,))
        total_warns = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        
        embed = discord.Embed(title="⚠️ Infraction Log Registered", description=f"Profile <@{target.id}> has received an official warning entry.", color=15158332)
        embed.add_field(name="Reason Profile", value=reason, inline=True)
        embed.add_field(name="Total Violations", value=str(total_warns), inline=True)
        await message.channel.send(embed=embed)
        return

    # --- COMMAND: WHOIS ---
    if command_lower.startswith("whois"):
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            await message.channel.send(f"Error: Specify raw target profile footprint ID. Usage: `{COMMAND_PREFIX}whois <Discord_ID>`")
            return
        target_id = int(args[1])
        try:
            user_data = await client.fetch_user(target_id)
            embed = discord.Embed(title=f"Identity Footprint Trace: {user_data.name}", color=3447003)
            embed.set_thumbnail(url=user_data.display_avatar.url)
            embed.add_field(name="Account Username", value=user_data.name, inline=True)
            embed.add_field(name="Profile Mention Vector", value=f"<@{user_data.id}>", inline=True)
            embed.add_field(name="Node Created At", value=f"{user_data.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC", inline=False)
            if user_data.bot:
                embed.add_field(name="System Status Flags", value="🤖 Automated bot-account script engine profile.", inline=False)
            await message.channel.send(embed=embed)
        except discord.NotFound:
            await message.channel.send("❌ Error: Target entry footprint ID not logged inside global Discord API network nodes.")
        except discord.HTTPException:
            await message.channel.send("❌ Error: Global endpoint connection handshake dropped by cloud directory.")
        return

    # --- RETAINED CORE SUB-SYSTEM COMMANDS ---
    if command_lower.startswith("afk"):
        reason = command_body[3:].strip() or "Status set to currently away from user keyboard terminal."
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO afk (user_id, reason, timestamp) VALUES (?, ?, ?)", (message.author.id, reason, timestamp_str))
        conn.commit()
        conn.close()
        await message.channel.send(embed=discord.Embed(description=f"💤 <@{message.author.id}> is now AFK: **{reason}**", color=10181046))
        return

    # --- COMMAND: REACTION (reacts to the message you replied to) ---
    if command_lower.startswith("reaction"):
        emoji = command_body[8:].strip()
        if not emoji:
            await message.channel.send(f"Error: Provide an emoji. Usage: reply to a message with `{COMMAND_PREFIX}reaction <emoji>`")
            return
        if not message.reference:
            await message.channel.send("Error: You must reply to a message to use this command.")
            return
        try:
            replied_message = message.reference.resolved
            if replied_message is None or isinstance(replied_message, discord.DeletedReferencedMessage):
                replied_message = await message.channel.fetch_message(message.reference.message_id)
            await replied_message.add_reaction(emoji)
            try:
                await message.delete()
            except discord.Forbidden:
                pass
        except discord.NotFound:
            await message.channel.send("Error: Couldn't find the message you replied to.")
        except discord.HTTPException:
            await message.channel.send("❌ Error: That doesn't look like a valid emoji.")
        except discord.Forbidden:
            await message.channel.send("❌ Error: Missing permission to add reactions.")
        return

    if command_lower.startswith("slowmode"):
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit(): return
        seconds = int(args[1])
        await message.channel.edit(slowmode_delay=seconds)
        await message.channel.send(f"⏱️ **Slowmode:** configured to `{seconds}` seconds.")
        return

    if command_lower == "serverinfo":
        guild = message.guild
        embed = discord.Embed(title=f"Server: {guild.name}", color=3447003)
        embed.add_field(name="Total Accounts Profile", value=f"👥 {guild.member_count} users", inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Created At", value=f"{guild.created_at.strftime('%Y-%m-%d')}", inline=True)
        await message.channel.send(embed=embed)
        return

    if command_lower == "info":
        embed = discord.Embed(title="⚙️ Core System Diagnostics", color=3447003)
        embed.add_field(name="Framework", value="Discord.py Structural Matrix", inline=True)
        embed.add_field(name="Database Cluster", value="Synchronous SQLite Node", inline=True)
        await message.channel.send(embed=embed)
        return

    if command_lower.startswith("math"):
        expr = command_body[4:].strip()
        clean_expr = re.sub(r'[^0-9\+\-\*\/\(\)\.\s]', '', expr)
        try:
            result = eval(clean_expr, {"__builtins__": None}, {})
            await message.channel.send(f"📊 `{expr}` = **{result}**")
        except Exception: pass
        return

    if command_lower.startswith("botavatar"):
        url = command_body[9:].strip()
        if not url: return
        status_msg = await message.channel.send("⏳ Accessing network asset and configuring profile data...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200: return
                    image_bytes = await response.read()
            await message.guild.me.edit(avatar=image_bytes)
            await status_msg.edit(content="✅ Success! Bot avatar updated for **this server only**.")
        except Exception as e: await status_msg.edit(content=f"❌ Operational Fault: {e}")
        return

    if command_lower.startswith("botname"):
        new_nickname = command_body[7:].strip()
        if not new_nickname: return
        try:
            await message.guild.me.edit(nick=new_nickname)
            await message.channel.send(f"✅ Success! Local execution network nickname configured to: **{new_nickname}**")
        except Exception as e: await message.channel.send(f"❌ Operational Fault: {e}")
        return

    if command_lower.startswith("avatar"):
        target = message.mentions[0] if message.mentions else message.author
        embed = discord.Embed(title=f"{target.name}'s Avatar")
        embed.set_image(url=target.display_avatar.url)
        await message.channel.send(embed=embed)
        return

    if command_lower.startswith("userinfo"):
        target = message.mentions[0] if message.mentions else message.author
        joined_at = target.joined_at.strftime('%Y-%m-%d %H:%M') if target.joined_at else "Unknown"
        created_at = target.created_at.strftime('%Y-%m-%d %H:%M')
        embed = discord.Embed(title=f"User Info: {target.name}", color=target.color)
        embed.add_field(name="Account Footprint ID", value=f"`{target.id}`", inline=False)
        embed.add_field(name="Joined Server Matrix", value=joined_at, inline=True)
        embed.add_field(name="Created Account Node", value=created_at, inline=True)
        await message.channel.send(embed=embed)
        return

    # --- COMMAND: SETBIO ---
    if command_lower.startswith("setbio"):
        bio_text = command_body[6:].strip()
        if not bio_text:
            await message.channel.send(f"Error: Provide bio text. Usage: `{COMMAND_PREFIX}setbio <text>`")
            return
        if len(bio_text) > 200:
            await message.channel.send("Error: Bio must be 200 characters or fewer.")
            return
        set_bio(message.author.id, bio_text)
        await message.channel.send("✅ Your bio has been updated. Check it with `v!profile`.")
        return

    # --- COMMAND: PROFILE ---
    if command_lower.startswith("profile"):
        target = message.mentions[0] if message.mentions else message.author
        bio = get_bio(target.id)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (target.id,))
        warn_count = cursor.fetchone()[0]
        conn.close()

        embed = discord.Embed(title=f"{target.name}'s Profile", color=target.color)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Bio", value=bio or "*No bio set. Use `v!setbio <text>` to add one.*", inline=False)
        embed.add_field(name="Account Created", value=target.created_at.strftime('%Y-%m-%d'), inline=True)
        if target.joined_at:
            embed.add_field(name="Joined Server", value=target.joined_at.strftime('%Y-%m-%d'), inline=True)
        embed.add_field(name="Warnings", value=str(warn_count), inline=True)
        await message.channel.send(embed=embed)
        return

    if command_lower == "roll":
        await message.channel.send(f"🎲 **Roll:** {random.randint(1, 100)}")
        return

    if command_lower == "coin":
        await message.channel.send(f"🪙 **Coin Flip:** {random.choice(['HEADS', 'TAILS'])}")
        return

    if command_lower == "joke":
        jokes = [
            "I told my wife she was drawing her eyebrows too high. She looked surprised.",
            "Why don't skeletons fight each other? They don't have the guts.",
            "I used to be a banker, but I lost interest.",
            "What do you call a fish with no eyes? A fsh.",
            "I'm reading a book about anti-gravity. It's impossible to put down.",
            "Why don't scientists trust atoms? Because they make up everything.",
            "I only know 25 letters of the alphabet. I don't know why.",
            "What do you call a bear with no teeth? A gummy bear.",
            "I invented a new word: Plagiarism.",
            "Why did the scarecrow win an award? Because he was outstanding in his field.",
            "My dog used to chase people on a bike a lot. It got so bad, we had to take the bike away.",
            "I told my doctor I broke my arm in two places. He told me to stop going to those places.",
        ]
        await message.channel.send(f"😂 {random.choice(jokes)}")
        return

    if command_lower.startswith("8ball"):
        question = command_body[5:].strip()
        if not question:
            await message.channel.send(f"Error: Ask a question. Usage: `{COMMAND_PREFIX}8ball <question>`")
            return
        answers = [
            "It is certain.", "Without a doubt.", "Yes, definitely.", "You may rely on it.",
            "As I see it, yes.", "Most likely.", "Outlook good.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
            "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.",
            "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."
        ]
        embed = discord.Embed(title="🎱 Magic 8-Ball", color=9807270)
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=random.choice(answers), inline=False)
        await message.channel.send(embed=embed)
        return

    if command_lower == "uptime":
        delta = datetime.datetime.now() - BOT_START_TIME
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)
        await message.channel.send(f"⏳ **Uptime:** {days}d {hours}h {minutes}m {seconds}s")
        return

    # --- COMMANDS MATRIX INTERFACE ---
    if command_lower == "commands":
        embed1 = discord.Embed(title="📋 Basic Commands (Everyone)", color=3447003)
        embed1.add_field(name="v!ping", value="Bot latency.", inline=True)
        embed1.add_field(name="v!roll", value="Random 1-100.", inline=True)
        embed1.add_field(name="v!coin", value="Flip a coin.", inline=True)
        embed1.add_field(name="v!8ball <question>", value="Ask the magic 8-ball.", inline=True)
        embed1.add_field(name="v!math <expression>", value="Evaluate math.", inline=True)
        embed1.add_field(name="v!randomcolor", value="Random color + hex/RGB.", inline=True)
        embed1.add_field(name="v!avatar [@user]", value="Show an avatar.", inline=True)
        embed1.add_field(name="v!userinfo [@user]", value="Account info.", inline=True)
        embed1.add_field(name="v!serverinfo", value="Server info.", inline=True)
        embed1.add_field(name="v!whois <Discord ID>", value="Look up a profile by ID.", inline=True)
        embed1.add_field(name="v!afk [reason]", value="Set AFK status.", inline=True)
        embed1.add_field(name="v!remindme [every] [dm] <time> <note>", value="Set a one-time or recurring reminder.", inline=True)
        embed1.add_field(name="v!remindme stop", value="Cancel your recurring reminders.", inline=True)
        embed1.add_field(name="v!joke", value="Random joke.", inline=True)
        embed1.add_field(name="v!profile [@user]", value="Show a profile card.", inline=True)
        embed1.add_field(name="v!setbio <text>", value="Set your profile bio.", inline=True)
        embed1.add_field(name="v!reaction <emoji>", value="React to a replied message.", inline=True)
        embed1.add_field(name="v!dm [text]", value="DM yourself.", inline=True)
        embed1.add_field(name="v!dm @user [text]", value="DM a mentioned user.", inline=True)
        embed1.add_field(name="v!uptime", value="Bot uptime.", inline=True)
        embed1.add_field(name="v!info", value="Bot diagnostics.", inline=True)
        embed1.add_field(name="v!commands", value="This command list.", inline=True)
        embed1.set_footer(text="Page 1/2")

        embed2 = discord.Embed(title="🛡️ Admin Commands (Administrator only)", color=15158332)
        embed2.add_field(name="v!say <text>", value="Delete & repeat as bot.", inline=True)
        embed2.add_field(name="v!purge <number>", value="Delete messages.", inline=True)
        embed2.add_field(name="v!slowmode <seconds>", value="Set slowmode.", inline=True)
        embed2.add_field(name="v!lockdown", value="Toggle channel lockdown.", inline=True)
        embed2.add_field(name="v!botlock [channel/ID]", value="Blacklist a channel/category.", inline=True)
        embed2.add_field(name="v!botisolate [channel/ID]", value="Whitelist a channel/category.", inline=True)
        embed2.add_field(name="v!mute @user <time> [reason]", value="Timeout a user.", inline=True)
        embed2.add_field(name="v!kick @user [reason]", value="Kick a user.", inline=True)
        embed2.add_field(name="v!ban @user [reason]", value="Permanently ban a user.", inline=True)
        embed2.add_field(name="v!tempban @user <time> [reason]", value="Temporary ban.", inline=True)
        embed2.add_field(name="v!unban <user ID>", value="Unban a user.", inline=True)
        embed2.add_field(name="v!warn @user [reason]", value="Add a warning.", inline=True)
        embed2.add_field(name="v!warns @user", value="Show warning history.", inline=True)
        embed2.add_field(name="v!delwarn @user #<case>", value="Delete a warning.", inline=True)
        embed2.add_field(name="v!unwarnall @user", value="Clear all warnings.", inline=True)
        embed2.add_field(name="v!dm @everyone [text]", value="Broadcast DM (confirm req.).", inline=True)
        embed2.add_field(name="v!botavatar <url>", value="Change bot avatar.", inline=True)
        embed2.add_field(name="v!botname <nickname>", value="Change bot nickname.", inline=True)
        embed2.add_field(name="v!prefix [new_prefix]", value="View or change this server's prefix.", inline=True)
        embed2.set_footer(text="Page 2/2")

        embeds_list = [embed1, embed2]
        pagination_view = CommandPaginationView(author=message.author, embeds=embeds_list)
        sent_message = await message.channel.send(embed=embeds_list[0], view=pagination_view)
        pagination_view.message = sent_message
        return

if BOT_TOKEN:
    client.run(BOT_TOKEN)