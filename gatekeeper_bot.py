# guild_gatekeeper_persistent.py
#
# Guild Gatekeeper Bot (Persistent Verification Variant)
# - Stateless, persistent verification UI using fixed custom_id's
# - Robust reaction→class mapping (supports custom emoji names; optional unicode)
# - Adds missing check_verification() gate
# - Reuses/extends your raid mirror, alt tools, stats, exports, etc.
#
# Requires: discord.py 2.x, matplotlib, python-dotenv

import asyncio
import os
import io
import csv
import json
import logging
import time
import re
from datetime import datetime, timezone
from typing import Dict, Optional
import sys
import queue
from logging.handlers import QueueHandler, QueueListener
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

VISITOR_ROLE = "Visitor"  # the “just visiting” role
DEFAULT_TRACK = "member"  # keep existing default behavior
VALID_TRACKS = {"member", "visitor"}
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
    global _audit_listener
    logger = logging.getLogger("guild_audit")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)

    # File handler (UTF-8, safe for emoji)
    fh = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Non-blocking queue path
    q = queue.SimpleQueue()
    qh = QueueHandler(q)
    logger.addHandler(qh)

    # Listener writes records to the file handler on a separate thread
    _audit_listener = QueueListener(q, fh, respect_handler_level=True)
    _audit_listener.daemon = True
    _audit_listener.start()

    # Optional console mirror ONLY if the console is UTF-8 (e.g., Windows Terminal with UTF-8)
    try:
        if sys.stderr and sys.stderr.encoding and "UTF-8" in sys.stderr.encoding.upper():
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(
                fmt="%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S"
            ))
            logger.addHandler(ch)
    except Exception:
        # If the console isn’t UTF-8, just skip adding a console handler
        pass

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
# ONBOARDING / VERIFICATION UI
# =========================
async def send_onboarding_embed(member: discord.Member):
    """
    Posts the onboarding message with a track choice (member vs visitor)
    and the persistent VerificationView (track buttons + verify buttons + class select).
    """
    try:
        onboarding_channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        if not onboarding_channel:
            return

        embed = discord.Embed(
            title="Welcome to Vindicated!",
            description=(
                "Choose your track below:\n"
                "• **I’m joining the guild** → you’ll become **Guild Member** after onboarding.\n"
                "• **I’m just visiting** → you’ll become **Visitor** after onboarding.\n\n"
                "Then complete these steps:\n"
                "1) Update your **server nickname** to your main WoW character\n"
                "2) **Accept the rules**\n"
                "3) **Confirm nickname**\n"
                "4) **Choose your class**"
            ),
            color=discord.Color.blue()
        )

        await onboarding_channel.send(
            content=f"{member.mention}",
            embed=embed,
            view=VerificationView()  # persistent, includes track + verify + class select
        )
    except Exception as e:
        logging.error(f"[ERROR] send_onboarding_embed: {e}")


# ---------- Persistent Verification View (stateless) ----------
# Buttons use fixed custom_id values; they do not capture 'member'.
# We derive the acting user from interaction.user, and store flags in verified_users[uid].

