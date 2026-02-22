#!/usr/bin/env python3
"""
Generates a dynamic HTML page listing all client-side and server-side mods,
their versions, requiredness, functionality descriptions, and config files.
"""

import os
import re
import json
import html
import subprocess
import glob
import gzip
import struct
import io
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(SCRIPT_DIR)  # instance root (one level above dashboard/)
CLIENT_MODS = os.path.join(BASE, "minecraft", "mods")
SERVER_MODS = os.path.join(BASE, "server", "mods")
CLIENT_CONFIG = os.path.join(BASE, "minecraft", "config")
SERVER_CONFIG = os.path.join(BASE, "server", "config")
CLIENT_INDEX = os.path.join(CLIENT_MODS, ".index")
SERVER_DIR = os.path.join(BASE, "server")
PLAYER_STATS = os.path.join(SERVER_DIR, "world", "stats")
PLAYER_ADVANCEMENTS = os.path.join(SERVER_DIR, "world", "advancements")
EASYAUTH_DB = os.path.join(SERVER_DIR, "EasyAuth", "easyauth.db")
USERCACHE = os.path.join(SERVER_DIR, "usercache.json")
OPS_FILE = os.path.join(SERVER_DIR, "ops.json")
WHITELIST_FILE = os.path.join(SERVER_DIR, "whitelist.json")
SERVER_PROPERTIES = os.path.join(SERVER_DIR, "server.properties")
LEVEL_DAT = os.path.join(SERVER_DIR, "world", "level.dat")
AI_CACHE_FILE = os.path.join(SCRIPT_DIR, "ai_cache.json")
DATAPACK_DIR = os.path.join(SERVER_DIR, "world", "datapacks")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# ── Mod knowledge base ──────────────────────────────────────────────────────
# Maps mod "slug" (lowercase, stripped) → { description, side, required }
# side: client / server / both
# required: required / optional / library
MOD_INFO = {
    # ── Client mods ──
    "bad packets":          {"desc": "Packet handling library for Fabric/Quilt mods", "side": "both", "req": "library"},
    "balm":                 {"desc": "Abstraction layer / library used by BlayMappings mods (Waystones, etc.)", "side": "both", "req": "library"},
    "betterf3":             {"desc": "Replaces the F3 debug screen with a customisable, more readable one", "side": "client", "req": "optional"},
    "bobby":                {"desc": "Caches and renders chunks beyond server view-distance on the client", "side": "client", "req": "optional"},
    "carry on":             {"desc": "Lets you pick up and carry tile-entities and mobs", "side": "both", "req": "optional"},
    "chat heads":           {"desc": "Adds player head icons next to chat messages", "side": "client", "req": "optional"},
    "chunk loaders":        {"desc": "Adds blocks that keep chunks loaded when you are away", "side": "both", "req": "optional"},
    "cloth config api":     {"desc": "Configuration screen library used by many mods", "side": "both", "req": "library"},
    "enchantment descriptions": {"desc": "Adds enchantment descriptions to tooltips", "side": "client", "req": "optional"},
    "fabric api":           {"desc": "Essential hooks and interoperability layer for Fabric mods", "side": "both", "req": "required"},
    "fabric language kotlin": {"desc": "Enables mods written in Kotlin on Fabric", "side": "both", "req": "library"},
    "fallingtree":          {"desc": "Chop a whole tree by breaking a single log block", "side": "both", "req": "optional"},
    "iris shaders":         {"desc": "Shader pack loader compatible with OptiFine shaders, works with Sodium", "side": "client", "req": "optional"},
    "just enough items (jei)": {"desc": "Item and recipe browser / lookup GUI", "side": "both", "req": "optional"},
    "lootr":                {"desc": "Makes loot containers per-player so everyone gets their own loot", "side": "both", "req": "required"},
    "mod menu":             {"desc": "Adds an in-game mod list screen with config access", "side": "client", "req": "optional"},
    "origins: legacy":      {"desc": "Choose an Origin at the start that gives unique abilities & weaknesses", "side": "both", "req": "required"},
    "text placeholder api": {"desc": "Server-side text placeholder / formatting API", "side": "both", "req": "library"},
    "prickle":              {"desc": "Library providing shared utilities for BlayMappings mods", "side": "both", "req": "library"},
    "promenade":            {"desc": "Adds new biomes, mobs, and building blocks to world generation", "side": "both", "req": "required"},
    "sodium":               {"desc": "High-performance rendering engine replacement — massive FPS improvements", "side": "client", "req": "required"},
    "supermartijn642's config lib": {"desc": "Configuration library for SuperMartijn642's mods", "side": "both", "req": "library"},
    "supermartijn642's core lib":   {"desc": "Core library for SuperMartijn642's mods", "side": "both", "req": "library"},
    "waystones":            {"desc": "Adds waystones for fast-travel teleportation between locations", "side": "both", "req": "required"},
    "wthit":                {"desc": "\"What The Hell Is That\" – shows info tooltip when looking at blocks/entities", "side": "both", "req": "optional"},
    "xaero's minimap":      {"desc": "Real-time minimap overlay with waypoints", "side": "client", "req": "optional"},
    "xaero's world map":    {"desc": "Full-screen world map companion to Xaero's Minimap", "side": "client", "req": "optional"},
    "yetanotherconfiglib (yacl)": {"desc": "Configuration screen library (YACL)", "side": "both", "req": "library"},
    "zoomify":              {"desc": "Adds a configurable zoom key (like OptiFine zoom)", "side": "client", "req": "optional"},

    # ── Server-only mods ──
    "additional structures":       {"desc": "Adds many new structures to world generation", "side": "server", "req": "required"},
    "chunky":                      {"desc": "Pre-generates world chunks to reduce lag on exploration", "side": "server", "req": "optional"},
    "distant horizons":            {"desc": "Renders low-detail terrain far beyond the normal view distance (LODs)", "side": "both", "req": "optional"},
    "explorify":                   {"desc": "Adds dozens of small vanilla-style structures to the world", "side": "server", "req": "required"},
    "incendium":                   {"desc": "Complete overhaul of Nether world generation with structures & biomes", "side": "server", "req": "required"},
    "moogs end structures":        {"desc": "Adds new structures to the End dimension", "side": "server", "req": "required"},
    "moogs missing villages":      {"desc": "Adds village variants for biomes that lack them", "side": "server", "req": "required"},
    "moogs nether structures":     {"desc": "Adds new structures to the Nether dimension", "side": "server", "req": "required"},
    "moogs soaring structures":    {"desc": "Adds floating/sky structures to the Overworld", "side": "server", "req": "required"},
    "moogs temples reimagined":    {"desc": "Revamps vanilla temple structures", "side": "server", "req": "required"},
    "moogs voyager structures":    {"desc": "Adds exploration-focused structures across dimensions", "side": "server", "req": "required"},
    "no expensive":                {"desc": "Removes the 'Too Expensive' anvil cap", "side": "server", "req": "optional"},
    "structory":                   {"desc": "Adds small vanilla-style structures and ruins to the world", "side": "server", "req": "required"},
    "structory towers":            {"desc": "Adds tower structures as an expansion to Structory", "side": "server", "req": "required"},
    "choicetheorem's overhauled village": {"desc": "Overhauls villages with new designs for every biome", "side": "server", "req": "required"},
    "abridged":                    {"desc": "Shortens & cleans up server join/leave messages", "side": "server", "req": "optional"},
    "alternate current":           {"desc": "Efficient redstone dust implementation for better server performance", "side": "server", "req": "required"},
    "antixray":                    {"desc": "Hides ores from X-ray texture packs / cheats", "side": "server", "req": "required"},
    "beacon range extender":       {"desc": "Increases the effective range of beacon effects", "side": "server", "req": "optional"},
    "c2me":                        {"desc": "Concurrent Chunk Management Engine — multi-threaded chunk loading", "side": "server", "req": "required"},
    "chest protection":            {"desc": "Protects chests from being opened/broken by non-owners", "side": "server", "req": "optional"},
    "cobweb":                      {"desc": "Library for mod configuration and utilities", "side": "server", "req": "library"},
    "collective":                  {"desc": "Shared library for Serilum's mods", "side": "server", "req": "library"},
    "config backuper":             {"desc": "Automatically backs up server config files", "side": "server", "req": "optional"},
    "cristellib":                  {"desc": "Library for Cristel's mods (CTOV, etc.)", "side": "server", "req": "library"},
    "dungeons and taverns":        {"desc": "Adds dungeon and tavern structures to world generation", "side": "server", "req": "required"},
    "easyauth":                    {"desc": "Server-side authentication system (login/register)", "side": "server", "req": "required"},
    "fastback":                    {"desc": "Server-side world backup manager using git-based snapshots", "side": "server", "req": "optional"},
    "ferritecore":                 {"desc": "Reduces RAM usage through memory optimisations", "side": "both", "req": "required"},
    "forge config api port":       {"desc": "Ports Forge's configuration API to Fabric", "side": "server", "req": "library"},
    "harvest with ease":           {"desc": "Right-click crops to harvest and auto-replant", "side": "server", "req": "optional"},
    "krypton":                     {"desc": "Optimises Minecraft networking stack for better performance", "side": "server", "req": "required"},
    "lithium":                     {"desc": "General-purpose server optimisation mod (game logic, AI, etc.)", "side": "server", "req": "required"},
    "lithostitched":               {"desc": "Library for data-driven worldgen stitching", "side": "server", "req": "library"},
    "moogs structure lib":         {"desc": "Library used by all Moogs structure mods", "side": "server", "req": "library"},
    "packetfixer":                 {"desc": "Fixes packet size issues to prevent disconnects", "side": "server", "req": "required"},
    "polymer":                     {"desc": "Server-side mod framework — lets server mods work without client mods", "side": "server", "req": "library"},
    "skin restorer":               {"desc": "Restores player skins on offline/hybrid servers", "side": "server", "req": "optional"},
    "spark":                       {"desc": "Performance profiler and monitoring tool", "side": "server", "req": "optional"},
    "sparse structures":           {"desc": "Adjusts vanilla structure spacing to reduce clustering", "side": "server", "req": "optional"},
    "tectonic":                    {"desc": "Overhauls terrain generation with dramatic landscapes", "side": "server", "req": "required"},
    "towns and towers":            {"desc": "Adds pillager outpost and village structure variants", "side": "server", "req": "required"},
    "villager names":              {"desc": "Gives villagers random human names", "side": "server", "req": "optional"},
    "worldedit":                   {"desc": "In-game map editor for large-scale building and terraforming", "side": "server", "req": "optional"},
    "brewery":                     {"desc": "Adds an alcohol brewing system with cauldrons, barrels, and aging", "side": "server", "req": "optional"},
}

# ── Map config files → mod names ────────────────────────────────────────────
CONFIG_TO_MOD = {
    # Client configs
    "bobby.conf": "Bobby",
    "carryon-client.json": "Carry On",
    "carryon-common.json": "Carry On",
    "chat_heads.json5": "Chat Heads",
    "chunkloaders-common.toml": "Chunk Loaders",
    "enchdesc.json": "Enchantment Descriptions",
    "fallingtree.json": "FallingTree",
    "iris-excluded.json": "Iris Shaders",
    "iris.properties": "Iris Shaders",
    "lootr.json": "Lootr",
    "modmenu.json": "Mod Menu",
    "origins_server.json": "Origins: Legacy",
    "pal.properties": "Origins: Legacy",
    "power_config.json5": "Origins: Legacy",
    "promenade.json": "Promenade",
    "sodium-fingerprint.json": "Sodium",
    "sodium-mixins.properties": "Sodium",
    "sodium-options.json": "Sodium",
    "waystones-common.toml": "Waystones",
    "xaerohud.txt": "Xaero's Minimap",
    "xaeropatreon.txt": "Xaero's Minimap",
    "yacl.json5": "YetAnotherConfigLib (YACL)",
    "zoomify.json": "Zoomify",
    "cardinal-components-api.properties": "Origins: Legacy",
    "biolith/general.json": "Promenade",
    "enhancedgroups/enhancedgroups.properties": "Enhanced Groups",
    "fabric/indigo-renderer.properties": "Fabric API",
    "jei/recipe-category-sort-order.ini": "Just Enough Items (JEI)",
    "jei/ingredient-list-type-sort-order.ini": "Just Enough Items (JEI)",
    "jei/blacklist.json": "Just Enough Items (JEI)",
    "jei/jei-colors.ini": "Just Enough Items (JEI)",
    "jei/ingredient-list-mod-sort-order.ini": "Just Enough Items (JEI)",
    "jei/jei-mod-id-format.ini": "Just Enough Items (JEI)",
    "jei/jei-client.ini": "Just Enough Items (JEI)",
    "jei/jei-debug.ini": "Just Enough Items (JEI)",
    "waila/waila.json5": "WTHIT",
    "waila/debug.json5": "WTHIT",
    "waila/blacklist.json5": "WTHIT",
    "waila/waila_plugins.json5": "WTHIT",
    "waila/plugin_toggle.json5": "WTHIT",
    "voicechat/username-cache.json": "Simple Voice Chat",
    "voicechat/voicechat-client.properties": "Simple Voice Chat",
    "voicechat/player-volumes.properties": "Simple Voice Chat",
    "voicechat/voicechat-server.properties": "Simple Voice Chat",
    "voicechat/category-volumes.properties": "Simple Voice Chat",
    "voicechat/translations.properties": "Simple Voice Chat",
    "xaero/minimap.txt": "Xaero's Minimap",

    # Server configs
    "abridged.json": "Abridged",
    "antixray.toml": "AntiXray",
    "beacon-range-extender.json": "Beacon Range Extender",
    "c2me.toml": "C2ME",
    "collective.json5": "Collective",
    "collective/entity_names.json": "Collective",
    "ctov.json": "ChoiceTheorem's Overhauled Village",
    "DistantHorizons.toml": "Distant Horizons",
    "ferritecore.mixin.properties": "FerriteCore",
    "forgeconfigapiport.toml": "Forge Config API Port",
    "harvest_with_ease-common.toml": "Harvest With Ease",
    "lithium.properties": "Lithium",
    "lithostitched.json": "Lithostitched",
    "NoExpensive.json": "No Expensive",
    "packetfixer.properties": "PacketFixer",
    "sparsestructures.json5": "Sparse Structures",
    "tectonic.json": "Tectonic",
    "villagernames.json5": "Villager Names",
    "villagernames/customnames.txt": "Villager Names",
    "chunky/config.json": "Chunky",
    "skinrestorer/mojang_profile_cache.json": "Skin Restorer",
    "skinrestorer/config.json": "Skin Restorer",
    "spark/config.json": "Spark",
    "EasyAuth/technical.conf": "EasyAuth",
    "EasyAuth/main.conf": "EasyAuth",
    "EasyAuth/storage.conf": "EasyAuth",
    "EasyAuth/extended.conf": "EasyAuth",
    "EasyAuth/translation.conf": "EasyAuth",
    "towns_and_towers/structure_enable_or_disable_new.json5": "Towns and Towers",
    "towns_and_towers/structure_rarity_new.json5": "Towns and Towers",
    "vanilla_structures/placement_structure_config.json5": "Sparse Structures",
    "vanilla_structures/toggle_structure_config.json5": "Sparse Structures",
    "cristellib/built_in_packs.json5": "CristelLib",
    "cristellib/auto_config_settings.json5": "CristelLib",
    "polymer/common.json": "Polymer",
    "polymer/server.json": "Polymer",
    "polymer/sound-patch.json": "Polymer",
    "polymer/auto-host.json": "Polymer",
    "worldedit/worldedit.properties": "WorldEdit",
}


