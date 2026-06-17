"""Build distributable zips for Mac and Windows into the Cowork outputs folder.

Excludes: screenshots/, *.db + snapshots, __pycache__, the other platform's
launcher scripts, and Bentley-specific maintenance scripts.

INCLUDE_API_KEY controls whether the real Anthropic key is baked into the
shipped config.json:
  True  -> internal team build; recipients can run with zero setup, but the key
           travels inside the zip (rotate it if a copy ever leaks).
  False -> the key is replaced with a PASTE_... placeholder (safe to share wider).
"""
import json
import os
import zipfile
from pathlib import Path

INCLUDE_API_KEY = False  # key is entered on the /setup screen now, never shipped

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.path.expanduser("~/Claude Cowork/outputs"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
TOP = "otishi-time-tracker"  # top-level folder inside each zip

# Build the config.json to ship in both packages.
_cfg = json.load(open(ROOT / "config.json"))
if not INCLUDE_API_KEY:
    _cfg["anthropic_api_key"] = "PASTE_YOUR_ANTHROPIC_API_KEY_HERE"
SCRUBBED_CONFIG = json.dumps(_cfg, indent=2).encode()

# (source_path_relative_to_ROOT, arcname_within_top, is_executable)
COMMON = [
    ("main.py", "main.py", False),
    ("requirements.txt", "requirements.txt", False),
    ("README.md", "README.md", False),
    ("CLAUDE.md", "CLAUDE.md", False),
    ("profiles.json", "profiles.json", False),
    (".gitignore", ".gitignore", False),
]
TEMPLATES = [
    ("templates/dashboard.html", "templates/dashboard.html", False),
    ("templates/timeline.html", "templates/timeline.html", False),
    ("templates/_blocks.html", "templates/_blocks.html", False),
    ("templates/quick_log.html", "templates/quick_log.html", False),
    ("templates/weekly_report.html", "templates/weekly_report.html", False),
    ("templates/setup.html", "templates/setup.html", False),
]
MAC_ONLY = [
    ("QUICKSTART-MAC.md", "QUICKSTART-MAC.md", False),
    ("start_tracker.sh", "start_tracker.sh", True),
    ("stop_tracker.sh", "stop_tracker.sh", True),
    ("install_autostart.sh", "install_autostart.sh", True),
]
WIN_ONLY = [
    ("QUICKSTART.md", "QUICKSTART.md", False),
    ("start_tracker.bat", "start_tracker.bat", False),
    ("stop_tracker.bat", "stop_tracker.bat", False),
    ("install_autostart.bat", "install_autostart.bat", False),
    ("install_shortcuts.bat", "install_shortcuts.bat", False),
    ("Otishi_Tracker.bat", "Otishi_Tracker.bat", False),
]


def build(zip_name: str, file_list: list):
    zip_path = OUT_DIR / zip_name
    if zip_path.exists():
        zip_path.unlink()
    written = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        # scrubbed config first
        cfg_info = zipfile.ZipInfo(f"{TOP}/config.json")
        cfg_info.compress_type = zipfile.ZIP_DEFLATED
        cfg_info.external_attr = (0o644 << 16)
        z.writestr(cfg_info, SCRUBBED_CONFIG)
        written.append("config.json")
        for src, arc, is_exec in file_list:
            src_path = ROOT / src
            if not src_path.exists():
                print(f"  WARNING missing: {src}")
                continue
            arcname = f"{TOP}/{arc}"
            data = src_path.read_bytes()
            info = zipfile.ZipInfo(arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 0o755 for shell scripts, 0o644 otherwise
            mode = 0o755 if is_exec else 0o644
            info.external_attr = (mode << 16) | (0o40000 if False else 0)
            z.writestr(info, data)
            written.append(arc)
    print(f"\n{zip_name}  ({zip_path.stat().st_size} bytes)")
    for w in sorted(written):
        print(f"  + {w}")
    return zip_path


print("Building Mac package...")
build("otishi-time-tracker-mac.zip", COMMON + TEMPLATES + MAC_ONLY)

print("\nBuilding Windows package...")
build("otishi-time-tracker-windows.zip", COMMON + TEMPLATES + WIN_ONLY)

print(f"\nOutput dir: {OUT_DIR}")
