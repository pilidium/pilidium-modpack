#!/usr/bin/env python3
"""
Syncs Minecraft server data to GitHub for the Vercel-hosted dashboard.

Generates data.json + configs.json, pushes to the 'data' branch,
and purges the jsDelivr CDN cache.

Usage:
  python3 sync_data.py            # Generate + push + purge
  python3 sync_data.py --local    # Generate local files only (no push)

Cron (every 5 minutes):
  */5 * * * * cd /home/pilidium/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances/pilidium/dashboard && python3 sync_data.py >> /tmp/pilidium-sync.log 2>&1

First-time setup:
  1. Run: python3 sync_data.py          (creates 'data' branch and pushes)
  2. Connect repo to Vercel (framework: Other, root: repo root)
  3. Add cron job above
"""

import os
import sys
import json
import subprocess
import hashlib
import urllib.request
from datetime import datetime, timezone

# Ensure imports work regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_modlist import (
    SCRIPT_DIR, SERVER_DIR, CLIENT_CONFIG, SERVER_CONFIG,
    load_mod_data,
    collect_client_mods, collect_server_mods, collect_datapacks,
    collect_configs, collect_server_properties, collect_gamerules,
    collect_players, format_ticks_to_time,
    _get_dir_size, _format_size,
)

# ── Config ──────────────────────────────────────────────────────────────────
GITHUB_REPO = "pilidium/pilidium-modpack"
DATA_BRANCH = "data"
HASH_FILE = os.path.join(SCRIPT_DIR, ".last_data_hash")


def collect_all():
    """Collect all dynamic data from the server into two dicts."""

    # Mods
    client_mods = collect_client_mods()
    server_mods = collect_server_mods()
    datapacks = collect_datapacks()

    # Configs (separate because they're large and rarely change)
    client_configs = collect_configs(CLIENT_CONFIG, "client")
    server_configs = collect_configs(SERVER_CONFIG, "server")

    # Server properties & gamerules
    server_props, changed_props, _ = collect_server_properties()
    gamerules, changed_gamerules = collect_gamerules()

    # Players
    players = collect_players()

    # World / backup sizes
    world_dir = os.path.join(SERVER_DIR, "world")
    backup_dir = os.path.join(SERVER_DIR, "world", ".git")
    world_size = _format_size(_get_dir_size(world_dir, exclude=".git"))
    backup_size = _format_size(_get_dir_size(backup_dir))

    now = datetime.now(timezone.utc).isoformat()

    # ── data.json — refreshed every 5 min by the frontend ──────────────
    data = {
        "home": {
            "client_mod_count": len(client_mods),
            "server_mod_count": len(server_mods),
            "player_count": len(players),
            "client_config_count": sum(len(v) for v in client_configs.values()),
            "server_config_count": sum(len(v) for v in server_configs.values()),
            "world_size": world_size,
            "backup_size": backup_size,
        },
        "client_mods": client_mods,
        "server_mods": server_mods,
        "datapacks": [
            {
                "name": d["name"],
                "folder": d.get("folder", ""),
                "desc": d.get("desc", ""),
                "covered": d.get("covered", False),
            }
            for d in datapacks
        ],
        "server_properties": {
            "all": server_props,
            "changed": changed_props,
        },
        "gamerules": {
            "all": gamerules,
            "changed": changed_gamerules,
        },
        "players": players,
        "player_totals": {
            "play_time_fmt": format_ticks_to_time(
                sum(p["play_time"] for p in players)
            ),
            "deaths": sum(p["deaths"] for p in players),
            "mob_kills": sum(p["mob_kills"] for p in players),
            "blocks_mined": sum(p["blocks_mined"] for p in players),
            "advancements": sum(p["advancements"] for p in players),
        },
        "updated": now,
    }

    # ── configs.json — fetched once on page load ───────────────────────
    configs = {
        "client": {
            mod: [{"path": path, "content": content} for path, content in files]
            for mod, files in sorted(client_configs.items())
        },
        "server": {
            mod: [{"path": path, "content": content} for path, content in files]
            for mod, files in sorted(server_configs.items())
        },
        "updated": now,
    }

    return data, configs