# Mods already covered in the hardcoded beginner's guide (no AI needed)
GUIDE_COVERED_MODS = {
    "easyauth", "origins: legacy", "fallingtree", "carry on",
    "harvest with ease", "no expensive", "lootr", "chunk loaders",
    "beacon range extender", "waystones", "xaero's minimap",
    "xaero's world map", "bobby", "tectonic", "promenade",
    "incendium", "structory", "structory towers", "explorify",
    "dungeons and taverns", "choicetheorem's overhauled village",
    "towns and towers", "moogs end structures",
    "moogs nether structures", "moogs soaring structures",
    "moogs temples reimagined", "moogs voyager structures",
    "moogs missing villages", "moogs structure lib",
    "additional structures",
    "just enough items (jei)", "wthit", "enchantment descriptions",
    "betterf3", "chat heads", "zoomify", "villager names",
    "iris shaders", "sodium", "mod menu",
    "lithium", "c2me", "ferritecore", "krypton",
    "alternate current", "antixray", "packetfixer",
    "sparse structures", "distant horizons",
    "chest protection", "brewery",
    # Libraries (never need guide entries)
    "bad packets", "balm", "cloth config api", "fabric api",
    "fabric language kotlin", "prickle", "text placeholder api",
    "supermartijn642's config lib", "supermartijn642's core lib",
    "yetanotherconfiglib (yacl)", "cobweb", "collective",
    "cristellib", "forge config api port", "lithostitched",
    "polymer", "config backuper",
}

GUIDE_COVERED_DATAPACKS = {
    "graves", "back", "tpa", "afk display",
    "villager workstation highlights", "xp bottling",
    "redstone rotation wrench", "terracotta rotation wrench",
    "orb_of_origin_recipe", "vanillatweaks",
}


# ── AI helper functions ─────────────────────────────────────────────────────

def load_ai_cache():
    """Load the AI result cache from disk."""
    if os.path.isfile(AI_CACHE_FILE):
        try:
            with open(AI_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"mods": {}, "datapacks": {}}


def save_ai_cache(cache):
    """Persist the AI result cache to disk."""
    try:
        with open(AI_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"  [AI] Warning: could not save cache: {e}")


_ollama_ok = None  # cached result of availability check


def _ollama_available():
    """Check if Ollama is reachable (cached after first call)."""
    global _ollama_ok
    if _ollama_ok is not None:
        return _ollama_ok
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            _ollama_ok = len(models) > 0
    except Exception:
        _ollama_ok = False
    return _ollama_ok


def query_ollama(prompt):
    """Send a prompt to the local Ollama instance and return the response text."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result.get("response", "")
    except Exception as e:
        print(f"  [AI] Ollama request failed: {e}")
        return None


def ai_get_mod_info(name, jar_filename, side_hint="unknown"):
    """Query AI for mod information. Returns dict or None. Checks cache first."""
    cache = load_ai_cache()
    key = name.lower().strip()

    if key in cache.get("mods", {}):
        return cache["mods"][key]

    if not _ollama_available():
        return None

    prompt = (
        "You are a Minecraft Fabric modding expert. "
        "I need information about this Fabric mod.\n\n"
        f"Mod name: {name}\n"
        f"Jar filename: {jar_filename}\n"
        f"Installed on: {side_hint} side\n\n"
        "Return ONLY a JSON object with these exact fields:\n"
        "{\n"
        '  "desc": "One concise sentence describing what this mod does for players (max 120 chars)",\n'
        '  "side": "client" or "server" or "both",\n'
        '  "req": "required" or "optional" or "library",\n'
        '  "guide_section": one of "first-join", "gameplay", "travel", "world-gen", "ui", "datapacks", "behind-scenes", or null,\n'
        '  "guide_name": "Short display name for beginner guide entry",\n'
        '  "guide_body": "2-3 sentences for someone new to the server. Use <em> for emphasis. Be specific.",\n'
        '  "guide_example": "A concrete example or null"\n'
        "}\n\n"
        "Rules:\n"
        '- "req" = "library" for API/library mods players never interact with\n'
        '- "req" = "required" for mods that add content players must deal with\n'
        '- "req" = "optional" for quality-of-life mods\n'
        '- "guide_section" = null for library mods and invisible server-side mods\n'
        '- For performance mods use "behind-scenes"\n'
        '- For world generation mods use "world-gen"\n'
        "- Keep everything concise and beginner-friendly"
    )

    print(f"  [AI] Querying Ollama for mod: {name} ...")
    raw = query_ollama(prompt)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        for field in ["desc", "side", "req"]:
            if field not in data:
                return None
        if data["side"] not in ("client", "server", "both"):
            data["side"] = side_hint if side_hint != "unknown" else "both"
        if data["req"] not in ("required", "optional", "library"):
            data["req"] = "optional"
        # Sanitise guide_section
        valid_sections = {"first-join", "gameplay", "travel", "world-gen", "ui", "datapacks", "behind-scenes"}
        if data.get("guide_section") not in valid_sections:
            data["guide_section"] = None
        cache.setdefault("mods", {})[key] = data
        save_ai_cache(cache)
        print(f"  [AI] Got info for {name}: {data['desc'][:60]}...")
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def ai_get_datapack_info(pack_name):
    """Query AI for datapack information. Returns dict or None. Checks cache first."""
    cache = load_ai_cache()
    key = pack_name.lower().strip()

    if key in cache.get("datapacks", {}):
        return cache["datapacks"][key]

    if not _ollama_available():
        return None

    prompt = (
        "You are a Minecraft datapack expert. "
        "I need info about this datapack on a Fabric 1.21.11 server.\n\n"
        f"Datapack name/folder: {pack_name}\n\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "desc": "One concise sentence describing what this datapack does",\n'
        '  "guide_name": "Short display name for the beginner guide",\n'
        '  "guide_body": "2-3 sentences for beginners. Use <em> for emphasis.",\n'
        '  "guide_example": "A concrete example or null"\n'
        "}\n\n"
        "Keep it concise and beginner-friendly."
    )

    print(f"  [AI] Querying Ollama for datapack: {pack_name} ...")
    raw = query_ollama(prompt)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if "desc" not in data or "guide_name" not in data:
            return None
        cache.setdefault("datapacks", {})[key] = data
        save_ai_cache(cache)
        print(f"  [AI] Got info for datapack {pack_name}")
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def guide_card_html(name, body, example=None):
    """Create a single guide card HTML string."""
    parts = ['            <div class="guide-card">']
    if name:
        parts.append(f'                <div class="gc-name">{name}</div>')
    parts.append(f'                <div class="gc-body">')
    parts.append(f'                    {body}')
    parts.append(f'                </div>')
    if example:
        parts.append(f'                <div class="gc-example">{example}</div>')
    parts.append('            </div>')
    return '\n'.join(parts)


def collect_datapacks():
    """Scan the datapacks directory and return info dicts (with AI for unknowns)."""
    datapacks = []
    if not os.path.isdir(DATAPACK_DIR):
        return datapacks

    has_ollama = _ollama_available()

    for entry in sorted(os.listdir(DATAPACK_DIR)):
        full_path = os.path.join(DATAPACK_DIR, entry)
        if not os.path.isdir(full_path):
            continue

        # Clean the display name (strip version + MC version suffix)
        name = re.sub(r'\s*v\d+[\d.]*\s*\(MC[^)]*\)$', '', entry).strip()
        name = re.sub(r'\s*v\d+[\d.]*$', '', name).strip()

        dp = {"name": name, "folder": entry, "desc": ""}

        # Check if already covered by the hardcoded guide
        key = name.lower().strip()
        is_covered = False
        for covered in GUIDE_COVERED_DATAPACKS:
            if covered in key or key in covered:
                is_covered = True
                break
        if entry.lower().startswith("vanillatweaks"):
            is_covered = True

        if is_covered:
            dp["covered"] = True
        elif has_ollama:
            # Query AI for unknown datapacks
            info = ai_get_datapack_info(entry)
            if info:
                dp["desc"] = info.get("desc", "")
                dp["guide_name"] = info.get("guide_name")
                dp["guide_body"] = info.get("guide_body")
                dp["guide_example"] = info.get("guide_example")

        datapacks.append(dp)
    return datapacks


def build_ai_guide_cards(client_mods, server_mods, datapacks):
    """Build a dict of section_id -> HTML string for AI-generated guide cards."""
    sections = {
        "first-join": [], "gameplay": [], "travel": [],
        "world-gen": [], "ui": [], "datapacks": [], "behind-scenes": [],
    }

    # Mods with AI guide info
    for mod in client_mods + server_mods:
        key = mod["name"].lower().strip()
        if key in GUIDE_COVERED_MODS:
            continue
        section = mod.get("ai_guide_section")
        if not section or section not in sections:
            continue
        card = guide_card_html(
            mod.get("ai_guide_name", mod["name"]),
            mod.get("ai_guide_body", mod.get("desc", "")),
            mod.get("ai_guide_example"),
        )
        sections[section].append(card)

    # Datapacks with AI guide info
    for dp in datapacks:
        if dp.get("covered"):
            continue
        if dp.get("guide_name"):
            card = guide_card_html(
                dp["guide_name"],
                dp.get("guide_body", dp.get("desc", "")),
                dp.get("guide_example"),
            )
            sections["datapacks"].append(card)

    # Return joined HTML: newline-prefixed when non-empty, empty string otherwise
    return {k: ('\n' + '\n'.join(v) if v else '') for k, v in sections.items()}


def parse_pw_toml(path):
    """Minimal parser for PrismLauncher .pw.toml metadata files."""
    data = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"^([\w\-]+)\s*=\s*'(.+)'$", line)
            if m:
                data[m.group(1)] = m.group(2)
            m2 = re.match(r'^([\w\-]+)\s*=\s*"(.+)"$', line)
            if m2:
                data[m2.group(1)] = m2.group(2)
    return data


def jar_to_name(jar):
    """Extract a human-readable name from a jar filename."""
    name = jar.replace(".jar", "")
    # Strip version-like suffixes
    name = re.sub(r'[-_]\d+\..*$', '', name)
    name = re.sub(r'[-_]fabric.*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]neoforge.*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]mc\d.*$', '', name, flags=re.IGNORECASE)
    # Clean up
    name = name.replace("-", " ").replace("_", " ").strip()
    return name


def version_from_jar(jar):
    """Extract version string from jar filename."""
    name = jar.replace(".jar", "")
    m = re.search(r'(\d+\.\d+[\w.+\-]*)', name)
    return m.group(1) if m else "unknown"


def lookup_mod_info(name):
    """Look up mod info from the knowledge base."""
    key = name.lower().strip()
    if key in MOD_INFO:
        return MOD_INFO[key]
    # Fuzzy: try partial matching
    for k, v in MOD_INFO.items():
        if k in key or key in k:
            return v
    # Extra fuzzy: strip punctuation and compare
    clean_key = re.sub(r'[^a-z0-9 ]', '', key)
    for k, v in MOD_INFO.items():
        clean_k = re.sub(r'[^a-z0-9 ]', '', k)
        if clean_k in clean_key or clean_key in clean_k:
            return v
    return None


# Additional jar→name overrides for server mods with non-standard jar names
JAR_NAME_OVERRIDES = {
    "[Fabric]ctov-1.21.11-3.6.1a.jar": ("ChoiceTheorem's Overhauled Village", "3.6.1a"),
    "t_and_t-fabric-neoforge-1.13.8.jar": ("Towns and Towers", "1.13.8"),
    "NE-1.21.11-1.10.2.jar": ("No Expensive", "1.10.2"),
    "Origins-Legacy-1.11.4+1.21.11.jar": ("Origins: Legacy", "1.11.4+1.21.11"),
    "beaconrange-1.4.0-21.jar": ("Beacon Range Extender", "1.4.0"),
    "Explorify v1.6.4 f15-88.mod.jar": ("Explorify", "1.6.4"),
    "Incendium_1.21.x_v5.4.10.jar": ("Incendium", "5.4.10"),
    "Structory_1.21.x_v1.3.14.jar": ("Structory", "1.3.14"),
    "Structory_Towers_1.21.x_v1.0.15.jar": ("Structory Towers", "1.0.15"),
    "supermartijn642configlib-1.1.8-fabric-mc1.21.11.jar": ("SuperMartijn642's Config Lib", "1.1.8"),
    "supermartijn642corelib-1.1.20-fabric-mc1.21.11.jar": ("SuperMartijn642's Core Lib", "1.1.20"),
}


def read_config_file(path):
    """Read a config file and return its content as a string."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        # Limit very large files
        if len(content) > 15000:
            content = content[:15000] + "\n... (truncated, file too large)"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def collect_configs(config_dir, side_label):
    """Collect config files mapped to their mod names."""
    configs = {}  # mod_name → [(relative_path, content)]
    if not os.path.isdir(config_dir):
        return configs

    for root, dirs, files in os.walk(config_dir):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, config_dir)
            mod_name = CONFIG_TO_MOD.get(rel, None)

            # If not in the explicit map, try to guess from the top-level dir/file name
            if mod_name is None:
                parts = rel.split(os.sep)
                top = parts[0]
                # Try the top-level directory name as a lookup
                for ckey in CONFIG_TO_MOD:
                    if ckey.startswith(top + "/") or ckey.startswith(top + os.sep):
                        mod_name = CONFIG_TO_MOD[ckey]
                        break
                # For jei subfolders
                if mod_name is None and top.lower() == "jei":
                    mod_name = "Just Enough Items (JEI)"
                elif mod_name is None and top.lower() == "waila":
                    mod_name = "WTHIT"
                elif mod_name is None and top.lower() == "voicechat":
                    mod_name = "Simple Voice Chat"
                elif mod_name is None and top.lower() == "xaero":
                    mod_name = "Xaero's Minimap"

            if mod_name is None:
                mod_name = f"Unknown ({rel})"
            if mod_name not in configs:
                configs[mod_name] = []
            configs[mod_name].append((rel, read_config_file(full)))
    return configs


