import discord
from discord.ext import commands
from discord import ui
import asyncio
import time
import io
import os
from keep_alive import keep_alive # Imports the script we just made

# --- CONFIGURATION ---
# Use Replit Secrets (Tools -> Secrets) to add a key named 'TOKEN'
TOKEN = os.environ.get('TOKEN') 
FORUM_CHANNEL_ID = 1478243184831107075 

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)
games = {} 

class DeleteChannelView(ui.View):
    def __init__(self, winner_id: int):
        super().__init__(timeout=None)
        self.winner_id = winner_id

    @ui.button(label="🗑️ Delete Game Channel", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id == self.winner_id or interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⚠️ Deleting in 10 seconds...", ephemeral=False)
            await asyncio.sleep(10)
            try: await interaction.channel.delete()
            except: pass
        else:
            await interaction.response.send_message("❌ Only the winner can delete this.", ephemeral=True)

class IdentifyView(ui.View):
    def __init__(self, channel_id: int, img_bytes: bytes, filename: str, hunter_id: int):
        super().__init__(timeout=120.0)
        self.channel_id = channel_id
        self.img_bytes = img_bytes
        self.filename = filename
        self.hunter_id = hunter_id
        
        game_state = games.get(self.channel_id)
        options = []
        if game_state:
            for p_id in game_state['players']:
                if p_id == self.hunter_id: continue
                m = game_state['channel'].guild.get_member(p_id)
                name = m.display_name if m else f"Player {p_id}"
                options.append(discord.SelectOption(label=name, value=str(p_id)))

        if not options:
            options.append(discord.SelectOption(label="No targets available", value="none"))

        self.player_dropdown = ui.Select(placeholder="Who is in this photo?", options=options)
        self.player_dropdown.callback = self.confirm_hit
        self.add_item(self.player_dropdown)

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.gray, row=1)
    async def cancel_callback(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="🗑️ Cancelled.", view=None, embed=None)

    async def confirm_hit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if self.player_dropdown.values[0] == "none": return
        
        victim_id = int(self.player_dropdown.values[0])
        game_state = games.get(self.channel_id)

        if not game_state or not game_state['active']:
            return await interaction.followup.send("Game inactive.", ephemeral=True)

        now = time.time()
        if now - game_state['cooldowns'].get(self.hunter_id, 0) < 15:
            return await interaction.followup.send("⏰ Reloading!", ephemeral=True)

        game_state['cooldowns'][self.hunter_id] = now
        game_state['players'][victim_id] -= 1
        
        victim_m = interaction.guild.get_member(victim_id)
        v_name = victim_m.display_name if victim_m else "Unknown"

        game_state['pending_gallery'].append({
            'hunter': interaction.user.display_name,
            'victim': v_name,
            'bytes': self.img_bytes,
            'filename': self.filename
        })

        f = discord.File(io.BytesIO(self.img_bytes), filename=self.filename)
        emb = discord.Embed(title="💥 SNAPPED!", description=f"**{interaction.user.display_name}** caught **{v_name}**!", color=0xFF0000)
        emb.set_image(url=f"attachment://{self.filename}")
        await game_state['channel'].send(embed=emb, file=f)

        if game_state['players'][victim_id] <= 0:
            del game_state['players'][victim_id]
            await game_state['channel'].send(f"💀 **{v_name} ELIMINATED!**")

        if game_state['lives_msg']:
            try: await game_state['lives_msg'].delete()
            except: pass
        
        txt = "\n".join(f"<@{u}>: {'❤️' * l}" for u, l in game_state['players'].items())
        game_state['lives_msg'] = await game_state['channel'].send(embed=discord.Embed(title="📊 Standings", description=txt if txt else "Match Over", color=0x3498DB))

        if len(game_state['players']) <= 1:
            game_state['active'] = False
            winner_id = next(iter(game_state['players'])) if game_state['players'] else self.hunter_id
            bot.loop.create_task(archive_to_forum(game_state, winner_id, f"Gallery: {game_state['channel'].name}"))
            await game_state['channel'].send(embed=discord.Embed(title="🏆 GAME OVER", description=f"<@{winner_id}> won!", color=0xF1C40F), view=DeleteChannelView(winner_id))

        bot.loop.create_task(self.run_cooldown_timer(interaction))

    async def run_cooldown_timer(self, interaction):
        msg = await interaction.followup.send("✅ Confirmed! Reloading: 15s", ephemeral=True)
        for i in range(14, -1, -1):
            await asyncio.sleep(1)
            try: await msg.edit(content=f"✅ Confirmed! Reloading: {i}s")
            except: break

