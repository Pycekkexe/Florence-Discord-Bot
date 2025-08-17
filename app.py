import discord
from discord.ext import commands
import aiohttp
from aiohttp import ClientTimeout, ClientError
import asyncio
import json
import sqlite3
from datetime import datetime
import os
import sys

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# API Configuration
RIOT_API_KEY = ""
BASE_URL = "https://{region}.api.riotgames.com"

# TFT Rank hierarchy for proper sorting
RANK_HIERARCHY = {
    'IRON': 1,
    'BRONZE': 2,
    'SILVER': 3,
    'GOLD': 4,
    'PLATINUM': 5,
    'DIAMOND': 6,
    'MASTER': 7,
    'GRANDMASTER': 8,
    'CHALLENGER': 9
}

TIER_NUMBERS = {
    'IV': 1,
    'III': 2,
    'II': 3,
    'I': 4
}

# Default players to add on startup
DEFAULT_PLAYERS = [
    {'summoner_name': 'Gemini Brimstone', 'tag': 'ISAAC', 'region': 'eune1'},
    {'summoner_name': 'Odkleja', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'MoBeeDick', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'Gemini delirium', 'tag': 'isaac', 'region': 'eune1'},
    {'summoner_name': 'Gemini Wkurw', 'tag': 'Isaac', 'region': 'eune1'},
]

# Database setup
def init_db():
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    cursor.execute('''
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
    ''')
    conn.commit()
    conn.close()

