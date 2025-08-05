"""
# Guild Gatekeeper Bot
A role-assignment and verification Discord bot that automates member onboarding, 
manages alt and main character records, assigns class roles, and supports 
administrative oversight with logging and moderation commands.

"""

import os
import io
import csv
import json
import logging
import time
import discord
import matplotlib.pyplot as plt
from discord.ext import commands
from discord.ui import View, Button
from discord import File
from dotenv import load_dotenv
from datetime import datetime

# === LOAD ENVIRONMENT VARIABLES ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# === CONFIGURATION ===
NEWCOMER_ROLE = "Newcomer"
MEMBER_ROLE = "Guild Member"
ONBOARDING_CHANNEL = "onboarding"
VERIFIED_DB = "verified_users.json"
ALTS_DB = "alts.json"
ALT_ROLE_NAME = "Alt"
BOT_OWNER_NAME = "Bingtoolbar"

CLASS_ROLES = [
    "Druid", "Hunter", "Mage", "Paladin", "Priest",
    "Rogue", "Shaman", "Warlock", "Warrior"
]

CLASS_EMOJIS = {
    ":Warrior:": "Warrior",
    ":Mage:": "Mage",
    ":Warlock:": "Warlock",
    ":Paladin:": "Paladin",
    ":Druid:": "Druid",
    ":Priest:": "Priest",
    ":Rogue:": "Rogue",
    ":Shaman:": "Shaman",
    ":Hunter:": "Hunter"
}

# === INTENTS SETUP ===
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# === BOT SETUP ===
bot = commands.Bot(command_prefix="!", intents=intents)
user_flags = {}

