import os
import json
import random
import asyncio
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask, render_template, session, redirect, url_for, request as flask_request, jsonify
from flask_session import Session
import requests


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

DATA_FILE = "bfc_data.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")

DASHBOARD_URL = os.getenv(
    "DASHBOARD_URL",
    "http://localhost:10000"
)

OAUTH_REDIRECT_URI = os.getenv(
    "OAUTH_REDIRECT_URI",
    "http://localhost:10000/callback"
)

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "your-secret-key"
)

DISCORD_API_BASE = "https://discord.com/api/v10"


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from .env"
    )

if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
    print(
        "⚠️ Warning: DISCORD_CLIENT_ID or "
        "DISCORD_CLIENT_SECRET is not set. "
        "Dashboard OAuth will not work."
    )


# =========================================================
# FLASK WEB SERVER
# =========================================================

SCRIPT_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

TEMPLATES_DIR = os.path.join(
    SCRIPT_DIR,
    "templates"
)

STATIC_DIR = os.path.join(
    SCRIPT_DIR,
    "static"
)


app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR,
    static_url_path="/static"
)

app.config["SECRET_KEY"] = SESSION_SECRET

app.config["SESSION_TYPE"] = "filesystem"

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    days=7
)

Session(app)


# Global reference used by Flask
bot_instance = None


# =========================================================
# DISCORD INTENTS
# =========================================================

intents = discord.Intents.default()

intents.members = True
intents.message_content = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# =========================================================
# DATA
# =========================================================

DEFAULT_GUILD_DATA = {
    "warnings": {},
    "profiles": {},
    "bounties": {},
    "giveaways": {}
}


DEFAULT_DATA = {
    "guilds": {}
}


def load_data():
    """
    Load BFC data.

    New format:

    {
        "guilds": {
            "GUILD_ID": {
                "warnings": {},
                "profiles": {},
                "bounties": {},
                "giveaways": {}
            }
        }
    }

    Older global data is automatically migrated
    into GUILD_ID when possible.
    """

    if not os.path.exists(DATA_FILE):

        save_data(DEFAULT_DATA)

        return {
            "guilds": {}
        }

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            loaded = json.load(f)

        # -------------------------------------------------
        # New format
        # -------------------------------------------------

        if isinstance(
            loaded,
            dict
        ) and isinstance(
            loaded.get("guilds"),
            dict
        ):

            for guild_id, guild_data in loaded["guilds"].items():

                if not isinstance(
                    guild_data,
                    dict
                ):
                    loaded["guilds"][guild_id] = {}

                for key in DEFAULT_GUILD_DATA:

                    if key not in loaded["guilds"][guild_id]:

                        loaded["guilds"][guild_id][key] = {}

            return loaded

        # -------------------------------------------------
        # Old format migration
        # -------------------------------------------------

        migrated = {
            "guilds": {}
        }

        if GUILD_ID:

            migrated["guilds"][str(GUILD_ID)] = {
                "warnings": loaded.get(
                    "warnings",
                    {}
                ),
                "profiles": loaded.get(
                    "profiles",
                    {}
                ),
                "bounties": loaded.get(
                    "bounties",
                    {}
                ),
                "giveaways": loaded.get(
                    "giveaways",
                    {}
                )
            }

        save_data(migrated)

        print(
            "✅ Migrated old BFC data format."
        )

        return migrated

    except Exception as error:

        print(
            f"⚠️ Data loading error: {error}"
        )

        return {
            "guilds": {}
        }


def save_data(data_to_save=None):

    if data_to_save is None:
        data_to_save = data

    try:

        with open(
            DATA_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data_to_save,
                f,
                indent=4
            )

    except Exception as error:

        print(
            f"❌ Data saving error: {error}"
        )


data = load_data()


def get_guild_data(guild_or_id):
    """
    Get data belonging only to one Discord server.
    """

    if hasattr(
        guild_or_id,
        "id"
    ):

        guild_id = str(
            guild_or_id.id
        )

    else:

        guild_id = str(
            guild_or_id
        )

    if not guild_id:

        guild_id = "0"

    if "guilds" not in data:

        data["guilds"] = {}

    if guild_id not in data["guilds"]:

        data["guilds"][guild_id] = {
            "warnings": {},
            "profiles": {},
            "bounties": {},
            "giveaways": {}
        }

    guild_data = data["guilds"][guild_id]

    for key in DEFAULT_GUILD_DATA:

        if key not in guild_data:

            guild_data[key] = {}

    return guild_data


def get_warnings(guild, user_id):
    """
    Get warnings for a user in a specific server.
    """

    guild_data = get_guild_data(guild)

    key = str(user_id)

    if key not in guild_data["warnings"]:

        guild_data["warnings"][key] = []

    return guild_data["warnings"][key]


# =========================================================
# HELPERS
# =========================================================

