import os
import sys
import json
import logging
import discord
from discord.ext import commands
from discord.ui import View, Button
from dotenv import load_dotenv

# === ENVIRONMENT ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# === CONFIGURATION ===
NEWCOMER_ROLE = "Newcomer"
MEMBER_ROLE = "Guild Member"
ONBOARDING_CHANNEL = "onboarding"
VERIFIED_DB = "verified_users.json"

# === LOGGING ===
logging.basicConfig(
    filename="discord_bot.log",
    filemode="a",
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

# === INTENTS ===
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# === BOT SETUP ===
bot = commands.Bot(command_prefix="!", intents=intents)
user_flags = {}

# === PERSISTENT STORAGE ===
def load_verified():
    if not os.path.exists(VERIFIED_DB):
        logging.info(f"[INIT] Creating empty {VERIFIED_DB}")
        with open(VERIFIED_DB, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(VERIFIED_DB, "r") as f:
            data = json.load(f)
            logging.info(f"[LOAD] Loaded {len(data)} verified users")
            return data
    except Exception as e:
        logging.error(f"[ERROR] Failed to load verified users: {e}")
        return {}

def save_verified(data):
    try:
        with open(VERIFIED_DB, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"[SAVE] Saved {len(data)} verified users")
    except Exception as e:
        logging.error(f"[ERROR] Failed to save verified users: {e}")

verified_users = load_verified()

# === VERIFICATION UI ===
class VerificationView(View):

    def __init__(self, member: discord.Member):
        super().__init__(timeout=None)
        self.member = member
        logging.info(f"[DEBUG] Created VerificationView for {member.name}")

    @discord.ui.button(label="✅ I Accept the Rules", style=discord.ButtonStyle.green)
    async def accept_rules(self, interaction: discord.Interaction, button: Button):
        logging.info(f"[CLICK] {interaction.user} clicked Accept Rules")
        if interaction.user != self.member:
            await interaction.response.send_message("These buttons are not for you.", ephemeral=True)
            return
        if str(self.member.id) in verified_users:
            await interaction.response.send_message("You're already verified.", ephemeral=True)
            return

        user_flags.setdefault(self.member.id, {"rules": False, "nickname": False})
        user_flags[self.member.id]["rules"] = True
        await interaction.response.send_message("✅ Rules accepted!", ephemeral=True)
        await check_verification(self.member)

    @discord.ui.button(label="🥿 I Updated My Nickname", style=discord.ButtonStyle.blurple)
    async def confirm_nickname(self, interaction: discord.Interaction, button: Button):
        logging.info(f"[CLICK] {interaction.user} clicked Confirm Nickname")
        if interaction.user != self.member:
            await interaction.response.send_message("These buttons are not for you.", ephemeral=True)
            return
        if str(self.member.id) in verified_users:
            await interaction.response.send_message("You're already verified.", ephemeral=True)
            return

        user_flags.setdefault(self.member.id, {"rules": False, "nickname": False})
        user_flags[self.member.id]["nickname"] = True
        await interaction.response.send_message("🥿 Nickname confirmed!", ephemeral=True)
        await check_verification(self.member)

# === CHECK VERIFICATION ===
async def check_verification(member: discord.Member):
    flags = user_flags.get(member.id, {"rules": False, "nickname": False})
    logging.info(f"[CHECK] Verifying {member.name}: {flags}")
    if not (flags["rules"] and flags["nickname"]):
        return

    # Nickname validation removed as requested

    guild = member.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

    if newcomer_role in member.roles:
        await member.remove_roles(newcomer_role)
        logging.info(f"[ROLE] Removed Newcomer from {member.name}")
    if member_role and member_role not in member.roles:
        await member.add_roles(member_role)
        logging.info(f"[ROLE] Added Member to {member.name}")

    verified_users[str(member.id)] = True
    save_verified(verified_users)

    try:
        await member.send("🎉 Welcome! You've been verified and now have full access.")
    except Exception as e:
        logging.warning(f"[WARN] Could not DM {member.name}: {e}")
    logging.info(f"[SUCCESS] {member.name} is fully verified!")

# === EVENTS ===
@bot.event
async def on_ready():
    logging.info(f"✅ Bot is online as {bot.user}")

@bot.event
async def on_member_join(member):
    logging.info(f"[JOIN] New member joined: {member.name}")

    if str(member.id) in verified_users:
        logging.info(f"[INFO] {member.name} was previously verified. Re-applying Member role.")
        member_role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
        if member_role:
            await member.add_roles(member_role)
            logging.info(f"[AUTO-ROLE] Re-assigned Member role to {member.name}")
        try:
            await member.send("👋 Welcome back! You're already verified.")
        except:
            pass
        return

    guild = member.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    if newcomer_role:
        await member.add_roles(newcomer_role)
        logging.info(f"[ROLE] Assigned Newcomer to {member.name}")
    else:
        logging.warning("[WARN] Newcomer role not found!")

    channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
    if channel:
        await channel.send(
            f"Welcome {member.mention}!\nPlease complete the following steps:",
            view=VerificationView(member))
        logging.info(f"[MESSAGE] Sent onboarding message to {member.name} in #{channel.name}")
    else:
        logging.warning("[WARN] Onboarding channel not found!")

# === ADMIN COMMANDS ===
@bot.command()
@commands.has_permissions(administrator=True)
async def forceverify(ctx, member: discord.Member):
    guild = ctx.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

    if newcomer_role in member.roles:
        await member.remove_roles(newcomer_role)
        logging.info(f"[ADMIN] Removed Newcomer from {member.name}")
    if member_role:
        await member.add_roles(member_role)
        logging.info(f"[ADMIN] Added Member to {member.name}")

    verified_users[str(member.id)] = True
    save_verified(verified_users)

    await member.send("✅ You've been manually verified.")
    await ctx.send(f"{member.mention} has been manually verified.")

@bot.command()
@commands.has_permissions(administrator=True)
async def verified(ctx):
    await ctx.send(f"Verified users: {list(verified_users.keys())}")

@bot.event
async def on_ready():
    logging.info(f"✅ Bot is online as {bot.user}")
    await sync_existing_verified_users()

async def sync_existing_verified_users():
    logging.info("[SYNC] Checking for existing members to auto-verify...")

    for guild in bot.guilds:
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)
        if not member_role:
            logging.warning(f"[SYNC] Member role '{MEMBER_ROLE}' not found in guild '{guild.name}'")
            continue

        for member in guild.members:
            if member_role in member.roles and str(member.id) not in verified_users:
                verified_users[str(member.id)] = True
                logging.info(f"[SYNC] Auto-verified existing member: {member.name}")
                try:
                    await member.send("👋 You've been auto-verified based on your role.")
                except Exception as e:
                    logging.warning(f"[SYNC] Could not DM {member.name}: {e}")

    save_verified(verified_users)
    logging.info("[SYNC] Finished syncing existing members.")


# === RUN BOT ===
bot.run(TOKEN)