def collect_client_mods():
    """Collect client mods from .index metadata + jar list."""
    mods = []
    # Parse .index metadata
    indexed = {}
    if os.path.isdir(CLIENT_INDEX):
        for f in sorted(os.listdir(CLIENT_INDEX)):
            if f.endswith(".pw.toml"):
                data = parse_pw_toml(os.path.join(CLIENT_INDEX, f))
                name = data.get("name", f.replace(".pw.toml", ""))
                version = data.get("x-prismlauncher-version-number", "")
                filename = data.get("filename", "")
                indexed[filename] = {"name": name, "version": version}

    # Walk jar files
    if os.path.isdir(CLIENT_MODS):
        for jar in sorted(os.listdir(CLIENT_MODS)):
            if not jar.endswith(".jar"):
                continue
            if jar in indexed:
                name = indexed[jar]["name"]
                version = indexed[jar]["version"]
            else:
                name = jar_to_name(jar)
                version = version_from_jar(jar)

            info = lookup_mod_info(name)
            if not info:
                info = ai_get_mod_info(name, jar, "client")
            if info:
                desc = info["desc"]
                side = info.get("side", "client")
                req = info.get("req", "unknown")
            else:
                desc = ""
                side = "client"
                req = "unknown"
                info = {}

            # Client mods can only be "client" or "both"
            if side == "server":
                side = "both"
            elif side not in ("client", "both"):
                side = "client"

            mods.append({
                "name": name,
                "version": version,
                "jar": jar,
                "desc": desc,
                "side": side,
                "req": req,
                "ai_guide_section": info.get("guide_section"),
                "ai_guide_name": info.get("guide_name"),
                "ai_guide_body": info.get("guide_body"),
                "ai_guide_example": info.get("guide_example"),
            })
    return mods


def collect_server_mods():
    """Collect server mods from jar filenames (no .index on server)."""
    mods = []
    if not os.path.isdir(SERVER_MODS):
        return mods

    for jar in sorted(os.listdir(SERVER_MODS)):
        if not jar.endswith(".jar"):
            continue

        # Check overrides first
        if jar in JAR_NAME_OVERRIDES:
            name, version = JAR_NAME_OVERRIDES[jar]
        else:
            name = jar_to_name(jar)
            version = version_from_jar(jar)

        info = lookup_mod_info(name)
        if not info:
            # Try with the full jar name for tricky cases
            for k, v in MOD_INFO.items():
                if k.replace(" ", "").replace("'", "") in jar.lower().replace("-", "").replace("_", ""):
                    info = v
                    break

        if not info:
            info = ai_get_mod_info(name, jar, "server")
        if info:
            desc = info["desc"]
            side = info.get("side", "server")
            req = info.get("req", "unknown")
        else:
            desc = ""
            side = "server"
            req = "unknown"
            info = {}

        # Server mods can only be "server" or "both"
        if side == "client":
            side = "both"
        elif side not in ("server", "both"):
            side = "server"

        mods.append({
            "name": name,
            "version": version,
            "jar": jar,
            "desc": desc,
            "side": side,
            "req": req,
            "ai_guide_section": info.get("guide_section"),
            "ai_guide_name": info.get("guide_name"),
            "ai_guide_body": info.get("guide_body"),
            "ai_guide_example": info.get("guide_example"),
        })
    return mods


def req_badge(req):
    if req == "required":
        return '<span class="badge req">Required</span>'
    elif req == "optional":
        return '<span class="badge opt">Optional</span>'
    elif req == "library":
        return '<span class="badge lib">Library</span>'
    else:
        return '<span class="badge unk">Unknown</span>'


def side_badge(side):
    if side == "client":
        return '<span class="badge side-client">Client</span>'
    elif side == "server":
        return '<span class="badge side-server">Server</span>'
    else:
        return '<span class="badge side-both">Both</span>'


def format_ticks_to_time(ticks):
    """Convert game ticks to human-readable time."""
    seconds = ticks // 20
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_min = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining_min}m"
    days = hours // 24
    remaining_hrs = hours % 24
    return f"{days}d {remaining_hrs}h"


def format_distance(cm):
    """Convert centimetres to human-readable distance."""
    m = cm / 100
    if m < 1000:
        return f"{m:,.0f}m"
    km = m / 1000
    return f"{km:,.1f}km"


def collect_players():
    """Collect player data from usercache, EasyAuth, stats, and advancements."""
    import sqlite3
    from datetime import datetime

    # 1. UUID → name mapping from usercache
    uuid_to_name = {}
    if os.path.isfile(USERCACHE):
        with open(USERCACHE) as f:
            for entry in json.load(f):
                uuid_to_name[entry["uuid"]] = entry["name"]

    # 2. Ops set
    ops = set()
    if os.path.isfile(OPS_FILE):
        with open(OPS_FILE) as f:
            for entry in json.load(f):
                ops.add(entry["uuid"])

    # 3. Whitelist set
    whitelisted = set()
    if os.path.isfile(WHITELIST_FILE):
        with open(WHITELIST_FILE) as f:
            for entry in json.load(f):
                whitelisted.add(entry.get("uuid", ""))
                # also add by name since whitelist may use different uuid
                whitelisted.add(entry.get("name", "").lower())

    # 4. EasyAuth registration & last login
    auth_data = {}  # uuid → {registration_date, last_authenticated_date}
    if os.path.isfile(EASYAUTH_DB):
        try:
            conn = sqlite3.connect(EASYAUTH_DB)
            c = conn.cursor()
            for row in c.execute("SELECT uuid, data FROM easyauth"):
                uuid, data_str = row
                try:
                    d = json.loads(data_str)
                    auth_data[uuid] = {
                        "reg_date": d.get("registration_date", ""),
                        "last_login": d.get("last_authenticated_date", ""),
                    }
                except json.JSONDecodeError:
                    pass
            conn.close()
        except Exception:
            pass

    # 5. Collect all known UUIDs from stats files
    all_uuids = set(uuid_to_name.keys())
    if os.path.isdir(PLAYER_STATS):
        for f in os.listdir(PLAYER_STATS):
            if f.endswith(".json"):
                all_uuids.add(f.replace(".json", ""))

    # 6. Build player list
    players = []
    for uuid in sorted(all_uuids):
        name = uuid_to_name.get(uuid, uuid[:8] + "...")
        is_op = uuid in ops

        # Parse dates from EasyAuth
        reg_str = ""
        last_login_str = ""
        if uuid in auth_data:
            raw_reg = auth_data[uuid]["reg_date"]
            raw_login = auth_data[uuid]["last_login"]
            # Parse dates like 2026-02-11T11:39:06.945289594+05:30[Asia/Kolkata]
            for raw, label in [(raw_reg, "reg"), (raw_login, "login")]:
                if raw:
                    try:
                        # Strip timezone name in brackets
                        clean = re.sub(r'\[.*\]', '', raw)
                        # Truncate nanoseconds to microseconds
                        clean = re.sub(r'(\d{2}:\d{2}:\d{2}\.\d{6})\d+', r'\1', clean)
                        dt = datetime.fromisoformat(clean)
                        formatted = dt.strftime("%Y-%m-%d %H:%M")
                        if label == "reg":
                            reg_str = formatted
                        else:
                            last_login_str = formatted
                    except Exception:
                        if label == "reg":
                            reg_str = raw[:16]
                        else:
                            last_login_str = raw[:16]

        # Stats
        play_time = 0
        deaths = 0
        mob_kills = 0
        distance_walked = 0
        distance_flown = 0
        blocks_mined = 0
        items_crafted = 0
        damage_dealt = 0

        stats_file = os.path.join(PLAYER_STATS, f"{uuid}.json")
        if os.path.isfile(stats_file):
            try:
                with open(stats_file) as f:
                    sd = json.load(f)
                custom = sd.get("stats", {}).get("minecraft:custom", {})
                play_time = custom.get("minecraft:play_time", 0)
                deaths = custom.get("minecraft:deaths", 0)
                mob_kills = custom.get("minecraft:mob_kills", 0)
                distance_walked = (
                    custom.get("minecraft:walk_one_cm", 0) +
                    custom.get("minecraft:sprint_one_cm", 0) +
                    custom.get("minecraft:crouch_one_cm", 0) +
                    custom.get("minecraft:walk_on_water_one_cm", 0) +
                    custom.get("minecraft:walk_under_water_one_cm", 0)
                )
                distance_flown = (
                    custom.get("minecraft:fly_one_cm", 0) +
                    custom.get("minecraft:aviate_one_cm", 0)
                )
                damage_dealt = custom.get("minecraft:damage_dealt", 0)

                mined = sd.get("stats", {}).get("minecraft:mined", {})
                blocks_mined = sum(mined.values())

                crafted = sd.get("stats", {}).get("minecraft:crafted", {})
                items_crafted = sum(crafted.values())
            except Exception:
                pass

        # Advancements
        adv_count = 0
        adv_file = os.path.join(PLAYER_ADVANCEMENTS, f"{uuid}.json")
        if os.path.isfile(adv_file):
            try:
                with open(adv_file) as f:
                    ad = json.load(f)
                adv_count = sum(
                    1 for k, v in ad.items()
                    if isinstance(v, dict) and v.get("done")
                    and not k.startswith("minecraft:recipes/")
                )
            except Exception:
                pass

        players.append({
            "name": name,
            "uuid": uuid,
            "is_op": is_op,
            "reg_date": reg_str,
            "last_login": last_login_str,
            "play_time": play_time,
            "play_time_fmt": format_ticks_to_time(play_time),
            "deaths": deaths,
            "mob_kills": mob_kills,
            "distance_walked": distance_walked,
            "distance_walked_fmt": format_distance(distance_walked),
            "distance_flown": distance_flown,
            "distance_flown_fmt": format_distance(distance_flown),
            "blocks_mined": blocks_mined,
            "items_crafted": items_crafted,
            "advancements": adv_count,
            "damage_dealt": damage_dealt,
        })

    # Filter out players with no playtime
    players = [p for p in players if p["play_time"] > 0]

    # Deduplicate by name (keep highest playtime entry)
    seen = {}
    for p in players:
        key = p["name"].lower()
        if key not in seen or p["play_time"] > seen[key]["play_time"]:
            seen[key] = p
    players = list(seen.values())

    # Sort by play_time descending
    players.sort(key=lambda p: p["play_time"], reverse=True)
    return players


# Minecraft 1.21 server.properties defaults (for highlighting changes)
SERVER_DEFAULTS = {
    "accepts-transfers": "false",
    "allow-flight": "false",
    "broadcast-console-to-ops": "true",
    "broadcast-rcon-to-ops": "true",
    "bug-report-link": "",
    "difficulty": "easy",
    "enable-code-of-conduct": "false",
    "enable-jmx-monitoring": "false",
    "enable-query": "false",
    "enable-rcon": "false",
    "enable-status": "true",
    "enforce-secure-profile": "true",
    "enforce-whitelist": "false",
    "entity-broadcast-range-percentage": "100",
    "force-gamemode": "false",
    "function-permission-level": "2",
    "gamemode": "survival",
    "generate-structures": "true",
    "generator-settings": "{}",
    "hardcore": "false",
    "hide-online-players": "false",
    "initial-disabled-packs": "",
    "initial-enabled-packs": "vanilla",
    "level-name": "world",
    "level-seed": "",
    "level-type": "minecraft\\:normal",
    "log-ips": "true",
    "management-server-allowed-origins": "",
    "management-server-enabled": "false",
    "management-server-host": "localhost",
    "management-server-port": "0",
    "management-server-secret": "",
    "management-server-tls-enabled": "true",
    "management-server-tls-keystore": "",
    "management-server-tls-keystore-password": "",
    "max-chained-neighbor-updates": "1000000",
    "max-players": "20",
    "max-tick-time": "60000",
    "max-world-size": "29999984",
    "motd": "A Minecraft Server",
    "network-compression-threshold": "256",
    "online-mode": "true",
    "op-permission-level": "4",
    "pause-when-empty-seconds": "-1",
    "player-idle-timeout": "0",
    "prevent-proxy-connections": "false",
    "query.port": "25565",
    "rate-limit": "0",
    "rcon.password": "",
    "rcon.port": "25575",
    "region-file-compression": "deflate",
    "require-resource-pack": "false",
    "resource-pack": "",
    "resource-pack-id": "",
    "resource-pack-prompt": "",
    "resource-pack-sha1": "",
    "server-ip": "",
    "server-port": "25565",
    "simulation-distance": "10",
    "spawn-protection": "16",
    "status-heartbeat-interval": "0",
    "sync-chunk-writes": "true",
    "text-filtering-config": "",
    "text-filtering-version": "0",
    "use-native-transport": "true",
    "view-distance": "10",
    "white-list": "false",
}