class VerificationView(View):
    """
    Persistent, stateless verification UI.
    Includes:
      - Track selection: member vs visitor
      - Accept Rules button
      - Confirm Nickname button
      - Class dropdown (via ClassRoleSelect)
    Persists state in `verified_users[uid]`.

    UPDATE: Track buttons are now gated to *new users only* to prevent
    verified users from flipping roles later.
    """
    def __init__(self):
        super().__init__(timeout=None)
        # Put the class selector on the same message for a single-stop UI.
        self.add_item(ClassRoleSelect())

    # --- helpers ---
    @staticmethod
    def _nickname_ok(display_name: str) -> bool:
        try:
            return nickname_meets_policy(display_name)  # optional stricter policy
        except Exception:
            try:
                return is_valid_wow_nickname(display_name)
            except Exception:
                return True

    @staticmethod
    def _audit(event: str, member: discord.abc.User | None, **fields):
        fn = globals().get("audit", None)
        if callable(fn):
            try:
                fn(event, member, **fields)
            except Exception:
                pass

    @staticmethod
    def _set_track(uid: str, track: str):
        rec = verified_users.get(uid, {}) or {}
        rec["track"] = track if track in VALID_TRACKS else DEFAULT_TRACK
        verified_users[uid] = rec
        save_verified()

    @staticmethod
    def _is_new_user(member: discord.Member) -> bool:
        """
        New user = not verified yet AND either:
          - currently has Newcomer, OR
          - has neither Guild Member nor Visitor.
        """
        rec = verified_users.get(str(member.id), {})
        has_newcomer = any(r.name == NEWCOMER_ROLE for r in member.roles)
        has_track_role = any(r.name in (MEMBER_ROLE, VISITOR_ROLE) for r in member.roles)
        return (not rec.get("verified")) and (has_newcomer or not has_track_role)

    # ---------- TRACK: Member ----------
    @discord.ui.button(
        label="🛡 I’m joining the guild",
        style=discord.ButtonStyle.primary,
        custom_id="track:member"
    )
    async def choose_member_track(self, interaction: discord.Interaction, button: Button):
        try:
            user = interaction.user
            if not self._is_new_user(user):
                await interaction.response.send_message(
                    "Track selection is only available for newcomers. "
                    "If you need a change, please ping an officer.",
                    ephemeral=True
                )
                self._audit("track_select_blocked", user, attempted="member")
                return

            uid = str(user.id)
            self._set_track(uid, "member")
            await interaction.response.send_message(
                "Track set: **Guild Member**. Complete the steps to be promoted to **Guild Member**.",
                ephemeral=True
            )
            self._audit("track_selected", user, track="member")
            await log_verification_event(interaction.guild, user, "Selected Track", {"track": "member"})
            await check_verification(user)
        except Exception as e:
            logging.error(f"[ERROR] choose_member_track: {e}")
            self._audit("track_select_error", interaction.user, error=str(e))

    # ---------- TRACK: Visitor ----------
    @discord.ui.button(
        label="👋 I’m just visiting",
        style=discord.ButtonStyle.secondary,
        custom_id="track:visitor"
    )
    async def choose_visitor_track(self, interaction: discord.Interaction, button: Button):
        try:
            user = interaction.user
            if not self._is_new_user(user):
                await interaction.response.send_message(
                    "Track selection is only available for newcomers. "
                    "If you need a change, please ping an officer.",
                    ephemeral=True
                )
                self._audit("track_select_blocked", user, attempted="visitor")
                return

            uid = str(user.id)
            self._set_track(uid, "visitor")
            await interaction.response.send_message(
                "Track set: **Visitor**. Complete the steps to be promoted to **Visitor**.",
                ephemeral=True
            )
            self._audit("track_selected", user, track="visitor")
            await log_verification_event(interaction.guild, user, "Selected Track", {"track": "visitor"})
            await check_verification(user)
        except Exception as e:
            logging.error(f"[ERROR] choose_visitor_track: {e}")
            self._audit("track_select_error", interaction.user, error=str(e))

    # ---------- BUTTON: Accept Rules ----------
    @discord.ui.button(
        label="✅ I Accept the Rules",
        style=discord.ButtonStyle.success,
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
            save_verified()

            await interaction.response.send_message("✅ Rules accepted!", ephemeral=True)
            self._audit("rules_accepted", user)

            await log_verification_event(interaction.guild, user, "Accepted Rules", rec)
            await check_verification(user)
        except Exception as e:
            logging.error(f"[ERROR] accept_rules: {e}")
            self._audit("rules_accept_error", interaction.user, error=str(e))

    # ---------- BUTTON: Confirm Nickname ----------
    @discord.ui.button(
        label="✅ I Updated My Nickname",
        style=discord.ButtonStyle.primary,
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

            # Optional policy gate:
            # if not self._nickname_ok(display):
            #     await interaction.response.send_message(
            #         "Your nickname doesn't match the required format. "
            #         "Please set it to your **main WoW character name** and try again.",
            #         ephemeral=True
            #     )
            #     self._audit("nickname_invalid", user, display=display)
            #     return

            rec["nickname_confirmed"] = True
            verified_users[uid] = rec
            save_verified()

            await interaction.response.send_message("🏷 Nickname confirmed!", ephemeral=True)
            self._audit("nickname_confirmed", user, display=display)

            await log_verification_event(interaction.guild, user, "Confirmed Nickname", rec)
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

# =========================
# FINAL GATE (track-aware)
# =========================
async def check_verification(member: discord.Member) -> None:
    """
    Promote a user after onboarding based on selected track:
      track == "member"  -> add Guild Member
      track == "visitor" -> add Visitor
    Steps required:
      - rules_accepted
      - nickname_confirmed
      - class_assigned OR already has a class role
    Always removes Newcomer on success. Persists verified flag.

    UPDATE: Only performs promotion/track-based role changes for *newcomers* or
    users who are not yet verified. This prevents verified users from flipping
    between Visitor/Guild Member by pressing buttons later.
    """
    try:
        uid = str(member.id)
        rec = verified_users.get(uid, {}) or {}

        rules_ok = bool(rec.get("rules_accepted"))
        nick_ok  = bool(rec.get("nickname_confirmed"))
        class_ok = bool(rec.get("class_assigned")) or any(r.name in CLASS_ROLES for r in member.roles)

        track = rec.get("track", DEFAULT_TRACK)
        if track not in VALID_TRACKS:
            track = DEFAULT_TRACK

        guild = member.guild
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)
        member_role   = discord.utils.get(guild.roles, name=MEMBER_ROLE)
        visitor_role  = discord.utils.get(guild.roles, name=VISITOR_ROLE)

        is_newcomer = (newcomer_role in member.roles) if newcomer_role else False
        is_already_verified = bool(rec.get("verified"))
        allow_promotion = is_newcomer or not is_already_verified

        try:
            audit(
                "onboard_gate_check",
                member,
                rules_ok=rules_ok,
                nick_ok=nick_ok,
                class_ok=class_ok,
                track=track,
                currently_verified=is_already_verified,
                allow_promotion=allow_promotion,
                roles=[r.name for r in member.roles]
            )
        except Exception:
            pass

        # Not ready or not allowed to change anything → stop.
        if not (rules_ok and nick_ok and class_ok):
            return
        if not allow_promotion:
            return

        target_role_name = MEMBER_ROLE if track == "member" else VISITOR_ROLE
        target_role = member_role if track == "member" else visitor_role
        other_role  = visitor_role if track == "member" else member_role

        added_target = False
        removed_newcomer = False

        # Add target role if missing
        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason=f"Completed onboarding ({track})")
                added_target = True
            except Exception as e:
                logging.error(f"[ERROR] add {target_role_name} to {member}: {e}")

        # Ensure the opposite track role is not lingering
        if other_role and other_role in member.roles:
            try:
                await member.remove_roles(other_role, reason="Switching onboarding track")
            except Exception as e:
                logging.error(f"[ERROR] remove other track role from {member}: {e}")

        # Remove Newcomer if present
        if newcomer_role and newcomer_role in member.roles:
            try:
                await member.remove_roles(newcomer_role, reason="Completed onboarding")
                removed_newcomer = True
            except Exception as e:
                logging.error(f"[ERROR] remove Newcomer from {member}: {e}")

        # Persist verified flag
        if not rec.get("verified"):
            rec["verified"] = True
            verified_users[uid] = rec
            save_verified()

        # Channel notice
        onboarding_channel = discord.utils.get(guild.text_channels, name=ONBOARDING_CHANNEL)
        if onboarding_channel and (added_target or removed_newcomer):
            try:
                await onboarding_channel.send(
                    f"🎉 {member.mention} has completed onboarding and is now a **{target_role_name}**!"
                )
            except Exception:
                pass

        # Audit
        try:
            audit(
                "onboard_promoted",
                member,
                track=track,
                added_role=target_role_name,
                removed_newcomer=removed_newcomer,
                verified=True,
                roles=[r.name for r in member.roles]
            )
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
                v = View(timeout=None)
                v.add_item(ClassRoleSelect())  # reuse the persistent select with the same custom_id
                msg = await onboarding_channel.send(
                    f"{member.mention}, please select your class (dropdown or reactions):",
                    view=v
                )
                # Add custom emoji reactions by name if present in the guild
                for key, mapped_class in CLASS_EMOJIS.items():
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

    # 🔁 Retro-verify any pre-existing Guild Members on startup
    for g in bot.guilds:
        await retro_verify_existing_members(g)

    # Register persistent views once per process
    try:
        # Only the canonical verification view (includes ClassRoleSelect)
        bot.add_view(VerificationView())
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


