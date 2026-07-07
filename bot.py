import discord
from discord import app_commands
import random
import datetime
import asyncio
import re
import sqlite3
import os

# Safe reading of the token from environment variables
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = "v!"

if not BOT_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN variable is missing in environment settings!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True 
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

BOT_START_TIME = datetime.datetime.now()

# Tracking Cooldowns & Caches
rep_cooldowns = {}
help_cooldowns = {}
sniped_messages = {} # {channel_id: (content, author, timestamp)}
spam_tracker = {} # {(guild_id, user_id): [timestamps]}
active_mass_reactions = {} # {channel_id: [emoji_string_1, ...]}
REP_COOLDOWN_SECONDS = 3600  
HELP_COOLDOWN_SECONDS = 300  

# --- DATABASE SETUP ---
DB_PATH = "database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, reason TEXT, enforcer TEXT, timestamp TEXT, guild_id INTEGER NOT NULL DEFAULT 0)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS afk (user_id INTEGER PRIMARY KEY, reason TEXT, timestamp TEXT)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS system_settings (guild_id INTEGER, vector_id INTEGER, type TEXT, PRIMARY KEY (guild_id, vector_id, type))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS mention_strikes (guild_id INTEGER NOT NULL DEFAULT 0, user_id INTEGER NOT NULL, strike_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS spam_strikes (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, strike_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS profiles (user_id INTEGER PRIMARY KEY, bio TEXT)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS guild_prefixes (guild_id INTEGER PRIMARY KEY, prefix TEXT)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS reputation (guild_id INTEGER NOT NULL DEFAULT 0, user_id INTEGER NOT NULL, points INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS afk_users (user_id TEXT PRIMARY KEY, reason TEXT, timestamp INTEGER)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS restricted_commands (guild_id INTEGER, command_name TEXT, PRIMARY KEY (guild_id, command_name))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS banping_roles (guild_id INTEGER, role_id INTEGER, PRIMARY KEY (guild_id, role_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS banping_strikes (guild_id INTEGER, user_id INTEGER, strike_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS rep_cooldowns (guild_id INTEGER, giver_id INTEGER, receiver_id INTEGER, last_given INTEGER, PRIMARY KEY (guild_id, giver_id, receiver_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS blacklisted_words (guild_id INTEGER, word TEXT, PRIMARY KEY (guild_id, word))""")
    conn.commit()
    conn.close()

init_db()

