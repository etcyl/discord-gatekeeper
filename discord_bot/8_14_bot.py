# guild_gatekeeper_persistent.py
#
# Guild Gatekeeper Bot (Persistent Verification Variant)
# - Stateless, persistent verification UI using fixed custom_id's
# - Robust reaction→class mapping (supports custom emoji names; optional unicode)
# - Adds missing check_verification() gate
# - Reuses/extends your raid mirror, alt tools, stats, exports, etc.
#
# Requires: discord.py 2.x, matplotlib, python-dotenv

import os
import io
import csv
import json
import logging
import time
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import discord
from discord.ext import commands
from discord.ui import View, Button, Select
from discord import File
import matplotlib.pyplot as plt
from dotenv import load_dotenv

# =========================
# ENV / CONFIG
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

# Persistent emoji/class mapping:
#  - Keys support custom emoji **names** (preferred) and optional Unicode glyphs.
#  - Add unicode entries if you want to allow generic emoji, e.g. "🗡️": "Rogue"
CLASS_EMOJIS = {
    "Warrior": "Warrior",
    "Mage": "Mage",
    "Warlock": "Warlock",
    "Paladin": "Paladin",
    "Druid": "Druid",
    "Priest": "Priest",
    "Rogue": "Rogue",
    "Shaman": "Shaman",
    "Hunter": "Hunter",
    # "🗡️": "Rogue",
    # "🏹": "Hunter",
    # "🛡️": "Warrior",
}

# --- Raid Mirror Config ---
SOURCE_CHANNELS: Dict[str, str] = {
    "aq-20-thurs-sign-up": "AQ20-Thursday",
    "aq-20-sunday-sign-up": "AQ20-Sunday",
    "bwl-mc-saturday-sign-up": "BWL-MC-Saturday",
    "aq-40-friday-sign-up": "AQ40-Friday",
}
DESTINATION_CHANNEL: str = "current-raids"
STATE_DB = "raid_mirror_state.json"

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename='guild_bot.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# --- Structured audit logging (file + console) ---
AUDIT_LOG_FILE = "guild_audit.log"

def _ensure_audit_logger():
    logger = logging.getLogger("guild_audit")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler (useful while testing)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    ))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

_audit = _ensure_audit_logger()

def audit(event: str, member: discord.abc.User | None = None, **fields):
    """
    Structured audit log. Use this everywhere important:
      audit("member_join", member, guild=guild.id, newcomer_assigned=True)
    """
    try:
        payload = {
            "event": event,
            **fields
        }
        if member is not None:
            payload.update({
                "user_id": getattr(member, "id", None),
                "user_name": getattr(member, "name", None),
                "display": getattr(member, "display_name", None),
            })
        # compact JSON on one line for easy grep
        _audit.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        logging.error(f"[AUDIT] Failed to write audit log for {event}: {e}")


# =========================
# DISCORD INTENTS / BOT
# =========================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# PERSISTENCE HELPERS
# =========================
def _safe_load_json(path: str, default):
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f)
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[ERROR] load {path}: {e}")
        return default

def _safe_save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"[ERROR] save {path}: {e}")

verified_users: Dict[str, dict] = _safe_load_json(VERIFIED_DB, {})
# Normalize any legacy bool values
for uid, val in list(verified_users.items()):
    if isinstance(val, bool):
        verified_users[uid] = {"verified": val}

alts_data: Dict[str, dict] = _safe_load_json(ALTS_DB, {})

def save_verified(data=None):
    _safe_save_json(VERIFIED_DB, verified_users if data is None else data)

def save_alts(data=None):
    _safe_save_json(ALTS_DB, alts_data if data is None else data)