def make_embed(
    title,
    description="",
    color=discord.Color.blurple()
):

    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(
            timezone.utc
        )
    )


def parse_color(value):

    value = value.strip().replace(
        "#",
        ""
    )

    try:

        return discord.Color(
            int(value, 16)
        )

    except Exception:

        return discord.Color.blurple()


def is_staff(member):

    permissions = member.guild_permissions

    return (
        permissions.administrator
        or permissions.manage_guild
        or permissions.moderate_members
        or permissions.manage_messages
    )


def staff_check(interaction):

    if not isinstance(
        interaction.user,
        discord.Member
    ):

        return False

    return is_staff(
        interaction.user
    )


def user_key(user):

    return str(
        user.id
    )


def escape_html(text):

    if not isinstance(
        text,
        str
    ):

        text = str(text)

    return (
        text
        .replace(
            "&",
            "&amp;"
        )
        .replace(
            "<",
            "&lt;"
        )
        .replace(
            ">",
            "&gt;"
        )
        .replace(
            '"',
            "&quot;"
        )
        .replace(
            "'",
            "&#x27;"
        )
    )


# =========================================================
# DISCORD OAUTH
# =========================================================

def get_discord_oauth_url():

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds"
    }

    return (
        "https://discord.com/api/oauth2/authorize?"
        + urlencode(params)
    )


def exchange_code_for_token(code):

    payload = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI
    }

    headers = {
        "Content-Type":
        "application/x-www-form-urlencoded"
    }

    try:

        response = requests.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data=payload,
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:

            return response.json()

        print(
            "OAuth token error:",
            response.status_code,
            response.text
        )

    except Exception as error:

        print(
            f"OAuth token request error: {error}"
        )

    return None


def get_discord_user(access_token):

    headers = {
        "Authorization":
        f"Bearer {access_token}"
    }

    try:

        response = requests.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:

            return response.json()

    except Exception as error:

        print(
            f"Discord user request error: {error}"
        )

    return None


