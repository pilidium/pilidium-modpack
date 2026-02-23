#!/usr/bin/env python3
"""
Fetches mod/datapack information from Modrinth API and Ollama, storing
everything in mod_data.json.  Run this whenever mods change.

Pipeline:
  1. Scan client .index/*.pw.toml for Modrinth IDs  → batch-fetch from API
  2. Scan server jars → search Modrinth by name     → fetch from API
  3. Anything not found on Modrinth                  → Ollama qwen2.5:14b fallback
  4. Read all config files and map them to mods
  5. Write mod_data.json  (never hardcoded in generate_modlist.py)
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse

# ── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(SCRIPT_DIR)

CLIENT_MODS = os.path.join(BASE, "minecraft", "mods")
SERVER_MODS = os.path.join(BASE, "server", "mods")
CLIENT_INDEX = os.path.join(CLIENT_MODS, ".index")
CLIENT_CONFIG = os.path.join(BASE, "minecraft", "config")
SERVER_CONFIG = os.path.join(BASE, "server", "config")
SERVER_DIR = os.path.join(BASE, "server")
DATAPACK_DIR = os.path.join(SERVER_DIR, "world", "datapacks")

OUTPUT_FILE = os.path.join(SCRIPT_DIR, "mod_data.json")

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_HEADERS = {"User-Agent": "pilidium-modpack-dashboard/1.0 (github.com/pilidium/pilidium-modpack)"}

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# ── Jar-name overrides for server mods with non-standard filenames ──────────
# Maps jar filename → (display name, version)
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
    "MoogsEndStructures-1.21-2.0.1.jar": ("Moogs End Structures", "2.0.1"),
    "MoogsMissingVillages-1.21-2.0.0.jar": ("Moogs Missing Villages", "2.0.0"),
    "MoogsNetherStructures-1.21-2.0.31.jar": ("Moogs Nether Structures", "2.0.31"),
    "MoogsSoaringStructures-1.21-2.0.2.jar": ("Moogs Soaring Structures", "2.0.2"),
    "MoogsTemplesReimagined-1.21-1.1.0.jar": ("Moogs Temples Reimagined", "1.1.0"),
    "MoogsVoyagerStructures-1.21-5.0.5.jar": ("Moogs Voyager Structures", "5.0.5"),
    "ForgeConfigAPIPort-v21.11.1-mc1.21.11-Fabric.jar": ("Forge Config API Port", "21.11.1"),
    "worldedit-mod-7.4.0.jar": ("WorldEdit", "7.4.0"),
    "AdditionalStructures-1.21-(v.5.2.0-FABRIC)-dev.jar": ("Additional Structures", "5.2.0"),
}

# ── Known Modrinth slugs for mods that aren't found by name search ──────────
# Maps mod name (lowercase) → Modrinth slug or project ID
MODRINTH_SLUG_MAP = {
    "choicetheorem's overhauled village": "ct-overhaul-village",
    "towns and towers": "towns-and-towers",
    "no expensive": "no-expensive",
    "beacon range extender": "beacon-range-extender",
    "forge config api port": "forge-config-api-port",
    "moogs end structures": "mes-moogs-end-structures",
    "moogs missing villages": "mmv-moogs-missing-villages",
    "moogs nether structures": "mns-moogs-nether-structures",
    "moogs soaring structures": "mss-moogs-soaring-structures",
    "moogs temples reimagined": "mtr-moogs-temples-reimagined",
    "moogs voyager structures": "moogs-voyager-structures",
    "moogs structure lib": "moogs-structure-lib",
    "worldedit": "worldedit",
    "worldedit mod": "worldedit",
    "additional structures": "additional-structures",
    "c2me": "c2me-fabric",
}

# ── Manual overrides for mods not on Modrinth ──────────────────────────────
MANUAL_MOD_INFO = {
    "abridged": {
        "title": "Abridged",
        "desc": "Shortens & cleans up server join/leave messages",
        "side": "server",
        "req": "optional",
        "source": "manual",
    },
}

# Rate limiting: Modrinth asks for ≤300 req/min
_last_modrinth_call = 0.0


# ── Modrinth helpers ────────────────────────────────────────────────────────
def _modrinth_get(path, params=None):
    """GET request to Modrinth API with rate-limit politeness."""
    global _last_modrinth_call
    elapsed = time.time() - _last_modrinth_call
    if elapsed < 0.25:
        time.sleep(0.25 - elapsed)

    url = f"{MODRINTH_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=MODRINTH_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _last_modrinth_call = time.time()
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f"  [Modrinth] Request failed: {e}")
        _last_modrinth_call = time.time()
        return None


def modrinth_batch_projects(ids):
    """Fetch multiple projects by Modrinth IDs in one call (max 100)."""
    if not ids:
        return []
    encoded = json.dumps(ids)
    return _modrinth_get("/projects", {"ids": encoded}) or []


def modrinth_search(query, loader="fabric", limit=5):
    """Search Modrinth for a mod by name."""
    facets = json.dumps([["categories:" + loader]])
    return _modrinth_get("/search", {"query": query, "facets": facets, "limit": str(limit)})


def _extract_modrinth_info(project):
    """Extract relevant fields from a Modrinth project response."""
    # Map Modrinth side values to our side values
    client = project.get("client_side", "unknown")
    server = project.get("server_side", "unknown")
    if client in ("required", "optional") and server in ("required", "optional"):
        side = "both"
    elif client in ("required", "optional"):
        side = "client"
    elif server in ("required", "optional"):
        side = "server"
    else:
        side = "both"

    # Determine req/optional/library from categories
    categories = [c.lower() for c in project.get("categories", [])]
    additional_categories = [c.lower() for c in project.get("additional_categories", [])]
    all_cats = categories + additional_categories
    if "library" in all_cats or "api" in all_cats:
        req = "library"
    else:
        req = "optional"  # Default; generate_modlist.py can override

    return {
        "modrinth_id": project.get("id", ""),
        "slug": project.get("slug", ""),
        "title": project.get("title", ""),
        "desc": project.get("description", ""),
        "side": side,
        "req": req,
        "categories": all_cats,
        "icon_url": project.get("icon_url", ""),
        "source_url": project.get("source_url", ""),
        "wiki_url": project.get("wiki_url", ""),
        "issues_url": project.get("issues_url", ""),
        "source": "modrinth",
    }


# ── Ollama helpers ──────────────────────────────────────────────────────────
def _ollama_available():
    """Check if Ollama is reachable and has the model."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            # Accept both "qwen2.5:14b" and "qwen2.5:14b-instruct" etc.
            base_model = OLLAMA_MODEL.split(":")[0]
            return any(base_model in m for m in models)
    except Exception:
        return False