# Human-readable explanations for commonly changed properties
PROPERTY_EXPLANATIONS = {
    "accepts-transfers": "If true, the server accepts player transfers from other servers via the transfer packet.",
    "allow-flight": "If false, players who appear to fly (without elytra/creative) may be kicked by the anti-cheat.",
    "broadcast-console-to-ops": "If true, console command output is sent to all online ops.",
    "broadcast-rcon-to-ops": "If true, RCON command output is sent to all online ops.",
    "bug-report-link": "Custom URL shown in the bug report screen. Empty uses the default Mojang link.",
    "difficulty": "Controls hostile mob damage and hunger drain. hard = mobs deal more damage, hunger can kill.",
    "enable-code-of-conduct": "If true, shows a code-of-conduct popup to players on first join.",
    "enable-jmx-monitoring": "Exposes JMX MBeans for monitoring server performance with external tools.",
    "enable-query": "Enables the GameSpy4 query protocol, letting external tools poll server info on query.port.",
    "enable-rcon": "Enables remote console access (RCON) for sending commands remotely.",
    "enable-status": "If true, the server responds to status pings in the multiplayer server list.",
    "enforce-secure-profile": "If true, players must have a Mojang-signed key pair. Breaks offline mode if enabled incorrectly.",
    "enforce-whitelist": "If true, players removed from whitelist while online are kicked immediately.",
    "entity-broadcast-range-percentage": "Percentage of default entity tracking range. Lower = entities disappear sooner, less bandwidth.",
    "force-gamemode": "If true, players are forced into the default gamemode every time they (re)join.",
    "function-permission-level": "Op level required to run commands inside data pack functions (1-4).",
    "gamemode": "Default game mode for new players: survival, creative, adventure, or spectator.",
    "generate-structures": "If true, villages, dungeons, temples, etc. generate in new chunks.",
    "generator-settings": "JSON object for flat/custom world generation. Only used when level-type is flat or buffet.",
    "hardcore": "If true, players are banned on death and difficulty is locked to hard.",
    "hide-online-players": "If true, the player list in the server status response is hidden.",
    "initial-disabled-packs": "Comma-separated list of data packs that are disabled by default on world creation.",
    "initial-enabled-packs": "Comma-separated list of data packs enabled by default. 'vanilla' is always included.",
    "level-name": "Name of the world folder on disk.",
    "level-seed": "World generation seed. Empty = random seed chosen at creation.",
    "level-type": "World type used for generation. minecraft:normal is the standard overworld generator.",
    "log-ips": "If true, player IP addresses are logged when they connect.",
    "management-server-allowed-origins": "Comma-separated allowed origins for the management server HTTP endpoint.",
    "management-server-enabled": "Enables the internal management REST API used by some monitoring tools.",
    "management-server-host": "Hostname/IP that the management server binds to.",
    "management-server-port": "Port for the management REST API. 0 = auto-assign.",
    "management-server-secret": "Shared secret for authenticating management server requests.",
    "management-server-tls-enabled": "If true, the management server uses TLS encryption.",
    "management-server-tls-keystore": "Path to the TLS keystore file for the management server.",
    "management-server-tls-keystore-password": "Password for the management server TLS keystore.",
    "max-chained-neighbor-updates": "Limits cascading block updates (e.g. redstone chains). Prevents lag from massive chain reactions.",
    "max-players": "Maximum number of players that can be connected simultaneously.",
    "max-tick-time": "Milliseconds a single tick can take before the watchdog kills the server. -1 disables the watchdog.",
    "max-world-size": "Maximum world border radius in blocks. Limits how far players can travel.",
    "motd": "Message of the day displayed in the multiplayer server list.",
    "network-compression-threshold": "Packets larger than this (bytes) are compressed. Higher = less CPU, slightly more bandwidth. -1 disables.",
    "online-mode": "If false, the server skips Mojang authentication. Required for offline/cracked clients (EasyAuth handles auth instead).",
    "op-permission-level": "Default permission level granted to ops (1-4). 4 = full access including /stop.",
    "pause-when-empty-seconds": "Seconds after the last player leaves before the server pauses ticking. -1 = never pause.",
    "player-idle-timeout": "Minutes of inactivity before a player is kicked. 0 = never kick idle players.",
    "prevent-proxy-connections": "If true, the server blocks connections from known VPN/proxy IPs using the Mojang API.",
    "query.port": "Port used for the GameSpy4 query protocol (if enable-query is true).",
    "rate-limit": "Maximum number of packets a client can send per second. 0 = no limit.",
    "rcon.password": "Password required for RCON connections. Empty = RCON disabled even if enable-rcon is true.",
    "rcon.port": "Port used for RCON remote console connections.",
    "region-file-compression": "Compression algorithm for region files. 'deflate' is standard; 'lz4' is faster but uses more disk.",
    "require-resource-pack": "If true, players who decline the server resource pack are disconnected.",
    "resource-pack": "URL to a resource pack (.zip) that clients are prompted to download.",
    "resource-pack-id": "UUID identifying the resource pack. Avoids re-downloading if the pack hasn't changed.",
    "resource-pack-prompt": "Custom message shown when prompting the player to accept the resource pack.",
    "resource-pack-sha1": "SHA-1 hash of the resource pack file. Used to verify download integrity.",
    "server-ip": "IP address the server binds to. Empty = binds to all available interfaces (0.0.0.0).",
    "server-port": "The network port the server listens on for player connections.",
    "simulation-distance": "Chunk radius around each player that is actively ticked (mobs move, crops grow). Lower saves CPU.",
    "spawn-protection": "Block radius around world spawn where only ops can build/break. 0 disables protection.",
    "status-heartbeat-interval": "Overrides the heartbeat interval for the status endpoint. 0 uses the default.",
    "sync-chunk-writes": "If true, chunk writes are synchronous (safer but slower). false = async, risks corruption on crash.",
    "text-filtering-config": "Path to a text filtering configuration file for chat message filtering.",
    "text-filtering-version": "Version of the text filtering protocol to use. 0 = default.",
    "use-native-transport": "If true, uses optimized Linux epoll for networking. Disable if you experience connection issues.",
    "view-distance": "Chunk radius the server sends to each player. Higher = more visible terrain, more bandwidth and memory.",
    "white-list": "If true, only players listed in whitelist.json can join the server.",
}


# Properties whose values must never appear in the generated HTML
SECRET_PROPERTIES = {
    "rcon.password",
    "management-server-secret",
    "management-server-tls-keystore-password",
}

REDACTED = "********"


def collect_server_properties():
    """Parse server.properties, return (properties_list, changed_list, raw_content).

    properties_list: [{key, value, default, changed, explanation}]
    changed_list: subset where value != default
    raw_content: original file text
    """
    props = []
    changed = []
    raw = ""
    if not os.path.isfile(SERVER_PROPERTIES):
        return props, changed, raw
    with open(SERVER_PROPERTIES) as f:
        raw = f.read()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Redact sensitive values
        if key in SECRET_PROPERTIES and value:
            value = REDACTED
        default = SERVER_DEFAULTS.get(key)
        is_changed = default is not None and value != default
        explanation = PROPERTY_EXPLANATIONS.get(key, "")
        entry = {
            "key": key,
            "value": value,
            "default": default if default is not None else "?",
            "changed": is_changed,
            "explanation": explanation,
        }
        props.append(entry)
        if is_changed:
            changed.append(entry)
    return props, changed, raw


# ── Gamerule knowledge base ─────────────────────────────────────────────────
# Minecraft 1.21 vanilla defaults  (all values are strings in the NBT)
GAMERULE_DEFAULTS = {
    "minecraft:advance_time": "1",
    "minecraft:advance_weather": "1",
    "minecraft:allow_entering_nether_using_portals": "1",
    "minecraft:block_drops": "1",
    "minecraft:block_explosion_drop_decay": "1",
    "minecraft:command_block_output": "1",
    "minecraft:command_blocks_work": "1",
    "minecraft:drowning_damage": "1",
    "minecraft:elytra_movement_check": "1",
    "minecraft:ender_pearls_vanish_on_death": "1",
    "minecraft:entity_drops": "1",
    "minecraft:fall_damage": "1",
    "minecraft:fire_damage": "1",
    "minecraft:fire_spread_radius_around_player": "128",
    "minecraft:forgive_dead_players": "1",
    "minecraft:freeze_damage": "1",
    "minecraft:global_sound_events": "1",
    "minecraft:immediate_respawn": "0",
    "minecraft:keep_inventory": "0",
    "minecraft:lava_source_conversion": "0",
    "minecraft:limited_crafting": "0",
    "minecraft:locator_bar": "1",
    "minecraft:log_admin_commands": "1",
    "minecraft:max_block_modifications": "32768",
    "minecraft:max_command_forks": "65536",
    "minecraft:max_command_sequence_length": "65536",
    "minecraft:max_entity_cramming": "24",
    "minecraft:max_snow_accumulation_height": "1",
    "minecraft:mob_drops": "1",
    "minecraft:mob_explosion_drop_decay": "1",
    "minecraft:mob_griefing": "1",
    "minecraft:natural_health_regeneration": "1",
    "minecraft:player_movement_check": "1",
    "minecraft:players_nether_portal_creative_delay": "0",
    "minecraft:players_nether_portal_default_delay": "80",
    "minecraft:players_sleeping_percentage": "100",
    "minecraft:projectiles_can_break_blocks": "1",
    "minecraft:pvp": "1",
    "minecraft:raids": "1",
    "minecraft:random_tick_speed": "3",
    "minecraft:reduced_debug_info": "0",
    "minecraft:respawn_radius": "10",
    "minecraft:send_command_feedback": "1",
    "minecraft:show_advancement_messages": "1",
    "minecraft:show_death_messages": "1",
    "minecraft:spawn_mobs": "1",
    "minecraft:spawn_monsters": "1",
    "minecraft:spawn_patrols": "1",
    "minecraft:spawn_phantoms": "1",
    "minecraft:spawn_wandering_traders": "1",
    "minecraft:spawn_wardens": "1",
    "minecraft:spawner_blocks_work": "1",
    "minecraft:spectators_generate_chunks": "1",
    "minecraft:spread_vines": "1",
    "minecraft:tnt_explodes": "1",
    "minecraft:tnt_explosion_drop_decay": "0",
    "minecraft:universal_anger": "0",
    "minecraft:water_source_conversion": "1",
}

GAMERULE_EXPLANATIONS = {
    "minecraft:advance_time": "Whether in-game time of day advances.",
    "minecraft:advance_weather": "Whether weather patterns change over time.",
    "minecraft:allow_entering_nether_using_portals": "Whether players can use nether portals to travel between dimensions.",
    "minecraft:block_drops": "Whether blocks drop items when broken.",
    "minecraft:block_explosion_drop_decay": "Whether some block drops are destroyed by block-caused explosions (TNT).",
    "minecraft:command_block_output": "Whether command blocks show their output in chat.",
    "minecraft:command_blocks_work": "Whether command blocks can execute commands.",
    "minecraft:drowning_damage": "Whether players and mobs take drowning damage.",
    "minecraft:elytra_movement_check": "Whether the server validates elytra flight speed to prevent cheating.",
    "minecraft:ender_pearls_vanish_on_death": "Whether thrown ender pearls disappear when the player dies.",
    "minecraft:entity_drops": "Whether entities (excluding blocks) drop items on death.",
    "minecraft:fall_damage": "Whether players and mobs take fall damage.",
    "minecraft:fire_damage": "Whether players and mobs take fire/lava damage.",
    "minecraft:fire_spread_radius_around_player": "Block radius around players within which fire can spread. 0 disables fire spread.",
    "minecraft:forgive_dead_players": "Whether angered neutral mobs stop being angry when the target player dies.",
    "minecraft:freeze_damage": "Whether players and mobs take freezing damage from powder snow.",
    "minecraft:global_sound_events": "Whether certain sounds (e.g. wither spawning) are heard globally.",
    "minecraft:immediate_respawn": "Whether players respawn instantly without the death screen.",
    "minecraft:keep_inventory": "Whether players keep their inventory and XP on death.",
    "minecraft:lava_source_conversion": "Whether lava can form source blocks (like water does).",
    "minecraft:limited_crafting": "Whether players can only craft recipes they have unlocked.",
    "minecraft:locator_bar": "Whether the boss bar / locator bar displays at the top of the screen.",
    "minecraft:log_admin_commands": "Whether admin commands are logged to the server log.",
    "minecraft:max_block_modifications": "Maximum number of block changes per tick from commands.",
    "minecraft:max_command_forks": "Maximum number of command forks (e.g. /execute) allowed.",
    "minecraft:max_command_sequence_length": "Maximum length of a command sequence that can execute in a single tick.",
    "minecraft:max_entity_cramming": "Maximum number of entities that can push into the same block before suffocation damage begins.",
    "minecraft:max_snow_accumulation_height": "Maximum layers of snow that can accumulate from snowfall.",
    "minecraft:mob_drops": "Whether mobs drop loot on death.",
    "minecraft:mob_explosion_drop_decay": "Whether some block drops are destroyed by mob-caused explosions (creepers, ghasts).",
    "minecraft:mob_griefing": "Whether mobs can modify blocks (creeper explosions, endermen picking up blocks, etc.).",
    "minecraft:natural_health_regeneration": "Whether players naturally regenerate health when their hunger bar is full.",
    "minecraft:player_movement_check": "Whether the server validates player movement to prevent cheating.",
    "minecraft:players_nether_portal_creative_delay": "Ticks a creative-mode player must stand in a nether portal before teleporting. 0 = instant.",
    "minecraft:players_nether_portal_default_delay": "Ticks a survival/adventure player must stand in a nether portal before teleporting.",
    "minecraft:players_sleeping_percentage": "Percentage of online players that must sleep to skip the night. 100 = all, 0 = one player suffices.",
    "minecraft:projectiles_can_break_blocks": "Whether projectiles (arrows, tridents) can break certain blocks like chorus flowers.",
    "minecraft:pvp": "Whether players can deal damage to other players.",
    "minecraft:raids": "Whether raids can spawn when a player with Bad Omen enters a village.",
    "minecraft:random_tick_speed": "Speed of random block ticks (crop growth, leaf decay, etc.). Default 3, higher = faster.",
    "minecraft:reduced_debug_info": "Whether the debug screen (F3) shows reduced information.",
    "minecraft:respawn_radius": "Block radius around the world spawn within which players randomly respawn.",
    "minecraft:send_command_feedback": "Whether command execution results are shown in chat.",
    "minecraft:show_advancement_messages": "Whether advancement completion messages are broadcast in chat.",
    "minecraft:show_death_messages": "Whether death messages are shown in chat.",
    "minecraft:spawn_mobs": "Whether passive/neutral mobs can spawn naturally.",
    "minecraft:spawn_monsters": "Whether hostile mobs can spawn naturally.",
    "minecraft:spawn_patrols": "Whether pillager patrols can spawn.",
    "minecraft:spawn_phantoms": "Whether phantoms can spawn for players who haven't slept.",
    "minecraft:spawn_wandering_traders": "Whether wandering traders can spawn naturally.",
    "minecraft:spawn_wardens": "Whether wardens can spawn when triggered by sculk shriekers.",
    "minecraft:spawner_blocks_work": "Whether mob spawner blocks can spawn entities.",
    "minecraft:spectators_generate_chunks": "Whether spectator-mode players cause chunk generation.",
    "minecraft:spread_vines": "Whether vines (and similar blocks) can spread to adjacent surfaces.",
    "minecraft:tnt_explodes": "Whether TNT blocks explode when ignited.",
    "minecraft:tnt_explosion_drop_decay": "Whether some block drops are destroyed by TNT explosions.",
    "minecraft:universal_anger": "Whether angered neutral mobs attack all nearby players, not just the one who provoked them.",
    "minecraft:water_source_conversion": "Whether water can form new source blocks when flowing between two existing sources.",
}


def _parse_nbt_payload(f, tag_type):
    """Minimal NBT payload reader — supports the types found in level.dat."""
    if tag_type == 1:  return struct.unpack('>b', f.read(1))[0]
    if tag_type == 2:  return struct.unpack('>h', f.read(2))[0]
    if tag_type == 3:  return struct.unpack('>i', f.read(4))[0]
    if tag_type == 4:  return struct.unpack('>q', f.read(8))[0]
    if tag_type == 5:  return struct.unpack('>f', f.read(4))[0]
    if tag_type == 6:  return struct.unpack('>d', f.read(8))[0]
    if tag_type == 7:
        n = struct.unpack('>i', f.read(4))[0]
        return f.read(n)
    if tag_type == 8:
        n = struct.unpack('>h', f.read(2))[0]
        return f.read(n).decode('utf-8')
    if tag_type == 9:
        item_type = struct.unpack('>b', f.read(1))[0]
        n = struct.unpack('>i', f.read(4))[0]
        return [_parse_nbt_payload(f, item_type) for _ in range(n)]
    if tag_type == 10:
        result = {}
        while True:
            child_type = struct.unpack('>b', f.read(1))[0]
            if child_type == 0:
                break
            clen = struct.unpack('>h', f.read(2))[0]
            cname = f.read(clen).decode('utf-8')
            result[cname] = _parse_nbt_payload(f, child_type)
        return result
    if tag_type == 11:
        n = struct.unpack('>i', f.read(4))[0]
        return [struct.unpack('>i', f.read(4))[0] for _ in range(n)]
    if tag_type == 12:
        n = struct.unpack('>i', f.read(4))[0]
        return [struct.unpack('>q', f.read(8))[0] for _ in range(n)]
    return None


