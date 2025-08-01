import os
import json
import discord
from discord.ext import commands
from discord.ui import View, Button

# === CONFIGURATION ===
TOKEN = os.getenv(
    "DISCORD_TOKEN")  # Make sure this is set in your environment or .env
NEWCOMER_ROLE = "Newcomer"
MEMBER_ROLE = "Member"
ONBOARDING_CHANNEL = "onboarding"
VERIFIED_DB = "verified_users.json"

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
        print(f"[INIT] Creating empty {VERIFIED_DB}")
        with open(VERIFIED_DB, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(VERIFIED_DB, "r") as f:
            data = json.load(f)
            print(f"[LOAD] Loaded {len(data)} verified users")
            return data
    except Exception as e:
        print(f"[ERROR] Failed to load verified users: {e}")
        return {}


def save_verified(data):
    try:
        with open(VERIFIED_DB, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[SAVE] Saved {len(data)} verified users")
    except Exception as e:
        print(f"[ERROR] Failed to save verified users: {e}")


verified_users = load_verified()


# === NICKNAME CHECK ===
def is_valid_wow_nickname(nickname: str) -> bool:
    return nickname.isalpha() and len(nickname) > 2


# === VERIFICATION UI ===
class VerificationView(View):

    def __init__(self, member: discord.Member):
        super().__init__(timeout=None)
        self.member = member
        print(f"[DEBUG] Created VerificationView for {member.name}")

    @discord.ui.button(label="✅ I Accept the Rules",
                       style=discord.ButtonStyle.green)
    async def accept_rules(self, interaction: discord.Interaction,
                           button: Button):
        print(f"[CLICK] {interaction.user} clicked Accept Rules")
        if interaction.user != self.member:
            await interaction.response.send_message(
                "These buttons are not for you.", ephemeral=True)
            return
        if str(self.member.id) in verified_users:
            await interaction.response.send_message("You're already verified.",
                                                    ephemeral=True)
            return

        user_flags.setdefault(self.member.id, {
            "rules": False,
            "nickname": False
        })
        user_flags[self.member.id]["rules"] = True
        await interaction.response.send_message("✅ Rules accepted!",
                                                ephemeral=True)
        await check_verification(self.member)

    @discord.ui.button(label="🏷 I Updated My Nickname",
                       style=discord.ButtonStyle.blurple)
    async def confirm_nickname(self, interaction: discord.Interaction,
                               button: Button):
        print(f"[CLICK] {interaction.user} clicked Confirm Nickname")
        if interaction.user != self.member:
            await interaction.response.send_message(
                "These buttons are not for you.", ephemeral=True)
            return
        if str(self.member.id) in verified_users:
            await interaction.response.send_message("You're already verified.",
                                                    ephemeral=True)
            return

        user_flags.setdefault(self.member.id, {
            "rules": False,
            "nickname": False
        })
        user_flags[self.member.id]["nickname"] = True
        await interaction.response.send_message("🏷 Nickname confirmed!",
                                                ephemeral=True)
        await check_verification(self.member)


# === CHECK VERIFICATION ===
async def check_verification(member: discord.Member):
    flags = user_flags.get(member.id, {"rules": False, "nickname": False})
    print(f"[CHECK] Verifying {member.name}: {flags}")
    if not (flags["rules"] and flags["nickname"]):
        return

    if not is_valid_wow_nickname(member.display_name):
        await member.send(
            "⚠️ Your nickname doesn't look like a valid WoW character name. Please update it."
        )
        print(f"[BLOCK] Invalid nickname: {member.display_name}")
        return

    guild = member.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

    if newcomer_role in member.roles:
        await member.remove_roles(newcomer_role)
        print(f"[ROLE] Removed Newcomer from {member.name}")
    if member_role and member_role not in member.roles:
        await member.add_roles(member_role)
        print(f"[ROLE] Added Member to {member.name}")

    verified_users[str(member.id)] = True
    save_verified(verified_users)

    await member.send(
        "🎉 Welcome! You've been verified and now have full access.")
    print(f"[SUCCESS] {member.name} is fully verified!")


# === EVENTS ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")


@bot.event
async def on_member_join(member):
    print(f"[JOIN] New member joined: {member.name}")

    if str(member.id) in verified_users:
        print(
            f"[INFO] {member.name} was previously verified. Re-applying Member role."
        )
        member_role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
        if member_role:
            await member.add_roles(member_role)
            print(f"[AUTO-ROLE] Re-assigned Member role to {member.name}")
        try:
            await member.send("👋 Welcome back! You're already verified.")
        except:
            pass
        return

    guild = member.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    if newcomer_role:
        await member.add_roles(newcomer_role)
        print(f"[ROLE] Assigned Newcomer to {member.name}")
    else:
        print("[WARN] Newcomer role not found!")

    channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
    if channel:
        await channel.send(
            f"Welcome {member.mention}!\nPlease complete the following steps:",
            view=VerificationView(member))
        print(
            f"[MESSAGE] Sent onboarding message to {member.name} in #{channel.name}"
        )
    else:
        print("[WARN] Onboarding channel not found!")


# === ADMIN COMMANDS ===
@bot.command()
@commands.has_permissions(administrator=True)
async def forceverify(ctx, member: discord.Member):
    guild = ctx.guild
    newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
    member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

    if newcomer_role in member.roles:
        await member.remove_roles(newcomer_role)
        print(f"[ADMIN] Removed Newcomer from {member.name}")
    if member_role:
        await member.add_roles(member_role)
        print(f"[ADMIN] Added Member to {member.name}")

    verified_users[str(member.id)] = True
    save_verified(verified_users)

    await member.send("✅ You've been manually verified.")
    await ctx.send(f"{member.mention} has been manually verified.")


@bot.command()
@commands.has_permissions(administrator=True)
async def verified(ctx):
    await ctx.send(f"Verified users: {list(verified_users.keys())}")


# === RUN BOT ===
bot.run(TOKEN)
