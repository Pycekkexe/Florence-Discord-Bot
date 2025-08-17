import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import sqlite3
from datetime import datetime
import os

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# API Configuration
RIOT_API_KEY = "YOUR_RIOT_API_KEY_HERE"  # Replace with your actual API key
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
    {'summoner_name': 'Gemini Brimstone', 'tag': 'ISAAC', 'region': 'euw1'},
    {'summoner_name': 'Odkleja', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'MoBeeDick', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'Gemini delirium', 'tag': 'isaac', 'region': 'euw1'},
    {'summoner_name': 'Gemini Wkurw', 'tag': 'Isaac', 'region': 'euw1'},
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
    
    for player in DEFAULT_PLAYERS:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO players (discord_user_id, summoner_name, tag_line, puuid, region, last_updated, is_default)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', ('default', player['summoner_name'], player['tag'], '', player['region'], datetime.now(), 1))
        except Exception as e:
            print(f"Error adding default player {player['summoner_name']}#{player['tag']}: {e}")
    
    conn.commit()
    conn.close()
    print(f"✅ Added {len(DEFAULT_PLAYERS)} default players to database")

class TFTRankFetcher:
    def __init__(self, api_key):
        self.api_key = api_key
        
    async def get_summoner_by_riot_id(self, game_name, tag_line, region):
        """Get summoner info by Riot ID (new system)"""
        # Map region to account region for Riot ID lookup
        account_regions = {
            'na1': 'americas', 'br1': 'americas', 'la1': 'americas', 'la2': 'americas', 'oc1': 'americas',
            'euw1': 'europe', 'eune1': 'europe', 'tr1': 'europe', 'ru': 'europe',
            'kr': 'asia', 'jp1': 'asia'
        }
        
        account_region = account_regions.get(region, 'americas')
        
        # First get PUUID from account API
        account_url = f"https://{account_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        headers = {"X-Riot-Token": self.api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(account_url, headers=headers) as response:
                if response.status == 200:
                    account_data = await response.json()
                    puuid = account_data['puuid']
                    
                    # Then get summoner data using PUUID
                    summoner_url = f"{BASE_URL.format(region=region)}/tft/summoner/v1/summoners/by-puuid/{puuid}"
                    async with session.get(summoner_url, headers=headers) as summoner_response:
                        if summoner_response.status == 200:
                            summoner_data = await summoner_response.json()
                            summoner_data['riotIdGameName'] = game_name
                            summoner_data['riotIdTagline'] = tag_line
                            return summoner_data
                        else:
                            raise Exception(f"Summoner API Error: {summoner_response.status}")
                elif response.status == 404:
                    return None
                else:
                    raise Exception(f"Account API Error: {response.status}")
    
    async def get_tft_rank(self, summoner_id, region):
        """Get TFT rank for a summoner"""
        url = f"{BASE_URL.format(region=region)}/tft/league/v1/entries/by-summoner/{summoner_id}"
        headers = {"X-Riot-Token": self.api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # Find TFT ranked entry
                    for entry in data:
                        if entry.get('queueType') == 'RANKED_TFT':
                            return entry
                    return None
                else:
                    raise Exception(f"API Error: {response.status}")

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
    
    # Get all players (default + user added)
    cursor.execute('''
        SELECT summoner_name, tag_line, puuid, region FROM players
        ORDER BY is_default DESC, summoner_name
    ''')
    
    players = cursor.fetchall()
    conn.close()
    
    if not players:
        await ctx.send("❌ No players found in database!")
        return
    
    # Show loading message with custom styling
    loading_embed = discord.Embed(
        title="🔄 Fetching TFT Ranks...",
        description="Please wait while I gather the latest rank data",
        color=0x3498db
    )
    loading_msg = await ctx.send(embed=loading_embed)
    
    player_ranks = []
    
    for summoner_name, tag_line, puuid, region in players:
        try:
            # Get summoner info first
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
                    # Unranked player
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
        except Exception as e:
            print(f"Error fetching rank for {summoner_name}#{tag_line}: {e}")
            # Add as API error if fetch fails
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
    
    # Sort by rank score (highest first)
    player_ranks.sort(key=lambda x: x['score'], reverse=True)
    
    # Create main leaderboard embed
    embed = discord.Embed(
        title="🏆 FLO TFT LEADERBOARD 🏆",
        description="Current rankings for all tracked players",
        color=0xffd700,
        timestamp=datetime.now()
    )
    
    # Split into chunks for multiple fields if needed
    leaderboard_chunks = []
    current_chunk = ""
    
    for i, player in enumerate(player_ranks, 1):
        # Position emoji
        if i == 1:
            position_emoji = "🥇"
        elif i == 2:
            position_emoji = "🥈"
        elif i == 3:
            position_emoji = "🥉"
        else:
            position_emoji = f"#{i}"
        
        # Handle different rank states
        if player['tier'] == 'API_ERROR':
            rank_emoji = "❌"
            rank_text = "API Error"
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
        
        # Format player entry
        player_entry = f"{position_emoji} {rank_emoji} **{player['name']}#{player['tag']}**\n"
        player_entry += f"    🌍 {player['region'].upper()} | {rank_text}\n"
        player_entry += f"    📊 {winrate_text} | Level {player['level']}\n\n"
        
        # Check if adding this entry would exceed Discord's field limit
        if len(current_chunk + player_entry) > 1024:
            leaderboard_chunks.append(current_chunk)
            current_chunk = player_entry
        else:
            current_chunk += player_entry
    
    # Add the last chunk
    if current_chunk:
        leaderboard_chunks.append(current_chunk)
    
    # Add fields to embed
    for i, chunk in enumerate(leaderboard_chunks):
        field_name = "Rankings" if i == 0 else f"Rankings (continued {i+1})"
        embed.add_field(name=field_name, value=chunk, inline=False)
    
    # Add statistics
    total_players = len(player_ranks)
    ranked_players = len([p for p in player_ranks if p['tier'] not in ['UNRANKED', 'API_ERROR']])
    
    stats_text = f"👥 Total Players: {total_players}\n"
    stats_text += f"🎯 Ranked Players: {ranked_players}\n"
    stats_text += f"🔸 Unranked: {total_players - ranked_players}\n"
    
    # Find highest rank
    if ranked_players > 0:
        highest_rank_player = next((p for p in player_ranks if p['tier'] not in ['UNRANKED', 'API_ERROR']), None)
        if highest_rank_player:
            if highest_rank_player['tier'] in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                highest_rank_text = f"{highest_rank_player['tier'].title()} {highest_rank_player['lp']} LP"
            else:
                highest_rank_text = f"{highest_rank_player['tier'].title()} {highest_rank_player['rank']}"
            stats_text += f"👑 Highest: {highest_rank_player['name']} ({highest_rank_text})"
    
    embed.add_field(name="📈 Statistics", value=stats_text, inline=False)
    
    # Footer with additional info
    embed.set_footer(
        text="Use !rank <name> <tag> <region> for detailed player info | Updated every use",
        icon_url="https://storage.googleapis.com/workspace-0f70711f-8b4e-4d94-86f1-2a93ccde5887/image/6c1b1562-0c5e-4d79-9c9d-86f5c0b90e1f.png"
    )
    
    # Update the loading message with results
    await loading_msg.edit(embed=embed)

@bot.command(name='add_player')
async def add_player(ctx, game_name: str, tag_line: str, region: str = "euw1"):
    """Add a player to track their TFT rank using Riot ID (Name#TAG)"""
    region = region.lower()
    valid_regions = ["na1", "euw1", "eune1", "kr", "jp1", "br1", "la1", "la2", "oc1", "tr1", "ru"]
    
    if region not in valid_regions:
        await ctx.send(f"❌ Invalid region. Valid regions: {', '.join(valid_regions)}")
        return
    
    try:
        # Show loading message
        loading_msg = await ctx.send(f"🔍 Searching for summoner **{game_name}#{tag_line}** in **{region.upper()}**...")
        
        # Get summoner info
        summoner_data = await tft_bot.fetcher.get_summoner_by_riot_id(game_name, tag_line, region)
        
        if not summoner_data:
            await loading_msg.edit(content=f"❌ Summoner **{game_name}#{tag_line}** not found in **{region.upper()}**")
            return
        
        # Save to database
        conn = sqlite3.connect('tft_players.db')
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO players (discord_user_id, summoner_name, tag_line, puuid, region, last_updated, is_default)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (str(ctx.author.id), game_name, tag_line, summoner_data['puuid'], region, datetime.now(), 0))
            conn.commit()
            
            await loading_msg.edit(content=f"✅ Successfully added **{game_name}#{tag_line}** from **{region.upper()}** to tracking!")
            
        except sqlite3.IntegrityError:
            await loading_msg.edit(content=f"⚠️ **{game_name}#{tag_line}** from **{region.upper()}** is already being tracked!")
        
        conn.close()
        
    except Exception as e:
        await ctx.send(f"❌ Error adding player: {str(e)}")