# Minimal persistent mirror state
def _load_state() -> dict:
    try:
        with open(STATE_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "week_key" in data and "mirrors" in data:
            return data
    except Exception:
        pass
    return {"week_key": None, "mirrors": {}}

def _save_state(state: dict) -> None:
    _safe_save_json(STATE_DB, state)

# =========================
# UTILITIES
# =========================
def is_valid_wow_nickname(nickname: str) -> bool:
    return nickname.isalpha() and len(nickname) > 2

def nickname_meets_policy(nick: str) -> bool:
    return is_valid_wow_nickname(nick)

def _iso_week_key(now: Optional[datetime] = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"

def _normalize(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().lower())

def _find_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    want = _normalize(name)
    for ch in guild.text_channels:
        if _normalize(ch.name) == want:
            return ch
    return None

def _clone_embed(src: discord.Embed) -> discord.Embed:
    dst = discord.Embed(
        title=src.title, description=src.description, color=src.color,
        url=src.url, timestamp=src.timestamp,
    )
    if src.author:
        dst.set_author(
            name=src.author.name or discord.Embed.Empty,
            url=getattr(src.author, "url", None) or discord.Embed.Empty,
            icon_url=getattr(src.author, "icon_url", None) or discord.Embed.Empty,
        )
    if src.footer:
        dst.set_footer(
            text=src.footer.text or discord.Embed.Empty,
            icon_url=getattr(src.footer, "icon_url", None) or discord.Embed.Empty,
        )
    if src.thumbnail and src.thumbnail.url:
        dst.set_thumbnail(url=src.thumbnail.url)
    if src.image and src.image.url:
        dst.set_image(url=src.image.url)
    for f in src.fields:
        dst.add_field(name=f.name, value=f.value, inline=f.inline)
    return dst

# =========================
# RAID MIRROR COG
# =========================
def _raidkey_for_message(message: discord.Message) -> Optional[str]:
    if not isinstance(message.channel, discord.TextChannel):
        return None
    raidkey = SOURCE_CHANNELS.get(_normalize(message.channel.name))
    if not raidkey:
        return None
    if not message.embeds:
        return None
    return raidkey

class CurrentWeekRaidMirror(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()

    async def _ensure_week(self, guild: discord.Guild) -> None:
        wk_now = _iso_week_key()
        if self.state.get("week_key") == wk_now:
            return
        dest = _find_channel_by_name(guild, DESTINATION_CHANNEL)
        if isinstance(dest, discord.TextChannel):
            for info in list(self.state.get("mirrors", {}).values()):
                dest_id = info.get("dest_msg_id")
                if not dest_id:
                    continue
                try:
                    msg = await dest.fetch_message(dest_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    pass
        self.state = {"week_key": wk_now, "mirrors": {}}
        _save_state(self.state)

    async def refresh_all_mirrors(self, guild: discord.Guild, per_channel_scan: int = 50) -> None:
        try:
            await self._ensure_week(guild)
            for src_name, raidkey in SOURCE_CHANNELS.items():
                src = _find_channel_by_name(guild, src_name)
                if not isinstance(src, discord.TextChannel):
                    continue
                found = None
                async for m in src.history(limit=per_channel_scan, oldest_first=False):
                    if m.embeds and _raidkey_for_message(m) == raidkey:
                        found = m
                        break
                if found:
                    await self._post_or_replace(guild, raidkey, found)
        except Exception:
            pass

    async def _post_or_replace(self, guild: discord.Guild, raidkey: str, source_msg: discord.Message) -> None:
        if not source_msg.embeds:
            return
        dest = _find_channel_by_name(guild, DESTINATION_CHANNEL)
        if not isinstance(dest, discord.TextChannel):
            return
        emb = _clone_embed(source_msg.embeds[0])
        note = f"Mirrored from {source_msg.channel.mention}. Sign up on the original: {source_msg.jump_url}"
        emb.description = f"{note}\n\n{emb.description or ''}".strip()
        existing = self.state["mirrors"].get(raidkey)
        if existing and existing.get("dest_msg_id"):
            try:
                old = await dest.fetch_message(existing["dest_msg_id"])
                await old.delete()
            except discord.NotFound:
                pass
            except Exception:
                pass
        sent = await dest.send(embed=emb)
        self.state["mirrors"][raidkey] = {"source_msg_id": source_msg.id, "dest_msg_id": sent.id}
        _save_state(self.state)

    async def _update_mirror(self, guild: discord.Guild, raidkey: str, source_msg: discord.Message) -> None:
        stored = self.state["mirrors"].get(raidkey)
        if not stored or not stored.get("dest_msg_id") or not source_msg.embeds:
            return
        dest = _find_channel_by_name(guild, DESTINATION_CHANNEL)
        if not isinstance(dest, discord.TextChannel):
            return
        try:
            mirror_msg = await dest.fetch_message(stored["dest_msg_id"])
        except discord.NotFound:
            self.state["mirrors"].pop(raidkey, None)
            _save_state(self.state)
            return
        except Exception:
            return

        emb = _clone_embed(source_msg.embeds[0])
        note = f"Mirrored from {source_msg.channel.mention}. Sign up on the original: {source_msg.jump_url}"
        emb.description = f"{note}\n\n{emb.description or ''}".strip()
        try:
            await mirror_msg.edit(embed=emb)
        except Exception:
            pass

    @commands.Cog.listener("on_message")
    async def on_message_create(self, message: discord.Message) -> None:
        try:
            if not message.guild:
                return
            raidkey = _raidkey_for_message(message)
            if not raidkey:
                return
            await self._ensure_week(message.guild)
            await self._post_or_replace(message.guild, raidkey, message)
        except Exception:
            pass

    @commands.Cog.listener("on_message_edit")
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        try:
            if not after.guild:
                return
            raidkey = _raidkey_for_message(after)
            if not raidkey:
                return
            await self._ensure_week(after.guild)
            await self._update_mirror(after.guild, raidkey, after)
        except Exception:
            pass

async def register_raid_mirror(bot: commands.Bot) -> None:
    await bot.add_cog(CurrentWeekRaidMirror(bot))

# =========================
# ONBOARDING / VERIFICATION
# =========================
async def send_onboarding_embed(member: discord.Member):
    try:
        onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        embed = discord.Embed(
            title="Welcome to Vindicated!",
            description="Follow these steps to get verified and join the guild chat.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="1) Update Your Server Nickname",
            value=(
                "Set your **server nickname** to your **main WoW character name**.\n"
                "**Right-click your name → Edit Server Profile → Nickname**."
            ),
            inline=False
        )
        embed.add_field(
            name="2) Accept the Rules",
            value="Click the green button below to accept the rules. Read #rules first.",
            inline=False
        )
        embed.add_field(
            name="3) Confirm Nickname Change",
            value="Click the blue button to confirm you've updated your nickname.",
            inline=False
        )
        embed.add_field(
            name="4) Choose Your Class",
            value="Use the dropdown under this message or react with your class emoji.",
            inline=False
        )
        if onboarding_channel:
            await onboarding_channel.send(content=f"{member.mention}", embed=embed)
    except Exception as e:
        logging.error(f"[ERROR] send_onboarding_embed: {e}")

# ---------- Persistent Verification View (stateless) ----------
# Buttons use fixed custom_id values; they do not capture 'member'.
# We derive the acting user from interaction.user, and store flags in verified_users[uid].

class VerificationView(View):
    """
    Persistent, stateless verification UI.
    Uses fixed custom_id values so buttons keep working across restarts.
    Persists state in verified_users; no user_flags dependency.
    """
    def __init__(self):
        super().__init__(timeout=None)  # persistent view

    # --- helpers (local) ---
    @staticmethod
    def _nickname_ok(display_name: str) -> bool:
        # Prefer nickname_meets_policy if defined; else fallback to is_valid_wow_nickname; else allow.
        try:
            return nickname_meets_policy(display_name)  # type: ignore[name-defined]
        except Exception:
            try:
                return is_valid_wow_nickname(display_name)  # type: ignore[name-defined]
            except Exception:
                return True

    @staticmethod
    def _audit(event: str, member: discord.abc.User, **fields):
        # Safe audit: only call if an audit() helper exists
        fn = globals().get("audit", None)
        if callable(fn):
            try:
                fn(event, member, **fields)
            except Exception:
                pass  # never break UX due to logging

    # ---------- BUTTON: Accept Rules ----------
    @discord.ui.button(
        label="✅ I Accept the Rules",
        style=discord.ButtonStyle.green,
        custom_id="verify:accept_rules"
    )
    async def accept_rules(self, interaction: discord.Interaction, button: Button):
        try:
            user = interaction.user
            uid = str(user.id)

            self._audit("rules_button_click", user)

            rec = verified_users.get(uid, {})
            if rec.get("verified"):
                await interaction.response.send_message("You're already verified ✅", ephemeral=True)
                self._audit("rules_click_already_verified", user)
                return

            rec["rules_accepted"] = True
            verified_users[uid] = rec
            # save_verified() signature varies across versions; call with no args per your snippet
            save_verified()  # if your helper requires an arg, use: save_verified(verified_users)

            await interaction.response.send_message("✅ Rules accepted!", ephemeral=True)
            self._audit("rules_accepted", user)

            # Optional channel log (keeps your existing behavior)
            await log_verification_event(interaction.guild, user, "Accepted Rules", rec)

            # Run the final gate to promote to Guild Member if ready
            await check_verification(user)
        except Exception as e:
            logging.error(f"[ERROR] accept_rules: {e}")
            self._audit("rules_accept_error", interaction.user, error=str(e))

    # ---------- BUTTON: Confirm Nickname ----------
    @discord.ui.button(
        label="🏷 I Updated My Nickname",
        style=discord.ButtonStyle.blurple,
        custom_id="verify:confirm_nickname"
    )
    async def confirm_nickname(self, interaction: discord.Interaction, button: Button):
        try:
            user = interaction.user
            uid = str(user.id)
            display = user.display_name

            self._audit("nickname_button_click", user, display=display)

            rec = verified_users.get(uid, {})
            if rec.get("verified"):
                await interaction.response.send_message("You're already verified ✅", ephemeral=True)
                self._audit("nickname_click_already_verified", user, display=display)
                return

            #if not self._nickname_ok(display):
                #await interaction.response.send_message(
                    #"Your nickname doesn't match the required format. "
                    #"Please set it to your **main WoW character name** and try again.",
                    #ephemeral=True
                #)
                #self._audit("nickname_invalid", user, display=display)
                #return

            rec["nickname_confirmed"] = True
            verified_users[uid] = rec
            save_verified()  # if your helper requires an arg, use: save_verified(verified_users)

            await interaction.response.send_message("🏷 Nickname confirmed!", ephemeral=True)
            self._audit("nickname_confirmed", user, display=display)

            await log_verification_event(interaction.guild, user, "Confirmed Nickname", rec)

            # Run the final gate to promote to Guild Member if ready
            await check_verification(user)
        except Exception as e:
            logging.error(f"[ERROR] confirm_nickname: {e}")
            self._audit("nickname_confirm_error", interaction.user, error=str(e))


# ---------- Persistent Class Select (stateless) ----------
class ClassRoleSelect(Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose your class",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=role, value=role) for role in CLASS_ROLES],
            custom_id="verify:class_select"  # persistent ID
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            user = interaction.user
            guild = interaction.guild
            selected_class = self.values[0]

            # Remove any existing class roles
            for class_name in CLASS_ROLES:
                r = discord.utils.get(guild.roles, name=class_name)
                if r and r in user.roles:
                    await user.remove_roles(r)

            role = discord.utils.get(guild.roles, name=selected_class)
            if role:
                await user.add_roles(role)
                # Persist 'class_assigned'
                uid = str(user.id)
                rec = verified_users.get(uid, {})
                rec["class_assigned"] = True
                verified_users[uid] = rec
                save_verified()

                await interaction.response.send_message(f"✅ {selected_class} role assigned!", ephemeral=True)
                # Log + advance verification
                onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
                if onboarding_channel:
                    await onboarding_channel.send(f"✅ {user.mention} assigned class role: **{selected_class}**")
                await check_verification(user)
            else:
                await interaction.response.send_message(f"Role `{selected_class}` not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to assign role.", ephemeral=True)
        except Exception as e:
            logging.error(f"[ERROR] class select: {e}")
            await interaction.response.send_message(f"❌ Error assigning role: {e}", ephemeral=True)

class ClassRoleView(View):
    def __init__(self, member: discord.Member | None = None):
        # timeout=None keeps it persistent
        super().__init__(timeout=None)
        self.member = member  # will be set later if not provided

    @discord.ui.select(
        placeholder="Select your class",
        options=[
            discord.SelectOption(label="Druid"),
            discord.SelectOption(label="Hunter"),
            discord.SelectOption(label="Mage"),
            discord.SelectOption(label="Paladin"),
            discord.SelectOption(label="Priest"),
            discord.SelectOption(label="Rogue"),
            discord.SelectOption(label="Shaman"),
            discord.SelectOption(label="Warlock"),
            discord.SelectOption(label="Warrior")
        ],
        custom_id="classrole:select_class"
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            # If persistent view loaded without member, set it now
            if self.member is None:
                self.member = interaction.user

            selected_class = select.values[0]
            guild = interaction.guild
            role = discord.utils.get(guild.roles, name=selected_class)
            if role:
                await self.member.add_roles(role)
                user_record = verified_users.get(str(self.member.id), {})
                user_record["class_assigned"] = True
                verified_users[str(self.member.id)] = user_record
                save_verified(verified_users)
                await interaction.response.send_message(f"✅ {selected_class} role assigned!", ephemeral=True)
                logging.info(f"[INFO] {self.member} assigned class role {selected_class}")
                await check_verification(self.member)
            else:
                await interaction.response.send_message(f"Role `{selected_class}` not found.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to assign that role.", ephemeral=True)
        except Exception as e:
            logging.error(f"[ERROR] select_callback: {e}")



# ---------- Verification logging ----------
async def log_verification_event(guild: discord.Guild, member: discord.Member, action: str, flags: dict):
    try:
        onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel:
            embed = discord.Embed(
                title="Verification Log",
                color=discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="User", value=member.mention, inline=False)
            embed.add_field(name="Action", value=action, inline=False)
            embed.add_field(name="Rules Accepted", value=str(flags.get("rules_accepted", False)), inline=True)
            embed.add_field(name="Nickname Confirmed", value=str(flags.get("nickname_confirmed", False)), inline=True)
            embed.add_field(name="Class Assigned", value=str(flags.get("class_assigned", False)), inline=True)
            await onboarding_channel.send(embed=embed)
    except Exception as e:
        logging.error(f"[ERROR] log_verification_event: {e}")

# ---------- Final gate ----------
async def check_verification(member: discord.Member) -> None:
    """
    Promote a user to 'Guild Member' when all onboarding steps are clicked:
      - rules_accepted
      - nickname_confirmed   (no format validation here)
      - class_assigned OR an existing class role
    Removes 'Newcomer', adds 'Guild Member', sets verified=True, logs, and persists.
    """
    try:
        uid = str(member.id)
        rec = verified_users.get(uid, {}) or {}

        # Only honor the user's button/select clicks; do NOT validate nickname format here.
        rules_ok = bool(rec.get("rules_accepted"))
        nick_ok  = bool(rec.get("nickname_confirmed"))
        class_ok = bool(rec.get("class_assigned")) or any(r.name in CLASS_ROLES for r in member.roles)

        # Optional structured audit if you added `audit()`
        try:
            audit("onboard_gate_check",
                  member,
                  rules_ok=rules_ok, nick_ok=nick_ok, class_ok=class_ok,
                  currently_verified=bool(rec.get("verified")),
                  roles=[r.name for r in member.roles])
        except Exception:
            pass

        if not (rules_ok and nick_ok and class_ok):
            return  # not ready yet

        guild = member.guild
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
        member_role   = discord.utils.get(guild.roles, name=MEMBER_ROLE)

        removed_newcomer = False
        added_member     = False

        # Ensure Guild Member role present
        if member_role and member_role not in member.roles:
            try:
                await member.add_roles(member_role, reason="Completed onboarding")
                added_member = True
            except Exception as e:
                logging.error(f"[ERROR] add Guild Member to {member}: {e}")

        # Ensure Newcomer role removed
        if newcomer_role and newcomer_role in member.roles:
            try:
                await member.remove_roles(newcomer_role, reason="Completed onboarding")
                removed_newcomer = True
            except Exception as e:
                logging.error(f"[ERROR] remove Newcomer from {member}: {e}")

        # Persist verification flag
        if not rec.get("verified"):
            rec["verified"] = True
            verified_users[uid] = rec
            save_verified()  # your helper persists the global dict

        # Channel notice (optional)
        onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel and (added_member or removed_newcomer):
            try:
                await onboarding_channel.send(
                    f"🎉 {member.mention} has completed onboarding and is now a **{MEMBER_ROLE}**!"
                )
            except Exception:
                pass

        # Audit log
        try:
            audit("onboard_promoted",
                  member,
                  added_member=added_member,
                  removed_newcomer=removed_newcomer,
                  verified=True,
                  roles=[r.name for r in member.roles])
        except Exception:
            pass

    except Exception as e:
        logging.error(f"[ERROR] check_verification failed for {member}: {e}")
        try:
            audit("onboard_gate_error", member, error=str(e))
        except Exception:
            pass


# ---------- Prompt helpers ----------
async def prompt_for_class_role(member: discord.Member):
    try:
        uid = str(member.id)
        rec = verified_users.get(uid, {})
        if rec.get("class_assigned"):
            return
        onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel:
            has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
            if not has_class:
                msg = await onboarding_channel.send(
                    f"{member.mention}, please select your class (dropdown or reactions):",
                    view=ClassRoleView()
                )
                # Add custom emoji reactions by name if present in the guild
                for key, mapped_class in CLASS_EMOJIS.items():
                    # Only consider keys that look like emoji names (alphabetic) — skip unicode here
                    if key.isalpha():
                        emoji_obj = discord.utils.get(member.guild.emojis, name=key)
                        if emoji_obj:
                            try:
                                await msg.add_reaction(emoji_obj)
                            except Exception as e:
                                logging.warning(f"[WARN] Could not add reaction for {key}: {e}")
    except Exception as e:
        logging.error(f"[ERROR] prompt_for_class_role: {e}")

# =========================
# REACTION HANDLER (robust)
# =========================
@bot.event
async def on_raw_reaction_add(payload):
    """
    Assign a class role based on reaction emoji (supports custom emoji names and unicode).
    Promotes via check_verification() once class is set.
    """
    try:
        # Ignore bots / DMs
        if payload.member is None or payload.member.bot or payload.guild_id is None:
            return

        guild = discord.utils.get(bot.guilds, id=payload.guild_id)
        if not guild:
            return

        # Resolve emoji in a robust way
        emoji_name = getattr(payload.emoji, "name", None)  # "Warrior" for <:Warrior:ID>
        emoji_str  = str(payload.emoji)                    # "<:Warrior:ID>" or "🗡️"
        class_name = CLASS_EMOJIS.get(emoji_name) or CLASS_EMOJIS.get(emoji_str)
        if not class_name:
            # Not one of our class emojis—ignore
            try:
                audit("reaction_ignored", payload.member, emoji_name=emoji_name, emoji_str=emoji_str)
            except Exception:
                pass
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        # Remove any existing class role to keep exactly one class
        removed = []
        for existing_class in CLASS_ROLES:
            existing_role = discord.utils.get(guild.roles, name=existing_class)
            if existing_role and existing_role in member.roles:
                await member.remove_roles(existing_role)
                removed.append(existing_class)

        # Add the selected class role
        role = discord.utils.get(guild.roles, name=class_name)
        if not role:
            logging.warning(f"[CLASS-REACTION] Role '{class_name}' not found.")
            try:
                await member.send(f"⚠️ I couldn't find the '{class_name}' role. Please ping an officer.")
            except Exception:
                pass
            return

        if role not in member.roles:
            await member.add_roles(role)

        # Persist class_assigned flag in DB
        uid = str(member.id)
        rec = verified_users.get(uid, {}) or {}
        rec["class_assigned"] = True
        verified_users[uid] = rec
        save_verified()  # persist global verified_users

        # Log to channel (optional) and audit
        onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel:
            await onboarding_channel.send(f"✅ {member.mention} assigned class role: **{class_name}**")

        try:
            audit("class_assigned_via_reaction",
                  member,
                  assigned=class_name,
                  removed=removed,
                  emoji_name=emoji_name,
                  emoji_str=emoji_str)
        except Exception:
            pass

        # Now attempt final promotion (adds Guild Member, removes Newcomer, sets verified=True)
        try:
            await check_verification(member)
        except Exception as e:
            logging.error(f"[ERROR] check_verification after reaction for {member}: {e}")
            try:
                audit("onboard_gate_post_class_error", member, error=str(e))
            except Exception:
                pass

    except Exception as e:
        logging.error(f"[ERROR] on_raw_reaction_add failed: {e}")
        try:
            audit("reaction_handler_error", None, error=str(e))
        except Exception:
            pass



# =========================
# READY / JOIN / REMOVE
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

    # Register persistent views (already in your code)
    try:
        bot.add_view(VerificationView())
        bot.add_view(ClassRoleView())
    except Exception as e:
        logging.error(f"[ERROR] add persistent views: {e}")

    # Retro-verify any pre-existing Guild Members
    for g in bot.guilds:
        await retro_verify_existing_members(g)

    # Register persistent views once per process
    # These views are stateless and will handle presses across restarts
    try:
        bot.add_view(VerificationView())
        bot.add_view(ClassRoleView())
    except Exception as e:
        logging.error(f"[ERROR] add persistent views: {e}")

    # Load Raid Mirror and backfill
    if not hasattr(bot, "_raid_mirror_loaded"):
        await register_raid_mirror(bot)
        bot._raid_mirror_loaded = True
        print("Bot ready to mirror channels")
        cog = bot.get_cog("CurrentWeekRaidMirror")
        if cog:
            for g in bot.guilds:
                await cog.refresh_all_mirrors(g)

    # Re-prompt class & verification UI for anyone still unverified
    #for guild in bot.guilds:
        #for uid, rec in list(verified_users.items()):
            #if not rec.get("verified"):
                #member = guild.get_member(int(uid))
                #if member:
                    #await prompt_for_class_role(member)
                    #ch = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
                    #if ch:
                        #try:
                            #await ch.send(
                               #f"{member.mention} Please follow the instructions and click the buttons below:",
                               # view=VerificationView()
                            #)
                        #except Exception as e:
                            #logging.error(f"[ERROR] repost verification UI: {e}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def onboardstatus(ctx, member: discord.Member = None):
    """Show onboarding flags for a member (default: caller)."""
    try:
        member = member or ctx.author
        uid = str(member.id)
        rec = verified_users.get(uid, {})
        legacy = user_flags.get(member.id, {"rules": False, "nickname": False})
        has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
        await ctx.send(
            "Onboarding status for {}:\n"
            "- rules_accepted: {}\n"
            "- nickname_confirmed: {}\n"
            "- class_assigned: {}\n"
            "- legacy.flags: {}\n"
            "- verified: {}\n"
            "- roles: {}".format(
                member.display_name,
                rec.get("rules_accepted", False),
                rec.get("nickname_confirmed", False),
                rec.get("class_assigned", has_class),
                legacy,
                rec.get("verified", False),
                ", ".join([r.name for r in member.roles])
            )
        )
        audit("onboardstatus_query", ctx.author, target=member.id)
    except Exception as e:
        await ctx.send("Failed to read onboarding status.")
        audit("onboardstatus_error", ctx.author, error=str(e))


@bot.event
async def on_member_join(member):
    try:
        audit("member_join", member, guild_id=member.guild.id)

        record = verified_users.get(str(member.id), {})
        if record.get("verified"):
            member_role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
            if member_role:
                await member.add_roles(member_role)
            try:
                await member.send("Welcome back! You're already verified.")
            except Exception:
                pass
            audit("member_rejoin_verified", member, roles=[r.name for r in member.roles])
            return

        newcomer_role = discord.utils.get(member.guild.roles, name=NEWCOMER_ROLE)
        newcomer_assigned = False
        if newcomer_role:
            await member.add_roles(newcomer_role)
            newcomer_assigned = True

        channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        if channel:
            await send_onboarding_embed(member)
            await channel.send(
                f"{member.mention} Please follow the instructions above:",
                view=VerificationView(member)
            )
            await prompt_for_class_role(member)

        audit("member_join_initialized",
              member,
              newcomer_assigned=newcomer_assigned,
              onboarding_channel_found=bool(channel),
              roles=[r.name for r in member.roles])
    except Exception as e:
        logging.error(f"[ERROR] on_member_join failed for {member.name}: {e}")
        audit("member_join_error", member, error=str(e))

@bot.event
async def on_member_remove(member):
    try:
        user_id = str(member.id)
        removed_verified = False
        removed_alts = False

        if user_id in verified_users:
            del verified_users[user_id]
            save_verified(verified_users)
            removed_verified = True

        if user_id in alts_data:
            del alts_data[user_id]
            save_alts(alts_data)
            removed_alts = True

        audit("member_remove", member, removed_verified=removed_verified, removed_alts=removed_alts)
    except Exception as e:
        logging.error(f"[ERROR] Failed to clean up member {member.name}: {e}")
        audit("member_remove_error", member, error=str(e))


# =========================
# ADMIN / UTILITY COMMANDS
# =========================
@bot.command(name="fixgate")
@commands.has_permissions(manage_guild=True)
async def fix_gate(ctx: commands.Context):
    """
    Re-run the onboarding gate for every member in this guild.
    Adds detailed logging + optional audit() calls for transparency.
    """
    guild = ctx.guild
    logging.info(f"[fixgate] Command invoked by {ctx.author} ({ctx.author.id}) in guild '{guild.name}' ({guild.id})")
    try:
        count = 0
        promoted_count = 0
        already_verified_count = 0
        errors = 0

        # Optional: structured audit header
        try:
            audit("fixgate_begin", ctx.author, guild_id=guild.id, guild_name=guild.name)
        except Exception:
            pass

        for m in guild.members:
            try:
                before_verified = verified_users.get(str(m.id), {}).get("verified", False)
                before_roles = [r.name for r in m.roles]

                await check_verification(m)

                after_verified = verified_users.get(str(m.id), {}).get("verified", False)
                after_roles = [r.name for r in m.roles]

                if after_verified and not before_verified:
                    promoted_count += 1
                    logging.info(f"[fixgate] PROMOTED {m} ({m.id}) — roles before={before_roles}, after={after_roles}")
                    try:
                        audit("fixgate_promoted", m, before_roles=before_roles, after_roles=after_roles)
                    except Exception:
                        pass
                elif after_verified:
                    already_verified_count += 1
                    logging.debug(f"[fixgate] ALREADY VERIFIED: {m} ({m.id}) — roles={after_roles}")

                count += 1
            except Exception as inner_e:
                errors += 1
                logging.error(f"[ERROR] fixgate failed for {m} ({m.id}): {inner_e}")
                try:
                    audit("fixgate_member_error", m, error=str(inner_e))
                except Exception:
                    pass

        # Summary logging
        logging.info(
            f"[fixgate] Completed. Total checked={count}, newly promoted={promoted_count}, "
            f"already verified={already_verified_count}, errors={errors}"
        )
        try:
            audit("fixgate_end",
                  ctx.author,
                  total_checked=count,
                  newly_promoted=promoted_count,
                  already_verified=already_verified_count,
                  errors=errors)
        except Exception:
            pass

        await ctx.send(
            f"✅ Rechecked onboarding gate for {count} members.\n"
            f"📈 Newly promoted: {promoted_count}\n"
            f"📋 Already verified: {already_verified_count}\n"
            f"⚠️ Errors: {errors}"
        )
    except Exception as e:
        logging.error(f"[ERROR] fixgate: {e}")
        try:
            audit("fixgate_fatal", ctx.author, error=str(e))
        except Exception:
            pass
        await ctx.send("❌ Failed to run gate fix; check logs.")

@bot.command(name="debuggate")
@commands.has_permissions(manage_guild=True)
async def debug_gate(ctx: commands.Context, member: discord.Member):
    """Show the gate flags and roles for a member."""
    uid = str(member.id)
    rec = verified_users.get(uid, {}) or {}
    rules_ok = bool(rec.get("rules_accepted"))
    nick_ok  = bool(rec.get("nickname_confirmed"))
    class_ok = bool(rec.get("class_assigned")) or any(r.name in CLASS_ROLES for r in member.roles)
    roles = ", ".join([r.name for r in member.roles]) or "(none)"
    await ctx.send(
        f"Gate for **{member.display_name}**:\n"
        f"- rules_accepted: {rules_ok}\n"
        f"- nickname_confirmed: {nick_ok}\n"
        f"- class_assigned flag: {rec.get('class_assigned', False)}\n"
        f"- class role present: {any(r.name in CLASS_ROLES for r in member.roles)}\n"
        f"- VERIFIED flag: {bool(rec.get('verified'))}\n"
        f"- ROLES: {roles}"
    )


@bot.command(name="auditsnapshot")
@commands.has_permissions(manage_guild=True)
async def audit_snapshot(ctx: commands.Context, *args: str):
    """
    Write a point-in-time onboarding snapshot into guild_audit.log,
    and print the info in the channel.

    Usage:
      !auditsnapshot
      !auditsnapshot verified
      !auditsnapshot unverified
      !auditsnapshot -A
      !auditsnapshot verified -A
      !auditsnapshot --alpha
    """
    try:
        # --- helper: safe audit() call (no-op if not present) ---
        def _audit(event: str, member: discord.abc.User | None = None, **fields):
            fn = globals().get("audit", None)
            if callable(fn):
                try:
                    fn(event, member, **fields)
                except Exception:
                    pass

        # --- emoji flag helper ---
        def flag(ok: bool) -> str:
            return "✅" if ok else "❌"

        # --- parse args: filter + sort mode ---
        # Filter: None (all), True (verified only), False (unverified only)
        filter_mode = None
        sort_alpha = False
        for a in args:
            s = a.strip().lower()
            if s in {"verified", "true", "yes", "y"}:
                filter_mode = True
            elif s in {"unverified", "not_verified", "false", "no", "n"}:
                filter_mode = False
            elif s in {"-a", "--alpha"}:
                sort_alpha = True

        guild = ctx.guild
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)

        # Collect raw rows first so we can sort/filter consistently
        rows = []
        for m in guild.members:
            uid = str(m.id)
            rec = verified_users.get(uid, {})

            rules_ok = bool(rec.get("rules_accepted"))
            # Only use the "clicked" flag; do NOT validate nickname format right now.
            nick_ok = bool(rec.get("nickname_confirmed"))

            # If you ever want to enforce policy again, uncomment below:
            # try:
            #     nick_ok = nick_ok and nickname_meets_policy(m.display_name)
            # except Exception:
            #     try:
            #         nick_ok = nick_ok and is_valid_wow_nickname(m.display_name)
            #     except Exception:
            #         pass

            has_class_role = any(r.name in CLASS_ROLES for r in m.roles)
            class_ok = bool(rec.get("class_assigned")) or has_class_role

            is_newcomer = (newcomer_role in m.roles) if newcomer_role else False
            is_member = (member_role in m.roles) if member_role else False
            verified_flag = bool(rec.get("verified"))
            gate_ready = rules_ok and nick_ok and class_ok

            rows.append({
                "member": m,
                "uid": uid,
                "rules_ok": rules_ok,
                "nick_ok": nick_ok,
                "class_ok": class_ok,
                "is_newcomer": is_newcomer,
                "is_member": is_member,
                "verified": verified_flag,
                "gate_ready": gate_ready,
                "joined_at": getattr(m, "joined_at", None),
                "roles": [r.name for r in m.roles],
            })

        # Apply filter (slice)
        if filter_mode is True:
            rows = [r for r in rows if r["verified"]]
        elif filter_mode is False:
            rows = [r for r in rows if not r["verified"]]

        # Sort rows: default chronological by joined_at (None at end), or alphabetically by display name
        if sort_alpha:
            rows.sort(key=lambda r: (r["member"].display_name or "").lower())
        else:
            from datetime import datetime as _dt
            rows.sort(key=lambda r: (r["joined_at"] or _dt.max))

        # Totals for the selected slice
        totals = {
            "members_total": 0,
            "newcomers": 0,
            "guild_members": 0,
            "verified_true": 0,
            "gate_ready_not_promoted": 0,
            "incomplete_rules": 0,
            "incomplete_nickname": 0,
            "incomplete_class": 0,
        }

        # Start audit block
        _audit(
            "snapshot_begin",
            None,
            guild_id=guild.id,
            guild_name=guild.name,
            invoked_by=ctx.author.id,
            filter=("verified" if filter_mode is True else "unverified" if filter_mode is False else "all"),
            sort=("alpha" if sort_alpha else "chronological")
        )

        # Build channel output
        sort_label = "alphabetical" if sort_alpha else "chronological"
        header = f"{'Member':<24} | {'Joined':<10} | Rules | Nick | Class | Verified | Newcomer | GuildMem | GateReady"
        sep = "-" * len(header)
        channel_lines = [f"[slice={('verified' if filter_mode is True else 'unverified' if filter_mode is False else 'all')}, sort={sort_label}]", header, sep]

        # Emit per-member + accumulate totals
        for r in rows:
            m = r["member"]
            rules_ok = r["rules_ok"]
            nick_ok = r["nick_ok"]
            class_ok = r["class_ok"]
            is_newcomer = r["is_newcomer"]
            is_member = r["is_member"]
            verified_flag = r["verified"]
            gate_ready = r["gate_ready"]
            joined_at = r["joined_at"]

            totals["members_total"] += 1
            if is_newcomer:
                totals["newcomers"] += 1
            if is_member:
                totals["guild_members"] += 1
            if verified_flag:
                totals["verified_true"] += 1
            if gate_ready and not is_member:
                totals["gate_ready_not_promoted"] += 1
            if not rules_ok:
                totals["incomplete_rules"] += 1
            if not nick_ok:
                totals["incomplete_nickname"] += 1
            if not class_ok:
                totals["incomplete_class"] += 1

            # Log this member in the audit file
            _audit(
                "snapshot_member",
                m,
                roles=r["roles"],
                rules_ok=rules_ok,
                nickname_ok=nick_ok,
                class_ok=class_ok,
                verified=verified_flag,
                newcomer=is_newcomer,
                guild_member=is_member,
                gate_ready=gate_ready,
                joined_at=(joined_at.isoformat() if joined_at else None)
            )

            # Channel row
            joined_str = "-"
            try:
                if joined_at:
                    joined_str = joined_at.strftime("%Y-%m-%d")
            except Exception:
                pass

            channel_lines.append(
                f"{m.display_name:<24} | {joined_str:<10} | "
                f"{flag(rules_ok)}   | {flag(nick_ok)}  | {flag(class_ok)}   | "
                f"{flag(verified_flag)}      | {flag(is_newcomer)}       | {flag(is_member)}     | {flag(gate_ready)}"
            )

        # End audit block
        _audit("snapshot_summary", None, **totals)
        _audit("snapshot_end", None, guild_id=guild.id)

        # Human-friendly slice label
        slice_label = "verified" if filter_mode is True else "unverified" if filter_mode is False else "all members"

        # Send audit log confirmation + totals
        await ctx.send(
            "📘 Snapshot written to `guild_audit.log` "
            f"(slice: **{slice_label}**, sort: **{sort_label}**).\n"
            f"Total in slice: {totals['members_total']} | "
            f"Newcomers: {totals['newcomers']} | "
            f"Guild Members: {totals['guild_members']} | "
            f"Verified flag: {totals['verified_true']} | "
            f"Gate ready (not promoted): {totals['gate_ready_not_promoted']}"
        )

        # If the slice is empty, say so
        if totals["members_total"] == 0:
            await ctx.send("ℹ️ No members matched the requested filter.")
            return

        # Send detailed table in channel (chunk to respect Discord 2000 char limit)
        detailed_output = "```\n" + "\n".join(channel_lines) + "\n```"
        for i in range(0, len(detailed_output), 1990):
            await ctx.send(detailed_output[i:i+1990])

    except Exception as e:
        logging.error(f"[ERROR] audit_snapshot failed: {e}")
        fn = globals().get("audit", None)
        if callable(fn):
            try:
                fn("snapshot_error", ctx.author, error=str(e))
            except Exception:
                pass
        await ctx.send("❌ Failed to write audit snapshot. Check logs.")

async def retro_verify_existing_members(guild: discord.Guild) -> None:
    """
    On startup, reconcile verification state with roles:
      - If a member HAS the Guild Member role but is not verified in DB -> mark verified and fill flags.
      - If a member IS verified in DB but LACKS the Guild Member role -> grant Guild Member role.
      - If Newcomer remains on a verified member -> remove it.
      - Persist changes and write structured audit lines.
    """
    try:
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE)
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)

        if not member_role:
            logging.warning(f"[RETROVERIFY] '{MEMBER_ROLE}' role not found in guild '{guild.name}' ({guild.id})")
            return

        total_checked = 0
        total_updated_db = 0
        total_added_member_role = 0
        total_removed_newcomer = 0

        for m in guild.members:
            total_checked += 1

            uid = str(m.id)
            rec = verified_users.get(uid, {}) or {}

            has_member_role = member_role in m.roles
            has_newcomer_role = newcomer_role in m.roles if newcomer_role else False
            has_any_class_role = any(r.name in CLASS_ROLES for r in m.roles)

            # Case A: Member has Guild Member role but is not verified in DB -> mark verified + fill flags.
            if has_member_role and not rec.get("verified", False):
                rec["verified"] = True
                rec["rules_accepted"] = True
                rec["nickname_confirmed"] = True
                if has_any_class_role:
                    rec["class_assigned"] = True
                verified_users[uid] = rec
                save_verified()  # persist this change
                total_updated_db += 1

                # If they also still have Newcomer, remove it.
                if has_newcomer_role:
                    try:
                        await m.remove_roles(newcomer_role, reason="Retro-verify: already Guild Member")
                        total_removed_newcomer += 1
                    except Exception as e:
                        logging.error(f"[RETROVERIFY] Failed removing Newcomer from {m}: {e}")

                # Nothing to add here; they already have Member role.

                try:
                    audit(
                        "retro_verify_member",
                        m,
                        set_verified=True,
                        set_rules_accepted=True,
                        set_nickname_confirmed=True,
                        set_class_assigned=bool(rec.get("class_assigned")),
                        removed_newcomer=has_newcomer_role,
                        ensured_member_role=True,
                        roles=[r.name for r in m.roles]
                    )
                except Exception:
                    pass
                continue  # next member

            # Case B: Member is verified in DB but lacks Guild Member role -> grant it now.
            if rec.get("verified", False) and not has_member_role:
                try:
                    await m.add_roles(member_role, reason="Retro-verify: verified but missing Guild Member")
                    total_added_member_role += 1
                except Exception as e:
                    logging.error(f"[RETROVERIFY] Failed adding Guild Member to {m}: {e}")

                # If they still have Newcomer, remove it.
                if has_newcomer_role:
                    try:
                        await m.remove_roles(newcomer_role, reason="Retro-verify: verified user cleanup")
                        total_removed_newcomer += 1
                    except Exception as e:
                        logging.error(f"[RETROVERIFY] Failed removing Newcomer from {m}: {e}")

                try:
                    audit(
                        "retro_verify_promote_member_role",
                        m,
                        ensured_member_role=True,
                        removed_newcomer=has_newcomer_role,
                        verified_in_db=True,
                        roles=[r.name for r in m.roles]
                    )
                except Exception:
                    pass
                continue  # next member

            # Case C: Already consistent (either verified+member, or unverified+no member)
            # If they are verified+member and still have Newcomer -> cleanup.
            if rec.get("verified", False) and has_member_role and has_newcomer_role:
                try:
                    await m.remove_roles(newcomer_role, reason="Retro-verify: cleanup for verified member")
                    total_removed_newcomer += 1
                    try:
                        audit("retro_verify_cleanup_newcomer", m, removed_newcomer=True)
                    except Exception:
                        pass
                except Exception as e:
                    logging.error(f"[RETROVERIFY] Failed removing stray Newcomer from {m}: {e}")

        # Summary audit
        try:
            audit(
                "retro_verify_summary",
                None,
                guild_id=guild.id,
                guild_name=guild.name,
                total_checked=total_checked,
                total_updated_db=total_updated_db,
                total_added_member_role=total_added_member_role,
                total_removed_newcomer=total_removed_newcomer
            )
        except Exception:
            pass

        logging.info(
            f"[RETROVERIFY] Guild '{guild.name}' ({guild.id}) "
            f"checked={total_checked} updated_db={total_updated_db} "
            f"added_member_role={total_added_member_role} removed_newcomer={total_removed_newcomer}"
        )

    except Exception as e:
        logging.error(f"[RETROVERIFY] Error reconciling members in guild '{guild.name}': {e}")
        try:
            audit("retro_verify_error", None, guild_id=guild.id, error=str(e))
        except Exception:
            pass



def is_admin_or_owner(ctx):
    return (
        ctx.author.guild_permissions.administrator
        or ctx.author.display_name == BOT_OWNER_NAME
        or ctx.author.name == BOT_OWNER_NAME
    )

@bot.command()
@commands.has_permissions(manage_roles=True)
async def reverify(ctx, member: discord.Member = None):
    """Re-post onboarding UI and class prompt for a user."""
    try:
        member = member or ctx.author
        await send_onboarding_embed(member)
        await ctx.send(f"{member.mention} please complete onboarding below:", view=VerificationView())
        await prompt_for_class_role(member)
        await ctx.send("Class selection prompt re-sent.")
    except Exception as e:
        logging.error(f"[ERROR] reverify: {e}")
        await ctx.send("Failed to re-post onboarding. Check logs.")

@bot.command(name="refreshraids")
@commands.has_permissions(manage_messages=True)
async def refresh_raids_command(ctx: commands.Context, scan: int = 50):
    """Refresh the #current-raids channel from configured sources."""
    try:
        cog = bot.get_cog("CurrentWeekRaidMirror")
        if not cog:
            await ctx.send("Raid mirror Cog is not loaded.")
            return
        await cog.refresh_all_mirrors(ctx.guild, per_channel_scan=scan)
        await ctx.send(f"Current raids refreshed (scanned up to {scan} messages per source).")
    except Exception as e:
        await ctx.send("Failed to refresh current raids.")
        logging.error(f"[ERROR] refreshraids: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def verified(ctx):
    """List all currently verified users by ID."""
    try:
        await ctx.send(f"Verified users: {list(verified_users.keys())}")
    except Exception as e:
        logging.error(f"[ERROR] verified cmd: {e}")
        await ctx.send("Failed to list verified users.")

# =========================
# ALT / CLASS COMMANDS (unchanged behavior)
# =========================
@bot.command()
async def listalts(ctx, member: discord.Member = None):
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
        logging.error(f"[ERROR] listalts: {e}")
        await ctx.send("❌ Error retrieving alts.")

@bot.command()
async def reassignalt(ctx, alt_name: str, member: discord.Member, alt_class: str):
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
        new_owner_id = str(member.id)
        alts_data[new_owner_id] = alts_data.get(new_owner_id, {})
        alts_data[new_owner_id].setdefault("alts", {})
        alts_data[new_owner_id]["alts"][alt_name] = alt_class
        alts_data[new_owner_id]["main"] = member.display_name
        save_alts()
        await ctx.send(f"🔄 `{alt_name}` ({alt_class}) is now assigned as an alt to `{member.display_name}`.")
    except Exception as e:
        logging.error(f"[ERROR] reassignalt: {e}")
        await ctx.send("❌ Error reassigning alt.")

@bot.command()
async def setmainfor(ctx, member: discord.Member, main_name: str, main_class: str = None):
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
        save_alts()
        await ctx.send(f"🛠 `{member.display_name}`'s main set to `{main_name}`" + (f" with class `{main_class}`." if main_class else "."))
    except Exception as e:
        logging.error(f"[ERROR] setmainfor: {e}")
        await ctx.send("❌ Error setting main.")

@bot.command()
async def classstats(ctx):
    try:
        class_members = {cls: [] for cls in CLASS_ROLES}
        all_members_combined = {cls: [] for cls in CLASS_ROLES}
        is_alt_flags = {}

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

        summary_lines = ["**Vindicated's Class Composition**"]
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
        logging.error(f"[ERROR] classstats: {e}")
        await ctx.send("❌ Error generating class stats.")

@bot.command()
async def addalt(ctx, alt_name: str, *, alt_class: str = None):
    try:
        if not alt_class:
            await ctx.send(
                "Usage: `!addalt <alt_name> <class>`\n"
                f"Valid classes: {', '.join(CLASS_ROLES)}"
            )
            return
        user_id = str(ctx.author.id)
        alt_class = alt_class.strip().capitalize()
        if alt_class not in CLASS_ROLES:
            await ctx.send(f"Invalid class `{alt_class}`. Choose from: {', '.join(CLASS_ROLES)}")
            return
        record = alts_data.get(user_id, {})
        record.setdefault("alts", {})
        record.setdefault("main", ctx.author.display_name)
        if alt_name in record["alts"]:
            await ctx.send(f"Alt `{alt_name}` is already linked to your account.")
            return
        if len(record["alts"]) >= 9:
            await ctx.send("You can only have up to 9 alts per main (10 characters total).")
            return
        record["alts"][alt_name] = alt_class
        alts_data[user_id] = record
        save_alts()
        await ctx.send(f"Added alt `{alt_name}` with class `{alt_class}` to your account.")
    except Exception as e:
        logging.error(f"[ERROR] addalt: {e}")
        await ctx.send("Error adding alt.")

@bot.command()
async def removealt(ctx, alt_name: str):
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
        save_alts()
        await ctx.send(f"🗑 Removed alt `{alt_name}` from your account.")
    except Exception as e:
        logging.error(f"[ERROR] removealt: {e}")
        await ctx.send("❌ Error removing alt.")

@bot.command()
async def whoismain(ctx, alt_name: str):
    try:
        for main_id, record in alts_data.items():
            if alt_name in record.get("alts", {}):
                main = record.get("main", "Unknown")
                await ctx.send(f"🧾 `{alt_name}` belongs to main: `{main}`")
                return
        await ctx.send(f"❌ `{alt_name}` not found in alt records.")
    except Exception as e:
        logging.error(f"[ERROR] whoismain: {e}")
        await ctx.send("❌ Error checking alt ownership.")

@bot.command()
@commands.has_permissions(administrator=True)
async def importalts(ctx):
    try:
        with open('alts_import.csv', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                main_name = row[0].strip()
                alts = [alt.strip() for alt in row[1:] if alt.strip()]
                main_member = discord.utils.get(ctx.guild.members, display_name=main_name)
                if main_member:
                    alts_data[str(main_member.id)] = {"main": main_name, "alts": {a: "Unknown" for a in alts}}
        save_alts()
        await ctx.send("📥 Alts imported successfully from alts_import.csv")
    except Exception as e:
        logging.error(f"[ERROR] importalts: {e}")
        await ctx.send(f"❌ Error importing alts: {e}")

@bot.command()
async def classstatus(ctx, member: discord.Member = None):
    try:
        member = member or ctx.author
        assigned_class = next((role.name for role in member.roles if role.name in CLASS_ROLES), None)
        if assigned_class:
            await ctx.send(f"📜 {member.display_name} has class role: **{assigned_class}**")
        else:
            await ctx.send(f"❌ {member.display_name} does not have a class role assigned.")
    except Exception as e:
        logging.error(f"[ERROR] classstatus: {e}")
        await ctx.send("❌ Error checking class status.")

@bot.command()
@commands.has_permissions(administrator=True)
async def exportclasses(ctx):
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
        logging.error(f"[ERROR] exportclasses: {e}")
        await ctx.send("❌ Error exporting class roles.")

@bot.command()
async def resetclass(ctx, member: discord.Member = None):
    try:
        RESET_COOLDOWN_SECONDS = 60
        now = time.time()
        if not hasattr(bot, "_reset_cooldowns"):
            bot._reset_cooldowns = {}
        caller_id = ctx.author.id
        if caller_id in bot._reset_cooldowns and now - bot._reset_cooldowns[caller_id] < RESET_COOLDOWN_SECONDS:
            remaining = int(RESET_COOLDOWN_SECONDS - (now - bot._reset_cooldowns[caller_id]))
            await ctx.send(f"⏱ Please wait {remaining} seconds before using this command again.")
            return
        bot._reset_cooldowns[caller_id] = now

        if member is None:
            member = ctx.author
        elif not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ You don't have permission to reset others.")
            return

        # Remove any class roles + reset persistent flag
        for role_name in CLASS_ROLES:
            role = discord.utils.get(member.guild.roles, name=role_name)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role)
                except Exception as e:
                    logging.error(f"[ERROR] remove role {role_name} from {member}: {e}")

        uid = str(member.id)
        rec = verified_users.get(uid, {})
        rec["class_assigned"] = False
        verified_users[uid] = rec
        save_verified()

        onboarding_channel = discord.utils.get(ctx.guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel:
            await onboarding_channel.send(f"🔁 {member.mention}'s class role prompt has been reset by {ctx.author.mention}.")
        await prompt_for_class_role(member)
        await ctx.send(f"✅ {member.display_name} has been prompted again for class role selection.")
    except Exception as e:
        logging.error(f"[ERROR] resetclass: {e}")
        await ctx.send("❌ Error resetting class selection.")

@bot.command()
async def count_raiders(ctx):
    try:
        raider_role = discord.utils.get(ctx.guild.roles, name="Raider")
        if not raider_role:
            await ctx.send("The 'Raider' role does not exist.")
            return
        raiders = [member for member in ctx.guild.members if raider_role in member.roles]
        await ctx.send(f"There are {len(raiders)} members with the Raider role.")
    except Exception as e:
        logging.error(f"[ERROR] count_raiders: {e}")
        await ctx.send("Failed to count Raider members.")

@bot.command()
async def count_members(ctx):
    try:
        member_role = discord.utils.get(ctx.guild.roles, name=MEMBER_ROLE)
        if not member_role:
            await ctx.send(f"The '{MEMBER_ROLE}' role does not exist.")
            return
        members = [member for member in ctx.guild.members if member_role in member.roles]
        await ctx.send(f"There are {len(members)} members with the {MEMBER_ROLE} role.")
    except Exception as e:
        logging.error(f"[ERROR] count_members: {e}")
        await ctx.send(f"Failed to count {MEMBER_ROLE} members.")

@bot.command()
async def list_officers(ctx):
    try:
        officer_role = discord.utils.get(ctx.guild.roles, name="Officer")
        if not officer_role:
            await ctx.send("The 'Officer' role does not exist.")
            return
        officers = [member.display_name for member in ctx.guild.members if officer_role in member.roles]
        if officers:
            officer_list = "\n".join(officers)
            await ctx.send(f"**Officer List:**\n{officer_list}")
        else:
            await ctx.send("There are currently no officers assigned.")
    except Exception as e:
        logging.error(f"[ERROR] list_officers: {e}")
        await ctx.send("Failed to list officers.")

@bot.command()
async def count_class(ctx, class_name: str):
    try:
        class_name = class_name.capitalize()
        class_role = discord.utils.get(ctx.guild.roles, name=class_name)
        if not class_role:
            await ctx.send(f"Class role '{class_name}' does not exist.")
            return
        players = [member for member in ctx.guild.members if class_role in member.roles]
        await ctx.send(f"There are {len(players)} members with the {class_name} class role.")
    except Exception as e:
        logging.error(f"[ERROR] count_class: {e}")
        await ctx.send("Failed to count class members.")

# =========================
# RUN
# =========================
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN not set.")
    bot.run(TOKEN)