def get_discord_guilds(access_token):

    headers = {
        "Authorization":
        f"Bearer {access_token}"
    }

    try:

        response = requests.get(
            f"{DISCORD_API_BASE}/users/@me/guilds",
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:

            return response.json()

    except Exception as error:

        print(
            f"Discord guild request error: {error}"
        )

    return []


def can_manage_guild(guild_data):
    """
    Discord OAuth guild permissions are returned
    as a string, so convert them to an integer first.
    """

    try:

        permissions = int(
            guild_data.get(
                "permissions",
                0
            )
        )

    except (
        TypeError,
        ValueError
    ):

        permissions = 0

    return (
        bool(permissions & 8)
        or bool(permissions & 32)
        or bool(
            guild_data.get(
                "owner",
                False
            )
        )
    )


def bot_is_in_guild(guild_id):

    global bot_instance

    if not bot_instance:

        return False

    try:

        guild_id_int = int(
            guild_id
        )

    except (
        TypeError,
        ValueError
    ):

        return False

    return (
        bot_instance.get_guild(
            guild_id_int
        ) is not None
    )


def get_manageable_bot_guilds(
    access_token
):
    """
    Return only servers where:

    1. The user can manage the server.
    2. BFC Bot is actually installed.
    """

    guilds = get_discord_guilds(
        access_token
    )

    result = []

    for guild_data in guilds:

        guild_id = guild_data.get(
            "id"
        )

        if not guild_id:
            continue

        if not can_manage_guild(
            guild_data
        ):
            continue

        if not bot_is_in_guild(
            guild_id
        ):
            continue

        result.append(
            guild_data
        )

    return result


# =========================================================
# DASHBOARD ACCESS
# =========================================================

def refresh_manageable_guilds():

    access_token = session.get(
        "access_token"
    )

    if not access_token:

        session["manageable_guilds"] = []

        return []

    manageable_guilds = (
        get_manageable_bot_guilds(
            access_token
        )
    )

    session["manageable_guilds"] = (
        manageable_guilds
    )

    return manageable_guilds


def get_requested_guild_id():

    """
    Get guild ID from:
    - URL query
    - POST form
    - Flask route parameters
    """

    guild_id = (
        flask_request.args.get(
            "guild_id"
        )
        or flask_request.form.get(
            "guild_id"
        )
    )

    if not guild_id:

        guild_id = (
            flask_request.view_args or {}
        ).get(
            "guild_id"
        )

    if not guild_id:

        return None

    try:

        return str(
            int(guild_id)
        )

    except (
        TypeError,
        ValueError
    ):

        return None


def get_allowed_guild(guild_id):

    if not guild_id:

        return None

    manageable_guilds = (
        session.get(
            "manageable_guilds",
            []
        )
    )

    for guild_data in manageable_guilds:

        if str(
            guild_data.get("id")
        ) == str(guild_id):

            return guild_data

    return None


def require_login(f):

    @wraps(f)
    def decorated_function(
        *args,
        **kwargs
    ):

        if "user" not in session:

            return redirect(
                url_for("landing")
            )

        return f(
            *args,
            **kwargs
        )

    return decorated_function


def require_guild_access(f):

    @wraps(f)
    def decorated_function(
        *args,
        **kwargs
    ):

        if "user" not in session:

            return redirect(
                url_for("landing")
            )

        guild_id = (
            get_requested_guild_id()
        )

        if not guild_id:

            return redirect(
                url_for("dashboard")
            )

        # ---------------------------------------------
        # Refresh permissions from Discord.
        # This prevents stale sessions.
        # ---------------------------------------------

        manageable_guilds = (
            refresh_manageable_guilds()
        )

        allowed_guild = None

        for guild_data in manageable_guilds:

            if str(
                guild_data.get("id")
            ) == str(guild_id):

                allowed_guild = guild_data
                break

        if not allowed_guild:

            return redirect(
                url_for("dashboard")
            )

        # ---------------------------------------------
        # Make sure BFC Bot is actually in this server.
        # ---------------------------------------------

        if not bot_is_in_guild(
            guild_id
        ):

            return redirect(
                url_for("dashboard")
            )

        return f(
            *args,
            **kwargs
        )

    return decorated_function


# =========================================================
# DASHBOARD ROUTES
# =========================================================

@app.route("/")
def landing():

    if "user" in session:

        return redirect(
            url_for("dashboard")
        )

    login_url = (
        get_discord_oauth_url()
    )

    return render_template(
        "landing.html",
        login_url=login_url
    )


@app.route("/login")
def login():

    if (
        not DISCORD_CLIENT_ID
        or not DISCORD_CLIENT_SECRET
    ):

        return (
            "OAuth not configured",
            500
        )

    return redirect(
        get_discord_oauth_url()
    )


@app.route("/callback")
def callback():

    code = flask_request.args.get(
        "code"
    )

    if not code:

        return redirect(
            url_for("landing")
        )

    token_data = (
        exchange_code_for_token(
            code
        )
    )

    if not token_data:

        return redirect(
            url_for("landing")
        )

    access_token = (
        token_data.get(
            "access_token"
        )
    )

    if not access_token:

        return redirect(
            url_for("landing")
        )

    user_data = (
        get_discord_user(
            access_token
        )
    )

    if not user_data:

        return redirect(
            url_for("landing")
        )

    # -----------------------------------------------------
    # Save OAuth token SERVER-SIDE in Flask-Session.
    # It is not placed in the URL or page.
    # -----------------------------------------------------

    session.permanent = True

    session["access_token"] = (
        access_token
    )

    session["user"] = {
        "id": user_data.get(
            "id"
        ),
        "username": user_data.get(
            "username"
        ),
        "avatar": user_data.get(
            "avatar"
        )
    }

    # -----------------------------------------------------
    # Only show manageable servers where the bot exists.
    # -----------------------------------------------------

    manageable_guilds = (
        get_manageable_bot_guilds(
            access_token
        )
    )

    session["manageable_guilds"] = (
        manageable_guilds
    )

    return redirect(
        url_for("dashboard")
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("landing")
    )


# =========================================================
# DASHBOARD PAGE HELPER
# =========================================================

def render_guild_page(page):

    guild_id = (
        get_requested_guild_id()
    )

    if not guild_id:

        return redirect(
            url_for("dashboard")
        )

    manageable_guilds = (
        refresh_manageable_guilds()
    )

    current_guild = next(
        (
            guild
            for guild in manageable_guilds
            if str(
                guild.get("id")
            ) == str(guild_id)
        ),
        None
    )

    if not current_guild:

        return redirect(
            url_for("dashboard")
        )

    user = session.get(
        "user",
        {}
    )

    return render_template(
        "dashboard.html",
        manageable_guilds=manageable_guilds,
        current_guild=current_guild,
        user=user,
        page=page
    )


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
@require_login
def dashboard():

    manageable_guilds = (
        refresh_manageable_guilds()
    )

    user = session.get(
        "user",
        {}
    )

    return render_template(
        "dashboard.html",
        manageable_guilds=manageable_guilds,
        user=user,
        page="overview"
    )


@app.route("/dashboard/overview")
@require_login
@require_guild_access
def dashboard_overview():

    return render_guild_page(
        "overview"
    )


@app.route("/dashboard/messages")
@require_login
@require_guild_access
def dashboard_messages():

    return render_guild_page(
        "messages"
    )


@app.route("/dashboard/moderation")
@require_login
@require_guild_access
def dashboard_moderation():

    return render_guild_page(
        "moderation"
    )


@app.route("/dashboard/giveaways")
@require_login
@require_guild_access
def dashboard_giveaways():

    return render_guild_page(
        "giveaways"
    )


@app.route("/dashboard/bfc")
@require_login
@require_guild_access
def dashboard_bfc():

    return render_guild_page(
        "bfc"
    )


@app.route("/dashboard/server")
@require_login
@require_guild_access
def dashboard_server():

    return render_guild_page(
        "server"
    )


# =========================================================
# API
# =========================================================

@app.route("/api/status")
@require_login
def api_status():

    global bot_instance

    if not bot_instance:

        return jsonify({
            "error":
            "Bot not ready"
        }), 503

    return jsonify({
        "online":
            bot_instance.user is not None,

        "latency":
            round(
                bot_instance.latency * 1000
            ),

        "guilds":
            len(
                bot_instance.guilds
            ),

        "commands":
            len(
                bot_instance.tree._get_all_commands()
            )
    })


@app.route(
    "/api/guild/<guild_id>/info",
    methods=["GET"]
)
@require_login
@require_guild_access
def api_guild_info(guild_id):

    global bot_instance

    if not bot_instance:

        return jsonify({
            "error":
            "Bot not ready"
        }), 503

    try:

        guild = bot_instance.get_guild(
            int(guild_id)
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error":
            "Invalid guild ID"
        }), 400

    if not guild:

        return jsonify({
            "error":
            "Guild not found"
        }), 404

    return jsonify({

        "id":
            str(guild.id),

        "name":
            escape_html(
                guild.name
            ),

        "icon":
            guild.icon.url
            if guild.icon
            else None,

        "member_count":
            guild.member_count,

        "channel_count":
            len(
                guild.channels
            ),

        "role_count":
            len(
                guild.roles
            ),

        "owner_id":
            str(guild.owner_id)
            if guild.owner_id
            else None,

        "owner":
            escape_html(
                guild.owner.display_name
            )
            if guild.owner
            else None
    })