@bot.command()
@commands.has_permissions(manage_guild=True)
async def onboardstatus(ctx, member: discord.Member = None):
    """Show onboarding flags for a member (default: caller)."""
    try:
        member = member or ctx.author
        uid = str(member.id)
        rec = verified_users.get(uid, {})
        track = rec.get("track", DEFAULT_TRACK)
        # If you still want legacy flags, define user_flags = {} somewhere, or guard like below:
        legacy = {}
        try:
            legacy = user_flags.get(member.id, {"rules": False, "nickname": False})  # type: ignore[name-defined]
        except Exception:
            legacy = {"rules": False, "nickname": False}

        has_class = any(discord.utils.get(member.roles, name=cls) for cls in CLASS_ROLES)
        await ctx.send(
            "Onboarding status for {}:\n"
            "- track: {}\n"
            "- rules_accepted: {}\n"
            "- nickname_confirmed: {}\n"
            "- class_assigned: {}\n"
            "- legacy.flags: {}\n"
            "- verified: {}\n"
            "- roles: {}".format(
                member.display_name,
                track,
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
            track = record.get("track", DEFAULT_TRACK)
            member_role  = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
            visitor_role = discord.utils.get(member.guild.roles, name=VISITOR_ROLE)
            newcomer_role = discord.utils.get(member.guild.roles, name=NEWCOMER_ROLE)

            target_role = member_role if track == "member" else visitor_role
            if target_role:
                try:
                    await member.add_roles(target_role)
                except Exception as e:
                    logging.error(f"[REJOIN] Failed adding {track} role to {member}: {e}")

            if newcomer_role and newcomer_role in member.roles:
                try:
                    await member.remove_roles(newcomer_role, reason="Rejoin cleanup")
                except Exception as e:
                    logging.error(f"[REJOIN] Failed removing Newcomer from {member}: {e}")

            try:
                await member.send("Welcome back! You're already verified.")
            except Exception:
                pass
            audit("member_rejoin_verified", member, track=track, roles=[r.name for r in member.roles])
            return

        # New user path (unchanged except note about duplicate sends below)
        newcomer_role = discord.utils.get(member.guild.roles, name=NEWCOMER_ROLE)
        newcomer_assigned = False
        if newcomer_role:
            await member.add_roles(newcomer_role)
            newcomer_assigned = True

        channel = discord.utils.get(member.guild.text_channels, name=ONBOARDING_CHANNEL)
        if channel:
            # EITHER just this:
            await send_onboarding_embed(member)
            # (and remove the two lines below)
            # OR if you prefer a second message, remove send_onboarding_embed above.
            # await channel.send(f"{member.mention} Please follow the instructions above:", view=VerificationView())
            # await prompt_for_class_role(member)  # not needed if VerificationView includes ClassRoleSelect

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
        filter_mode = None        # None (all), True (verified only), False (unverified only)
        sort_alpha  = False       # default chronological by joined_at
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
        member_role   = discord.utils.get(guild.roles, name=MEMBER_ROLE)

        # ------------------------------------------------------------
        # PREPASS: if a member ALREADY has Guild Member, ensure stored
        # flags (rules_accepted / nickname_confirmed) are True in DB.
        # ------------------------------------------------------------
        changed = 0
        for m in guild.members:
            if member_role and (member_role in m.roles):
                uid = str(m.id)
                rec = verified_users.get(uid, {}) or {}
                to_update = False

                if not rec.get("rules_accepted", False):
                    rec["rules_accepted"] = True
                    to_update = True
                if not rec.get("nickname_confirmed", False):
                    rec["nickname_confirmed"] = True
                    to_update = True

                if to_update:
                    verified_users[uid] = rec
                    changed += 1
                    try:
                        _audit(
                            "snapshot_autoset_flags",
                            m,
                            set_rules_accepted=True,
                            set_nickname_confirmed=True,
                            had_member_role=True
                        )
                    except Exception:
                        pass

        if changed:
            save_verified()  # persist the batch of fixes

        # ------------------------------------------------------------
        # Build snapshot rows (now using the updated stored flags)
        # ------------------------------------------------------------
        rows = []
        for m in guild.members:
            uid = str(m.id)
            rec = verified_users.get(uid, {})

            is_newcomer = (newcomer_role in m.roles) if newcomer_role else False
            is_member   = (member_role in m.roles) if member_role else False

            # Read stored flags AFTER pre-fix
            rules_ok = bool(rec.get("rules_accepted"))
            nick_ok  = bool(rec.get("nickname_confirmed"))

            # Class: either the stored flag OR actually having a class role
            has_class_role = any(r.name in CLASS_ROLES for r in m.roles)
            class_ok = bool(rec.get("class_assigned")) or has_class_role

            verified_flag = bool(rec.get("verified"))
            gate_ready    = rules_ok and nick_ok and class_ok

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
            sort=("alpha" if sort_alpha else "chronological"),
            prepass_autofixed=changed
        )

        # Build channel output
        sort_label = "alphabetical" if sort_alpha else "chronological"
        header = f"{'Member':<24} | {'Joined':<10} | Rules | Nick | Class | Verified | Newcomer | GuildMem | GateReady"
        sep = "-" * len(header)
        channel_lines = [f"[slice={('verified' if filter_mode is True else 'unverified' if filter_mode is False else 'all')}, sort={sort_label}, autofixed={changed}]", header, sep]

        # Emit per-member + accumulate totals
        for r in rows:
            m = r["member"]
            rules_ok    = r["rules_ok"]
            nick_ok     = r["nick_ok"]
            class_ok    = r["class_ok"]
            is_newcomer = r["is_newcomer"]
            is_member   = r["is_member"]
            verified_f  = r["verified"]
            gate_ready  = r["gate_ready"]
            joined_at   = r["joined_at"]

            totals["members_total"] += 1
            if is_newcomer:
                totals["newcomers"] += 1
            if is_member:
                totals["guild_members"] += 1
            if verified_f:
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
                verified=verified_f,
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
                f"{flag(verified_f)}      | {flag(is_newcomer)}       | {flag(is_member)}     | {flag(gate_ready)}"
            )

        # End audit block
        _audit("snapshot_summary", None, **totals)
        _audit("snapshot_end", None, guild_id=guild.id)

        # Human-friendly slice label
        slice_label = "verified" if filter_mode is True else "unverified" if filter_mode is False else "all members"

        # Send audit log confirmation + totals
        await ctx.send(
            "📘 Snapshot written to `guild_audit.log` "
            f"(slice: **{slice_label}**, sort: **{sort_label}**, autofixed: **{changed}**).\n"
            f"Total in slice: {totals['members_total']} | "
            f"Newcomers: {totals['newcomers']} | "
            f"Guild Members: {totals['guild_members']} | "
            f"Verified flag: {totals['verified_true']} | "
            f"Gate ready (not promoted): {totals['gate_ready_not_promoted']}"
        )

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