def _read_level_dat(path):
    """Read level.dat and return the root compound dict."""
    with gzip.open(path) as gz:
        data = gz.read()
    f = io.BytesIO(data)
    tag_type = struct.unpack('>b', f.read(1))[0]
    nlen = struct.unpack('>h', f.read(2))[0]
    f.read(nlen)  # root tag name
    return _parse_nbt_payload(f, tag_type)


def collect_gamerules():
    """Read gamerules from level.dat, return (rules_list, changed_list).

    rules_list:  [{key, display_key, value, default, changed, explanation}]
    changed_list: subset where value != default
    """
    rules = []
    changed = []
    if not os.path.isfile(LEVEL_DAT):
        return rules, changed
    try:
        root = _read_level_dat(LEVEL_DAT)
        game_rules = root.get("Data", {}).get("game_rules", {})
    except Exception:
        return rules, changed
    for key in sorted(game_rules.keys()):
        value = str(game_rules[key])
        default = GAMERULE_DEFAULTS.get(key)
        is_changed = default is not None and value != default
        explanation = GAMERULE_EXPLANATIONS.get(key, "")
        # Friendly display: strip "minecraft:" prefix
        display_key = key.replace("minecraft:", "") if key.startswith("minecraft:") else key
        entry = {
            "key": key,
            "display_key": display_key,
            "value": value,
            "default": default if default is not None else "?",
            "changed": is_changed,
            "explanation": explanation,
        }
        rules.append(entry)
        if is_changed:
            changed.append(entry)
    return rules, changed


def _get_dir_size(path, exclude=None):
    """Return total size of a directory in bytes using du (fast)."""
    if not os.path.isdir(path):
        return 0
    try:
        cmd = ["du", "-sb"]
        if exclude:
            cmd.append(f"--exclude={exclude}")
        cmd.append(path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return int(result.stdout.split()[0])
    except Exception:
        pass
    return 0


def _format_size(nbytes):
    """Human-readable file size."""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def generate_html():
    client_mods = collect_client_mods()
    server_mods = collect_server_mods()
    client_configs = collect_configs(CLIENT_CONFIG, "client")
    server_configs = collect_configs(SERVER_CONFIG, "server")
    players = collect_players()
    server_props, changed_props, raw_properties = collect_server_properties()
    gamerules, changed_gamerules = collect_gamerules()
    datapacks = collect_datapacks()

    # World / backup sizes  (du is much faster than os.walk for large dirs)
    world_dir = os.path.join(SERVER_DIR, "world")
    backup_dir = os.path.join(SERVER_DIR, "world", ".git")
    world_only_bytes = _get_dir_size(world_dir, exclude=".git")
    backup_size_bytes = _get_dir_size(backup_dir)
    world_size_str = _format_size(world_only_bytes)
    backup_size_str = _format_size(backup_size_bytes)

    ai_guide_cards = build_ai_guide_cards(client_mods, server_mods, datapacks)

    # Build config sections per mod
    def config_html(configs_dict):
        parts = []
        for mod_name in sorted(configs_dict.keys()):
            files = configs_dict[mod_name]
            for rel_path, content in sorted(files, key=lambda x: x[0]):
                safe_id = re.sub(r'[^a-zA-Z0-9]', '-', f"{mod_name}-{rel_path}")
                parts.append(f'''
                <div class="config-block" data-mod="{html.escape(mod_name.lower())}">
                    <div class="config-header" onclick="this.parentElement.classList.toggle('open')">
                        <span class="config-chevron">&#9654;</span>
                        <strong>{html.escape(mod_name)}</strong>
                        <code class="config-path">{html.escape(rel_path)}</code>
                    </div>
                    <div class="config-content">
                        <pre><code>{html.escape(content)}</code></pre>
                    </div>
                </div>''')
        return "\n".join(parts)

    def mod_rows(mods):
        rows = []
        for m in mods:
            rows.append(f'''
                <tr data-name="{html.escape(m['name'].lower())}" data-req="{m['req']}" data-side="{m['side']}">
                    <td class="mod-name"><strong>{html.escape(m['name'])}</strong></td>
                    <td><code>{html.escape(m['version'])}</code></td>
                    <td>{req_badge(m['req'])}</td>
                    <td>{side_badge(m['side'])}</td>
                    <td class="mod-desc">{html.escape(m['desc'])}</td>
                </tr>''')
        return "\n".join(rows)

    def player_rows(players):
        rows = []
        for i, p in enumerate(players, 1):
            op_badge = ' <span class="op-badge">OP</span>' if p["is_op"] else ""
            rows.append(f'''
                <tr data-name="{html.escape(p['name'].lower())}">
                    <td class="num-cell" data-val="{i}" style="color:var(--text-dim);font-size:0.8rem">{i}</td>
                    <td>
                        <div class="player-name">
                            <span>{html.escape(p['name'])}{op_badge}</span>
                        </div>
                    </td>
                    <td data-val="{html.escape(p['reg_date']) or ''}">{html.escape(p['reg_date']) or '<span style="color:var(--text-dim)">—</span>'}</td>
                    <td data-val="{html.escape(p['last_login']) or ''}">{html.escape(p['last_login']) or '<span style="color:var(--text-dim)">—</span>'}</td>
                    <td class="num-cell" data-val="{p['play_time']}">{p['play_time_fmt']}</td>
                    <td class="num-cell" data-val="{p['deaths']}">{p['deaths']:,}</td>
                    <td class="num-cell" data-val="{p['mob_kills']}">{p['mob_kills']:,}</td>
                    <td class="num-cell" data-val="{p['distance_walked']}">{p['distance_walked_fmt']}</td>
                    <td class="num-cell" data-val="{p['distance_flown']}">{p['distance_flown_fmt']}</td>
                    <td class="num-cell" data-val="{p['blocks_mined']}">{p['blocks_mined']:,}</td>
                    <td class="num-cell" data-val="{p['items_crafted']}">{p['items_crafted']:,}</td>
                    <td class="num-cell" data-val="{p['advancements']}">{p['advancements']}</td>
                </tr>''')
        return rows

    page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pilidium Modpack — Mod &amp; Config Reference</title>
<style>
/* ── Minecraft Font (Monocraft via Google Fonts CDN) ── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

:root {{
    /* Minecraft UI palette — based on in-game formatting codes */
    --bg: #1d1f21;           /* obsidian dark */
    --bg-deep: #141517;      /* void black */
    --surface: #2a2d31;      /* stone dark */
    --surface-hover: #35393e; /* stone mid */
    --border: #3e4248;       /* stone edge */
    --border-glow: #555a62;  /* iron gray */
    --text: #e0e0e0;         /* near-white */
    --text-dim: #aaaaaa;     /* MC gray */
    --text-bright: #ffffff;  /* MC white */
    --accent: #55ff55;       /* MC green (grass) */
    --accent-glow: #7fff7f;  /* lighter green */
    --accent2: #55ffff;      /* MC aqua (diamond) */
    --accent2-glow: #88ffff; /* lighter aqua */
    --red: #ff5555;          /* MC red (redstone) */
    --orange: #ffaa00;       /* MC gold */
    --purple: #aa00aa;       /* MC dark purple (enchant) */
    --cyan: #55ffff;         /* MC aqua */
    --gold: #ffaa00;         /* MC gold */
    --enchant: #ff55ff;      /* MC light purple */
    --radius: 3px;
    --radius-lg: 4px;
    --grid-color: rgba(62,66,72,0.2);
    --header-bg: linear-gradient(135deg, #2a2d31 0%, rgba(31,31,56,0.8) 100%);
    --th-bg: rgba(0,0,0,0.35);
    --code-bg: rgba(0,0,0,0.35);
    --pre-bg: rgba(0,0,0,0.2);
    --hover-tint: rgba(85,255,85,0.05);
    --shadow: rgba(0,0,0,0.5);
}}

[data-theme="light"] {{
    --bg: #c8c3b8;
    --bg-deep: #b8b3a7;
    --surface: #d8d4c9;
    --surface-hover: #cec9bd;
    --border: #9e9788;
    --border-glow: #8a8375;
    --text: #1a1a1a;
    --text-dim: #4a4540;
    --text-bright: #0d0d0d;
    --accent: #147014;
    --accent-glow: #1a8a1a;
    --accent2: #0a5f78;
    --accent2-glow: #0e7490;
    --red: #b02828;
    --orange: #9a7209;
    --purple: #6a2478;
    --cyan: #0a5f78;
    --gold: #9a7209;
    --enchant: #7b28c4;
    --grid-color: rgba(120,115,105,0.2);
    --header-bg: linear-gradient(135deg, #d8d4c9 0%, #c8c3b8 100%);
    --th-bg: rgba(0,0,0,0.1);
    --code-bg: rgba(0,0,0,0.1);
    --pre-bg: rgba(0,0,0,0.07);
    --hover-tint: rgba(20,112,20,0.08);
    --shadow: rgba(0,0,0,0.18);
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: 'JetBrains Mono', 'Cascadia Code', 'SF Mono', 'Consolas', monospace;
    background: var(--bg-deep);
    color: var(--text);
    line-height: 1.6;
    padding: 0;
    min-height: 100vh;
    /* Subtle grid pattern reminiscent of crafting table */
    background-image:
        linear-gradient(var(--grid-color) 1px, transparent 1px),
        linear-gradient(90deg, var(--grid-color) 1px, transparent 1px);
    background-size: 32px 32px;
    background-attachment: fixed;
}}

/* ── Container ── */
.container {{
    max-width: 1440px;
    margin: 0 auto;
    padding: 2rem 2rem;
}}

/* ── Header ── */
.header {{
    position: relative;
    background: var(--header-bg);
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 2rem 2.5rem;
    margin-bottom: 2rem;
    overflow: hidden;
}}
.header::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, var(--accent), var(--gold), var(--accent2));
}}
.header-content {{
    position: relative;
    z-index: 1;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
}}
h1 {{
    font-size: 2.2rem;
    font-weight: 700;
    margin-bottom: 0.15rem;
    color: var(--text-bright);
    letter-spacing: -0.02em;
}}
h1 .title-accent {{
    color: var(--accent-glow);
}}
h1 small {{
    font-size: 0.8rem;
    color: var(--text-dim);
    font-weight: 400;
    letter-spacing: 0.04em;
}}
.header-sub {{
    color: var(--text-dim);
    font-size: 0.78rem;
    margin-top: 0.5rem;
    letter-spacing: 0.02em;
}}

/* ── Stat Cards ── */
.stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1rem;
    margin: 1.5rem 0 2rem;
}}
.stat-card {{
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 1.25rem 1rem;
    text-align: center;
    transition: all 0.2s ease;
    position: relative;
    overflow: hidden;
    cursor: default;
}}
.stat-card::after {{
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent);
    opacity: 0;
    transition: opacity 0.2s;
}}
.stat-card:hover {{
    border-color: var(--border-glow);
    background: var(--surface-hover);
    transform: translateY(-2px);
}}
.stat-card:hover::after {{ opacity: 1; }}
.stat-card .num {{
    font-size: 2.2rem;
    font-weight: 700;
    color: var(--accent-glow);
    line-height: 1;
    margin-bottom: 0.4rem;
}}
.stat-card .label {{
    font-size: 0.7rem;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
}}

/* ── Legend ── */
.legend {{
    display: grid;
    grid-template-columns: repeat(3, auto);
    gap: 0.5rem 2rem;
    justify-content: start;
    margin: 0.5rem 0 1.5rem;
    font-size: 0.78rem;
    padding: 0.75rem 1.25rem;
    background: var(--surface);
    border: 1px solid var(--border);
}}
.legend-item {{
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--text-dim);
    white-space: nowrap;
}}

/* ── Theme Toggle ── */
.theme-toggle {{
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius);
    color: var(--text-dim);
    font-family: inherit;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.4rem 0.9rem;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
    letter-spacing: 0.03em;
    flex-shrink: 0;
    margin-top: 0.25rem;
}}
.theme-toggle:hover {{
    border-color: var(--border-glow);
    color: var(--text);
    background: var(--surface-hover);
}}

/* ── Section headings ── */
h2 {{
    font-size: 1.25rem;
    font-weight: 700;
    margin: 2rem 0 1rem;
    color: var(--text-bright);
    padding-bottom: 0.6rem;
    border-bottom: 2px solid var(--border);
    letter-spacing: -0.01em;
}}
h2 .count {{
    font-size: 0.75rem;
    color: var(--text-dim);
    font-weight: 400;
    margin-left: 0.5rem;
}}

/* ── Tabs ── */
.tabs {{
    display: flex;
    flex-wrap: wrap;
    gap: 0;
    margin-bottom: 0;
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    padding: 0.35rem 0.35rem 0;
    overflow-x: auto;
}}
.tab {{
    padding: 0.65rem 1.25rem;
    cursor: pointer;
    background: transparent;
    border: none;
    color: var(--text-dim);
    font-family: inherit;
    font-size: 0.82rem;
    font-weight: 600;
    border-radius: var(--radius) var(--radius) 0 0;
    transition: all 0.15s ease;
    white-space: nowrap;
    letter-spacing: 0.01em;
    position: relative;
}}
.tab:hover {{
    color: var(--text);
    background: rgba(85,255,85,0.06);
}}
.tab.active {{
    color: var(--accent-glow);
    background: var(--bg-deep);
    border: 1px solid var(--border);
    border-bottom-color: var(--bg-deep);
    margin-bottom: -1px;
}}
.tab-panel {{
    display: none;
    background: var(--bg-deep);
    border: 2px solid var(--border);
    border-top: none;
    border-radius: 0 0 var(--radius-lg) var(--radius-lg);
    padding: 1.75rem 2rem;
}}
.tab-panel.active {{ display: block; }}