def push_data_files(files):
    """Push files to the 'data' branch using git plumbing (no branch switch).

    files: dict of {filename: content_str}
    Returns True on success.
    """
    repo = SCRIPT_DIR
    tree_lines = []

    for filename, content in files.items():
        r = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            input=content.encode(),
            capture_output=True, text=True, cwd=repo,
        )
        if r.returncode != 0:
            print(f"[!] git hash-object failed for {filename}: {r.stderr.strip()}")
            return False
        blob = r.stdout.strip()
        tree_lines.append(f"100644 blob {blob}\t{filename}")

    r = subprocess.run(
        ["git", "mktree"],
        input="\n".join(tree_lines) + "\n",
        capture_output=True, text=True, cwd=repo,
    )
    if r.returncode != 0:
        print(f"[!] git mktree failed: {r.stderr.strip()}")
        return False
    tree = r.stdout.strip()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = subprocess.run(
        ["git", "commit-tree", tree, "-m", f"data {ts}"],
        capture_output=True, text=True, cwd=repo,
    )
    if r.returncode != 0:
        print(f"[!] git commit-tree failed: {r.stderr.strip()}")
        return False
    commit = r.stdout.strip()

    subprocess.run(
        ["git", "update-ref", f"refs/heads/{DATA_BRANCH}", commit],
        cwd=repo,
    )

    r = subprocess.run(
        ["git", "push", "origin", DATA_BRANCH, "--force"],
        capture_output=True, text=True, cwd=repo,
    )
    if r.returncode != 0:
        print(f"[!] git push failed: {r.stderr.strip()}")
        return False

    print(f"[OK] Pushed to {DATA_BRANCH} ({commit[:8]})")
    return True


def purge_cdn():
    """Purge jsDelivr CDN cache for both data files."""
    for fname in ("data.json", "configs.json"):
        url = f"https://purge.jsdelivr.net/gh/{GITHUB_REPO}@{DATA_BRANCH}/{fname}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
            print(f"[OK] CDN purged {fname}")
        except Exception as e:
            print(f"[!] CDN purge failed for {fname}: {e}")


def main():
    local_only = "--local" in sys.argv
    ts = datetime.now().strftime("%H:%M:%S")

    print(f"\n[{ts}] Collecting data...")
    load_mod_data()

    try:
        data, configs = collect_all()
    except Exception as e:
        print(f"[!] Data collection failed: {e}")
        raise

    data_json = json.dumps(data, separators=(",", ":"))
    configs_json = json.dumps(configs, separators=(",", ":"))

    # ── Change detection ────────────────────────────────────────────────
    combined_hash = hashlib.md5(
        (data_json + configs_json).encode()
    ).hexdigest()

    if os.path.isfile(HASH_FILE):
        with open(HASH_FILE) as f:
            last_hash = f.read().strip()
        if combined_hash == last_hash:
            print("[--] No changes, skipping")
            return

    # ── Write local copies ──────────────────────────────────────────────
    for fname, content in [("data.json", data_json), ("configs.json", configs_json)]:
        path = os.path.join(SCRIPT_DIR, fname)
        with open(path, "w") as f:
            f.write(content)
        print(f"[OK] {fname} ({len(content):,} bytes)")

    # ── Push ────────────────────────────────────────────────────────────
    if not local_only:
        ok = push_data_files({
            "data.json": data_json,
            "configs.json": configs_json,
        })
        if ok:
            purge_cdn()

    # ── Save hash ───────────────────────────────────────────────────────
    with open(HASH_FILE, "w") as f:
        f.write(combined_hash)


if __name__ == "__main__":
    main()