@app.route(
    "/api/guild/<guild_id>/channels",
    methods=["GET"]
)
@require_login
@require_guild_access
def api_guild_channels(guild_id):

    global bot_instance

    if not bot_instance:

        return jsonify({
            "error":
            "Bot not ready"
        }), 503

    try:

        guild = bot_instance.get_guild(
            int(guild_id)
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error":
            "Invalid guild ID"
        }), 400

    if not guild:

        return jsonify({
            "error":
            "Guild not found"
        }), 404

    channels = []

    for channel in guild.text_channels:

        channels.append({
            "id":
                str(channel.id),

            "name":
                escape_html(
                    channel.name
                )
        })

    return jsonify({
        "channels":
            channels
    })


@app.route(
    "/api/guild/<guild_id>/members",
    methods=["GET"]
)
@require_login
@require_guild_access
def api_guild_members(guild_id):

    global bot_instance

    if not bot_instance:

        return jsonify({
            "error":
            "Bot not ready"
        }), 503

    try:

        guild = bot_instance.get_guild(
            int(guild_id)
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error":
            "Invalid guild ID"
        }), 400

    if not guild:

        return jsonify({
            "error":
            "Guild not found"
        }), 404

    members = []

    for member in list(
        guild.members
    )[:50]:

        members.append({

            "id":
                str(member.id),

            "name":
                escape_html(
                    member.display_name
                ),

            "avatar":
                member.display_avatar.url
        })

    return jsonify({
        "members":
            members
    })


@app.route(
    "/api/guild/<guild_id>/data",
    methods=["GET"]
)
@require_login
@require_guild_access
def api_guild_data(guild_id):

    guild_data = get_guild_data(
        guild_id
    )

    warnings = guild_data.get(
        "warnings",
        {}
    )

    profiles = guild_data.get(
        "profiles",
        {}
    )

    bounties = guild_data.get(
        "bounties",
        {}
    )

    giveaways = guild_data.get(
        "giveaways",
        {}
    )

    total_warning_entries = sum(
        len(
            warning_list
        )
        for warning_list
        in warnings.values()
        if isinstance(
            warning_list,
            list
        )
    )

    return jsonify({

        "warnings":
            len(warnings),

        "warning_users":
            len(warnings),

        "warning_entries":
            total_warning_entries,

        "profiles":
            len(profiles),

        "bounties":
            len(bounties),

        "giveaways":
            len(giveaways)
    })


@app.route("/health")
def health():

    return "OK"


# =========================================================
# WEB SERVER
# =========================================================

def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    global bot_instance

    bot_instance = bot

    print(
        "========================================"
    )

    print(
        f"Logged in as {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    print(
        f"Servers: {len(bot.guilds)}"
    )

    print(
        "========================================"
    )

    try:

        if GUILD_ID:

            guild = discord.Object(
                id=GUILD_ID
            )

            bot.tree.copy_global_to(
                guild=guild
            )

            synced = await bot.tree.sync(
                guild=guild
            )

            print(
                f"Synced {len(synced)} "
                f"commands to BFC server."
            )

        else:

            synced = await bot.tree.sync()

            print(
                f"Synced {len(synced)} "
                f"global commands."
            )

    except Exception as error:

        print(
            f"Slash command sync error: {error}"
        )

    await bot.change_presence(

        activity=discord.Activity(

            type=discord.ActivityType.watching,

            name="Blox Fruits Community"
        )
    )