/* ── Search & Filters ── */
.controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.65rem;
    margin: 1rem 0 1.25rem;
    align-items: center;
}}
.search-box {{
    flex: 1;
    min-width: 200px;
    padding: 0.55rem 1rem;
    border: 2px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--text);
    font-family: inherit;
    font-size: 0.82rem;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
}}
.search-box::placeholder {{ color: var(--text-dim); }}
.search-box:focus {{
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(85,255,85,0.15);
}}
.filter-btn {{
    padding: 0.45rem 0.9rem;
    border: 2px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--text-dim);
    font-family: inherit;
    cursor: pointer;
    font-size: 0.78rem;
    font-weight: 600;
    transition: all 0.15s ease;
    letter-spacing: 0.02em;
}}
.filter-btn:hover {{
    border-color: var(--accent);
    color: var(--text);
    background: var(--surface-hover);
}}
.filter-btn.active {{
    background: var(--accent);
    color: var(--bg-deep);
    border-color: var(--accent);
    font-weight: 700;
}}

/* ── Tables ── */
.mod-table-wrap {{
    overflow-x: auto;
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}}
thead th {{
    text-align: left;
    padding: 0.75rem 1rem;
    background: var(--th-bg);
}}
thead th:hover {{ color: var(--accent-glow); }}
thead th .sort-arrow {{ margin-left: 4px; font-size: 0.65rem; }}
tbody tr {{
    border-bottom: 1px solid rgba(62,66,72,0.5);
    transition: background 0.1s;
}}
tbody tr:last-child {{ border-bottom: none; }}
tbody tr:hover {{ background: rgba(85,255,85,0.05); }}
tbody tr.hidden {{ display: none; }}
td {{ padding: 0.6rem 1rem; vertical-align: middle; }}
td.mod-name {{ white-space: nowrap; font-weight: 600; color: var(--text-bright); }}
td.mod-desc {{ color: var(--text-dim); min-width: 200px; }}
td code {{
    background: var(--code-bg);
    padding: 0.12rem 0.4rem;
    border-radius: 3px;
    font-size: 0.8rem;
    font-family: inherit;
    color: var(--cyan);
    border: 1px solid rgba(85,255,255,0.15);
}}

/* ── Badges ── */
.badge {{
    display: inline-block;
    padding: 0.12rem 0.55rem;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 700;
    white-space: nowrap;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    border: 1px solid transparent;
}}
.badge.req {{ background: rgba(255,85,85,0.1); color: var(--red); border-color: rgba(255,85,85,0.25); }}
.badge.opt {{ background: rgba(255,170,0,0.1); color: var(--orange); border-color: rgba(255,170,0,0.25); }}
.badge.lib {{ background: rgba(170,0,170,0.1); color: var(--enchant); border-color: rgba(170,0,170,0.25); }}
.badge.unk {{ background: rgba(170,170,170,0.1); color: var(--text-dim); border-color: rgba(170,170,170,0.2); }}
.badge.side-client {{ background: rgba(85,255,85,0.08); color: var(--accent); border-color: rgba(85,255,85,0.2); }}
.badge.side-server {{ background: rgba(85,255,255,0.08); color: var(--accent2); border-color: rgba(85,255,255,0.2); }}
.badge.side-both {{ background: rgba(255,170,0,0.1); color: var(--orange); border-color: rgba(255,170,0,0.25); }}

/* ── Config blocks ── */
.config-section {{
    margin-top: 1rem;
}}
.config-block {{
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    margin-bottom: 0.5rem;
    background: var(--surface);
    overflow: hidden;
    transition: border-color 0.15s;
}}
.config-block:hover {{ border-color: var(--border-glow); }}
.config-header {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.65rem 1rem;
    cursor: pointer;
    user-select: none;
    transition: background 0.1s;
}}
.config-header:hover {{ background: var(--surface-hover); }}
.config-chevron {{
    font-size: 0.7rem;
    color: var(--text-dim);
    transition: transform 0.2s ease;
    flex-shrink: 0;
}}
.config-block.open .config-chevron {{ transform: rotate(90deg); color: var(--gold); }}
.config-path {{
    margin-left: auto;
    font-size: 0.72rem;
    color: var(--text-dim);
    background: var(--code-bg);
    padding: 0.15rem 0.5rem;
    border-radius: 3px;
    font-family: inherit;
    border: 1px solid rgba(62,66,72,0.4);
}}
.config-content {{
    display: none;
    border-top: 2px solid var(--border);
}}
.config-block.open .config-content {{ display: block; }}
.config-content pre {{
    margin: 0;
    padding: 1rem;
    overflow-x: auto;
    font-size: 0.78rem;
    line-height: 1.55;
    color: var(--text-dim);
    background: var(--pre-bg);
    font-family: inherit;
}}
.config-content pre code {{
    background: none;
    padding: 0;
    border: none;
    color: inherit;
    font-size: inherit;
    font-family: inherit;
}}
.config-block.hidden {{ display: none; }}

/* ── Server Properties ── */
.props-table-wrap, .props-full-wrap {{
    overflow-x: auto;
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    margin-bottom: 1.5rem;
}}
.props-table {{
    width: 100%;
    border-collapse: collapse;
}}
.props-table th {{
    text-align: left;
    padding: 0.65rem 1rem;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    background: var(--th-bg);
    border-bottom: 2px solid var(--border);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
}}
.props-table td {{
    padding: 0.55rem 1rem;
    font-size: 0.8rem;
    border-bottom: 1px solid rgba(45,45,80,0.5);
    vertical-align: top;
}}
.props-table tr:last-child td {{ border-bottom: none; }}
.props-table tr:hover {{ background: rgba(85,255,85,0.03); }}
.props-table tr.prop-changed td {{
    background: rgba(85,255,85,0.05);
}}
.props-table tr.prop-changed td:first-child code {{
    color: var(--accent-glow);
    border-color: rgba(85,255,85,0.25);
}}
.props-table .prop-default {{
    color: var(--text-dim);
    font-size: 0.78rem;
}}
.props-table .prop-explain {{
    color: var(--text-dim);
    font-size: 0.76rem;
    max-width: 420px;
    line-height: 1.5;
}}

/* ── Stats (player summary reuse) ── */
.player-stats-summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 0.75rem;
    margin: 1.25rem 0 1.5rem;
}}
.player-stats-summary .stat-card .num {{
    font-size: 1.6rem;
}}

/* ── Guide ── */
.guide-intro {{
    color: var(--text-dim);
    font-size: 0.85rem;
    margin: 0.5rem 0 2rem;
    max-width: 800px;
    line-height: 1.75;
}}
.guide-section {{
    margin-bottom: 2.5rem;
}}
.guide-section h3 {{
    font-size: 1.05rem;
    color: var(--accent-glow);
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--border);
}}
.guide-section h3 .gs-icon {{
    font-size: 1.3rem;
}}
.guide-card {{
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 1rem 1.25rem;
    margin-bottom: 0.6rem;
    transition: border-color 0.15s;
}}
.guide-card:hover {{ border-color: var(--border-glow); }}
.guide-card .gc-name {{
    font-weight: 700;
    color: var(--text-bright);
    font-size: 0.88rem;
}}
.guide-card .gc-body {{
    color: var(--text-dim);
    font-size: 0.82rem;
    margin-top: 0.35rem;
    line-height: 1.7;
}}
.guide-card .gc-body em {{
    color: var(--accent-glow);
    font-style: normal;
}}
.guide-card .gc-example {{
    margin-top: 0.5rem;
    padding: 0.6rem 0.85rem;
    background: var(--pre-bg);
    border-radius: var(--radius);
    font-size: 0.78rem;
    color: var(--text-dim);
    border-left: 3px solid var(--accent);
}}
.guide-img-wrap {{
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    overflow: hidden;
    background: #000;
    display: inline-block;
    max-width: 100%;
    margin-top: 0.75rem;
    box-shadow: 0 4px 24px var(--shadow);
}}
.guide-img-wrap img {{
    display: block;
    max-width: 100%;
    height: auto;
}}
.guide-divider {{
    border: none;
    border-top: 2px solid var(--border);
    margin: 2rem 0;
}}

/* ── Players ── */
.player-table-wrap {{
    overflow-x: auto;
    border: 2px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
}}
.player-table-wrap table td {{
    font-size: 0.8rem;
}}
.player-name {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    white-space: nowrap;
    font-weight: 600;
    color: var(--text-bright);
}}
.op-badge {{
    display: inline-block;
    padding: 0.1rem 0.45rem;
    border-radius: 3px;
    font-size: 0.6rem;
    font-weight: 700;
    background: rgba(255,85,85,0.1);
    color: var(--red);
    border: 1px solid rgba(255,85,85,0.25);
    margin-left: 0.25rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}
td.num-cell {{
    text-align: right;
    font-variant-numeric: tabular-nums;
}}

/* ── Scrollbar ── */
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: var(--bg-deep); }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--border-glow); }}

/* ── Selection ── */
::selection {{ background: rgba(85,255,85,0.25); color: var(--text-bright); }}

/* ── Responsive ── */
@media (max-width: 1024px) {{
    .tab-panel {{ padding: 1.25rem 1.25rem; }}
}}
@media (max-width: 768px) {{
    body {{ background-size: 24px 24px; }}
    .container {{ padding: 1rem 0.75rem; }}
    .header {{ padding: 1.25rem 1.25rem; }}
    h1 {{ font-size: 1.5rem; }}
    h1 small {{ display: block; margin-top: 0.25rem; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.65rem; }}
    .stat-card {{ padding: 0.9rem 0.75rem; }}
    .stat-card .num {{ font-size: 1.6rem; }}
    .tabs {{ border-radius: var(--radius) var(--radius) 0 0; }}
    .tab {{ padding: 0.5rem 0.85rem; font-size: 0.75rem; }}
    .tab-panel {{ padding: 1rem 0.85rem; border-radius: 0 0 var(--radius) var(--radius); }}
    .controls {{ gap: 0.5rem; }}
    .legend {{ gap: 0.4rem 1.25rem; font-size: 0.72rem; grid-template-columns: repeat(2, auto); }}
    td {{ padding: 0.45rem 0.65rem; }}
}}
@media (max-width: 480px) {{
    .container {{ padding: 0.5rem; }}
    .header {{ padding: 1rem; }}
    h1 {{ font-size: 1.3rem; }}
    .stats {{ grid-template-columns: repeat(2, 1fr); }}
    .tab {{ padding: 0.4rem 0.6rem; font-size: 0.7rem; }}
    .tab-panel {{ padding: 0.75rem 0.65rem; }}
    .search-box {{ min-width: 150px; }}
    .header-content {{ flex-wrap: wrap; gap: 0.5rem; }}
    .theme-toggle {{ font-size: 0.68rem; padding: 0.3rem 0.6rem; }}
}}
</style>
</head>
<body>