def _ollama_query(prompt):
    """Send a prompt to Ollama and return parsed JSON or None."""
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
            raw = result.get("response", "")
            return json.loads(raw)
    except Exception as e:
        print(f"  [Ollama] Request failed: {e}")
        return None


def ollama_mod_info(name, jar_filename, side_hint="unknown"):
    """Get mod info from Ollama."""
    prompt = (
        "You are a Minecraft Fabric modding expert. "
        "I need information about this Fabric mod.\n\n"
        f"Mod name: {name}\n"
        f"Jar filename: {jar_filename}\n"
        f"Installed on: {side_hint} side\n\n"
        "Return ONLY a JSON object with these exact fields:\n"
        "{\n"
        '  "desc": "One concise sentence describing what this mod does (max 120 chars)",\n'
        '  "side": "client" or "server" or "both",\n'
        '  "req": "required" or "optional" or "library"\n'
        "}\n\n"
        "Rules:\n"
        '- "req" = "library" for API/library mods players never interact with\n'
        '- "req" = "required" for mods that add content players must deal with\n'
        '- "req" = "optional" for quality-of-life mods\n'
        "- Keep the description concise and beginner-friendly\n"
        "- If unsure, mark as optional"
    )
    print(f"  [Ollama] Querying for mod: {name} ...")
    data = _ollama_query(prompt)
    if not data or "desc" not in data:
        return None
    # Validate
    if data.get("side") not in ("client", "server", "both"):
        data["side"] = side_hint if side_hint != "unknown" else "both"
    if data.get("req") not in ("required", "optional", "library"):
        data["req"] = "optional"
    data["source"] = "ollama"
    data["title"] = name
    return data