async def archive_to_forum(game_state, winner_id, title):
    forum = bot.get_channel(FORUM_CHANNEL_ID)
    if not forum: return
    summary = discord.Embed(title="📸 Match Results", description=f"Winner: <@{winner_id}>", color=0xF1C40F)
    try:
        thread_bundle = await forum.create_thread(name=title, embed=summary)
        thread = thread_bundle.thread
        for p in game_state['pending_gallery']:
            f = discord.File(io.BytesIO(p['bytes']), filename=p['filename'])
            e = discord.Embed(description=f"**Hunter:** {p['hunter']}\n**Victim:** {p['victim']}", color=0x2ECC71)
            e.set_image(url=f"attachment://{p['filename']}")
            await thread.send(embed=e, file=f)
            await asyncio.sleep(1) 
    except Exception as e: print(f"Archive Error: {e}")

class GameMenuView(ui.View):
    def __init__(self, channel):
        super().__init__(timeout=None)
        self.channel = channel
        self.selected_players = []
        self.select = ui.UserSelect(placeholder="Select players...", min_values=2, max_values=25)
        self.select.callback = self.user_callback
        self.add_item(self.select)
        self.btn = ui.Button(label="Setup Match", style=discord.ButtonStyle.green, disabled=True)
        self.btn.callback = self.setup_callback
        self.add_item(self.btn)

    async def user_callback(self, interaction):
        self.selected_players = [u.id for u in self.select.values]
        self.btn.disabled = False
        await interaction.response.edit_message(view=self)

    async def setup_callback(self, interaction):
        await interaction.response.send_modal(LivesModal(self.channel, self.selected_players))

class LivesModal(ui.Modal, title="Match Settings"):
    lives = ui.TextInput(label="Lives", default="3")
    grace = ui.TextInput(label="Grace Period (Seconds)", default="10")
    def __init__(self, channel, players):
        super().__init__()
        self.channel, self.players = channel, players
    async def on_submit(self, interaction):
        l = int(self.lives.value) if self.lives.value.isdigit() else 3
        g = int(self.grace.value) if self.grace.value.isdigit() else 10
        state = {'players': {p: l for p in self.players}, 'active': False, 'grace': True, 'cooldowns': {}, 'channel': self.channel, 'pending_gallery': [], 'lives_msg': None}
        games[self.channel.id] = state
        await interaction.response.send_message(f"✅ Game starting!", ephemeral=True)
        m = await self.channel.send(embed=discord.Embed(title="⏳ Grace Period", description=f"Starting in {g}s...", color=0xFFFF00))
        for i in range(g-1, -1, -1):
            await asyncio.sleep(1)
            await m.edit(embed=discord.Embed(title="⏳ Grace Period", description=f"Starting in {i}s...", color=0xFFFF00))
        state['active'], state['grace'] = True, False
        txt = "\n".join(f"<@{p}>: {'❤️' * l}" for p in self.players)
        state['lives_msg'] = await self.channel.send(embed=discord.Embed(title="📸 GAME ON!", description=txt, color=0x3498DB))

@bot.command()
async def newgame(ctx):
    c = await ctx.guild.create_text_channel(f"camera-shy-{len(games)+1}")
    await c.send("🎮 Setup the match:", view=GameMenuView(c))

@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.channel.id in games:
        g = games[message.channel.id]
        if message.attachments:
            if g.get('grace'):
                try: await message.delete()
                except: pass
                return
            if g['active'] and message.author.id in g['players']:
                att = message.attachments[0]
                if 'image' in att.content_type:
                    bits = await att.read()
                    name = att.filename
                    try: await message.delete()
                    except: pass
                    v = ui.View(timeout=60)
                    b = ui.Button(label="Identify Target", style=discord.ButtonStyle.blurple)
                    async def callback(interaction):
                        if interaction.user.id != message.author.id: return
                        p_v = IdentifyView(message.channel.id, bits, name, message.author.id)
                        await interaction.response.send_message(embed=discord.Embed(title="Identify Target").set_image(url=f"attachment://{name}"), file=discord.File(io.BytesIO(bits), filename=name), view=p_v, ephemeral=True)
                        await interaction.message.delete()
                    b.callback = callback
                    v.add_item(b)
                    await message.channel.send(f"🎯 **{message.author.display_name}** is identifying...", view=v)
    await bot.process_commands(message)

@bot.event
async def on_ready(): print(f"Logged in as {bot.user}")

# --- START BOT ---
keep_alive() # Starts the Flask web server
bot.run(TOKEN)