@bot.command(name='remove_player')
async def remove_player(ctx, game_name: str, tag_line: str, region: str = "euw1"):
    """Remove a player from tracking"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM players 
        WHERE discord_user_id = ? AND summoner_name = ? AND tag_line = ? AND region = ? AND is_default = 0
    ''', (str(ctx.author.id), game_name, tag_line, region.lower()))
    
    if cursor.rowcount > 0:
        await ctx.send(f"✅ Removed **{game_name}#{tag_line}** from **{region.upper()}** from tracking!")
    else:
        await ctx.send(f"❌ **{game_name}#{tag_line}** from **{region.upper()}** was not found in your tracked players or is a default player!")
    
    conn.commit()
    conn.close()

@bot.command(name='my_players')
async def my_players(ctx):
    """Show all players you're tracking"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT summoner_name, tag_line, region FROM players 
        WHERE discord_user_id = ?
        ORDER BY summoner_name
    ''', (str(ctx.author.id),))
    
    players = cursor.fetchall()
    conn.close()
    
    if not players:
        await ctx.send("❌ You're not tracking any players yet! Use `!add_player <name> <tag> <region>` to start tracking.")
        return
    
    embed = discord.Embed(
        title="📋 Your Tracked Players",
        color=0x3498db,
        timestamp=datetime.now()
    )
    
    player_list = ""
    for summoner_name, tag_line, region in players:
        player_list += f"• **{summoner_name}#{tag_line}** ({region.upper()})\n"
    
    embed.add_field(name="Players", value=player_list, inline=False)
    embed.set_footer(text=f"Total: {len(players)} players")
    
    await ctx.send(embed=embed)

@bot.command(name='default_players')
async def show_default_players(ctx):
    """Show all default players"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT summoner_name, tag_line, region FROM players 
        WHERE is_default = 1
        ORDER BY summoner_name
    ''')
    
    players = cursor.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="🌟 Default Players",
        description="These players are automatically tracked by the bot",
        color=0xf39c12,
        timestamp=datetime.now()
    )
    
    player_list = ""
    for summoner_name, tag_line, region in players:
        player_list += f"• **{summoner_name}#{tag_line}** ({region.upper()})\n"
    
    embed.add_field(name="Players", value=player_list, inline=False)
    embed.set_footer(text=f"Total: {len(players)} default players")
    
    await ctx.send(embed=embed)

