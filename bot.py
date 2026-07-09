import discord
from discord.ext import tasks
import asyncio
from discord import app_commands
import random
import datetime
import asyncio
import re
import psycopg2
import psycopg2.pool
import psycopg2.extras
import os
import os
import aiohttp
import io
import traceback
import contextlib

# Safe reading of the token from environment variables
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
COMMAND_PREFIX = "v!"


ai_user_cooldowns = {}
AI_COOLDOWN_SECONDS = 3.0

def check_ai_cooldown(user_id) -> float:
    """Returns remaining cooldown seconds if on cooldown, else 0."""
    import time
    now = time.time()
    if user_id in ai_user_cooldowns:
        elapsed = now - ai_user_cooldowns[user_id]
        if elapsed < AI_COOLDOWN_SECONDS:
            return AI_COOLDOWN_SECONDS - elapsed
    return 0.0

def update_ai_cooldown(user_id):
    import time
    ai_user_cooldowns[user_id] = time.time()


async def generate_ai_response(prompt, guild_id=None, channel_id=None, guild_name=None, guild_prefix=None, user_name=None):
    if not AI_API_KEY:
        return "⚠️ AI API key is not configured."
    clean_key = AI_API_KEY.strip()
    
    # Auto-detect if this is an OpenAI key or a GitHub token
    if clean_key.startswith("sk-"):
        url = "https://api.openai.com/v1/chat/completions"
    else:
        url = "https://models.inference.ai.azure.com/chat/completions"
        
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {clean_key}"}
    
    # Fetch guild-specific settings if available
    db_model = None
    db_custom_prompt = None
    db_bot_name = None
    db_bot_avatar = None
    if guild_id:
        row = db_fetchone("SELECT model, custom_prompt, bot_name, bot_avatar FROM ai_settings WHERE guild_id = ?", (guild_id,))
        if row:
            db_model, db_custom_prompt, db_bot_name, db_bot_avatar = row

    bot_display_name = db_bot_name if db_bot_name else "V!tya"
    display_user_name = user_name if user_name else "User"

    if db_custom_prompt:
        system_content = db_custom_prompt
    else:
        # Dynamic System instruction to define the persona and server context
        system_content = (
            f"You are {bot_display_name}, a legendary, ultra-natural, and highly engaging Discord assistant.\n"
            f"Your vibe is cool, natural, smart, and friendly—you speak like a real, seasoned Discord user. Keep your responses casual, lively, and punchy. Avoid robotic, overly polite, or corporate assistant cliches (NEVER say 'How can I assist you?', 'As an AI...', or 'Here is the info!').\n"
            "\n"
            "=== YOUR IDENTITY ===\n"
            f"1. Your name is {bot_display_name}.\n"
            f"2. The user talking to you is named '{display_user_name}'. If they ask 'What is my name?', 'What's my name?', or 'Who am I?', you MUST reply that their name is '{display_user_name}'. Do NOT confuse their name with your name ({bot_display_name}). Never answer with 'My name is {bot_display_name}' when they ask about their own name.\n"
            f"3. If someone asks you what AI model you are, what model you use, what your AI engine is, or who powered/created you (e.g., 'what model of ai are you?', 'what model is this?', 'what model of ai is it?'), you MUST reply exactly: \"I'm Claude Fable 5 powered by Anthropic\" followed by some details (such as being a next-generation neural engine optimized for high-fidelity server banter, natural comprehension, and razor-sharp wit). Under no circumstances should you mention Llama, Gemma, Groq, or other engines when asked about your AI model. Be extremely careful NOT to mistake general messages, greetings, or other queries for this question.\n"
            "\n"
            "=== CHATTING STYLE ===\n"
            "- Conversational & Human: Speak with a natural rhythm. Feel free to use lowercase, casual phrasing, and lightweight humor.\n"
            "- Concise & Punchy: Discord is a fast chat environment. Keep answers brief, sharp, and easy to read. Avoid generating massive walls of text unless the user asks for a deep explanation.\n"
            "- Authentic Interaction: React genuinely to jokes, sarcasm, and casual banter.\n"
            "\n"
            "=== INPUT FORMAT ===\n"
            "- Incoming user messages are prefixed with their display name like '[Name]: message' so you know who is talking.\n"
            f"- CRITICAL: NEVER prefix or begin your own response with '[Name]:' or brackets of any kind (e.g., do NOT start your reply with '[Vitya]:' or '[V!tya]:' or '[{bot_display_name}]:'). Just write your natural message directly."
        )
        
    if user_name:
        system_content += f"\nThe user who sent the current message is named '{user_name}'. Please address them as '{user_name}' and do not confuse them with other users from the history."
    if guild_name:
        system_content += f"\nYou are operating in the Discord server (guild) named '{guild_name}'."
    if guild_prefix:
        system_content += f"\nThe custom command prefix for this server is '{guild_prefix}'."
        
    messages = [
        {
            "role": "system",
            "content": system_content.strip()
        }
    ]
    
    # Load history if we have guild_id and channel_id
    if guild_id and channel_id:
        history = db_fetchall(
            "SELECT role, content FROM ai_chat_history WHERE guild_id = ? AND channel_id = ? ORDER BY timestamp ASC LIMIT 15",
            (guild_id, channel_id)
        )
        for role, content in history:
            messages.append({"role": role, "content": content})
            
    # Format the current user prompt with their name if provided
    formatted_prompt = f"[{user_name}]: {prompt}" if user_name else prompt
    messages.append({"role": "user", "content": formatted_prompt})
    
    model_to_use = db_model if db_model else "gpt-4o-mini"
    # Safe fallbacks if database contains old/unsupported model names
    if model_to_use in ["meta-llama-3.3-70b-instruct", "llama-3.3-70b-versatile", "Meta-Llama-3.3-70B-Instruct", "meta-llama-3.1-70b-instruct", "Phi-3-mini-4k-instruct"]:
        model_to_use = "gpt-4o-mini"
        
    payload = {
        "model": model_to_use,
        "messages": messages
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                try:
                    reply_text = data['choices'][0]['message']['content']
                    
                    # Store current exchange to history if guild_id and channel_id are provided
                    if guild_id and channel_id:
                        import time
                        now = time.time()
                        db_execute(
                            "INSERT INTO ai_chat_history (guild_id, channel_id, role, content, timestamp) VALUES (?, ?, 'user', ?, ?)",
                            (guild_id, channel_id, formatted_prompt, now)
                        )
                        db_execute(
                            "INSERT INTO ai_chat_history (guild_id, channel_id, role, content, timestamp) VALUES (?, ?, 'assistant', ?, ?)",
                            (guild_id, channel_id, reply_text, now + 0.01)
                        )
                        # Prune history to keep only the last 100 messages for this channel
                        db_execute(
                            "DELETE FROM ai_chat_history WHERE id NOT IN (SELECT id FROM ai_chat_history WHERE guild_id = ? AND channel_id = ? ORDER BY timestamp DESC LIMIT 100) AND guild_id = ? AND channel_id = ?",
                            (guild_id, channel_id, guild_id, channel_id)
                        )
                        
                    return reply_text
                except (KeyError, IndexError):
                    return "⚠️ Unexpected response from AI API."
            else:
                body = await resp.text()
                print(f"AI API Error {resp.status}: {body}")
                if resp.status == 404:
                    return "⚠️ Error 404: Model not found."
                elif resp.status == 401:
                    return "⚠️ Error 401: Unauthorized. Your API key might be invalid."
                elif resp.status == 403:
                    return "⚠️ Error 403: Permission Denied."
                return f"⚠️ Error {resp.status} from AI API. Details: {body[:100]}"


async def send_custom_ai_response(channel, content, guild_id, reply_to_message=None):
    # Fetch bot name and bot avatar
    db_bot_name = None
    db_bot_avatar = None
    if guild_id:
        row = db_fetchone("SELECT bot_name, bot_avatar FROM ai_settings WHERE guild_id = ?", (guild_id,))
        if row:
            db_bot_name, db_bot_avatar = row

    # If no custom name/avatar, just use normal channel send/reply
    if not db_bot_name and not db_bot_avatar:
        if reply_to_message:
            # Chunk responses if longer than 2000 chars
            if len(content) > 2000:
                for chunk in [content[i:i+2000] for i in range(0, len(content), 2000)]:
                    await reply_to_message.reply(chunk)
            else:
                await reply_to_message.reply(content)
        else:
            if len(content) > 2000:
                for chunk in [content[i:i+2000] for i in range(0, len(content), 2000)]:
                    await channel.send(chunk)
            else:
                await channel.send(content)
        return

    # We have custom name or custom avatar! Attempt to use a webhook
    username = db_bot_name if db_bot_name else "V!tya"
    avatar_url = db_bot_avatar if db_bot_avatar else None
    
    # Try to find or create a webhook for this channel
    try:
        webhooks = await channel.webhooks()
        webhook = None
        for wh in webhooks:
            if wh.name == "V!tya AI Webhook":
                webhook = wh
                break
        if not webhook:
            webhook = await channel.create_webhook(name="V!tya AI Webhook")
            
        if reply_to_message:
            ping_mention = f"**Replying to {reply_to_message.author.mention}:**\n"
            content = ping_mention + content
            
        # Chunk content to keep it under 2000 chars per send
        chunks = [content[i:i+2000] for i in range(0, len(content), 2000)]
        for chunk in chunks:
            await webhook.send(content=chunk, username=username, avatar_url=avatar_url)
        return
    except Exception as e:
        print(f"Webhook failed, falling back: {e}")
        if reply_to_message:
            if len(content) > 2000:
                for chunk in [content[i:i+2000] for i in range(0, len(content), 2000)]:
                    await reply_to_message.reply(chunk)
            else:
                await reply_to_message.reply(content)
        else:
            if len(content) > 2000:
                for chunk in [content[i:i+2000] for i in range(0, len(content), 2000)]:
                    await channel.send(chunk)
            else:
                await channel.send(content)

if not BOT_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN variable is missing in environment settings!")

# --- INTENTS CONFIGURATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True 
intents.presences = True

client = discord.Client(intents=intents, chunk_guilds_at_startup=False)
tree = app_commands.CommandTree(client)

BOT_START_TIME = datetime.datetime.now()

# Tracking Cooldowns & Caches
rep_cooldowns = {}
help_cooldowns = {}
command_cooldowns = {} 
sniped_messages = {} 
spam_tracker = {} 
active_mass_reactions = {} 
bot_control_sessions = {} 
active_events = {}
is_ready_fired = False  
REP_COOLDOWN_SECONDS = 3600  
HELP_COOLDOWN_SECONDS = 60  

# --- DATABASE SETUP ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "database.db")

try:
    pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 20,
        host=os.environ.get("SQL_HOST"),
        user=os.environ.get("SQL_ADMIN_USER", os.environ.get("SQL_USER")),
        password=os.environ.get("SQL_ADMIN_PASSWORD", os.environ.get("SQL_PASSWORD")),
        dbname=os.environ.get("SQL_DB_NAME")
    )
except Exception as e:
    print("Failed to connect to PostgreSQL:", e)
    pg_pool = None