<div class="container">
    <div class="header">
        <div class="header-content">
            <div>
                <h1><span class="title-accent">Pilidium</span> Modpack <small>Minecraft 1.21.11 // Fabric</small></h1>
                <div class="header-sub">Server dashboard -- mods, configs, players, and setup guide</div>
            </div>
            <button class="theme-toggle" id="theme-toggle" title="Toggle light/dark mode">LIGHT MODE</button>
        </div>
    </div>

    <div class="stats">
        <div class="stat-card">
            <div class="num">{len(client_mods)}</div>
            <div class="label">Client Mods</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(server_mods)}</div>
            <div class="label">Server Mods</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(client_configs)}</div>
            <div class="label">Client Configs</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(server_configs)}</div>
            <div class="label">Server Configs</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(players)}</div>
            <div class="label">Players</div>
        </div>
    </div>

    <div class="legend">
        <div class="legend-item">{req_badge("required")} Must be installed</div>
        <div class="legend-item">{req_badge("optional")} Can be removed</div>
        <div class="legend-item">{req_badge("library")} Dependency for other mods</div>
        <div class="legend-item">{side_badge("client")} Client-side only</div>
        <div class="legend-item">{side_badge("server")} Server-side only</div>
        <div class="legend-item">{side_badge("both")} Required on both sides</div>
    </div>

    <!-- ── Tab Navigation ── -->
    <div class="tabs">
        <button class="tab active" data-tab="home">Home</button>
        <button class="tab" data-tab="client-mods">Client Mods</button>
        <button class="tab" data-tab="server-mods">Server Mods</button>
        <button class="tab" data-tab="client-configs">Client Configs</button>
        <button class="tab" data-tab="server-configs">Server Configs</button>
        <button class="tab" data-tab="server-properties">Server Properties</button>
        <button class="tab" data-tab="gamerules">Gamerules</button>
        <button class="tab" data-tab="players">Players</button>
        <button class="tab" data-tab="beginners-guide">Beginner's Guide</button>
    </div>

    <!-- ═══ HOME ═══ -->
    <div id="home" class="tab-panel active">
        <h2>Welcome to <span style="color:var(--accent)">Pilidium's</span> Local Minecraft Server</h2>

        <div style="margin:1.5rem 0; line-height:1.8;">
            <p style="margin-bottom:1rem; color:var(--text);">A casual, modded Fabric 1.21.11 server built around exploration, quality-of-life, and having a good time. The world is shaped by <strong style="color:#ffaa00">Tectonic</strong> terrain generation with hundreds of custom structures scattered across the Overworld, a fully overhauled <strong style="color:#ff5555">Nether</strong> via Incendium, and new End content. Pick an <strong style="color:var(--accent2)">Origin</strong> with unique abilities, fast-travel with <strong style="color:#aa00aa">Waystones</strong>, brew drinks at the <strong style="color:#ffaa00">Brewery</strong>, and enjoy conveniences like tree-felling, crop right-click harvesting, per-player loot, and no anvil cost cap.</p>
            <p style="margin-bottom:1rem; color:var(--text);">Non-consensual griefing/attacking is highly discouraged. We are a relatively peaceful bunch of people trying to enjoy the game in our own ways.</p>
            <p style="margin-bottom:0.5rem; color:var(--text-muted); font-size:0.9rem;">This dashboard lists every client and server mod with descriptions, all config files, server properties, active gamerules, player statistics, and a beginner's guide to get you started.</p>
            <div style="display:flex; flex-wrap:wrap; gap:0.75rem; margin-top:1.25rem;">
                <span class="badge" style="background:var(--hover-tint); border:1px solid var(--accent); color:var(--accent); padding:0.4rem 1rem; font-size:0.82rem;">TLauncher players can join</span>
                <span class="badge" style="background:var(--hover-tint); border:1px solid var(--accent2); color:var(--accent2); padding:0.4rem 1rem; font-size:0.82rem;">Server backup every 2 hours</span>
            </div>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:1rem; margin-top:2rem;">
            <div class="stat-card">
                <div class="num">{world_size_str}</div>
                <div class="label">World Size</div>
            </div>
            <div class="stat-card">
                <div class="num">{backup_size_str}</div>
                <div class="label">Backup Size (Git)</div>
            </div>
        </div>
    </div>

    <!-- ═══ CLIENT MODS ═══ -->
    <div id="client-mods" class="tab-panel">
        <h2>Client Mods <span class="count">({len(client_mods)} mods)</span></h2>
        <div class="controls">
            <input type="text" class="search-box" placeholder="Search client mods…" data-target="client-table">
            <button class="filter-btn active" data-filter="all" data-target="client-table">All</button>
            <button class="filter-btn" data-filter="required" data-target="client-table">Required</button>
            <button class="filter-btn" data-filter="optional" data-target="client-table">Optional</button>
            <button class="filter-btn" data-filter="library" data-target="client-table">Library</button>
        </div>
        <div class="mod-table-wrap">
            <table id="client-table">
                <thead>
                    <tr>
                        <th data-sort="name">Name <span class="sort-arrow"></span></th>
                        <th data-sort="version">Version <span class="sort-arrow"></span></th>
                        <th data-sort="req">Required <span class="sort-arrow"></span></th>
                        <th data-sort="side">Side <span class="sort-arrow"></span></th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                    {mod_rows(client_mods)}
                </tbody>
            </table>
        </div>
    </div>

    <!-- ═══ SERVER MODS ═══ -->
    <div id="server-mods" class="tab-panel">
        <h2>Server Mods <span class="count">({len(server_mods)} mods)</span></h2>
        <div class="controls">
            <input type="text" class="search-box" placeholder="Search server mods…" data-target="server-table">
            <button class="filter-btn active" data-filter="all" data-target="server-table">All</button>
            <button class="filter-btn" data-filter="required" data-target="server-table">Required</button>
            <button class="filter-btn" data-filter="optional" data-target="server-table">Optional</button>
            <button class="filter-btn" data-filter="library" data-target="server-table">Library</button>
        </div>
        <div class="mod-table-wrap">
            <table id="server-table">
                <thead>
                    <tr>
                        <th data-sort="name">Name <span class="sort-arrow"></span></th>
                        <th data-sort="version">Version <span class="sort-arrow"></span></th>
                        <th data-sort="req">Required <span class="sort-arrow"></span></th>
                        <th data-sort="side">Side <span class="sort-arrow"></span></th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                    {mod_rows(server_mods)}
                </tbody>
            </table>
        </div>
    </div>

    <!-- ═══ CLIENT CONFIGS ═══ -->
    <div id="client-configs" class="tab-panel">
        <h2>Client Configuration Files <span class="count">({sum(len(v) for v in client_configs.values())} files)</span></h2>
        <div class="controls">
            <input type="text" class="search-box config-search" placeholder="Search client configs…" data-target="client-config-section">
        </div>
        <div id="client-config-section" class="config-section">
            {config_html(client_configs)}
        </div>
    </div>

    <!-- ═══ SERVER CONFIGS ═══ -->
    <div id="server-configs" class="tab-panel">
        <h2>Server Configuration Files <span class="count">({sum(len(v) for v in server_configs.values())} files)</span></h2>
        <div class="controls">
            <input type="text" class="search-box config-search" placeholder="Search server configs…" data-target="server-config-section">
        </div>
        <div id="server-config-section" class="config-section">
            {config_html(server_configs)}
        </div>
    </div>

    <!-- ═══ SERVER PROPERTIES ═══ -->
    <div id="server-properties" class="tab-panel">
        <h2>Server Properties <span class="count">({len(changed_props)} changed from default)</span></h2>

        <h3 style="margin-top:1.5rem; color:var(--accent);">Non-Default Settings</h3>
        <p style="color:var(--text-dim); margin-bottom:1rem;">These values differ from the Minecraft 1.21 defaults.</p>
        <div class="props-table-wrap">
            <table id="props-changed-table" class="props-table">
                <thead>
                    <tr>
                        <th>Property</th>
                        <th>Value</th>
                        <th>Default</th>
                        <th>Explanation</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""<tr class="prop-changed">
                        <td><code>{html.escape(p['key'])}</code></td>
                        <td><strong>{html.escape(p['value'])}</strong></td>
                        <td class="prop-default">{html.escape(str(p['default']))}</td>
                        <td class="prop-explain">{html.escape(p['explanation'])}</td>
                    </tr>""" for p in changed_props)}
                </tbody>
            </table>
        </div>

        <h3 style="margin-top:2rem;">Full server.properties</h3>
        <p style="color:var(--text-dim); margin-bottom:1rem;">Complete file contents. Changed values are <span style="color:var(--accent);">highlighted</span>.</p>
        <div class="props-full-wrap">
            <table id="props-full-table" class="props-table">
                <thead>
                    <tr>
                        <th data-sort="text">Property <span class="sort-arrow"></span></th>
                        <th data-sort="text">Value <span class="sort-arrow"></span></th>
                        <th data-sort="text">Default <span class="sort-arrow"></span></th>
                        <th>Explanation</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""<tr class="{"prop-changed" if p['changed'] else ""}">
                        <td><code>{html.escape(p['key'])}</code></td>
                        <td>{("<strong>" + html.escape(p['value']) + "</strong>") if p['changed'] else html.escape(p['value'])}</td>
                        <td class="prop-default">{html.escape(str(p['default']))}</td>
                        <td class="prop-explain">{html.escape(p['explanation'])}</td>
                    </tr>""" for p in server_props)}
                </tbody>
            </table>
        </div>
    </div>

    <!-- ═══ GAMERULES ═══ -->
    <div id="gamerules" class="tab-panel">
        <h2>Gamerules <span class="count">({len(changed_gamerules)} changed from default)</span></h2>

        <h3 style="margin-top:1.5rem; color:var(--accent);">Non-Default Gamerules</h3>
        <p style="color:var(--text-dim); margin-bottom:1rem;">These gamerules differ from the Minecraft 1.21 defaults.</p>
        <div class="props-table-wrap">
            <table id="gamerules-changed-table" class="props-table">
                <thead>
                    <tr>
                        <th>Gamerule</th>
                        <th>Value</th>
                        <th>Default</th>
                        <th>Explanation</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""<tr class="prop-changed">
                        <td><code>{html.escape(g['display_key'])}</code></td>
                        <td><strong>{html.escape(g['value'])}</strong></td>
                        <td class="prop-default">{html.escape(str(g['default']))}</td>
                        <td class="prop-explain">{html.escape(g['explanation'])}</td>
                    </tr>""" for g in changed_gamerules)}
                </tbody>
            </table>
        </div>

        <h3 style="margin-top:2rem;">All Gamerules</h3>
        <p style="color:var(--text-dim); margin-bottom:1rem;">Complete list from level.dat. Changed values are <span style="color:var(--accent);">highlighted</span>.</p>
        <div class="props-full-wrap">
            <table id="gamerules-full-table" class="props-table">
                <thead>
                    <tr>
                        <th data-sort="text">Gamerule <span class="sort-arrow"></span></th>
                        <th data-sort="text">Value <span class="sort-arrow"></span></th>
                        <th data-sort="text">Default <span class="sort-arrow"></span></th>
                        <th>Explanation</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""<tr class="{"prop-changed" if g['changed'] else ""}">
                        <td><code>{html.escape(g['display_key'])}</code></td>
                        <td>{("<strong>" + html.escape(g['value']) + "</strong>") if g['changed'] else html.escape(g['value'])}</td>
                        <td class="prop-default">{html.escape(str(g['default']))}</td>
                        <td class="prop-explain">{html.escape(g['explanation'])}</td>
                    </tr>""" for g in gamerules)}
                </tbody>
            </table>
        </div>
    </div>

    <!-- ═══ PLAYERS ═══ -->
    <div id="players" class="tab-panel">
        <h2>Players <span class="count">({len(players)} total)</span></h2>

        <div class="player-stats-summary">
            <div class="stat-card">
                <div class="num">{format_ticks_to_time(sum(p['play_time'] for p in players))}</div>
                <div class="label">Total Playtime</div>
            </div>
            <div class="stat-card">
                <div class="num">{sum(p['deaths'] for p in players):,}</div>
                <div class="label">Total Deaths</div>
            </div>
            <div class="stat-card">
                <div class="num">{sum(p['mob_kills'] for p in players):,}</div>
                <div class="label">Mobs Killed</div>
            </div>
            <div class="stat-card">
                <div class="num">{sum(p['blocks_mined'] for p in players):,}</div>
                <div class="label">Blocks Mined</div>
            </div>
            <div class="stat-card">
                <div class="num">{sum(p['advancements'] for p in players):,}</div>
                <div class="label">Advancements Earned</div>
            </div>
        </div>

        <div class="controls">
            <input type="text" class="search-box" placeholder="Search players…" data-target="player-table">
        </div>
        <div class="player-table-wrap">
            <table id="player-table">
                <thead>
                    <tr>
                        <th data-sort="num" style="width:2.5rem;color:var(--text-dim)"># <span class="sort-arrow"></span></th>
                        <th data-sort="text">Player <span class="sort-arrow"></span></th>
                        <th data-sort="text">Registered <span class="sort-arrow"></span></th>
                        <th data-sort="text">Last Seen <span class="sort-arrow"></span></th>
                        <th data-sort="num">Playtime <span class="sort-arrow"></span></th>
                        <th data-sort="num">Deaths <span class="sort-arrow"></span></th>
                        <th data-sort="num">Mob Kills <span class="sort-arrow"></span></th>
                        <th data-sort="num">Walked <span class="sort-arrow"></span></th>
                        <th data-sort="num">Flown <span class="sort-arrow"></span></th>
                        <th data-sort="num">Mined <span class="sort-arrow"></span></th>
                        <th data-sort="num">Crafted <span class="sort-arrow"></span></th>
                        <th data-sort="num">Adv. <span class="sort-arrow"></span></th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(player_rows(players))}
                </tbody>
            </table>
        </div>
    </div>

    <!-- ═══ BEGINNER'S GUIDE ═══ -->
    <div id="beginners-guide" class="tab-panel">
        <h2>Beginner's Guide — How This Server Differs from Vanilla</h2>
        <p class="guide-intro">
            This server runs a curated set of mods on top of Minecraft 1.21.11 (Fabric). Most changes are transparent — the world just feels richer.
            Below is everything you need to know, in the order you will encounter it.
        </p>

        <!-- ── 1. First Moments ── -->
        <div class="guide-section">
            <h3>When You First Join</h3>

            <div class="guide-card">
                <div class="gc-name">EasyAuth — Server Login</div>
                <div class="gc-body">
                    The server uses its own authentication. The first time you join you will be asked to <em>/register</em> a password.
                    On future logins you will need to <em>/login</em> before you can move.
                </div>
                <div class="gc-example">Example: <code>/register mypassword mypassword</code> &nbsp;→&nbsp; then on reconnect: <code>/login mypassword</code></div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Origins: Legacy — Choose Your Origin</div>
                <div class="gc-body">
                    Right after spawning you will see a selection screen. Pick an <em>Origin</em> — each one gives you
                    unique abilities and trade-offs. For example, the <em>Avian</em> can slow-fall but is scared of the dark,
                    while the <em>Merling</em> breathes underwater but cannot survive on land for long.
                    You can change your origin later with the <em>/origin set</em> command if the server allows it.
                </div>
                <div class="guide-img-wrap">
                    <img src="origins_choice.jpeg" alt="Origins choice screen">
                </div>
            </div>
{ai_guide_cards["first-join"]}
        </div>

        <!-- ── 2. Gameplay Changes ── -->
        <div class="guide-section">
            <h3>Gameplay Changes</h3>

            <div class="guide-card">
                <div class="gc-name">FallingTree — Instant Tree Felling</div>
                <div class="gc-body">
                    Break <em>one log</em> of a tree and the entire tree comes down at once, dropping all its logs.
                    No more pillaring up 30 blocks to get the top of a jungle tree.
                </div>
                <div class="gc-example">Just break the bottom log with an axe — the whole tree falls.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Carry On — Pick Up &amp; Move Blocks</div>
                <div class="gc-body">
                    Shift + right-click a chest, spawner, or even a mob to <em>pick it up</em> and carry it.
                    Right-click again to place it back down. Great for reorganising bases or relocating villagers.
                </div>
                <div class="gc-example">Sneak + right-click a chest → it goes on your back → place it somewhere else.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Harvest With Ease — Right-Click Harvesting</div>
                <div class="gc-body">
                    Right-click a fully grown crop to <em>harvest and auto-replant</em> it in one action.
                    No need to break and re-place seeds.
                </div>
                <div class="gc-example">Walk through your wheat farm holding right-click — everything gets harvested and replanted.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">No Expensive — No Anvil Cap</div>
                <div class="gc-body">
                    In vanilla, the anvil refuses repairs/renames after the cost exceeds 39 levels ("Too Expensive!").
                    This mod <em>removes that cap</em>, so you can keep repairing your favourite tools.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Lootr — Per-Player Loot</div>
                <div class="gc-body">
                    Loot chests in dungeons, temples, and shipwrecks are <em>unique per player</em>.
                    Everyone gets their own loot — no more racing to be first.
                    Lootr chests have a sparkle effect so you can tell them apart.
                </div>
                <div class="gc-example">Two players open the same dungeon chest → each sees different, untouched loot.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Chunk Loaders — Keep Areas Active</div>
                <div class="gc-body">
                    Craft a <em>Chunk Loader</em> block to keep nearby chunks loaded even when no players are around.
                    Useful for farms, redstone machines, and mob grinders that need to run 24/7.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Beacon Range Extender</div>
                <div class="gc-body">
                    Beacons have a <em>larger effective range</em> than vanilla, so you can cover your entire base
                    with a single beacon.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Brewery — Craft Drinks</div>
                <div class="gc-body">
                    Adds a full <em>brewing system</em> using cauldrons, brewing stands, and barrels.
                    Cook ingredients in a cauldron over fire, distil in a brewing stand, then age in wooden barrels
                    to produce drinks with various effects. Different recipes, cook times, and barrel types
                    yield different quality levels.
                </div>
                <div class="gc-example">Place a cauldron over fire → add wheat → wait 8 min → bottle it → distil → age in a barrel → wheat beer.</div>
            </div>
{ai_guide_cards["gameplay"]}
        </div>

        <!-- ── 3. Travel & Navigation ── -->
        <div class="guide-section">
            <h3>Travel &amp; Navigation</h3>

            <div class="guide-card">
                <div class="gc-name">Waystones — Fast Travel</div>
                <div class="gc-body">
                    You will find <em>Waystones</em> scattered around the world (often in villages).
                    Right-click one to activate it, then right-click any other Waystone to teleport there.
                    You can also craft your own.
                </div>
                <div class="gc-example">Find a Waystone in a village → activate it → teleport back to it from any other Waystone.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Xaero's Minimap &amp; World Map</div>
                <div class="gc-body">
                    A <em>minimap</em> shows in the corner of your screen with nearby terrain, mobs, and players.
                    Press <em>M</em> to open a full-screen world map of everywhere you have explored.
                    You can set waypoints by pressing <em>B</em>.
                </div>
                <div class="gc-example">Press B to create a waypoint at your current location. Press M to see the full map.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Bobby — Extended Render Distance</div>
                <div class="gc-body">
                    Bobby caches chunks on your computer so you can see <em>far beyond the server's view distance</em>.
                    The server might send 10 chunks, but Bobby remembers and renders chunks you have already visited.
                </div>
            </div>
{ai_guide_cards["travel"]}
        </div>

        <!-- ── 4. World Generation ── -->
        <div class="guide-section">
            <h3>The World Looks Different</h3>
            <p class="guide-intro" style="margin-top:0">
                The terrain, biomes, and structures are heavily enhanced. The world still feels like Minecraft,
                but exploration is far more rewarding.
            </p>

            <div class="guide-card">
                <div class="gc-name">Tectonic — Terrain Overhaul</div>
                <div class="gc-body">
                    Terrain generation is dramatically different: towering mountains, deep valleys, winding rivers,
                    and vast cave systems. The landscape feels much more <em>epic and varied</em> than vanilla.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Promenade — New Biomes &amp; Mobs</div>
                <div class="gc-body">
                    Adds new biomes like cherry oak forests, dark amaranth forests (Nether), and glacarian taiga.
                    Some come with <em>unique mobs and building blocks</em>.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Incendium — Nether Overhaul</div>
                <div class="gc-body">
                    The Nether is completely reimagined with <em>new biomes, structures, mobs, and loot</em>.
                    Expect huge fortresses, volcanic caves, and quartz palaces.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Structures Everywhere</div>
                <div class="gc-body">
                    A large collection of structure mods populates the world with things to find:
                    <em>Structory</em> (ruins, towers), <em>Explorify</em> (small vanilla-style builds),
                    <em>Dungeons &amp; Taverns</em> (underground dungeons and roadside taverns),
                    <em>ChoiceTheorem's Overhauled Village</em> (completely redesigned villages for every biome),
                    <em>Towns &amp; Towers</em> (pillager outpost variants), and the
                    <em>Moogs</em> series (End, Nether, sky, voyager, temple, and village structures).
                    Exploration is the main loop — there is always something new over the next hill.
                </div>
            </div>
{ai_guide_cards["world-gen"]}
        </div>

        <!-- ── 5. UI & Information ── -->
        <div class="guide-section">
            <h3>UI &amp; Information</h3>

            <div class="guide-card">
                <div class="gc-name">JEI — Just Enough Items</div>
                <div class="gc-body">
                    Press <em>E</em> to open your inventory and you will see a searchable item list on the right.
                    Click any item to see its <em>crafting recipe</em>. Press <em>U</em> on an item to see what it is used in.
                    Essential for looking up recipes from modded content.
                </div>
                <div class="gc-example">Hover over an item → press R to see how to craft it, U to see what uses it.</div>
            </div>

            <div class="guide-card">
                <div class="gc-name">WTHIT — "What The Hell Is That"</div>
                <div class="gc-body">
                    A small tooltip at the top of the screen tells you <em>what block or mob you are looking at</em>,
                    including its mod source and, for some blocks, extra info like crop growth stage or chest contents.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Enchantment Descriptions</div>
                <div class="gc-body">
                    Hover over an enchanted item and the tooltip now shows a <em>plain-English description</em>
                    of what each enchantment does. No more guessing what "Impaling V" means.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">BetterF3</div>
                <div class="gc-body">
                    The F3 debug screen is redesigned to be <em>colour-coded and easier to read</em>,
                    with information grouped into clear sections.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Chat Heads</div>
                <div class="gc-body">
                    Chat messages show the sender's <em>player head</em> icon next to their name,
                    making it easier to tell who said what.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Zoomify — Zoom Key</div>
                <div class="gc-body">
                    Press <em>C</em> to zoom in like a spyglass, without needing one.
                    Works just like the old OptiFine zoom.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Villager Names</div>
                <div class="gc-body">
                    Villagers have <em>random human names</em> displayed above their heads,
                    making it easier to keep track of your villager trading hall.
                </div>
            </div>

            <div class="guide-card">
                <div class="gc-name">Iris Shaders + Sodium</div>
                <div class="gc-body">
                    Sodium replaces the rendering engine for much better FPS, and Iris lets you load
                    <em>shader packs</em> (like Complementary or BSL) for beautiful lighting, water, and shadows.
                    Press <em>K</em> to toggle shaders on/off.
                </div>
            </div>
{ai_guide_cards["ui"]}
        </div>

        <!-- ── 6. Vanilla Tweaks & Datapacks ── -->
        <div class="guide-section">
            <h3>Vanilla Tweaks &amp; Datapacks</h3>
            <p class="guide-intro" style="margin-top:0">
                Several <em>datapacks</em> add quality-of-life features and extra recipes without any client mods needed.
                These are all server-side — they just work.
            </p>

            <div class="guide-card">
                <div class="gc-name">Graves</div>
                <div class="gc-body">
                    When you die, your items are stored in a <strong>grave block</strong> at the death location instead of scattering on the ground.
                    Walk up to it and right-click (or just walk over it) to reclaim everything. No more losing items in lava!
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">/back</div>
                <div class="gc-body">
                    Type <code>/trigger back</code> in chat to teleport to your <strong>last death location</strong>.
                    Handy for getting back to your grave quickly.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">/tpa (Teleport Request)</div>
                <div class="gc-body">
                    Request a teleport to another player: <code>/trigger tpa</code> then follow the prompts.
                    The other player must accept before you are teleported.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">AFK Display</div>
                <div class="gc-body">
                    If you stand still for a while your name turns <em>grey</em> in the tab list so others know you are AFK.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">Villager Workstation Highlights</div>
                <div class="gc-body">
                    When you look at a villager, their linked workstation block gets a <strong>glowing outline</strong>,
                    making it easy to figure out which workstation belongs to which villager.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">XP Bottling</div>
                <div class="gc-body">
                    Stand on an <strong>enchanting table</strong> and throw a <em>glass bottle</em> to convert your XP into a
                    <strong>Bottle o&rsquo; Enchanting</strong>. Great for storing experience safely.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">Rotation Wrenches</div>
                <div class="gc-body">
                    Craft a <em>Redstone Rotation Wrench</em> or <em>Terracotta Rotation Wrench</em> to rotate
                    redstone components and glazed terracotta blocks in-place without breaking them.
                </div>
            </div>
            <div class="guide-card">
                <div class="gc-name">Orb of Origin Recipe</div>
                <div class="gc-body">
                    Normally you can only pick your Origin once. A custom recipe lets you craft an
                    <strong>Orb of Origin</strong> (expensive — requires a Nether Star, Netherite Ingot, Mace, Eye of Ender,
                    Heart of the Sea, Shulker Box, and Echo Shard) to re-choose your Origin.
                </div>
            </div>

            <h4 style="margin-top:1.5rem; color:var(--text-dim);">Extra Crafting Recipes (Vanilla Tweaks bundle)</h4>
            <div class="guide-card">
                <div class="gc-body">
                    <strong>Craftable Blackstone</strong> — Smelt basalt to get blackstone.<br>
                    <strong>Copper Powered Rails</strong> — Craft powered rails with copper ingots instead of gold.<br>
                    <strong>Rotten Flesh to Leather</strong> — Smelt rotten flesh into leather.<br>
                    <strong>Powder to Glass</strong> — Smelt concrete powder directly into stained glass.<br>
                    <strong>Sandstone Dyeing</strong> — Dye sandstone variants between regular and red.<br>
                    <strong>Universal Dyeing</strong> — Re-dye already-dyed blocks (wool, concrete, beds, etc.).<br>
                    <strong>Wool to String</strong> — Place wool in a crafting grid to get string.<br>
                    <strong>Uncraft Ice</strong> — Craft packed/blue ice back into regular ice.<br>
                    <strong>Uncraft Nether Wart Block</strong> — Craft nether wart blocks back into nether wart.<br>
                    <strong>More Stairs</strong> — Stair recipes for additional block types.<br>
                    <strong>Slabs &amp; Stairs to Blocks</strong> — Combine slabs or stairs back into full blocks.
                </div>
            </div>
{ai_guide_cards["datapacks"]}
        </div>

        <!-- ── 7. Behind the Scenes ── -->
        <div class="guide-section">
            <h3>Behind the Scenes (Server Performance)</h3>
            <p class="guide-intro" style="margin-top:0">
                You do not need to think about these, but they are why the server runs smoothly:
            </p>

            <div class="guide-card">
                <div class="gc-body">
                    <strong>Lithium</strong> — Optimises game logic, mob AI, and world ticking.<br>
                    <strong>C2ME</strong> — Multi-threaded chunk loading for faster terrain generation.<br>
                    <strong>FerriteCore</strong> — Reduces the server's memory usage.<br>
                    <strong>Krypton</strong> — Optimises the networking stack for lower latency.<br>
                    <strong>Alternate Current</strong> — Faster redstone calculations.<br>
                    <strong>AntiXray</strong> — Hides ores from X-ray cheats.<br>
                    <strong>PacketFixer</strong> — Prevents disconnects from oversized packets.<br>
                    <strong>Sparse Structures</strong> — Spreads structures out so they don't cluster unnaturally.
                </div>
            </div>
{ai_guide_cards["behind-scenes"]}
        </div>

    </div>
