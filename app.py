import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
import sqlite3
from datetime import datetime

RIOT_API_KEY = ""
DISCORD_TOKEN = ""

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "tft_players.db"

# region mapping
ROUTING = {
    "eun1": "europe",
    "euw1": "europe",
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "tr1": "europe",
    "ru": "europe"
}

# ✅ Inicjalizacja DB
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        discord_user_id TEXT,
        summoner_name TEXT,
        tag_line TEXT,
        puuid TEXT,
        region TEXT,
        last_updated TIMESTAMP,
        is_default BOOLEAN DEFAULT 0,
        UNIQUE(summoner_name, tag_line, region)
    )
    """)
    conn.commit()
    conn.close()

init_db()

# 🔄 Load players
def load_players():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT summoner_name, tag_line, region FROM players")
    players = [{"name": row[0], "tag": row[1], "region": row[2]} for row in cursor.fetchall()]
    conn.close()
    return players

async def get_player_rank(name, tag="1", platform_region="eun1"):
    routing_region = ROUTING.get(platform_region, "europe")
    print(f"🔍 {name}#{tag} ({platform_region}/{routing_region})")
    
    timeout = aiohttp.ClientTimeout(total=10)
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        headers = {"X-Riot-Token": RIOT_API_KEY}

        # 1️⃣ Riot ID → puuid
        account_url = f"https://{routing_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
        print(f"🔗 Requesting Riot API: {account_url}")
        async with session.get(account_url, headers=headers) as response:
            print(f"📥 Response status: {response.status}")
            account_data = await response.json()
            puuid = account_data.get("puuid")
            if response.status != 200 or not puuid:
                return {"name": name, "tag": tag, "tier": "NOT_FOUND", "rank": "", "lp": 0, "wins": 0, "losses": 0}

        # 2️⃣ puuid → summonerId
        summoner_url = f"https://{platform_region}.api.riotgames.com/tft/summoner/v1/summoners/by-puuid/{puuid}"
        async with session.get(summoner_url, headers=headers) as response:
            if response.status != 200:
                return {"name": name, "tag": tag, "tier": "NOT_FOUND", "rank": "", "lp": 0, "wins": 0, "losses": 0}
            summoner_data = await response.json()
            summoner_id = summoner_data.get("id")
            if not summoner_id:
                return {"name": name, "tag": tag, "tier": "NOT_FOUND", "rank": "", "lp": 0, "wins": 0, "losses": 0}

        # 3️⃣ rank info
        league_url = f"https://{platform_region}.api.riotgames.com/tft/league/v1/entries/by-summoner/{summoner_id}"
        async with session.get(league_url, headers=headers) as response:
            if response.status != 200:
                return {"name": name, "tag": tag, "tier": "UNRANKED", "rank": "", "lp": 0, "wins": 0, "losses": 0}
            leagues = await response.json()
            if not leagues:
                return {"name": name, "tag": tag, "tier": "UNRANKED", "rank": "", "lp": 0, "wins": 0, "losses": 0}
            entry = leagues[0]
            return {
                "name": name,
                "tag": tag,
                "tier": entry.get("tier", "UNRANKED"),
                "rank": entry.get("rank", ""),
                "lp": entry.get("leaguePoints", 0),
                "wins": entry.get("wins", 0),
                "losses": entry.get("losses", 0)
            }
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"name": name, "tag": tag, "tier": "ERROR", "rank": "", "lp": 0, "wins": 0, "losses": 0}
    finally:
        await session.close()


# 📊 !flo command
@bot.command(name="flo")
async def flo(ctx):
    players = load_players()
    if not players:
        await ctx.send("⚠️ Brak graczy w bazie. Dodaj kogoś komendą !addplayer <name> <tag> <region>.")
        return

    await ctx.send(f"📊 Tracking {len(players)} players...")

    results = []
    for p in players:
        data = await get_player_rank(p["name"], p["tag"], p["region"])
        results.append(data)

    # Sortowanie według tier i LP
    results.sort(key=lambda x: (x['tier'], x['lp']), reverse=True)

    msg = "\n".join([
        f"{r['name']}#{r['tag']}: {r['tier']} {r['rank']} {r['lp']} LP (W:{r['wins']} / L:{r['losses']})"
        for r in results
    ])

    await ctx.send(f"```\n{msg}\n```")

# 🎯 !rank <nick>
@bot.command(name="rank")
async def rank(ctx, summoner_name: str, tag: str = "1", region: str = "eun1"):
    data = await get_player_rank(summoner_name, tag, region)
    await ctx.send(f"🎯 {summoner_name}#{tag} → {data['tier']} {data['rank']} {data['lp']} LP (W:{data['wins']} / L:{data['losses']})")

# ➕ !addplayer (domyślny region eun1)
@bot.command(name="addplayer")
async def addplayer(ctx, summoner_name: str, tag: str):
    region = "eun1"  # domyślny region EUNE
    # Walidacja Riot API
    data = await get_player_rank(summoner_name, tag, region)
    if data["tier"] == "NOT_FOUND" or data["tier"] == "ERROR":
        await ctx.send(f"❌ Nie znaleziono gracza {summoner_name}#{tag} w regionie EUNE.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO players (discord_user_id, summoner_name, tag_line, region) VALUES (?, ?, ?, ?)",
            (str(ctx.author.id), summoner_name, tag, region)
        )
        conn.commit()
        await ctx.send(f"✅ Dodano {summoner_name}#{tag} (EUNE) do bazy.")
    except sqlite3.IntegrityError:
        await ctx.send("⚠️ Taki gracz już istnieje w bazie.")
    finally:
        conn.close()



# 📖 !info
@bot.command(name="info")
async def info(ctx):
    await ctx.send(
        "Florence Bot 🤖\nKomendy:\n"
        "!flo — ranking wszystkich\n"
        "!rank <nick> <tag> <region> — rank jednego gracza\n"
        "!addplayer <nick> <tag> <region> — dodaj gracza do bazy"
    )

bot.run(DISCORD_TOKEN)
