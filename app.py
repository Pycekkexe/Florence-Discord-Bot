import discord
from discord.ext import commands
import aiohttp
import asyncio
import sqlite3
from datetime import datetime

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# API Configuration
RIOT_API_KEY = ""
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your actual bot token

# TFT Rank hierarchy
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
    try:
        conn = sqlite3.connect('tft_players.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY,
                summoner_name TEXT,
                tag_line TEXT,
                region TEXT,
                UNIQUE(summoner_name, tag_line, region)
            )
        ''')
        
        # Clear and add default players
        cursor.execute('DELETE FROM players')
        for player in DEFAULT_PLAYERS:
            cursor.execute('''
                INSERT OR IGNORE INTO players (summoner_name, tag_line, region)
                VALUES (?, ?, ?)
            ''', (player['summoner_name'], player['tag'], player['region']))
        
        conn.commit()
        conn.close()
        print(f"✅ Database initialized with {len(DEFAULT_PLAYERS)} players")
    except Exception as e:
        print(f"❌ Database error: {e}")

class TFTRankFetcher:
    def __init__(self, api_key):
        self.api_key = api_key
        
    async def get_summoner_by_riot_id(self, game_name, tag_line, region):
        """Simple API call with basic error handling"""
        try:
            # Map region to account region
            account_regions = {
                'eune1': 'europe', 'euw1': 'europe', 'tr1': 'europe', 'ru': 'europe',
                'na1': 'americas', 'br1': 'americas', 'la1': 'americas', 'la2': 'americas', 'oc1': 'americas',
                'kr': 'asia', 'jp1': 'asia'
            }
            
            account_region = account_regions.get(region, 'europe')
            
            # Simple session with timeout
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Get PUUID
                account_url = f"https://{account_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
                headers = {"X-Riot-Token": self.api_key}
                
                async with session.get(account_url, headers=headers) as response:
                    if response.status == 200:
                        account_data = await response.json()
                        puuid = account_data['puuid']
                        
                        # Get summoner data
                        summoner_url = f"https://{region}.api.riotgames.com/tft/summoner/v1/summoners/by-puuid/{puuid}"
                        async with session.get(summoner_url, headers=headers) as summoner_response:
                            if summoner_response.status == 200:
                                return await summoner_response.json()
                            
                return None
        except Exception as e:
            print(f"❌ API Error for {game_name}#{tag_line}: {e}")
            return None
    
    async def get_tft_rank(self, summoner_id, region):
        """Get TFT rank"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"https://{region}.api.riotgames.com/tft/league/v1/entries/by-summoner/{summoner_id}"
                headers = {"X-Riot-Token": self.api_key}
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        for entry in data:
                            if entry.get('queueType') == 'RANKED_TFT':
                                return entry
                        return None
        except Exception as e:
            print(f"❌ Rank API Error: {e}")
            return None

def get_rank_emoji(tier):
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

# Initialize
init_db()
fetcher = TFTRankFetcher(RIOT_API_KEY)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} connected!')
    print(f'✅ Tracking {len(DEFAULT_PLAYERS)} players')

@bot.command(name='flo')
async def flo_leaderboard(ctx):
    """Simple leaderboard command"""
    try:
        # Get players from database
        conn = sqlite3.connect('tft_players.db')
        cursor = conn.cursor()
        cursor.execute('SELECT summoner_name, tag_line, region FROM players')
        players = cursor.fetchall()
        conn.close()
        
        if not players:
            await ctx.send("❌ No players found!")
            return
        
        # Send loading message
        loading_msg = await ctx.send("🔄 Fetching ranks...")
        
        player_ranks = []
        
        # Process each player (limit to prevent crashes)
        for i, (summoner_name, tag_line, region) in enumerate(players[:5]):  # Limit to 5 players
            try:
                print(f"Processing {summoner_name}#{tag_line}...")
                
                summoner_data = await fetcher.get_summoner_by_riot_id(summoner_name, tag_line, region)
                
                if summoner_data:
                    rank_data = await fetcher.get_tft_rank(summoner_data['id'], region)
                    
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
                            'tier': tier,
                            'rank': rank,
                            'lp': lp,
                            'wins': wins,
                            'losses': losses,
                            'score': score
                        })
                    else:
                        player_ranks.append({
                            'name': summoner_name,
                            'tag': tag_line,
                            'tier': 'UNRANKED',
                            'rank': '',
                            'lp': 0,
                            'wins': 0,
                            'losses': 0,
                            'score': 0
                        })
                else:
                    player_ranks.append({
                        'name': summoner_name,
                        'tag': tag_line,
                        'tier': 'NOT_FOUND',
                        'rank': '',
                        'lp': 0,
                        'wins': 0,
                        'losses': 0,
                        'score': -1
                    })
                
                # Small delay to prevent rate limiting
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"Error processing {summoner_name}: {e}")
                player_ranks.append({
                    'name': summoner_name,
                    'tag': tag_line,
                    'tier': 'ERROR',
                    'rank': '',
                    'lp': 0,
                    'wins': 0,
                    'losses': 0,
                    'score': -2
                })
        
        # Sort by score
        player_ranks.sort(key=lambda x: x['score'], reverse=True)
        
        # Create embed
        embed = discord.Embed(
            title="🏆 FLO TFT LEADERBOARD 🏆",
            color=0xffd700,
            timestamp=datetime.now()
        )
        
        leaderboard_text = ""
        for i, player in enumerate(player_ranks, 1):
            position = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            
            if player['tier'] in ['ERROR', 'NOT_FOUND']:
                rank_text = player['tier']
                winrate_text = "N/A"
            elif player['tier'] == 'UNRANKED':
                rank_text = "Unranked"
                winrate_text = "N/A"
            else:
                emoji = get_rank_emoji(player['tier'])
                if player['tier'] in ['MASTER', 'GRANDMASTER', 'CHALLENGER']:
                    rank_text = f"{emoji} {player['tier'].title()} {player['lp']} LP"
                else:
                    rank_text = f"{emoji} {player['tier'].title()} {player['rank']} {player['lp']} LP"
                
                total_games = player['wins'] + player['losses']
                if total_games > 0:
                    winrate = round((player['wins'] / total_games * 100), 1)
                    winrate_text = f"{player['wins']}W {player['losses']}L ({winrate}%)"
                else:
                    winrate_text = "No games"
            
            leaderboard_text += f"{position} **{player['name']}#{player['tag']}**\n"
            leaderboard_text += f"    {rank_text} | {winrate_text}\n\n"
        
        embed.add_field(name="Rankings", value=leaderboard_text or "No data", inline=False)
        embed.set_footer(text="Lightweight TFT Bot")
        
        await loading_msg.edit(content="", embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Command error: {e}")

@bot.command(name='help')
async def help_command(ctx):
    """Show help"""
    embed = discord.Embed(
        title="🎮 TFT Bot Commands",
        description="Simple TFT rank tracking",
        color=0x9b59b6
    )
    
    embed.add_field(
        name="Commands",
        value="`!flo` - Show TFT leaderboard\n`!help` - Show this help",
        inline=False
    )
    
    players_text = "\n".join([f"• {p['summoner_name']}#{p['tag']}" for p in DEFAULT_PLAYERS])
    embed.add_field(name="Tracked Players", value=players_text, inline=False)
    
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    print(f"Command error: {error}")
    await ctx.send("❌ Something went wrong!")

# Run bot
if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set your bot token!")
    else:
        print("🚀 Starting lightweight TFT bot...")
        try:
            bot.run(BOT_TOKEN)
        except Exception as e:
            print(f"❌ Bot failed to start: {e}")