@bot.command(name='leaderboard')
async def leaderboard(ctx, scope: str = "all"):
    """Show TFT leaderboard (all/personal/default)"""
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
    
    if scope.lower() == "personal":
        cursor.execute('''
            SELECT summoner_name, tag_line, puuid, region FROM players 
            WHERE discord_user_id = ?
        ''', (str(ctx.author.id),))
        title = f"🏆 {ctx.author.display_name}'s TFT Leaderboard"
    elif scope.lower() == "default":
        cursor.execute('''
            SELECT summoner_name, tag_line, puuid, region FROM players
            WHERE is_default = 1
        ''')
        title = "🌟 Default Players TFT Leaderboard"
    else:
        cursor.execute('''
            SELECT summoner_name, tag_line, puuid, region FROM players
        ''')
        title = f"🏆 TFT Leaderboard - All Players"
    
    players = cursor.fetchall()
    conn.close()
    
    if not players:
        await ctx.send("❌ No players to display in leaderboard!")
        return
    
    # Show loading message
    loading_msg = await ctx.send("🔄 Fetching latest ranks...")
    
    player_ranks = []
    
    for summoner_name, tag_line, puuid, region in players:
        try:
            # Get summoner info first
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
                        'score': score
                    })
                else:
                    # Unranked player
                    player_ranks.append({
                        'name': summoner_name,
                        'tag': tag_line,
                        'region': region,
                        'tier': 'UNRANKED',
                        'rank': '',
                        'lp': 0,
                        'wins': 0,
                        'losses': 0,
                        'score': 0
                    })
        except Exception as e:
            print(f"Error fetching rank for {summoner_name}#{tag_line}: {e}")
            # Add as unranked if API fails
            player_ranks.append({
                'name': summoner_name,
                'tag': tag_line,
                'region': region,
                'tier': 'API_ERROR',
                'rank': '',
                'lp': 0,
                'wins': 0,
                'losses': 0,
                'score': -1
            })
            continue
    
    # Sort by rank score (highest first)
    player_ranks.sort(key=lambda x: x['score'], reverse=True)
    
    # Create embed
    embed = discord.Embed(
        title=title,
        color=0xffd700,
        timestamp=datetime.now()
    )
    
    leaderboard_text = ""
    
    for i, player in enumerate(player_ranks[:15], 1):  # Top 15
        position_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
        
        if player['tier'] == 'API_ERROR':
            rank_text = "❌ API Error"
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
            
            winrate = round((player['wins'] / (player['wins'] + player['losses']) * 100), 1) if (player['wins'] + player['losses']) > 0 else 0
            winrate_text = f"{player['wins']}W {player['losses']}L ({winrate}%)"
        
        leaderboard_text += f"{position_emoji} {rank_emoji if player['tier'] != 'API_ERROR' else ''} **{player['name']}#{player['tag']}** ({player['region'].upper()})\n"
        leaderboard_text += f"    {rank_text} | {winrate_text}\n\n"
    
    if leaderboard_text:
        embed.add_field(name="Rankings", value=leaderboard_text, inline=False)
    else:
        embed.add_field(name="Rankings", value="No ranked players found!", inline=False)
    
    embed.set_footer(text="Use !rank <name> <tag> <region> for individual lookup")
    
    await loading_msg.edit(content="", embed=embed)

