from datetime import datetime
import discord
from discord.ext import commands
from discord.ui import View, Button
from dotenv import load_dotenv
import json
import os

load_dotenv()  # This must come before os.getenv
TOKEN = os.getenv("DISCORD_TOKEN")

# === CONFIGURATION ===
TOKEN = os.getenv(
    "DISCORD_TOKEN")  # Make sure this is set in your environment or .env
NEWCOMER_ROLE = "Newcomer"
MEMBER_ROLE = "Guild Member"
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

# === ONBOARDING EMBED ===
async def send_onboarding_embed(member: discord.Member):
    rules_channel = discord.utils.get(member.guild.text_channels, name="rules")

    embed = discord.Embed(
        title="🎯 Welcome to Vindicated!",
        description="Follow these steps to get verified and join the guild chat.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="1️⃣ Update Your Server Nickname",
        value=(
            "Change your **Vindicated** server nickname to your **main WoW character name**.\n"
            "We do this to help everyone recognize each other across the guild.\n"
            "**Right-click your name → Edit Server Profile → Nickname**."
        ),
        inline=False
    )

    embed.add_field(
        name="2️⃣ Accept the Rules",
        value=(
            "Click the green button below to accept the rules.\n"
            f"For more information, read the rules in {rules_channel.mention}."
        ),
        inline=False
    )

    embed.add_field(
        name="3️⃣ Confirm Nickname Change",
        value="Click the blue button to confirm you've updated your nickname.",
        inline=False
    )

    embed.add_field(
        name="💬 Need Help?",
        value=(
            "**GM**: Tsubone / Tebes\n"
            "**Officers**: Khitkat, Drenna, Gnope, Prettytatted, Smergil\n"
            "**Onboarding Feedback**: Contact Bingtoolbar if you're stuck!"
        ),
        inline=False
    )

    embed.set_footer(text="Thanks for joining Vindicated!")

    onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
    if onboarding_channel:
        await onboarding_channel.send(content=f"{member.mention}", embed=embed)


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
        await log_verification_event(self.member.guild, self.member, "Accepted Rules", user_flags[self.member.id])
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
        await log_verification_event(self.member.guild, self.member, "Confirmed Nickname", user_flags[self.member.id])
        await interaction.response.send_message("🏷 Nickname confirmed!",
                                                ephemeral=True)
        await check_verification(self.member)


# === VERIFICATION LOGGER ===
async def log_verification_event(guild: discord.Guild, member: discord.Member, action: str, flags: dict):
    onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
    if onboarding_channel:
        embed = discord.Embed(
            title="📋 Verification Log",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="User", value=member.mention, inline=False)
        embed.add_field(name="Action", value=action, inline=False)
        embed.add_field(name="Rules Accepted", value=str(flags.get("rules", False)), inline=True)
        embed.add_field(name="Nickname Confirmed", value=str(flags.get("nickname", False)), inline=True)
        await onboarding_channel.send(embed=embed)
    else:
        print("[WARN] Onboarding channel not found for logging!")


# === CHECK VERIFICATION ===
async def check_verification(member: discord.Member):
    flags = user_flags.get(member.id, {"rules": False, "nickname": False})
    print(f"[CHECK] Verifying {member.name}: {flags}")
    if not (flags["rules"] and flags["nickname"]):
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

    await member.send("🎉 Welcome! You've been verified and now have full access.")
    print(f"[SUCCESS] {member.name} is fully verified!")

    await log_verification_event(guild, member, "Full Verification Complete", flags)


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
        await send_onboarding_embed(member)
        await channel.send(
            f"{member.mention} Please follow the instructions above:",
            view=VerificationView(member)
        )
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