# =========================================================
# PING
# =========================================================

@bot.tree.command(
    name="ping",
    description="Check the bot's latency."
)
async def ping(interaction):

    latency = round(
        bot.latency * 1000
    )

    embed = make_embed(
        "🏓 Pong!",
        f"Bot latency: **{latency}ms**"
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# HELP
# =========================================================

@bot.tree.command(
    name="help",
    description="Show all BFC Bot commands."
)
async def help_command(interaction):

    embed = make_embed(
        "⚡ BFC Bot Commands",
        "Everything available in the BFC Bot."
    )

    embed.add_field(
        name="🛠 Utility",
        value=(
            "`/ping`\n"
            "`/help`\n"
            "`/serverinfo`\n"
            "`/userinfo`\n"
            "`/avatar`"
        ),
        inline=True
    )

    embed.add_field(
        name="🔨 Moderation",
        value=(
            "`/ban`\n"
            "`/kick`\n"
            "`/timeout`\n"
            "`/warn`\n"
            "`/warnings`\n"
            "`/clear`\n"
            "`/lock`\n"
            "`/unlock`"
        ),
        inline=True
    )

    embed.add_field(
        name="📢 Server Tools",
        value=(
            "`/embed`\n"
            "`/announce`\n"
            "`/say`"
        ),
        inline=True
    )

    embed.add_field(
        name="🎉 Giveaways",
        value="`/giveaway`",
        inline=True
    )

    embed.add_field(
        name="🏴‍☠️ BFC",
        value=(
            "`/profile`\n"
            "`/setprofile`\n"
            "`/bounty`\n"
            "`/verify`"
        ),
        inline=True
    )

    embed.set_footer(
        text="Blox Fruits Community • BFC Bot"
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# SERVER INFO
# =========================================================

@bot.tree.command(
    name="serverinfo",
    description="Show information about the server."
)
async def serverinfo(interaction):

    guild = interaction.guild

    if not guild:

        return await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )

    embed = make_embed(
        f"🏴‍☠️ {guild.name}",
        f"Server ID: `{guild.id}`"
    )

    embed.add_field(
        name="👥 Members",
        value=str(
            guild.member_count
        ),
        inline=True
    )

    embed.add_field(
        name="💬 Channels",
        value=str(
            len(guild.channels)
        ),
        inline=True
    )

    embed.add_field(
        name="🎭 Roles",
        value=str(
            len(guild.roles)
        ),
        inline=True
    )

    if guild.owner:

        embed.add_field(
            name="👑 Owner",
            value=guild.owner.mention,
            inline=True
        )

    if guild.icon:

        embed.set_thumbnail(
            url=guild.icon.url
        )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# USER INFO
# =========================================================

@bot.tree.command(
    name="userinfo",
    description="Show information about a member."
)
@app_commands.describe(
    member="Member to inspect"
)
async def userinfo(
    interaction,
    member: discord.Member = None
):

    member = (
        member
        or interaction.user
    )

    embed = make_embed(
        f"👤 {member.display_name}",
        f"Username: `{member}`"
    )

    embed.add_field(
        name="🆔 ID",
        value=str(
            member.id
        ),
        inline=False
    )

    embed.add_field(
        name="📅 Account Created",
        value=discord.utils.format_dt(
            member.created_at,
            "F"
        ),
        inline=False
    )

    if member.joined_at:

        embed.add_field(
            name="📥 Joined Server",
            value=discord.utils.format_dt(
                member.joined_at,
                "F"
            ),
            inline=False
        )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# AVATAR
# =========================================================

@bot.tree.command(
    name="avatar",
    description="Show a member's avatar."
)
@app_commands.describe(
    member="Member whose avatar you want"
)
async def avatar(
    interaction,
    member: discord.Member = None
):

    member = (
        member
        or interaction.user
    )

    embed = make_embed(
        "🖼️ Avatar",
        f"{member.mention}'s avatar"
    )

    embed.set_image(
        url=member.display_avatar.url
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# BAN
# =========================================================

@bot.tree.command(
    name="ban",
    description="Ban a member."
)
@app_commands.describe(
    member="Member to ban",
    reason="Reason for the ban"
)
async def ban(
    interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    try:

        await member.ban(
            reason=reason
        )

        embed = make_embed(
            "🔨 Member Banned",
            f"{member.mention} has been banned."
        )

        embed.add_field(
            name="Reason",
            value=reason
        )

        await interaction.response.send_message(
            embed=embed
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "❌ I can't ban this member.",
            ephemeral=True
        )


# =========================================================
# KICK
# =========================================================

@bot.tree.command(
    name="kick",
    description="Kick a member."
)
@app_commands.describe(
    member="Member to kick",
    reason="Reason for the kick"
)
async def kick(
    interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    try:

        await member.kick(
            reason=reason
        )

        embed = make_embed(
            "👢 Member Kicked",
            f"{member.mention} has been kicked."
        )

        embed.add_field(
            name="Reason",
            value=reason
        )

        await interaction.response.send_message(
            embed=embed
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "❌ I can't kick this member.",
            ephemeral=True
        )


# =========================================================
# TIMEOUT
# =========================================================

@bot.tree.command(
    name="timeout",
    description="Timeout a member."
)
@app_commands.describe(
    member="Member to timeout",
    minutes="Timeout duration in minutes",
    reason="Reason"
)
async def timeout(
    interaction,
    member: discord.Member,
    minutes: int,
    reason: str = "No reason provided"
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    if minutes < 1 or minutes > 40320:

        return await interaction.response.send_message(
            "❌ Timeout must be between 1 minute and 28 days.",
            ephemeral=True
        )

    try:

        until = (
            discord.utils.utcnow()
            + timedelta(
                minutes=minutes
            )
        )

        await member.timeout(
            until,
            reason=reason
        )

        await interaction.response.send_message(
            f"⏳ {member.mention} was timed out "
            f"for **{minutes} minutes**."
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "❌ I can't timeout this member.",
            ephemeral=True
        )


# =========================================================
# WARN
# =========================================================

@bot.tree.command(
    name="warn",
    description="Warn a member."
)
@app_commands.describe(
    member="Member to warn",
    reason="Warning reason"
)
async def warn(
    interaction,
    member: discord.Member,
    reason: str
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    warnings = get_warnings(
        interaction.guild,
        member.id
    )

    warnings.append({

        "reason":
            reason,

        "moderator":
            interaction.user.id,

        "time":
            datetime.now(
                timezone.utc
            ).isoformat()
    })

    save_data()

    embed = make_embed(
        "⚠️ Warning Issued",
        f"{member.mention} has received a warning."
    )

    embed.add_field(
        name="Reason",
        value=reason
    )

    embed.add_field(
        name="Total Warnings",
        value=str(
            len(warnings)
        )
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# WARNINGS
# =========================================================

@bot.tree.command(
    name="warnings",
    description="View a member's warnings."
)
@app_commands.describe(
    member="Member to inspect"
)
async def warnings(
    interaction,
    member: discord.Member
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    user_warnings = get_warnings(
        interaction.guild,
        member.id
    )

    embed = make_embed(
        f"⚠️ Warnings — {member}",
        f"Total warnings: **{len(user_warnings)}**"
    )

    if not user_warnings:

        embed.description = (
            "This member has no warnings."
        )

    else:

        for index, warning_data in enumerate(
            user_warnings[-10:],
            start=1
        ):

            embed.add_field(
                name=f"Warning #{index}",
                value=warning_data["reason"],
                inline=False
            )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# =========================================================
# CLEAR
# =========================================================

@bot.tree.command(
    name="clear",
    description="Delete messages from a channel."
)
@app_commands.describe(
    amount="Number of messages to delete"
)
async def clear(
    interaction,
    amount: int
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    if amount < 1 or amount > 100:

        return await interaction.response.send_message(
            "❌ Amount must be between 1 and 100.",
            ephemeral=True
        )

    await interaction.response.defer(
        ephemeral=True
    )

    deleted = await interaction.channel.purge(
        limit=amount
    )

    await interaction.followup.send(
        f"🧹 Deleted **{len(deleted)}** messages.",
        ephemeral=True
    )


# =========================================================
# LOCK
# =========================================================

@bot.tree.command(
    name="lock",
    description="Lock the current channel."
)
async def lock(interaction):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    channel = interaction.channel

    overwrite = channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = False

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔒 This channel has been locked."
    )


# =========================================================
# UNLOCK
# =========================================================

@bot.tree.command(
    name="unlock",
    description="Unlock the current channel."
)
async def unlock(interaction):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    channel = interaction.channel

    overwrite = channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = None

    await channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔓 This channel has been unlocked."
    )


# =========================================================
# EMBED
# =========================================================

@bot.tree.command(
    name="embed",
    description="Create a custom embed."
)
@app_commands.describe(
    title="Embed title",
    description="Embed description",
    color="Hex color, example: 5865F2",
    image="Upload the main image",
    thumbnail="Upload a thumbnail",
    footer="Footer text",
    author="Author text",
    channel="Channel where the embed will be sent"
)
async def embed_command(
    interaction,
    title: str,
    description: str,
    color: str = "5865F2",
    image: discord.Attachment = None,
    thumbnail: discord.Attachment = None,
    footer: str = None,
    author: str = None,
    channel: discord.TextChannel = None
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission to use `/embed`.",
            ephemeral=True
        )

    embed = discord.Embed(
        title=title,
        description=description,
        color=parse_color(
            color
        ),
        timestamp=datetime.now(
            timezone.utc
        )
    )

    if image:

        embed.set_image(
            url=image.url
        )

    if thumbnail:

        embed.set_thumbnail(
            url=thumbnail.url
        )

    if footer:

        embed.set_footer(
            text=footer
        )

    if author:

        embed.set_author(
            name=author
        )

    target_channel = (
        channel
        or interaction.channel
    )

    try:

        await target_channel.send(
            embed=embed
        )

        await interaction.response.send_message(
            f"✅ Embed sent to "
            f"{target_channel.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "❌ I don't have permission to send messages there.",
            ephemeral=True
        )


# =========================================================
# ANNOUNCE
# =========================================================

@bot.tree.command(
    name="announce",
    description="Send an announcement embed."
)
@app_commands.describe(
    title="Announcement title",
    message="Announcement message",
    channel="Channel to send it to"
)
async def announce(
    interaction,
    title: str,
    message: str,
    channel: discord.TextChannel = None
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    target = (
        channel
        or interaction.channel
    )

    embed = make_embed(
        f"📢 {title}",
        message
    )

    embed.set_footer(
        text=f"Announcement by {interaction.user}"
    )

    await target.send(
        embed=embed
    )

    await interaction.response.send_message(
        f"✅ Announcement sent to "
        f"{target.mention}.",
        ephemeral=True
    )


# =========================================================
# SAY
# =========================================================

@bot.tree.command(
    name="say",
    description="Make the bot say something."
)
@app_commands.describe(
    message="Message to send",
    channel="Channel to send it to"
)
async def say(
    interaction,
    message: str,
    channel: discord.TextChannel = None
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    target = (
        channel
        or interaction.channel
    )

    await target.send(
        message
    )

    await interaction.response.send_message(
        "✅ Message sent.",
        ephemeral=True
    )


# =========================================================
# GIVEAWAY
# =========================================================

class GiveawayView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

        self.entries = set()

    @discord.ui.button(
        label="Enter Giveaway",
        emoji="🎉",
        style=discord.ButtonStyle.primary
    )
    async def enter(
        self,
        interaction,
        button
    ):

        user_id = interaction.user.id

        if user_id in self.entries:

            self.entries.remove(
                user_id
            )

            await interaction.response.send_message(
                "❌ You left the giveaway.",
                ephemeral=True
            )

        else:

            self.entries.add(
                user_id
            )

            await interaction.response.send_message(
                "🎉 You entered the giveaway!",
                ephemeral=True
            )


@bot.tree.command(
    name="giveaway",
    description="Start a giveaway."
)
@app_commands.describe(
    prize="Giveaway prize",
    duration="Duration in seconds",
    winners="Number of winners"
)
async def giveaway(
    interaction,
    prize: str,
    duration: int,
    winners: int = 1
):

    if not staff_check(interaction):

        return await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

    if duration < 10:

        return await interaction.response.send_message(
            "❌ Duration must be at least 10 seconds.",
            ephemeral=True
        )

    if winners < 1:

        return await interaction.response.send_message(
            "❌ Winners must be at least 1.",
            ephemeral=True
        )

    view = GiveawayView()

    embed = make_embed(
        "🎉 GIVEAWAY!",
        f"## {prize}\n\n"
        f"Click the button below to enter!\n\n"
        f"⏱️ Duration: **{duration} seconds**\n"
        f"🏆 Winners: **{winners}**"
    )

    embed.set_footer(
        text=f"Hosted by {interaction.user}"
    )

    await interaction.response.send_message(
        embed=embed,
        view=view
    )

    message = await interaction.original_response()

    await asyncio.sleep(
        duration
    )

    guild_data = get_guild_data(
        interaction.guild
    )

    giveaway_key = str(
        message.id
    )

    guild_data["giveaways"][
        giveaway_key
    ] = {
        "message_id":
            str(message.id),

        "channel_id":
            str(interaction.channel.id),

        "prize":
            prize,

        "winners":
            winners,

        "host":
            str(interaction.user.id),

        "ended":
            True,

        "ended_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    if not view.entries:

        end_embed = make_embed(
            "🎉 Giveaway Ended",
            f"Prize: **{prize}**\n\n"
            "Nobody entered the giveaway."
        )

        await message.edit(
            embed=end_embed,
            view=None
        )

        save_data()

        return

    entry_list = list(
        view.entries
    )

    random.shuffle(
        entry_list
    )

    selected = entry_list[
        :min(
            winners,
            len(entry_list)
        )
    ]

    mentions = []

    for user_id in selected:

        user = (
            interaction.guild.get_member(
                user_id
            )
        )

        if user:

            mentions.append(
                user.mention
            )

    end_embed = make_embed(
        "🎉 Giveaway Ended!",
        f"Prize: **{prize}**\n\n"
        f"Winner(s): "
        f"{', '.join(mentions) if mentions else 'Unknown'}"
    )

    await message.edit(
        embed=end_embed,
        view=None
    )

    save_data()


# =========================================================
# BFC PROFILE
# =========================================================

@bot.tree.command(
    name="profile",
    description="View a BFC player profile."
)
@app_commands.describe(
    member="Player to view"
)
async def profile(
    interaction,
    member: discord.Member = None
):

    member = (
        member
        or interaction.user
    )

    guild_data = get_guild_data(
        interaction.guild
    )

    key = user_key(
        member
    )

    profile_data = (
        guild_data["profiles"].get(
            key,
            {
                "fruit":
                    "Not set",

                "level":
                    "Not set",

                "main":
                    "Not set",

                "bio":
                    "No bio set."
            }
        )
    )

    embed = make_embed(
        f"🏴‍☠️ {member.display_name}'s BFC Profile",
        profile_data["bio"]
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="🍎 Fruit",
        value=profile_data["fruit"],
        inline=True
    )

    embed.add_field(
        name="⭐ Level",
        value=profile_data["level"],
        inline=True
    )

    embed.add_field(
        name="⚔️ Main",
        value=profile_data["main"],
        inline=True
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# SET PROFILE
# =========================================================

@bot.tree.command(
    name="setprofile",
    description="Set your BFC player profile."
)
@app_commands.describe(
    fruit="Your Blox Fruit",
    level="Your level",
    main="Your main",
    bio="Your profile bio"
)
async def setprofile(
    interaction,
    fruit: str,
    level: str,
    main: str,
    bio: str
):

    guild_data = get_guild_data(
        interaction.guild
    )

    key = user_key(
        interaction.user
    )

    guild_data["profiles"][key] = {

        "fruit":
            fruit,

        "level":
            level,

        "main":
            main,

        "bio":
            bio
    }

    save_data()

    await interaction.response.send_message(
        "✅ Your BFC profile has been updated!",
        ephemeral=True
    )


# =========================================================
# BOUNTY
# =========================================================

@bot.tree.command(
    name="bounty",
    description="View a player's BFC bounty."
)
@app_commands.describe(
    member="Player to check"
)
async def bounty(
    interaction,
    member: discord.Member = None
):

    member = (
        member
        or interaction.user
    )

    guild_data = get_guild_data(
        interaction.guild
    )

    amount = guild_data[
        "bounties"
    ].get(
        user_key(member),
        0
    )

    embed = make_embed(
        "💰 BFC Bounty",
        f"{member.mention}'s bounty"
    )

    embed.add_field(
        name="🏴‍☠️ Bounty",
        value=f"**{amount:,}**",
        inline=False
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# VERIFY
# =========================================================

@bot.tree.command(
    name="verify",
    description="Verify a Roblox username and get the Verified role."
)
@app_commands.describe(
    username="Your Roblox username"
)
async def verify(
    interaction,
    username: str
):

    await interaction.response.defer(
        ephemeral=True
    )

    try:

        encoded = urllib.parse.quote(
            username
        )

        url = (
            "https://users.roblox.com/v1/users/search"
            f"?keyword={encoded}&limit=10"
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "BFC-Bot"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:

            result = json.loads(
                response.read().decode()
            )

        users = result.get(
            "data",
            []
        )

        found = None

        for user in users:

            if user.get(
                "name",
                ""
            ).lower() == username.lower():

                found = user

                break

        if not found:

            return await interaction.followup.send(
                "❌ Roblox username not found.",
                ephemeral=True
            )

        role = discord.utils.get(
            interaction.guild.roles,
            name="Verified"
        )

        if not role:

            return await interaction.followup.send(
                "❌ The `Verified` role doesn't exist.",
                ephemeral=True
            )

        try:

            await interaction.user.add_roles(
                role,
                reason="BFC Roblox verification"
            )

        except discord.Forbidden:

            return await interaction.followup.send(
                "❌ I can't assign the Verified role. "
                "Make sure my bot role is above it.",
                ephemeral=True
            )

        embed = make_embed(
            "✅ Verification Successful",
            f"Roblox account: **{found['name']}**\n"
            f"Roblox ID: `{found['id']}`\n\n"
            f"{interaction.user.mention} has received "
            f"{role.mention}."
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True
        )

    except Exception as error:

        print(
            f"Verification error: {error}"
        )

        await interaction.followup.send(
            "❌ Something went wrong while checking Roblox.",
            ephemeral=True
        )


# =========================================================
# ERROR HANDLER
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    print(
        f"Command error: {error}"
    )

    try:

        if interaction.response.is_done():

            await interaction.followup.send(
                "❌ An error occurred while running that command.",
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                "❌ An error occurred while running that command.",
                ephemeral=True
            )

    except Exception:

        pass


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "Starting BFC Bot with Dashboard..."
    )

    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True
    )

    web_thread.start()

    bot.run(
        TOKEN
    )