def ollama_datapack_info(pack_name):
    """Get datapack info from Ollama."""
    prompt = (
        "You are a Minecraft datapack expert. "
        "I need info about this datapack on a Fabric 1.21.11 server.\n\n"
        f"Datapack name/folder: {pack_name}\n\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "desc": "One concise sentence describing what this datapack does"\n'
        "}\n\n"
        "Keep it concise and beginner-friendly."
    )
    print(f"  [Ollama] Querying for datapack: {pack_name} ...")
    data = _ollama_query(prompt)
    if not data or "desc" not in data:
        return None
    data["source"] = "ollama"
    return data


# ── .pw.toml parser ─────────────────────────────────────────────────────────
def parse_pw_toml(path):
    """Minimal TOML parser — enough for PrismLauncher .pw.toml files."""
    data = {}
    current_section = None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                section_match = re.match(r'^\[(.+)\]$', line)
                if section_match:
                    current_section = section_match.group(1)
                    continue
                kv = re.match(r"^(\S+)\s*=\s*'([^']*)'", line)
                if not kv:
                    kv = re.match(r'^(\S+)\s*=\s*"([^"]*)"', line)
                if not kv:
                    kv = re.match(r'^(\S+)\s*=\s*(.+)', line)
                if kv:
                    key = kv.group(1).strip()
                    val = kv.group(2).strip().strip("'\"")
                    if current_section:
                        key = f"{current_section}.{key}"
                    data[key] = val
    except Exception:
        pass
    return data


# ── Jar name parsing ────────────────────────────────────────────────────────
def jar_to_name(jar):
    """Extract a human-friendly mod name from a jar filename."""
    name = jar.replace(".jar", "")
    # Remove common prefixes
    name = re.sub(r'^\[Fabric\]', '', name)
    # Remove version part (number-heavy suffix)
    name = re.sub(r'[-_]\d+[\d.+a-zA-Z_-]*$', '', name)
    # Remove mc version patterns
    name = re.sub(r'[-_]mc\d+[\d.]*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]fabric[-_]?', '-', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]forge[-_]?', '-', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]neoforge[-_]?', '-', name, flags=re.IGNORECASE)
    # Clean separators
    name = name.replace('-', ' ').replace('_', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    # Title case
    name = name.title()
    return name


# ── Config mapping ──────────────────────────────────────────────────────────
def scan_configs(config_dir):
    """Scan a config directory and return list of relative paths."""
    configs = []
    if not os.path.isdir(config_dir):
        return configs
    for root, dirs, files in os.walk(config_dir):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, config_dir)
            configs.append(rel)
    return sorted(configs)


# ── Datapack scanning ───────────────────────────────────────────────────────
def scan_datapacks():
    """Scan the datapacks directory and return cleaned names."""
    packs = []
    if not os.path.isdir(DATAPACK_DIR):
        return packs
    for entry in sorted(os.listdir(DATAPACK_DIR)):
        full = os.path.join(DATAPACK_DIR, entry)
        if not os.path.isdir(full):
            continue
        # Clean version suffixes
        name = re.sub(r'\s*v\d+[\d.]*\s*\(MC[^)]*\)$', '', entry).strip()
        name = re.sub(r'\s*v\d+[\d.]*$', '', name).strip()
        packs.append({"folder": entry, "name": name})
    return packs


# ── Main pipeline ───────────────────────────────────────────────────────────
def load_existing():
    """Load existing mod_data.json if present."""
    if os.path.isfile(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"mods": {}, "datapacks": {}, "config_map": {}}


def save_data(data):
    """Save mod_data.json."""
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Saved {OUTPUT_FILE}")