# =========================
# STARTUP RECONCILIATION (track-aware)
# =========================
async def retro_verify_existing_members(guild: discord.Guild) -> None:
    """
    Reconcile DB flags with actual roles at startup, honoring track:
      - If user has target role (Guild Member or Visitor) but DB not verified -> set verified + flags.
      - If DB says verified but target role missing -> add it.
      - Remove Newcomer from verified users.
      - Infer track from roles if missing.
    """
    try:
        member_role  = discord.utils.get(guild.roles, name=MEMBER_ROLE)
        visitor_role = discord.utils.get(guild.roles, name=VISITOR_ROLE)
        newcomer_role = discord.utils.get(guild.roles, name=NEWCOMER_ROLE)

        if not member_role and not visitor_role:
            logging.warning(f"[RETROVERIFY] Missing one/both roles: '{MEMBER_ROLE}', '{VISITOR_ROLE}'")

        total_checked = 0
        total_updated_db = 0
        total_added_role = 0
        total_removed_newcomer = 0

        for m in guild.members:
            total_checked += 1

            uid = str(m.id)
            rec = verified_users.get(uid, {}) or {}

            has_member   = member_role in m.roles if member_role else False
            has_visitor  = visitor_role in m.roles if visitor_role else False
            has_newcomer = newcomer_role in m.roles if newcomer_role else False
            has_class    = any(r.name in CLASS_ROLES for r in m.roles)

            # Infer track if missing
            track = rec.get("track")
            if track not in VALID_TRACKS:
                track = "member" if has_member else "visitor" if has_visitor else DEFAULT_TRACK
                rec["track"] = track

            target_role = member_role if track == "member" else visitor_role
            has_target  = (target_role in m.roles) if target_role else False

            # A) Has target role but DB not verified -> mark verified and set flags
            if has_target and not rec.get("verified", False):
                rec["verified"] = True
                rec["rules_accepted"] = True
                rec["nickname_confirmed"] = True
                if has_class:
                    rec["class_assigned"] = True
                verified_users[uid] = rec
                save_verified()
                total_updated_db += 1

                if has_newcomer and newcomer_role:
                    try:
                        await m.remove_roles(newcomer_role, reason="Retro-verify: already target role")
                        total_removed_newcomer += 1
                    except Exception as e:
                        logging.error(f"[RETROVERIFY] remove newcomer: {e}")

                try:
                    audit("retro_verify_member", m, track=track, ensured_role=True, roles=[r.name for r in m.roles])
                except Exception:
                    pass
                continue

            # B) DB says verified but missing target role -> add role
            if rec.get("verified", False) and not has_target and target_role:
                try:
                    await m.add_roles(target_role, reason="Retro-verify: verified but missing target role")
                    total_added_role += 1
                except Exception as e:
                    logging.error(f"[RETROVERIFY] add target role: {e}")

                if has_newcomer and newcomer_role:
                    try:
                        await m.remove_roles(newcomer_role, reason="Retro-verify: verified user cleanup")
                        total_removed_newcomer += 1
                    except Exception as e:
                        logging.error(f"[RETROVERIFY] remove newcomer: {e}")

                try:
                    audit("retro_verify_promote_role", m, track=track, ensured_role=True, roles=[r.name for r in m.roles])
                except Exception:
                    pass
                continue

            # C) Clean up stray Newcomer for verified users
            if rec.get("verified", False) and has_newcomer and newcomer_role:
                try:
                    await m.remove_roles(newcomer_role, reason="Retro-verify: cleanup for verified user")
                    total_removed_newcomer += 1
                    try:
                        audit("retro_verify_cleanup_newcomer", m, removed_newcomer=True)
                    except Exception:
                        pass
                except Exception as e:
                    logging.error(f"[RETROVERIFY] Failed removing stray Newcomer from {m}: {e}")

        try:
            audit(
                "retro_verify_summary",
                None,
                guild_id=guild.id,
                guild_name=guild.name,
                total_checked=total_checked,
                total_updated_db=total_updated_db,
                total_added_role=total_added_role,
                total_removed_newcomer=total_removed_newcomer
            )
        except Exception:
            pass

        logging.info(
            f"[RETROVERIFY] Guild '{guild.name}' ({guild.id}) "
            f"checked={total_checked} updated_db={total_updated_db} "
            f"added_role={total_added_role} removed_newcomer={total_removed_newcomer}"
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
    """Re-post onboarding UI for a user."""
    try:
        member = member or ctx.author
        await send_onboarding_embed(member)
        await ctx.send(f"{member.mention} please complete onboarding above.")
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
async def verified(ctx, mode: str = "list"):
    """
    Show verified users.
    Usage:
      !verified                -> pretty list (paginated)
      !verified count          -> just a count
      !verified file           -> CSV export
      !verified visitors       -> only track=visitor
      !verified members        -> only track=member
    """
    # Gather rows from DB -> live guild members
    guild = ctx.guild
    rows = []
    for uid, rec in verified_users.items():
        if not rec or not rec.get("verified"):
            continue
        m = guild.get_member(int(uid))
        if not m:
            continue  # not in this guild anymore
        track = rec.get("track", DEFAULT_TRACK)
        joined = getattr(m, "joined_at", None)
        rows.append({
            "id": uid,
            "name": getattr(m, "name", str(uid)),
            "display": m.display_name,
            "track": track if track in VALID_TRACKS else DEFAULT_TRACK,
            "joined": joined.isoformat(timespec="seconds") if joined else "",
        })

    if mode.lower() == "count":
        await ctx.send(f"✅ Verified users in **{guild.name}**: **{len(rows)}**")
        return

    # Optional filtering by track
    filt = mode.lower()
    if filt in {"members", "member"}:
        rows = [r for r in rows if r["track"] == "member"]
    elif filt in {"visitors", "visitor"}:
        rows = [r for r in rows if r["track"] == "visitor"]
    elif filt in {"file", "csv"}:
        import io, csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["User ID", "Username", "Display Name", "Track", "Joined"])
        for r in rows:
            w.writerow([r["id"], r["name"], r["display"], r["track"], r["joined"]])
        data = io.BytesIO(buf.getvalue().encode("utf-8"))
        await ctx.send(file=discord.File(fp=data, filename="verified_users.csv"))
        return
    # else: pretty list

    # Sort by joined date then name (joined may be empty)
    rows.sort(key=lambda r: (r["joined"] == "", r["joined"], r["display"].lower()))

    # Build a compact monospace table (Name | Track | Joined | ID tail)
    def tail(s, n=6): 
        return str(s)[-n:]

    header = f"{'Display Name':<24} {'Track':<8} {'Joined (UTC)':<19} ID"
    sep = "-" * len(header)

    # paginate safely under 2000 chars
    out = ["```\n" + header + "\n" + sep]
    for r in rows:
        joined_short = (r["joined"].replace('T',' ')[:19]) if r["joined"] else "-"
        line = f"{r['display'][:24]:<24} {r['track']:<8} {joined_short:<19} {tail(r['id'])}"
        # send a chunk if adding this line would exceed limit
        if sum(len(x) for x in out) + len(line) + 5 > 1990:
            out[-1] += "\n```"
            await ctx.send(out[-1])
            out = ["```\n" + header + "\n" + sep]
        out[-1] += "\n" + line

    out[-1] += "\n```"
    await ctx.send(out[-1] if rows else "ℹ️ No verified users found.")

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
    import asyncio
    try:
        class_members = {cls: [] for cls in CLASS_ROLES}
        all_members_combined = {cls: [] for cls in CLASS_ROLES}
        is_alt_flags = {}

        # Build class/member maps from your alts_data
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

        # Add members that have class roles but aren't in alts_data
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

        # Text summary (kept lightweight per message)
        summary_lines = ["**Vindicated's Class Composition**"]
        for cls in sorted(class_members):
            members = sorted(class_members[cls], key=lambda x: x.lower())
            if members:
                summary_lines.append(f"\n**{cls}** ({len(members)}):\n" + ", ".join(members))
        for part in summary_lines:
            await ctx.send(part)

        # Bar chart data
        labels = [cls for cls in CLASS_ROLES if len(all_members_combined[cls]) > 0]
        mains_count = [len([m for m in all_members_combined[cls] if not is_alt_flags.get(m, False)]) for cls in labels]
        alts_count  = [len([m for m in all_members_combined[cls] if     is_alt_flags.get(m, False)]) for cls in labels]

        if not labels:
            return

        # Helper that builds the PNG in a background thread
        def _build_class_plot_png(labels, mains_count, alts_count):
            import io
            import matplotlib
            matplotlib.use("Agg")  # headless backend
            import matplotlib.pyplot as plt

            x = range(len(labels))
            plt.figure(figsize=(8, 6))
            # Colors optional; keep if you like, or omit for defaults
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
            return buffer

        # Offload plotting + PNG save so we don't block the event loop/heartbeat
        buffer = await asyncio.to_thread(_build_class_plot_png, labels, mains_count, alts_count)

        file = File(fp=buffer, filename="full_class_composition.png")
        await ctx.send(file=file)

    except Exception as e:
        logging.error(f"[ERROR] classstats: {e}", exc_info=True)
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