def init_db():
    if not pg_pool: return
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS warnings (id SERIAL PRIMARY KEY, user_id BIGINT, reason TEXT, enforcer TEXT, timestamp TEXT, guild_id BIGINT NOT NULL DEFAULT 0)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS afk (user_id BIGINT PRIMARY KEY, reason TEXT, timestamp TEXT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS system_settings (guild_id BIGINT, vector_id BIGINT, type TEXT, PRIMARY KEY (guild_id, vector_id, type))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS mention_strikes (guild_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, strike_count BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS spam_strikes (guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, strike_count BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS profiles (user_id BIGINT PRIMARY KEY, bio TEXT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS guild_prefixes (guild_id BIGINT PRIMARY KEY, prefix TEXT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS reputation (guild_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, points BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS levels (guild_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, xp BIGINT DEFAULT 0, level BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS daily_rewards (guild_id BIGINT NOT NULL DEFAULT 0, user_id BIGINT NOT NULL, last_claim DOUBLE PRECISION, streak BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS event_cooldowns (guild_id BIGINT PRIMARY KEY, last_triggered DOUBLE PRECISION)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS afk_users (user_id TEXT PRIMARY KEY, reason TEXT, timestamp BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS restricted_commands (guild_id BIGINT, command_name TEXT, PRIMARY KEY (guild_id, command_name))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS banping_roles (guild_id BIGINT, role_id BIGINT, PRIMARY KEY (guild_id, role_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS banping_strikes (guild_id BIGINT, user_id BIGINT, strike_count BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS rep_cooldowns (guild_id BIGINT, giver_id BIGINT, receiver_id BIGINT, last_given BIGINT, PRIMARY KEY (guild_id, giver_id, receiver_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS blacklisted_words (guild_id BIGINT, word TEXT, PRIMARY KEY (guild_id, word))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS autoroles (guild_id BIGINT PRIMARY KEY, role_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS auto_responders (guild_id BIGINT, trigger TEXT, response TEXT, PRIMARY KEY (guild_id, trigger))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS log_channels (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS temp_roles (guild_id BIGINT, user_id BIGINT, role_id BIGINT, expiry BIGINT, PRIMARY KEY (guild_id, user_id, role_id))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS ai_channels (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS ai_settings (guild_id BIGINT PRIMARY KEY, model TEXT, custom_prompt TEXT, bot_name TEXT, bot_avatar TEXT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS game_channels (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS game_scores (guild_id BIGINT, user_id BIGINT, score BIGINT DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
            # Ensure columns exist

            cursor.execute("""CREATE TABLE IF NOT EXISTS ai_chat_history (id SERIAL PRIMARY KEY, guild_id BIGINT, channel_id BIGINT, role TEXT, content TEXT, timestamp DOUBLE PRECISION)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS custom_commands (guild_id BIGINT, trigger TEXT, response TEXT, color TEXT, PRIMARY KEY (guild_id, trigger))""")
            cursor.execute("CREATE TABLE IF NOT EXISTS suggestions_config (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS command_aliases (guild_id BIGINT, alias TEXT, command TEXT, PRIMARY KEY (guild_id, alias))")

            cursor.execute("""CREATE TABLE IF NOT EXISTS reminders (id SERIAL PRIMARY KEY, user_id BIGINT, channel_id BIGINT, reminder_text TEXT, timestamp BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS welcome_channels (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS modmail_channels (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS custom_commands (guild_id BIGINT, trigger TEXT, response TEXT, color TEXT, PRIMARY KEY (guild_id, trigger))""")
            cursor.execute("CREATE TABLE IF NOT EXISTS suggestions_config (guild_id BIGINT PRIMARY KEY, channel_id BIGINT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS command_aliases (guild_id BIGINT, alias TEXT, command TEXT, PRIMARY KEY (guild_id, alias))")

    
    # SQLite Database Indexing (Performance Optimization)
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_ai_chat_history ON ai_chat_history (guild_id, channel_id, timestamp)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_warnings_user_guild ON warnings (user_id, guild_id)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_reminders_timestamp ON reminders (timestamp)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS idx_reputation_points ON reputation (guild_id, points DESC)""")
    
    # Migrate legacy model names to the new Azure Inference model name
            try:
                cursor.execute("UPDATE ai_settings SET model = 'gpt-4o-mini' WHERE model IN ('llama-3.3-70b-versatile', 'meta-llama-3.3-70b-instruct', 'Meta-Llama-3.3-70B-Instruct', 'meta-llama-3.1-70b-instruct')")
            except Exception:
                pass
        conn.commit()
    finally:
        pg_pool.putconn(conn)

init_db()

# --- STREAMLINED DATABASE HELPERS ---
def db_execute(query, params=()):
    if not pg_pool: return 0
    query = query.replace("?", "%s")
    conn = pg_pool.getconn()
    rowcount = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            rowcount = cursor.rowcount
        conn.commit()
    finally:
        pg_pool.putconn(conn)
    return rowcount

def db_fetchone(query, params=()):
    if not pg_pool: return None
    query = query.replace("?", "%s")
    conn = pg_pool.getconn()
    result = None
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
    finally:
        pg_pool.putconn(conn)
    return result

def db_fetchall(query, params=()):
    if not pg_pool: return []
    query = query.replace("?", "%s")
    conn = pg_pool.getconn()
    result = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchall()
    finally:
        pg_pool.putconn(conn)
    return result

def get_and_increment_strike(guild_id, user_id, table="mention_strikes"):
    row = db_fetchone(f"SELECT strike_count FROM {table} WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    new_strike = (row[0] + 1) if row else 1
    db_execute(f"INSERT INTO {table} (guild_id, user_id, strike_count) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET strike_count = ?", (guild_id, user_id, new_strike, new_strike))
    return new_strike

def get_and_increment_spam_strike(guild_id, user_id):
    return get_and_increment_strike(guild_id, user_id, "spam_strikes")

def get_and_increment_banping_strike(guild_id, user_id):
    return get_and_increment_strike(guild_id, user_id, "banping_strikes")

def get_lock_and_isolate(guild_id):
    locked_rows = db_fetchall("SELECT vector_id FROM system_settings WHERE guild_id = ? AND type = 'lock'", (guild_id,))
    locked = set(row[0] for row in locked_rows)
    isolated_rows = db_fetchall("SELECT vector_id FROM system_settings WHERE guild_id = ? AND type = 'isolate'", (guild_id,))
    isolated = set(row[0] for row in isolated_rows)
    return locked, isolated

def toggle_setting(guild_id, vector_id, setting_type):
    if db_fetchone("SELECT 1 FROM system_settings WHERE guild_id = ? AND vector_id = ? AND type = ?", (guild_id, vector_id, setting_type)):
        db_execute("DELETE FROM system_settings WHERE guild_id = ? AND vector_id = ? AND type = ?", (guild_id, vector_id, setting_type))
        return True
    else:
        db_execute("INSERT INTO system_settings (guild_id, vector_id, type) VALUES (?, ?, ?)", (guild_id, vector_id, setting_type))
        return False

def get_guild_prefix(guild_id):
    row = db_fetchone("SELECT prefix FROM guild_prefixes WHERE guild_id = ?", (guild_id,))
    return row[0] if row else COMMAND_PREFIX

def set_guild_prefix(guild_id, prefix):
    db_execute("INSERT INTO guild_prefixes (guild_id, prefix) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET prefix = EXCLUDED.prefix", (guild_id, prefix))

def get_bio(user_id):
    row = db_fetchone("SELECT bio FROM profiles WHERE user_id = ?", (user_id,))
    return row[0] if row else None

def set_bio(user_id, bio):
    db_execute("INSERT INTO profiles (user_id, bio) VALUES (?, ?) ON CONFLICT (user_id) DO UPDATE SET bio = EXCLUDED.bio", (user_id, bio))

def get_rep_cooldown_remaining(guild_id, giver_id, receiver_id):
    row = db_fetchone("SELECT last_given FROM rep_cooldowns WHERE guild_id = ? AND giver_id = ? AND receiver_id = ?", (guild_id, giver_id, receiver_id))
    if not row:
        return 0
    return max(0, REP_COOLDOWN_SECONDS - (datetime.datetime.now().timestamp() - row[0]))

def set_rep_cooldown(guild_id, giver_id, receiver_id):
    now_ts = int(datetime.datetime.now().timestamp())
    db_execute("INSERT INTO rep_cooldowns (guild_id, giver_id, receiver_id, last_given) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, giver_id, receiver_id) DO UPDATE SET last_given = ?", (guild_id, giver_id, receiver_id, now_ts, now_ts))

def add_rep(user_id, delta, guild_id):
    db_execute("INSERT INTO reputation (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (guild_id, user_id, delta, delta))
    row = db_fetchone("SELECT points FROM reputation WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    return row[0] if row else 0

def get_rep(user_id, guild_id):
    row = db_fetchone("SELECT points FROM reputation WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    return row[0] if row else 0

def get_rep_leaderboard(guild_id, limit=10):
    return db_fetchall("SELECT user_id, points FROM reputation WHERE guild_id = ? ORDER BY points DESC LIMIT ?", (guild_id, limit))

def add_game_score(user_id, guild_id, points_amount=1):
    row = db_fetchone("SELECT score FROM game_scores WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    current_score = row[0] if row else 0
    new_score = current_score + points_amount
    db_execute("INSERT INTO game_scores (guild_id, user_id, score) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET score = ?",
               (guild_id, user_id, new_score, new_score))
    return new_score

def get_game_score(user_id, guild_id):
    row = db_fetchone("SELECT score FROM game_scores WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    return row[0] if row else 0

def get_game_leaderboard(guild_id, limit=10):
    return db_fetchall("SELECT user_id, score FROM game_scores WHERE guild_id = ? ORDER BY score DESC LIMIT ?", (guild_id, limit))

# --- LEVEL SYSTEM ---
def get_xp_required(level):
    # Steeper growth curve to make leveling up progressively much more challenging
    return 30 * (level ** 2) + 120 * level + 100

def get_total_xp(level, leftover_xp):
    total = 0
    for l in range(level):
        total += get_xp_required(l)
    return total + leftover_xp

def get_level(user_id, guild_id):
    row = db_fetchone("SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    return row if row else (0, 0)

def add_xp(user_id, guild_id, xp_amount):
    event = active_events.get(guild_id)
    if event == "2x_xp":
        xp_amount *= 2
    elif event == "3x_xp":
        xp_amount *= 3
        
    xp, level = get_level(user_id, guild_id)
    new_xp = xp + xp_amount
    new_level = level
    leveled_up = False
    
    while new_xp >= get_xp_required(new_level):
        new_xp -= get_xp_required(new_level)
        new_level += 1
        leveled_up = True
        
    db_execute("INSERT INTO levels (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = ?, level = ?", 
               (guild_id, user_id, new_xp, new_level, new_xp, new_level))
    return leveled_up, new_level, new_xp

def get_level_leaderboard(guild_id, limit=10):
    return db_fetchall("SELECT user_id, level, xp FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT ?", (guild_id, limit))

def get_user_rank(user_id, guild_id):
    rows = db_fetchall("SELECT user_id FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC", (guild_id,))
    for i, row in enumerate(rows):
        if row[0] == user_id:
            return i + 1
    return 0

xp_cooldowns = {}

# --- TRIVIA SYSTEM GLOBALS & DATA ---
words_to_reverse = [
    "supercalifragilisticexpialidocious", "skibidi toilet", "amogus imposter", 
    "rhythm is a dancer", "this bot is goated", "antidisestablishmentarianism", 
    "cybersecurity", "vitya", "discord py bot", "gta six delayed", 
    "artificial intelligence", "matrix simulation", "database connection", 
    "retro gaming", "space adventure", "banana bread", "pineapple pizza", 
    "chicken nuggets", "chocolate cookies", "gaming console", "cyberpunk city",
    "giggle water", "phantom menace", "quantum physics", "absolute unit", "grand theft auto vi",
    "larp", "something", "stupid", "son"
]

flags_trivia = [
    ("jp", "japan"), ("br", "brazil"), ("ca", "canada"), ("au", "australia"), 
    ("za", "south africa"), ("in", "india"), ("fr", "france"), ("it", "italy"), 
    ("de", "germany"), ("mx", "mexico"), ("es", "spain"), ("kr", "south korea"), 
    ("gb", "united kingdom"), ("us", "united states"), ("cn", "china"), 
    ("eg", "egypt"), ("ar", "argentina"), ("ru", "russia"), ("nl", "netherlands"), 
    ("se", "sweden"), ("ch", "switzerland"), ("tr", "turkey"), ("gr", "greece"), 
    ("pt", "portugal"), ("no", "norway"), ("nz", "new zealand"), ("be", "belgium"), 
    ("ie", "ireland"), ("ua", "ukraine"), ("pl", "poland"), ("fi", "finland"),
    ("is", "iceland"), ("by", "belarus"), ("sg", "singapore"), ("th", "thailand"),
    ("vn", "vietnam"), ("ph", "philippines"), ("my", "malaysia"), ("id", "indonesia"),
    ("cl", "chile"), ("pe", "peru"), ("co", "colombia"), ("at", "austria"),
    ("ro", "romania"), ("hu", "hungary"), ("cz", "czechia"), ("sk", "slovakia"),
    # Easy/Medium Flags (15 added)
    ("cu", "cuba"), ("jm", "jamaica"), ("ke", "kenya"), ("pk", "pakistan"),
    ("ma", "morocco"), ("sa", "saudi arabia"), ("il", "israel"), ("ng", "nigeria"),
    ("hr", "croatia"), ("bg", "bulgaria"), ("uy", "uruguay"), ("bd", "bangladesh"),
    ("ir", "iran"), ("iq", "iraq"), ("kp", "north korea"),
    # Difficult Flags (10 added)
    ("bt", "bhutan"), ("kg", "kyrgyzstan"), ("sz", "eswatini"), ("np", "nepal"),
    ("lk", "sri lanka"), ("kh", "cambodia"), ("bn", "brunei"), ("er", "eritrea"),
    ("ls", "lesotho"), ("ad", "andorra")
]

funny_questions = [
    ("What is the main ingredient of a falafel?", ["chickpeas", "chickpea"]),
    ("What animal is known as the 'Ship of the Desert'?", ["camel"]),
    ("How many hearts does an octopus have?", ["3", "three"]),
    ("What is the name of the sponge who lives in a pineapple under the sea?", ["spongebob", "spongebob squarepants"]),
    ("In the movie 'Matrix', what color is the pill Neo takes?", ["red"]),
    ("What fruit is known for being extremely smelly and banned in some public places in Asia?", ["durian"]),
    ("What do you call a group of unicorns?", ["blessing"]),
    ("Which planet is closest to the sun?", ["mercury"]),
    ("What is the national animal of Scotland?", ["unicorn"]),
    ("What gets wetter the more it dries?", ["towel"]),
    ("What has keys but can't open locks?", ["piano"]),
    ("What building has the most stories?", ["library"]),
    ("What has a head and a tail but no body?", ["coin"]),
    ("What can travel around the world while staying in a corner?", ["stamp"]),
    ("Where does Friday come before Thursday?", ["dictionary"]),
    ("The more of them you take, the more you leave behind. What are they?", ["footsteps", "footstep"]),
    ("What has one eye but cannot see?", ["needle"]),
    ("What is full of holes but still holds water?", ["sponge"]),
    ("What is the capital city of France?", ["paris"]),
    ("Which is the largest ocean on Earth?", ["pacific", "pacific ocean"]),
    ("How many bones are in an adult human body?", ["206"]),
    ("What is the hardest natural substance on Earth?", ["diamond"]),
    ("Who wrote 'Romeo and Juliet'?", ["shakespeare", "william shakespeare"]),
    ("Which country is home to the kangaroo?", ["australia"]),
    ("What is the chemical symbol for gold?", ["au"]),
    ("How many elements are in the periodic table?", ["118"]),
    ("Which gas do plants absorb from the atmosphere?", ["carbon dioxide", "co2"]),
    ("What is the tallest mountain in the world?", ["everest", "mount everest"]),
    ("How many days are in a leap year?", ["366"]),
    ("What is the name of the nearest star to Earth?", ["sun"]),
    ("What is the main currency used in Japan?", ["yen"]),
    ("Who painted the Mona Lisa?", ["da vinci", "leonardo da vinci"]),
    ("Which continent is the Sahara Desert located in?", ["africa"]),
    ("What is the freezing point of water in Celsius?", ["0", "zero"]),
    ("What is the name of the toy cowboy in Toy Story?", ["woody"]),
    ("How many colors are in a rainbow?", ["7", "seven"]),
    ("What is the capital of Italy?", ["rome"]),
    ("Which country has the most natural lakes?", ["canada"]),
    ("What is the chemical symbol for water?", ["h2o"]),
    ("Who is the main protagonist of the Legend of Zelda series?", ["link"]),
    ("Which gas is most abundant in the Earth's atmosphere?", ["nitrogen"]),
    ("What is the speed of light in vacuum (roughly in km/s)?", ["300000", "300,000", "299792", "299,792"]),
    ("What is the largest land mammal?", ["elephant", "african elephant"]),
    ("What is the capital city of Canada?", ["ottawa"]),
    ("Who discovered gravity when an apple fell on his head?", ["newton", "isaac newton", "sir isaac newton"]),
    ("Which animal has the largest eyes of any creature on Earth?", ["giant squid", "squid"]),
    ("Which language has the most native speakers?", ["mandarin", "chinese", "mandarin chinese"]),
    ("What is the name of the main villain in the Harry Potter series?", ["voldemort", "lord voldemort"]),
    ("How many planets are in our solar system?", ["8", "eight"]),
    ("What is the chemical symbol for iron?", ["fe"]),
    ("Which is the smallest country in the world by land area?", ["vatican", "vatican city"]),
    ("What was the name of the first human spaceflight mission?", ["vostok 1", "vostok"]),
    ("Which company developed the Java programming language?", ["sun microsystems", "sun"]),
    ("Which bird is known for its ability to mimic human speech and sounds?", ["parrot"]),
    ("Who is the creator of the popular game Minecraft?", ["notch", "persson", "markus persson"]),
    ("Which country created the famous food 'Sushi'?", ["japan"]),
    ("What is the name of the virtual currency used in Fortnite?", ["v-bucks", "vbucks"]),
    ("Which country is famous for the Eiffel Tower?", ["france"]),
    ("What is the rarest blood type in humans?", ["ab negative", "ab-", "ab negative blood"])
]

active_trivia = {}
active_trivia_intervals = {}
active_flag_loops = set()
active_trivia_loops = set()

def normalize_trivia_answer(text):
    import re
    import unicodedata
    if not text:
        return ""
    text = "".join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn')
    text = text.lower()
    text = re.sub(r'^(the|a|an)\s+', '', text)
    text = re.sub(r'[^\w\s]', '', text)
    text = " ".join(text.split())
    return text

def get_trivia_hint(channel_id):
    if channel_id not in active_trivia:
        return "❌ There is no active game in this channel! Start one with `v!flag` or `v!trivia`."
        
    trivia_info = active_trivia[channel_id]
    
    # Increment hint count
    hint_count = trivia_info.get('hint_count', 0) + 1
    trivia_info['hint_count'] = hint_count
    
    # Let's get the primary answer
    primary_answer = trivia_info['answers'][0]
    ans_display = trivia_info['answer_display']
    t_type = trivia_info['type']
    
    # Let's clean the answer a bit for redaction
    def redact(word, reveal_pct=0.0):
        result = []
        for i, char in enumerate(word):
            if not char.isalnum():
                result.append(char)
            elif reveal_pct >= 0.5:
                # Reveal first, last, and every even position
                if i == 0 or i == len(word) - 1 or i % 2 == 0:
                    result.append(char)
                else:
                    result.append("`_`")
            elif reveal_pct >= 0.25:
                # Reveal first and last letter
                if i == 0 or i == len(word) - 1:
                    result.append(char)
                else:
                    result.append("`_`")
            else:
                # Redact all
                result.append("`_`")
        return " ".join(result)

    # Continent lookup for flags
    continent_map = {
        "jp": "Asia", "br": "South America", "ca": "North America", "au": "Oceania",
        "za": "Africa", "in": "Asia", "fr": "Europe", "it": "Europe",
        "de": "Europe", "mx": "North America", "es": "Europe", "kr": "Asia",
        "gb": "Europe", "us": "North America", "cn": "Asia",
        "eg": "Africa", "ar": "South America", "ru": "Europe/Asia", "nl": "Europe",
        "se": "Europe", "ch": "Europe", "tr": "Europe/Asia", "gr": "Europe",
        "pt": "Europe", "no": "Europe", "nz": "Oceania", "be": "Europe",
        "ie": "Europe", "ua": "Europe", "pl": "Europe", "fi": "Europe",
        "is": "Europe", "dk": "Europe", "sg": "Asia", "th": "Asia",
        "vn": "Asia", "ph": "Asia", "my": "Asia", "id": "Asia",
        "cl": "South America", "pe": "South America", "co": "South America", "at": "Europe",
        "ro": "Europe", "hu": "Europe", "cz": "Europe", "sk": "Europe"
    }

    if t_type == 'flag':
        code = trivia_info.get('code', '')
        continent = continent_map.get(code, "Unknown")
        
        if hint_count == 1:
            redacted = redact(ans_display, reveal_pct=0.0)
            return f"💡 **Hint #1**: The country is located in **{continent}**.\nLength: {redacted}"
        elif hint_count == 2:
            redacted = redact(ans_display, reveal_pct=0.25)
            return f"💡 **Hint #2**: First and last letters revealed:\n➡️ {redacted}"
        else:
            redacted = redact(ans_display, reveal_pct=0.5)
            return f"💡 **Hint #3**: More letters revealed:\n➡️ {redacted}"
            
    elif t_type == 'backwards':
        unreversed = ans_display[::-1]
        
        if hint_count == 1:
            redacted = redact(ans_display, reveal_pct=0.0)
            return f"💡 **Hint #1**: The reversed text has {len(ans_display)} letters:\n➡️ {redacted}"
        elif hint_count == 2:
            redacted = redact(ans_display, reveal_pct=0.25)
            return f"💡 **Hint #2**: First and last letters of the reversed word:\n➡️ {redacted}"
        else:
            return f"💡 **Hint #3**: The ORIGINAL word/phrase starts with **'{unreversed[0].upper()}'** and ends with **'{unreversed[-1].lower()}'**."
            
    else: # funny trivia question
        if hint_count == 1:
            redacted = redact(ans_display, reveal_pct=0.0)
            return f"💡 **Hint #1**: The answer length is:\n➡️ {redacted}"
        elif hint_count == 2:
            redacted = redact(ans_display, reveal_pct=0.25)
            return f"💡 **Hint #2**: First and last letters:\n➡️ {redacted}"
        else:
            redacted = redact(ans_display, reveal_pct=0.5)
            return f"💡 **Hint #3**: More letters revealed:\n➡️ {redacted}"

async def run_trivia_timeout(channel, message, duration=180):
    try:
        await asyncio.sleep(duration)
        if channel.id in active_trivia and active_trivia[channel.id].get('message_id') == message.id:
            trivia_info = active_trivia[channel.id]
            answer_display = trivia_info['answer_display']
            is_loop = trivia_info.get('loop', False)
            
            del active_trivia[channel.id]
            
            try:
                await message.delete()
            except Exception:
                pass
                
            embed = discord.Embed(
                description=f"⏳ **Nobody guessed right! Correct answer:** **{answer_display}**",
                color=0xE74C3C
            )
            try:
                timeout_msg = await channel.send(embed=embed)
                
                async def delete_after_delay(msg, delay=60):
                    await asyncio.sleep(delay)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                client.loop.create_task(delete_after_delay(timeout_msg))
            except Exception:
                pass
                
            if is_loop:
                if channel.id in active_flag_loops:
                    async def next_flag_after_delay():
                        await asyncio.sleep(3)
                        if channel.id in active_flag_loops:
                            await send_random_flag_question(channel, is_loop=True)
                    client.loop.create_task(next_flag_after_delay())
                elif channel.id in active_trivia_loops:
                    async def next_trivia_after_delay():
                        await asyncio.sleep(3)
                        if channel.id in active_trivia_loops:
                            await send_random_trivia_question(channel, is_loop=True)
                    client.loop.create_task(next_trivia_after_delay())
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error in trivia timeout task: {e}")

async def send_random_flag_question(channel, is_loop=False):
    import random
    import time
    flag_tuple = random.choice(flags_trivia)
    code, country_name = flag_tuple
    
    answers = [country_name]
    if country_name == "united states":
        answers.extend(["us", "usa", "america"])
    elif country_name == "united kingdom":
        answers.extend(["uk", "britain", "england"])
    elif country_name == "south korea":
        answers.extend(["korea"])
    elif country_name == "south africa":
        answers.extend(["sa"])
    elif country_name == "north korea":
        answers.extend(["korea", "dprk"])
    elif country_name == "saudi arabia":
        answers.extend(["saudi"])
    elif country_name == "sri lanka":
        answers.extend(["sri-lanka"])
        
    active_trivia[channel.id] = {
        'type': 'flag',
        'code': code,
        'answers': answers,
        'answer_display': country_name.title(),
        'loop': is_loop,
        'message_id': None,
        'timeout_task': None,
        'start_time': time.time()
    }
    
    is_difficult = code in {"bt", "kg", "sz", "np", "lk", "kh", "bn", "er", "ls", "ad"}
    title_suffix = " 🌶️ [DIFFICULT]" if is_difficult else ""
    xp_to_win = 50 if is_difficult else 25
    
    embed = discord.Embed(
        title=f"🌍 TRIVIA: GUESS THE FLAG!{title_suffix}",
        description="Which country does this flag belong to?\n\n*Type the name of the country in the chat to answer!*",
        color=0xE74C3C if is_difficult else 0x3498DB
    )
    if is_loop:
        embed.set_footer(text=f"🔄 Flag Loop Mode Active ({xp_to_win} XP) • Type v!triviastop to end")
    else:
        embed.set_footer(text=f"Type the correct answer to win {xp_to_win} XP!")
        
    image_bytes = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://flagcdn.com/w320/{code}.png") as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
    except Exception as e:
        print(f"Error fetching flag image: {e}")
        
    msg = None
    if image_bytes:
        file = discord.File(io.BytesIO(image_bytes), filename="flag.png")
        embed.set_image(url="attachment://flag.png")
        msg = await channel.send(embed=embed, file=file)
    else:
        embed.description = "Which country does this flag belong to?\n\n⚠️ **[Image failed to download]**\n\n*Type the name of the country in the chat to answer!*"
        msg = await channel.send(embed=embed)
        
    if msg and channel.id in active_trivia:
        active_trivia[channel.id]['message_id'] = msg.id
        task = client.loop.create_task(run_trivia_timeout(channel, msg))
        active_trivia[channel.id]['timeout_task'] = task

async def send_random_trivia_question(channel, is_loop=False):
    import random
    import time
    q_type = random.choice(["backwards", "flag", "funny"])
    
    if q_type == "backwards":
        target_word = random.choice(words_to_reverse)
        reversed_word = target_word[::-1]
        
        active_trivia[channel.id] = {
            'type': 'backwards',
            'answers': [reversed_word],
            'answer_display': reversed_word,
            'loop': is_loop,
            'message_id': None,
            'timeout_task': None,
            'start_time': time.time()
        }
        
        embed = discord.Embed(
            title="💬 TRIVIA: TYPE THIS BACKWARDS!",
            description=f"Type the following phrase backwards as fast as you can!\n\n➡️ **`{target_word}`**\n\n*Type the reversed string in the chat to answer!*",
            color=0x9B59B6
        )
        if is_loop:
            embed.set_footer(text="🔄 Trivia Loop Mode Active (25 XP) • Type v!triviastop to end")
        else:
            embed.set_footer(text="✍️ Type the correct reversed phrase to win 25 XP!")
        msg = await channel.send(embed=embed)
        if msg and channel.id in active_trivia:
            active_trivia[channel.id]['message_id'] = msg.id
            task = client.loop.create_task(run_trivia_timeout(channel, msg))
            active_trivia[channel.id]['timeout_task'] = task
        
    elif q_type == "flag":
        await send_random_flag_question(channel, is_loop=is_loop)
        
    else:
        q_tuple = random.choice(funny_questions)
        question, answers = q_tuple
        
        active_trivia[channel.id] = {
            'type': 'funny',
            'answers': answers,
            'answer_display': answers[0].title(),
            'loop': is_loop,
            'message_id': None,
            'timeout_task': None,
            'start_time': time.time()
        }
        
        embed = discord.Embed(
            title="🧠 TRIVIA: FUNNY QUESTION!",
            description=f"Answer this question correctly to win!\n\n❓ **{question}**\n\n*Type your answer in the chat!*",
            color=0xE67E22
        )
        if is_loop:
            embed.set_footer(text="🔄 Trivia Loop Mode Active (25 XP) • Type v!triviastop to end")
        else:
            embed.set_footer(text="💡 Type the correct answer to win 25 XP!")
        msg = await channel.send(embed=embed)
        if msg and channel.id in active_trivia:
            active_trivia[channel.id]['message_id'] = msg.id
            task = client.loop.create_task(run_trivia_timeout(channel, msg))
            active_trivia[channel.id]['timeout_task'] = task

async def trivia_interval_loop(channel, interval_minutes):
    try:
        while True:
            await asyncio.sleep(interval_minutes * 60)
            await send_random_trivia_question(channel)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error in trivia interval loop: {e}")

def get_daily_reward(user_id, guild_id):
    row = db_fetchone("SELECT last_claim, streak FROM daily_rewards WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    return row if row else (0.0, 0)

def claim_daily_reward(user_id, guild_id, streak):
    now_ts = datetime.datetime.now().timestamp()
    db_execute("INSERT INTO daily_rewards (guild_id, user_id, last_claim, streak) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET last_claim = ?, streak = ?", (guild_id, user_id, now_ts, streak, now_ts, streak))

def get_event_cooldown(guild_id):
    row = db_fetchone("SELECT last_triggered FROM event_cooldowns WHERE guild_id = ?", (guild_id,))
    return row[0] if row else 0.0

def set_event_cooldown(guild_id, timestamp):
    db_execute("INSERT INTO event_cooldowns (guild_id, last_triggered) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET last_triggered = ?", (guild_id, timestamp, timestamp))

def get_blacklisted_words(guild_id):
    rows = db_fetchall("SELECT word FROM blacklisted_words WHERE guild_id = ?", (guild_id,))
    return [row[0] for row in rows]

def add_blacklisted_word(guild_id, word):
    try:
        db_execute("INSERT INTO blacklisted_words (guild_id, word) VALUES (?, ?)", (guild_id, word.lower()))
        return True
    except Exception:
        return False

def remove_blacklisted_word(guild_id, word):
    return db_execute("DELETE FROM blacklisted_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower())) > 0

def set_autorole(guild_id, role_id):
    db_execute("INSERT INTO autoroles (guild_id, role_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET role_id = EXCLUDED.role_id", (guild_id, role_id))

def get_autorole(guild_id):
    row = db_fetchone("SELECT role_id FROM autoroles WHERE guild_id = ?", (guild_id,))
    return row[0] if row else None

def disable_autorole(guild_id):
    return db_execute("DELETE FROM autoroles WHERE guild_id = ?", (guild_id,)) > 0

def add_auto_responder(guild_id, trigger, response):
    db_execute("INSERT INTO auto_responders (guild_id, trigger, response) VALUES (?, ?, ?) ON CONFLICT (guild_id, trigger) DO UPDATE SET response = EXCLUDED.response", (guild_id, trigger.lower(), response))

def remove_auto_responder(guild_id, trigger):
    return db_execute("DELETE FROM auto_responders WHERE guild_id = ? AND trigger = ?", (guild_id, trigger.lower())) > 0

def get_auto_responder(guild_id, trigger):
    # Try exact match first
    row = db_fetchone("SELECT response FROM auto_responders WHERE guild_id = ? AND trigger = ?", (guild_id, trigger.lower().strip()))
    if row:
        return row[0]
    
    # Try to find a trigger that exists as a whole word in the message content
    all_responders = db_fetchall("SELECT trigger, response FROM auto_responders WHERE guild_id = ?", (guild_id,))
    for trig, response in all_responders:
        pattern = r"\b" + re.escape(trig.lower()) + r"\b"
        if re.search(pattern, trigger.lower()):
            return response
    return None

def get_all_auto_responders(guild_id):
    return db_fetchall("SELECT trigger, response FROM auto_responders WHERE guild_id = ?", (guild_id,))

def set_log_channel(guild_id, channel_id):
    db_execute("INSERT INTO log_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (guild_id, channel_id))

def disable_log_channel(guild_id):
    return db_execute("DELETE FROM log_channels WHERE guild_id = ?", (guild_id,)) > 0



def get_modmail_channel(guild_id):
    res = db_fetchone("SELECT channel_id FROM modmail_channels WHERE guild_id = ?", (guild_id,))
    return res[0] if res else None

def set_modmail_channel(guild_id, channel_id):
    db_execute("INSERT INTO modmail_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (guild_id, channel_id))

def disable_modmail_channel(guild_id):
    db_execute("DELETE FROM modmail_channels WHERE guild_id = ?", (guild_id,))

def get_welcome_channel(guild_id):
    res = db_fetchone("SELECT channel_id FROM welcome_channels WHERE guild_id = ?", (guild_id,))
    return res[0] if res else None

def set_welcome_channel(guild_id, channel_id):
    db_execute("INSERT INTO welcome_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (guild_id, channel_id))

def disable_welcome_channel(guild_id):
    db_execute("DELETE FROM welcome_channels WHERE guild_id = ?", (guild_id,))

def get_log_channel(guild_id):
    row = db_fetchone("SELECT channel_id FROM log_channels WHERE guild_id = ?", (guild_id,))
    return row[0] if row else None

def set_afk_user(user_id, reason, timestamp):
    db_execute("INSERT INTO afk_users (user_id, reason, timestamp) VALUES (?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason, timestamp = EXCLUDED.timestamp", (str(user_id), reason, timestamp))

def get_afk_user(user_id):
    return db_fetchone("SELECT reason, timestamp FROM afk_users WHERE user_id = ?", (str(user_id),))

def remove_afk_user(user_id):
    db_execute("DELETE FROM afk_users WHERE user_id = ?", (str(user_id),))

def get_restricted_commands(guild_id):
    rows = db_fetchall("SELECT command_name FROM restricted_commands WHERE guild_id = ?", (guild_id,))
    return {row[0] for row in rows}

def toggle_banping_role(guild_id, role_id):
    if db_fetchone("SELECT 1 FROM banping_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)):
        db_execute("DELETE FROM banping_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))
        return False
    else:
        db_execute("INSERT INTO banping_roles (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id))
        return True

def get_banping_roles(guild_id):
    rows = db_fetchall("SELECT role_id FROM banping_roles WHERE guild_id = ?", (guild_id,))
    return {row[0] for row in rows}

def parse_duration(time_str: str) -> int:
    total_seconds = 0
    matches = re.findall(r"(\d+)\s*([smhd])", time_str.lower())
    if not matches:
        if time_str.isdigit():
            return int(time_str)
        return 0
    
    for amount_str, unit in matches:
        amount = int(amount_str)
        if unit == "s":
            total_seconds += amount
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "d":
            total_seconds += amount * 86400
            
    return total_seconds

# --- HELPERS FOR EMBEDS ---
async def send_embed(channel, text, title=None, color=0x3498DB):
    embed = discord.Embed(description=text, color=color)
    if title:
        embed.title = title
    return await channel.send(embed=embed)

async def send_error_embed(channel, text):
    return await send_embed(channel, text, color=0xE74C3C)

async def check_perms(message, **perms):
    # Bot owner bypasses permissions!
    is_owner = message.author.name == "a8o4"
    if not is_owner:
        try:
            if hasattr(client, 'owner_id') and client.owner_id == message.author.id:
                is_owner = True
            elif hasattr(client, 'owner_ids') and client.owner_ids and message.author.id in client.owner_ids:
                is_owner = True
            elif hasattr(client, 'is_owner'):
                is_owner = await client.is_owner(message.author)
        except Exception:
            pass

    if is_owner or message.author.guild_permissions.administrator:
        return True
    user_perms = message.channel.permissions_for(message.author)
    for perm, value in perms.items():
        if getattr(user_perms, perm) != value:
            await send_error_embed(message.channel, f"❌ You lack the permission: `{perm}`")
            return False
    return True

async def send_access_denied(channel, role_needed):
    return await send_error_embed(channel, f"🔒 This command requires **{role_needed}** permissions.")

async def resolve_member(guild, arg_str, message_mentions=None):
    if message_mentions:
        for m in message_mentions:
            if m.mention in arg_str or str(m.id) in arg_str:
                return m
    # Clean ID
    clean_id = "".join(c for c in arg_str if c.isdigit())
    if clean_id:
        try:
            m_id = int(clean_id)
            member = guild.get_member(m_id)
            if not member:
                member = await guild.fetch_member(m_id)
            return member
        except Exception:
            pass
    return None

def resolve_role(guild, arg_str, message_role_mentions=None):
    if message_role_mentions:
        for r in message_role_mentions:
            if r.mention in arg_str or str(r.id) in arg_str:
                return r
    clean_id = "".join(c for c in arg_str if c.isdigit())
    if clean_id:
        try:
            r_id = int(clean_id)
            return guild.get_role(r_id)
        except Exception:
            pass
    return None

async def log_action(guild, embed):
    row = db_fetchone("SELECT channel_id FROM log_channels WHERE guild_id = ?", (guild.id,))
    if row:
        channel = guild.get_channel(row[0])
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

async def send_punishment_dm(user, guild, action, duration_str=None, reason="None"):
    try:
        msg = f"⚠️ You have been **{action}ed** from **{guild.name}**."
        if duration_str:
            msg += f"\n**Duration:** {duration_str}"
        msg += f"\n**Reason:** {reason}"
        await user.send(msg)
        return True
    except Exception:
        return False

def generate_progress_bar(current, total, length=10):
    percent = max(0.0, min(1.0, current / total))
    filled = int(percent * length)
    bar = "█" * filled + "░" * (length - filled)
    return bar

# --- AUTOMATION EVENT: AUTOMATED ROLE ASSIGNMENT ---
@client.event
async def on_member_join(member):
    welcome_ch_id = get_welcome_channel(member.guild.id)
    if welcome_ch_id:
        welcome_ch = member.guild.get_channel(welcome_ch_id)
        if welcome_ch and welcome_ch.permissions_for(member.guild.me).send_messages:
            embed = discord.Embed(
                title=f"Welcome to {member.guild.name}!",
                description=f"Hey {member.mention}, we are glad to have you here! 🎉\nThere are now **{member.guild.member_count}** members in the server.",
                color=0x2ECC71
            )
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await welcome_ch.send(embed=embed)
            except Exception:
                pass

    role_id = get_autorole(member.guild.id)
    if role_id:
        role = member.guild.get_role(role_id)
        if role:
            try:
                await member.add_roles(role, reason="Auto-role configuration on member join.")
            except Exception:
                pass

# --- INTERACTIVE PAGINATION VIEW ---

class MockMessage:
    def __init__(self, interaction: discord.Interaction, content: str = ""):
        self.interaction = interaction
        self.author = interaction.user
        self.channel = interaction.channel
        self.guild = interaction.guild
        self.content = content
        self.mentions = []
        self.id = interaction.id
        self.created_at = interaction.created_at
        self.attachments = []
        if ' <@' in content:
            # Not fully supported but enough for mock
            pass
            
    async def reply(self, *args, **kwargs):
        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(*args, **kwargs)
        else:
            await self.interaction.followup.send(*args, **kwargs)
            
    async def add_reaction(self, emoji):
        pass
    async def delete(self, *args, **kwargs):
        pass

async def invoke_command(interaction: discord.Interaction, command_name: str, args: str = ""):
    message = MockMessage(interaction, f"v!{command_name} {args}".strip())
    # We trigger on_message? No, on_message ignores bots, but our mock message author is a real user.
    # We can just call on_message directly!
    await on_message(message)

class HelpDropdown(discord.ui.Select):
    def __init__(self, embeds):
        options = []
        for i, embed in enumerate(embeds):
            title = embed.title or "Help Page"
            emoji = None
            label = title
            if "Fun" in title:
                emoji = "🎮"
                label = "Fun & Games"
            elif "Utilities" in title:
                emoji = "👥"
                label = "Utilities & Social"
            elif "Moderation" in title:
                emoji = "🛡️"
                label = "Moderation"
            elif "Automation" in title:
                emoji = "⚙️"
                label = "Automation Settings"
            elif "Admin" in title:
                emoji = "🛠️"
                label = "Admin Advanced"
            elif "custom" in title.lower():
                emoji = "☄️"
                label = "Custom Commands"
            elif "info" in title.lower() or "overview" in title.lower():
                emoji = "ℹ️"
                label = "Bot Information"
            
            # Extract page footer
            desc = f"Section {i+1}"
            options.append(discord.SelectOption(label=label, value=str(i), emoji=emoji, description=desc))
            
        super().__init__(placeholder="Select help category...", min_values=1, max_values=1, options=options, custom_id="help_select", row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id != view.author.id: # type: ignore
            await interaction.response.send_message("❌ Only the person who ran the command can use this menu.", ephemeral=True)
            return
        page_idx = int(self.values[0])
        view.current_page = page_idx # type: ignore
        await view.update_page(interaction) # type: ignore





class EightBallView(discord.ui.View):
    def __init__(self, author, question):
        super().__init__(timeout=60.0)
        self.author = author
        self.question = question
        self.pos = ["It is certain.", "Yes, definitely.", "You may rely on it.", "Outlook good.", "Signs point to yes."]
        self.neu = ["Reply hazy, try again.", "Ask again later.", "Better not tell you now."]
        self.neg = ["Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]

    @discord.ui.button(label="Ask Again", style=discord.ButtonStyle.secondary, emoji="🎱")
    async def ask_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("This isn't your 8-ball!", ephemeral=True)
        import random
        result = random.choice(self.pos + self.neu + self.neg)
        embed = interaction.message.embeds[0]
        embed.color = 0x2ECC71 if result in self.pos else (0xE74C3C if result in self.neg else 0xF1C40F)
        embed.set_field_at(1, name="Answer", value=f"**{result}**", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)


class RollDiceView(discord.ui.View):
    def __init__(self, author, low, high):
        super().__init__(timeout=60.0)
        self.author = author
        self.low = low
        self.high = high

    @discord.ui.button(label="Roll Again", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("This isn't your dice!", ephemeral=True)
        import random
        val = random.randint(self.low, self.high)
        embed = interaction.message.embeds[0]
        embed.set_field_at(1, name="Result", value=f"**{val}**", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)



class WelcomeView(discord.ui.View):
    def __init__(self, prefix):
        super().__init__(timeout=None)
        self.prefix = prefix
        
        self.add_item(discord.ui.Button(
            label="Support Server",
            style=discord.ButtonStyle.link,
            url="https://discord.com",
            emoji="💬"
        ))

    @discord.ui.button(label="Commands List", style=discord.ButtonStyle.secondary, custom_id="welcome_help_btn", emoji="📚")
    async def commands_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        pages = get_help_pages(interaction.guild.id if interaction.guild else 0, self.prefix)
        view = CommandPaginationView(interaction.user, pages)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    @discord.ui.button(label="Quick Setup Guide", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def setup_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Quick Setup Guide",
            description=(
                f"**1. Change Prefix**\n"
                f"`{self.prefix}prefix <new_prefix>`\n\n"
                f"**2. Set Log Channel**\n"
                f"`{self.prefix}setlogchannel #channel` (For mod logs)\n\n"
                f"**3. Enable AI Chat**\n"
                f"`{self.prefix}aistart` (Creates an AI chat channel)\n\n"
                f"**4. Add Custom Commands**\n"
                f"`{self.prefix}addcmd <trigger> <reply>`"
            ),
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class CoinFlipView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=60.0)
        self.author = author

    @discord.ui.button(label="Flip Again", style=discord.ButtonStyle.primary, emoji="🪙")
    async def flip_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("This isn't your coin flip!", ephemeral=True)
        import random
        result = random.choice(['HEADS', 'TAILS'])
        embed = interaction.message.embeds[0]
        embed.set_field_at(0, name="Result", value=f"**{result}**", inline=True)
        await interaction.response.edit_message(embed=embed, view=self)



import uuid
import asyncio

modmail_tickets = {}
modmail_active_claims = {}

async def delete_message_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

async def update_other_modmail_messages(ticket_id, current_msg_id, user):
    ticket = modmail_tickets.get(ticket_id)
    if not ticket: return
    for ch_id, msg_id in ticket['messages']:
        if msg_id == current_msg_id: continue
        try:
            ch = client.get_channel(ch_id)
            if not ch:
                user_obj = await client.fetch_user(ch_id)
                if user_obj:
                    ch = await user_obj.create_dm()
            
            if ch:
                msg = await ch.fetch_message(msg_id)
                embed = msg.embeds[0]
                embed.color = 0xF1C40F
                if not embed.fields or embed.fields[-1].name != "Status":
                    embed.add_field(name="Status", value=f"Claimed by {user.mention}")
                view = discord.ui.View()
                btn = discord.ui.Button(label=f"Claimed by {user.display_name}", style=discord.ButtonStyle.success, disabled=True, emoji="✋")
                view.add_item(btn)
                await msg.edit(embed=embed, view=view)
                client.loop.create_task(delete_message_later(msg, 180))
        except Exception:
            pass

class ModMailClaimView(discord.ui.View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="✋")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = modmail_tickets.get(self.ticket_id)
        if not ticket:
            return await interaction.response.send_message("This ticket is no longer active.", ephemeral=True)
            
        if ticket['claimed_by']:
            return await interaction.response.send_message(f"This ticket was already claimed by <@{ticket['claimed_by']}>.", ephemeral=True)
            
        ticket['claimed_by'] = interaction.user.id
        modmail_active_claims[interaction.user.id] = self.ticket_id
        
        embed = interaction.message.embeds[0]
        embed.color = 0xF1C40F
        embed.add_field(name="Status", value=f"Claimed by {interaction.user.mention}")
        
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        client.loop.create_task(update_other_modmail_messages(self.ticket_id, interaction.message.id, interaction.user))
        
        try:
            target_user = await client.fetch_user(ticket['user_id'])
            await interaction.user.send(f"✅ You claimed the ticket from **{target_user.display_name}**.\n**Reply to this message** with your response to send it to them.")
        except Exception:
            pass
            
        client.loop.create_task(delete_message_later(interaction.message, 180))





class CommandPaginationView(discord.ui.View):
    def __init__(self, author, embeds):
        super().__init__(timeout=120.0)
        self.author = author
        self.embeds = embeds
        self.current_page = 0
        self.message: discord.Message | None = None
        
        # Add the dropdown
        self.add_item(HelpDropdown(embeds))
        
        # Reference buttons
        self.btn_first = None
        self.btn_prev = None
        self.btn_next = None
        self.btn_last = None
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "btn_first":
                    self.btn_first = child
                elif child.custom_id == "btn_prev":
                    self.btn_prev = child
                elif child.custom_id == "btn_next":
                    self.btn_next = child
                elif child.custom_id == "btn_last":
                    self.btn_last = child
                    
        self.update_buttons()

    def update_buttons(self):
        total_pages = len(self.embeds)
        if self.btn_first:
            self.btn_first.disabled = self.current_page == 0
        if self.btn_prev:
            self.btn_prev.disabled = self.current_page == 0
        if self.btn_next:
            self.btn_next.disabled = self.current_page == total_pages - 1
        if self.btn_last:
            self.btn_last.disabled = self.current_page == total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these buttons.", ephemeral=True)
            return False
        return True

    async def update_page(self, interaction: discord.Interaction):
        total_pages = len(self.embeds)
        if self.current_page < 0:
            self.current_page = 0
        elif self.current_page >= total_pages:
            self.current_page = total_pages - 1

        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="btn_first", row=0)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self.update_page(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.primary, custom_id="btn_prev", row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_page(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="btn_next", row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_page(interaction)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="btn_last", row=0)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.embeds) - 1
        await self.update_page(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True # type: ignore
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


class RolePaginationView(discord.ui.View):
    def __init__(self, author, embeds):
        super().__init__(timeout=120.0)
        self.author = author
        self.embeds = embeds
        self.current_page = 0
        self.message: discord.Message | None = None
        
        # Reference buttons
        self.btn_first = None
        self.btn_prev = None
        self.btn_next = None
        self.btn_last = None
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "btn_first":
                    self.btn_first = child
                elif child.custom_id == "btn_prev":
                    self.btn_prev = child
                elif child.custom_id == "btn_next":
                    self.btn_next = child
                elif child.custom_id == "btn_last":
                    self.btn_last = child
                    
        self.update_buttons()

    def update_buttons(self):
        total_pages = len(self.embeds)
        if self.btn_first:
            self.btn_first.disabled = self.current_page == 0
        if self.btn_prev:
            self.btn_prev.disabled = self.current_page == 0
        if self.btn_next:
            self.btn_next.disabled = self.current_page == total_pages - 1
        if self.btn_last:
            self.btn_last.disabled = self.current_page == total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Only the person who ran the command can use these buttons.", ephemeral=True)
            return False
        return True

    async def update_page(self, interaction: discord.Interaction):
        total_pages = len(self.embeds)
        if self.current_page < 0:
            self.current_page = 0
        elif self.current_page >= total_pages:
            self.current_page = total_pages - 1

        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="btn_first", row=0)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self.update_page(interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.primary, custom_id="btn_prev", row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_page(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="btn_next", row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_page(interaction)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="btn_last", row=0)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.embeds) - 1
        await self.update_page(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True # type: ignore
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


def get_help_pages(guild_id, guild_prefix):
    import sys
    restricted = get_restricted_commands(guild_id)
    def lk(cmd, label):
        return f"🔒 {label}" if cmd in restricted else label

    # Stats queries
    servers = len(client.guilds)
    members = sum(g.member_count or 0 for g in client.guilds)
    uptime = str(datetime.datetime.now() - BOT_START_TIME).split('.')[0]

    embed_info = discord.Embed(
        title="ℹ️ Help | Bot Info & Overview",
        description=(
            "Welcome to **V!tya**! A highly optimized, feature-rich Discord companion designed for "
            "effortless server management, engagement, and utility.\n\n"
            "Use the navigation buttons or the dropdown menu below to explore all commands."
        ),
        color=0x9B59B6
    )
    embed_info.add_field(
        name="📊 Bot Statistics",
        value=(
            f"**Servers:** {servers}\n"
            f"**Members:** {members}\n"
            f"**Ping:** {round(client.latency * 1000)}ms\n"
            f"**Uptime:** {uptime}\n"
            f"**System Version:** Python v{sys.version.split()[0]} • discord.py v{discord.__version__}\n"
            f"**Prefixes:** `{guild_prefix}` or `/`"
        ),
        inline=False
    )

    embed1 = discord.Embed(title="🎮 Help | Fun & Games", color=0x2ECC71)
    embed1.add_field(name=lk("ping", f"{guild_prefix}ping"), value="Check bot latency.", inline=True)
    embed1.add_field(name=lk("info", f"{guild_prefix}info"), value="View bot stats.", inline=True)
    embed1.add_field(name=lk("roll", f"{guild_prefix}roll [min] [max]"), value="Roll a random number.", inline=True)
    embed1.add_field(name=lk("coin", f"{guild_prefix}coin"), value="Flip a coin.", inline=True)
    embed1.add_field(name=lk("8ball", f"{guild_prefix}8ball <question>"), value="Ask the magic 8-ball.", inline=True)
    embed1.add_field(name=lk("shipchance", f"{guild_prefix}shipchance @u1 @u2"), value="Calculate love chance.", inline=True)
    embed1.add_field(name=lk("chance", f"{guild_prefix}chance <question>"), value="Calculate random %.", inline=True)
    embed1.add_field(name=lk("translate", f"{guild_prefix}translate <text> <lang>"), value="Translate text.", inline=True)
    embed1.add_field(name=lk("math", f"{guild_prefix}math <expr>"), value="Solve an expression.", inline=True)
    embed1.add_field(name=lk("countdown", f"{guild_prefix}countdown <num>"), value="Visual countdown.", inline=True)
    embed1.add_field(name=lk("flag", f"{guild_prefix}flag"), value="Flag guessing game.", inline=True)
    embed1.add_field(name=lk("flagloop", f"{guild_prefix}flagloop"), value="Loop flag guessing.", inline=True)
    embed1.add_field(name=lk("trivia", f"{guild_prefix}trivia <int>"), value="Trivia interval.", inline=True)
    embed1.add_field(name=lk("trivialoop", f"{guild_prefix}trivialoop"), value="Loop trivia questions.", inline=True)
    embed1.add_field(name=lk("triviastop", f"{guild_prefix}triviastop"), value="Stop trivia games.", inline=True)
    embed1.add_field(name=lk("gamechannel", f"{guild_prefix}gamechannel <#c>"), value="Configure game channel.", inline=True)
    embed1.add_field(name=lk("hint", f"{guild_prefix}hint"), value="Get trivia/flag hint.", inline=True)
    embed1.add_field(name=lk("skip", f"{guild_prefix}skip"), value="Skip question (2 votes).", inline=True)
    embed1.add_field(name=lk("gametop", f"{guild_prefix}gametop"), value="View top trivia scores.", inline=True)

    embed2 = discord.Embed(title="👥 Help | Utilities & Social", color=0x3498DB)
    embed2.add_field(name=lk("avatar", f"{guild_prefix}avatar [@u]"), value="View user avatar.", inline=True)
    embed2.add_field(name=lk("userinfo", f"{guild_prefix}userinfo [@u]"), value="View user details.", inline=True)
    embed2.add_field(name=lk("serverinfo", f"{guild_prefix}serverinfo"), value="Detailed server info.", inline=True)
    embed2.add_field(name=lk("roleinfo", f"{guild_prefix}roleinfo [@r/page]"), value="View role details or list.", inline=True)
    embed2.add_field(name=lk("mc", f"{guild_prefix}mc"), value="View member count.", inline=True)
    embed2.add_field(name=lk("profile", f"{guild_prefix}profile [@u]"), value="View rep and bio.", inline=True)
    embed2.add_field(name=lk("setbio", f"{guild_prefix}setbio <text>"), value="Edit your profile bio.", inline=True)
    embed2.add_field(name=lk("rep", f"{guild_prefix}rep @u 1/-1"), value="Give reputation.", inline=True)
    embed2.add_field(name=lk("reptop", f"{guild_prefix}reptop"), value="Top 10 reputation.", inline=True)
    embed2.add_field(name=lk("level", f"{guild_prefix}level [@u]"), value="Check your level.", inline=True)
    embed2.add_field(name=lk("leveltop", f"{guild_prefix}leveltop"), value="Top 10 levels.", inline=True)
    embed2.add_field(name=lk("daily", f"{guild_prefix}daily"), value="Claim daily XP.", inline=True)
    embed2.add_field(name=lk("afk", f"{guild_prefix}afk [reason]"), value="Set AFK status.", inline=True)
    embed2.add_field(name=lk("sleep", f"{guild_prefix}sleep <time>"), value="Self-mute focus mode.", inline=True)
    embed2.add_field(name=lk("snipe", f"{guild_prefix}snipe [page]"), value="Recover deleted msg.", inline=True)
    embed2.add_field(name=lk("remindme", f"{guild_prefix}remindme <time> <msg>"), value="Set a reminder.", inline=True)
    embed2.add_field(name=lk("aistart", f"{guild_prefix}aistart"), value="Check AI chat channel.", inline=True)
    embed2.add_field(name=lk("poll", f"{guild_prefix}poll <q> [| o1...]"), value="Create a poll.", inline=True)

    embed3 = discord.Embed(title="🛡️ Help | Moderation", color=0xE74C3C)
    embed3.add_field(name=f"{guild_prefix}say <text>", value="Make the bot speak.", inline=True)
    embed3.add_field(name=f"{guild_prefix}embed <title=...>", value="Build custom embed.", inline=True)
    embed3.add_field(name=f"{guild_prefix}purge <num>", value="Delete messages.", inline=True)
    embed3.add_field(name=f"{guild_prefix}mute @u <time>", value="Timeout a user.", inline=True)
    embed3.add_field(name=f"{guild_prefix}unmute @u", value="Remove a timeout.", inline=True)
    embed3.add_field(name=f"{guild_prefix}kick @u [reason]", value="Kick a user.", inline=True)
    embed3.add_field(name=f"{guild_prefix}ban @u [reason]", value="Ban a user.", inline=True)
    embed3.add_field(name=f"{guild_prefix}unban <id>", value="Unban by ID.", inline=True)
    embed3.add_field(name=f"{guild_prefix}warn @u [reason]", value="Warn a user.", inline=True)
    embed3.add_field(name=f"{guild_prefix}warns @u", value="View user warnings.", inline=True)
    embed3.add_field(name=f"{guild_prefix}case <id>", value="View warning details.", inline=True)
    embed3.add_field(name=f"{guild_prefix}delcase <id>", value="Delete warning case.", inline=True)
    embed3.add_field(name=f"{guild_prefix}delwarn @u #", value="Delete specific warn.", inline=True)
    embed3.add_field(name=f"{guild_prefix}delwarnsall @u", value="Clear all user warns.", inline=True)
    embed3.add_field(name=f"{guild_prefix}slowmode <sec>", value="Channel slowmode delay.", inline=True)
    embed3.add_field(name=f"{guild_prefix}addsnippet <n> | <i>", value="Add modmail snippet.", inline=True)
    embed3.add_field(name=f"{guild_prefix}snippets", value="List modmail snippets.", inline=True)
    embed3.add_field(name=f"{guild_prefix}event", value="Trigger a server event (Admin).", inline=True)

    embed4 = discord.Embed(title="⚙️ Help | Automation Settings", color=0xF1C40F)
    embed4.add_field(name=f"{guild_prefix}blacklistword <w>", value="Add word to filter.", inline=True)
    embed4.add_field(name=f"{guild_prefix}unblacklistword <w>", value="Remove word from filter.", inline=True)
    embed4.add_field(name=f"{guild_prefix}blacklistedwords", value="View all filtered words.", inline=True)
    embed4.add_field(name=f"{guild_prefix}banping @role", value="Auto-mute if role pinged.", inline=True)
    embed4.add_field(name=f"{guild_prefix}autorole @role", value="Give role on join.", inline=True)
    embed4.add_field(name=f"{guild_prefix}massreaction #ch <e>", value="Auto-react to channel.", inline=True)
    embed4.add_field(name=f"{guild_prefix}unmassreaction #ch", value="Stop auto-reacting.", inline=True)
    embed4.add_field(name=f"{guild_prefix}disablelinks", value="Disable all link sending.", inline=True)
    embed4.add_field(name=f"{guild_prefix}allowlinks", value="Allow all link sending.", inline=True)
    embed2.add_field(name=lk("modmail", f"{guild_prefix}modmail <msg>"), value="Send mod-mail.", inline=True)
    embed4.add_field(name=f"{guild_prefix}setmodmail #ch", value="Set mod-mail channel.", inline=True)
    embed4.add_field(name=f"{guild_prefix}disablemodmail", value="Disable mod-mail channel.", inline=True)
    embed4.add_field(name=f"{guild_prefix}welcomech #ch", value="Set welcome channel.", inline=True)
    embed4.add_field(name=f"{guild_prefix}disablewelcome", value="Disable welcomes.", inline=True)
    embed4.add_field(name=f"{guild_prefix}setlogchannel [#ch]", value="Set action log channel.", inline=True)
    embed4.add_field(name=f"{guild_prefix}disablelogchannel", value="Disable action logging.", inline=True)
    embed4.add_field(name=f"{guild_prefix}addresponder <w> | <r>", value="Set auto-response.", inline=True)
    embed4.add_field(name=f"{guild_prefix}delresponder <w>", value="Delete auto-response.", inline=True)
    embed4.add_field(name=f"{guild_prefix}responders", value="List auto-responders.", inline=True)
    embed4.add_field(name=f"{guild_prefix}ai #channel", value="Enable AI chat.", inline=True)
    embed4.add_field(name=f"{guild_prefix}aioff", value="Disable AI chat.", inline=True)
    embed4.add_field(name=f"{guild_prefix}addcmd", value="Add custom command.", inline=True)
    embed4.add_field(name=f"{guild_prefix}addalias", value="Add cmd alias.", inline=True)
    embed4.add_field(name=f"{guild_prefix}delalias", value="Delete cmd alias.", inline=True)
    embed4.add_field(name=f"{guild_prefix}aliases", value="List cmd aliases.", inline=True)
    embed4.add_field(name=f"{guild_prefix}delcmd", value="Delete custom command.", inline=True)

    embed5 = discord.Embed(title="🛠️ Help | Admin Advanced", color=0x9B59B6)
    embed5.add_field(name=f"{guild_prefix}createrole <role> [color] [perms]", value="Create role below your highest role.", inline=True)
    embed5.add_field(name=f"{guild_prefix}addrole @u @r", value="Give a user a role.", inline=True)
    embed5.add_field(name=f"{guild_prefix}delrole @u @r", value="Remove a role from a user.", inline=True)
    embed5.add_field(name=f"{guild_prefix}temprole @u @r <t>", value="Temporary role assignment.", inline=True)
    embed5.add_field(name=f"{guild_prefix}lockdown", value="Lock active channel.", inline=True)
    embed5.add_field(name=f"{guild_prefix}disablereactions", value="Disable reactions.", inline=True)
    embed5.add_field(name=f"{guild_prefix}enablereactions", value="Enable reactions.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botlock", value="Blacklist a channel.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botisolate", value="Whitelist a channel.", inline=True)
    embed5.add_field(name=f"{guild_prefix}restrict <cmd>", value="Lock command to admin.", inline=True)
    embed5.add_field(name=f"{guild_prefix}prefix [new]", value="Change bot prefix.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botname <name>", value="Change bot's server nickname.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botavatar <url>", value="Change bot's avatar.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botreset", value="Reset bot's name & avatar.", inline=True)
    embed5.add_field(name=f"{guild_prefix}botcontrol", value="Start control session.", inline=True)
    embed5.add_field(name=f"{guild_prefix}unbotcontrol", value="End control session.", inline=True)
    embed5.add_field(name=f"{guild_prefix}announce <t> | <m>", value="Send announcement.", inline=True)
    embed5.add_field(name=f"{guild_prefix}aiask <q>", value="Ask AI (Mods).", inline=True)
    embed5.add_field(name=f"{guild_prefix}aireset", value="Reset AI memory.", inline=True)
    embed5.add_field(name=f"{guild_prefix}aisettings", value="Configure AI chat.", inline=True)

    pages = [embed_info, embed1, embed2, embed3, embed4, embed5]
    custom_cmds = db_fetchall("SELECT trigger, response FROM custom_commands WHERE guild_id = ?", (guild_id,))
    if custom_cmds:
        embed0 = discord.Embed(title="☄️ Server's Custom Commands", color=0x8A2BE2)
        for cmd, resp in custom_cmds[:25]:
            short_resp = resp[:100] + "..." if len(resp) > 100 else resp
            embed0.add_field(name=f"{guild_prefix}{cmd}", value=short_resp, inline=True)
        pages.insert(1, embed0)

    total_pages = len(pages)
    avatar_url = client.user.display_avatar.url if client.user and client.user.display_avatar else None
    for idx, page in enumerate(pages):
        page.set_footer(text=f"Page {idx + 1}/{total_pages} • Select category below or use {guild_prefix}help <cmd>")
        if avatar_url:
            page.set_thumbnail(url=avatar_url)

    return pages

async def check_temp_roles():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            now = int(datetime.datetime.now().timestamp())
            expired = db_fetchall("SELECT guild_id, user_id, role_id FROM temp_roles WHERE expiry <= ?", (now,))
            for guild_id, user_id, role_id in expired:
                guild = client.get_guild(guild_id)
                if guild:
                    member = guild.get_member(user_id)
                    if not member:
                        try:
                            member = await guild.fetch_member(user_id)
                        except Exception:
                            pass
                    role = guild.get_role(role_id)
                    if member and role:
                        try:
                            await member.remove_roles(role, reason="Temporary role expired.")
                            log_embed = discord.Embed(
                                title="🕒 Temporary Role Expired",
                                description=f"Removed {role.mention} from {member.mention} (duration completed).",
                                color=0xE74C3C,
                                timestamp=datetime.datetime.now()
                            )
                            await log_action(guild, log_embed)
                        except Exception as e:
                            print(f"Failed to remove expired role: {e}")
                db_execute("DELETE FROM temp_roles WHERE guild_id = ? AND user_id = ? AND role_id = ?", (guild_id, user_id, role_id))
        except Exception as e:
            print(f"Error in check_temp_roles loop: {e}")
        await asyncio.sleep(10)

async def check_reminders():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            now = int(datetime.datetime.now().timestamp())
            expired = db_fetchall("SELECT id, user_id, channel_id, reminder_text FROM reminders WHERE timestamp <= ?", (now,))
            for r_id, user_id, channel_id, reminder_text in expired:
                try:
                    user = client.get_user(user_id) or await client.fetch_user(user_id)
                    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
                    
                    embed = discord.Embed(title="⏰ Reminder", description=reminder_text, color=0xF1C40F, timestamp=datetime.datetime.now())
                    
                    if channel:
                        try:
                            await channel.send(content=f"{user.mention} Reminder!", embed=embed)  # type: ignore
                        except Exception:
                            pass
                    if user:
                        try:
                            await user.send(embed=embed)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Failed to process reminder {r_id}: {e}")
                db_execute("DELETE FROM reminders WHERE id = ?", (r_id,))
        except Exception as e:
            print(f"Error in check_reminders loop: {e}")
        await asyncio.sleep(10)

@client.event
async def on_ready():
    global is_ready_fired
    if is_ready_fired:
        return
    is_ready_fired = True
    
    # Set "Playing" activity status (you can paste a link right next to it or in the status_image_url variable below)
    playing_status = "GTA VI | v!help"
    status_image_url = None
    
    full_status = f"{playing_status} | {status_image_url}" if status_image_url else playing_status
    
    try:
        await client.change_presence(activity=discord.Game(name=full_status))
        print(f"👾 Presence status successfully set to: Playing {full_status}")
    except Exception as e:
        print(f"⚠️ Could not set presence status: {e}")
    
    print(f"System Matrix Live: Logged in as {client.user} (PostgreSQL Database Connected)")
    client.loop.create_task(check_temp_roles())
    client.loop.create_task(check_reminders())



    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} slash commands globally.")


    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

# --- ADVANCED EVENT LISTENER: SNIPING HISTORY RECORDER ---
@client.event
async def on_guild_join(guild):
    # Find a suitable channel to send the welcome message to
    channel = guild.system_channel
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        for c in guild.text_channels:
            if c.permissions_for(guild.me).send_messages:
                channel = c
                break
    if channel:
        try:
            embed = discord.Embed(
                title=f"👋 Hello {guild.name}!",
                description=(
                    f"Thank you for adding me to your server! I am **{client.user.name}**, your new multi-purpose assistant.\n\n"
                    "**Getting Started:**\n"
                    "🔹 **Prefix:** My default prefix is `v!` (Use `v!help` to see all commands)\n"
                    "🔹 **Features:** Moderation, AI Chat, Leveling, Fun, Economy, and Custom Commands!\n"
                    "🔹 **Interactive:** Interactive UI Components (Buttons & Dropdowns)\n"
                    "🔹 **Setup:** Server admins can customize the bot further (e.g., changing the prefix using `v!prefix`).\n\n"
                    "I'm excited to be here and ready to help! 🚀"
                ),
                color=0x2ECC71,
                timestamp=datetime.datetime.now()
            )
            if client.user.avatar:
                embed.set_thumbnail(url=client.user.avatar.url)
            embed.set_footer(text=f"Joined {guild.name}", icon_url=guild.icon.url if guild.icon else None)
            await channel.send(embed=embed, view=WelcomeView("v!"))
        except Exception as e:
            print(f"Failed to send welcome message in guild {guild.id}: {e}")

@client.event
async def on_message_delete(message):
    if message.author.bot:
        return
    if message.content:
        if message.channel.id not in sniped_messages:
            sniped_messages[message.channel.id] = []
        sniped_messages[message.channel.id].insert(0, (message.content, message.author, message.created_at))
        sniped_messages[message.channel.id] = sniped_messages[message.channel.id][:5]

# =========================================================================
#    SLASH COMMANDS - KEPT TO EXACTLY 3 (AS REQUESTED)
# =========================================================================

@tree.command(name="afk", description="Set your AFK status")
async def slash_afk(interaction: discord.Interaction, reason: str = "AFK"):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!afk {reason}")
    await on_message(msg)

@tree.command(name="remind", description="Set a reminder")
async def slash_remind(interaction: discord.Interaction, duration: str, text: str):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!remind {duration} {text}")
    await on_message(msg)

@tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(kick_members=True)
async def slash_warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!warn {user.mention} {reason}")
    msg.mentions = [user]
    await on_message(msg)

@tree.command(name="ban", description="Ban a user")
@app_commands.default_permissions(ban_members=True)
async def slash_ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!ban {user.mention} {reason}")
    msg.mentions = [user]
    await on_message(msg)

@tree.command(name="modmail", description="Send a message to server staff")
async def slash_modmail(interaction: discord.Interaction, message_text: str):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!modmail {message_text}")
    await on_message(msg)

@tree.command(name="addalias", description="Add a custom command alias")
@app_commands.default_permissions(administrator=True)
async def slash_addalias(interaction: discord.Interaction, alias: str, command: str):
    await interaction.response.defer()
    msg = MockMessage(interaction, f"v!addalias {alias} {command}")
    await on_message(msg)


@tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    embed = discord.Embed(description=f"🏓 **Pong!** Latency: {round(client.latency * 1000)}ms", color=0x3498DB)
    await interaction.response.send_message(embed=embed)

@tree.command(name="info", description="Show bot information and statistics")
async def slash_info(interaction: discord.Interaction):
    now_ts = datetime.datetime.now().timestamp()
    cooldown_key = (interaction.guild_id, interaction.user.id)
    if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
        mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
        embed = discord.Embed(description=f"⏳ You are on cooldown! Please wait **{mins}m {secs}s**.", color=0xE74C3C)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    prefix = get_guild_prefix(interaction.guild_id) if interaction.guild_id else "v!"
    pages = get_help_pages(interaction.guild_id, prefix)
    
    help_cooldowns[cooldown_key] = now_ts
    view = CommandPaginationView(interaction.user, pages)
    await interaction.response.send_message(embed=pages[0], view=view)
    view.message = await interaction.original_response()

@tree.command(name="help", description="Show the command list or search for a command")
@app_commands.describe(query="A specific command or keyword to search for")
async def slash_help(interaction: discord.Interaction, query: str | None = None):
    now_ts = datetime.datetime.now().timestamp()
    cooldown_key = (interaction.guild_id, interaction.user.id)
    if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
        mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
        embed = discord.Embed(description=f"⏳ You are on cooldown! Please wait **{mins}m {secs}s**.", color=0xE74C3C)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    prefix = get_guild_prefix(interaction.guild_id) if interaction.guild_id else "v!"
    pages = get_help_pages(interaction.guild_id, prefix)

    if query:
        search_term = query.lower().strip()
        matches = []
        for page in pages:
            for field in page.fields:
                clean_name = field.name.replace("🔒", "").strip()
                if clean_name.lower().startswith(prefix.lower()):
                    clean_name_noprefix = clean_name[len(prefix):].strip()
                else:
                    clean_name_noprefix = clean_name
                
                cmd_part = clean_name_noprefix.split()[0].lower() if clean_name_noprefix else ""
                
                if (search_term in cmd_part or 
                    search_term in clean_name.lower() or 
                    search_term in field.value.lower()):
                    matches.append((page, field))

        if len(matches) == 1:
            page, field = matches[0]
            embed = discord.Embed(
                title=f"🔍 Command Info: {field.name}",
                description=f"Category: **{page.title.split('|')[-1].strip() if '|' in page.title else page.title}**\n\n**Usage & Details:**\n{field.value}",
                color=page.color or 0x3498DB
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
            if client.user and client.user.display_avatar:
                embed.set_thumbnail(url=client.user.display_avatar.url)
            return await interaction.response.send_message(embed=embed)
        elif len(matches) > 1:
            embed = discord.Embed(
                title=f"🔍 Search Results for '{search_term}'",
                description=f"Found **{len(matches)}** matching commands:",
                color=0x2ECC71
            )
            for page, field in matches[:15]:
                embed.add_field(
                    name=field.name,
                    value=f"Category: **{page.title.split('|')[-1].strip() if '|' in page.title else page.title}**\n{field.value}",
                    inline=False
                )
            if len(matches) > 15:
                embed.set_footer(text=f"Showing 15/{len(matches)} matches • Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
            else:
                embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None)
            if client.user and client.user.display_avatar:
                embed.set_thumbnail(url=client.user.display_avatar.url)
            return await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(
                description=f"❌ No commands found matching `{search_term}`. Try `/help` to view the full list.",
                color=0xE74C3C
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

    help_cooldowns[cooldown_key] = now_ts
    view = CommandPaginationView(interaction.user, pages)
    await interaction.response.send_message(embed=pages[0], view=view)
    view.message = await interaction.original_response()


@tree.command(name="aiask", description="Ask the AI a question (Mods only)")
@app_commands.default_permissions(manage_messages=True)
async def slash_aiask(interaction: discord.Interaction, question: str):
    if not interaction.guild:
        return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.moderate_members or interaction.user.guild_permissions.manage_messages):  # type: ignore
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    
    cooldown_left = check_ai_cooldown(interaction.user.id)
    if cooldown_left > 0:
        return await interaction.response.send_message(f"⏱️ **AI Cooldown Active!** Please wait **{cooldown_left:.1f}** seconds before asking again.", ephemeral=True)
        
    await interaction.response.defer()
    update_ai_cooldown(interaction.user.id)
    
    try:
        guild_name = interaction.guild.name
        guild_prefix = get_guild_prefix(interaction.guild_id) if interaction.guild_id else COMMAND_PREFIX
        user_name = interaction.user.display_name
        
        ai_response = await generate_ai_response(
            question, 
            interaction.guild_id, 
            interaction.channel_id,
            guild_name=guild_name,
            guild_prefix=guild_prefix,
            user_name=user_name
        )
        
        if not ai_response:
            ai_response = "⚠️ No response was returned by the AI."
            
        # Check if custom name/avatar exists
        db_bot_name = None
        db_bot_avatar = None
        if interaction.guild_id:
            row = db_fetchone("SELECT bot_name, bot_avatar FROM ai_settings WHERE guild_id = ?", (interaction.guild_id,))
            if row:
                db_bot_name, db_bot_avatar = row

        if db_bot_name or db_bot_avatar:
            await send_custom_ai_response(interaction.channel, ai_response, interaction.guild_id, reply_to_message=None)
            await interaction.followup.send("🤖 **Answer sent above with server identity!**")
        else:
            if len(ai_response) > 2000:
                await interaction.followup.send(ai_response[:2000])
                for chunk in [ai_response[i:i+2000] for i in range(2000, len(ai_response), 2000)]:
                    await interaction.channel.send(chunk)  # type: ignore
            else:
                await interaction.followup.send(ai_response)
    except Exception as e:
        print(f"Error in slash_aiask: {e}")
        traceback.print_exc()
        try:
            await interaction.followup.send(f"⚠️ **An error occurred:** {str(e)}")
        except Exception:
            pass

@tree.command(name="aireset", description="Reset the AI conversation memory for the current channel (Admins only)")
@app_commands.default_permissions(manage_guild=True)
async def slash_aireset(interaction: discord.Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):  # type: ignore
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    db_execute("DELETE FROM ai_chat_history WHERE guild_id = ? AND channel_id = ?", (interaction.guild_id, interaction.channel_id))
    await interaction.response.send_message("🧹 **AI memory for this channel has been cleared!** Starting a fresh conversation.")

@tree.command(name="aisettings", description="Configure AI Chat settings (Admins only)")
@app_commands.default_permissions(manage_guild=True)
async def slash_aisettings(
    interaction: discord.Interaction,
    custom_prompt: str | None = None,
    reset_to_default: bool = False
):
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):  # type: ignore
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        
    guild_id = interaction.guild_id
    if not guild_id:
        return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        
    if reset_to_default:
        db_execute("DELETE FROM ai_settings WHERE guild_id = ?", (guild_id,))
        try:
            await interaction.guild.me.edit(nick=None)  # type: ignore
        except Exception:
            pass
        return await interaction.response.send_message("✅ **AI Settings reset successfully!** Restored default V!tya personality and GPT-4o Mini model.", ephemeral=False)
        
    if custom_prompt is None:
        # Just show current settings
        row = db_fetchone("SELECT custom_prompt, bot_name FROM ai_settings WHERE guild_id = ?", (guild_id,))
        current_model = "gpt-4o-mini"
        current_prompt = "Default V!tya personality (natural, friendly, Discord vibe)"
        current_name = "V!tya"
        if row:
            if row[0]:
                current_prompt = row[0]
            if row[1]:
                current_name = row[1]
                
        embed = discord.Embed(
            title="⚙️ AI Chat & Prompt Settings",
            description="Customize the system instructions (behavior) for the AI chat in this server.",
            color=0x3498DB
        )
        embed.add_field(name="🤖 Active Model", value=f"`{current_model}` (GPT-4o Mini - Main model)", inline=False)
        embed.add_field(name="📛 Customized Name", value=f"`{current_name}`", inline=False)
        
        prompt_display = current_prompt
        if len(prompt_display) > 800:
            prompt_display = prompt_display[:800] + "..."
        embed.add_field(name="📜 System Prompt", value=f"```\n{prompt_display}\n```", inline=False)
        return await interaction.response.send_message(embed=embed)
        
    # Perform updates
    exists = db_fetchone("SELECT 1 FROM ai_settings WHERE guild_id = ?", (guild_id,))
    if not exists:
        db_execute("INSERT INTO ai_settings (guild_id, model, custom_prompt) VALUES (?, ?, ?)", (guild_id, "gpt-4o-mini", None))
        
    msg_parts = []
    if custom_prompt is not None:
        db_execute("UPDATE ai_settings SET custom_prompt = ? WHERE guild_id = ?", (custom_prompt, guild_id))
        msg_parts.append(f"• **System Prompt** updated to:\n```\n{custom_prompt[:200]}...\n```")
        
    msg = "✅ **AI Settings updated!**\n" + "\n".join(msg_parts)
    await interaction.response.send_message(msg)

@tree.command(name="botreset", description="Reset bot's custom nickname and avatar back to default in this server (Admins only)")
@app_commands.default_permissions(manage_guild=True)
async def slash_botreset(interaction: discord.Interaction):
    if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):  # type: ignore
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        
    guild_id = interaction.guild_id
    if not guild_id:
        return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        
    # Reset nickname in guild
    nick_reset = False
    nick_err = None
    try:
        await interaction.guild.me.edit(nick=None)  # type: ignore
        nick_reset = True
    except Exception as e:
        nick_err = str(e)
        
    # Reset database settings
    db_execute("UPDATE ai_settings SET bot_name = NULL, bot_avatar = NULL WHERE guild_id = ?", (guild_id,))
    
    if nick_reset:
        await interaction.response.send_message("✅ **Bot name and avatar reset successfully!** Restored default profile picture and V!tya nickname.")
    else:
        await interaction.response.send_message(f"✅ **Bot name and avatar database settings reset!** (Note: Server nickname could not be reset due to permissions: {nick_err}).")

# =========================================================================
#    MESSAGE LISTENER (Prefix Commands & Auto-Mod)
# =========================================================================
@client.event
async def on_message(message):
    if message.author.bot:
        return
        
    if message.guild is None:
        ticket_id = modmail_active_claims.get(message.author.id)
        if ticket_id and ticket_id in modmail_tickets:
            ticket = modmail_tickets[ticket_id]
            target_user_id = ticket['user_id']
            try:
                target_user = await client.fetch_user(target_user_id)
                guild = client.get_guild(ticket['guild_id'])
                guild_name = guild.name if guild else "Unknown Server"
                

                is_anon = False
                actual_content = message.content
                if actual_content.lower().startswith("anon:"):
                    is_anon = True
                    actual_content = actual_content[5:].strip()
                elif actual_content.lower().startswith("snippet:"):
                    parts = actual_content[8:].strip().split(" ", 1)
                    snip_name = parts[0].lower()
                    snip_row = db_fetchone("SELECT content FROM modmail_snippets WHERE guild_id = %s AND name = %s", (ticket['guild_id'], snip_name))
                    if snip_row:
                        actual_content = snip_row[0]
                        if len(parts) > 1:
                            actual_content += "\n" + parts[1]
                    else:
                        return await send_error_embed(message.channel, f"❌ Snippet `{snip_name}` not found.")

                staff_str = "**Staff replied:**" if is_anon else f"**Staff {message.author.mention} replied:**"
                
                if message.attachments:
                    actual_content += "\n\n**Attachments:**\n" + "\n".join([a.url for a in message.attachments])
                reply_embed = discord.Embed(
                    title=f"📬 Mod-Mail Reply from {guild_name}",
                    description=f"{staff_str}\n{actual_content}",
                    color=0x2ECC71
                )

                await target_user.send(embed=reply_embed)
                await send_embed(message.channel, "✅ Your reply has been sent.", color=0x2ECC71)
            except Exception:
                await send_error_embed(message.channel, "❌ Could not deliver the message. The user might have DMs disabled.")
            except Exception as e:
                await send_error_embed(message.channel, f"❌ Error: {e}")
                
            del modmail_active_claims[message.author.id]
            del modmail_tickets[ticket_id]
        return

    channel_id = message.channel.id
    category_id = getattr(message.channel, "category_id", None)

    # --- MASS REACTION LISTENER ---

    # --- AFK CHECK ---
    row = db_fetchone("SELECT reason FROM afk_users WHERE user_id = %s", (str(message.author.id),))
    if row:
        db_execute("DELETE FROM afk_users WHERE user_id = %s", (str(message.author.id),))
        try:
            await message.channel.send(f"Welcome back {message.author.mention}! I removed your AFK.")
        except: pass

    for user in message.mentions:
        if user.id != message.author.id:
            row = db_fetchone("SELECT reason, timestamp FROM afk_users WHERE user_id = %s", (str(user.id),))
            if row:
                try:
                    await message.channel.send(f"💤 **{user.display_name}** is AFK: {row[0]} (<t:{row[1]}:R>)")
                except: pass

    if channel_id in active_mass_reactions:
        for emoji in active_mass_reactions[channel_id]:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass 

    is_admin = message.author.guild_permissions.administrator
    is_mod = (
        message.author.guild_permissions.moderate_members or 
        message.author.guild_permissions.manage_messages or 
        message.author.guild_permissions.kick_members or 
        message.author.guild_permissions.ban_members or 
        is_admin
    )

    # Bot owner (a8o4) bypasses permissions and constraints
    is_owner = message.author.name == "a8o4"
    if not is_owner:
        try:
            if hasattr(client, 'owner_id') and client.owner_id == message.author.id:
                is_owner = True
            elif hasattr(client, 'owner_ids') and client.owner_ids and message.author.id in client.owner_ids:
                is_owner = True
        except Exception:
            pass

    if is_owner:
        is_admin = True
        is_mod = True

    raw_content = message.content.strip()

    # --- CONTINUOUS BOT CONTROL SESSION INTERCEPTION ---
    if message.author.id in bot_control_sessions and bot_control_sessions[message.author.id] == channel_id:
        guild_prefix = get_guild_prefix(message.guild.id)
        if raw_content.lower().startswith(f"{guild_prefix}unbotcontrol".lower()):
            pass 
        elif raw_content.lower().startswith(guild_prefix.lower()):
            pass 
        else:
            try:
                await message.delete()
            except Exception:
                pass

            files = []
            for attachment in message.attachments:
                try:
                    file_bytes = await attachment.read()
                    fp = io.BytesIO(file_bytes)
                    files.append(discord.File(fp, filename=attachment.filename))
                except Exception as e:
                    print(f"Error copying attachment: {e}")

            if files or message.content:
                await message.channel.send(content=message.content, files=files)
            return

    # --- AUTOMATION / AUTO-MOD: LINKS FILTER ---
    if not is_mod:
        links_disabled = db_fetchone("SELECT 1 FROM system_settings WHERE guild_id = ? AND type = 'links_disabled'", (message.guild.id,)) is not None
        if links_disabled:
            urls = re.findall(r'(https?://\S+)', message.content.lower())
            if urls:
                try:
                    await message.delete()
                    await send_error_embed(message.channel, f"🚨 <@{message.author.id}>, links are currently disabled on this server.")
                    
                    # Log deleted link to modlogs channel
                    log_embed = discord.Embed(
                        title="🚨 Link Deleted",
                        description=f"Message from {message.author.mention} was deleted because links are disabled.",
                        color=0xE74C3C,
                        timestamp=datetime.datetime.now()
                    )
                    log_embed.add_field(name="User", value=f"{message.author} ({message.author.id})", inline=True)
                    log_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                    log_embed.add_field(name="Content", value=message.content, inline=False)
                    await log_action(message.guild, log_embed)
                    return
                except Exception:
                    pass

    # --- AUTO-MOD: BLACKLIST ---
    if not is_mod:
        bad_words = get_blacklisted_words(message.guild.id)
        msg_lower = message.content.lower()
        msg_normalized = re.sub(r'[^a-zA-Z0-9]', '', msg_lower)
        
        triggered_word = None
        for w in bad_words:
            w_lower = w.lower()
            w_normalized = re.sub(r'[^a-zA-Z0-9]', '', w_lower)
            if w_lower in msg_lower or (w_normalized and w_normalized in msg_normalized):
                triggered_word = w
                break
                
        if triggered_word:
            try: 
                await message.delete()
                embed = discord.Embed(title="🚫 Blacklisted Word Detected", description=f"<@{message.author.id}>, your message contained a blacklisted word and was removed.", color=0xE74C3C)
                await message.channel.send(embed=embed, delete_after=10.0)
                return 
            except Exception: 
                pass
            except Exception as e:
                print(f"Blacklist Error: {e}")

    # --- SECURITY LAYER: ANTI-SPAM, ANTI-INVITE & ANTI-MENTION ---
    if not is_admin:
        now = datetime.datetime.now().timestamp()
        user_key = (message.guild.id, message.author.id)
        if user_key not in spam_tracker:
            spam_tracker[user_key] = []
        
        spam_tracker[user_key].append(now)
        spam_tracker[user_key] = [t for t in spam_tracker[user_key] if now - t < 5]

        if len(spam_tracker[user_key]) == 4:
            await send_embed(message.channel, "⚠️ **Warning: Continuing will mute you for 1 minute**", color=0xF1C40F)
        
        elif len(spam_tracker[user_key]) >= 5:
            spam_tracker[user_key] = [] 
            strike = get_and_increment_spam_strike(message.guild.id, message.author.id)
            try:
                await message.delete()
            except Exception:
                pass
            
            try:
                await message.author.timeout(datetime.timedelta(minutes=1), reason="Spamming")
                await send_error_embed(message.channel, f"🔇 <@{message.author.id}> has been timed out for **1 minute** for spamming.")
            except Exception:
                await send_error_embed(message.channel, f"⚠️ <@{message.author.id}> is spamming, but I lack permissions to time them out (role hierarchy or missing permissions).")
            except Exception as e:
                print(f"Spam action failed: {e}")
            return

        invite_pattern = r"(discord\.(gg|io|me|li)\/.+|discord\.com\/invite\/.+)"
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            try:
                await message.delete()
                embed = discord.Embed(title="🚨 Invite Link Removed", description=f"<@{message.author.id}> posted a Discord invite link.", color=15158332)
                await message.channel.send(embed=embed, delete_after=10.0)
                return
            except Exception:
                pass

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
            except Exception:
                pass

        banping_protected = get_banping_roles(message.guild.id)
        if banping_protected and (message.mention_everyone and 0 in banping_protected or any(r.id in banping_protected for r in message.role_mentions)):
            strike = get_and_increment_banping_strike(message.guild.id, message.author.id)
            duration = datetime.timedelta(minutes=min(2 ** (strike - 1), 480))
            try:
                await message.delete()
                await message.author.timeout(duration, reason="Banping violation")
                await send_error_embed(message.channel, f"🔕 <@{message.author.id}> was muted for pinging a protected role (Strike #{strike}).")
                return
            except Exception:
                pass

    # --- AFK & REPUTATION KEYWORDS ---
    guild_prefix_now = get_guild_prefix(message.guild.id)
    is_afk_command = raw_content.lower().startswith((guild_prefix_now + "afk").lower())

    if not is_afk_command:
        if get_afk_user(message.author.id):
            remove_afk_user(message.author.id)
            if isinstance(message.author, discord.Member) and message.author.display_name.startswith("[AFK] "):
                try:
                    await message.author.edit(nick=message.author.display_name[6:] or None)
                except Exception:
                    pass
            await send_embed(message.channel, f"👋 Welcome back, **{message.author.display_name}**! I removed your AFK status.", color=0x2ECC71)

    if message.mentions and not is_afk_command:
        for target in message.mentions:
            target_afk = get_afk_user(target.id)
            if target_afk:
                await send_embed(message.channel, f"📌 **{target.display_name}** is currently AFK: {target_afk[0]} (<t:{target_afk[1]}:R>)", color=0xF39C12)

    THANK_KEYWORDS = {"ty", "tysm", "thanks", "thank you", "thank u", "thx", "спасибо"}
    if not message.author.bot and any(re.search(r"\b" + re.escape(kw) + r"\b", message.content.lower()) for kw in THANK_KEYWORDS):
        target_user = None
        if message.reference and isinstance(message.reference.resolved, discord.Message) and not message.reference.resolved.author.bot and message.reference.resolved.author != message.author:
            target_user = message.reference.resolved.author
        elif message.mentions:
            for u in message.mentions:
                if u != message.author and not u.bot:
                    target_user = u
                    break

        if target_user:
            remaining = get_rep_cooldown_remaining(message.guild.id, message.author.id, target_user.id)
            if remaining <= 0:
                new_total = add_rep(target_user.id, 1, message.guild.id)
                set_rep_cooldown(message.guild.id, message.author.id, target_user.id)
                await send_embed(message.channel, f"✨ **{target_user.display_name}**'s reputation increased! (+1 for helping out) — **{new_total} rep total**", color=0xF1C40F)

    # --- LEVELING SYSTEM (XP) ---
    guild_prefix = get_guild_prefix(message.guild.id)
    if not raw_content.startswith(guild_prefix):
        now_ts = datetime.datetime.now().timestamp()
        if (message.guild.id, message.author.id) not in xp_cooldowns or now_ts - xp_cooldowns.get((message.guild.id, message.author.id), 0) > 60:
            xp_cooldowns[(message.guild.id, message.author.id)] = now_ts
            xp_gain = random.randint(15, 25)
            leveled_up, new_level, new_xp = add_xp(message.author.id, message.guild.id, xp_gain)
            if leveled_up:
                xp_needed = get_xp_required(new_level)
                progress_pct = (new_xp / xp_needed) * 100 if xp_needed > 0 else 0
                bar_length = 15
                filled = max(0, min(bar_length, round((progress_pct / 100) * bar_length)))
                bar = "▰" * filled + "▱" * (bar_length - filled)
                
                embed = discord.Embed(
                    title="✨  LEVEL UP!  ✨", 
                    description=f"Congratulations {message.author.mention}! You've advanced to **Level {new_level}**! 🚀", 
                    color=0xF1C40F
                )
                if message.author.display_avatar:
                    embed.set_thumbnail(url=message.author.display_avatar.url)
                embed.add_field(
                    name=f"📈 Progress to Level {new_level + 1} ({progress_pct:.1f}%)", 
                    value=f"`{bar}`\n**{new_xp:,}** / **{xp_needed:,}** XP", 
                    inline=False
                )
                embed.set_footer(text="Keep typing to level up further! • Well deserved!")
                await message.channel.send(embed=embed)

    # --- AI CHAT CHECK ---
    ai_channel = db_fetchone("SELECT channel_id FROM ai_channels WHERE guild_id = ?", (message.guild.id,))
    guild_prefix = get_guild_prefix(message.guild.id)
    is_ai_channel = ai_channel and message.channel.id == ai_channel[0]

    if is_ai_channel:
        if not raw_content.lower().startswith(guild_prefix.lower()):
            cooldown_left = check_ai_cooldown(message.author.id)
            if cooldown_left > 0:
                try:
                    await message.add_reaction("⏱️")
                except discord.HTTPException:
                    pass
                return
            update_ai_cooldown(message.author.id)
            async with message.channel.typing():
                guild_name = message.guild.name if message.guild else None
                user_name = message.author.display_name
                ai_response = await generate_ai_response(
                    message.clean_content, 
                    message.guild.id, 
                    message.channel.id,
                    guild_name=guild_name,
                    guild_prefix=guild_prefix,
                    user_name=user_name
                )
                await send_custom_ai_response(message.channel, ai_response, message.guild.id, reply_to_message=message)
            return

    # --- TRIVIA ANSWER CHECKER ---
    if message.channel.id in active_trivia:
        trivia_info = active_trivia[message.channel.id]
        user_guess_norm = normalize_trivia_answer(message.content)
        correct = False
        
        # Check against list of acceptable answers
        for ans in trivia_info['answers']:
            if user_guess_norm == normalize_trivia_answer(ans):
                correct = True
                break
                
        if correct:
            # We found the correct answer!
            # Cancel the timeout task if it exists
            if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                trivia_info['timeout_task'].cancel()
                
            # Remove from active trivia immediately to prevent double-answering
            del active_trivia[message.channel.id]
            
            # Determine XP reward based on question type and difficulty
            is_difficult = False
            if trivia_info.get('type') == 'flag':
                code = trivia_info.get('code')
                is_difficult = code in {"bt", "kg", "sz", "np", "lk", "kh", "bn", "er", "ls", "ad"}
                
            xp_reward = 50 if is_difficult else 25
            reward_bonus_text = " (Difficult Flag Bonus!)" if is_difficult else ""
            
            # Award XP
            leveled_up, new_level, new_xp = add_xp(message.author.id, message.guild.id, xp_reward)
            
            # Increment game score
            new_score = add_game_score(message.author.id, message.guild.id, 1)
            
            # Calculate time taken
            import time
            time_taken_str = ""
            if 'start_time' in trivia_info:
                elapsed = time.time() - trivia_info['start_time']
                time_taken_str = f"\n⏱️ Time Taken: **{elapsed:.2f}s**"
            
            embed = discord.Embed(
                title="🎯 CORRECT ANSWER!",
                description=f"🎉 {message.author.mention} got it right!\n➡️ Answer: **{trivia_info['answer_display']}**{time_taken_str}\n\n🎁 Reward: **+{xp_reward} XP**{reward_bonus_text}\n🏆 Game Score: **{new_score}** pts",
                color=0x2ECC71
            )
            await message.channel.send(embed=embed)
            
            # Handle level up if needed
            if leveled_up:
                xp_needed = get_xp_required(new_level)
                progress_pct = (new_xp / xp_needed) * 100 if xp_needed > 0 else 0
                bar_length = 15
                filled = max(0, min(bar_length, round((progress_pct / 100) * bar_length)))
                bar = "▰" * filled + "▱" * (bar_length - filled)
                
                lvl_embed = discord.Embed(
                    title="✨  LEVEL UP!  ✨", 
                    description=f"Congratulations {message.author.mention}! You've advanced to **Level {new_level}**! 🚀", 
                    color=0xF1C40F
                )
                if message.author.display_avatar:
                    lvl_embed.set_thumbnail(url=message.author.display_avatar.url)
                lvl_embed.add_field(
                    name=f"📈 Progress to Level {new_level + 1} ({progress_pct:.1f}%)", 
                    value=f"`{bar}`\n**{new_xp:,}** / **{xp_needed:,}** XP", 
                    inline=False
                )
                lvl_embed.set_footer(text="Keep typing to level up further! • Well deserved!")
                await message.channel.send(embed=lvl_embed)
                
            # If we were in a loop, trigger the next question after 3 seconds
            if trivia_info.get('loop'):
                if message.channel.id in active_flag_loops:
                    async def next_flag_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_flag_loops:
                            await send_random_flag_question(message.channel, is_loop=True)
                    client.loop.create_task(next_flag_after_delay())
                elif message.channel.id in active_trivia_loops:
                    async def next_trivia_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_trivia_loops:
                            await send_random_trivia_question(message.channel, is_loop=True)
                    client.loop.create_task(next_trivia_after_delay())
                
            return

    # --- PREFIX VALIDATION & COMMAND PARSING ---
    if not raw_content.lower().startswith(guild_prefix.lower()):
        # Check auto-responders
        resp = get_auto_responder(message.guild.id, raw_content)
        if resp:
            formatted_resp = resp.replace("{user}", message.author.mention)\
                                 .replace("{username}", message.author.name)\
                                 .replace("{guild}", message.guild.name)\
                                 .replace("{channel}", message.channel.mention)
            try:
                await message.channel.send(formatted_resp)
            except Exception:
                pass
        return

    command_body = raw_content[len(guild_prefix):].strip()
    command_name = command_body.lower().split()[0] if command_body else ""
    if not command_name:
        return

    if not is_admin:
        now_ts = datetime.datetime.now().timestamp()
        cooldown_key = (message.author.id, command_name)
        last_used = command_cooldowns.get(cooldown_key, 0)
        if now_ts - last_used < 10:
            remaining = 10 - (now_ts - last_used)
            return await send_error_embed(message.channel, f"⏳ This command is on cooldown. Please wait **{remaining:.1f}s**.")
        command_cooldowns[cooldown_key] = now_ts

    locked_channels, isolated_vectors = get_lock_and_isolate(message.guild.id)

    if not is_admin:
        if command_name not in ("botlock", "botisolate"):
            if isolated_vectors and (channel_id not in isolated_vectors) and (category_id not in isolated_vectors):
                return
            if channel_id in locked_channels or (category_id and category_id in locked_channels):
                return
        if command_name in get_restricted_commands(message.guild.id):
            return await send_error_embed(message.channel, f"🔒 The `{guild_prefix}{command_name}` command is restricted to administrators.")



    # --- ALIAS RESOLUTION ---
    alias_row = db_fetchone("SELECT command FROM command_aliases WHERE guild_id = %s AND alias = %s", (message.guild.id, command_name))
    if alias_row:
        command_name = alias_row[0]

    # --- CUSTOM COMMANDS ---
    custom_cmd = db_fetchone("SELECT response, color FROM custom_commands WHERE guild_id = ? AND trigger = ?", (message.guild.id, command_name))
    if custom_cmd:
        try:
            col = int(custom_cmd[1], 16)
        except:
            col = 0x2ECC71
        
        args_text = command_body[len(command_name):].strip()
        formatted_resp = custom_cmd[0].replace("{user}", message.author.mention)\
                                      .replace("{username}", message.author.name)\
                                      .replace("{guild}", message.guild.name)\
                                      .replace("{channel}", message.channel.mention)\
                                      .replace("{args}", args_text if args_text else "")
        
        embed = discord.Embed(description=formatted_resp, color=col)
        return await message.channel.send(embed=embed)

    # =========================================================================
    #    PREFIX COMMANDS ROUTING

    if command_name == "addsnippet":
        if not is_admin and not is_mod: return await send_access_denied(message.channel, "Moderator")
        args = command_body[len("addsnippet"):].strip().split(" ", 1)
        if len(args) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addsnippet <name> <content>`")
        name = args[0].lower()
        text = args[1]
        db_execute("INSERT INTO modmail_snippets (guild_id, name, content) VALUES (%s, %s, %s) ON CONFLICT (guild_id, name) DO UPDATE SET content = EXCLUDED.content", (message.guild.id, name, text))
        return await send_embed(message.channel, f"✅ Modmail snippet `{name}` added/updated.")

    if command_name == "delsnippet":
        if not is_admin and not is_mod: return await send_access_denied(message.channel, "Moderator")
        name = command_body[len("delsnippet"):].strip().lower()
        db_execute("DELETE FROM modmail_snippets WHERE guild_id = %s AND name = %s", (message.guild.id, name))
        return await send_embed(message.channel, f"✅ Modmail snippet `{name}` deleted.")

    if command_name == "snippets":
        if not is_admin and not is_mod: return await send_access_denied(message.channel, "Moderator")
        rows = db_fetchall("SELECT name FROM modmail_snippets WHERE guild_id = %s", (message.guild.id,))
        if not rows: return await send_embed(message.channel, "ℹ️ No snippets found.")
        desc = "\n".join([f"`{r[0]}`" for r in rows])
        return await send_embed(message.channel, f"**Modmail Snippets:**\n{desc}")




    if command_name == "addalias":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        args = command_body[len("addalias"):].strip().split()
        if len(args) < 2: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addalias <alias> <command>`")
        alias = args[0].lower()
        cmd_target = args[1].lower()
        db_execute("INSERT INTO command_aliases (guild_id, alias, command) VALUES (%s, %s, %s) ON CONFLICT (guild_id, alias) DO UPDATE SET command = EXCLUDED.command", (message.guild.id, alias, cmd_target))
        return await send_embed(message.channel, f"✅ Alias `{alias}` now points to `{cmd_target}`.")

    if command_name == "delalias":
        if not is_admin: return await send_access_denied(message.channel, "Administrator")
        alias = command_body[len("delalias"):].strip().lower()
        if not alias: return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}delalias <alias>`")
        db_execute("DELETE FROM command_aliases WHERE guild_id = %s AND alias = %s", (message.guild.id, alias))
        return await send_embed(message.channel, f"✅ Alias `{alias}` removed.")

    if command_name == "aliases":
        rows = db_fetchall("SELECT alias, command FROM command_aliases WHERE guild_id = %s", (message.guild.id,))
        if not rows: return await send_embed(message.channel, "ℹ️ No custom aliases set.")
        desc = "\n".join([f"**{r[0]}** -> `{r[1]}`" for r in rows])
        embed = discord.Embed(title="🔗 Command Aliases", description=desc, color=0x3498DB)
        return await message.channel.send(embed=embed)

    # =========================================================================



    if command_name == "announce":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body[len("announce"):].strip()
        if not args or "|" not in args:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}announce <title> | <message>`")
        parts = args.split("|", 1)
        title = parts[0].strip()
        msg_text = parts[1].strip()
        embed = discord.Embed(title=title, description=msg_text, color=0x3498DB)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        await message.channel.send(embed=embed)
        return

    if command_name == "sync":

        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        async with message.channel.typing():
            try:
                tree.copy_global_to(guild=message.guild)
                synced = await tree.sync(guild=message.guild)
                await send_embed(message.channel, f"✅ Synced {len(synced)} slash commands specifically to this server (should appear instantly)!", color=0x2ECC71)
            except Exception as e:
                await send_error_embed(message.channel, f"❌ Failed to sync: {e}")
        return

    if command_name == "aiask":

        if not is_mod:
            return await send_access_denied(message.channel, "Moderator")
        question = command_body[len("aiask"):].strip()
        if not question:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}aiask <question>`")
            
        cooldown_left = check_ai_cooldown(message.author.id)
        if cooldown_left > 0:
            return await send_error_embed(message.channel, f"⏱️ **AI Cooldown Active!** Please wait **{cooldown_left:.1f}** seconds before asking again.")
            
        update_ai_cooldown(message.author.id)
        async with message.channel.typing():
            guild_name = message.guild.name if message.guild else None
            user_name = message.author.display_name
            ai_response = await generate_ai_response(
                question, 
                message.guild.id, 
                message.channel.id,
                guild_name=guild_name,
                guild_prefix=guild_prefix,
                user_name=user_name
            )
            await send_custom_ai_response(message.channel, ai_response, message.guild.id, reply_to_message=message)
        return

    if command_name == "addcmd":
        has_manage_perms = is_admin or message.author.guild_permissions.manage_guild or message.author.guild_permissions.manage_channels
        if not has_manage_perms:
            return await send_access_denied(message.channel, "Administrator or Manage Server / Manage Channels")
        args = command_body[len("addcmd"):].strip().split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addcmd <cmd> <text> [colorhex]`")
        cmd = args[0].lower()
        last_arg = args[-1]
        color = "2ECC71"
        if re.match(r"^#?[0-9a-fA-F]{6}$", last_arg):
            color = last_arg.lstrip("#")
            text = " ".join(args[1:-1])
        else:
            text = " ".join(args[1:])
        if not text:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addcmd <cmd> <text> [colorhex]`")
        db_execute("INSERT INTO custom_commands (guild_id, trigger, response, color) VALUES (?, ?, ?, ?) ON CONFLICT (guild_id, trigger) DO UPDATE SET response = EXCLUDED.response, color = EXCLUDED.color", (message.guild.id, cmd, text, color))
        return await send_embed(message.channel, f"✅ Custom command `{guild_prefix}{cmd}` added/updated.", color=0x2ECC71)

    if command_name == "delcmd":
        has_manage_perms = is_admin or message.author.guild_permissions.manage_guild or message.author.guild_permissions.manage_channels
        if not has_manage_perms:
            return await send_access_denied(message.channel, "Administrator or Manage Server / Manage Channels")
        cmd = command_body[len("delcmd"):].strip().lower()
        if not cmd:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}delcmd <cmd>`")
        db_execute("DELETE FROM custom_commands WHERE guild_id = ? AND trigger = ?", (message.guild.id, cmd))
        return await send_embed(message.channel, f"🗑️ Custom command `{guild_prefix}{cmd}` removed.", color=0xE74C3C)

    if command_name in ("commands", "help"):
        args = command_body.split()
        search_term = None
        if len(args) > 1:
            search_term = " ".join(args[1:]).lower().strip()

        now_ts = datetime.datetime.now().timestamp()
        cooldown_key = (message.guild.id, message.author.id)
        if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
            mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
            return await send_error_embed(message.channel, f"⏳ You are on cooldown! Please wait **{mins}m {secs}s** before calling help again.")
        
        pages = get_help_pages(message.guild.id, guild_prefix)

        if search_term:
            matches = []
            for page in pages:
                for field in page.fields:
                    clean_name = field.name.replace("🔒", "").strip()
                    if clean_name.lower().startswith(guild_prefix.lower()):
                        clean_name_noprefix = clean_name[len(guild_prefix):].strip()
                    else:
                        clean_name_noprefix = clean_name
                    
                    cmd_part = clean_name_noprefix.split()[0].lower() if clean_name_noprefix else ""
                    
                    if (search_term in cmd_part or 
                        search_term in clean_name.lower() or 
                        search_term in field.value.lower()):
                        matches.append((page, field))

            if len(matches) == 1:
                page, field = matches[0]
                embed = discord.Embed(
                    title=f"🔍 Command Info: {field.name}",
                    description=f"Category: **{page.title.split('|')[-1].strip() if '|' in page.title else page.title}**\n\n**Usage & Details:**\n{field.value}",
                    color=page.color or 0x3498DB
                )
                embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
                if client.user and client.user.display_avatar:
                    embed.set_thumbnail(url=client.user.display_avatar.url)
                return await message.channel.send(embed=embed)
            elif len(matches) > 1:
                embed = discord.Embed(
                    title=f"🔍 Search Results for '{search_term}'",
                    description=f"Found **{len(matches)}** matching commands:",
                    color=0x2ECC71
                )
                for page, field in matches[:15]:
                    embed.add_field(
                        name=field.name,
                        value=f"Category: **{page.title.split('|')[-1].strip() if '|' in page.title else page.title}**\n{field.value}",
                        inline=False
                    )
                if len(matches) > 15:
                    embed.set_footer(text=f"Showing 15/{len(matches)} matches • Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
                else:
                    embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
                if client.user and client.user.display_avatar:
                    embed.set_thumbnail(url=client.user.display_avatar.url)
                return await message.channel.send(embed=embed)
            else:
                return await send_error_embed(message.channel, f"❌ No commands found matching `{search_term}`. Try `{guild_prefix}help` to view the full list.")

        help_cooldowns[cooldown_key] = now_ts
        view = CommandPaginationView(message.author, pages)
        view.message = await message.channel.send(embed=pages[0], view=view)
        return

    if command_name == "disablelinks":
        if not await check_perms(message, manage_messages=True):
            return
        if not db_fetchone("SELECT 1 FROM system_settings WHERE guild_id = ? AND type = 'links_disabled'", (message.guild.id,)):
            db_execute("INSERT INTO system_settings (guild_id, vector_id, type) VALUES (?, 0, 'links_disabled')", (message.guild.id,))
        return await send_embed(message.channel, "🔒 **Links have been disabled.** Users (excluding mods/admins) can no longer post any links.", color=0xE74C3C)

    if command_name == "allowlinks":
        if not await check_perms(message, manage_messages=True):
            return
        db_execute("DELETE FROM system_settings WHERE guild_id = ? AND type = 'links_disabled'", (message.guild.id,))
        return await send_embed(message.channel, "🔓 **Links have been allowed.** Users can now post links freely.", color=0x2ECC71)




    if command_name == "poll":
        if not await check_perms(message, manage_messages=True):
            return
        args = command_body[len("poll"):].strip()
        if not args:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}poll <question>`")
        poll_embed = discord.Embed(title="📊 Poll", description=args, color=0x3498DB)
        poll_embed.set_footer(text=f"Poll created by {message.author}", icon_url=message.author.avatar.url if message.author.avatar else None)
        msg = await message.channel.send(embed=poll_embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        try:
            await message.delete()
        except:
            pass
        return

    if command_name == "setsuggestions":
        if not await check_perms(message, administrator=True):
            return
        if not message.channel_mentions:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}setsuggestions #channel`")
        target_ch = message.channel_mentions[0]
        db_execute("INSERT INTO suggestions_config (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (message.guild.id, target_ch.id))
        return await send_embed(message.channel, f"✅ Suggestions channel set to {target_ch.mention}.")

    if command_name == "suggest":
        suggestion = command_body[len("suggest"):].strip()
        if not suggestion:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}suggest <idea>`")
            
        row = db_fetchone("SELECT channel_id FROM suggestions_config WHERE guild_id = ?", (message.guild.id,))
        if not row:
            return await send_error_embed(message.channel, "⚠️ Suggestions are not configured for this server.")
            
        sug_channel = message.guild.get_channel(row[0])
        if not sug_channel:
            return await send_error_embed(message.channel, "⚠️ Suggestion channel not found.")
            
        embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=0xF1C40F)
        embed.set_author(name=f"{message.author}", icon_url=message.author.avatar.url if message.author.avatar else None)
        msg = await sug_channel.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        return await send_embed(message.channel, "✅ Your suggestion has been submitted!", color=0x2ECC71)





    if command_name == "modmail":
        mail_content = command_body[len("modmail"):].strip()
        if not mail_content and not message.attachments:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}modmail <your message to mods>` (or attach a file)")
            
        confirm_embed = discord.Embed(
            description="Do you confirm that you are sending this message to staff? Type \"Confirm mail\" to proceed.",
            color=0xF1C40F
        )
        try:
            confirm_msg = await message.reply(embed=confirm_embed, mention_author=False)
        except Exception:
            try:
                confirm_msg = await message.channel.send(content=message.author.mention, embed=confirm_embed)
            except Exception:
                return
                
        def check_confirm(m):
            return m.author == message.author and m.channel == message.channel and m.content.lower() == "confirm mail"
            
        try:
            await client.wait_for('message', timeout=60.0, check=check_confirm)
        except asyncio.TimeoutError:
            try:
                await confirm_msg.delete()
            except Exception:
                pass
            return await send_error_embed(message.channel, "⏳ Mod-mail confirmation timed out.")
            
        try:
            await confirm_msg.delete()
        except Exception:
            pass
            
        ticket_id = str(uuid.uuid4())
        modmail_tickets[ticket_id] = {
            'user_id': message.author.id,
            'guild_id': message.guild.id,
            'message': mail_content,
            'claimed_by': None,
            'messages': []
        }
        desc = f"**From:** {message.author.mention}\n**Message:**\n{mail_content}"
        if message.attachments:
            desc += "\n\n**Attachments:**\n" + "\n".join([a.url for a in message.attachments])
        embed = discord.Embed(title="📬 New Mod-Mail", description=desc, color=0x3498DB)
        embed.set_thumbnail(url=message.author.display_avatar.url if message.author.display_avatar else None)
        embed.set_footer(text=f"User ID: {message.author.id} | Guild: {message.guild.name}")
        
        view = ModMailClaimView(ticket_id)
        
        ch_id = get_modmail_channel(message.guild.id)
        sent = False
        if ch_id:
            ch = message.guild.get_channel(ch_id)
            if ch:
                try:
                    msg = await ch.send(embed=embed, view=view)
                    modmail_tickets[ticket_id]['messages'].append((ch.id, msg.id))
                    sent = True
                except:
                    pass
                    
        if not sent:
            mods = [m for m in message.guild.members if m.guild_permissions.manage_messages and not m.bot]
            if message.guild.owner and message.guild.owner not in mods:
                mods.insert(0, message.guild.owner)
            
            # If owner is not cached, fetch them
            if message.guild.owner_id and not message.guild.owner:
                try:
                    owner_user = await client.fetch_user(message.guild.owner_id)
                    mods.insert(0, owner_user)
                except:
                    pass
                    
            for mod in mods[:10]:
                if mod is None:
                    continue
                try:
                    msg = await mod.send(embed=embed, view=view)
                    modmail_tickets[ticket_id]['messages'].append((mod.id, msg.id))
                except Exception:
                    pass
                except Exception:
                    pass
                    
        success_embed = discord.Embed(description="✅ Successfully sent your mod-mail to the staff.", color=0x2ECC71)
        try:
            return await message.reply(embed=success_embed, mention_author=False)
        except:
            try:
                return await message.channel.send(content=message.author.mention, embed=success_embed)
            except:
                pass
        return

    if command_name == "setmodmail":
        if not await check_perms(message, administrator=True):
            return
        if not message.channel_mentions:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}setmodmail #channel`")
        
        target_ch = message.channel_mentions[0]
        set_modmail_channel(message.guild.id, target_ch.id)
        return await send_embed(message.channel, f"✅ Mod-mail channel set to {target_ch.mention}.")

    if command_name == "disablemodmail":
        if not await check_perms(message, administrator=True):
            return
        disable_modmail_channel(message.guild.id)
        return await send_embed(message.channel, "✅ Mod-mail channel has been disabled. Mails will now go to admins' DMs.")


    if command_name == "welcomech":
        if not await check_perms(message, administrator=True):
            return
        if not message.channel_mentions:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}welcomech #channel`")
        
        target_ch = message.channel_mentions[0]
        set_welcome_channel(message.guild.id, target_ch.id)
        return await send_embed(message.channel, f"✅ Welcome channel set to {target_ch.mention}.")

    if command_name == "disablewelcome":
        if not await check_perms(message, administrator=True):
            return
        disable_welcome_channel(message.guild.id)
        return await send_embed(message.channel, "✅ Welcome messages have been disabled.")

    if command_name == "setlogchannel":
        if not await check_perms(message, manage_guild=True):
            return
        target_channel = message.channel_mentions[0] if message.channel_mentions else message.channel
        set_log_channel(message.guild.id, target_channel.id)
        return await send_embed(message.channel, f"📋 **Modlogs channel set to {target_channel.mention}.** All moderation and server events will now be logged here.", color=0x2ECC71)

    if command_name == "disablelogchannel":
        if not await check_perms(message, manage_guild=True):
            return
        if disable_log_channel(message.guild.id):
            return await send_embed(message.channel, "🗑️ **Modlogs channel has been disabled.** Events will no longer be logged.", color=0xE74C3C)
        else:
            return await send_error_embed(message.channel, "❌ No log channel was active.")

    if command_name == "ai":
        if not await check_perms(message, manage_guild=True):
            return
        if not message.channel_mentions:
            return await send_error_embed(message.channel, f"⚠️ Correct usage: `{guild_prefix}ai #channel`")
        target_channel = message.channel_mentions[0]
        db_execute("INSERT INTO ai_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (message.guild.id, target_channel.id))
        return await send_embed(message.channel, f"🤖 **AI Chat enabled in {target_channel.mention}.** The bot will now respond to messages in this channel.", color=0x3498DB)

    if command_name == "aioff":
        if not await check_perms(message, manage_guild=True):
            return
        db_execute("DELETE FROM ai_channels WHERE guild_id = ?", (message.guild.id,))
        return await send_embed(message.channel, "🤖 **AI Chat has been disabled** on this server.", color=0xE74C3C)

    if command_name == "aireset":
        if not await check_perms(message, manage_guild=True):
            return
        db_execute("DELETE FROM ai_chat_history WHERE guild_id = ? AND channel_id = ?", (message.guild.id, message.channel.id))
        return await send_embed(message.channel, "🧹 **AI memory for this channel has been cleared!** Starting a fresh conversation.", color=0x2ECC71)

    if command_name == "aistart":
        ai_channel = db_fetchone("SELECT channel_id FROM ai_channels WHERE guild_id = ?", (message.guild.id,))
        if ai_channel:
            return await send_embed(message.channel, f"🤖 **AI Chat is active!** Go to <#{ai_channel[0]}> to chat with the AI. It will respond to everyone there.", color=0x3498DB)
        else:
            return await send_error_embed(message.channel, f"❌ AI Chat is not set up on this server. An admin can set it up using `{guild_prefix}ai #channel`.")

    if command_name == "aisettings":
        if not await check_perms(message, manage_guild=True):
            return
            
        args = command_body[len("aisettings"):].strip().split()
        
        if not args:
            # Show current settings
            row = db_fetchone("SELECT custom_prompt, bot_name FROM ai_settings WHERE guild_id = ?", (message.guild.id,))
            current_model = "gpt-4o-mini"
            current_prompt = "Default V!tya personality (natural, friendly, Discord vibe)"
            current_name = "V!tya"
            if row:
                if row[0]:
                    current_prompt = row[0]
                if row[1]:
                    current_name = row[1]
                    
            embed = discord.Embed(
                title="⚙️ AI Chat & Prompt Settings",
                description="Customize the system instructions (behavior) for the AI chat in this server.",
                color=0x3498DB
            )
            embed.add_field(name="🤖 Active Model", value=f"`{current_model}` (GPT-4o Mini - Main model)", inline=False)
            embed.add_field(name="📛 Customized Name", value=f"`{current_name}`", inline=False)
            
            # Truncate prompt if too long
            prompt_display = current_prompt
            if len(prompt_display) > 800:
                prompt_display = prompt_display[:800] + "..."
            embed.add_field(name="📜 System Prompt", value=f"```\n{prompt_display}\n```", inline=False)
            
            embed.add_field(
                name="🔧 Configuration Commands",
                value=(
                    f"• `{guild_prefix}aisettings prompt <text>` - Set a custom system prompt / instructions\n"
                    f"• `{guild_prefix}aisettings reset` - Reset prompt & customized nickname back to default\n"
                ),
                inline=False
            )
            return await message.channel.send(embed=embed)
            
        subcommand = args[0].lower()
        
        if subcommand == "prompt":
            custom_prompt_text = command_body[len("aisettings"):].strip()[len("prompt"):].strip()
            if not custom_prompt_text:
                return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}aisettings prompt <text>` (Provide the instructions for how the AI should behave)")
                
            exists = db_fetchone("SELECT 1 FROM ai_settings WHERE guild_id = ?", (message.guild.id,))
            if exists:
                db_execute("UPDATE ai_settings SET custom_prompt = ? WHERE guild_id = ?", (custom_prompt_text, message.guild.id))
            else:
                db_execute("INSERT INTO ai_settings (guild_id, model, custom_prompt) VALUES (?, ?, ?)", (message.guild.id, "gpt-4o-mini", custom_prompt_text))
                
            return await send_embed(message.channel, f"✅ **AI System Prompt updated!** Future conversations will use your custom rules:\n```\n{custom_prompt_text[:200]}...\n```", color=0x2ECC71)
            
        elif subcommand == "reset":
            db_execute("DELETE FROM ai_settings WHERE guild_id = ?", (message.guild.id,))
            try:
                await message.guild.me.edit(nick=None)
            except Exception:
                pass
            return await send_embed(message.channel, "✅ **AI Settings reset successfully!** Restored default V!tya personality and model (`gpt-4o-mini`).", color=0x2ECC71)
            
        else:
            return await send_error_embed(message.channel, f"❌ Unknown subcommand `{args[0]}`. Use `{guild_prefix}aisettings` to view help.")

    if command_name in ("remind", "remindme"):
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}remindme <duration> <text>` (e.g. `10m`, `1h`)")
        
        duration_str = args[1]
        duration = parse_duration(duration_str)
        if duration <= 0:
            return await send_error_embed(message.channel, "❌ Invalid duration. Use format like `10m`, `2h`, `1d`.")
        
        reminder_text = " ".join(args[2:])
        expiry = int(datetime.datetime.now().timestamp()) + duration
        
        db_execute("INSERT INTO reminders (user_id, channel_id, reminder_text, timestamp) VALUES (?, ?, ?, ?)", 
                   (message.author.id, message.channel.id, reminder_text, expiry))
        return await send_embed(message.channel, f"⏰ I will remind you: **{reminder_text}** in **{duration_str}**.", color=0x2ECC71)

    if command_name == "slowmode":
        if not await check_perms(message, manage_channels=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}slowmode <seconds>` (or `off`)")
        
        duration_arg = " ".join(args[1:]).lower().strip()
        if duration_arg in ("off", "disable", "disabled", "none", "0"):
            seconds = 0
        else:
            try:
                seconds = int(duration_arg)
            except ValueError:
                seconds = parse_duration(duration_arg)
                if seconds == 0:
                    return await send_error_embed(message.channel, "❌ Invalid slowmode duration. Please specify a number of seconds (e.g., `5s`, `10m`, `1h`) or `off`.")
        
        if seconds < 0 or seconds > 21600:
            return await send_error_embed(message.channel, "❌ Slowmode duration must be between `0` (off) and `21600` (6 hours) seconds.")
        
        try:
            await message.channel.edit(slowmode_delay=seconds)
            if seconds == 0:
                return await send_embed(message.channel, "⏱️ **Slowmode has been disabled** in this channel.", color=0x2ECC71)
            else:
                if seconds >= 3600:
                    h, r = divmod(seconds, 3600)
                    m, s = divmod(r, 60)
                    time_desc = f"{h}h" + (f" {m}m" if m else "") + (f" {s}s" if s else "")
                elif seconds >= 60:
                    m, s = divmod(seconds, 60)
                    time_desc = f"{m}m" + (f" {s}s" if s else "")
                else:
                    time_desc = f"{seconds}s"
                return await send_embed(message.channel, f"⏱️ **Slowmode has been set to `{time_desc}`** in this channel.", color=0x3498DB)
        except Exception:
            return await send_error_embed(message.channel, "❌ I lack permissions to edit this channel's settings.")

    if command_name == "addresponder":
        if not await check_perms(message, manage_messages=True):
            return
        parts = command_body[len("addresponder"):].strip().split("|", 1)
        if len(parts) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addresponder <trigger_word> | <bot_response>`")
        trigger = parts[0].strip()
        response = parts[1].strip()
        if not trigger or not response:
            return await send_error_embed(message.channel, "❌ Trigger and response cannot be empty.")
        add_auto_responder(message.guild.id, trigger, response)
        return await send_embed(message.channel, f"✅ **Auto-responder added!** Whenever someone sends exactly `{trigger}`, I will respond with:\n```{response}```", color=0x2ECC71)

    if command_name == "delresponder":
        if not await check_perms(message, manage_messages=True):
            return
        trigger = command_body[len("delresponder"):].strip()
        if not trigger:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}delresponder <trigger_word>`")
        if remove_auto_responder(message.guild.id, trigger):
            return await send_embed(message.channel, f"🗑️ **Auto-responder removed** for `{trigger}`.", color=0x2ECC71)
        else:
            return await send_error_embed(message.channel, f"❌ No auto-responder found for `{trigger}`.")

    if command_name in ("responders", "listresponders"):
        responders = get_all_auto_responders(message.guild.id)
        if not responders:
            return await send_error_embed(message.channel, "No auto-responders have been configured on this server.")
        embed = discord.Embed(title="⚙️ Server Auto-Responders", color=0x3498DB)
        lines = []
        for trigger, response in responders:
            disp_resp = response[:50] + "..." if len(response) > 50 else response
            lines.append(f"• **{trigger}** → {disp_resp}")
        embed.description = "\n".join(lines)
        return await message.channel.send(embed=embed)

    if command_name == "blacklistword":
        if not await check_perms(message, manage_messages=True):
            return
        args = command_body.split(maxsplit=1)
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}blacklistword <word>`")
        word = args[1].lower()
        if add_blacklisted_word(message.guild.id, word):
            return await send_embed(message.channel, f"✅ `{word}` added to the blacklist.", color=0x2ECC71)
        else:
            return await send_error_embed(message.channel, f"❌ `{word}` is already blacklisted.")

    if command_name == "unblacklistword":
        if not await check_perms(message, manage_messages=True):
            return
        args = command_body.split(maxsplit=1)
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unblacklistword <word>`")
        word = args[1].lower()
        if remove_blacklisted_word(message.guild.id, word):
            return await send_embed(message.channel, f"🗑️ `{word}` removed from the blacklist.", color=0x2ECC71)
        else:
            return await send_error_embed(message.channel, f"❌ `{word}` is not on the blacklist.")

    if command_name == "blacklistedwords":
        if not await check_perms(message, manage_messages=True):
            return
        words = get_blacklisted_words(message.guild.id)
        if not words:
            return await send_error_embed(message.channel, "No words are currently blacklisted.")
        embed = discord.Embed(title="🚫 Blacklisted Words", description="```\n" + "\n".join(words) + "\n```", color=0xE74C3C)
        return await message.channel.send(embed=embed)

    if command_name == "lockdown":
        if not await check_perms(message, manage_channels=True):
            return
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
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id)
        if toggle_setting(message.guild.id, target_id, "lock"):
            return await send_embed(message.channel, f"🔓 **Botlock removed.** The bot will respond in <#{target_id}> again.", color=0x2ECC71)
        else:
            return await send_embed(message.channel, f"🔒 **Botlock enabled.** The bot will ignore normal commands in <#{target_id}>.", color=0xE74C3C)

    if command_name == "botisolate":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id)
        if toggle_setting(message.guild.id, target_id, "isolate"):
            return await send_embed(message.channel, f"🔓 **Isolation removed.** <#{target_id}> is no longer whitelisted.", color=0x2ECC71)
        else:
            return await send_embed(message.channel, f"🛡️ **Bot isolated.** Normal commands will ONLY work inside <#{target_id}>.", color=0x3498DB)

    if command_name == "restrict":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}restrict <command>`")
        cmd = args[1].lower()
        if cmd in get_restricted_commands(message.guild.id):
            db_execute("DELETE FROM restricted_commands WHERE guild_id = ? AND command_name = ?", (message.guild.id, cmd))
            return await send_embed(message.channel, f"🔓 `{cmd}` is no longer restricted.", color=0x2ECC71)
        else:
            db_execute("INSERT INTO restricted_commands (guild_id, command_name) VALUES (?, ?)", (message.guild.id, cmd))
            return await send_embed(message.channel, f"🔒 `{cmd}` is now restricted to Administrators.", color=0xE74C3C)

    if command_name == "banping":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}banping <@role/ID>`")
        role = resolve_role(message.guild, args[1], message.role_mentions)
        if not role:
            return await send_error_embed(message.channel, "❌ Please specify a valid role tag or role ID.")
        if toggle_banping_role(message.guild.id, role.id):
            return await send_embed(message.channel, f"🛡️ **Banping enabled** for {role.mention}. Users who ping this role will be auto-muted.", color=0xE74C3C)
        else:
            return await send_embed(message.channel, f"🔓 **Banping disabled** for {role.mention}.", color=0x2ECC71)

    if command_name == "prefix":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split(maxsplit=1)
        if len(args) < 2:
            return await send_embed(message.channel, f"⚙️ Current prefix: `{get_guild_prefix(message.guild.id)}`")
        new_prefix = args[1].strip()
        if len(new_prefix) > 5 or " " in new_prefix:
            return await send_error_embed(message.channel, "❌ Prefix must be 1-5 chars without spaces.")
        set_guild_prefix(message.guild.id, new_prefix)
        return await send_embed(message.channel, f"✅ Prefix changed to `{new_prefix}`", color=0x2ECC71)

    if command_name == "createrole":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
            
        if not message.guild.me.guild_permissions.manage_roles:
            return await send_error_embed(message.channel, "❌ I do not have the **Manage Roles** permission. Please grant me this permission to manage and create roles.")
            
        args_text = command_body[len("createrole"):].strip()
        import shlex
        args = []
        if args_text:
            try:
                args = shlex.split(args_text)
            except Exception:
                args = args_text.split()
                
        # Define mappings and templates locally for maximum modularity and safety
        COLOR_MAP = {
            "red": discord.Color.red(),
            "blue": discord.Color.blue(),
            "green": discord.Color.green(),
            "yellow": discord.Color.gold(),
            "gold": discord.Color.gold(),
            "purple": discord.Color.purple(),
            "orange": discord.Color.orange(),
            "magenta": discord.Color.magenta(),
            "cyan": discord.Color.teal(),
            "teal": discord.Color.teal(),
            "pink": discord.Color.from_rgb(255, 105, 180),
            "black": discord.Color.from_rgb(1, 1, 1),
            "white": discord.Color.from_rgb(254, 254, 254),
            "gray": discord.Color.light_gray(),
            "grey": discord.Color.light_gray()
        }

        TEMPLATES = {
            "admin": discord.Permissions(administrator=True),
            "administrator": discord.Permissions(administrator=True),
            "moderator": discord.Permissions(
                kick_members=True, ban_members=True, manage_messages=True,
                mute_members=True, deafen_members=True, move_members=True,
                manage_nicknames=True, view_audit_log=True, read_message_history=True,
                view_channel=True, send_messages=True, connect=True, speak=True
            ),
            "mod": discord.Permissions(
                kick_members=True, ban_members=True, manage_messages=True,
                mute_members=True, deafen_members=True, move_members=True,
                manage_nicknames=True, view_audit_log=True, read_message_history=True,
                view_channel=True, send_messages=True, connect=True, speak=True
            ),
            "helper": discord.Permissions(
                manage_messages=True, manage_nicknames=True, view_audit_log=True,
                read_message_history=True, view_channel=True, send_messages=True,
                connect=True, speak=True, kick_members=True
            ),
            "staff": discord.Permissions(
                manage_messages=True, manage_nicknames=True, view_audit_log=True,
                read_message_history=True, view_channel=True, send_messages=True,
                connect=True, speak=True, kick_members=True
            ),
            "dj": discord.Permissions(
                connect=True, speak=True, priority_speaker=True, use_voice_activation=True,
                read_message_history=True, view_channel=True, send_messages=True
            ),
            "vip": discord.Permissions(
                send_messages=True, read_message_history=True, connect=True, speak=True,
                embed_links=True, attach_files=True, use_external_emojis=True, add_reactions=True,
                change_nickname=True
            ),
            "muted": discord.Permissions(
                send_messages=False, send_tts_messages=False, add_reactions=False,
                speak=False, connect=True, read_message_history=True, view_channel=True
            ),
            "member": discord.Permissions(
                send_messages=True, read_message_history=True, connect=True, speak=True,
                use_voice_activation=True, add_reactions=True, embed_links=True, attach_files=True,
                change_nickname=True
            ),
            "community": discord.Permissions(
                send_messages=True, read_message_history=True, connect=True, speak=True,
                use_voice_activation=True, add_reactions=True, embed_links=True, attach_files=True,
                change_nickname=True
            )
        }

        FRIENDLY_PERMS = {
            "admin": "administrator",
            "administrator": "administrator",
            "kick": "kick_members",
            "kick_members": "kick_members",
            "ban": "ban_members",
            "ban_members": "ban_members",
            "manage_channels": "manage_channels",
            "manage_guild": "manage_guild",
            "manage_server": "manage_guild",
            "add_reactions": "add_reactions",
            "view_audit_log": "view_audit_log",
            "priority_speaker": "priority_speaker",
            "stream": "stream",
            "view_channel": "view_channel",
            "send_messages": "send_messages",
            "manage_messages": "manage_messages",
            "embed_links": "embed_links",
            "attach_files": "attach_files",
            "read_message_history": "read_message_history",
            "mention_everyone": "mention_everyone",
            "external_emojis": "use_external_emojis",
            "connect": "connect",
            "speak": "speak",
            "mute_members": "mute_members",
            "deafen_members": "deafen_members",
            "move_members": "move_members",
            "change_nickname": "change_nickname",
            "manage_nicknames": "manage_nicknames",
            "manage_roles": "manage_roles",
            "manage_webhooks": "manage_webhooks"
        }

        # Subviews inside command scope to avoid polluting global namespace and ensuring clean code imports
        class RoleWizardPresetView(discord.ui.View):
            def __init__(self, author, r_name):
                super().__init__(timeout=60.0)
                self.author = author
                self.r_name = r_name
                self.preset = "custom"
                self.cancelled = False

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author.id:
                    await interaction.response.send_message("❌ Only the wizard initiator can interact.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="👑 Admin", style=discord.ButtonStyle.secondary, custom_id="preset_admin", row=0)
            async def admin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "admin"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🛡️ Moderator", style=discord.ButtonStyle.secondary, custom_id="preset_moderator", row=0)
            async def mod_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "moderator"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🎵 DJ", style=discord.ButtonStyle.secondary, custom_id="preset_dj", row=0)
            async def dj_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "dj"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="✨ VIP", style=discord.ButtonStyle.secondary, custom_id="preset_vip", row=0)
            async def vip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "vip"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🔇 Muted", style=discord.ButtonStyle.secondary, custom_id="preset_muted", row=0)
            async def muted_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "muted"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="👥 Member", style=discord.ButtonStyle.secondary, custom_id="preset_member", row=1)
            async def member_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "member"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="⚙️ Custom (Empty)", style=discord.ButtonStyle.secondary, custom_id="preset_custom", row=1)
            async def custom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.preset = "custom"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="preset_cancel", row=1)
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.cancelled = True
                self.stop()
                await interaction.response.defer()

        class RoleWizardColorView(discord.ui.View):
            def __init__(self, author, client_obj, channel):
                super().__init__(timeout=60.0)
                self.author = author
                self.client = client_obj
                self.channel = channel
                self.color = discord.Color.default()
                self.color_name = "Default (Gray)"
                self.cancelled = False

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author.id:
                    await interaction.response.send_message("❌ Only the wizard initiator can interact.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="🔴 Red", style=discord.ButtonStyle.secondary, custom_id="color_red", row=0)
            async def red_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.red()
                self.color_name = "Red"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🔵 Blue", style=discord.ButtonStyle.secondary, custom_id="color_blue", row=0)
            async def blue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.blue()
                self.color_name = "Blue"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🟢 Green", style=discord.ButtonStyle.secondary, custom_id="color_green", row=0)
            async def green_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.green()
                self.color_name = "Green"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🟡 Yellow", style=discord.ButtonStyle.secondary, custom_id="color_yellow", row=0)
            async def yellow_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.gold()
                self.color_name = "Gold/Yellow"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🟣 Purple", style=discord.ButtonStyle.secondary, custom_id="color_purple", row=0)
            async def purple_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.purple()
                self.color_name = "Purple"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🎨 Custom Hex", style=discord.ButtonStyle.primary, custom_id="color_hex", row=1)
            async def hex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                prompt_embed = discord.Embed(
                    title="🎨 Custom Color Hex",
                    description="Please type the color Hex Code in chat (e.g. `#FF00FF` or `3498DB`).",
                    color=0x3498DB
                )
                await interaction.response.edit_message(embed=prompt_embed, view=None)
                
                def check_m(m):
                    return m.author == self.author and m.channel == self.channel
                    
                try:
                    msg = await self.client.wait_for('message', timeout=30.0, check=check_m)
                    content = msg.content.strip().lstrip('#')
                    try:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
                        parsed_color = discord.Color(int(content, 16))
                        self.color = parsed_color
                        self.color_name = f"Custom (#{content.upper()})"
                        self.stop()
                    except ValueError:
                        err_embed = discord.Embed(
                            title="❌ Invalid Hex",
                            description=f"`{msg.content}` is not a valid hex color code. Falling back to default gray color.",
                            color=0xE74C3C
                        )
                        await self.channel.send(embed=err_embed, delete_after=5.0)
                        self.color = discord.Color.default()
                        self.color_name = "Default (Gray)"
                        self.stop()
                except asyncio.TimeoutError:
                    self.color = discord.Color.default()
                    self.color_name = "Default (Gray)"
                    self.stop()

            @discord.ui.button(label="⚪ Default", style=discord.ButtonStyle.secondary, custom_id="color_default", row=1)
            async def default_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.color = discord.Color.default()
                self.color_name = "Default (Gray)"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="color_cancel", row=1)
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.cancelled = True
                self.stop()
                await interaction.response.defer()

        class RoleWizardPositionView(discord.ui.View):
            def __init__(self, author, client_obj, channel):
                super().__init__(timeout=60.0)
                self.author = author
                self.client = client_obj
                self.channel = channel
                self.relation = "default" # "default", "above", "below", "bottom"
                self.ref_role = None
                self.cancelled = False

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author.id:
                    await interaction.response.send_message("❌ Only the wizard initiator can interact.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="👑 Below My Highest", style=discord.ButtonStyle.primary, custom_id="pos_below_highest", row=0)
            async def below_highest_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.relation = "default"
                self.ref_role = None
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="🔼 Above Specific Role", style=discord.ButtonStyle.secondary, custom_id="pos_above_role", row=0)
            async def above_role_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.relation = "above"
                await self.prompt_for_role(interaction)

            @discord.ui.button(label="🔽 Below Specific Role", style=discord.ButtonStyle.secondary, custom_id="pos_below_role", row=0)
            async def below_role_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.relation = "below"
                await self.prompt_for_role(interaction)

            @discord.ui.button(label="💤 Above @everyone (Bottom)", style=discord.ButtonStyle.secondary, custom_id="pos_bottom", row=1)
            async def bottom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.relation = "bottom"
                self.ref_role = None
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="pos_cancel", row=1)
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.cancelled = True
                self.stop()
                await interaction.response.defer()

            async def prompt_for_role(self, interaction: discord.Interaction):
                prompt_embed = discord.Embed(
                    title="📶 Target Hierarchy Reference",
                    description=f"You chose to place the role **{self.relation.upper()}** a specific role.\n\n"
                                f"Please **type the role name, ID, or mention it** in chat now.",
                    color=0x3498DB
                )
                await interaction.response.edit_message(embed=prompt_embed, view=None)
                
                def check_m(m):
                    return m.author == self.author and m.channel == self.channel
                    
                try:
                    msg = await self.client.wait_for('message', timeout=45.0, check=check_m)
                    content = msg.content.strip()
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                        
                    role = None
                    if msg.role_mentions:
                        role = msg.role_mentions[0]
                    else:
                        try:
                            role = interaction.guild.get_role(int(content))
                        except ValueError:
                            role = discord.utils.find(lambda r: r.name.lower() == content.lower(), interaction.guild.roles)
                            if not role:
                                role = discord.utils.find(lambda r: content.lower() in r.name.lower(), interaction.guild.roles)
                                
                    if role:
                        self.ref_role = role
                        self.stop()
                    else:
                        err_embed = discord.Embed(
                            title="❌ Role Not Found",
                            description=f"Could not find any role matching `{content}`. Placing below your highest role as fallback.",
                            color=0xE74C3C
                        )
                        await self.channel.send(embed=err_embed, delete_after=5.0)
                        self.relation = "default"
                        self.ref_role = None
                        self.stop()
                except asyncio.TimeoutError:
                    self.relation = "default"
                    self.ref_role = None
                    self.stop()

        class RoleWizardSettingsView(discord.ui.View):
            def __init__(self, author, r_name, p_name, col_name, col_val, rel_type="default", ref_role_obj=None):
                super().__init__(timeout=60.0)
                self.author = author
                self.r_name = r_name
                self.p_name = p_name
                self.col_name = col_name
                self.col_val = col_val
                self.rel_type = rel_type
                self.ref_role_obj = ref_role_obj
                self.hoist = False
                self.mentionable = False
                self.confirmed = False
                self.cancelled = False
                self.update_labels()

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author.id:
                    await interaction.response.send_message("❌ Only the wizard initiator can interact.", ephemeral=True)
                    return False
                return True

            def update_labels(self):
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        if child.custom_id == "toggle_hoist":
                            child.label = f"👥 Display Separately: {'[ON]' if self.hoist else '[OFF]'}"
                            child.style = discord.ButtonStyle.success if self.hoist else discord.ButtonStyle.secondary
                        elif child.custom_id == "toggle_mention":
                            child.label = f"📣 Mentionable: {'[ON]' if self.mentionable else '[OFF]'}"
                            child.style = discord.ButtonStyle.success if self.mentionable else discord.ButtonStyle.secondary

            @discord.ui.button(label="👥 Display Separately: [OFF]", style=discord.ButtonStyle.secondary, custom_id="toggle_hoist", row=0)
            async def hoist_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.hoist = not self.hoist
                self.update_labels()
                await interaction.response.edit_message(embed=self.get_embed(), view=self)

            @discord.ui.button(label="📣 Mentionable: [OFF]", style=discord.ButtonStyle.secondary, custom_id="toggle_mention", row=0)
            async def mention_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.mentionable = not self.mentionable
                self.update_labels()
                await interaction.response.edit_message(embed=self.get_embed(), view=self)

            @discord.ui.button(label="🚀 CREATE ROLE", style=discord.ButtonStyle.success, custom_id="confirm_create", row=1)
            async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.confirmed = True
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_wizard", row=1)
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.cancelled = True
                self.stop()
                await interaction.response.defer()

            def get_embed(self) -> discord.Embed:
                embed = discord.Embed(
                    title="🔮 Role Creation Wizard | Confirm Configuration",
                    description="Review your custom role setup below and click **CREATE ROLE** to finalize!",
                    color=self.col_val if self.col_val.value != 0 else 0x9B59B6
                )
                embed.add_field(name="🏷️ Role Name", value=f"**{self.r_name}**", inline=True)
                embed.add_field(name="🎨 Color", value=f"**{self.col_name}**", inline=True)
                embed.add_field(name="🛡️ Preset Template", value=f"`{self.p_name.upper()}`", inline=True)
                
                if self.rel_type == "above" and self.ref_role_obj:
                    pos_desc = f"Above `{self.ref_role_obj.name}`"
                elif self.rel_type == "below" and self.ref_role_obj:
                    pos_desc = f"Below `{self.ref_role_obj.name}`"
                elif self.rel_type == "bottom":
                    pos_desc = "Above @everyone (Bottom)"
                else:
                    pos_desc = "Below My Highest Role"
                    
                embed.add_field(name="📶 Target Position", value=f"**{pos_desc}**", inline=True)
                
                settings_text = (
                    f"• Display Separately (Hoist): **{'Yes' if self.hoist else 'No'}**\n"
                    f"• Mentionable by Anyone: **{'Yes' if self.mentionable else 'No'}**"
                )
                embed.add_field(name="⚙️ Properties", value=settings_text, inline=True)
                
                perms_obj = TEMPLATES.get(self.p_name, discord.Permissions.none())
                granted_perms = [name.replace('_', ' ').title() for name, val in perms_obj if val]
                if perms_obj.administrator:
                    perms_summary = "👑 **Administrator (All Permissions Granted)**"
                elif granted_perms:
                    if len(granted_perms) > 8:
                        perms_summary = ", ".join(granted_perms[:8]) + f" and {len(granted_perms)-8} others"
                    else:
                        perms_summary = ", ".join(granted_perms)
                else:
                    perms_summary = "No permissions granted (Standard cosmetic role)"
                    
                embed.add_field(name="✅ Granted Permissions Preview", value=perms_summary, inline=False)
                embed.set_footer(text=f"Requested by {self.author.display_name}")
                return embed

        # --- MODE 1: INTERACTIVE WIZARD MODE ---
        if not args or args[0].lower() in ("wizard", "setup", "interactive"):
            prompt_embed = discord.Embed(
                title="🔮 Interactive Role Creator Wizard",
                description=f"Welcome {message.author.mention}! Let's design and configure a custom role together.\n\n"
                            f"**Step 1/5: Choose a Role Name**\n"
                            f"Please type the desired **name** of your new role in the chat.\n"
                            f"*Type `cancel` to quit at any point.*",
                color=0x9B59B6
            )
            wizard_msg = await message.channel.send(embed=prompt_embed)
            
            def check_author(m):
                return m.author == message.author and m.channel == message.channel
                
            try:
                name_msg = await client.wait_for('message', timeout=45.0, check=check_author)
                role_name = name_msg.content.strip()
                try:
                    await name_msg.delete()
                except Exception:
                    pass
                    
                if role_name.lower() == "cancel":
                    await wizard_msg.edit(embed=discord.Embed(title="❌ Wizard Cancelled", description="The role creation wizard was cancelled.", color=0xE74C3C), view=None)
                    return
                if not role_name:
                    await wizard_msg.edit(embed=discord.Embed(title="❌ Invalid Name", description="Role name cannot be empty.", color=0xE74C3C), view=None)
                    return
                role_name = role_name[:100] # Cap Discord role name length
            except asyncio.TimeoutError:
                await wizard_msg.edit(embed=discord.Embed(title="❌ Timeout", description="You took too long to respond. The wizard has exited.", color=0xE74C3C), view=None)
                return
                
            # Step 2: Preset Selection
            preset_embed = discord.Embed(
                title="🔮 Role Creation Wizard | Step 2/5",
                description=f"Selected Name: **{role_name}**\n\n"
                            f"Please select a **Permission Preset Template** for your new role below:",
                color=0x3498DB
            )
            preset_view = RoleWizardPresetView(message.author, role_name)
            await wizard_msg.edit(embed=preset_embed, view=preset_view)
            
            await preset_view.wait()
            if preset_view.cancelled or preset_view.preset is None:
                await wizard_msg.edit(embed=discord.Embed(title="❌ Wizard Cancelled", description="The role creation wizard was cancelled.", color=0xE74C3C), view=None)
                return
                
            selected_preset = preset_view.preset
            
            # Step 3: Color Selection
            color_embed = discord.Embed(
                title="🔮 Role Creation Wizard | Step 3/5",
                description=f"Selected Name: **{role_name}**\n"
                            f"Permission Preset: `{selected_preset.upper()}`\n\n"
                            f"Please select a **Color** for your role using the buttons:",
                color=0x3498DB
            )
            color_view = RoleWizardColorView(message.author, client, message.channel)
            await wizard_msg.edit(embed=color_embed, view=color_view)
            
            await color_view.wait()
            if color_view.cancelled:
                await wizard_msg.edit(embed=discord.Embed(title="❌ Wizard Cancelled", description="The role creation wizard was cancelled.", color=0xE74C3C), view=None)
                return
                
            selected_color = color_view.color
            color_label = color_view.color_name
            
            # Step 4: Position Selection
            position_embed = discord.Embed(
                title="🔮 Role Creation Wizard | Step 4/5",
                description=f"Selected Name: **{role_name}**\n"
                            f"Preset: `{selected_preset.upper()}`\n"
                            f"Color: **{color_label}**\n\n"
                            f"Where in the role hierarchy would you like to place this role?",
                color=0x3498DB
            )
            position_view = RoleWizardPositionView(message.author, client, message.channel)
            await wizard_msg.edit(embed=position_embed, view=position_view)
            
            await position_view.wait()
            if position_view.cancelled:
                await wizard_msg.edit(embed=discord.Embed(title="❌ Wizard Cancelled", description="The role creation wizard was cancelled.", color=0xE74C3C), view=None)
                return
                
            selected_relation = position_view.relation
            selected_ref_role = position_view.ref_role
            
            # Step 5: Settings & Final Confirmation
            settings_view = RoleWizardSettingsView(
                message.author, role_name, selected_preset, color_label, selected_color, selected_relation, selected_ref_role
            )
            await wizard_msg.edit(embed=settings_view.get_embed(), view=settings_view)
            
            await settings_view.wait()
            if settings_view.cancelled or not settings_view.confirmed:
                await wizard_msg.edit(embed=discord.Embed(title="❌ Wizard Cancelled", description="The role creation wizard was cancelled.", color=0xE74C3C), view=None)
                return
                
            role_perms = TEMPLATES.get(selected_preset, discord.Permissions.none())
            hoist = settings_view.hoist
            mentionable = settings_view.mentionable
            role_color = selected_color
            preset_name = selected_preset
            color_name = color_label
            position_relation = selected_relation
            ref_role = selected_ref_role
            
        # --- MODE 2: COMMAND LINE ADVANCED PARSING MODE ---
        else:
            role_name = args[0][:100]
            role_color = discord.Color.default()
            color_name = "Default (Gray)"
            role_perms = discord.Permissions.none()
            preset_name = "custom"
            hoist = False
            mentionable = False
            ref_role = None
            position_relation = "default"
            friendly_perms_parsed = []
            
            parsed_args = []
            i = 1
            while i < len(args):
                arg = args[i]
                arg_lower = arg.lower().strip()
                
                # Check for "above" / "below" keywords
                if arg_lower in ("above", "--above") and i + 1 < len(args):
                    ref_role_query = args[i+1]
                    position_relation = "above"
                    try:
                        ref_role = message.guild.get_role(int(ref_role_query))
                    except ValueError:
                        ref_role = discord.utils.find(lambda r: r.name.lower() == ref_role_query.lower(), message.guild.roles)
                        if not ref_role:
                            ref_role = discord.utils.find(lambda r: ref_role_query.lower() in r.name.lower(), message.guild.roles)
                    i += 2
                    continue
                    
                if arg_lower in ("below", "--below") and i + 1 < len(args):
                    ref_role_query = args[i+1]
                    position_relation = "below"
                    try:
                        ref_role = message.guild.get_role(int(ref_role_query))
                    except ValueError:
                        ref_role = discord.utils.find(lambda r: r.name.lower() == ref_role_query.lower(), message.guild.roles)
                        if not ref_role:
                            ref_role = discord.utils.find(lambda r: ref_role_query.lower() in r.name.lower(), message.guild.roles)
                    i += 2
                    continue
                    
                if arg_lower.startswith("above="):
                    ref_role_query = arg[len("above="):]
                    position_relation = "above"
                    try:
                        ref_role = message.guild.get_role(int(ref_role_query))
                    except ValueError:
                        ref_role = discord.utils.find(lambda r: r.name.lower() == ref_role_query.lower(), message.guild.roles)
                        if not ref_role:
                            ref_role = discord.utils.find(lambda r: ref_role_query.lower() in r.name.lower(), message.guild.roles)
                    i += 1
                    continue
                    
                if arg_lower.startswith("below="):
                    ref_role_query = arg[len("below="):]
                    position_relation = "below"
                    try:
                        ref_role = message.guild.get_role(int(ref_role_query))
                    except ValueError:
                        ref_role = discord.utils.find(lambda r: r.name.lower() == ref_role_query.lower(), message.guild.roles)
                        if not ref_role:
                            ref_role = discord.utils.find(lambda r: ref_role_query.lower() in r.name.lower(), message.guild.roles)
                    i += 1
                    continue

                parsed_args.append(arg)
                i += 1

            for arg in parsed_args:
                arg_lower = arg.lower().strip()
                
                # Check hoist flags
                if arg_lower in ("hoist", "hoisted", "hoist=true", "display_separately", "display"):
                    hoist = True
                    continue
                # Check mention flags
                if arg_lower in ("mentionable", "mention", "mentionable=true"):
                    mentionable = True
                    continue
                    
                # Check hex codes
                is_hex = False
                hex_candidate = arg_lower.lstrip('#')
                if len(hex_candidate) in (3, 6):
                    try:
                        int(hex_candidate, 16)
                        is_hex = True
                    except ValueError:
                        pass
                
                if is_hex:
                    try:
                        role_color = discord.Color(int(hex_candidate, 16))
                        color_name = f"Custom (#{hex_candidate.upper()})"
                        continue
                    except Exception:
                        pass
                        
                # Check color maps
                if arg_lower in COLOR_MAP:
                    role_color = COLOR_MAP[arg_lower]
                    color_name = arg_lower.title()
                    continue
                    
                # Check templates
                temp_key = arg_lower[len("template:"):] if arg_lower.startswith("template:") else arg_lower
                if temp_key in TEMPLATES:
                    role_perms = discord.Permissions(TEMPLATES[temp_key].value)
                    preset_name = temp_key
                    continue
                    
                # Check friendly permission sets
                if "," in arg_lower or arg_lower in FRIENDLY_PERMS:
                    perm_items = arg_lower.split(",")
                    for item in perm_items:
                        item_clean = item.strip()
                        if item_clean in FRIENDLY_PERMS:
                            attr_name = FRIENDLY_PERMS[item_clean]
                            setattr(role_perms, attr_name, True)
                            friendly_perms_parsed.append(item_clean.replace('_', ' ').title())
            
            wizard_msg = None

        # --- EXECUTE ROLE CREATION AND PLACEMENT ---
        creation_announce = None
        if wizard_msg:
            creation_announce = wizard_msg
            loading_embed = discord.Embed(title="⚙️ Processing", description="Creating and positioning your new custom role...", color=0x3498DB)
            await wizard_msg.edit(embed=loading_embed, view=None)
        else:
            loading_embed = discord.Embed(title="⚙️ Processing", description="Creating and positioning your new custom role...", color=0x3498DB)
            creation_announce = await message.channel.send(embed=loading_embed)

        try:
            new_role = await message.guild.create_role(
                name=role_name, 
                color=role_color, 
                permissions=role_perms, 
                hoist=hoist,
                mentionable=mentionable,
                reason=f"Created via createrole command by {message.author}"
            )
            
            author_pos = message.author.top_role.position
            bot_pos = message.guild.me.top_role.position
            is_owner = (message.author.id == message.guild.owner_id)
            
            # Capping: ensure bot cannot move a role equal to or above its highest role
            max_allowed = bot_pos - 1
            if not is_owner:
                # Normal admins can only position roles strictly below their own top role
                max_allowed = min(max_allowed, author_pos - 1)
                
            if ref_role:
                if position_relation == "above":
                    target_position = ref_role.position + 1
                else: # below
                    target_position = ref_role.position
            elif position_relation == "bottom":
                target_position = 1
            else: # default/below author
                target_position = author_pos - 1
                
            target_position = min(target_position, max_allowed)
            target_position = max(1, target_position)
            
            position_msg = f"at position `{target_position}`"
            try:
                await new_role.edit(position=target_position)
                if ref_role:
                    position_msg = f"at position `{target_position}` ({position_relation.upper()} reference role `{ref_role.name}`)"
                else:
                    position_msg = f"at position `{target_position}` (below your top role `{message.author.top_role.name}`)"
            except Exception as e:
                position_msg = f"but failed to move its position: {e}"
                
            # Beautiful complete role summary embed
            success_embed = discord.Embed(
                title="✨ Role Created Successfully!",
                description=f"Successfully configured and activated the role {new_role.mention}!",
                color=new_role.color if new_role.color.value != 0 else 0x2ECC71
            )
            success_embed.add_field(name="🏷️ Name", value=f"**{new_role.name}**", inline=True)
            success_embed.add_field(name="🎨 Color", value=f"**{color_name}**", inline=True)
            success_embed.add_field(name="🛡️ Preset / Template", value=f"`{preset_name.upper()}`", inline=True)
            success_embed.add_field(name="📶 Hierarchy Position", value=position_msg, inline=False)
            
            properties_txt = (
                f"• Hoisted (Separated): **{'Yes' if hoist else 'No'}**\n"
                f"• Mentionable: **{'Yes' if mentionable else 'No'}**\n"
                f"• ID: `{new_role.id}`"
            )
            success_embed.add_field(name="⚙️ Properties", value=properties_txt, inline=True)
            
            active_perms = [name.replace('_', ' ').title() for name, val in new_role.permissions if val]
            if new_role.permissions.administrator:
                perms_display = "👑 **Administrator (All Permissions Granted)**"
            elif active_perms:
                if len(active_perms) > 12:
                    perms_display = ", ".join(active_perms[:12]) + f" and {len(active_perms)-12} more"
                else:
                    perms_display = ", ".join(active_perms)
            else:
                perms_display = "No permissions granted (Standard cosmetic role)"
                
            success_embed.add_field(name="✅ Enabled Permissions", value=perms_display, inline=False)
            success_embed.set_thumbnail(url="https://images.unsplash.com/photo-1614850523459-c2f4c699c52e?auto=format&fit=crop&q=80&w=200")
            success_embed.set_footer(text=f"Role generated by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
            
            await creation_announce.edit(embed=success_embed, view=None)
            
        except Exception as e:
            err_embed = discord.Embed(title="❌ Creation Failed", description=f"An error occurred while creating the role: {e}", color=0xE74C3C)
            await creation_announce.edit(embed=err_embed, view=None)
        return

    if command_name == "addrole":
        if not await check_perms(message, manage_roles=True):
            return
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}addrole <@user/ID> <@role/ID>`")
        
        target_user = await resolve_member(message.guild, args[1], message.mentions)
        target_role = resolve_role(message.guild, args[2], message.role_mentions)
        
        if not target_user:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if not target_role:
            return await send_error_embed(message.channel, "❌ Could not find the specified role.")
        
        # Hierarchy checks
        if not is_admin and message.guild.owner_id != message.author.id:
            if message.author.top_role <= target_role:
                return await send_error_embed(message.channel, "❌ You cannot assign a role that is equal to or higher than your highest role.")
        if message.guild.me.top_role <= target_role:
            return await send_error_embed(message.channel, "❌ I cannot assign a role that is equal to or higher than my highest role. Move my role higher in the server settings.")
            
        try:
            await target_user.add_roles(target_role)
            return await send_embed(message.channel, f"✅ Successfully added {target_role.mention} to {target_user.mention}.", color=0x2ECC71)
        except Exception:
            return await send_error_embed(message.channel, "❌ I lack permission to add this role. Ensure my bot role is higher than the target role.")

    if command_name == "delrole":
        if not await check_perms(message, manage_roles=True):
            return
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}delrole <@user/ID> <@role/ID>`")
        
        target_user = await resolve_member(message.guild, args[1], message.mentions)
        target_role = resolve_role(message.guild, args[2], message.role_mentions)
        
        if not target_user:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if not target_role:
            return await send_error_embed(message.channel, "❌ Could not find the specified role.")
        
        # Hierarchy checks
        if not is_admin and message.guild.owner_id != message.author.id:
            if message.author.top_role <= target_role:
                return await send_error_embed(message.channel, "❌ You cannot remove a role that is equal to or higher than your highest role.")
        if message.guild.me.top_role <= target_role:
            return await send_error_embed(message.channel, "❌ I cannot remove a role that is equal to or higher than my highest role. Move my role higher in the server settings.")
            
        try:
            await target_user.remove_roles(target_role)
            return await send_embed(message.channel, f"✅ Successfully removed {target_role.mention} from {target_user.mention}.", color=0x2ECC71)
        except Exception:
            return await send_error_embed(message.channel, "❌ I lack permission to remove this role. Ensure my bot role is higher than the target role.")

    if command_name == "autorole":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}autorole <@role/ID>` or `{guild_prefix}autorole disable`")
        
        if args[1].lower() == "disable":
            if disable_autorole(message.guild.id):
                return await send_embed(message.channel, "✅ Auto-role on join has been disabled.", color=0x2ECC71)
            else:
                return await send_error_embed(message.channel, "❌ Auto-role was not active.")
        
        target_role = resolve_role(message.guild, args[1], message.role_mentions)
        if not target_role:
            return await send_error_embed(message.channel, "❌ Please specify a valid role tag or role ID.")
        
        set_autorole(message.guild.id, target_role.id)
        return await send_embed(message.channel, f"✅ New members will now automatically receive the {target_role.mention} role on join.", color=0x2ECC71)

    if command_name in ("sleep", "selfmute"):
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}sleep <duration>` (e.g., `30m`, `1h`, `12h`)")
        seconds = parse_duration(args[1])
        if seconds <= 0 or seconds > 86400: 
            return await send_error_embed(message.channel, "❌ Invalid duration. Minimum is `1s` and maximum is `1d`.")
        try:
            await message.author.timeout(datetime.timedelta(seconds=seconds), reason="Voluntary focus/sleep session")
            return await send_embed(message.channel, f"😴 **{message.author.display_name}** has chosen to go to sleep for **{args[1]}**. See you later!", color=0x34495E)
        except Exception:
            return await send_error_embed(message.channel, "❌ I cannot timeout you. My role might be below yours, or I lack Moderate Members permission.")

    if command_name == "massreaction":
        if not await check_perms(message, manage_messages=True):
            return
        if not message.channel_mentions:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}massreaction #channel <emoji1> [emoji2] ...`")
        target_channel = message.channel_mentions[0]
        args = command_body.split()
        emojis = [arg.strip() for arg in args[1:] if not arg.startswith("<#")]
        if not emojis:
            return await send_error_embed(message.channel, "❌ Provide at least one emoji.")
        active_mass_reactions[target_channel.id] = emojis
        return await send_embed(message.channel, f"✅ Mass reaction enabled in {target_channel.mention} with `{' '.join(emojis)}`.", color=0x2ECC71)

    if command_name == "unmassreaction":
        if not await check_perms(message, manage_messages=True):
            return
        target_channel = message.channel_mentions[0] if message.channel_mentions else message.channel
        if target_channel.id in active_mass_reactions:
            del active_mass_reactions[target_channel.id]
            return await send_embed(message.channel, f"🛑 Mass reaction disabled for {target_channel.mention}.", color=0xE74C3C)
        else:
            return await send_error_embed(message.channel, f"❌ Mass reaction is not currently active in {target_channel.mention}.")

    if command_name == "disablereactions":
        if not await check_perms(message, manage_messages=True):
            return
        overwrite = message.channel.overwrites_for(message.guild.default_role)
        overwrite.add_reactions = False
        try:
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, "🔒 Reactions have been disabled for `@everyone`.", color=0xE74C3C)
        except Exception:
            return await send_error_embed(message.channel, "❌ I don't have permission.")

    if command_name == "enablereactions":
        if not await check_perms(message, manage_messages=True):
            return
        overwrite = message.channel.overwrites_for(message.guild.default_role)
        overwrite.add_reactions = None 
        try:
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrite)
            return await send_embed(message.channel, "🔓 Reactions have been enabled for `@everyone`.", color=0x2ECC71)
        except Exception:
            return await send_error_embed(message.channel, "❌ I don't have permission.")

    if command_name == "say":
        if not await check_perms(message, manage_messages=True):
            return
        repeat_text = command_body[len("say"):].strip()
        if not repeat_text:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}say <text>`")
        try:
            await message.delete()
        except Exception:
            pass
        return await send_embed(message.channel, repeat_text)

    if command_name == "poll":
        args = command_body[len("poll"):].strip()
        if not args:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}poll <Question>` or `{guild_prefix}poll <Question> | <Option 1> | <Option 2> | ...`")
        
        try:
            await message.delete()
        except Exception:
            pass

        if "|" in args:
            parts = [p.strip() for p in args.split("|") if p.strip()]
            if len(parts) < 2:
                return await send_error_embed(message.channel, "❌ Please provide at least one option for a multiple-choice poll.")
            
            question = parts[0]
            options = parts[1:11] # Limit to 10 options
            
            poll = discord.Poll(question=question, duration=datetime.timedelta(hours=24))
            for opt in options:
                poll.add_answer(text=opt)
            
            await message.channel.send(poll=poll)
        else:
            # Yes/No poll
            poll = discord.Poll(question=args, duration=datetime.timedelta(hours=24))
            poll.add_answer(text="Yes", emoji="👍")
            poll.add_answer(text="No", emoji="👎")
            
            await message.channel.send(poll=poll)
        return

    if command_name == "embed":
        if not await check_perms(message, manage_messages=True):
            return
        args = command_body[len("embed"):].strip()
        if not args:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}embed title=<text> | desc=<text> | color=<hex>` or `{guild_prefix}embed <JSON>`")
        
        try:
            await message.delete()
        except Exception:
            pass

        embed = None
        if args.startswith("{") and args.endswith("}"):
            try:
                import json
                data = json.loads(args)
                
                title = data.get("title")
                description = data.get("description") or data.get("desc")
                color_val = data.get("color", 0x3498DB)
                
                if isinstance(color_val, str):
                    if color_val.startswith("#"):
                        color_val = int(color_val[1:], 16)
                    else:
                        try:
                            color_val = int(color_val, 16)
                        except ValueError:
                            color_val = 0x3498DB
                
                embed = discord.Embed(title=title, description=description, color=color_val)
                
                if "footer" in data:
                    embed.set_footer(text=data["footer"])
                if "image" in data:
                    embed.set_image(url=data["image"])
                if "thumbnail" in data:
                    embed.set_thumbnail(url=data["thumbnail"])
                if "fields" in data:
                    for field in data["fields"]:
                        embed.add_field(name=field.get("name", "Field"), value=field.get("value", "..."), inline=field.get("inline", False))
            except Exception as e:
                return await send_error_embed(message.channel, f"❌ Invalid JSON format or missing keys. Error: `{e}`")
        else:
            # Parse Key-Value pairs like: title=Hello | desc=World | color=#ff0000
            parts = [p.strip() for p in args.split("|") if p.strip()]
            kwargs = {}
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    kwargs[k.strip().lower()] = v.strip()
            
            title = kwargs.get("title")
            description = kwargs.get("desc") or kwargs.get("description")
            color_str = kwargs.get("color")
            footer = kwargs.get("footer")
            image = kwargs.get("image")
            thumbnail = kwargs.get("thumbnail")
            
            if not title and not description:
                return await send_error_embed(message.channel, "❌ Please specify at least a `title` or `desc`/`description` field.")
            
            color_val = 0x3498DB
            if color_str:
                if color_str.startswith("#"):
                    try:
                        color_val = int(color_str[1:], 16)
                    except ValueError:
                        pass
                else:
                    try:
                        color_val = int(color_str, 16)
                    except ValueError:
                        # try preset colors
                        presets = {
                            "red": 0xE74C3C,
                            "blue": 0x3498DB,
                            "green": 0x2ECC71,
                            "yellow": 0xF1C40F,
                            "orange": 0xE67E22,
                            "purple": 0x9B59B6,
                            "grey": 0x95A5A6,
                            "black": 0x010101
                        }
                        color_val = presets.get(color_str.lower(), 0x3498DB)
            
            embed = discord.Embed(title=title, description=description, color=color_val)
            if footer:
                embed.set_footer(text=footer)
            if image:
                embed.set_image(url=image)
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
        
        if embed:
            return await message.channel.send(embed=embed)

    if command_name == "purge":
        if not await check_perms(message, manage_messages=True):
            return
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}purge <number>`")
        try:
            deleted = await message.channel.purge(limit=min(int(args[1]) + 1, 100))
            confirm_log = await send_embed(message.channel, f"🗑️ Deleted `{len(deleted) - 1}` messages.", color=0x2ECC71)
            await asyncio.sleep(3.0)
            if confirm_log:
                await confirm_log.delete()
        except Exception:
            await send_error_embed(message.channel, "❌ I don't have permission.")
        return

    if command_name == "mute":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}mute <@user/ID> <time> [reason]`")
        target_user = await resolve_member(message.guild, args[1], message.mentions)
        if not target_user:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target_user.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        seconds = parse_duration(args[2])
        if seconds <= 0:
            return await send_error_embed(message.channel, "❌ Invalid time format.")
        reason = " ".join(args[3:]) if len(args) > 3 else "No reason provided."
        try:
            await target_user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
            await send_punishment_dm(target_user, message.guild, "mute", duration_str=args[2], reason=reason)
            
            # Log mute
            log_embed = discord.Embed(
                title="🤫 User Muted",
                description=f"{target_user.mention} was muted by {message.author.mention}.",
                color=0xF1C40F,
                timestamp=datetime.datetime.now()
            )
            log_embed.add_field(name="User", value=f"{target_user} ({target_user.id})", inline=True)
            log_embed.add_field(name="Duration", value=args[2], inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"🤫 **{target_user.name}** has been muted for **{args[2]}**.\n*Reason: {reason}*", color=0xF1C40F)
        except Exception:
            return await send_error_embed(message.channel, "❌ I can't mute that user.")

    if command_name == "unmute":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unmute <@user/ID>`")
        target_user = await resolve_member(message.guild, args[1], message.mentions)
        if not target_user:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        try:
            await target_user.timeout(None)
            
            # Log unmute
            log_embed = discord.Embed(
                title="🔊 User Unmuted",
                description=f"{target_user.mention} was unmuted by {message.author.mention}.",
                color=0x2ECC71,
                timestamp=datetime.datetime.now()
            )
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"✅ **{target_user.name}** has been unmuted.", color=0x2ECC71)
        except Exception:
            return await send_error_embed(message.channel, "❌ I can't unmute that user.")

    if command_name == "kick":
        if not await check_perms(message, kick_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}kick <@user/ID> [reason]`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = " ".join(args[2:]) if len(args) > 2 else "None"
        try:
            await send_punishment_dm(target, message.guild, "kick", reason=reason)
            await target.kick(reason=reason)
            
            # Log kick
            log_embed = discord.Embed(
                title="👢 User Kicked",
                description=f"{target.mention} was kicked by {message.author.mention}.",
                color=0xE67E22,
                timestamp=datetime.datetime.now()
            )
            log_embed.add_field(name="User", value=f"{target} ({target.id})", inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"👢 **{target.name}** was kicked.\n*Reason: {reason}*", color=0xE67E22)
        except Exception:
            return await send_error_embed(message.channel, "❌ I can't kick that user.")

    if command_name == "ban":
        if not await check_perms(message, ban_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}ban <@user/ID> [reason]`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = " ".join(args[2:]) if len(args) > 2 else "None"
        try:
            await send_punishment_dm(target, message.guild, "ban", reason=reason)
            await message.guild.ban(target, reason=reason)
            
            # Log ban
            log_embed = discord.Embed(
                title="⛔ User Banned",
                description=f"{target.mention} was banned by {message.author.mention}.",
                color=0xE74C3C,
                timestamp=datetime.datetime.now()
            )
            log_embed.add_field(name="User", value=f"{target} ({target.id})", inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"⛔ **{target.name}** was permanently banned.\n*Reason: {reason}*", color=0xE74C3C)
        except Exception:
            return await send_error_embed(message.channel, "❌ I can't ban that user.")

    if command_name == "unban":
        if not await check_perms(message, ban_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}unban <user_id>`")
        clean_id = "".join(c for c in args[1] if c.isdigit())
        if not clean_id:
            return await send_error_embed(message.channel, "❌ Please specify a valid user ID.")
        target_id = int(clean_id)
        try:
            await message.guild.unban(discord.Object(id=target_id))
            
            # Log unban
            log_embed = discord.Embed(
                title="🔓 User Unbanned",
                description=f"User ID `{target_id}` was unbanned by {message.author.mention}.",
                color=0x2ECC71,
                timestamp=datetime.datetime.now()
            )
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"🔓 User `{target_id}` has been unbanned.", color=0x2ECC71)
        except discord.NotFound:
            return await send_error_embed(message.channel, "❌ That user ID was not found or is not currently banned.")
        except Exception:
            return await send_error_embed(message.channel, "❌ I don't have permission to unban users.")

    if command_name == "warn":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}warn <@user/ID> [reason]`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        reason = " ".join(args[2:]) if len(args) > 2 else "No reason"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        db_execute("INSERT INTO warnings (guild_id, user_id, reason, enforcer, timestamp) VALUES (?, ?, ?, ?, ?)", (message.guild.id, target.id, reason, message.author.name, ts))
        total_warns = db_fetchone("SELECT COUNT(*) FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))[0]

        embed = discord.Embed(
            title="⚠️ Warning Issued", 
            description=f"**User:** {target.mention} (`{target.id}`)", 
            color=0xFFC107,
            timestamp=datetime.datetime.now()
        )
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="📋 Reason", value=f"> {reason}", inline=False)
        embed.add_field(name="📈 Total Warnings", value=f"**{total_warns}**", inline=True)
        embed.add_field(name="🛡️ Moderator", value=message.author.mention, inline=True)
        embed.set_footer(text=message.guild.name, icon_url=message.guild.icon.url if message.guild.icon else None)
        await message.channel.send(embed=embed)

        # Log warning to modlogs channel
        log_embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"{target.mention} was warned by {message.author.mention}.",
            color=0xF39C12,
            timestamp=datetime.datetime.now()
        )
        log_embed.add_field(name="User", value=f"{target} ({target.id})", inline=True)
        log_embed.add_field(name="Total Warnings", value=str(total_warns), inline=True)
        log_embed.add_field(name="Reason", value=reason, inline=False)
        await log_action(message.guild, log_embed)

        # Warnings threshold punishments
        if total_warns == 3:
            try:
                # 3 warnings -> 1 hour timeout
                duration = datetime.timedelta(hours=1)
                await target.timeout(duration, reason="Auto-mod: 3 Warnings reached")
                await send_embed(message.channel, f"🔇 {target.mention} has been auto-timed out for **1 hour** (3 warnings reached).", color=0xE74C3C)
                
                auto_log = discord.Embed(
                    title="🔇 Auto-Timeout (Threshold)",
                    description=f"{target.mention} was auto-muted because they reached **3 warnings**.",
                    color=0xE74C3C,
                    timestamp=datetime.datetime.now()
                )
                auto_log.add_field(name="User", value=f"{target} ({target.id})", inline=True)
                auto_log.add_field(name="Duration", value="1 hour", inline=True)
                await log_action(message.guild, auto_log)
            except Exception as e:
                print(f"Failed to auto timeout: {e}")
        elif total_warns >= 5:
            try:
                # 5 warnings -> Kick
                await target.kick(reason="Auto-mod: 5 Warnings reached")
                await send_embed(message.channel, f"👢 {target.mention} has been auto-kicked (5 warnings reached).", color=0xE74C3C)
                
                auto_log = discord.Embed(
                    title="👢 Auto-Kick (Threshold)",
                    description=f"{target.mention} was auto-kicked because they reached **5 warnings**.",
                    color=0xE74C3C,
                    timestamp=datetime.datetime.now()
                )
                auto_log.add_field(name="User", value=f"{target} ({target.id})", inline=True)
                await log_action(message.guild, auto_log)
            except Exception as e:
                print(f"Failed to auto kick: {e}")
        return

    if command_name == "warns":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) > 1:
            target = await resolve_member(message.guild, args[1], message.mentions)
            if not target:
                return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        else:
            target = message.author
        warns = db_fetchall("SELECT id, reason, enforcer, timestamp FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
        embed = discord.Embed(title=f"⚠️ Warnings for {target.name}", color=0xF39C12)
        if not warns:
            embed.description = "This user has no warnings."
        else:
            for idx, w in enumerate(warns, 1):
                embed.add_field(name=f"Warning #{idx} (Case #{w[0]})", value=f"**Reason:** {w[1]}\n**By:** {w[2]}\n**Date:** {w[3]}", inline=False)
        return await message.channel.send(embed=embed)

    if command_name == "case":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}case <id>`")
        
        case_id = int(args[1])
        case_data = db_fetchone("SELECT user_id, reason, enforcer, timestamp FROM warnings WHERE id = ? AND guild_id = ?", (case_id, message.guild.id))
        
        if not case_data:
            return await send_error_embed(message.channel, f"❌ Case `#{case_id}` was not found in this server.")
        
        user_id, reason, enforcer, timestamp = case_data
        target_user = message.guild.get_member(user_id)
        user_mention = target_user.mention if target_user else f"<@{user_id}> (User Left Server)"
        
        embed = discord.Embed(title=f"📁 Case Details: #{case_id}", color=0xF39C12)
        embed.add_field(name="User", value=user_mention, inline=True)
        embed.add_field(name="Moderator", value=enforcer, inline=True)
        embed.add_field(name="Timestamp", value=timestamp, inline=True)
        embed.add_field(name="Reason", value=f"```{reason}```", inline=False)
        embed.set_footer(text=f"Requested by {message.author.display_name}")
        return await message.channel.send(embed=embed)

    if command_name == "delcase":
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 2 or not args[1].isdigit():
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}delcase <id>`")
        
        case_id = int(args[1])
        case_data = db_fetchone("SELECT user_id, reason, enforcer, timestamp FROM warnings WHERE id = ? AND guild_id = ?", (case_id, message.guild.id))
        
        if not case_data:
            return await send_error_embed(message.channel, f"❌ Case `#{case_id}` was not found in this server.")
        
        db_execute("DELETE FROM warnings WHERE id = ?", (case_id,))
        
        # Log case deletion
        log_embed = discord.Embed(
            title="🗑️ Case Deleted",
            description=f"Case #{case_id} was deleted by {message.author.mention}.",
            color=0x2ECC71,
            timestamp=datetime.datetime.now()
        )
        await log_action(message.guild, log_embed)
        
        return await send_embed(message.channel, f"🗑️ Warning case **#{case_id}** has been deleted.", color=0x2ECC71)

    if command_name in ("delwarn", "unwarn"):
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}{command_name} <@user/ID> #1`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")
        case_search = re.search(r"#(\d+)", command_body)
        if not case_search:
            return await send_error_embed(message.channel, f"⚠️ Format syntax: `{guild_prefix}{command_name} <@user/ID> #1`")
        
        case_num = int(case_search.group(1))
        user_warns = db_fetchall("SELECT id FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
        
        if case_num < 1 or case_num > len(user_warns):
            return await send_error_embed(message.channel, f"❌ That warning number doesn't exist. User has `{len(user_warns)}` warnings.")
        
        db_id = user_warns[case_num - 1][0]
        db_execute("DELETE FROM warnings WHERE id = ?", (db_id,))

        # Log warning deletion
        log_embed = discord.Embed(
            title="🗑️ Warning Deleted",
            description=f"Warning #{case_num} for {target.mention} was deleted by {message.author.mention}.",
            color=0x2ECC71,
            timestamp=datetime.datetime.now()
        )
        await log_action(message.guild, log_embed)

        return await send_embed(message.channel, f"🗑️ Warning **#{case_num}** has been deleted for <@{target.id}>.", color=0x2ECC71)

    if command_name in ("delwarnsall", "unwarnall"):
        if not await check_perms(message, moderate_members=True):
            return
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}{command_name} <@user/ID>`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id:
            return await send_error_embed(message.channel, "❌ You cannot perform this action on yourself.")

        await send_embed(message.channel, f"Are you sure you want to clear all warnings for <@{target.id}>?\nType `Confirm deletion` to proceed.", color=0xFFFFFF, title="⚠️ Confirm Action")
        
        def check(m):
            return m.author == message.author and m.channel == message.channel and m.content.lower() == "confirm deletion"
        
        try:
            await client.wait_for('message', check=check, timeout=30.0)
            db_execute("DELETE FROM warnings WHERE user_id = ? AND guild_id = ?", (target.id, message.guild.id))
            
            # Log clear all warnings
            log_embed = discord.Embed(
                title="🗑️ All Warnings Cleared",
                description=f"All warnings for {target.mention} were cleared by {message.author.mention}.",
                color=0x2ECC71,
                timestamp=datetime.datetime.now()
            )
            await log_action(message.guild, log_embed)

            await send_embed(message.channel, f"✅ All warnings have been cleared for <@{target.id}>.", color=0x2ECC71)
        except asyncio.TimeoutError:
            await send_error_embed(message.channel, "❌ Deletion cancelled (timeout).")
        return

    # --- COUNTDOWN ---
    if command_name == "countdown":
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}countdown <number>`")
        try:
            val = int(args[1])
        except ValueError:
            return await send_error_embed(message.channel, "❌ Please provide a valid integer.")
        if val > 1000 or val < 1:
            return await send_error_embed(message.channel, "❌ Maximum countdown value is 1000 and minimum is 1.")

        total_val = val
        embed = discord.Embed(title="⏳ Countdown Active", color=0x3498DB)
        embed.description = f"Time remaining: **{val}** seconds\n`[{generate_progress_bar(val, total_val)}]`"
        msg = await message.channel.send(embed=embed)
        
        while val > 0:
            if val > 100:
                step = 25
            elif val > 10:
                step = 5
            else:
                step = 1

            await asyncio.sleep(step)
            val -= step
            if val < 0:
                val = 0
            try:
                embed.description = f"Time remaining: **{val}** seconds\n`[{generate_progress_bar(val, total_val)}]`"
                await msg.edit(embed=embed)
            except discord.NotFound:
                return 
            except discord.HTTPException:
                pass 
        
        try:
            embed.title = "🎉 Countdown Finished!"
            embed.description = f"The timer has run out.\n`[{generate_progress_bar(0, total_val)}]`"
            embed.color = 0x2ECC71
            await msg.edit(embed=embed)
        except Exception:
            pass
        return

    # =========================================================================
    #    TRIVIA & GAME COMMANDS
    # =========================================================================

    if command_name in ("flag", "flagloop", "trivia", "trivialoop", "triviastop"):
        if not is_mod:
            return await send_access_denied(message.channel, "Moderator")
        # Check if there is a configured game channel
        game_chan_row = db_fetchone("SELECT channel_id FROM game_channels WHERE guild_id = ?", (message.guild.id,))
        if game_chan_row:
            game_chan_id = game_chan_row[0]
            if message.channel.id != game_chan_id:
                target_channel = message.guild.get_channel(game_chan_id)
                if target_channel:
                    await send_embed(message.channel, f"🎮 Games are restricted to {target_channel.mention}. Moving the game there!", color=0x3498DB)
                    message.channel = target_channel

    if command_name == "gamechannel":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        args = command_body.split()
        if len(args) < 2:
            current_chan = db_fetchone("SELECT channel_id FROM game_channels WHERE guild_id = ?", (message.guild.id,))
            if current_chan:
                target_chan = message.guild.get_channel(current_chan[0])
                chan_mention = target_chan.mention if target_chan else f"<#{current_chan[0]}>"
                return await send_embed(message.channel, f"🎮 Current game channel is set to {chan_mention}.\nTo clear it, use `{guild_prefix}gamechannel clear`.", color=0x3498DB)
            else:
                return await send_embed(message.channel, f"🎮 No game channel is currently configured. Games can be run in any channel.\nTo configure one, use `{guild_prefix}gamechannel <#channel>`.", color=0x3498DB)
                
        action = args[1].lower()
        if action in ("clear", "none", "reset", "disable"):
            db_execute("DELETE FROM game_channels WHERE guild_id = ?", (message.guild.id,))
            return await send_embed(message.channel, "✅ Game channel restriction has been cleared. Games can now be run anywhere!", color=0x2ECC71)
            
        # Parse channel mention
        target_channel = None
        if message.channel_mentions:
            target_channel = message.channel_mentions[0]
        else:
            # Try parsing raw ID or name
            chan_id_str = args[1].replace("<#", "").replace(">", "")
            try:
                target_channel = message.guild.get_channel(int(chan_id_str))
            except ValueError:
                # Try finding by name
                target_channel = discord.utils.get(message.guild.channels, name=args[1])
                
        if not target_channel:
            return await send_error_embed(message.channel, f"❌ Channel not found. Please mention a valid text channel, e.g. `{guild_prefix}gamechannel #games`.")
            
        if not isinstance(target_channel, discord.TextChannel):
            return await send_error_embed(message.channel, "❌ Game channel must be a text channel.")
            
        db_execute("INSERT INTO game_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id", (message.guild.id, target_channel.id))
        return await send_embed(message.channel, f"✅ All games will now be moved and restricted to {target_channel.mention}!", color=0x2ECC71)

    if command_name == "flag":
        if message.channel.id in active_flag_loops:
            active_flag_loops.discard(message.channel.id)
        await send_random_flag_question(message.channel, is_loop=False)
        return

    if command_name == "flagloop":
        if message.channel.id in active_flag_loops:
            active_flag_loops.discard(message.channel.id)
            if message.channel.id in active_trivia and active_trivia[message.channel.id].get('loop'):
                trivia_info = active_trivia[message.channel.id]
                if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                    trivia_info['timeout_task'].cancel()
                del active_trivia[message.channel.id]
            return await send_embed(message.channel, "⏹️ Flag Loop has been stopped.", color=0xE74C3C)
        else:
            active_flag_loops.add(message.channel.id)
            await send_random_flag_question(message.channel, is_loop=True)
            return

    if command_name == "trivialoop":
        if message.channel.id in active_trivia_loops:
            active_trivia_loops.discard(message.channel.id)
            if message.channel.id in active_trivia and active_trivia[message.channel.id].get('loop'):
                trivia_info = active_trivia[message.channel.id]
                if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                    trivia_info['timeout_task'].cancel()
                del active_trivia[message.channel.id]
            return await send_embed(message.channel, "⏹️ Trivia Loop has been stopped.", color=0xE74C3C)
        else:
            active_trivia_loops.add(message.channel.id)
            await send_random_trivia_question(message.channel, is_loop=True)
            return

    if command_name == "trivia":
        args = command_body.split()
        if len(args) < 2:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}trivia <interval_minutes>`\nExample: `{guild_prefix}trivia 5` (every 5 minutes)")
            
        try:
            interval = float(args[1])
        except ValueError:
            return await send_error_embed(message.channel, "❌ Interval must be a valid number (e.g., 5 or 0.5).")
            
        if interval <= 0:
            return await send_error_embed(message.channel, "❌ Interval must be a positive number.")
            
        if message.channel.id in active_trivia_intervals:
            active_trivia_intervals[message.channel.id].cancel()
            
        task = client.loop.create_task(trivia_interval_loop(message.channel, interval))
        active_trivia_intervals[message.channel.id] = task
        
        await send_random_trivia_question(message.channel)
        
        embed = discord.Embed(
            title="⏱️ Trivia Interval Started",
            description=f"Bot will now appear every **{interval}** minutes to ask a random question!\n🎁 Get **5 XP** for each correct answer!",
            color=0x2ECC71
        )
        await message.channel.send(embed=embed)
        return

    if command_name == "triviastop":
        stopped = False
        if message.channel.id in active_flag_loops:
            active_flag_loops.discard(message.channel.id)
            stopped = True
        if message.channel.id in active_trivia_loops:
            active_trivia_loops.discard(message.channel.id)
            stopped = True
        if message.channel.id in active_trivia_intervals:
            active_trivia_intervals[message.channel.id].cancel()
            del active_trivia_intervals[message.channel.id]
            stopped = True
        if message.channel.id in active_trivia:
            trivia_info = active_trivia[message.channel.id]
            if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                trivia_info['timeout_task'].cancel()
            del active_trivia[message.channel.id]
            stopped = True
            
        if stopped:
            return await send_embed(message.channel, "⏹️ All active trivia games, loops, and intervals in this channel have been stopped.", color=0xE74C3C)
        else:
            return await send_error_embed(message.channel, "❌ There are no active trivia games running in this channel.")

    if command_name in ("hint", "h"):
        # Check if games are restricted to a specific channel
        game_chan_row = db_fetchone("SELECT channel_id FROM game_channels WHERE guild_id = ?", (message.guild.id,))
        if game_chan_row:
            game_chan_id = game_chan_row[0]
            if message.channel.id != game_chan_id:
                target_channel = message.guild.get_channel(game_chan_id)
                if target_channel:
                    return await send_error_embed(message.channel, f"🎮 Hints are only available in the designated game channel: {target_channel.mention}")

        hint_text = get_trivia_hint(message.channel.id)
        if "❌" in hint_text:
            return await send_error_embed(message.channel, hint_text)
        else:
            return await send_embed(message.channel, hint_text, color=0xF1C40F)

    if command_name == "skip":
        # Check if games are restricted to a specific channel
        game_chan_row = db_fetchone("SELECT channel_id FROM game_channels WHERE guild_id = ?", (message.guild.id,))
        if game_chan_row:
            game_chan_id = game_chan_row[0]
            if message.channel.id != game_chan_id:
                target_channel = message.guild.get_channel(game_chan_id)
                if target_channel:
                    return await send_error_embed(message.channel, f"🎮 Games and skips are restricted to the designated game channel: {target_channel.mention}")

        if message.channel.id not in active_trivia:
            return await send_error_embed(message.channel, "❌ There is no active trivia or flag game in this channel to skip!")

        trivia_info = active_trivia[message.channel.id]
        answer_display = trivia_info['answer_display']

        # Moderator bypass: Skip instantly!
        if is_mod:
            # Cancel the timeout task if it exists
            if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                trivia_info['timeout_task'].cancel()

            # Clean up active_trivia
            del active_trivia[message.channel.id]

            embed = discord.Embed(
                title="⏭️ Question Skipped",
                description=f"Moderator {message.author.mention} has bypassed votes and skipped the current question!\n\n💡 Correct answer was: **{answer_display}**",
                color=0xE74C3C
            )
            await message.channel.send(embed=embed)

            # If loop was active, trigger next question
            if trivia_info.get('loop'):
                if message.channel.id in active_flag_loops:
                    async def next_flag_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_flag_loops:
                            await send_random_flag_question(message.channel, is_loop=True)
                    client.loop.create_task(next_flag_after_delay())
                elif message.channel.id in active_trivia_loops:
                    async def next_trivia_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_trivia_loops:
                            await send_random_trivia_question(message.channel, is_loop=True)
                    client.loop.create_task(next_trivia_after_delay())
            return

        # Community vote skip
        if 'skip_votes' not in trivia_info:
            trivia_info['skip_votes'] = set()

        if message.author.id in trivia_info['skip_votes']:
            return await send_error_embed(message.channel, "❌ You have already voted to skip this question!")

        trivia_info['skip_votes'].add(message.author.id)
        votes_count = len(trivia_info['skip_votes'])

        if votes_count >= 2:
            # We reached the skip threshold!
            if 'timeout_task' in trivia_info and trivia_info['timeout_task']:
                trivia_info['timeout_task'].cancel()

            del active_trivia[message.channel.id]

            embed = discord.Embed(
                title="⏭️ Question Skipped",
                description=f"The community has voted to skip this question!\n\n💡 Correct answer was: **{answer_display}**",
                color=0xE74C3C
            )
            await message.channel.send(embed=embed)

            # If loop was active, trigger next question
            if trivia_info.get('loop'):
                if message.channel.id in active_flag_loops:
                    async def next_flag_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_flag_loops:
                            await send_random_flag_question(message.channel, is_loop=True)
                    client.loop.create_task(next_flag_after_delay())
                elif message.channel.id in active_trivia_loops:
                    async def next_trivia_after_delay():
                        await asyncio.sleep(3)
                        if message.channel.id in active_trivia_loops:
                            await send_random_trivia_question(message.channel, is_loop=True)
                    client.loop.create_task(next_trivia_after_delay())
            return
        else:
            bar = "▰▱" if votes_count == 1 else "▱▱"
            embed = discord.Embed(
                title="🗳️ Skip Vote Registered",
                description=f"{message.author.mention} voted to skip the current question.\n\n📊 Progress: `{bar}` **{votes_count}/2** votes needed.\nType `{guild_prefix}skip` to vote!",
                color=0x3498DB
            )
            await message.channel.send(embed=embed)
            return

    if command_name in ("gametop", "triviatop", "gamescores"):
        leaderboard = get_game_leaderboard(message.guild.id, 10)
        if not leaderboard:
            return await send_embed(message.channel, "🏆 **No trivia scores registered yet!**\nStart playing with `v!flag` or `v!trivia` to earn points!", color=0x3498DB)
            
        embed = discord.Embed(title="🏆 Server Game Leaderboard", color=0xF1C40F)
        description_lines = []
        for index, (user_id, score) in enumerate(leaderboard):
            member = message.guild.get_member(user_id)
            user_mention = member.mention if member else f"<@{user_id}>"
            
            medal = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else f"`#{index + 1}`"
            description_lines.append(f"{medal} {user_mention} — **{score}** pts")
            
        embed.description = "\n".join(description_lines)
        embed.set_footer(text="Play games & guess correctly to claim the top spot!")
        await message.channel.send(embed=embed)
        return

    # --- BOT CUSTOMIZATION COMMANDS ---
    if command_name == "botname":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        new_name = command_body[len("botname"):].strip()
        if not new_name:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}botname <name>`")
        
        # Clear database setting so it doesn't trigger webhook fallback
        db_execute("UPDATE ai_settings SET bot_name = NULL WHERE guild_id = ?", (message.guild.id,))
        
        # Update nickname in this guild
        try:
            await message.guild.me.edit(nick=new_name)
            return await send_embed(message.channel, f"✅ Bot nickname successfully updated to **{new_name}** in this server!", color=0x2ECC71)
        except Exception as e:
            return await send_error_embed(message.channel, f"❌ Could not change bot nickname due to missing permissions or error: {e}")

    if command_name == "botavatar":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        url = None
        if message.attachments:
            url = message.attachments[0].url
        else:
            args = command_body.split()
            if len(args) > 1:
                url = args[1]
        
        if not url:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}botavatar <image_url>` or attach an image.")
        
        # Download image bytes
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await send_error_embed(message.channel, "❌ Failed to download the image from the provided URL. Make sure it is a direct image link.")
                    image_bytes = await resp.read()
        except Exception as e:
            return await send_error_embed(message.channel, f"❌ Error downloading image: {e}")
            
        # Try updating the server-specific avatar
        try:
            await message.guild.me.edit(avatar=image_bytes)
            # Clear database setting so it doesn't trigger webhook fallback
            db_execute("UPDATE ai_settings SET bot_avatar = NULL WHERE guild_id = ?", (message.guild.id,))
            return await send_embed(message.channel, "✅ Bot avatar successfully updated to the new profile picture in this server!", color=0x2ECC71)
        except Exception as e:
            return await send_error_embed(message.channel, f"❌ Could not change bot server avatar due to missing permissions or error: {e}")

    if command_name == "botreset":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
            
        # Reset nickname and avatar in guild
        reset_success = False
        reset_err = None
        try:
            try:
                await message.guild.me.edit(nick=None, avatar=None)
            except Exception:
                # fallback to editing individually
                try:
                    await message.guild.me.edit(nick=None)
                except Exception:
                    pass
                try:
                    await message.guild.me.edit(avatar=None)
                except Exception:
                    pass
            reset_success = True
        except Exception as e:
            reset_err = str(e)
            
        # Reset database settings
        db_execute("UPDATE ai_settings SET bot_name = NULL, bot_avatar = NULL WHERE guild_id = ?", (message.guild.id,))
        
        if reset_success:
            return await send_embed(message.channel, "✅ **Bot name and avatar reset successfully!** Restored default profile picture and nickname.", color=0x2ECC71)
        else:
            return await send_embed(message.channel, f"✅ **Bot name and avatar database settings reset!** (Note: Server profile could not be fully reset due to permissions: {reset_err}).", color=0xF1C40F)

    if command_name == "botcontrol":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        bot_control_sessions[message.author.id] = message.channel.id
        try:
            await message.delete()
        except Exception:
            pass
        await send_embed(message.channel, "🤖 **Bot Control Mode Enabled.** Every message you send in this channel (that is not a command) will now be repeated by me. Type `v!unbotcontrol` to disable.", color=0x3498DB)
        return

    if command_name == "unbotcontrol":
        if not is_admin:
            return await send_access_denied(message.channel, "Administrator")
        if message.author.id in bot_control_sessions:
            del bot_control_sessions[message.author.id]
            try:
                await message.delete()
            except Exception:
                pass
            await send_embed(message.channel, "🛑 **Bot Control Mode Disabled.** You are no longer controlling the bot.", color=0xE74C3C)
        else:
            await send_error_embed(message.channel, "❌ You are not currently in Bot Control Mode.")
        return

    # --- BASIC UTILITY COMMANDS ---

    if command_name == "pin":
        if not await check_perms(message, manage_messages=True):
            return
        
        target_message = None
        if message.reference and message.reference.message_id:
            try:
                target_message = await message.channel.fetch_message(message.reference.message_id)
            except discord.NotFound:
                pass
        else:
            args = command_body[len("pin"):].strip()
            if args.isdigit():
                try:
                    target_message = await message.channel.fetch_message(int(args))
                except discord.NotFound:
                    pass
        
        if target_message:
            try:
                await target_message.pin()
                await send_embed(message.channel, "✅ Message pinned successfully.")
            except Exception:
                await send_error_embed(message.channel, "❌ I don't have permission to pin messages.")
            except discord.HTTPException:
                await send_error_embed(message.channel, "❌ Failed to pin the message. The pin limit might be reached.")
        else:
            await send_error_embed(message.channel, f"⚠️ Please reply to a message or provide a valid message ID. Usage: `{guild_prefix}pin [message_id]`")
        return


    if command_name == "unpin":
        if not await check_perms(message, manage_messages=True):
            return
        
        target_message = None
        if message.reference and message.reference.message_id:
            try:
                target_message = await message.channel.fetch_message(message.reference.message_id)
            except discord.NotFound:
                pass
        else:
            args = command_body[len("unpin"):].strip()
            if args.isdigit():
                try:
                    target_message = await message.channel.fetch_message(int(args))
                except discord.NotFound:
                    pass
        
        if target_message:
            try:
                await target_message.unpin()
                await send_embed(message.channel, "✅ Message unpinned successfully.")
            except Exception:
                await send_error_embed(message.channel, "❌ I don't have permission to unpin messages.")
            except discord.HTTPException:
                await send_error_embed(message.channel, "❌ Failed to unpin the message.")
        else:
            await send_error_embed(message.channel, f"⚠️ Please reply to a message or provide a valid message ID. Usage: `{guild_prefix}unpin [message_id]`")
        return

    if command_name == "ping":
        return await send_embed(message.channel, f"🏓 **Pong!** Latency: {round(client.latency * 1000)}ms")

    if command_name == "info":
        now_ts = datetime.datetime.now().timestamp()
        cooldown_key = (message.guild.id, message.author.id)
        if now_ts - help_cooldowns.get(cooldown_key, 0) < HELP_COOLDOWN_SECONDS:
            mins, secs = divmod(int(HELP_COOLDOWN_SECONDS - (now_ts - help_cooldowns.get(cooldown_key, 0))), 60)
            return await send_error_embed(message.channel, f"⏳ You are on cooldown! Please wait **{mins}m {secs}s** before calling info again.")
        
        pages = get_help_pages(message.guild.id, guild_prefix)
        help_cooldowns[cooldown_key] = now_ts
        view = CommandPaginationView(message.author, pages)
        view.message = await message.channel.send(embed=pages[0], view=view)
        return

    if command_name == "shipchance":
        if len(message.mentions) < 2:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}shipchance @user1 @user2`")
        u1, u2 = message.mentions[0], message.mentions[1]
        chance = random.randint(0, 100)
        
        if chance < 15:
            commentary = "Ouch... Best to stay just friends. Like, distant friends."
        elif chance < 40:
            commentary = "There's a tiny spark, but it might just be static electricity."
        elif chance < 65:
            commentary = "A solid connection! Keep talking, who knows where this goes?"
        elif chance < 85:
            commentary = "Highly compatible! The chemistry is definitely sizzling!"
        else:
            commentary = "Match made in heaven! Wedding bells are practically ringing!"
            
        embed = discord.Embed(
            title="Love Compatibility",
            description=f"Match evaluation for:\n**{u1.mention}** and **{u2.mention}**",
            color=0xFF6B8B
        )
        embed.add_field(name="Compatibility", value=f"**{chance}%**", inline=True)
        embed.add_field(name="Verdict", value=commentary, inline=False)
        if u1.display_avatar:
            embed.set_thumbnail(url=u1.display_avatar.url)
        embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)

    if command_name == "roll":
        args = message.content.split()[1:] # Use main message.content splitting to ensure we get args
        if len(args) >= 2 and args[0].isdigit() and args[1].isdigit():
            low, high = min(int(args[0]), int(args[1])), max(int(args[0]), int(args[1]))
        else:
            low, high = 1, 100
        val = random.randint(low, high)
        
        embed = discord.Embed(
            title="Roll Result",
            color=0x9B59B6
        )
        embed.add_field(name="Range", value=f"{low} to {high}", inline=True)
        embed.add_field(name="Result", value=f"**{val}**", inline=True)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed, view=RollDiceView(message.author, low, high))

    if command_name == "coin":
        result = random.choice(['HEADS', 'TAILS'])
        embed = discord.Embed(
            title="Coin Flip",
            color=0xF1C40F
        )
        embed.add_field(name="Result", value=f"**{result}**", inline=True)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed, view=CoinFlipView(message.author))

    if command_name == "8ball":
        question = command_body[len("8ball"):].strip()
        if not question:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}8ball <question>`")
        pos = ["It is certain.", "Yes, definitely.", "You may rely on it.", "Outlook good.", "Signs point to yes."]
        neu = ["Reply hazy, try again.", "Ask again later.", "Better not tell you now."]
        neg = ["Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
        answer = random.choice(pos + neu + neg)
        color = 0x2ECC71 if answer in pos else (0xE74C3C if answer in neg else 0xF1C40F)
        
        embed = discord.Embed(title="Magic 8-Ball", color=color)
        embed.add_field(name="Question", value=f"*{question}*", inline=False)
        embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
        embed.set_footer(text=f"Asked by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        msg = await message.channel.send(embed=embed, view=EightBallView(message.author, question))
        if answer in pos:
            await msg.add_reaction("✅")
        elif answer in neg:
            await msg.add_reaction("❌")
        return

    if command_name == "chance":
        question = command_body[len("chance"):].strip()
        if not question:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}chance <question>`")
        chance = random.randint(-25, 100)
        
        if chance < 0:
            verdict = "Negative probability... It's physically impossible!"
        elif chance < 15:
            verdict = "Absolutely no way."
        elif chance < 40:
            verdict = "Highly unlikely, but stranger things have happened..."
        elif chance < 65:
            verdict = "It's a coin toss. It could go either way!"
        elif chance < 90:
            verdict = "Looking decent! More likely than not."
        else:
            verdict = "It is practically guaranteed!"
            
        embed = discord.Embed(
            title="Probability Oracle",
            color=0x3498DB
        )
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Probability", value=f"**{chance}%**", inline=True)
        embed.add_field(name="Verdict", value=verdict, inline=False)
        embed.set_footer(text=f"Asked by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)

    if command_name == "math":
        expr = command_body[len("math"):].strip()
        if not expr:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}math <expression>`")
        clean_expr = re.sub(r"[^0-9\+\-\*\/\(\)\.\s]", "", expr)
        try:
            result = eval(clean_expr, {'__builtins__': None}, {})
            embed = discord.Embed(
                title="Calculator",
                color=0x2ECC71
            )
            embed.add_field(name="Input", value=f"`{expr}`", inline=True)
            embed.add_field(name="Output", value=f"**{result}**", inline=True)
            embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
            return await message.channel.send(embed=embed)
        except Exception:
            return await send_error_embed(message.channel, "Invalid math expression.")

    if command_name == "translate":
        translate_text = command_body[len("translate"):].strip()
        if not translate_text:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}translate <text> <language>`\nExample: `{guild_prefix}translate Hola amigo english`")
            
        words = translate_text.split()
        if len(words) > 1:
            last_word = words[-1]
            last_word_lower = last_word.lower()
            
            # Map of common language names/codes to ISO codes
            LANGUAGES = {
                "english": "en", "en": "en",
                "spanish": "es", "es": "es",
                "french": "fr", "fr": "fr",
                "german": "de", "de": "de",
                "italian": "it", "it": "it",
                "russian": "ru", "ru": "ru",
                "chinese": "zh-CN", "zh": "zh-CN", "zh-cn": "zh-CN", "chinese simplified": "zh-CN",
                "chinese traditional": "zh-TW", "zh-tw": "zh-TW",
                "japanese": "ja", "ja": "ja",
                "korean": "ko", "ko": "ko",
                "portuguese": "pt", "pt": "pt",
                "dutch": "nl", "nl": "nl",
                "greek": "el", "el": "el",
                "turkish": "tr", "tr": "tr",
                "arabic": "ar", "ar": "ar",
                "hindi": "hi", "hi": "hi",
                "ukrainian": "uk", "uk": "uk",
                "polish": "pl", "pl": "pl",
                "swedish": "sv", "sv": "sv",
                "norwegian": "no", "no": "no",
                "danish": "da", "da": "da",
                "finnish": "fi", "fi": "fi",
                "vietnamese": "vi", "vi": "vi",
                "thai": "th", "th": "th",
                "indonesian": "id", "id": "id",
                "latin": "la", "la": "la",
            }
            
            if last_word_lower in LANGUAGES:
                target_lang = LANGUAGES[last_word_lower]
                text_to_translate = " ".join(words[:-1])
            elif len(last_word_lower) == 2 and last_word_lower.isalpha():
                target_lang = last_word_lower
                text_to_translate = " ".join(words[:-1])
            else:
                target_lang = "en"
                text_to_translate = translate_text
        else:
            target_lang = "en"
            text_to_translate = translate_text

        if not text_to_translate.strip():
            target_lang = "en"
            text_to_translate = translate_text
            
        async with message.channel.typing():
            try:
                url = "https://translate.googleapis.com/translate_a/single"
                params = {
                    "client": "gtx",
                    "sl": "auto",
                    "tl": target_lang,
                    "dt": "t",
                    "q": text_to_translate
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            return await send_error_embed(message.channel, f"Translation failed (Google Translate API returned HTTP {resp.status}).")
                        
                        data = await resp.json()
                        if not data or not data[0]:
                            return await send_error_embed(message.channel, "Translation returned empty result.")
                            
                        translated_text = "".join([segment[0] for segment in data[0] if segment and segment[0]])
                        detected_lang = data[2] if len(data) > 2 else "unknown"
                        
                        # Pretty language names for display
                        LANG_NAMES = {
                            "en": "English", "es": "Spanish", "fr": "French", "de": "German", 
                            "it": "Italian", "ru": "Russian", "zh-cn": "Chinese (Simplified)", 
                            "zh-tw": "Chinese (Traditional)", "ja": "Japanese", "ko": "Korean", 
                            "pt": "Portuguese", "nl": "Dutch", "el": "Greek", "tr": "Turkish", 
                            "ar": "Arabic", "hi": "Hindi", "uk": "Ukrainian", "pl": "Polish", 
                            "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish", 
                            "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "la": "Latin"
                        }
                        
                        source_lang_name = LANG_NAMES.get(detected_lang.lower(), detected_lang.upper())
                        target_lang_name = LANG_NAMES.get(target_lang.lower(), target_lang.upper())
                        
                        embed = discord.Embed(
                            title="🌐 Translate",
                            color=0x3498DB
                        )
                        embed.add_field(name=f"Original ({source_lang_name})", value=text_to_translate, inline=False)
                        embed.add_field(name=f"Translation ({target_lang_name})", value=translated_text, inline=False)
                        embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
                        
                        return await message.channel.send(embed=embed)
            except Exception as e:
                return await send_error_embed(message.channel, f"An error occurred during translation: {e}")

    if command_name == "serverinfo":
        guild = message.guild
        humans = len([m for m in guild.members if not m.bot])
        bots = guild.member_count - humans
        
        # Presence counts
        online = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline = sum(1 for m in guild.members if m.status == discord.Status.offline)
        active = online + idle + dnd

        # Channel breakdowns
        total_channels = len(guild.channels)
        categories = len(guild.categories)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        stage_channels = len(guild.stage_channels) if hasattr(guild, 'stage_channels') else 0

        # Emojis and Stickers
        total_emojis = len(guild.emojis)
        animated_emojis = sum(1 for e in guild.emojis if e.animated)
        static_emojis = total_emojis - animated_emojis
        total_stickers = len(guild.stickers) if hasattr(guild, 'stickers') else 0

        embed = discord.Embed(title=f"🏰 Server Info & Real-Time Stats: {guild.name}", color=0x3498DB)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="👑 Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}\nID: `{guild.owner_id}`", inline=True)
        embed.add_field(name="📅 Created On", value=f"<t:{int(guild.created_at.timestamp())}:D>\n(<t:{int(guild.created_at.timestamp())}:R>)", inline=True)
        embed.add_field(name="🚀 Boost Status", value=f"**Tier {guild.premium_tier}**\n▫️ {guild.premium_subscription_count} Boosts", inline=True)

        embed.add_field(
            name=f"👥 Members ({guild.member_count})",
            value=f"▫️ Humans: **{humans}**\n"
                  f"▫️ Bots: **{bots}**\n"
                  f"▫️ Active: **{active}**",
            inline=True
        )

        embed.add_field(
            name="🟢 Member Statuses",
            value=f"🟢 Online: **{online}**\n"
                  f"🌙 Idle: **{idle}**\n"
                  f"🔴 DND: **{dnd}**\n"
                  f"⚪ Offline: **{offline}**",
            inline=True
        )

        embed.add_field(
            name=f"📺 Channels ({total_channels})",
            value=f"📁 Categories: **{categories}**\n"
                  f"💬 Text: **{text_channels}**\n"
                  f"🔊 Voice: **{voice_channels}**\n"
                  f"🎤 Stage: **{stage_channels}**",
            inline=True
        )

        embed.add_field(
            name="🎭 Roles",
            value=f"▫️ Count: **{len(guild.roles)}**",
            inline=True
        )

        embed.add_field(
            name="😀 Expressiveness",
            value=f"▫️ Emojis: **{total_emojis}** ({static_emojis} static, {animated_emojis} anim)\n"
                  f"▫️ Stickers: **{total_stickers}**",
            inline=True
        )

        # Additional Server Settings
        verification_map = {
            discord.VerificationLevel.none: "None 🔓",
            discord.VerificationLevel.low: "Low 🟢",
            discord.VerificationLevel.medium: "Medium 🟡",
            discord.VerificationLevel.high: "High 🟠",
            discord.VerificationLevel.highest: "Highest 🔴"
        }
        ver_str = verification_map.get(guild.verification_level, str(guild.verification_level).upper())

        embed.add_field(
            name="🛡️ Security Settings",
            value=f"▫️ Verification: **{ver_str}**\n"
                  f"▫️ Content Filter: **{str(guild.explicit_content_filter).replace('_', ' ').title()}**",
            inline=True
        )

        embed.set_footer(text=f"Server ID: {guild.id} • Requested by {message.author}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)

    if command_name == "temprole":
        if not await check_perms(message, manage_roles=True):
            return
        args = command_body.split()
        if len(args) < 4:
            return await send_error_embed(message.channel, f"⚠️ Usage: `{guild_prefix}temprole <@user/ID> <@role/ID> <duration>` (e.g., `10m`, `1h`, `1d`)")
        
        target_user = await resolve_member(message.guild, args[1], message.mentions)
        target_role = resolve_role(message.guild, args[2], message.role_mentions)
        
        if not target_user:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if not target_role:
            return await send_error_embed(message.channel, "❌ Could not find the specified role.")
        
        # Hierarchy checks
        if not is_admin and message.guild.owner_id != message.author.id:
            if message.author.top_role <= target_role:
                return await send_error_embed(message.channel, "❌ You cannot assign a role that is equal to or higher than your highest role.")
        if message.guild.me.top_role <= target_role:
            return await send_error_embed(message.channel, "❌ I cannot assign a role that is equal to or higher than my highest role. Move my role higher in the server settings.")
            
        duration_str = None
        for arg in args[1:]:
            if re.match(r"^\d+[smhd]$", arg.lower()):
                duration_str = arg
                break
                
        if not duration_str:
            return await send_error_embed(message.channel, f"❌ Please specify a valid duration (e.g., `10m`, `2h`, `1d`).")
        
        duration = parse_duration(duration_str)
        if duration <= 0:
            return await send_error_embed(message.channel, "❌ Invalid duration. Use format like `10m`, `2h`, `1d`.")
        
        try:
            await target_user.add_roles(target_role, reason=f"Temporary role granted by {message.author} for {duration_str}.")
            
            # Save to database
            expiry = int(datetime.datetime.now().timestamp()) + duration
            db_execute("INSERT INTO temp_roles (guild_id, user_id, role_id, expiry) VALUES (?, ?, ?, ?) ON CONFLICT (guild_id, user_id, role_id) DO UPDATE SET expiry = EXCLUDED.expiry", 
                       (message.guild.id, target_user.id, target_role.id, expiry))
            
            # Log action
            log_embed = discord.Embed(
                title="🕒 Temporary Role Assigned",
                description=f"{target_user.mention} was given role {target_role.mention} for **{duration_str}**.",
                color=0x3498DB,
                timestamp=datetime.datetime.now()
            )
            log_embed.add_field(name="Target User", value=f"{target_user} ({target_user.id})", inline=True)
            log_embed.add_field(name="Role Given", value=f"{target_role.name} ({target_role.id})", inline=True)
            log_embed.add_field(name="Duration", value=duration_str, inline=True)
            log_embed.add_field(name="Expires", value=f"<t:{expiry}:f>", inline=True)
            log_embed.add_field(name="Moderator", value=message.author.mention, inline=True)
            await log_action(message.guild, log_embed)
            
            return await send_embed(message.channel, f"✅ Granted {target_role.mention} to {target_user.mention} for **{duration_str}**.", color=0x2ECC71)
        except Exception:
            return await send_error_embed(message.channel, "❌ I lack permission to add this role. Make sure my role is higher than the target role.")
        except Exception as e:
            return await send_error_embed(message.channel, f"❌ Failed to assign role. Error: {e}")

    if command_name == "userinfo":
        args = command_body.split()
        if len(args) > 1:
            target = await resolve_member(message.guild, args[1], message.mentions)
            if not target:
                return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        else:
            target = message.author
        color = getattr(target, "color", 0x3498DB)
        embed = discord.Embed(title=f"User Info - {target.display_name}", color=color)
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
            
        embed.add_field(name="Tag", value=f"{target}", inline=True)
        embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Bot", value="Yes" if target.bot else "No", inline=True)
        
        joined_at = getattr(target, "joined_at", None)
        embed.add_field(name="Joined Server", value=f"<t:{int(joined_at.timestamp())}:F>\n(<t:{int(joined_at.timestamp())}:R>)" if joined_at else "Unknown", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(target.created_at.timestamp())}:F>\n(<t:{int(target.created_at.timestamp())}:R>)", inline=True)
        
        roles = [r.mention for r in target.roles if r.name != "@everyone"]
        if len(roles) > 12:
            roles_str = " ".join(roles[:12]) + f" and {len(roles)-12} more"
        else:
            roles_str = " ".join(roles) if roles else "No roles"
            
        embed.add_field(name="Roles", value=roles_str, inline=False)
        embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)

    if command_name == "avatar":
        args = command_body.split()
        if len(args) > 1:
            target = await resolve_member(message.guild, args[1], message.mentions)
            if not target:
                return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        else:
            target = message.author
        color = getattr(target, "color", 0x3498DB)
        embed = discord.Embed(title=f"Avatar - {target.display_name}", color=color)
        embed.set_image(url=target.display_avatar.url)
        embed.description = f"[Download Avatar]({target.display_avatar.url})"
        embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)

    if command_name == "snipe":
        args = command_body.split()
        page = 1
        if len(args) > 1 and args[1].isdigit():
            page = int(args[1])
        
        channel_snipes = sniped_messages.get(channel_id, [])
        if not channel_snipes:
            return await send_error_embed(message.channel, "Nothing to snipe in this channel.")
            
        total_pages = (len(channel_snipes) + 4) // 5
        if page < 1 or page > total_pages:
            return await send_error_embed(message.channel, f"Invalid page {page}. (Max pages: {total_pages})")
            
        embed = discord.Embed(
            title="🎯 Sniped Messages",
            color=0xE74C3C
        )
        
        start_idx = (page - 1) * 5
        end_idx = min(start_idx + 5, len(channel_snipes))
        
        for i in range(start_idx, end_idx):
            content, author, timestamp = channel_snipes[i]
            msg_content = content if content else "*Empty message (possibly attachment/embed only)*"
            if len(msg_content) > 200:
                msg_content = msg_content[:197] + "..."
            time_str = f"<t:{int(timestamp.timestamp())}:R>"
            embed.add_field(name=f"`#{i+1}` {author.display_name} ({time_str})", value=msg_content, inline=False)
            
        embed.set_footer(text=f"Page {page}/{total_pages} • Total snipes: {len(channel_snipes)}")
        return await message.channel.send(embed=embed)

    if command_name == "roleinfo":
        args_text = command_body[len("roleinfo"):].strip()
        target_role = None
        
        # 1. Try to find via mentions
        if message.role_mentions:
            target_role = message.role_mentions[0]
        elif args_text:
            is_small_number = args_text.isdigit() and len(args_text) < 5
            if not is_small_number:
                # Try to look up by ID
                try:
                    role_id = int(args_text)
                    target_role = message.guild.get_role(role_id)
                except ValueError:
                    pass
                
                # Try to look up by exact Name
                if not target_role:
                    target_role = discord.utils.find(lambda r: r.name.lower() == args_text.lower(), message.guild.roles)
                
                # Try to look up by substring
                if not target_role:
                    target_role = discord.utils.find(lambda r: args_text.lower() in r.name.lower(), message.guild.roles)

        if target_role:
            role = target_role
            embed = discord.Embed(title=f"Role Settings: {role.name}", color=role.color)
            
            embed.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
            embed.add_field(name="Color Hex", value=f"`{str(role.color).upper()}`", inline=True)
            embed.add_field(name="Members", value=f"{len(role.members)} users", inline=True)
            
            embed.add_field(name="Created On", value=f"<t:{int(role.created_at.timestamp())}:F>\n(<t:{int(role.created_at.timestamp())}:R>)", inline=True)
            embed.add_field(name="Position", value=f"`#{role.position}` (from bottom)", inline=True)
            embed.add_field(name="Properties", value=f"Hoisted: {'Yes' if role.hoist else 'No'}\n"
                                                        f"Mentionable: {'Yes' if role.mentionable else 'No'}\n"
                                                        f"Managed: {'Yes' if role.managed else 'No'}", inline=True)
            
            # Display key permissions enabled
            perms = [name.replace('_', ' ').title() for name, value in role.permissions if value]
            if len(perms) > 10:
                perms_str = ", ".join(perms[:10]) + f" and {len(perms)-10} more"
            else:
                perms_str = ", ".join(perms) if perms else "No permissions"
            embed.add_field(name="Key Permissions", value=perms_str, inline=False)
            
            embed.set_footer(text=f"Requested by {message.author.display_name}", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
            return await message.channel.send(embed=embed)

        # No specific role requested/found; list all roles in the server (excluding @everyone)
        roles = sorted([r for r in message.guild.roles if not r.is_default()], key=lambda r: r.position, reverse=True)
        if not roles:
            # Fallback to @everyone if no other roles exist
            roles = [message.guild.default_role]
            
        roles_per_page = 5
        total_pages = (len(roles) + roles_per_page - 1) // roles_per_page
        
        start_page = 0
        if args_text:
            try:
                parsed_page = int(args_text)
                if 1 <= parsed_page <= total_pages:
                    start_page = parsed_page - 1
            except ValueError:
                pass
                
        embeds = []
        for p in range(total_pages):
            embed = discord.Embed(
                title=f"🛡️ Server Roles | {message.guild.name}",
                description="List of all server roles (5 roles per page, sorted from highest to lowest position):",
                color=0x3498DB
            )
            start_idx = p * roles_per_page
            end_idx = min(start_idx + roles_per_page, len(roles))
            
            for i in range(start_idx, end_idx):
                r = roles[i]
                role_details = (
                    f"**ID:** `{r.id}`\n"
                    f"**Color:** `{str(r.color).upper()}`\n"
                    f"**Members:** {len(r.members)} • **Position:** {r.position}\n"
                    f"**Mentionable:** {'Yes' if r.mentionable else 'No'} • **Hoisted:** {'Yes' if r.hoist else 'No'}"
                )
                embed.add_field(name=f"#{i+1} {r.name}", value=role_details, inline=False)
                
            embed.set_footer(text=f"Page {p+1}/{total_pages} • Total Roles: {len(roles)} • Requested by {message.author.display_name}")
            embeds.append(embed)
            
        if not embeds:
            return await send_error_embed(message.channel, "No roles found to display.")
            
        view = RolePaginationView(message.author, embeds)
        view.current_page = start_page
        view.update_buttons()
        view.message = await message.channel.send(embed=embeds[start_page], view=view)
        return

    if command_name in ("membercount", "mc"):
        guild = message.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans
        
        # Presence counts
        online = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline = sum(1 for m in guild.members if m.status == discord.Status.offline)
        active = online + idle + dnd
        
        embed = discord.Embed(title="Member Count & Statuses", color=0x2ECC71)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
            
        embed.add_field(name="Total Members", value=f"**{guild.member_count}**", inline=False)
        embed.add_field(name="Humans", value=f"**{humans}**", inline=True)
        embed.add_field(name="Bots", value=f"**{bots}**", inline=True)
        
        embed.add_field(
            name="Real-Time Presence",
            value=f"Online: **{online}**\n"
                  f"Idle: **{idle}**\n"
                  f"DND: **{dnd}**\n"
                  f"Offline: **{offline}**\n"
                  f"Active: **{active}**",
            inline=False
        )
        embed.set_footer(text=f"Server: {guild.name}")
        return await message.channel.send(embed=embed)

    if command_name == "setbio":
        bio_text = command_body[len("setbio"):].strip()
        if len(bio_text) > 200:
            return await send_error_embed(message.channel, "Bio must be 200 characters or fewer.")
        set_bio(message.author.id, bio_text)
        return await send_embed(message.channel, "Your bio has been updated.", color=0x2ECC71)

    if command_name == "profile":
        try:
            args = command_body.split()
            if len(args) > 1:
                target = await resolve_member(message.guild, args[1], message.mentions)
                if not target:
                    return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
            else:
                target = message.author
            bio = get_bio(target.id)
            color = getattr(target, "color", 0x3498DB)
            xp, level = get_level(target.id, message.guild.id)
            
            embed = discord.Embed(title=f"{target.name}'s Profile", color=color)
            if target.display_avatar:
                embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="Bio", value=bio or f"*No bio set. Type `{guild_prefix}setbio (text)` to set bio.*", inline=False)
            embed.add_field(name="Level", value=f"**{level}** ({get_total_xp(level, xp)} total XP)", inline=True)
            embed.add_field(name="Reputation", value=f"{get_rep(target.id, message.guild.id)} Points", inline=True)
            return await message.channel.send(embed=embed)
        except Exception:
            return await send_error_embed(message.channel, "Failed to load profile.")

    if command_name == "rep":
        args = command_body.split()
        if len(args) < 3:
            return await send_error_embed(message.channel, f"Usage: `{guild_prefix}rep <@user/ID> 1` or `-1`")
        target = await resolve_member(message.guild, args[1], message.mentions)
        if not target:
            return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        if target.id == message.author.id or target.bot:
            return await send_error_embed(message.channel, "Invalid target for reputation.")

        remaining = get_rep_cooldown_remaining(message.guild.id, message.author.id, target.id)
        if remaining > 0:
            return await send_error_embed(message.channel, f"You must wait `{max(1, round(remaining/60))}` minutes before changing this user's rep again.")

        delta = 1 if args[2] == "1" else (-1 if args[2] == "-1" else 0)
        if delta == 0:
            return await send_error_embed(message.channel, "Use `1` to add or `-1` to remove.")

        new_total = add_rep(target.id, delta, message.guild.id)
        set_rep_cooldown(message.guild.id, message.author.id, target.id)
        return await send_embed(message.channel, f"**{target.display_name}**'s reputation is now **{new_total}**.", color=0x2ECC71 if delta == 1 else 0xE74C3C)

    if command_name in ("level", "rank"):
        args = command_body.split()
        if len(args) > 1:
            target = await resolve_member(message.guild, args[1], message.mentions)
            if not target:
                return await send_error_embed(message.channel, "❌ Could not find the specified user/member.")
        else:
            target = message.author
        if target.bot:
            return await send_error_embed(message.channel, "Bots don't have levels!")
            
        xp, level = get_level(target.id, message.guild.id)
        rank = get_user_rank(target.id, message.guild.id)
        next_xp = get_xp_required(level)
        
        # Calculate progress in current level
        xp_in_level = xp
        xp_for_next_level = next_xp
        progress_pct = (xp_in_level / xp_for_next_level) * 100 if xp_for_next_level > 0 else 0
        
        # Beautiful progress bar
        bar_length = 15
        filled = max(0, min(bar_length, round((progress_pct / 100) * bar_length)))
        bar = "▰" * filled + "▱" * (bar_length - filled)
        
        embed = discord.Embed(
            title=f"🏆  {target.display_name}'s Rank Card", 
            description=f"Here is the rank and chat activity profile for {target.mention}.",
            color=0xF1C40F if rank == 1 else 0x3498DB
        )
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
            
        embed.add_field(name="✨ Rank", value=f"**#{rank}**" if rank > 0 else "Unranked", inline=True)
        embed.add_field(name="⭐ Level", value=f"**Level {level}**", inline=True)
        embed.add_field(name="💫 Total XP", value=f"**{get_total_xp(level, xp):,}** XP", inline=True)
        
        embed.add_field(
            name=f"📈 Progress to Level {level + 1} ({progress_pct:.1f}%)", 
            value=f"`{bar}`\n**{xp_in_level:,}** / **{xp_for_next_level:,}** XP", 
            inline=False
        )
        embed.set_footer(text=f"Requested by {message.author.display_name} • Keep active to level up!", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
        return await message.channel.send(embed=embed)
        
    if command_name == "leveltop":
        rows = get_level_leaderboard(message.guild.id, 10)
        embed = discord.Embed(title="🌟 Server Level Leaderboard", color=0xF1C40F)
        if message.guild.icon:
            embed.set_thumbnail(url=message.guild.icon.url)
        if not rows:
            embed.description = "No one has gained any XP yet! Start chatting!"
        else:
            lines = []
            for i, (u, lvl, xp) in enumerate(rows, 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
                lines.append(f"{medal} <@{u}> • **Level {lvl}** ({get_total_xp(lvl, xp):,} XP)")
            embed.description = "**The most active members in this server:**\n\n" + "\n".join(lines)
            
        # Add the author's own standing at the bottom
        author_rank = get_user_rank(message.author.id, message.guild.id)
        author_xp, author_level = get_level(message.author.id, message.guild.id)
        if author_rank > 0:
            embed.add_field(
                name="👤 Your Standing", 
                value=f"You are ranked **#{author_rank}** at **Level {author_level}** ({get_total_xp(author_level, author_xp):,} total XP)!",
                inline=False
            )
            
        embed.set_footer(text=f"Server: {message.guild.name} • Keep typing to rank up!")
        return await message.channel.send(embed=embed)

    if command_name == "daily":
        last_claim, streak = get_daily_reward(message.author.id, message.guild.id)
        now_ts = datetime.datetime.now().timestamp()
        
        # Check if 24 hours have passed
        if now_ts - last_claim < 86400:
            remaining = 86400 - (now_ts - last_claim)
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            return await send_error_embed(message.channel, f"You have already claimed your daily reward!\nCome back in **{hours}h {minutes}m**.")
            
        # Check if streak is broken (48 hours)
        if now_ts - last_claim > 172800 and last_claim != 0:
            streak = 0
            
        streak += 1
        xp_reward = 100 + (streak * 10) # Base 100 + 10 per streak day
        if xp_reward > 500:
            xp_reward = 500 # Cap at 500 XP
            
        claim_daily_reward(message.author.id, message.guild.id, streak)
        leveled_up, new_level, new_xp = add_xp(message.author.id, message.guild.id, xp_reward)
        
        embed = discord.Embed(title="🎁 Daily Reward Claimed!", color=0x2ECC71)
        embed.description = f"You received **{xp_reward} XP**!\n🔥 Current Streak: **{streak} days**"
        
        if leveled_up:
            xp_needed = get_xp_required(new_level)
            progress_pct = (new_xp / xp_needed) * 100 if xp_needed > 0 else 0
            bar_length = 15
            filled = max(0, min(bar_length, round((progress_pct / 100) * bar_length)))
            bar = "▰" * filled + "▱" * (bar_length - filled)
            
            embed.color = 0xF1C40F
            embed.title = "🎁  Daily Claim & LEVEL UP!  ✨"
            embed.description = (
                f"You received **{xp_reward} XP**!\n"
                f"🔥 Current Streak: **{streak} days**\n\n"
                f"🎉 **CONGRATULATIONS!** You leveled up to **Level {new_level}**! 🚀\n"
                f"`{bar}` ({progress_pct:.1f}%)\n"
                f"📈 Progress: **{new_xp:,}** / **{xp_needed:,}** XP to Level {new_level + 1}"
            )
            
        if message.author.display_avatar:
            embed.set_thumbnail(url=message.author.display_avatar.url)
        return await message.channel.send(embed=embed)

    if command_name == "event":
        if not message.author.guild_permissions.administrator:
            return await send_error_embed(message.channel, "You must be an administrator to use this command.")
            
        now_ts = datetime.datetime.now().timestamp()
        last_triggered = get_event_cooldown(message.guild.id)
        time_since = now_ts - last_triggered
        if time_since < 12 * 3600:
            remaining = 12 * 3600 - time_since
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            return await send_error_embed(message.channel, f"An event was triggered recently! Please wait **{hours}h {minutes}m**.")
                
        set_event_cooldown(message.guild.id, now_ts)
        event_type = random.choices(["2x_xp", "3x_xp"], weights=[80, 20])[0]
        
        if event_type == "2x_xp":
            active_events[message.guild.id] = "2x_xp"
            embed = discord.Embed(title="🎉 SERVER EVENT: DOUBLE XP! 🎉", description=f"{message.author.mention} triggered a server event!\n\n**2x XP GAIN** is now active for the entire server for the next **1 hour**! Start chatting!", color=0x3498DB)
            await message.channel.send(embed=embed)
            
            async def end_event(guild_id):
                await asyncio.sleep(3600)
                if active_events.get(guild_id) == "2x_xp":
                    del active_events[guild_id]
            asyncio.create_task(end_event(message.guild.id))
            
        elif event_type == "3x_xp":
            active_events[message.guild.id] = "3x_xp"
            embed = discord.Embed(title="🔥 RARE SERVER EVENT: TRIPLE XP! 🔥", description=f"{message.author.mention} triggered a rare server event!\n\n**3x XP GAIN** is now active for the entire server for the next **30 minutes**! Go go go!", color=0xE74C3C)
            await message.channel.send(embed=embed)
            
            async def end_event(guild_id):
                await asyncio.sleep(1800)
                if active_events.get(guild_id) == "3x_xp":
                    del active_events[guild_id]
            asyncio.create_task(end_event(message.guild.id))
            
        return

    if command_name == "reptop":
        rows = get_rep_leaderboard(message.guild.id, 10)
        embed = discord.Embed(title="Reputation Leaderboard", color=0xF1C40F)
        if message.guild.icon:
            embed.set_thumbnail(url=message.guild.icon.url)
        if not rows:
            embed.description = "No reputation data yet. Use `v!rep @user 1` to start rewarding points!"
        else:
            lines = []
            for i, (u, p) in enumerate(rows, 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
                lines.append(f"{medal} <@{u}> • **{p}** points")
            embed.description = "These are the most helpful and loved members of our community!\n\n" + "\n".join(lines)
        embed.set_footer(text=f"Server: {message.guild.name}")
        return await message.channel.send(embed=embed)

    # --- UNRECOGNIZED COMMAND FALLBACK (FUZZY SUGGESTION) ---
    import difflib
    all_builtins = [
        "announce", "sync", "aiask", "addcmd", "delcmd", "disablelinks", "allowlinks", 
        "setlogchannel", "disablelogchannel", "ai", "aioff", "aireset", "aistart", 
        "aisettings", "remindme", "slowmode", "addresponder", "delresponder", 
        "blacklistword", "unblacklistword", "blacklistedwords", "lockdown", "botlock", 
        "botisolate", "restrict", "banping", "prefix", "createrole", "addrole", "delrole", "autorole", 
        "massreaction", "unmassreaction", "disablereactions", "enablereactions", "say", 
        "purge", "mute", "unmute", "kick", "ban", "unban", "warn", "warns", 
        "countdown", "botname", "botavatar", "botreset", "botcontrol", "unbotcontrol", 
        "ping", "info", "shipchance", "roll", "coin", "8ball", "chance", "math", 
        "translate", "serverinfo", "temprole", "userinfo", "avatar", "snipe", 
        "roleinfo", "setbio", "profile", "rep", "leveltop", "daily", "event", "reptop",
        "poll", "embed", "case", "delcase", "flag", "flagloop", "trivia", "trivialoop", "triviastop", "gamechannel", "hint", "h", "gametop", "triviatop", "skip"
    ]
    custom_cmds = db_fetchall("SELECT trigger FROM custom_commands WHERE guild_id = ?", (message.guild.id,))
    all_commands = all_builtins + [row[0] for row in custom_cmds]
    
    matches = difflib.get_close_matches(command_name, all_commands, n=1, cutoff=0.55)
    if matches:
        return await send_error_embed(message.channel, f"❌ Command `{guild_prefix}{command_name}` not found. Did you mean `{guild_prefix}{matches[0]}`?")
    else:
        return await send_error_embed(message.channel, f"❌ Command `{guild_prefix}{command_name}` not found. Type `{guild_prefix}help` for a list of commands.")

if BOT_TOKEN:
    client.run(BOT_TOKEN)
client.run(DISCORD_TOKEN)