def add_default_players():
    """Add default players to the database"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    # Clear existing default players to avoid duplicates
    cursor.execute('DELETE FROM players WHERE is_default = 1')
    
    for player in DEFAULT_PLAYERS:
        try:
            cursor.execute('''
                INSERT INTO players (discord_user_id, summoner_name, tag_line, puuid, region, last_updated, is_default)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', ('default', player['summoner_name'], player['tag'], '', player['region'], datetime.now(), 1))
            print(f"✅ Added {player['summoner_name']}#{player['tag']} ({player['region']})")
        except Exception as e:
            print(f"❌ Error adding default player {player['summoner_name']}#{player['tag']}: {e}")
    
    conn.commit()
    conn.close()
    print(f"✅ Processed {len(DEFAULT_PLAYERS)} default players")

class TFTRankFetcher:
    def __init__(self, api_key):
        self.api_key = api_key
        
    async def get_summoner_by_riot_id(self, game_name, tag_line, region, max_retries=3):
        """Get summoner info by Riot ID with retry logic"""
        account_regions = {
            'na1': 'americas', 'br1': 'americas', 'la1': 'americas', 'la2': 'americas', 'oc1': 'americas',
            'euw1': 'europe', 'eune1': 'europe', 'tr1': 'europe', 'ru': 'europe',
            'kr': 'asia', 'jp1': 'asia'
        }
        
        account_region = account_regions.get(region, 'europe')
        
        for attempt in range(max_retries):
            try:
                account_url = f"https://{account_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                headers = {"X-Riot-Token": self.api_key}
                
                timeout = ClientTimeout(total=15)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(account_url, headers=headers) as response:
                        if response.status == 200:
                            account_data = await response.json()
                            puuid = account_data['puuid']
                            
                            summoner_url = f"{BASE_URL.format(region=region)}/tft/summoner/v1/summoners/by-puuid/{puuid}"
                            async with session.get(summoner_url, headers=headers) as summoner_response:
                                if summoner_response.status == 200:
                                    summoner_data = await summoner_response.json()
                                    summoner_data['riotIdGameName'] = game_name
                                    summoner_data['riotIdTagline'] = tag_line
                                    return summoner_data
                                elif summoner_response.status == 404:
                                    return None
                                else:
                                    raise Exception(f"Summoner API Error: {summoner_response.status}")
                        elif response.status == 404:
                            return None
                        elif response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', 1))
                            print(f"⚠️ Rate limited, waiting {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            raise Exception(f"Account API Error: {response.status}")
                            
            except (ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️ Attempt {attempt + 1} failed for {game_name}#{tag_line}, retrying in {wait_time}s... Error: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    print(f"❌ All {max_retries} attempts failed for {game_name}#{tag_line}")
                    raise e
        
        return None
    
    async def get_tft_rank(self, summoner_id, region, max_retries=3):
        """Get TFT rank for a summoner with retry logic"""
        for attempt in range(max_retries):
            try:
                url = f"{BASE_URL.format(region=region)}/tft/league/v1/entries/by-summoner/{summoner_id}"
                headers = {"X-Riot-Token": self.api_key}
                
                timeout = ClientTimeout(total=15)
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            for entry in data:
                                if entry.get('queueType') == 'RANKED_TFT':
                                    return entry
                            return None
                        elif response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', 1))
                            print(f"⚠️ Rate limited, waiting {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            raise Exception(f"Rank API Error: {response.status}")
                            
            except (ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️ Rank fetch attempt {attempt + 1} failed, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise e
        
        return None

def get_rank_emoji(tier, rank):
    """Get emoji representation for ranks"""
    emojis = {
        'IRON': '⚫',
        'BRONZE': '🟤', 
        'SILVER': '⚪',
        'GOLD': '🟡',
        'PLATINUM': '🔵',
        'DIAMOND': '💎',
        'MASTER': '🔮',
        'GRANDMASTER': '🔴',
        'CHALLENGER': '👑'
    }
    return emojis.get(tier, '❓')

def calculate_rank_score(tier, rank, lp):
    """Calculate a score for sorting players"""
    tier_score = RANK_HIERARCHY.get(tier, 0) * 1000
    rank_score = TIER_NUMBERS.get(rank, 0) * 100 if rank else 0
    return tier_score + rank_score + lp

class TFTBot:
    def __init__(self):
        self.fetcher = TFTRankFetcher(RIOT_API_KEY)
        init_db()
        add_default_players()

tft_bot = TFTBot()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is ready to track TFT ranks!')
    print(f'Default players loaded: {len(DEFAULT_PLAYERS)}')

@bot.command(name='flo')
async def flo_leaderboard(ctx):
    """Display TFT leaderboard with detailed rank information"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT summoner_name, tag_line, puuid, region FROM players
        ORDER BY is_default DESC, summoner_name
    ''')
    
    players = cursor.fetchall()
    conn.close()
    
    if not players:
        await ctx.send("❌ No players found in database!")
        return
    
    loading_embed = discord.Embed(
        title="🔄 Fetching TFT Ranks...",
        description=f"Please wait while I gather the latest rank data for {len(players)} players",
        color=0x3498db
    )
    loading_msg = await ctx.send(embed=loading_embed)
    
    player_ranks = []
    processed = 0
    
    for summoner_name, tag_line, puuid, region in players:
        try:
            processed += 1
            
            if processed % 2 == 0:
                loading_embed.description = f"Processing player {processed}/{len(players)}... ({summoner_name}#{tag_line})"
                await loading_msg.edit(embed=loading_embed)
            
            summoner_data = await tft_bot.fetcher.get_summoner_by_riot_id(summoner_name, tag_line, region)
            if summoner_data:
                rank_data = await tft_bot.fetcher.get_tft_rank(summoner_data['id'], region)
                
                if rank_data:
                    tier = rank_data.get('tier', 'UNRANKED')
                    rank = rank_data.get('rank', '')
                    lp = rank_data.get('leaguePoints', 0)
                    wins = rank_data.get('wins', 0)
                    losses = rank_data.get('losses', 0)
                    
                    score = calculate_rank_score(tier, rank, lp)
                    
                    player_ranks.append({
                        'name': summoner_name,
                        'tag': tag_line,
                        'region': region,
                        'tier': tier,
                        'rank': rank,
                        'lp': lp,
                        'wins': wins,
                        'losses': losses,
                        'score': score,
                        'level': summoner_data.get('summonerLevel', 'Unknown')
                    })
                else:
                    player_ranks.append({
                        'name': summoner_name,
                        'tag': tag_line,
                        'region': region,
                        'tier': 'UNRANKED',
                        'rank': '',
                        'lp': 0,
                        'wins': 0,
                        'losses': 0,
                        'score': 0,
                        'level': summoner_data.get('summonerLevel', 'Unknown')
                    })
            else:
                player_ranks.append({
                    'name': summoner_name,
                    'tag': tag_line,
                    'region': region,
                    'tier': 'NOT_FOUND',
                    'rank': '',
                    'lp': 0,
                    'wins': 0,
                    'losses': 0,
                    'score': -2,
                    'level': 'Unknown'
                })
                
        except Exception as e:
            print(f"Error fetching rank for {summoner_name}#{tag_line}: {e}")
            player_ranks.append({
                'name': summoner_name,
                'tag': tag_line,
                'region': region,
                'tier': 'API_ERROR',
                'rank': '',
                'lp': 0,
                'wins': 0,
                'losses': 0,
                'score': -1,
                'level': 'Unknown'
            })
            continue
    
    player_ranks.sort(key=lambda x: x['score'], reverse=True)
    
    embed = discord.Embed(
        title="🏆 FLO TFT LEADERBOARD 🏆",
        description="Current rankings for all tracked players",
        color=0xffd700,
        timestamp=datetime.now()
    )
    
    leaderboard_chunks = []
    current_chunk = ""
    
    for i, player in enumerate(player_ranks, 1):
        if i == 1:
            position_emoji = "🥇"
        elif i == 2:
            position_emoji = "🥈"
        elif i == 3:
            position_emoji = "🥉"
        else:
            position_emoji = f"#{i}"
        
        if player['tier'] == 'API_ERROR':
            rank_emoji = "❌"
            rank_text = "API Error"
            winrate_text = "N/A"
        elif player['tier'] == 'NOT_FOUND':
            rank_emoji = "❓"
            rank_text = "Not Found"
            winrate_text = "N/A"
        elif player['tier'] == 'UNRANKED':
            rank_emoji = "🔸"
            rank_text = "Unranked"
            winrate_text = "N/A"
        else:
            rank_emoji = get_rank_emoji(player['tier'], player['rank'])
            if player['tier'] in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                rank_text = f"{player['tier'].title()} {player['lp']} LP"
            else:
                rank_text = f"{player['tier'].title()} {player['rank']} {player['lp']} LP"
            
            total_games = player['wins'] + player['losses']
            if total_games > 0:
                winrate = round((player['wins'] / total_games * 100), 1)
                winrate_text = f"{player['wins']}W {player['losses']}L ({winrate}%)"
            else:
                winrate_text = "No games played"
        
        player_entry = f"{position_emoji} {rank_emoji} **{player['name']}#{player['tag']}**\n"
        player_entry += f"    🌍 {player['region'].upper()} | {rank_text}\n"
        player_entry += f"    📊 {winrate_text} | Level {player['level']}\n\n"
        
        if len(current_chunk + player_entry) > 1024:
            leaderboard_chunks.append(current_chunk)
            current_chunk = player_entry
        else:
            current_chunk += player_entry
    
    if current_chunk:
        leaderboard_chunks.append(current_chunk)
    
    for i, chunk in enumerate(leaderboard_chunks):
        field_name = "Rankings" if i == 0 else f"Rankings (continued {i+1})"
        embed.add_field(name=field_name, value=chunk, inline=False)
    
    total_players = len(player_ranks)
    ranked_players = len([p for p in player_ranks if p['tier'] not in ['UNRANKED', 'API_ERROR', 'NOT_FOUND']])
    
    stats_text = f"👥 Total Players: {total_players}\n"
    stats_text += f"🎯 Ranked Players: {ranked_players}\n"
    stats_text += f"🔸 Unranked: {total_players - ranked_players}\n"
    
    if ranked_players > 0:
        highest_rank_player = next((p for p in player_ranks if p['tier'] not in ['UNRANKED', 'API_ERROR', 'NOT_FOUND']), None)
        if highest_rank_player:
            if highest_rank_player['tier'] in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                highest_rank_text = f"{highest_rank_player['tier'].title()} {highest_rank_player['lp']} LP"
            else:
                highest_rank_text = f"{highest_rank_player['tier'].title()} {highest_rank_player['rank']}"
            stats_text += f"👑 Highest: {highest_rank_player['name']} ({highest_rank_text})"
    
    embed.add_field(name="📈 Statistics", value=stats_text, inline=False)
    
    embed.set_footer(
        text="Use !rank <name> <tag> <region> for detailed player info | Updated every use"
    )
    
    await loading_msg.edit(embed=embed)

@bot.command(name='help_tft')
async def help_tft(ctx):
    """Show help for TFT commands"""
    embed = discord.Embed(
        title="🎮 TFT Rank Bot Commands",
        description="Track and compare Teamfight Tactics ranks using Riot ID!",
        color=0x9b59b6
    )
    
    commands_text = """
    `!flo` - Show detailed TFT leaderboard (main command)
    `!help_tft` - Show this help message
    """
    
    embed.add_field(name="Commands", value=commands_text, inline=False)
    
    default_text = ""
    for player in DEFAULT_PLAYERS:
        default_text += f"• {player['summoner_name']}#{player['tag']} ({player['region'].upper()})\n"
    
    embed.add_field(name="Default Players", value=default_text, inline=False)
    
    embed.set_footer(text="Made with ❤️ for TFT players | Uses new Riot ID system")
    
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Use `!help_tft` for command usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")

# Run the bot with proper token handling
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("❌ Usage: python app.py <BOT_TOKEN>")
        print("Example: python app.py MTQwMDk5NDEyOTM3...")
        sys.exit(1)
    
    BOT_TOKEN = sys.argv[1]
    
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please provide a valid bot token!")
        sys.exit(1)
    
    print("✅ Starting bot...")
    bot.run(BOT_TOKEN)