# === LOGGING SETUP ===
logging.basicConfig(
    filename='guild_bot.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# === PERSISTENT STORAGE ===
def load_verified():
    """
    Load verified users from JSON file.

    Returns:
        dict: Verified users mapped by user ID.
    """
    if not os.path.exists(VERIFIED_DB):
        try:
            with open(VERIFIED_DB, "w") as f:
                json.dump({}, f)
            logging.info("Initialized empty verified_users.json")
        except Exception as e:
            logging.error(f"Failed to initialize verified_users.json: {e}")
            return {}
        return {}
    try:
        with open(VERIFIED_DB, "r") as f:
            data = json.load(f)
            logging.info(f"Loaded {len(data)} verified users")
            return data
    except Exception as e:
        logging.error(f"Failed to load verified users: {e}")
        return {}

def save_verified(data):
    """
    Save verified user data to JSON.

    Args:
        data (dict): Verified user data
    """
    try:
        with open(VERIFIED_DB, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Saved {len(data)} verified users")
    except Exception as e:
        logging.error(f"Failed to save verified users: {e}")

verified_users = load_verified()
for uid, val in list(verified_users.items()):
    if isinstance(val, bool):
        verified_users[uid] = {"verified": val}


# === ONBOARDING EMBED ===
async def send_onboarding_embed(member: discord.Member):
    """
    Sends an onboarding embed message with verification steps.

    Args:
        member (discord.Member): Member to send the onboarding message to.
    """
    try:
        rules_channel = discord.utils.get(member.guild.text_channels, name="rules")
        onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)

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
                f"For more information, read the rules in #rules."
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

        if onboarding_channel:
            await onboarding_channel.send(content=f"{member.mention}", embed=embed)
        else:
            logging.warning("[WARN] Onboarding channel not found while sending embed")

    except Exception as e:
        logging.error(f"[ERROR] Failed to send onboarding embed for {member.name}: {e}")



# === NICKNAME CHECK ===
def is_valid_wow_nickname(nickname: str) -> bool:
    """
    Checks if the nickname is a valid WoW character name.
    Valid names contain only alphabetic characters and are longer than 2 characters.

    Args:
        nickname (str): The nickname to validate.

    Returns:
        bool: True if valid, False otherwise.
    """
    return nickname.isalpha() and len(nickname) > 2

# === VERIFICATION UI ===
class VerificationView(View):
    """
    A Discord UI View for verifying new users via buttons.
    Includes logic to track acceptance of rules and nickname confirmation.
    """

    def __init__(self, member: discord.Member):
        """
        Initialize the verification UI.

        Args:
            member (discord.Member): Member associated with the view.
        """
        super().__init__(timeout=None)
        self.member = member
        logging.debug(f"[DEBUG] Created VerificationView for {member.name}")

    @discord.ui.button(label="✅ I Accept the Rules", style=discord.ButtonStyle.green)
    async def accept_rules(self, interaction: discord.Interaction, button: Button):
        """
        Handler for accepting rules button. Sets internal flags and checks verification.

        Args:
            interaction (discord.Interaction): Interaction context.
            button (Button): The clicked button.
        """
        try:
            logging.info(f"[CLICK] {interaction.user} clicked Accept Rules")
            if interaction.user != self.member:
                await interaction.response.send_message("These buttons are not for you.", ephemeral=True)
                return
            if str(self.member.id) in verified_users:
                await interaction.response.send_message("You're already verified.", ephemeral=True)
                return

            user_flags.setdefault(self.member.id, {"rules": False, "nickname": False})
            user_flags[self.member.id]["rules"] = True

            await log_verification_event(
                self.member.guild, self.member, "Accepted Rules", user_flags[self.member.id]
            )
            await interaction.response.send_message("✅ Rules accepted!", ephemeral=True)
            await check_verification(self.member)
        except Exception as e:
            logging.error(f"Error during rules acceptance: {e}")

    @discord.ui.button(label="🏷 I Updated My Nickname", style=discord.ButtonStyle.blurple)
    async def confirm_nickname(self, interaction: discord.Interaction, button: Button):
        """
        Handler for confirming nickname update. Sets internal flags and checks verification.

        Args:
            interaction (discord.Interaction): Interaction context.
            button (Button): The clicked button.
        """
        try:
            logging.info(f"[CLICK] {interaction.user} clicked Confirm Nickname")
            if interaction.user != self.member:
                await interaction.response.send_message("These buttons are not for you.", ephemeral=True)
                return
            if str(self.member.id) in verified_users:
                await interaction.response.send_message("You're already verified.", ephemeral=True)
                return

            user_flags.setdefault(self.member.id, {"rules": False, "nickname": False})
            user_flags[self.member.id]["nickname"] = True

            await log_verification_event(
                self.member.guild, self.member, "Confirmed Nickname", user_flags[self.member.id]
            )
            await interaction.response.send_message("🏷 Nickname confirmed!", ephemeral=True)
            await check_verification(self.member)
        except Exception as e:
            logging.error(f"Error during nickname confirmation: {e}")


# === VERIFICATION LOGGER ===
async def log_verification_event(guild: discord.Guild, member: discord.Member, action: str, flags: dict):
    """
    Logs a verification event to the onboarding channel or the bot log.

    Args:
        guild (discord.Guild): The Discord server.
        member (discord.Member): The user involved in the event.
        action (str): The type of verification action performed.
        flags (dict): The state flags for the user (rules and nickname status).
    """
    try:
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
            logging.warning("[WARN] Onboarding channel not found for logging verification event.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to log verification event for {member.name}: {e}")

# === CLASS ROLE SELECTION ===
logging.basicConfig(
    filename='class_role_audit.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Cooldown tracker for reset command
reset_cooldowns = {}
RESET_COOLDOWN_SECONDS = 60

# === CLASS ROLE SELECTION ===
logging.basicConfig(
    filename='class_role_audit.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Cooldown tracker for reset command
reset_cooldowns = {}
RESET_COOLDOWN_SECONDS = 60

# === ALT STORAGE ===
def load_alts():
    """
    Load alt data from disk.

    Returns:
        dict: Dictionary of alt data.
    """
    try:
        if not os.path.exists(ALTS_DB):
            with open(ALTS_DB, 'w') as f:
                json.dump({}, f)
        with open(ALTS_DB, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[ERROR] Failed to load alts: {e}")
        return {}

def save_alts(data):
    """
    Save alt data to disk.

    Args:
        data (dict): Dictionary of alt data to write.
    """
    try:
        with open(ALTS_DB, 'w') as f:
            json.dump(data, f, indent=2)
        logging.info(f"[SAVE] Saved {len(data)} alt records")
    except Exception as e:
        logging.error(f"[ERROR] Failed to save alts: {e}")

alts_data = load_alts()

# === ADMIN OVERRIDE CHECK ===
def is_admin_or_owner(ctx):
    """
    Determine if the command caller is an admin or the designated bot owner.

    Args:
        ctx (commands.Context): The command invocation context.

    Returns:
        bool: True if the user is an admin or the bot owner.
    """
    return (
        ctx.author.guild_permissions.administrator
        or ctx.author.display_name == BOT_OWNER_NAME
        or ctx.author.name == BOT_OWNER_NAME
    )

# === COMMAND: LIST ALTS ===
@bot.command()
async def listalts(ctx, member: discord.Member = None):
    """
    List all alts associated with a Discord member (or the caller if no member is provided).

    Args:
        ctx (commands.Context): The command invocation context.
        member (discord.Member, optional): The Discord member to query.
    """
    try:
        member = member or ctx.author
        user_id = str(member.id)
        record = alts_data.get(user_id)

        if not record:
            await ctx.send(f"❌ No main/alt records found for {member.display_name}.")
            return

        main = record.get("main", "(not set)")
        alts = record.get("alts", {})

        lines = [f"**{member.display_name}'s Main:** `{main}`"]
        if alts:
            lines.append("**Alts:**")
            for alt, alt_class in alts.items():
                lines.append(f"• `{alt}` ({alt_class})")
        else:
            lines.append("*(No alts recorded)*")

        await ctx.send("\n".join(lines))
    except Exception as e:
        logging.error(f"[ERROR] Failed to list alts for {ctx.author.display_name}: {e}")
        await ctx.send("❌ An error occurred while retrieving alt data.")

# === COMMAND: REASSIGN ALT ===
@bot.command()
async def reassignalt(ctx, alt_name: str, member: discord.Member, alt_class: str):
    """
    Reassign an alt character to a different Discord user.

    Args:
        ctx (commands.Context): Command context.
        alt_name (str): Name of the alt character.
        member (discord.Member): Member to assign the alt to.
        alt_class (str): WoW class of the alt character.
    """
    try:
        if not is_admin_or_owner(ctx):
            await ctx.send("❌ You do not have permission to reassign alts.")
            return

        alt_class = alt_class.capitalize()
        if alt_class not in CLASS_ROLES:
            await ctx.send(f"❌ Invalid class `{alt_class}`. Choose from: {', '.join(CLASS_ROLES)}")
            return

        for uid, record in alts_data.items():
            existing = record.get("alts", {})
            if isinstance(existing, dict) and alt_name in existing:
                del record["alts"][alt_name]
                logging.info(f"Removed alt '{alt_name}' from user ID {uid}")

        new_owner_id = str(member.id)
        alts_data[new_owner_id] = alts_data.get(new_owner_id, {})
        alts_data[new_owner_id].setdefault("alts", {})
        alts_data[new_owner_id]["alts"][alt_name] = alt_class
        alts_data[new_owner_id]["main"] = member.display_name

        save_alts(alts_data)
        logging.info(f"Alt '{alt_name}' ({alt_class}) reassigned to '{member.display_name}' by {ctx.author.display_name}")
        await ctx.send(f"🔄 `{alt_name}` ({alt_class}) is now assigned as an alt to `{member.display_name}`.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to reassign alt '{alt_name}': {e}")
        await ctx.send("❌ An error occurred while reassigning the alt.")

# === COMMAND: SET MAIN FOR ===
@bot.command()
async def setmainfor(ctx, member: discord.Member, main_name: str, main_class: str = None):
    """
    Set the main character name and optional class for a given member.

    Args:
        ctx (commands.Context): The command context.
        member (discord.Member): The target Discord member.
        main_name (str): The new main character name.
        main_class (str, optional): WoW class name.
    """
    try:
        if not is_admin_or_owner(ctx):
            await ctx.send("❌ You do not have permission to set another user's main.")
            return

        user_id = str(member.id)
        alts_data[user_id] = alts_data.get(user_id, {})
        old_main = alts_data[user_id].get("main")
        alts_data[user_id]["main"] = main_name

        if main_class:
            main_class = main_class.capitalize()
            if main_class not in CLASS_ROLES:
                await ctx.send(f"❌ Invalid class `{main_class}`. Choose from: {', '.join(CLASS_ROLES)}")
                return
            alts_data[user_id]["class"] = main_class

        if old_main and old_main != main_name:
            alts_data[user_id].setdefault("alts", {})
            if old_main not in alts_data[user_id]["alts"]:
                alts_data[user_id]["alts"][old_main] = "Unknown"

        save_alts(alts_data)
        logging.info(f"Set main '{main_name}' (class={main_class}) for user {member.display_name} by {ctx.author.display_name}")
        await ctx.send(f"🛠 `{member.display_name}`'s main set to `{main_name}`" + (f" with class `{main_class}`." if main_class else "."))
    except Exception as e:
        logging.error(f"[ERROR] Failed to set main for {member.display_name}: {e}")
        await ctx.send("❌ An error occurred while setting the main character.")


# === COMMAND: CLASS STATS ===
@bot.command()
async def classstats(ctx):
    """
    Display the full class composition of the guild including mains and alts,
    and send a graphical representation.

    Args:
        ctx (commands.Context): The command invocation context.
    """
    try:
        class_members = {cls: [] for cls in CLASS_ROLES}
        all_members_combined = {cls: [] for cls in CLASS_ROLES}
        is_alt_flags = {}

        # Step 1: Collect all alts from DB
        for uid, record in alts_data.items():
            main_name = record.get("main")
            main_class = record.get("class")
            if main_name and main_class in CLASS_ROLES:
                class_members[main_class].append(main_name)
                all_members_combined[main_class].append(main_name)
                is_alt_flags[main_name] = False

            for alt_name, alt_class in record.get("alts", {}).items():
                if alt_class in CLASS_ROLES:
                    class_members[alt_class].append(f"{alt_name} (Alt)")
                    all_members_combined[alt_class].append(alt_name)
                    is_alt_flags[alt_name] = True

        # Step 2: Collect live Discord members
        for guild in bot.guilds:
            for member in guild.members:
                class_role = next((role.name for role in member.roles if role.name in CLASS_ROLES), None)
                if class_role:
                    if member.display_name not in is_alt_flags and member.display_name not in class_members[class_role]:
                        class_members[class_role].append(member.display_name)
                        all_members_combined[class_role].append(member.display_name)
                        is_alt_flags[member.display_name] = False

        if not any(class_members.values()):
            await ctx.send("📊 No class roles assigned yet.")
            return

        summary_lines = ["**🏰 Vindicated's Class Composition**"]
        for cls in sorted(class_members):
            members = sorted(class_members[cls], key=lambda x: x.lower())
            if members:
                summary_lines.append(f"\n**{cls}** ({len(members)}):\n" + ", ".join(members))

        for part in summary_lines:
            await ctx.send(part)

        labels = [cls for cls in CLASS_ROLES if len(all_members_combined[cls]) > 0]
        mains_count = [len([m for m in all_members_combined[cls] if not is_alt_flags.get(m, False)]) for cls in labels]
        alts_count = [len([m for m in all_members_combined[cls] if is_alt_flags.get(m, False)]) for cls in labels]

        if mains_count or alts_count:
            x = range(len(labels))
            plt.figure(figsize=(8, 6))
            plt.bar(x, mains_count, label='Mains', color='skyblue')
            plt.bar(x, alts_count, bottom=mains_count, label='Alts', color='orange')
            plt.title("Vindicated Full Class Composition (Mains + Alts)")
            plt.xlabel("Class")
            plt.ylabel("Count")
            plt.xticks(ticks=x, labels=labels, rotation=45)
            plt.legend()
            plt.tight_layout()

            buffer = io.BytesIO()
            plt.savefig(buffer, format='png')
            buffer.seek(0)
            plt.close()

            file = File(fp=buffer, filename="full_class_composition.png")
            await ctx.send(file=file)
    except Exception as e:
        logging.error(f"[ERROR] Failed to generate class stats: {e}")
        await ctx.send("❌ An error occurred while generating class statistics.")


# === COMMAND: ADD ALT ===
@bot.command()
async def addalt(ctx, alt_name: str, alt_class: str):
    """
    Add a new alt character under the calling user's account.

    Args:
        ctx (commands.Context): The command context.
        alt_name (str): Name of the alt character to add.
        alt_class (str): Class of the alt character.
    """
    try:
        user_id = str(ctx.author.id)
        alt_class = alt_class.capitalize()

        if alt_class not in CLASS_ROLES:
            await ctx.send(f"❌ Invalid class `{alt_class}`. Choose from: {', '.join(CLASS_ROLES)}")
            return

        record = alts_data.get(user_id, {})
        record.setdefault("alts", {})
        record.setdefault("main", ctx.author.display_name)

        if alt_name in record["alts"]:
            await ctx.send(f"🧾 Alt `{alt_name}` is already linked to your account.")
            return

        if len(record["alts"]) >= 9:
            await ctx.send("⚠️ You can only have up to 9 alts per main (10 characters total).")
            return

        record["alts"][alt_name] = alt_class
        alts_data[user_id] = record
        save_alts(alts_data)

        logging.info(f"[ADD] {ctx.author.display_name} added alt '{alt_name}' ({alt_class})")
        await ctx.send(f"✅ Added alt `{alt_name}` with class `{alt_class}` to your account.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to add alt for {ctx.author.display_name}: {e}")
        await ctx.send("❌ An error occurred while adding your alt.")


# === COMMAND: REMOVE ALT ===
@bot.command()
async def removealt(ctx, alt_name: str):
    """
    Remove an alt character from the user's account.

    Args:
        ctx (commands.Context): The command context.
        alt_name (str): Name of the alt to remove.
    """
    try:
        user_id = str(ctx.author.id)
        if user_id not in alts_data:
            await ctx.send("❌ You have no alts recorded.")
            return

        alts = alts_data[user_id].get("alts", {})
        if alt_name not in alts:
            await ctx.send(f"❌ `{alt_name}` is not listed as one of your alts.")
            return

        del alts[alt_name]
        alts_data[user_id]["alts"] = alts
        save_alts(alts_data)

        logging.info(f"[REMOVE] {ctx.author.display_name} removed alt '{alt_name}'")
        await ctx.send(f"🗑 Removed alt `{alt_name}` from your account.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to remove alt '{alt_name}' for {ctx.author.display_name}: {e}")
        await ctx.send("❌ An error occurred while removing your alt.")

# === COMMAND: WHO IS MAIN ===
@bot.command()
async def whoismain(ctx, alt_name: str):
    """
    Identify the main character associated with an alt.

    Args:
        ctx (commands.Context): The command context.
        alt_name (str): Alt character name to look up.
    """
    try:
        for main_id, record in alts_data.items():
            if alt_name in record.get("alts", {}):
                main = record.get("main", "Unknown")
                await ctx.send(f"🧾 `{alt_name}` belongs to main: `{main}`")
                return
        await ctx.send(f"❌ `{alt_name}` not found in alt records.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to lookup main for alt '{alt_name}': {e}")
        await ctx.send("❌ An error occurred while checking alt ownership.")

# === COMMAND: IMPORT ALTS ===
@bot.command()
@commands.has_permissions(administrator=True)
async def importalts(ctx):
    """
    Import alts from a local CSV file named 'alts_import.csv'.
    The format should be: MainName,Alt1,Alt2,...

    Args:
        ctx (commands.Context): The command context.
    """
    try:
        with open('alts_import.csv', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                main_name = row[0].strip()
                alts = [alt.strip() for alt in row[1:] if alt.strip()]
                main_member = discord.utils.get(ctx.guild.members, display_name=main_name)
                if main_member:
                    alts_data[str(main_member.id)] = {"main": main_name, "alts": alts}
        save_alts(alts_data)
        await ctx.send("📥 Alts imported successfully from alts_import.csv")
    except Exception as e:
        logging.error(f"[ERROR] Failed to import alts: {e}")
        await ctx.send(f"❌ Error importing alts: {e}")

# === COMMAND: CLASS STATUS ===
@bot.command()
async def classstatus(ctx, member: discord.Member = None):
    """
    Report the current class role of a member or the invoking user.

    Args:
        ctx (commands.Context): The command context.
        member (discord.Member, optional): The member to check (defaults to author).
    """
    try:
        member = member or ctx.author
        assigned_class = next((role.name for role in member.roles if role.name in CLASS_ROLES), None)
        if assigned_class:
            await ctx.send(f"📜 {member.display_name} has class role: **{assigned_class}**")
        else:
            await ctx.send(f"❌ {member.display_name} does not have a class role assigned.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to retrieve class status for {member.display_name}: {e}")
        await ctx.send("❌ An error occurred while checking class status.")

# === COMMAND: EXPORT CLASSES ===
@bot.command()
@commands.has_permissions(administrator=True)
async def exportclasses(ctx):
    """
    Export all class roles to a CSV file named 'class_roles_export.csv'.

    Args:
        ctx (commands.Context): The command context.
    """
    try:
        with open("class_roles_export.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["User ID", "Username", "Class Role"])
            for guild in bot.guilds:
                for member in guild.members:
                    class_role = next((r.name for r in member.roles if r.name in CLASS_ROLES), None)
                    if class_role:
                        writer.writerow([member.id, member.name, class_role])
        await ctx.send("📤 Exported class roles to `class_roles_export.csv`")
    except Exception as e:
        logging.error(f"[ERROR] Failed to export class roles: {e}")
        await ctx.send("❌ An error occurred while exporting class roles.")

# === COMMAND: RESET CLASS ===
@bot.command()
async def resetclass(ctx, member: discord.Member = None):
    """
    Reset the class role prompt for a member (or the caller).

    Args:
        ctx (commands.Context): The command context.
        member (discord.Member, optional): The member to reset. Defaults to the command caller.
    """
    try:
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
    except Exception as e:
        logging.error(f"[ERROR] Failed to reset class for {member.display_name if member else ctx.author.display_name}: {e}")
        await ctx.send("❌ An error occurred while resetting class selection.")


# === CLASS ROLE VIEW ===
class ClassRoleView(View):
    """
    UI view for allowing users to select their class role using a dropdown menu.
    """
    def __init__(self, member):
        super().__init__(timeout=120)
        self.member = member

    @discord.ui.select(
        placeholder="Choose your class",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label=role, value=role) for role in CLASS_ROLES],
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            if interaction.user != self.member:
                await interaction.response.send_message("This menu is not for you.", ephemeral=True)
                return

            selected_class = select.values[0]
            for class_name in CLASS_ROLES:
                existing_role = discord.utils.get(interaction.guild.roles, name=class_name)
                if existing_role and existing_role in self.member.roles:
                    await self.member.remove_roles(existing_role)

            role = discord.utils.get(interaction.guild.roles, name=selected_class)
            if role:
                await self.member.add_roles(role)
                user_record = verified_users.get(str(self.member.id), {})
                user_record["class_assigned"] = True
                verified_users[str(self.member.id)] = user_record
                save_verified(verified_users)
                await interaction.response.send_message(f"✅ {selected_class} role assigned!", ephemeral=True)
            else:
                await interaction.response.send_message(f"Role `{selected_class}` not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to assign role.", ephemeral=True)
        except Exception as e:
            logging.error(f"[ERROR] Failed class selection for {self.member.display_name}: {e}")
            await interaction.response.send_message(f"❌ Error assigning role: {e}", ephemeral=True)

# === PROMPT FOR CLASS ROLE ===
async def prompt_for_class_role(member):
    """
    Prompt a user to select their class role if one is not assigned.

    Args:
        member (discord.Member): Member to prompt.
    """
    try:
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

                for emoji_name in CLASS_EMOJIS:
                    emoji_obj = discord.utils.get(member.guild.emojis, name=emoji_name.strip(":"))
                    if emoji_obj:
                        await class_prompt.add_reaction(emoji_obj)
                    else:
                        logging.warning(f"[WARN] Emoji {emoji_name} not found in guild.")
    except Exception as e:
        logging.error(f"[ERROR] Failed to prompt class role for {member.display_name}: {e}")

# === EVENT: ON RAW REACTION ADD ===
@bot.event
async def on_raw_reaction_add(payload):
    """
    Assign a class role based on reaction emoji.

    Args:
        payload (discord.RawReactionActionEvent): The reaction payload.
    """
    try:
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
                for existing_class in CLASS_ROLES:
                    existing_role = discord.utils.get(guild.roles, name=existing_class)
                    if existing_role and existing_role in member.roles:
                        await member.remove_roles(existing_role)

                if role not in member.roles:
                    await member.add_roles(role)
                    user_record = verified_users.get(str(member.id), {})
                    user_record["class_assigned"] = True
                    verified_users[str(member.id)] = user_record
                    save_verified(verified_users)

                onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
                if onboarding_channel:
                    await onboarding_channel.send(f"✅ {member.mention} assigned class role: {class_name}")
    except Exception as e:
        logging.error(f"[ERROR] on_raw_reaction_add failed: {e}")

# === EVENT: ON READY ===
@bot.event
async def on_ready():
    """
    Called when the bot is ready. Prompts any unverified users for class selection.
    """
    print(f"✅ Bot is online as {bot.user}")
    for guild in bot.guilds:
        for uid in verified_users:
            member = guild.get_member(int(uid))
            if member:
                has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
                if not has_class:
                    await prompt_for_class_role(member)

# === EVENT: ON MEMBER JOIN ===
@bot.event
async def on_member_join(member):
    """
    Handles new member join events.

    Args:
        member (discord.Member): The new member who joined.
    """
    try:
        print(f"[JOIN] New member joined: {member.name}")

        record = verified_users.get(str(member.id), {})
        if record.get("verified"):
            print(f"[INFO] {member.name} was previously verified. Re-applying Member role.")
            member_role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
            if member_role:
                await member.add_roles(member_role)
                print(f"[AUTO-ROLE] Re-assigned Member role to {member.name}")
            try:
                await member.send("👋 Welcome back! You're already verified.")
            except Exception:
                pass
            return

        newcomer_role = discord.utils.get(member.guild.roles, name=NEWCOMER_ROLE)
        if newcomer_role:
            await member.add_roles(newcomer_role)
            print(f"[ROLE] Assigned Newcomer to {member.name}")
        else:
            print("[WARN] Newcomer role not found!")

        channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        if channel:
            await send_onboarding_embed(member)
            await channel.send(
                f"{member.mention} Please follow the instructions above:",
                view=VerificationView(member)
            )
            print(f"[MESSAGE] Sent onboarding message to {member.name} in #{channel.name}")
        else:
            print("[WARN] Onboarding channel not found!")
    except Exception as e:
        logging.error(f"[ERROR] on_member_join failed for {member.name}: {e}")



# === EVENT: ON MEMBER REMOVE ===
@bot.event
async def on_member_remove(member):
    """
    Cleans up verification data when a member leaves the server.

    Args:
        member (discord.Member): The member who left.
    """
    try:
        user_id = str(member.id)
        if user_id in verified_users:
            logging.info(f"[CLEANUP] {member.name} left the server. Removing verification.")
            del verified_users[user_id]
            save_verified(verified_users)

        if user_id in alts_data:
            logging.info(f"[CLEANUP] {member.name} left the server. Removing alts.")
            del alts_data[user_id]
            save_alts(alts_data)
    except Exception as e:
        logging.error(f"[ERROR] Failed to clean up member {member.name}: {e}")

# === COMMAND: FORCE VERIFY ===
@bot.command()
@commands.has_permissions(administrator=True)
async def forceverify(ctx, member: discord.Member):
    """
    Forcefully verifies a user by giving them the Member role.

    Args:
        ctx (commands.Context): The command context.
        member (discord.Member): The member to verify.
    """
    try:
        guild = ctx.guild
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

        if newcomer_role in member.roles:
            await member.remove_roles(newcomer_role)
        if member_role:
            await member.add_roles(member_role)

        user_record = verified_users.get(str(member.id), {})
        user_record["verified"] = True
        verified_users[str(member.id)] = user_record
        save_verified(verified_users)

        await member.send("✅ You've been manually verified.")
        await ctx.send(f"{member.mention} has been manually verified.")
        logging.info(f"[ADMIN] {ctx.author.display_name} force verified {member.display_name}")
    except Exception as e:
        logging.error(f"[ERROR] forceverify failed: {e}")
        await ctx.send("❌ Failed to force verify the user.")

# === COMMAND: VERIFIED USERS ===
@bot.command()
@commands.has_permissions(administrator=True)
async def verified(ctx):
    """
    List all currently verified users by ID.

    Args:
        ctx (commands.Context): The command context.
    """
    try:
        await ctx.send(f"Verified users: {list(verified_users.keys())}")
    except Exception as e:
        logging.error(f"[ERROR] verified command failed: {e}")
        await ctx.send("❌ Failed to list verified users.")

# === RUN BOT ===
bot.run(TOKEN)
