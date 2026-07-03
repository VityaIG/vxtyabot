import discord
import random
import datetime
import asyncio
import re
import aiosqlite
import os
import ast
from discord.ext import tasks
from dotenv import load_load # Для локальной разработки, если есть .env

load_dotenv()

BOT_TOKEN = os.getenv('DISCORD_TOKEN')
COMMAND_PREFIX = "v!"

if not BOT_TOKEN:
    print("❌ ERROR: DISCORD_TOKEN variable is missing in environment settings!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
client = discord.Client(intents=intents)

DB_PATH = "database.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        # Таблица варнов
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reason TEXT,
                enforcer TEXT,
                timestamp TEXT
            )
        """)
        # Таблица AFK
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS afk (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                timestamp TEXT
            )
        """)
        # Таблица настроек (botlock и botisolate)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                vector_id INTEGER PRIMARY KEY,
                type TEXT
            )
        """)
        # Таблица персистентных напоминаний
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                note TEXT,
                trigger_time INTEGER
            )
        """)
        # Репутация и Кулдауны репутации
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reputation (
                user_id INTEGER PRIMARY KEY,
                points INTEGER DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rep_cooldowns (
                giver_id INTEGER PRIMARY KEY,
                last_give_timestamp INTEGER
            )
        """)
        await conn.commit()

# --- ASYNC DATABASE HELPER FUNCTIONS ---
async def get_lock_and_isolate():
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT vector_id FROM system_settings WHERE type = 'lock'") as cursor:
            locked = set(row[0] for row in await cursor.fetchall())
        async with conn.execute("SELECT vector_id FROM system_settings WHERE type = 'isolate'") as cursor:
            isolated = set(row[0] for row in await cursor.fetchall())
    return locked, isolated

async def toggle_setting(vector_id, setting_type):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM system_settings WHERE vector_id = ? AND type = ?", (vector_id, setting_type)) as cursor:
            exists = await cursor.fetchone()
        if exists:
            await conn.execute("DELETE FROM system_settings WHERE vector_id = ? AND type = ?", (vector_id, setting_type))
            removed = True
        else:
            await conn.execute("INSERT INTO system_settings (vector_id, type) VALUES (?, ?)", (vector_id, setting_type))
            removed = False
        await conn.commit()
    return removed

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

# --- BACKGROUND TASKS ---
@tasks.loop(seconds=10)
async def check_reminders():
    await client.wait_until_ready()
    current_timestamp = int(datetime.datetime.now().timestamp())
    
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT id, user_id, channel_id, note FROM reminders WHERE trigger_time <= ?", (current_timestamp,)) as cursor:
            due_reminders = await cursor.fetchall()
        
        for rem_id, user_id, channel_id, note in due_reminders:
            channel = client.get_channel(channel_id)
            if not channel:
                try:
                    channel = await client.fetch_channel(channel_id)
                except Exception:
                    channel = None

            if channel:
                try:
                    await channel.send(f"⏰ <@{user_id}> Напоминание: **{note}**")
                except Exception:
                    pass
            await conn.execute("DELETE FROM reminders WHERE id = ?", (rem_id,))
        await conn.commit()

# --- AUTOMATIC MODLOGS EVENTS ---
async def send_to_modlogs(guild, embed):
    log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass

@client.event
async def on_message_delete(message):
    if message.author.bot or not message.guild: return
    embed = discord.Embed(title="🗑️ Сообщение удалено", color=discord.Color.red(), timestamp=datetime.datetime.now())
    embed.add_field(name="Автор:", value=f"{message.author.mention} ({message.author.name})", inline=True)
    embed.add_field(name="Канал:", value=message.channel.mention, inline=True)
    content = message.content if message.content and message.content.strip() else "*[Вложение, эмбед или пустое сообщение]*"
    embed.add_field(name="Содержимое:", value=content, inline=False)
    await send_to_modlogs(message.guild, embed)

@client.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content: return
    embed = discord.Embed(title="📝 Сообщение отредактировано", color=discord.Color.orange(), timestamp=datetime.datetime.now())
    embed.add_field(name="Автор:", value=f"{before.author.mention} ({before.author.name})", inline=True)
    embed.add_field(name="Канал:", value=before.channel.mention, inline=True)
    
    before_content = before.content if before.content and before.content.strip() else "*[Пусто]*"
    after_content = after.content if after.content and after.content.strip() else "*[Пусто]*"
    
    embed.add_field(name="Было:", value=before_content, inline=False)
    embed.add_field(name="Стало:", value=after_content, inline=False)
    await send_to_modlogs(before.guild, embed)

@client.event
async def on_member_join(member):
    embed = discord.Embed(title="📥 Новый участник", color=discord.Color.green(), timestamp=datetime.datetime.now())
    embed.add_field(name="Аккаунт:", value=f"{member.mention} ({member.name})", inline=False)
    embed.add_field(name="ID аккаунта:", value=f"`{member.id}`", inline=True)
    embed.add_field(name="Возраст аккаунта:", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    await send_to_modlogs(member.guild, embed)

# --- INTERACTIVE PAGINATION VIEW ---
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
            await interaction.response.send_message("❌ Панель заблокирована для других пользователей.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Назад", style=discord.ButtonStyle.primary, custom_id="btn_prev")
    async def left_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Вперед ▶", style=discord.ButtonStyle.primary, custom_id="btn_next")
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
    # Асинхронно создаем таблицы при запуске
    await init_db()
    print(f"System Matrix Live: Logged in as {client.user} (aiosqlite Connected)")
    if not check_reminders.is_running():
        check_reminders.start()

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # --- AFK DETECTOR LOOP (aiosqlite) ---
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (message.author.id,)) as cursor:
            afk_record = await cursor.fetchone()
        if afk_record:
            await conn.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
            await conn.commit()
            
            saved_time = datetime.datetime.strptime(afk_record[1], "%Y-%m-%d %H:%M:%S")
            duration = datetime.datetime.now() - saved_time
            minutes = round(duration.total_seconds() / 60, 1)
            
            embed = discord.Embed(
                description=f"👋 С возвращением {message.author.mention}! AFK статус снят. (Был в AFK: {minutes} мин.)",
                color=65280
            )
            await message.channel.send(embed=embed)

        if message.mentions:
            for target in message.mentions:
                async with conn.execute("SELECT reason FROM afk WHERE user_id = ?", (target.id,)) as cursor:
                    mention_afk = await cursor.fetchone()
                if mention_afk:
                    embed = discord.Embed(
                        description=f"💤 **{target.name}** сейчас AFK: {mention_afk[0]}",
                        color=15844367
                    )
                    await message.channel.send(embed=embed)

    # --- АВТОМАТИЧЕСКАЯ СИСТЕМА РЕПУТАЦИИ (aiosqlite) ---
    thank_words = {"ty", "tysm", "thanks", "thank you", "thx", "thank u"}
    clean_content = message.content.strip().lower()
    
    if message.reference and message.reference.message_id and clean_content in thank_words:
        try:
            replied_message = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
            if replied_message and replied_message.author.id != message.author.id and not replied_message.author.bot:
                current_time = int(datetime.datetime.now().timestamp())
                cooldown_duration = 1800 
                
                async with aiosqlite.connect(DB_PATH) as conn:
                    async with conn.execute("SELECT last_give_timestamp FROM rep_cooldowns WHERE giver_id = ?", (message.author.id,)) as cursor:
                        cooldown_record = await cursor.fetchone()
                    
                    if cooldown_record and (current_time - cooldown_record[0] < cooldown_duration):
                        remaining_minutes = round((cooldown_duration - (current_time - cooldown_record[0])) / 60)
                        warn_msg = await message.channel.send(f"⏱️ <@{message.author.id}>, вы можете благодарить снова через {remaining_minutes} мин.")
                        await asyncio.sleep(4)
                        try: await warn_msg.delete()
                        except: pass
                    else:
                        await conn.execute("INSERT OR REPLACE INTO rep_cooldowns (giver_id, last_give_timestamp) VALUES (?, ?)", (message.author.id, current_time))
                        await conn.execute("INSERT INTO reputation (user_id, points) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET points = points + 1", (replied_message.author.id,))
                        async with conn.execute("SELECT points FROM reputation WHERE user_id = ?", (replied_message.author.id,)) as cursor:
                            new_points = (await cursor.fetchone())[0]
                        await conn.commit()
                        
                        embed = discord.Embed(
                            description=f"✨ {message.author.mention} поблагодарил {replied_message.author.mention}!\n**Репутация {replied_message.author.name} увеличилась:** `⭐ {new_points}`",
                            color=discord.Color.gold()
                        )
                        await message.channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка системы репутации: {e}")

    raw_text = message.content.strip()
    prefix_lower = COMMAND_PREFIX.lower()
    if not raw_text.lower().startswith(prefix_lower): return

    command_body = raw_text[len(COMMAND_PREFIX):].strip()
    command_lower = command_body.lower()

    # --- GLOBAL INTERCEPTOR ---
    is_admin = message.author.guild_permissions.administrator
    channel_id = message.channel.id
    category_id = message.channel.category_id if hasattr(message.channel, 'category_id') else None

    if not is_admin:
        locked_channels, isolated_vectors = await get_lock_and_isolate()
        if isolated_vectors and (channel_id not in isolated_vectors) and (category_id not in isolated_vectors): return
        if channel_id in locked_channels or (category_id and category_id in locked_channels): return

    # --- COMMANDS ---
    if command_lower == "ping":
        await message.channel.send(f"🏓 **Pong!** Задержка: {round(client.latency * 1000)}ms")
        return

    if command_lower.startswith("say"):
        repeat_text = command_body[3:].strip()
        if not repeat_text: return
        try: await message.delete()
        except: pass
        await message.channel.send(repeat_text)
        return

    if command_lower.startswith("rep"):
        if command_lower.startswith("reptop"):
            async with aiosqlite.connect(DB_PATH) as conn:
                async with conn.execute("SELECT user_id, points FROM reputation ORDER BY points DESC LIMIT 10") as cursor:
                    top_users = await cursor.fetchall()
            
            embed = discord.Embed(title="🏆 Таблица лидеров по репутации", color=discord.Color.gold())
            if not top_users:
                embed.description = "Пока здесь пусто."
            else:
                description_text = ""
                for idx, (user_id, points) in enumerate(top_users, 1):
                    description_text += f"**{idx}.** <@{user_id}> — `{points}` ⭐\n"
                embed.description = description_text
            await message.channel.send(embed=embed)
            return
        else:
            target = message.mentions[0] if message.mentions else message.author
            async with aiosqlite.connect(DB_PATH) as conn:
                async with conn.execute("SELECT points FROM reputation WHERE user_id = ?", (target.id,)) as cursor:
                    record = await cursor.fetchone()
            points = record[0] if record else 0
            await message.channel.send(f"🌟 У пользователя **{target.name}** `{points}` очков репутации.")
            return

    if command_lower.startswith("mute"):
        if not message.author.guild_permissions.moderate_members: return
        if not message.mentions: return
        args = command_body.split()
        if len(args) < 3: return
        target_user = message.mentions[0]
        seconds = parse_duration(args[2])
        if seconds <= 0: return
        reason = " ".join(args[3:]) if len(args) > 3 else "Нарушение правил."
        try:
            await target_user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
            await message.channel.send(f"🤫 **{target_user.name}** отправлен в мут на **{args[2]}**. Причина: *{reason}*")
        except: pass
        return

    if command_lower.startswith("unmute"):
        if not message.author.guild_permissions.moderate_members: return
        if not message.mentions: return
        target_user = message.mentions[0]
        try:
            await target_user.timeout(None)
            await message.channel.send(f"🔊 Мут с пользователя **{target_user.name}** снят.")
        except: pass
        return

    if command_lower.startswith("massrole"):
        if not is_admin: return
        if not message.role_mentions: return
        target_role = message.role_mentions[0]
        status_msg = await message.channel.send(f"🔄 Выдаю роль всем... Это займет время.")
        success = 0
        async for member in message.guild.fetch_members(limit=None):
            if target_role not in member.roles and not member.bot:
                try:
                    await member.add_roles(target_role)
                    success += 1
                    await asyncio.sleep(0.3)
                except discord.Forbidden: pass
        await status_msg.edit(content=f"✅ Роль {target_role.name} выдана `{success}` участникам!")
        return

    if command_lower.startswith("embed"):
        if not message.author.guild_permissions.manage_messages: return
        text = command_body[5:].strip()
        if not text: return
        try: await message.delete()
        except: pass
        await message.channel.send(embed=discord.Embed(description=text, color=discord.Color.blue()))
        return

    if command_lower.startswith("remindme"):
        args = command_body.split()
        if len(args) < 3: return
        seconds = parse_duration(args[1])
        if seconds <= 0: return
        note = " ".join(args[2:])
        trigger_timestamp = int(datetime.datetime.now().timestamp()) + seconds
        
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("INSERT INTO reminders (user_id, channel_id, note, trigger_time) VALUES (?, ?, ?, ?)",
                               (message.author.id, message.channel.id, note, trigger_timestamp))
            await conn.commit()
        await message.channel.send(f"⏰ Напоминание сохранено в асинхронную базу данных на срок: {args[1]}.")
        return

    if command_lower.startswith("math"):
        expr = command_body[4:].strip()
        clean_expr = re.sub(r'[^0-9\+\-\*\/\(\)\.\s]', '', expr)
        try:
            node = ast.parse(clean_expr, mode='eval')
            result = ast.literal_eval(node)
            await message.channel.send(f"📊 `{expr}` = **{result}**")
        except Exception:
            await message.channel.send("❌ Ошибка математического синтаксиса.")
        return

    if command_lower.startswith("warns"):
        if not message.mentions: return
        target = message.mentions[0]
        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute("SELECT reason, enforcer, timestamp FROM warnings WHERE user_id = ?", (target.id,)) as cursor:
                user_warns = await cursor.fetchall()
        
        embed = discord.Embed(title=f"История предупреждений: {target.name}", color=15158332)
        for idx, warn in enumerate(user_warns, 1):
            embed.add_field(name=f"Нарушение #{idx}", value=f"**Причина:** {warn[0]}\n**Модератор:** {warn[1]}\n**Дата:** {warn[2]}", inline=False)
        await message.channel.send(embed=embed)
        return

    elif command_lower.startswith("warn"):
        if not message.author.guild_permissions.manage_messages: return
        if not message.mentions: return
        target = message.mentions[0]
        args = command_body.split(maxsplit=2)
        reason = args[2] if len(args) > 2 else "Нарушение правил сервера."
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("INSERT INTO warnings (user_id, reason, enforcer, timestamp) VALUES (?, ?, ?, ?)", (target.id, reason, message.author.name, timestamp))
            async with conn.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (target.id,)) as cursor:
                total_warns = (await cursor.fetchone())[0]
            await conn.commit()
        await message.channel.send(f"⚠️ {target.mention} получил варн. Всего варнов: {total_warns}")
        return

    if command_lower.startswith("afk"):
        reason = command_body[3:].strip() or "Отсутствует."
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("INSERT OR REPLACE INTO afk (user_id, reason, timestamp) VALUES (?, ?, ?)", (message.author.id, reason, timestamp_str))
            await conn.commit()
        await message.channel.send(f"💤 {message.author.mention} ушел в AFK: **{reason}**")
        return

    if command_lower.startswith("botlock"):
        if not is_admin: return
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else channel_id)
        removed = await toggle_setting(target_id, "lock")
        await message.channel.send(f"🔓 Настройки изменены для ID `{target_id}`" if removed else f"🔒 Вектор ID `{target_id}` заморожен.")
        return

    if command_lower.startswith("botisolate"):
        if not is_admin: return
        args = command_body.split()
        target_id = message.channel_mentions[0].id if message.channel_mentions else (int(args[1]) if len(args) > 1 and args[1].isdigit() else channel_id)
        removed = await toggle_setting(target_id, "isolate")
        await message.channel.send(f"🔓 Изоляция снята для ID `{target_id}`" if removed else f"🛡️ Режим изоляции. Команды разрешены только в ID `{target_id}`.")
        return

    if command_lower == "commands":
        embed1 = discord.Embed(title="🛠️ Общие команды & Репутация", color=10181046)
        embed1.add_field(name="v!rep [@user]", value="Посмотреть очки репутации.")
        embed1.add_field(name="v!reptop", value="Топ-10 участников по репутации.")
        embed1.add_field(name="v!remindme <время> <текст>", value="Бессмертное напоминание.")
        
        embed2 = discord.Embed(title="🛡️ Модерация", color=15158332)
        embed2.add_field(name="v!mute @user <время>", value="Выдать тайм-аут.")
        embed2.add_field(name="Ответ на сообщение + 'ty'", value="Повысить репутацию человеку.")
        
        pagination_view = CommandPaginationView(author=message.author, embeds=[embed1, embed2])
        sent_message = await message.channel.send(embed=embed1, view=pagination_view)
        pagination_view.message = sent_message
        return

if BOT_TOKEN:
    client.run(BOT_TOKEN)
