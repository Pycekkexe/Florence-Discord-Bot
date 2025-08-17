import discord
from discord.ext import commands
import aiohttp
from aiohttp import ClientTimeout, ClientError
import asyncio
import json
import sqlite3
from datetime import datetime
import os
import socket

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# API Configuration
RIOT_API_KEY = ""
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your actual bot token
BASE_URL = "https://{region}.api.riotgames.com"

# Configure socket timeout
socket.setdefaulttimeout(30)

# TFT Rank hierarchy for proper sorting
RANK_HIERARCHY = {
    'IRON': 1, 'BRONZE': 2, 'SILVER': 3, 'GOLD': 4, 'PLATINUM': 5,
    'DIAMOND': 6, 'MASTER': 7, 'GRANDMASTER': 8, 'CHALLENGER': 9
}

TIER_NUMBERS = {'IV': 1, 'III': 2, 'II': 3, 'I': 4}

# Default players
DEFAULT_PLAYERS = [
    {'summoner_name': 'Gemini Brimstone', 'tag': 'ISAAC', 'region': 'eune1'},
    {'summoner_name': 'Odkleja', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'MoBeeDick', 'tag': 'EUNE', 'region': 'eune1'},
    {'summoner_name': 'Gemini delirium', 'tag': 'isaac', 'region': 'eune1'},
    {'summoner_name': 'Gemini Wkurw', 'tag': 'Isaac', 'region': 'eune1'},
]

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
    conn = sqlite3.connect('tft_players.db')
    cursor = conn.cursor()
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

async def test_connectivity():
    """Test network connectivity and DNS resolution"""
    test_hosts = [
        'google.com',
        'europe.api.riotgames.com',
        'eune1.api.riotgames.com'
    ]
    
    print("🔍 Testing network connectivity...")
    
    for host in test_hosts:
        try:
            # Test DNS resolution
            socket.getaddrinfo(host, 443, socket.AF_INET)
            print(f"✅ DNS OK: {host}")
            
            # Test HTTP connection
            connector = aiohttp.TCPConnector(
                resolver=aiohttp.resolver.AsyncResolver(
                    nameservers=['8.8.8.8', '8.8.4.4', '1.1.1.1']
                ),
                ttl_dns_cache=300,
                use_dns_cache=True,
                enable_cleanup_closed=True
            )
            
            timeout = ClientTimeout(total=10)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                try:
                    async with session.get(f'https://{host}', ssl=False) as response:
                        print(f"✅ HTTP OK: {host} (Status: {response.status})")
                except Exception as e:
                    print(f"⚠️ HTTP Issue: {host} - {e}")
                    
        except socket.gaierror as e:
            print(f"❌ DNS FAILED: {host} - {e}")
        except Exception as e:
            print(f"❌ Connection FAILED: {host} - {e}")

class TFTRankFetcher:
    def __init__(self, api_key):
        self.api_key = api_key
        
    async def create_session(self):
        """Create aiohttp session with custom DNS and SSL settings"""
        connector = aiohttp.TCPConnector(
            resolver=aiohttp.resolver.AsyncResolver(
                nameservers=['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1']
            ),
            ttl_dns_cache=300,
            use_dns_cache=True,
            enable_cleanup_closed=True,
            ssl=False  # Disable SSL verification if needed
        )
        
        timeout = ClientTimeout(total=20, connect=10)
        return aiohttp.ClientSession(connector=connector, timeout=timeout)
        
    async def get_summoner_by_riot_id(self, game_name, tag_line, region, max_retries=3):
        """Get summoner info by Riot ID with enhanced error handling"""
        account_regions = {
            'na1': 'americas', 'br1': 'americas', 'la1': 'americas', 'la2': 'americas', 'oc1': 'americas',
            'euw1': 'europe', 'eune1': 'europe', 'tr1': 'europe', 'ru': 'europe',
            'kr': 'asia', 'jp1': 'asia'
        }
        
        account_region = account_regions.get(region, 'europe')
        
        for attempt in range(max_retries):
            session = None
            try:
                session = await self.create_session()
                
                # First get PUUID from account API
                account_url = f"https://{account_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                headers = {"X-Riot-Token": self.api_key}
                
                print(f"🔍 Attempt {attempt + 1}: Fetching {game_name}#{tag_line} from {account_region}")
                
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
                                print(f"✅ Successfully fetched {game_name}#{tag_line}")
                                return summoner_data
                            elif summoner_response.status == 404:
                                return None
                            else:
                                raise Exception(f"Summoner API Error: {summoner_response.status}")
                    elif response.status == 404:
                        return None
                    elif response.status == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        print(f"⚠️ Rate limited, waiting {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        raise Exception(f"Account API Error: {response.status}")
                        
            except Exception as e:
                print(f"❌ Attempt {attempt + 1} failed for {game_name}#{tag_line}: {e}")
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1
                    print(f"⚠️ Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ All attempts failed for {game_name}#{tag_line}")
                    raise e
            finally:
                if session:
                    await session.close()
        
        return None
    
    async def get_tft_rank(self, summoner_id, region, max_retries=3):
        """Get TFT rank for a summoner"""
        for attempt in range(max_retries):
            session = None
            try:
                session = await self.create_session()
                
                url = f"{BASE_URL.format(region=region)}/tft/league/v1/entries/by-summoner/{summoner_id}"
                headers = {"X-Riot-Token": self.api_key}
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        for entry in data:
                            if entry.get('queueType') == 'RANKED_TFT':
                                return entry
                        return None
                    elif response.status == 429:
                        retry_after = int(response.headers.get('Retry-After', 5))
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        raise Exception(f"Rank API Error: {response.status}")
                        
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise e
            finally:
                if session:
                    await session.close()
        
        return None

def get_rank_emoji(tier, rank):
    emojis = {
        'IRON': '⚫', 'BRONZE': '🟤', 'SILVER': '⚪', 'GOLD': '🟡',
        'PLATINUM': '🔵', 'DIAMOND': '💎', 'MASTER': '🔮',
        'GRANDMASTER': '🔴', 'CHALLENGER': '👑'
    }
    return emojis.get(tier, '❓')

def calculate_rank_score(tier, rank, lp):
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
    
    # Test connectivity on startup
    await test_connectivity()

@bot.command(name='test_dns')
async def test_dns_command(ctx):
    """Test DNS resolution"""
    await ctx.send("🔍 Testing DNS resolution...")
    await test_connectivity()
    await ctx.send("✅ DNS test completed. Check console for results.")

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
