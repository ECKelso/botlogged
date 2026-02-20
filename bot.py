import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------
async def init_db():
    print("[DB] Initialising database…")

    bot.db = await asyncpg.create_pool(DATABASE_URL)

    async with bot.db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                channel_id BIGINT
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_users (
                guild_id TEXT,
                username TEXT,
                last_review TEXT,
                PRIMARY KEY (guild_id, username)
            );
        """)

    print("[DB] Tables ready.")

# ---------------------------------------------------
# Helper: Fetch latest review (your exact scraper)
# ---------------------------------------------------
async def fetch_latest_review(username):
    url = f"https://backloggd.com/u/{username}/reviews/"
    print(f"[Scraper] Fetching: {url}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"[Scraper] HTTP {resp.status} for {username}")
                    return None

                text = await resp.text()

                try:
                    start = text.index("review-card")
                except ValueError:
                    print(f"[Scraper] No review-card found for {username}")
                    return None

                try:
                    link_anchor = text.index("open-review-link", start)
                    link_start = text.index("/u/", link_anchor)
                    link_end = text.index('"', link_start)
                    review_link = "https://backloggd.com" + text[link_start:link_end]
                except ValueError:
                    print(f"[Scraper] Could not extract review link for {username}")
                    return None

                try:
                    img_start = text.index("card-img", start)
                    alt_start = text.index('alt="', img_start) + 5
                    alt_end = text.index('"', alt_start)
                    game_title = text[alt_start:alt_end]
                except ValueError:
                    print(f"[Scraper] Could not extract game title for {username}")
                    game_title = "Unknown Game"

                return {
                    "game": game_title,
                    "link": review_link
                }

        except Exception as e:
            print(f"[Scraper] Exception for {username}: {e}")
            return None

# ---------------------------------------------------
# Slash Commands (EXACT behaviour)
# ---------------------------------------------------
@bot.tree.command(name="setchannel", description="Set the channel for review updates.")
async def setchannel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    channel_id = interaction.channel_id

    async with bot.db.acquire() as conn:
        await conn.execute("""
            INSERT INTO guilds (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id;
        """, guild_id, channel_id)

    await interaction.response.send_message("This channel is now set for Backloggd updates.")

@bot.tree.command(name="adduser", description="Add a Backloggd user to track.")
async def adduser(interaction: discord.Interaction, username: str):
    guild_id = str(interaction.guild_id)
    username = username.lower()

    async with bot.db.acquire() as conn:
        await conn.execute("""
            INSERT INTO tracked_users (guild_id, username, last_review)
            VALUES ($1, $2, NULL)
            ON CONFLICT (guild_id, username) DO NOTHING;
        """, guild_id, username)

    await interaction.response.send_message(f"Added **{username}** to the tracking list.")

@bot.tree.command(name="removeuser", description="Remove a Backloggd user.")
async def removeuser(interaction: discord.Interaction, username: str):
    guild_id = str(interaction.guild_id)
    username = username.lower()

    async with bot.db.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM tracked_users
            WHERE guild_id = $1 AND username = $2;
        """, guild_id, username)

    if result == "DELETE 0":
        await interaction.response.send_message("That user is not being tracked.")
    else:
        await interaction.response.send_message(f"Removed **{username}**.")

@bot.tree.command(name="listusers", description="List all tracked Backloggd users.")
async def listusers(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    async with bot.db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT username FROM tracked_users
            WHERE guild_id = $1;
        """, guild_id)

    if not rows:
        await interaction.response.send_message("No users are being tracked yet.")
        return

    users = "\n".join(f"- {r['username']}" for r in rows)
    await interaction.response.send_message(f"Tracked users:\n{users}")

# ---------------------------------------------------
# Background Task (EXACT behaviour)
# ---------------------------------------------------
@tasks.loop(minutes=5)
async def check_reviews():
    print("[Loop] Checking reviews for all guilds…")

    async with bot.db.acquire() as conn:
        guilds = await conn.fetch("SELECT guild_id, channel_id FROM guilds")

    for guild in guilds:
        guild_id = guild["guild_id"]
        channel_id = guild["channel_id"]

        if not channel_id:
            print(f"[Loop] Guild {guild_id} has no channel set, skipping.")
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"[Loop] Channel {channel_id} not found, skipping.")
            continue

        async with bot.db.acquire() as conn:
            users = await conn.fetch("""
                SELECT username, last_review FROM tracked_users
                WHERE guild_id = $1;
            """, guild_id)

        for user in users:
            username = user["username"]
            last_review = user["last_review"]

            print(f"[Loop] Checking user: {username}")

            latest = await fetch_latest_review(username)
            if not latest:
                print(f"[Loop] Could not fetch review for {username}")
                continue

            review_id = latest["link"]

            if last_review != review_id:
                print(f"[Loop] NEW REVIEW FOUND for {username}: {review_id}")

                async with bot.db.acquire() as conn:
                    await conn.execute("""
                        UPDATE tracked_users
                        SET last_review = $1
                        WHERE guild_id = $2 AND username = $3;
                    """, review_id, guild_id, username)

                await channel.send(
                    f"**{username}** posted a new review!\n"
                    f"**{latest['game']}**\n"
                    f"{latest['link']}"
                )
            else:
                print(f"[Loop] No new review for {username}")

# ---------------------------------------------------
# Ready Event
# ---------------------------------------------------
@bot.event
async def on_ready():
    print("[Bot] Logged in, syncing commands…")

    await init_db()
    await bot.tree.sync()
    check_reviews.start()

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Your Backlogged reviews since 2026 🤩"
        )
    )

    print(f"Logged in as {bot.user}")

bot.run(os.getenv("DISCORD_TOKEN"))

