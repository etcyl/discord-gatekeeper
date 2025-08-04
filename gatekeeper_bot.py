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
for uid, val in list(verified_users.items()):
    if isinstance(val, bool):
        verified_users[uid] = {"verified": val}


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


# === CLASS ROLE SELECTION ===
import logging
import csv
import time
from collections import Counter
import matplotlib.pyplot as plt
import discord
from discord import File
import io

# Setup audit logger
logging.basicConfig(
    filename='class_role_audit.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Cooldown tracker for reset command
reset_cooldowns = {}
RESET_COOLDOWN_SECONDS = 60

@bot.command()
async def classstats(ctx):
    class_members = {cls: [] for cls in CLASS_ROLES}

    for guild in bot.guilds:
        for member in guild.members:
            class_role = next((role.name for role in member.roles if role.name in CLASS_ROLES), None)
            if class_role:
                class_members[class_role].append(member.display_name)

    if not any(class_members.values()):
        await ctx.send("📊 No class roles assigned yet.")
        return

    # Generate class breakdown with names
    summary_lines = ["**🏰 Vindicated's Class Composition**"]
    for cls in sorted(CLASS_ROLES, key=lambda c: len(class_members[c]), reverse=True):
        members = class_members[cls]
        count = len(members)
        if count == 0:
            continue
        member_list = ", ".join(sorted(members))
        summary_lines.append(f"\n**{cls}** ({count}):\n{member_list}")

    # Send paginated output if too long
    split_output = []
    chunk = ""
    for line in summary_lines:
        if len(chunk + line) > 1900:
            split_output.append(chunk)
            chunk = ""
        chunk += line + "\n"
    split_output.append(chunk)

    for part in split_output:
        await ctx.send(part)

    # Optional chart generation (unchanged)
    labels = [cls for cls in CLASS_ROLES if len(class_members[cls]) > 0]
    sizes = [len(class_members[cls]) for cls in labels]

    if sizes:
        plt.figure(figsize=(8, 6))
        plt.bar(labels, sizes, color='skyblue')
        plt.title("Vindicated Class Composition")
        plt.xlabel("Class")
        plt.ylabel("Count")
        plt.xticks(rotation=45)
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png')
        buffer.seek(0)
        plt.close()

        file = File(fp=buffer, filename="class_composition.png")
        await ctx.send(file=file)


@bot.command()
async def classstatus(ctx, member: discord.Member = None):
    member = member or ctx.author
    assigned_class = next((role.name for role in member.roles if role.name in CLASS_ROLES), None)
    if assigned_class:
        await ctx.send(f"📜 {member.display_name} has class role: **{assigned_class}**")
    else:
        await ctx.send(f"❌ {member.display_name} does not have a class role assigned.")

@bot.command()
@commands.has_permissions(administrator=True)
async def exportclasses(ctx):
    with open("class_roles_export.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["User ID", "Username", "Class Role"])
        for guild in bot.guilds:
            for member in guild.members:
                class_role = next((r.name for r in member.roles if r.name in CLASS_ROLES), None)
                if class_role:
                    writer.writerow([member.id, member.name, class_role])
    await ctx.send("📤 Exported class roles to `class_roles_export.csv`")

@bot.command()
async def resetclass(ctx, member: discord.Member = None):
    now = time.time()
    caller_id = ctx.author.id
    if caller_id in reset_cooldowns and now - reset_cooldowns[caller_id] < RESET_COOLDOWN_SECONDS:
        remaining = int(RESET_COOLDOWN_SECONDS - (now - reset_cooldowns[caller_id]))
        await ctx.send(f"⏱ Please wait {remaining} seconds before using this command again.")
        return
    reset_cooldowns[caller_id] = now

    if member is None:
        member = ctx.author
    elif not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You don't have permission to reset others.")
        return

    user_record = verified_users.get(str(member.id), {})
    for role_name in CLASS_ROLES:
        role = discord.utils.get(member.guild.roles, name=role_name)
        if role and role in member.roles:
            try:
                await member.remove_roles(role)
            except Exception as e:
                logging.error(f"Failed to remove role {role_name} from {member.name}: {e}")

    user_record["class_assigned"] = False
    verified_users[str(member.id)] = user_record
    save_verified(verified_users)

    onboarding_channel = discord.utils.get(ctx.guild.text_channels, name=ONBOARDING_CHANNEL)
    if onboarding_channel:
        await onboarding_channel.send(f"🔁 {member.mention}'s class role prompt has been reset by {ctx.author.mention}.")
    await prompt_for_class_role(member)
    await ctx.send(f"✅ {member.display_name} has been prompted again for class role selection.")
    log_msg = f"Class role prompt reset for {member.name} by {ctx.author.name}"
    logging.info(log_msg)
    print(f"[ADMIN] {log_msg}")

CLASS_ROLES = ["Warlock", "Warrior", "Paladin", "Druid", "Priest", "Rogue", "Hunter", "Mage", "Shaman"]
CLASS_EMOJIS = {
    "⚔️": "Warrior",
    "🔮": "Mage",
    "🌑": "Warlock",
    "🌟": "Paladin",
    "🌿": "Druid",
    "✨": "Priest",
    "🗡️": "Rogue",
    "🌩️": "Shaman",
    "🌹": "Hunter"
}

class ClassRoleView(View):
    def __init__(self, member):
        super().__init__(timeout=120)
        self.member = member

    @discord.ui.select(
        placeholder="Choose your class",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=role, value=role)
            for role in CLASS_ROLES
        ],
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user != self.member:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return
        selected_class = select.values[0]
        for class_name in CLASS_ROLES:
            existing_role = discord.utils.get(interaction.guild.roles, name=class_name)
            if existing_role and existing_role in self.member.roles:
                try:
                    await self.member.remove_roles(existing_role)
                except Exception as e:
                    logging.error(f"Failed to remove role {class_name} from {self.member.name}: {e}")
        role = discord.utils.get(interaction.guild.roles, name=selected_class)
        if role:
            try:
                await self.member.add_roles(role)
                user_record = verified_users.get(str(self.member.id), {})
                user_record["class_assigned"] = True
                verified_users[str(self.member.id)] = user_record
                save_verified(verified_users)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Missing permissions to assign role.", ephemeral=True)
                return
            except Exception as e:
                await interaction.response.send_message(f"❌ Error assigning role: {e}", ephemeral=True)
                return
            await interaction.response.send_message(f"✅ {selected_class} role assigned!", ephemeral=True)
        else:
            await interaction.response.send_message(f"Role `{selected_class}` not found.", ephemeral=True)

async def prompt_for_class_role(member):
    user_record = verified_users.get(str(member.id), {})
    if user_record.get("class_assigned"):
        return
    onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
    if onboarding_channel:
        has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
        if not has_class:
            class_prompt = await onboarding_channel.send(
                f"{member.mention}, please select your class (react or use dropdown):",
                view=ClassRoleView(member)
            )
            for emoji in CLASS_EMOJIS:
                await class_prompt.add_reaction(emoji)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member is None or payload.member.bot:
        return

    guild = discord.utils.get(bot.guilds, id=payload.guild_id)
    if not guild:
        return

    emoji = str(payload.emoji)
    class_name = CLASS_EMOJIS.get(emoji)
    if class_name:
        member = guild.get_member(payload.user_id)
        if not member:
            return
        role = discord.utils.get(guild.roles, name=class_name)
        if role:
            for class_name_existing in CLASS_ROLES:
                existing_role = discord.utils.get(guild.roles, name=class_name_existing)
                if existing_role and existing_role in member.roles:
                    try:
                        await member.remove_roles(existing_role)
                    except Exception as e:
                        logging.error(f"Failed to remove role {class_name_existing} from {member.name}: {e}")
            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    user_record = verified_users.get(str(member.id), {})
                    user_record["class_assigned"] = True
                    verified_users[str(member.id)] = user_record
                    save_verified(verified_users)
                except discord.Forbidden:
                    print(f"[ERROR] Missing permissions to assign role {role.name} to {member.display_name}")
                except Exception as e:
                    print(f"[ERROR] Failed to assign role {role.name} to {member.display_name}: {e}")
            onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
            if onboarding_channel:
                await onboarding_channel.send(f"✅ {member.mention} assigned class role: {class_name}")

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    for guild in bot.guilds:
        for uid in verified_users:
            member = guild.get_member(int(uid))
            if member:
                has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
                if not has_class:
                    await prompt_for_class_role(member)

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

    user_record = verified_users.get(str(member.id), {})
    user_record["verified"] = True
    verified_users[str(member.id)] = user_record

    save_verified(verified_users)

    await member.send("🎉 Welcome! You've been verified and now have full access.")
    print(f"[SUCCESS] {member.name} is fully verified!")

    await log_verification_event(guild, member, "Full Verification Complete", flags)
    await prompt_for_class_role(member)

@bot.event
async def on_member_join(member):
    print(f"[JOIN] New member joined: {member.name}")

    record = verified_users.get(str(member.id), {})
    if record.get("verified"):

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

    user_record = verified_users.get(str(member.id), {})
    user_record["verified"] = True
    verified_users[str(member.id)] = user_record

    save_verified(verified_users)

    await member.send("✅ You've been manually verified.")
    await ctx.send(f"{member.mention} has been manually verified.")


@bot.command()
@commands.has_permissions(administrator=True)
async def verified(ctx):
    await ctx.send(f"Verified users: {list(verified_users.keys())}")


# === RUN BOT ===
bot.run(TOKEN)