</div>

<script>
// ── Theme toggle ──
(function() {{
    const toggle = document.getElementById('theme-toggle');
    const root = document.documentElement;
    const saved = localStorage.getItem('theme');
    if (saved === 'light') {{
        root.setAttribute('data-theme', 'light');
        toggle.textContent = 'DARK MODE';
    }}
    toggle.addEventListener('click', () => {{
        const isLight = root.getAttribute('data-theme') === 'light';
        if (isLight) {{
            root.removeAttribute('data-theme');
            toggle.textContent = 'LIGHT MODE';
            localStorage.setItem('theme', 'dark');
        }} else {{
            root.setAttribute('data-theme', 'light');
            toggle.textContent = 'DARK MODE';
            localStorage.setItem('theme', 'light');
        }}
    }});
}})();

// ── Tab switching ──
document.querySelectorAll('.tab').forEach(tab => {{
    tab.addEventListener('click', () => {{
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.tab).classList.add('active');
    }});
}});

// ── Search ──
document.querySelectorAll('.search-box[data-target]').forEach(box => {{
    box.addEventListener('input', () => {{
        const q = box.value.toLowerCase();
        const target = box.dataset.target;

        // Table search
        const table = document.getElementById(target);
        if (table) {{
            table.querySelectorAll('tbody tr').forEach(row => {{
                const text = row.textContent.toLowerCase();
                row.classList.toggle('hidden', !text.includes(q));
            }});
        }}

        // Config search
        const section = document.getElementById(target);
        if (section && section.classList.contains('config-section')) {{
            section.querySelectorAll('.config-block').forEach(block => {{
                const text = block.textContent.toLowerCase();
                block.classList.toggle('hidden', !text.includes(q));
            }});
        }}
    }});
}});

// ── Config search ──
document.querySelectorAll('.config-search').forEach(box => {{
    box.addEventListener('input', () => {{
        const q = box.value.toLowerCase();
        const section = document.getElementById(box.dataset.target);
        if (section) {{
            section.querySelectorAll('.config-block').forEach(block => {{
                const text = block.textContent.toLowerCase();
                block.classList.toggle('hidden', !text.includes(q));
            }});
        }}
    }});
}});

// ── Filter buttons ──
document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
        const target = btn.dataset.target;
        const filter = btn.dataset.filter;

        // Toggle active state within same group
        btn.parentElement.querySelectorAll('.filter-btn').forEach(b => {{
            if (b.dataset.target === target) b.classList.remove('active');
        }});
        btn.classList.add('active');

        const table = document.getElementById(target);
        if (table) {{
            table.querySelectorAll('tbody tr').forEach(row => {{
                if (filter === 'all') {{
                    row.classList.remove('hidden');
                }} else {{
                    row.classList.toggle('hidden', row.dataset.req !== filter);
                }}
            }});
        }}
    }});
}});

// ── Column sorting ──
document.querySelectorAll('th[data-sort]').forEach(th => {{
    th.addEventListener('click', () => {{
        const table = th.closest('table');
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const col = th.dataset.sort;
        const idx = Array.from(th.parentElement.children).indexOf(th);

        const currentDir = th.getAttribute('data-dir') || 'asc';
        const newDir = currentDir === 'asc' ? 'desc' : 'asc';

        // Reset all arrows in this table
        th.closest('thead').querySelectorAll('th').forEach(h => {{
            h.removeAttribute('data-dir');
            h.querySelector('.sort-arrow').textContent = '';
        }});
        th.setAttribute('data-dir', newDir);
        th.querySelector('.sort-arrow').textContent = newDir === 'asc' ? '▲' : '▼';

        rows.sort((a, b) => {{
            let va, vb;
            if (col === 'req') {{
                va = a.dataset.req;
                vb = b.dataset.req;
            }} else if (col === 'side') {{
                va = a.dataset.side;
                vb = b.dataset.side;
            }} else if (col === 'num') {{
                const cellA = a.children[idx];
                const cellB = b.children[idx];
                const na = parseFloat(cellA.getAttribute('data-val')) || 0;
                const nb = parseFloat(cellB.getAttribute('data-val')) || 0;
                let cmp = na - nb;
                return newDir === 'asc' ? cmp : -cmp;
            }} else {{
                const cellA = a.children[idx];
                const cellB = b.children[idx];
                va = (cellA.getAttribute('data-val') !== null ? cellA.getAttribute('data-val') : cellA.textContent).toLowerCase();
                vb = (cellB.getAttribute('data-val') !== null ? cellB.getAttribute('data-val') : cellB.textContent).toLowerCase();
            }}
            let cmp = va.localeCompare(vb);
            return newDir === 'asc' ? cmp : -cmp;
        }});

        rows.forEach(r => tbody.appendChild(r));
    }});
}});

// ── Expand/collapse all configs ──
document.addEventListener('keydown', e => {{
    if (e.key === 'e' && e.altKey) {{
        const activePanel = document.querySelector('.tab-panel.active');
        const blocks = activePanel.querySelectorAll('.config-block');
        const allOpen = Array.from(blocks).every(b => b.classList.contains('open'));
        blocks.forEach(b => b.classList.toggle('open', !allOpen));
    }}
}});
</script>

</body>
</html>'''

    return page


if __name__ == "__main__":
    # Check Ollama availability
    if _ollama_available():
        print(f"[AI] Ollama available (model: {OLLAMA_MODEL})")
    else:
        print(f"[AI] Ollama not available at {OLLAMA_URL}")
        print(f"     To enable AI auto-fill: ollama pull {OLLAMA_MODEL}")
        print(f"     Unknown mods/datapacks will have empty descriptions.")
    output = os.path.join(SCRIPT_DIR, "index.html")
    with open(output, "w") as f:
        f.write(generate_html())
    print(f"Generated: {output}")
    print(f"Open in browser: file://{output}")