def main():
    print("=" * 60)
    print("  Pilidium Mod Data Fetcher")
    print("=" * 60)

    existing = load_existing()
    data = {
        "mods": existing.get("mods", {}),
        "datapacks": existing.get("datapacks", {}),
        "config_map": {},
    }

    # ── Step 1: Collect client mods from .index ─────────────────────────────
    print("\n[1/5] Scanning client mods (.index)...")
    client_mods = []  # (name, jar, modrinth_id)
    if os.path.isdir(CLIENT_INDEX):
        for f in sorted(os.listdir(CLIENT_INDEX)):
            if not f.endswith(".pw.toml"):
                continue
            meta = parse_pw_toml(os.path.join(CLIENT_INDEX, f))
            name = meta.get("name", f.replace(".pw.toml", ""))
            jar = meta.get("filename", "")
            mid = meta.get("update.modrinth.mod-id", "")
            client_mods.append((name, jar, mid, "client"))
            print(f"  {name}" + (f" [modrinth:{mid}]" if mid else " [no modrinth id]"))
    print(f"  Found {len(client_mods)} client mods")

    # ── Step 2: Collect server mods from jar files ──────────────────────────
    print("\n[2/5] Scanning server mods (jars)...")
    server_mods = []  # (name, jar, modrinth_id)
    if os.path.isdir(SERVER_MODS):
        for jar in sorted(os.listdir(SERVER_MODS)):
            if not jar.endswith(".jar"):
                continue
            if jar in JAR_NAME_OVERRIDES:
                name = JAR_NAME_OVERRIDES[jar][0]
            else:
                name = jar_to_name(jar)
            server_mods.append((name, jar, "", "server"))
            print(f"  {name} ({jar})")
    print(f"  Found {len(server_mods)} server mods")

    # ── Step 3: Fetch from Modrinth ─────────────────────────────────────────
    print("\n[3/5] Fetching mod info from Modrinth...")

    # Apply manual overrides first
    for key, info in MANUAL_MOD_INFO.items():
        if key not in data["mods"]:
            data["mods"][key] = info
            print(f"  [manual] {info.get('title', key)}: {info.get('desc', '')[:60]}...")

    # 3a: Batch-fetch client mods that have Modrinth IDs
    modrinth_ids = [mid for _, _, mid, _ in client_mods if mid]
    if modrinth_ids:
        print(f"  Batch-fetching {len(modrinth_ids)} client mods by ID...")
        # Modrinth batch limit is ~100 but we have <30
        projects = modrinth_batch_projects(modrinth_ids)
        id_to_project = {p["id"]: p for p in projects} if projects else {}

        for name, jar, mid, side in client_mods:
            key = name.lower().strip()
            if mid and mid in id_to_project:
                info = _extract_modrinth_info(id_to_project[mid])
                info["_jar"] = jar
                info["_side_hint"] = side
                data["mods"][key] = info
                print(f"  [OK] {name}: {info['desc'][:60]}...")
            elif key not in data["mods"]:
                print(f"  [--] {name}: not in batch response")

    # 3b: Search Modrinth for mods that don't have IDs (server mods + missed client mods)
    all_mods = client_mods + server_mods
    missing = [(n, j, s) for n, j, mid, s in all_mods if n.lower().strip() not in data["mods"]]

    if missing:
        print(f"\n  Searching Modrinth for {len(missing)} remaining mods...")
        for name, jar, side in missing:
            key = name.lower().strip()
            if key in data["mods"]:
                continue

            # Try direct slug/ID lookup first
            slug = MODRINTH_SLUG_MAP.get(key)
            if slug:
                project = _modrinth_get(f"/project/{slug}")
                if project:
                    info = _extract_modrinth_info(project)
                    info["_jar"] = jar
                    info["_side_hint"] = side
                    data["mods"][key] = info
                    print(f"  [OK] {name} (slug): {info['desc'][:60]}...")
                    continue

            # Fall back to search
            result = modrinth_search(name)
            if result and result.get("hits"):
                # Find the best match (closest title match)
                best = None
                name_lower = name.lower().replace(" ", "").replace("'", "")
                for hit in result["hits"]:
                    slug_h = hit.get("slug", "").lower().replace("-", "")
                    title = hit.get("title", "").lower().replace(" ", "").replace("'", "")
                    if slug_h == name_lower or title == name_lower or name_lower in title or title in name_lower:
                        best = hit
                        break
                if not best:
                    # Try partial match — require at least 2 words matching
                    for hit in result["hits"]:
                        slug_h = hit.get("slug", "").lower().replace("-", "")
                        title = hit.get("title", "").lower().replace(" ", "")
                        words = name_lower.replace("'", "").split()
                        if len(words) >= 2 and all(w in title or w in slug_h for w in words[:2]):
                            best = hit
                            break

                if best:
                    # Fetch full project details
                    project = _modrinth_get(f"/project/{best['project_id']}")
                    if project:
                        info = _extract_modrinth_info(project)
                        info["_jar"] = jar
                        info["_side_hint"] = side
                        data["mods"][key] = info
                        print(f"  [OK] {name}: {info['desc'][:60]}...")
                        continue

            print(f"  [--] {name}: not found on Modrinth")

    # ── Step 4: Ollama fallback for remaining ───────────────────────────────
    print("\n[4/5] Ollama fallback for missing mods...")
    still_missing = [(n, j, s) for n, j, mid, s in all_mods if n.lower().strip() not in data["mods"]]

    has_ollama = _ollama_available()
    if not has_ollama:
        if still_missing:
            print(f"  [!] Ollama not available. {len(still_missing)} mods will have no descriptions.")
            print(f"      To fix: ollama pull {OLLAMA_MODEL}")
        else:
            print("  All mods covered by Modrinth data. Ollama not needed.")
    else:
        print(f"  Using model: {OLLAMA_MODEL}")
        for name, jar, side in still_missing:
            key = name.lower().strip()
            info = ollama_mod_info(name, jar, side)
            if info:
                data["mods"][key] = info
                print(f"  [OK] {name}: {info['desc'][:60]}...")
            else:
                data["mods"][key] = {
                    "title": name,
                    "desc": "",
                    "side": side,
                    "req": "unknown",
                    "source": "unknown",
                }
                print(f"  [!!] {name}: Ollama failed, saved with empty desc")

    # Also handle datapacks
    print("\n  Checking datapacks...")
    packs = scan_datapacks()
    for pack in packs:
        key = pack["name"].lower().strip()
        if key not in data["datapacks"]:
            if has_ollama:
                info = ollama_datapack_info(pack["name"])
                if info:
                    data["datapacks"][key] = info
                    print(f"  [OK] Datapack {pack['name']}: {info['desc'][:60]}...")
                    continue
            data["datapacks"][key] = {"desc": "", "source": "unknown"}
            print(f"  [--] Datapack {pack['name']}: no info")

    # ── Step 5: Scan configs ────────────────────────────────────────────────
    print("\n[5/5] Scanning config files...")
    client_configs = scan_configs(CLIENT_CONFIG)
    server_configs = scan_configs(SERVER_CONFIG)
    data["config_map"] = {
        "client": client_configs,
        "server": server_configs,
    }
    print(f"  {len(client_configs)} client configs, {len(server_configs)} server configs")

    # ── Summary ─────────────────────────────────────────────────────────────
    modrinth_count = sum(1 for v in data["mods"].values() if v.get("source") == "modrinth")
    ollama_count = sum(1 for v in data["mods"].values() if v.get("source") == "ollama")
    unknown_count = sum(1 for v in data["mods"].values() if v.get("source") == "unknown")
    no_desc = sum(1 for v in data["mods"].values() if not v.get("desc"))

    print(f"\n{'=' * 60}")
    print(f"  Total mods:     {len(data['mods'])}")
    print(f"  From Modrinth:  {modrinth_count}")
    print(f"  From Ollama:    {ollama_count}")
    print(f"  Unknown/empty:  {unknown_count}")
    if no_desc:
        print(f"  Missing desc:   {no_desc}")
    print(f"  Datapacks:      {len(data['datapacks'])}")
    print(f"{'=' * 60}")

    save_data(data)


if __name__ == "__main__":
    main()