@bot.command(name='rank')
async def get_rank(ctx, game_name: str, tag_line: str, region: str = "euw1"):
    """Get TFT rank for a specific player using Riot ID"""
    region = region.lower()
    
    try:
        loading_msg = await ctx.send(f"🔍 Looking up **{game_name}#{tag_line}** in **{region.upper()}**...")
        
        summoner_data = await tft_bot.fetcher.get_summoner_by_riot_id(game_name, tag_line, region)
        
        if not summoner_data:
            await loading_msg.edit(content=f"❌ Summoner **{game_name}#{tag_line}** not found in **{region.upper()}**")
            return
        
        rank_data = await tft_bot.fetcher.get_tft_rank(summoner_data['id'], region)
        
        embed = discord.Embed(
            title=f"🎯 TFT Rank - {game_name}#{tag_line}",
            color=0x3498db,
            timestamp=datetime.now()
        )
        
        if rank_data:
            tier = rank_data.get('tier', 'UNRANKED')
            rank = rank_data.get('rank', '')
            lp = rank_data.get('leaguePoints', 0)
            wins = rank_data.get('wins', 0)
            losses = rank_data.get('losses', 0)
            
            rank_emoji = get_rank_emoji(tier, rank)
            
            if tier in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                rank_text = f"{rank_emoji} {tier.title()} {lp} LP"
            else:
                rank_text = f"{rank_emoji} {tier.title()} {rank} {lp} LP"
            
            winrate = round((wins / (wins + losses) * 100), 1) if (wins + losses) > 0 else 0
            total_games = wins + losses
            
            embed.add_field(name="Current Rank", value=rank_text, inline=True)
            embed.add_field(name="LP", value=f"{lp} LP", inline=True)
            embed.add_field(name="Region", value=region.upper(), inline=True)
            embed.add_field(name="Wins", value=str(wins), inline=True)
            embed.add_field(name="Losses", value=str(losses), inline=True)
            embed.add_field(name="Win Rate", value=f"{winrate}%", inline=True)
            embed.add_field(name="Total Games", value=str(total_games), inline=True)
            
        else:
            embed.add_field(name="Rank Status", value="🔸 Unranked", inline=False)
            embed.add_field(name="Region", value=region.upper(), inline=True)
        
        embed.set_footer(text=f"Summoner Level: {summoner_data.get('summonerLevel', 'Unknown')}")
        
        await loading_msg.edit(content="", embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Error fetching rank: {str(e)}")

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
    `!add_player <name> <tag> <region>` - Add a player to track
    `!remove_player <name> <tag> <region>` - Remove a tracked player
    `!my_players` - Show your tracked players
    `!default_players` - Show default tracked players
    `!leaderboard [all/personal/default]` - Show TFT leaderboard
    `!rank <name> <tag> <region>` - Get rank for any player
    `!help_tft` - Show this help message
    """
    
    embed.add_field(name="Commands", value=commands_text, inline=False)
    
    regions_text = "euw1, eune1, na1, kr, jp1, br1, la1, la2, oc1, tr1, ru"
    embed.add_field(name="Valid Regions", value=regions_text, inline=False)
    
    embed.add_field(
        name="Examples", 
        value="`!flo` - Main leaderboard\n`!add_player Doublelift NA1 na1`\n`!rank Faker T1 kr`", 
        inline=False
    )
    
    default_text = ""
    for player in DEFAULT_PLAYERS:
        default_text += f"• {player['summoner_name']}#{player['tag']} ({player['region'].upper()})\n"
    
    embed.add_field(name="Default Players", value=default_text, inline=False)
    
    embed.set_footer(text="Made with ❤️ for TFT players | Uses new Riot ID system")
    
    await ctx.send(embed=embed)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Use `!help_tft` for command usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")

# Run the bot
if __name__ == "__main__":
    # Replace with your bot token
    BOT_TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
    bot.run(BOT_TOKEN)