# --- DATABASE HELPER FUNCTIONS ---
def get_lock_and_isolate(guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT vector_id FROM system_settings WHERE guild_id = ? AND type = 'lock'", (guild_id,))
    locked = set(row[0] for row in cursor.fetchall())
    cursor.execute("SELECT vector_id FROM system_settings WHERE guild_id = ? AND type = 'isolate'", (guild_id,))
    isolated = set(row[0] for row in cursor.fetchall())
    conn.close()
    return locked, isolated

def toggle_setting(guild_id, vector_id, setting_type):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM system_settings WHERE guild_id = ? AND vector_id = ? AND type = ?", (guild_id, vector_id, setting_type))
    if cursor.fetchone():
        cursor.execute("DELETE FROM system_settings WHERE guild_id = ? AND vector_id = ? AND type = ?", (guild_id, vector_id, setting_type))
        removed = True
    else:
        cursor.execute("INSERT INTO system_settings (guild_id, vector_id, type) VALUES (?, ?, ?)", (guild_id, vector_id, setting_type))
        removed = False
    conn.commit(); conn.close()
    return removed

def get_and_increment_strike(guild_id, user_id, table="mention_strikes"):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute(f"SELECT strike_count FROM {table} WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    row = cursor.fetchone()
    new_strike = (row[0] + 1) if row else 1
    cursor.execute(f"INSERT INTO {table} (guild_id, user_id, strike_count) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET strike_count = ?", (guild_id, user_id, new_strike, new_strike))
    conn.commit(); conn.close()
    return new_strike

def get_guild_prefix(guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT prefix FROM guild_prefixes WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else COMMAND_PREFIX

def set_guild_prefix(guild_id, prefix):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO guild_prefixes (guild_id, prefix) VALUES (?, ?)", (guild_id, prefix))
    conn.commit(); conn.close()

def get_bio(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT bio FROM profiles WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_bio(user_id, bio):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO profiles (user_id, bio) VALUES (?, ?)", (user_id, bio))
    conn.commit(); conn.close()

def get_rep_cooldown_remaining(guild_id, giver_id, receiver_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT last_given FROM rep_cooldowns WHERE guild_id = ? AND giver_id = ? AND receiver_id = ?", (guild_id, giver_id, receiver_id))
    row = cursor.fetchone()
    conn.close()
    if not row: return 0
    return max(0, REP_COOLDOWN_SECONDS - (datetime.datetime.now().timestamp() - row[0]))

def set_rep_cooldown(guild_id, giver_id, receiver_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    now_ts = int(datetime.datetime.now().timestamp())
    cursor.execute("INSERT INTO rep_cooldowns (guild_id, giver_id, receiver_id, last_given) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, giver_id, receiver_id) DO UPDATE SET last_given = ?", (guild_id, giver_id, receiver_id, now_ts, now_ts))
    conn.commit(); conn.close()

def add_rep(user_id, delta, guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("INSERT INTO reputation (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (guild_id, user_id, delta, delta))
    conn.commit()
    cursor.execute("SELECT points FROM reputation WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    new_total = cursor.fetchone()[0]
    conn.close()
    return new_total

def get_rep(user_id, guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT points FROM reputation WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def get_rep_leaderboard(guild_id, limit=10):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT user_id, points FROM reputation WHERE guild_id = ? ORDER BY points DESC LIMIT ?", (guild_id, limit))
    rows = cursor.fetchall(); conn.close()
    return rows

def get_blacklisted_words(guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT word FROM blacklisted_words WHERE guild_id = ?", (guild_id,))
    words = [row[0] for row in cursor.fetchall()]; conn.close()
    return words

def add_blacklisted_word(guild_id, word):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO blacklisted_words (guild_id, word) VALUES (?, ?)", (guild_id, word.lower()))
        conn.commit(); res = True
    except sqlite3.IntegrityError: res = False
    conn.close()
    return res

def remove_blacklisted_word(guild_id, word):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklisted_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower()))
    rows = cursor.rowcount
    conn.commit(); conn.close()
    return rows > 0

def set_afk_user(user_id, reason, timestamp):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO afk_users (user_id, reason, timestamp) VALUES (?, ?, ?)", (str(user_id), reason, timestamp))
    conn.commit(); conn.close()

def get_afk_user(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT reason, timestamp FROM afk_users WHERE user_id = ?", (str(user_id),))
    row = cursor.fetchone(); conn.close()
    return row

def remove_afk_user(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("DELETE FROM afk_users WHERE user_id = ?", (str(user_id),))
    conn.commit(); conn.close()

def get_restricted_commands(guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT command_name FROM restricted_commands WHERE guild_id = ?", (guild_id,))
    rows = cursor.fetchall(); conn.close()
    return {row[0] for row in rows}

def toggle_banping_role(guild_id, role_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banping_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))
    if cursor.fetchone():
        cursor.execute("DELETE FROM banping_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)); added = False
    else:
        cursor.execute("INSERT INTO banping_roles (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id)); added = True
    conn.commit(); conn.close()
    return added

def get_banping_roles(guild_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT role_id FROM banping_roles WHERE guild_id = ?", (guild_id,))
    rows = cursor.fetchall(); conn.close()
    return {row[0] for row in rows}

def parse_duration(time_str: str) -> int:
    match = re.match(r"^(\d+)([smhd])$", time_str.lower())
    if not match: return 0
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "s": return amount
    if unit == "m": return amount * 60
    if unit == "h": return amount * 3600
    if unit == "d": return amount * 86400
    return 0

# --- INTERACTIVE PAGINATION VIEW ---
class CommandPaginationView(discord.ui.View):
    def __init__(self, author, embeds):
        super().__init__(timeout=120.0)
        self.author = author
        self.embeds = embeds
        self.current_page = 0
        self.children[0].disabled = True
        self.children[1].disabled = len(embeds) <= 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Left", style=discord.ButtonStyle.primary, custom_id="btn_prev")
    async def left_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = False
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Right ▶", style=discord.ButtonStyle.primary, custom_id="btn_next")
    async def right_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.children[0].disabled = False
        self.children[1].disabled = self.current_page == len(self.embeds) - 1
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        for child in self.children: child.disabled = True
        try:
            if hasattr(self, "message"): await self.message.edit(view=self)
        except Exception: pass

def get_help_pages(guild_id, guild_prefix):
    restricted = get_restricted_commands(guild_id)
    def lk(cmd, label): return f"🔒 {label}" if cmd in restricted else label

    embed1 = discord.Embed(title="📋 Basic Commands", color=3447003)
    embed1.add_field(name=lk("ping", f"{guild_prefix}ping"), value="Bot latency.", inline=True)
    embed1.add_field(name=lk("info", f"{guild_prefix}info"), value="Bot stats.", inline=True)
    embed1.add_field(name=lk("roll", f"{guild_prefix}roll [n1] [n2]"), value="Random number.", inline=True)
    embed1.add_field(name=lk("coin", f"{guild_prefix}coin"), value="Flip a coin.", inline=True)
    embed1.add_field(name=lk("8ball", f"{guild_prefix}8ball <q>"), value="Ask 8-ball.", inline=True)
    embed1.add_field(name=lk("shipchance", f"{guild_prefix}shipchance @u1 @u2"), value="Love % calc.", inline=True)
    embed1.add_field(name=lk("snipe", f"{guild_prefix}snipe"), value="Recover msg.", inline=True)
    embed1.add_field(name=lk("mc", f"{guild_prefix}mc"), value="Member count.", inline=True)
    embed1.add_field(name=lk("poll", f"{guild_prefix}poll <q> | <opt>"), value="Create poll.", inline=True)
    embed1.set_footer(text="Page 1/4")

    embed1b = discord.Embed(title="📋 Info & Profiles", color=3447003)
    embed1b.add_field(name=lk("avatar", f"{guild_prefix}avatar [@u]"), value="User avatar.", inline=True)
    embed1b.add_field(name=lk("userinfo", f"{guild_prefix}userinfo [@u]"), value="User stats.", inline=True)
    embed1b.add_field(name=lk("serverinfo", f"{guild_prefix}serverinfo"), value="Detailed server info.", inline=True)
    embed1b.add_field(name=lk("roleinfo", f"{guild_prefix}roleinfo @r"), value="Role details.", inline=True)
    embed1b.add_field(name=lk("profile", f"{guild_prefix}profile [@u]"), value="Rep/Bio card.", inline=True)
    embed1b.add_field(name=lk("setbio", f"{guild_prefix}setbio <txt>"), value="Edit bio.", inline=True)
    embed1b.add_field(name=lk("rep", f"{guild_prefix}rep @u 1/-1"), value="Give reputation.", inline=True)
    embed1b.add_field(name=lk("repleaderboard", f"{guild_prefix}repleaderboard"), value="Top 10 rep.", inline=True)
    embed1b.add_field(name=lk("afk", f"{guild_prefix}afk [reason]"), value="Set AFK status.", inline=True)
    embed1b.set_footer(text="Page 2/4")

    embed2 = discord.Embed(title="🛡️ Moderation Commands", color=15158332)
    embed2.add_field(name=f"{guild_prefix}say <txt>", value="[Manage Msgs]", inline=True)
    embed2.add_field(name=f"{guild_prefix}purge <#>", value="[Manage Msgs]", inline=True)
    embed2.add_field(name=f"{guild_prefix}mute @u <t>", value="[Timeout Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}unmute @u", value="[Timeout Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}kick @u", value="[Kick Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}ban @u", value="[Ban Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}unban <id>", value="[Ban Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}warn @u", value="[Timeout Perm]", inline=True)
    embed2.add_field(name=f"{guild_prefix}delwarn @u #", value="Delete warning.", inline=True)
    embed2.add_field(name=f"{guild_prefix}delwarnsall @u", value="Clear warnings.", inline=True)
    embed2.set_footer(text="Page 3/4")

    embed2b = discord.Embed(title="⚙️ Settings (Admin Only)", color=15158332)
    embed2b.add_field(name=f"{guild_prefix}addrole @u @r", value="[Manage Roles]", inline=True)
    embed2b.add_field(name=f"{guild_prefix}blacklistword <w>", value="[Manage Msgs]", inline=True)
    embed2b.add_field(name=f"{guild_prefix}blacklistedwords", value="[Manage Msgs]", inline=True)
    embed2b.add_field(name=f"{guild_prefix}massreaction #ch <e>", value="[Manage Msgs]", inline=True)
    embed2b.add_field(name=f"{guild_prefix}disablereactions", value="[Manage Msgs]", inline=True)
    embed2b.add_field(name=f"{guild_prefix}lockdown", value="Lock channel.", inline=True)
    embed2b.add_field(name=f"{guild_prefix}botlock [ID]", value="Blacklist channel.", inline=True)
    embed2b.add_field(name=f"{guild_prefix}botisolate [ID]", value="Whitelist channel.", inline=True)
    embed2b.add_field(name=f"{guild_prefix}restrict <cmd>", value="Lock basic cmds.", inline=True)
    embed2b.add_field(name=f"{guild_prefix}banping @role", value="Auto-mute ping.", inline=True)
    embed2b.add_field(name=f"{guild_prefix}prefix [new]", value="Change prefix.", inline=True)
    embed2b.set_footer(text="Page 4/4")

    return [embed1, embed1b, embed2, embed2b]

@client.event
async def on_ready():
    print(f"System Matrix Live: Logged in as {client.user} (SQLite Database Connected)")
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

# =========================================================================
#    SLASH COMMANDS - BASIC / UTILITY (EVERYONE)
# =========================================================================
@tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=discord.Embed(description=f"🏓 **Pong!** Latency: {round(client.latency * 1000)}ms", color=0x3498DB))

@tree.command(name="info", description="Show bot information and statistics")
async def slash_info(interaction: discord.Interaction):
    servers = len(client.guilds)
    members = sum(g.member_count for g in client.guilds)
    uptime = str(datetime.datetime.now() - BOT_START_TIME).split('.')[0]
    embed = discord.Embed(title="V!tya - Bot information", description="Multi-use discord bot for different tasks, created by <@1155808109219020800>\nPrefixes: `v!` and `/`", color=0x90EE90)
    embed.set_thumbnail(url=client.user.display_avatar.url)
    embed.add_field(name="🌐 Servers", value=str(servers), inline=True)
    embed.add_field(name="👥 Members", value=str(members), inline=True)
    embed.add_field(name="⏱️ Uptime", value=uptime, inline=True)
    embed.add_field(name="🏓 Ping", value=f"{round(client.latency * 1000)}ms", inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="help", description="Show the command list")
async def slash_help(interaction: discord.Interaction):
    now_ts = datetime.datetime.now().timestamp()
    cooldown_key = (interaction.guild_id, interaction.user.id)
    if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
        mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
        return await interaction.response.send_message(embed=discord.Embed(description=f"⏳ You are on cooldown! Please wait **{mins}m {secs}s**.", color=0xE74C3C), ephemeral=True)
    help_cooldowns[cooldown_key] = now_ts
    prefix = get_guild_prefix(interaction.guild_id) if interaction.guild_id else "v!"
    pages = get_help_pages(interaction.guild_id, prefix)
    view = CommandPaginationView(interaction.user, pages)
    await interaction.response.send_message(embed=pages[0], view=view)
    view.message = await interaction.original_response()

@tree.command(name="avatar", description="View a user's avatar")
async def slash_avatar(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    embed = discord.Embed(title=f"{target.name}'s Avatar", color=0x3498DB)
    embed.set_image(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="8ball", description="Ask the Magic 8-Ball a question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    pos = ["It is certain.", "Yes, definitely.", "You may rely on it.", "Outlook good.", "Signs point to yes."]
    neu = ["Reply hazy, try again.", "Ask again later.", "Better not tell you now."]
    neg = ["Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
    answer = random.choice(pos + neu + neg)
    color = discord.Color.green() if answer in pos else (discord.Color.red() if answer in neg else discord.Color.gold())
    embed = discord.Embed(title="🎱 The Magic 8-Ball", color=color)
    embed.add_field(name="Question", value=f"*{question}*", inline=False)
    embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="roll", description="Roll a random number")
async def slash_roll(interaction: discord.Interaction, min_val: int = 1, max_val: int = 100):
    await interaction.response.send_message(embed=discord.Embed(description=f"🎲 **{interaction.user.display_name}** rolled ({min_val}-{max_val}): **{random.randint(min(min_val, max_val), max(min_val, max_val))}**", color=0x9B59B6))

@tree.command(name="coin", description="Flip a coin")
async def slash_coin(interaction: discord.Interaction):
    await interaction.response.send_message(embed=discord.Embed(description=f"🪙 **Coin Flip:** {random.choice(['HEADS', 'TAILS'])}", color=0xF1C40F))

@tree.command(name="shipchance", description="Check the love chance between two users")
async def slash_shipchance(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
    chance = random.randint(0, 100)
    await interaction.response.send_message(embed=discord.Embed(description=f"💖 The chance of {user1.mention} and {user2.mention} loving each other is: **{chance}%**", color=0xE74C3C))

@tree.command(name="profile", description="View a user's profile")
async def slash_profile(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    bio = get_bio(target.id)
    embed = discord.Embed(title=f"{target.name}'s Profile", color=target.color)
    if target.display_avatar: embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Bio", value=bio or f"*No bio set. Type `/setbio` to set bio.*", inline=False)
    embed.add_field(name="Reputation", value=f"✨ {get_rep(target.id, interaction.guild_id)} Points", inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="setbio", description="Set your profile bio")
async def slash_setbio(interaction: discord.Interaction, bio: str):
    if len(bio) > 200: return await interaction.response.send_message("❌ Bio must be 200 characters or fewer.", ephemeral=True)
    set_bio(interaction.user.id, bio)
    await interaction.response.send_message(embed=discord.Embed(description="✅ Your bio has been updated.", color=0x2ECC71))

@tree.command(name="rep", description="Give or remove reputation from a user")
@app_commands.choices(action=[app_commands.Choice(name="Add (+1)", value=1), app_commands.Choice(name="Remove (-1)", value=-1)])
async def slash_rep(interaction: discord.Interaction, user: discord.Member, action: int):
    if user.id == interaction.user.id or user.bot: return await interaction.response.send_message("❌ Invalid target.", ephemeral=True)
    remaining = get_rep_cooldown_remaining(interaction.guild_id, interaction.user.id, user.id)
    if remaining > 0: return await interaction.response.send_message(f"⏳ Please wait {max(1, round(remaining/60))} minutes.", ephemeral=True)
    new_total = add_rep(user.id, action, interaction.guild_id)
    set_rep_cooldown(interaction.guild_id, interaction.user.id, user.id)
    await interaction.response.send_message(embed=discord.Embed(description=f"{'+1 ✨' if action == 1 else '-1 📉'} **{user.display_name}**'s rep is now **{new_total}**.", color=0x2ECC71 if action == 1 else 0xE74C3C))

@tree.command(name="repleaderboard", description="View the top 10 reputation leaderboard")
async def slash_repleaderboard(interaction: discord.Interaction):
    rows = get_rep_leaderboard(interaction.guild_id, 10)
    embed = discord.Embed(title="🏆 Reputation Leaderboard", color=0xF1C40F)
    if not rows: embed.description = "No reputation data yet."
    else: embed.description = "\n".join([f"{'🥇' if i==1 else '🥈' if i==2 else '🥉' if i==3 else f'**#{i}**'} <@{u}> — **{p} rep**" for i, (u, p) in enumerate(rows, 1)])
    await interaction.response.send_message(embed=embed)

@tree.command(name="snipe", description="Recover the last deleted message")
async def slash_snipe(interaction: discord.Interaction):
    if interaction.channel_id in sniped_messages:
        content, author, timestamp = sniped_messages[interaction.channel_id]
        embed = discord.Embed(description=content, color=0x3498DB, timestamp=timestamp)
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        embed.set_footer(text="Sniped message")
        await interaction.response.send_message(embed=embed)
    else: await interaction.response.send_message("❌ Nothing to snipe.", ephemeral=True)

@tree.command(name="serverinfo", description="View server statistics")
async def slash_serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    humans = len([m for m in guild.members if not m.bot])
    embed = discord.Embed(title=f"Server Info: {guild.name}", color=0x3498DB)
    if guild.icon: embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👑 Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}", inline=True)
    embed.add_field(name="📅 Created", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
    embed.add_field(name="🚀 Boosts", value=f"Tier {guild.premium_tier} ({guild.premium_subscription_count} Boosts)", inline=True)
    embed.add_field(name=f"👥 Members ({guild.member_count})", value=f"🧑 Humans: {humans}\n🤖 Bots: {guild.member_count - humans}", inline=True)
    embed.add_field(name=f"📺 Channels ({len(guild.text_channels) + len(guild.voice_channels)})", value=f"💬 Text: {len(guild.text_channels)}\n🔊 Voice: {len(guild.voice_channels)}", inline=True)
    embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="userinfo", description="View user details")
async def slash_userinfo(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    embed = discord.Embed(title=f"User Info: {target.name}", color=target.color)
    if target.display_avatar: embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="ID", value=f"`{target.id}`", inline=False)
    embed.add_field(name="Joined Server", value=f"<t:{int(target.joined_at.timestamp())}:D>" if target.joined_at else "Unknown", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{int(target.created_at.timestamp())}:D>", inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="roleinfo", description="View role details")
async def slash_roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
    embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
    embed.add_field(name="Color Hex", value=f"`{str(role.color)}`", inline=True)
    embed.add_field(name="Members", value=f"{len(role.members)} users", inline=True)
    embed.add_field(name="Created", value=f"<t:{int(role.created_at.timestamp())}:D>", inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="membercount", description="View member count")
async def slash_membercount(interaction: discord.Interaction):
    humans = sum(1 for m in interaction.guild.members if not m.bot)
    embed = discord.Embed(title=f"📊 Member Count: {interaction.guild.name}", color=0x2ECC71)
    embed.add_field(name="Total Members", value=f"**{interaction.guild.member_count}**", inline=False)
    embed.add_field(name="🧑 Humans", value=str(humans), inline=True)
    embed.add_field(name="🤖 Bots", value=str(interaction.guild.member_count - humans), inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="chance", description="Calculate a random percentage")
async def slash_chance(interaction: discord.Interaction, question: str):
    await interaction.response.send_message(embed=discord.Embed(description=f"🎲 The chance of **{question}** is: **{random.randint(0, 100)}%**", color=0x3498DB))

@tree.command(name="math", description="Solve a math expression")
async def slash_math(interaction: discord.Interaction, expression: str):
    clean_expr = re.sub(r"[^0-9\+\-\*\/\(\)\.\s]", "", expression)
    try: await interaction.response.send_message(embed=discord.Embed(description=f"📊 `{expression}` = **{eval(clean_expr, {'__builtins__': None}, {})}**", color=0x2ECC71))
    except: await interaction.response.send_message("❌ Invalid math expression.", ephemeral=True)

@tree.command(name="afk", description="Set your AFK status")
async def slash_afk(interaction: discord.Interaction, reason: str = "AFK"):
    set_afk_user(interaction.user.id, reason, int(datetime.datetime.now().timestamp()))
    try: await interaction.user.edit(nick=f"[AFK] {interaction.user.display_name[:26]}")
    except: pass
    await interaction.response.send_message(embed=discord.Embed(description=f"💤 You are now AFK: {reason}", color=0x95A5A6))

@tree.command(name="poll", description="Create a poll")
async def slash_poll(interaction: discord.Interaction, question: str, options: str = None):
    if not options:
        msg = await interaction.channel.send(embed=discord.Embed(title="📊 Poll", description=f"**{question}**", color=0x3498DB))
        await msg.add_reaction("👍"); await msg.add_reaction("👎")
        await interaction.response.send_message("Poll created!", ephemeral=True)
    else:
        opts = [o.strip() for o in options.split("|")][:9]
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        desc = f"**{question}**\n\n" + "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(opts))
        msg = await interaction.channel.send(embed=discord.Embed(title="📊 Poll", description=desc, color=0x3498DB))
        for i in range(len(opts)): await msg.add_reaction(emojis[i])
        await interaction.response.send_message("Poll created!", ephemeral=True)

# --- SLASH COMMANDS - MODERATION (HIDDEN BY DEFAULT) ---
@tree.command(name="say", description="Make the bot say something")
@app_commands.default_permissions(manage_messages=True)
async def slash_say(interaction: discord.Interaction, text: str):
    await interaction.channel.send(text)
    await interaction.response.send_message("Sent!", ephemeral=True)

@tree.command(name="purge", description="Delete multiple messages")
@app_commands.default_permissions(manage_messages=True)
async def slash_purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=min(amount, 100))
        await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.")
    except: await interaction.followup.send("❌ I don't have permission to delete messages here.")

@tree.command(name="addrole", description="Assign a role to a user")
@app_commands.default_permissions(manage_roles=True)
async def slash_addrole(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    try:
        await user.add_roles(role)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Successfully added {role.mention} to {user.mention}.", color=0x2ECC71))
    except discord.Forbidden:
        await interaction.response.send_message(embed=discord.Embed(description="❌ I lack permission to add this role.", color=0xE74C3C), ephemeral=True)

@tree.command(name="mute", description="Mute a user")
@app_commands.default_permissions(moderate_members=True)
async def slash_mute(interaction: discord.Interaction, user: discord.Member, time: str, reason: str = "No reason"):
    if user.id == interaction.user.id: return await interaction.response.send_message("❌ You cannot mute yourself.", ephemeral=True)
    seconds = parse_duration(time)
    if seconds <= 0: return await interaction.response.send_message("❌ Invalid time format.", ephemeral=True)
    try:
        await user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
        await interaction.response.send_message(embed=discord.Embed(description=f"🤫 **{user.name}** has been muted for **{time}**.\n*Reason: {reason}*", color=0xF1C40F))
    except: await interaction.response.send_message("❌ I can't mute that user.", ephemeral=True)

@tree.command(name="unmute", description="Unmute a user")
@app_commands.default_permissions(moderate_members=True)
async def slash_unmute(interaction: discord.Interaction, user: discord.Member):
    try:
        await user.timeout(None)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ **{user.name}** has been unmuted.", color=0x2ECC71))
    except: await interaction.response.send_message("❌ I can't unmute that user.", ephemeral=True)

@tree.command(name="kick", description="Kick a user")
@app_commands.default_permissions(kick_members=True)
async def slash_kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    if user.id == interaction.user.id: return await interaction.response.send_message("❌ You cannot kick yourself.", ephemeral=True)
    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(embed=discord.Embed(description=f"👢 **{user.name}** was kicked.\n*Reason: {reason}*", color=0xE67E22))
    except: await interaction.response.send_message("❌ I can't kick that user.", ephemeral=True)

@tree.command(name="ban", description="Ban a user")
@app_commands.default_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    if user.id == interaction.user.id: return await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
    try:
        await interaction.guild.ban(user, reason=reason)
        await interaction.response.send_message(embed=discord.Embed(description=f"⛔ **{user.name}** was permanently banned.\n*Reason: {reason}*", color=0xE74C3C))
    except: await interaction.response.send_message("❌ I can't ban that user.", ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID")
@app_commands.default_permissions(ban_members=True)
async def slash_unban(interaction: discord.Interaction, user_id: str):
    if not user_id.isdigit(): return await interaction.response.send_message("❌ Provide a valid User ID.", ephemeral=True)
    try:
        await interaction.guild.unban(discord.Object(id=int(user_id)))
        await interaction.response.send_message(embed=discord.Embed(description=f"🔓 User `{user_id}` has been unbanned.", color=0x2ECC71))
    except discord.NotFound: await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)
    except: await interaction.response.send_message("❌ I don't have permission to unban.", ephemeral=True)

@tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(moderate_members=True)
async def slash_warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    if user.id == interaction.user.id: return await interaction.response.send_message("❌ You cannot warn yourself.", ephemeral=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("INSERT INTO warnings (guild_id, user_id, reason, enforcer, timestamp) VALUES (?, ?, ?, ?, ?)", (interaction.guild_id, user.id, reason, interaction.user.name, ts))
    cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ? AND guild_id = ?", (user.id, interaction.guild_id))
    total_warns = cursor.fetchone()[0]
    conn.commit(); conn.close()

    embed = discord.Embed(title="⚠️ User Warned", description=f"{user.mention} has received a warning.", color=0xF39C12)
    embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
    embed.add_field(name="Total Warnings", value=f"`{total_warns}`", inline=True)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)

@tree.command(name="warns", description="View a user's warnings")
@app_commands.default_permissions(moderate_members=True)
async def slash_warns(interaction: discord.Interaction, user: discord.Member):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT reason, enforcer, timestamp FROM warnings WHERE user_id = ? AND guild_id = ?", (user.id, interaction.guild_id))
    warns = cursor.fetchall(); conn.close()
    embed = discord.Embed(title=f"⚠️ Warnings for {user.name}", color=0xF39C12)
    if not warns: embed.description = "This user has no warnings."
    else:
        for idx, w in enumerate(warns, 1): embed.add_field(name=f"Warning #{idx}", value=f"**Reason:** {w[0]}\n**By:** {w[1]}\n**Date:** {w[2]}", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="delwarn", description="Delete a specific warning")
@app_commands.default_permissions(moderate_members=True)
async def slash_delwarn(interaction: discord.Interaction, user: discord.Member, case_number: int):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT id FROM warnings WHERE user_id = ? AND guild_id = ?", (user.id, interaction.guild_id))
    user_warns = cursor.fetchall()
    if case_number < 1 or case_number > len(user_warns):
        conn.close(); return await interaction.response.send_message("❌ Invalid warning number.", ephemeral=True)
    db_id = user_warns[case_number - 1][0]
    cursor.execute("DELETE FROM warnings WHERE id = ?", (db_id,))
    conn.commit(); conn.close()
    await interaction.response.send_message(embed=discord.Embed(description=f"🗑️ Warning **#{case_number}** deleted for {user.mention}.", color=0x2ECC71))

@tree.command(name="delwarnsall", description="Clear all warnings for a user")
@app_commands.default_permissions(moderate_members=True)
async def slash_delwarnsall(interaction: discord.Interaction, user: discord.Member):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("DELETE FROM warnings WHERE user_id = ? AND guild_id = ?", (user.id, interaction.guild_id))
    conn.commit(); conn.close()
    await interaction.response.send_message(embed=discord.Embed(description=f"✅ All warnings cleared for {user.mention}.", color=0x2ECC71))

@tree.command(name="blacklistword", description="Add a word to the auto-mod blacklist")
@app_commands.default_permissions(manage_messages=True)
async def slash_blacklistword(interaction: discord.Interaction, word: str):
    if add_blacklisted_word(interaction.guild_id, word):
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ `{word}` added to the blacklist.", color=0x2ECC71))
    else: await interaction.response.send_message(embed=discord.Embed(description=f"❌ `{word}` is already blacklisted.", color=0xE74C3C), ephemeral=True)

@tree.command(name="unblacklistword", description="Remove a word from the auto-mod blacklist")
@app_commands.default_permissions(manage_messages=True)
async def slash_unblacklistword(interaction: discord.Interaction, word: str):
    if remove_blacklisted_word(interaction.guild_id, word):
        await interaction.response.send_message(embed=discord.Embed(description=f"🗑️ `{word}` removed from the blacklist.", color=0x2ECC71))
    else: await interaction.response.send_message(embed=discord.Embed(description=f"❌ `{word}` is not on the blacklist.", color=0xE74C3C), ephemeral=True)

@tree.command(name="blacklistedwords", description="View all auto-mod blacklisted words")
@app_commands.default_permissions(manage_messages=True)
async def slash_blacklistedwords(interaction: discord.Interaction):
    words = get_blacklisted_words(interaction.guild_id)
    if not words: return await interaction.response.send_message(embed=discord.Embed(description="No words are currently blacklisted.", color=0x2ECC71))
    embed = discord.Embed(title="🚫 Blacklisted Words", description="```\n" + "\n".join(words) + "\n```", color=0xE74C3C)
    await interaction.response.send_message(embed=embed)

@tree.command(name="massreaction", description="Enable mass reaction for a channel")
@app_commands.default_permissions(manage_messages=True)
async def slash_massreaction(interaction: discord.Interaction, channel: discord.TextChannel, emojis: str):
    emojis_list = [e.strip() for e in emojis.split()]
    if not emojis_list: return await interaction.response.send_message("❌ Provide emojis separated by space.", ephemeral=True)
    active_mass_reactions[channel.id] = emojis_list
    await interaction.response.send_message(embed=discord.Embed(description=f"✅ Mass reaction enabled in {channel.mention} with `{' '.join(emojis_list)}`", color=0x2ECC71))

@tree.command(name="unmassreaction", description="Disable mass reaction for a channel")
@app_commands.default_permissions(manage_messages=True)
async def slash_unmassreaction(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target = channel or interaction.channel
    if target.id in active_mass_reactions:
        del active_mass_reactions[target.id]
        await interaction.response.send_message(embed=discord.Embed(description=f"🛑 Mass reaction disabled for {target.mention}.", color=0xE74C3C))
    else: await interaction.response.send_message(f"❌ Mass reaction is not active in {target.mention}.", ephemeral=True)

@tree.command(name="disablereactions", description="Disable reactions in this channel for @everyone")
@app_commands.default_permissions(manage_messages=True)
async def slash_disablereactions(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.add_reactions = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(embed=discord.Embed(description="🔒 Reactions disabled for `@everyone`.", color=0xE74C3C))

@tree.command(name="enablereactions", description="Enable reactions in this channel for @everyone")
@app_commands.default_permissions(manage_messages=True)
async def slash_enablereactions(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.add_reactions = None 
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(embed=discord.Embed(description="🔓 Reactions enabled for `@everyone`.", color=0x2ECC71))

@tree.command(name="lockdown", description="Lock/Unlock a channel")
@app_commands.default_permissions(manage_channels=True)
async def slash_lockdown(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target = channel or interaction.channel
    overwrite = target.overwrites_for(interaction.guild.default_role)
    if overwrite.send_messages is False:
        overwrite.send_messages = None
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=discord.Embed(description=f"🔓 {target.mention} is now unlocked.", color=0x2ECC71))
    else:
        overwrite.send_messages = False
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(embed=discord.Embed(description=f"🔒 {target.mention} is now locked.", color=0xE74C3C))

@tree.command(name="botlock", description="Toggle bot responding in a channel")
@app_commands.default_permissions(administrator=True)
async def slash_botlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target = channel or interaction.channel
    if toggle_setting(interaction.guild_id, target.id, "lock"): await interaction.response.send_message(embed=discord.Embed(description=f"🔓 **Botlock removed** in {target.mention}.", color=0x2ECC71))
    else: await interaction.response.send_message(embed=discord.Embed(description=f"🔒 **Botlock enabled.** The bot will ignore normal commands in {target.mention}.", color=0xE74C3C))

@tree.command(name="botisolate", description="Isolate the bot to specific channels")
@app_commands.default_permissions(administrator=True)
async def slash_botisolate(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target = channel or interaction.channel
    if toggle_setting(interaction.guild_id, target.id, "isolate"): await interaction.response.send_message(embed=discord.Embed(description=f"🔓 **Isolation removed** for {target.mention}.", color=0x2ECC71))
    else: await interaction.response.send_message(embed=discord.Embed(description=f"🛡️ **Bot isolated.** Commands will ONLY work inside {target.mention}.", color=0x3498DB))

@tree.command(name="restrict", description="Lock a command to Administrators")
@app_commands.default_permissions(administrator=True)
async def slash_restrict(interaction: discord.Interaction, command_name: str):
    cmd = command_name.lower().replace("v!", "")
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    if cmd in get_restricted_commands(interaction.guild_id):
        cursor.execute("DELETE FROM restricted_commands WHERE guild_id = ? AND command_name = ?", (interaction.guild_id, cmd))
        conn.commit(); conn.close()
        await interaction.response.send_message(embed=discord.Embed(description=f"🔓 `{cmd}` is no longer restricted.", color=0x2ECC71))
    else:
        cursor.execute("INSERT INTO restricted_commands (guild_id, command_name) VALUES (?, ?)", (interaction.guild_id, cmd))
        conn.commit(); conn.close()
        await interaction.response.send_message(embed=discord.Embed(description=f"🔒 `{cmd}` is now restricted to Administrators.", color=0xE74C3C))

@tree.command(name="banping", description="Auto-mute users who ping a specific role")
@app_commands.default_permissions(administrator=True)
async def slash_banping(interaction: discord.Interaction, role: discord.Role):
    if toggle_banping_role(interaction.guild_id, role.id): await interaction.response.send_message(embed=discord.Embed(description=f"🛡️ **Banping enabled** for {role.mention}.", color=0xE74C3C))
    else: await interaction.response.send_message(embed=discord.Embed(description=f"🔓 **Banping disabled** for {role.mention}.", color=0x2ECC71))

@tree.command(name="prefix", description="Change the bot's prefix")
@app_commands.default_permissions(administrator=True)
async def slash_prefix(interaction: discord.Interaction, new_prefix: str = None):
    if not new_prefix: return await interaction.response.send_message(embed=discord.Embed(description=f"⚙️ Current prefix: `{get_guild_prefix(interaction.guild_id)}`", color=0x3498DB))
    if len(new_prefix) > 5 or " " in new_prefix: return await interaction.response.send_message("❌ Prefix must be 1-5 chars without spaces.", ephemeral=True)
    set_guild_prefix(interaction.guild_id, new_prefix)
    await interaction.response.send_message(embed=discord.Embed(description=f"✅ Prefix changed to `{new_prefix}`", color=0x2ECC71))

# =========================================================================
#    MESSAGE LISTENER (Prefix Commands & Auto-Mod)
# =========================================================================
@client.event
async def on_message(message):
    if message.author == client.user or message.guild is None:
        return

    # --- MASS REACTION LISTENER ---
    if message.channel.id in active_mass_reactions:
        for emoji in active_mass_reactions[message.channel.id]:
            try: await message.add_reaction(emoji)
            except discord.HTTPException: pass 

    is_admin = message.author.guild_permissions.administrator

    # --- AUTO-MOD: BLACKLIST (Applies to EVERYONE) ---
    bad_words = get_blacklisted_words(message.guild.id)
    msg_lower = message.content.lower()
    for w in bad_words:
        if re.search(r'\b' + re.escape(w) + r'\b', msg_lower):
            try: await message.delete()
            except discord.Forbidden: pass
            embed = discord.Embed(title="🚫 Blacklisted Word Detected", description=f"<@{message.author.id}>, your message contained a blacklisted word and was removed.", color=0xE74C3C)
            try: await message.channel.send(embed=embed, delete_after=10.0)
            except: pass
            return

    # --- SECURITY LAYER: ANTI-SPAM, ANTI-INVITE & ANTI-MENTION (Ignores Admins) ---
    if not is_admin:
        # 1. Anti-Spam
        now = datetime.datetime.now().timestamp()
        user_key = (message.guild.id, message.author.id)
        if user_key not in spam_tracker: spam_tracker[user_key] = []
        spam_tracker[user_key].append(now)
        spam_tracker[user_key] = [t for t in spam_tracker[user_key] if now - t < 5]

        if len(spam_tracker[user_key]) >= 5:
            spam_tracker[user_key] = [] 
            strike = get_and_increment_spam_strike(message.guild.id, message.author.id)
            if strike == 1:
                try: await message.delete()
                except: pass
                await send_embed(message.channel, "⚠️ **No spamming!** Continuing will get you in a 1 minute mute.", color=0xFFFFFF)
                return
            else:
                duration_minutes = 2 ** (strike - 2)
                try:
                    await message.delete()
                    await message.author.timeout(datetime.timedelta(minutes=duration_minutes), reason="Spamming")
                    await send_error_embed(message.channel, f"🔇 <@{message.author.id}> has been muted for **{duration_minutes} minute(s)** for spamming.")
                except: pass
                return

        # 2. Anti-Invite
        invite_pattern = r"(discord\.(gg|io|me|li)\/.+|discord\.com\/invite\/.+)"
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            try:
                await message.delete()
                await message.channel.send(embed=discord.Embed(title="🚨 Invite Link Removed", description=f"<@{message.author.id}> posted a Discord invite link.", color=15158332), delete_after=10.0)
                return
            except discord.Forbidden: pass

        # 3. Mention Spam
        raw_mention_count = len(message.mentions)
        unique_mention_count = len(set(u.id for u in message.mentions))
        if (raw_mention_count >= 3 and unique_mention_count == 1) or unique_mention_count >= 5 or message.mention_everyone:
            strikes = get_and_increment_strike(message.guild.id, message.author.id)
            duration = datetime.timedelta(minutes=2) if strikes == 1 else (datetime.timedelta(minutes=15) if strikes == 2 else datetime.timedelta(hours=1))
            try:
                await message.delete()
                await message.author.timeout(duration, reason="Auto-mod: Mention Spam")
                await send_error_embed(message.channel, f"🔇 <@{message.author.id}> was muted for mention spam (Strike #{strikes}).")
                return
            except discord.Forbidden: pass

        # 4. Banping
        banping_protected = get_banping_roles(message.guild.id)
        if banping_protected and (message.mention_everyone and 0 in banping_protected or any(r.id in banping_protected for r in message.role_mentions)):
            strike = get_and_increment_banping_strike(message.guild.id, message.author.id)
            duration = datetime.timedelta(minutes=min(2 ** (strike - 1), 480))
            try:
                await message.delete()
                await message.author.timeout(duration, reason="Banping violation")
                await send_error_embed(message.channel, f"🔕 <@{message.author.id}> was muted for pinging a protected role (Strike #{strike}).")
                return
            except discord.Forbidden: pass

    # --- AFK & REPUTATION (THANK YOU) KEYWORDS ---
    raw_content = message.content.strip()
    guild_prefix_now = get_guild_prefix(message.guild.id)
    is_afk_command = raw_content.lower().startswith((guild_prefix_now + "afk").lower())

    if not is_afk_command:
        if get_afk_user(message.author.id):
            remove_afk_user(message.author.id)
            if isinstance(message.author, discord.Member) and message.author.display_name.startswith("[AFK] "):
                try: await message.author.edit(nick=message.author.display_name[6:] or None)
                except discord.Forbidden: pass
            await send_embed(message.channel, f"👋 Welcome back, **{message.author.display_name}**! I removed your AFK status.", color=0x2ECC71)

    if message.mentions and not is_afk_command:
        for target in message.mentions:
            target_afk = get_afk_user(target.id)
            if target_afk: await send_embed(message.channel, f"📌 **{target.display_name}** is currently AFK: {target_afk[0]} (<t:{target_afk[1]}:R>)", color=0xF39C12)

    THANK_KEYWORDS = {"ty", "tysm", "thanks", "thank you", "thank u", "thx", "спасибо"}
    if not message.author.bot and any(re.search(r"\b" + re.escape(kw) + r"\b", message.content.lower()) for kw in THANK_KEYWORDS):
        target_user = None
        if message.reference and isinstance(message.reference.resolved, discord.Message) and not message.reference.resolved.author.bot and message.reference.resolved.author != message.author:
            target_user = message.reference.resolved.author
        elif message.mentions:
            for u in message.mentions:
                if u != message.author and not u.bot:
                    target_user = u; break

        if target_user:
            remaining = get_rep_cooldown_remaining(message.guild.id, message.author.id, target_user.id)
            if remaining <= 0:
                new_total = add_rep(target_user.id, 1, message.guild.id)
                set_rep_cooldown(message.guild.id, message.author.id, target_user.id)
                await send_embed(message.channel, f"✨ **{target_user.display_name}**'s reputation increased! (+1 for helping out) — **{new_total} rep total**", color=0xF1C40F)

    # --- PREFIX VALIDATION & COMMAND PARSING ---
    guild_prefix = get_guild_prefix(message.guild.id)
    if not raw_content.lower().startswith(guild_prefix.lower()): return

    command_body = raw_content[len(guild_prefix):].strip()
    command_name = command_body.lower().split()[0] if command_body else ""

    # --- COMMAND ISOLATION (Mods Bypass) ---
    channel_id, category_id = message.channel.id, getattr(message.channel, "category_id", None)
    locked_channels, isolated_vectors = get_lock_and_isolate(message.guild.id)

    if not is_admin:
        if command_name not in ("botlock", "botisolate"):
            if isolated_vectors and (channel_id not in isolated_vectors) and (category_id not in isolated_vectors): return
            if channel_id in locked_channels or (category_id and category_id in locked_channels): return
        if command_name in get_restricted_commands(message.guild.id):
            return await send_error_embed(message.channel, f"🔒 The `{guild_prefix}{command_name}` command is restricted to administrators.")

    # =========================================================================
    #    PREFIX COMMANDS ROUTING
    # =========================================================================
    if command_name == "blacklistword":
        if not await check_perms(message, manage_messages=True): return
        args = command_body.split(maxsplit=1)
        if len(args) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}blacklistword <word>`")
        word = args[1].lower()
        if add_blacklisted_word(message.guild.id, word): return await send_embed(message.channel, f"✅ `{word}` added to the blacklist.", color=0x2ECC71)
        else: return await send_error_embed(message.channel, f"❌ `{word}` is already blacklisted.")

    if command_name == "unblacklistword":
        if not await check_perms(message, manage_messages=True): return
        args = command_body.split(maxsplit=1)
        if len(args) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unblacklistword <word>`")
        word = args[1].lower()
        if remove_blacklisted_word(message.guild.id, word): return await send_embed(message.channel, f"🗑️ `{word}` removed from the blacklist.", color=0x2ECC71)
        else: return await send_error_embed(message.channel, f"❌ `{word}` is not on the blacklist.")

    if command_name == "blacklistedwords":
        if not await check_perms(message, manage_messages=True): return
        words = get_blacklisted_words(message.guild.id)
        if not words: return await send_error_embed(message.channel, "No words are currently blacklisted.")
        embed = discord.Embed(title="🚫 Blacklisted Words", description="```\n" + "\n".join(words) + "\n```", color=0xE74C3C)
        return await message.channel.send(embed=embed)

    if command_name == "lockdown":
        if not await check_perms(message, manage_channels=True): return
        target_channel = message.channel_mentions[0] if message.channel_mentions else message.channel
        overwrite = target_channel.overwrites_for(message.guild.default_role)
        if overwrite.send_messages is False:
            overwrite.send_messages = None
            await target_channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, f"🔓 {target_channel.mention} is now unlocked.", color=0x2ECC71)
        else:
            overwrite.send_messages = False
            await target_channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, f"🔒 {target_channel.mention} is now locked. Only admins/mods can speak.", color=0xE74C3C)

    if command_name == "botlock":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id)
        if toggle_setting(message.guild.id, target_id, "lock"): return await send_embed(message.channel, f"🔓 **Botlock removed.** The bot will respond in <#{target_id}> again.", color=0x2ECC71)
        else: return await send_embed(message.channel, f"🔒 **Botlock enabled.** The bot will ignore normal commands in <#{target_id}>.", color=0xE74C3C)

    if command_name == "botisolate":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id)
        if toggle_setting(message.guild.id, target_id, "isolate"): return await send_embed(message.channel, f"🔓 **Isolation removed.** <#{target_id}> is no longer whitelisted.", color=0x2ECC71)
        else: return await send_embed(message.channel, f"🛡️ **Bot isolated.** Normal commands will ONLY work inside <#{target_id}>.", color=0x3498DB)

    if command_name == "restrict":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        if len(args) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}restrict <command>`")
        cmd = args[1].lower()
        if cmd in get_restricted_commands(message.guild.id):
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("DELETE FROM restricted_commands WHERE guild_id = ? AND command_name = ?", (message.guild.id, cmd))
            conn.commit(); conn.close()
            return await send_embed(message.channel, f"🔓 `{cmd}` is no longer restricted.", color=0x2ECC71)
        else:
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("INSERT INTO restricted_commands (guild_id, command_name) VALUES (?, ?)", (message.guild.id, cmd))
            conn.commit(); conn.close()
            return await send_embed(message.channel, f"🔒 `{cmd}` is now restricted to Administrators.", color=0xE74C3C)

    if command_name == "banping":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        if not message.role_mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}banping @role`")
        role = message.role_mentions[0]
        if toggle_banping_role(message.guild.id, role.id): return await send_embed(message.channel, f"🛡️ **Banping enabled** for {role.mention}. Users who ping this role will be auto-muted.", color=0xE74C3C)
        else: return await send_embed(message.channel, f"🔓 **Banping disabled** for {role.mention}.", color=0x2ECC71)

    if command_name == "prefix":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        args = command_body.split(maxsplit=1)
        if len(args) < 2: return await send_embed(message.channel, f"⚙️ Current prefix: `{get_guild_prefix(message.guild.id)}`")
        new_prefix = args[1].strip()
        if len(new_prefix) > 5 or " " in new_prefix: return await send_error_embed(message.channel, "❌ Prefix must be 1-5 chars without spaces.")
        set_guild_prefix(message.guild.id, new_prefix)
        return await send_embed(message.channel, f"✅ Prefix changed to `{new_prefix}`", color=0x2ECC71)

    if command_name == "addrole":
        if not await check_perms(message, manage_roles=True): return
        if not message.mentions or not message.role_mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addrole @user @role`")
        target_user = message.mentions[0]
        target_role = message.role_mentions[0]
        try:
            await target_user.add_roles(target_role)
            return await send_embed(message.channel, f"✅ Successfully added {target_role.mention} to {target_user.mention}.", color=0x2ECC71)
        except discord.Forbidden: return await send_error_embed(message.channel, "❌ I lack permission to add this role. Ensure my bot role is higher than the target role.")

    if command_name == "massreaction":
        if not await check_perms(message, manage_messages=True): return
        if not message.channel_mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}massreaction #channel <emoji1> [emoji2] ...`")
        target_channel = message.channel_mentions[0]
        args = command_body.split()
        emojis = [arg.strip() for arg in args[1:] if not arg.startswith("<#")]
        if not emojis: return await send_error_embed(message.channel, "❌ Provide at least one emoji.")
        active_mass_reactions[target_channel.id] = emojis
        return await send_embed(message.channel, f"✅ Mass reaction enabled in {target_channel.mention} with `{' '.join(emojis)}`.", color=0x2ECC71)

    if command_name == "unmassreaction":
        if not await check_perms(message, manage_messages=True): return
        target_channel = message.channel_mentions[0] if message.channel_mentions else message.channel
        if target_channel.id in active_mass_reactions:
            del active_mass_reactions[target_channel.id]
            return await send_embed(message.channel, f"🛑 Mass reaction disabled for {target_channel.mention}.", color=0xE74C3C)
        else: return await send_error_embed(message.channel, f"❌ Mass reaction is not currently active in {target_channel.mention}.")

    if command_name == "disablereactions":
        if not await check_perms(message, manage_messages=True): return
        overwrite = message.channel.overwrites_for(message.guild.default_role)
        overwrite.add_reactions = False
        try:
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, "🔒 Reactions have been disabled for `@everyone`.", color=0xE74C3C)
        except discord.Forbidden: return await send_error_embed(message.channel, "❌ I don't have permission.")

    if command_name == "enablereactions":
        if not await check_perms(message, manage_messages=True): return
        overwrite = message.channel.overwrites_for(message.guild.default_role)
        overwrite.add_reactions = None 
        try:
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, "🔓 Reactions have been enabled for `@everyone`.", color=0x2ECC71)
        except discord.Forbidden: return await send_error_embed(message.channel, "❌ I don't have permission.")

    if command_name == "say":
        if not await check_perms(message, manage_messages=True): return
        repeat_text = command_body[3:].strip()
        if not repeat_text: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}say <text>`")
        try: await message.delete()
        except: pass
        return await send_embed(message.channel, repeat_text)

    if command_name == "purge":
        if not await check_perms(message, manage_messages=True): return
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit(): return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}purge <number>`")
        try:
            deleted = await message.channel.purge(limit=min(int(args[1]) + 1, 100))
            confirm_log = await send_embed(message.channel, f"🗑️ Deleted `{len(deleted) - 1}` messages.", color=0x2ECC71)
            await asyncio.sleep(3.0)
            if confirm_log: await confirm_log.delete()
        except: await send_error_embed(message.channel, "❌ I don't have permission.")
        return

    if command_name == "mute":
        if not await check_perms(message, moderate_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}mute @user <time> [reason]`")
        target_user = message.mentions[0]
        if target_user.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        args = command_body.split()
        if len(args) < 3: return await send_error_embed(message.channel, "❌ Please provide a duration (e.g. `10m`, `1h`).")
        seconds = parse_duration(args[2])
        if seconds <= 0: return await send_error_embed(message.channel, "❌ Invalid time format.")
        reason = " ".join(args[3:]) if len(args) > 3 else "No reason provided."
        try:
            await target_user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
            dm_sent = await send_punishment_dm(target_user, message.guild, "mute", duration_str=args[2], reason=reason)
            return await send_embed(message.channel, f"🤫 **{target_user.name}** has been muted for **{args[2]}**.\n*Reason: {reason}*", color=0xF1C40F)
        except: return await send_error_embed(message.channel, "❌ I can't mute that user.")

    if command_name == "unmute":
        if not await check_perms(message, moderate_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unmute @user`")
        try:
            await message.mentions[0].timeout(None)
            return await send_embed(message.channel, f"✅ **{message.mentions[0].name}** has been unmuted.", color=0x2ECC71)
        except: return await send_error_embed(message.channel, "❌ I can't unmute that user.")

    if command_name == "kick":
        if not await check_perms(message, kick_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}kick @user [reason]`")
        target = message.mentions[0]
        if target.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = command_body.split(maxsplit=2)[2] if len(command_body.split(maxsplit=2)) > 2 else "None"
        try:
            dm_sent = await send_punishment_dm(target, message.guild, "kick", reason=reason)
            await target.kick(reason=reason)
            return await send_embed(message.channel, f"👢 **{target.name}** was kicked.\n*Reason: {reason}*", color=0xE67E22)
        except: return await send_error_embed(message.channel, "❌ I can't kick that user.")

    if command_name == "ban":
        if not await check_perms(message, ban_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}ban @user [reason]`")
        target = message.mentions[0]
        if target.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = command_body.split(maxsplit=2)[2] if len(command_body.split(maxsplit=2)) > 2 else "None"
        try:
            dm_sent = await send_punishment_dm(target, message.guild, "ban", reason=reason)
            await message.guild.ban(target, reason=reason)
            return await send_embed(message.channel, f"⛔ **{target.name}** was permanently banned.\n*Reason: {reason}*", color=0xE74C3C)
        except: return await send_error_embed(message.channel, "❌ I can't ban that user.")

    if command_name == "unban":
        if not await check_perms(message, ban_members=True): return
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit(): return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unban <user_id>`")
        target_id = int(args[1])
        try:
            await message.guild.unban(discord.Object(id=target_id))
            return await send_embed(message.channel, f"🔓 User `{target_id}` has been unbanned.", color=0x2ECC71)
        except discord.NotFound: return await send_error_embed(message.channel, "❌ That user ID was not found or is not currently banned.")
        except discord.Forbidden: return await send_error_embed(message.channel, "❌ I don't have permission to unban users.")

    if command_name == "warn":
        if not await check_perms(message, moderate_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}warn @user [reason]`")
        target = message.mentions[0]
        if target.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = command_body.split(maxsplit=2)[2] if len(command_body.split(maxsplit=2)) > 2 else "No reason"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
        cursor.execute("INSERT INTO warnings (guild_id, user_id, reason, enforcer, timestamp) VALUES (?, ?, ?, ?, ?)", (message.guild.id, target.id, reason, message.author.name, ts))
        cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
        total_warns = cursor.fetchone()[0]
        conn.commit(); conn.close()

        embed = discord.Embed(title="⚠️ User Warned", description=f"{target.mention} has received a warning.", color=0xF39C12)
        embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
        embed.add_field(name="Total Warnings", value=f"`{total_warns}`", inline=True)
        embed.add_field(name="Moderator", value=message.author.mention, inline=True)
        return await message.channel.send(embed=embed)

    if command_name == "warns":
        if not await check_perms(message, moderate_members=True): return
        target = message.mentions[0] if message.mentions else message.author
        conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
        cursor.execute("SELECT reason, enforcer, timestamp FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
        warns = cursor.fetchall(); conn.close()
        embed = discord.Embed(title=f"⚠️ Warnings for {target.name}", color=0xF39C12)
        if not warns: embed.description = "This user has no warnings."
        else:
            for idx, w in enumerate(warns, 1): embed.add_field(name=f"Warning #{idx}", value=f"**Reason:** {w[0]}\n**By:** {w[1]}\n**Date:** {w[2]}", inline=False)
        return await message.channel.send(embed=embed)

    if command_name in ("delwarn", "unwarn"):
        if not await check_perms(message, moderate_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}{command_name} @user #1`")
        target = message.mentions[0]
        if target.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        case_search = re.search(r"#(\d+)", command_body)
        if not case_search: return await send_error_embed(message.channel, f"⚠️ Format syntax: `{guild_prefix}{command_name} @user #1`")
        case_num = int(case_search.group(1))

        conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
        cursor.execute("SELECT id FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
        user_warns = cursor.fetchall()
        if case_num < 1 or case_num > len(user_warns):
            conn.close(); return await send_error_embed(message.channel, f"❌ That warning number doesn't exist. User has `{len(user_warns)}` warnings.")
        db_id = user_warns[case_num - 1][0]
        cursor.execute("DELETE FROM warnings WHERE id = ?", (db_id,))
        conn.commit(); conn.close()
        return await send_embed(message.channel, f"🗑️ Warning **#{case_num}** has been deleted for <@{target.id}>.", color=0x2ECC71)

    if command_name in ("delwarnsall", "unwarnall"):
        if not await check_perms(message, moderate_members=True): return
        if not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}{command_name} @user`")
        target = message.mentions[0]
        if target.id == message.author.id: return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")

        await send_embed(message.channel, f"Are you sure you want to clear all warnings for <@{target.id}>?\nType `Confirm deletion` to proceed.", color=0xFFFFFF, title="⚠️ Confirm Action")
        def check(m): return m.author == message.author and m.channel == message.channel and m.content.lower() == "confirm deletion"
        try:
            await client.wait_for('message', check=check, timeout=30.0)
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("DELETE FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
            conn.commit(); conn.close()
            await send_embed(message.channel, f"✅ All warnings have been cleared for <@{target.id}>.", color=0x2ECC71)
        except asyncio.TimeoutError: await send_error_embed(message.channel, "❌ Deletion cancelled (timeout).")
        return

    # --- BASIC UTILITY COMMANDS ---
    if command_name == "ping":
        return await send_embed(message.channel, f"🏓 **Pong!** Latency: {round(client.latency * 1000)}ms")

    if command_name == "info":
        servers = len(client.guilds)
        members = sum(g.member_count for g in client.guilds)
        uptime = str(datetime.datetime.now() - BOT_START_TIME).split('.')[0]
        embed = discord.Embed(title="V!tya - Bot information", description="Multi-use discord bot for different tasks, created by <@1155808109219020800>\nPrefixes: `v!` and `/`", color=0x90EE90)
        embed.set_thumbnail(url=client.user.display_avatar.url)
        embed.add_field(name="🌐 Servers", value=str(servers), inline=True)
        embed.add_field(name="👥 Members", value=str(members), inline=True)
        embed.add_field(name="⏱️ Uptime", value=uptime, inline=True)
        embed.add_field(name="🏓 Ping", value=f"{round(client.latency * 1000)}ms", inline=True)
        return await message.channel.send(embed=embed)

    if command_name == "shipchance":
        if len(message.mentions) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}shipchance @user1 @user2`")
        u1, u2 = message.mentions[0], message.mentions[1]
        chance = random.randint(0, 100)
        return await send_embed(message.channel, f"💖 The chance of {u1.mention} and {u2.mention} loving each other is: **{chance}%**", color=0xE74C3C)

    if command_name == "roll":
        args = command_body.split()
        if len(args) >= 3 and args[1].isdigit() and args[2].isdigit(): low, high = min(int(args[1]), int(args[2])), max(int(args[1]), int(args[2]))
        else: low, high = 1, 100
        return await send_embed(message.channel, f"🎲 **{message.author.display_name}** rolled ({low}-{high}): **{random.randint(low, high)}**", color=0x9B59B6)

    if command_name == "coin":
        return await send_embed(message.channel, f"🪙 **Coin Flip:** {random.choice(['HEADS', 'TAILS'])}", color=0xF1C40F)

    if command_name == "8ball":
        question = command_body[5:].strip()
        if not question: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}8ball <question>`")
        pos = ["It is certain.", "Yes, definitely.", "You may rely on it.", "Outlook good.", "Signs point to yes."]
        neu = ["Reply hazy, try again.", "Ask again later.", "Better not tell you now."]
        neg = ["Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
        answer = random.choice(pos + neu + neg)
        color = discord.Color.green() if answer in pos else (discord.Color.red() if answer in neg else discord.Color.gold())
        embed = discord.Embed(title="🎱 The Magic 8-Ball", color=color)
        embed.add_field(name="Question", value=f"*{question}*", inline=False)
        embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
        msg = await message.channel.send(embed=embed)
        if answer in pos: await msg.add_reaction("✅")
        elif answer in neg: await msg.add_reaction("❌")
        return

    if command_name == "chance":
        question = command_body[6:].strip()
        if not question: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}chance <question>`")
        return await send_embed(message.channel, f"🎲 The chance of **{question}** is: **{random.randint(0, 100)}%**", color=0x3498DB)

    if command_name == "math":
        expr = command_body[4:].strip()
        clean_expr = re.sub(r"[^0-9\+\-\*\/\(\)\.\s]", "", expr)
        try: return await send_embed(message.channel, f"📊 `{expr}` = **{eval(clean_expr, {'__builtins__': None}, {})}**", color=0x2ECC71)
        except Exception: return await send_error_embed(message.channel, "❌ Invalid math expression.")

    if command_name == "poll":
        poll_text = command_body[4:].strip()
        if not poll_text: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}poll <question> [| option1 | option2]`")
        parts = [p.strip() for p in poll_text.split("|")]
        if len(parts) == 1:
            poll_msg = await send_embed(message.channel, f"**{parts[0]}**", title="📊 Poll", color=0x3498DB)
            await poll_msg.add_reaction("👍"); await poll_msg.add_reaction("👎")
        else:
            options = parts[1:10]
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
            desc = f"**{parts[0]}**\n\n" + "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(options))
            poll_msg = await send_embed(message.channel, desc, title="📊 Poll", color=0x3498DB)
            for i in range(len(options)): await poll_msg.add_reaction(emojis[i])
        return

    if command_name == "serverinfo":
        guild = message.guild
        humans = len([m for m in guild.members if not m.bot])
        embed = discord.Embed(title=f"Server Info: {guild.name}", color=0x3498DB)
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👑 Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}", inline=True)
        embed.add_field(name="📅 Created", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="🚀 Boosts", value=f"Tier {guild.premium_tier} ({guild.premium_subscription_count} Boosts)", inline=True)
        embed.add_field(name=f"👥 Members ({guild.member_count})", value=f"🧑 Humans: {humans}\n🤖 Bots: {guild.member_count - humans}", inline=True)
        embed.add_field(name=f"📺 Channels ({len(guild.text_channels) + len(guild.voice_channels)})", value=f"💬 Text: {len(guild.text_channels)}\n🔊 Voice: {len(guild.voice_channels)}", inline=True)
        embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
        return await message.channel.send(embed=embed)

    if command_name == "userinfo":
        target = message.mentions[0] if message.mentions else message.author
        embed = discord.Embed(title=f"User Info: {target.name}", color=target.color)
        if target.display_avatar: embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="ID", value=f"`{target.id}`", inline=False)
        embed.add_field(name="Joined Server", value=f"<t:{int(target.joined_at.timestamp())}:D>" if target.joined_at else "Unknown", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(target.created_at.timestamp())}:D>", inline=True)
        return await message.channel.send(embed=embed)

    if command_name == "avatar":
        target = message.mentions[0] if message.mentions else message.author
        embed = discord.Embed(title=f"{target.name}'s Avatar", color=0x3498DB)
        embed.set_image(url=target.display_avatar.url)
        return await message.channel.send(embed=embed)

    if command_name == "snipe":
        if channel_id in sniped_messages:
            content, author, timestamp = sniped_messages[channel_id]
            embed = discord.Embed(description=content, color=0x3498DB, timestamp=timestamp)
            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
            embed.set_footer(text="Sniped message")
            return await message.channel.send(embed=embed)
        else: return await send_error_embed(message.channel, "❌ There's nothing to snipe in this channel.")

    if command_name == "roleinfo":
        if not message.role_mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}roleinfo @role`")
        role = message.role_mentions[0]
        embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Color Hex", value=f"`{str(role.color)}`", inline=True)
        embed.add_field(name="Members", value=f"{len(role.members)} users", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(role.created_at.timestamp())}:D>", inline=True)
        return await message.channel.send(embed=embed)

    if command_name in ("membercount", "mc"):
        humans = sum(1 for m in message.guild.members if not m.bot)
        embed = discord.Embed(title=f"📊 Member Count: {message.guild.name}", color=0x2ECC71)
        embed.add_field(name="Total Members", value=f"**{message.guild.member_count}**", inline=False)
        embed.add_field(name="🧑 Humans", value=str(humans), inline=True)
        embed.add_field(name="🤖 Bots", value=str(message.guild.member_count - humans), inline=True)
        return await message.channel.send(embed=embed)

    if command_name == "setbio":
        bio_text = command_body[6:].strip()
        if len(bio_text) > 200: return await send_error_embed(message.channel, "❌ Bio must be 200 characters or fewer.")
        set_bio(message.author.id, bio_text)
        return await send_embed(message.channel, "✅ Your bio has been updated.", color=0x2ECC71)

    if command_name == "profile":
        try:
            target = message.mentions[0] if message.mentions else message.author
            bio = get_bio(target.id)
            embed = discord.Embed(title=f"{target.name}'s Profile", color=target.color)
            if target.display_avatar: embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="Bio", value=bio or f"*No bio set. Type `{guild_prefix}setbio (text)` to set bio.*", inline=False)
            embed.add_field(name="Reputation", value=f"✨ {get_rep(target.id, message.guild.id)} Points", inline=True)
            return await message.channel.send(embed=embed)
        except Exception: return await send_error_embed(message.channel, "❌ Failed to load profile.")

    if command_name == "rep":
        args = command_body.split()
        if len(args) < 3 or not message.mentions: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}rep @user 1` or `-1`")
        target = message.mentions[0]
        if target.id == message.author.id or target.bot: return await send_error_embed(message.channel, "❌ Invalid target for reputation.")

        remaining = get_rep_cooldown_remaining(message.guild.id, message.author.id, target.id)
        if remaining > 0: return await send_error_embed(message.channel, f"⏳ You must wait `{max(1, round(remaining/60))}` minutes before changing this user's rep again.")

        delta = 1 if args[2] == "1" else (-1 if args[2] == "-1" else 0)
        if delta == 0: return await send_error_embed(message.channel, "❌ Use `1` to add or `-1` to remove.")

        new_total = add_rep(target.id, delta, message.guild.id)
        set_rep_cooldown(message.guild.id, message.author.id, target.id)
        return await send_embed(message.channel, f"{'+1 ✨' if delta == 1 else '-1 📉'} **{target.display_name}**'s rep is now **{new_total}**.", color=0x2ECC71 if delta == 1 else 0xE74C3C)

    if command_name == "repleaderboard":
        rows = get_rep_leaderboard(message.guild.id, 10)
        embed = discord.Embed(title="🏆 Reputation Leaderboard", color=0xF1C40F)
        if not rows: embed.description = "No reputation data yet."
        else:
            lines = [f"{'🥇' if i==1 else '🥈' if i==2 else '🥉' if i==3 else f'**#{i}**'} <@{u}> — **{p} rep**" for i, (u, p) in enumerate(rows, 1)]
            embed.description = "\n".join(lines)
        return await message.channel.send(embed=embed)

    # --- HELP MENU (PAGINATION) ---
    if command_name in ("commands", "help"):
        now_ts = datetime.datetime.now().timestamp()
        cooldown_key = (message.guild.id, message.author.id)
        if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
            mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
            return await send_error_embed(message.channel, f"⏳ You are on cooldown! Please wait **{mins}m {secs}s** before calling help again.")
        help_cooldowns[cooldown_key] = now_ts

        pages = get_help_pages(message.guild.id, guild_prefix)
        view = CommandPaginationView(message.author, pages)
        view.message = await message.channel.send(embed=pages[0], view=view)
        return

if BOT_TOKEN:
    client.run(BOT_TOKEN)
