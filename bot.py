import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import json
import os

DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------
# Helper: Fetch latest review (robust + fixed version)
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

                # Find the first review card
                try:
                    start = text.index("review-card")
                except ValueError:
                    print(f"[Scraper] No review-card found for {username}")
                    return None

                # Extract review link
                try:
                    link_anchor = text.index("open-review-link", start)
                    link_start = text.index("/u/", link_anchor)
                    link_end = text.index('"', link_start)
                    review_link = "https://backloggd.com" + text[link_start:link_end]
                except ValueError:
                    print(f"[Scraper] Could not extract review link for {username}")
                    return None

                # Extract game title
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
# Slash Commands
# ---------------------------------------------------
@bot.tree.command(name="setchannel", description="Set the channel for review updates.")
async def setchannel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    if guild_id not in data:
        data[guild_id] = {"channel_id": None, "users": {}}

    data[guild_id]["channel_id"] = interaction.channel_id
    save_data(data)

    await interaction.response.send_message("This channel is now set for Backloggd updates.")

@bot.tree.command(name="adduser", description="Add a Backloggd user to track.")
async def adduser(interaction: discord.Interaction, username: str):
    guild_id = str(interaction.guild_id)

    if guild_id not in data:
        data[guild_id] = {"channel_id": None, "users": {}}

    data[guild_id]["users"][username.lower()] = None
    save_data(data)

    await interaction.response.send_message(f"Added **{username}** to the tracking list.")

@bot.tree.command(name="removeuser", description="Remove a Backloggd user.")
async def removeuser(interaction: discord.Interaction, username: str):
    guild_id = str(interaction.guild_id)

    if guild_id in data and username.lower() in data[guild_id]["users"]:
        del data[guild_id]["users"][username.lower()]
        save_data(data)
        await interaction.response.send_message(f"Removed **{username}**.")
    else:
        await interaction.response.send_message("That user is not being tracked.")

@bot.tree.command(name="listusers", description="List all tracked Backloggd users.")
async def listusers(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    if guild_id not in data or not data[guild_id]["users"]:
        await interaction.response.send_message("No users are being tracked yet.")
        return

    users = "\n".join(f"- {u}" for u in data[guild_id]["users"].keys())
    await interaction.response.send_message(f"Tracked users:\n{users}")

# ---------------------------------------------------
# Background Task
# ---------------------------------------------------
@tasks.loop(minutes=5)
async def check_reviews():
    print("[Loop] Checking reviews for all guilds…")
    print("[Debug] Current data:", data)

    for guild_id, info in data.items():
        channel_id = info.get("channel_id")
        if not channel_id:
            print(f"[Loop] Guild {guild_id} has no channel set, skipping.")
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"[Loop] Channel {channel_id} not found, skipping.")
            continue

        for username, last_review in info["users"].items():
            print(f"[Loop] Checking user: {username}")

            latest = await fetch_latest_review(username)
            if not latest:
                print(f"[Loop] Could not fetch review for {username}")
                continue

            review_id = latest["link"]

            if last_review != review_id:
                print(f"[Loop] NEW REVIEW FOUND for {username}: {review_id}")

                data[guild_id]["users"][username] = review_id
                save_data(data)

                await channel.send(
                    f"**{username}** posted a new review!\n"
                    f"**{latest['game']}**\n"
                    f"{latest['link']}"
                )
            else:
                print(f"[Loop] No new review for {username}")

@bot.event
async def on_ready():
    await bot.tree.sync()
    check_reviews.start()

    # Set bot status here
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Your Backlogged reviwes since 2026 🤩"
        )
    )

    print(f"Logged in as {bot.user}")

bot.run(os.getenv("DISCORD_TOKEN"))
