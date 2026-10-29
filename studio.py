#!/usr/bin/env python3
"""KytyPS5 Studio - fork manager and PS5-style launcher for KytyPS5 builds.

Serves the UI in ./ui on 127.0.0.1 and opens it in an Edge/Chrome "app"
window. The optional zstandard package enables random-access reads from ZARs.

    python studio.py                 # open the shell
    python studio.py --no-browser    # only run the server (dev)
"""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# Installed builds keep user data in %LOCALAPPDATA% (Program Files is read-only); source runs stay portable.
DATA = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "KytyPS5Studio") if FROZEN else ROOT / "data"
UI = ROOT / "ui"
if sys.stdout is None:  # windowed exe has no console
    sys.stdout = sys.stderr = open(os.devnull, "w")
FORKS_DIR = DATA / "forks"
LOGS_DIR = DATA / "logs"
TMP_DIR = DATA / "tmp"
CONFIG_FILE = DATA / "config.json"
PLUGINS_DIR = DATA / "plugins"  # add-ons the user installed; Studio ships none
PLUGIN_DATA_DIR = DATA / "plugin-data"
COMMUNITY_DIR = DATA / "community"  # editable by the user; Studio ships with nothing here
CATALOG_FILE = COMMUNITY_DIR / "catalog.json"
PATCH_DIR = DATA / "patches"
EXE_NAME = "kyty_emulator.exe"

DEFAULT_FORKS = [  # a fresh install lists only the upstream project; everything else comes from a catalog you load
    {"id": "original", "name": "KytyPS5", "badge": "Original", "repo": "KytyPS5/KytyPS5", "builtin": True,
     "tagline": "Official upstream build"},
]

DEFAULT_CATALOG = "kytystudio/kytyps5-studio-catalog"  # metadata-only catalog repository, read when Settings > Community opens

DEFAULT_SETTINGS = {
    "fullscreen": True, "width": 1920, "height": 1080, "presentMode": "Mailbox", "shaderCache": True,
    "hdr": False, "hideCursor": True, "noSplash": False, "amdCpu": False, "userName": "Kyty", "gpu": "",
    "extraArgs": "", "keepVersions": 2, "githubToken": "", "sounds": True, "startScreen": True, "askUser": True, "catalogUrl": DEFAULT_CATALOG,
    "trophyNotifications": True, "trophyDuration": 6,
}

ALLOWED_DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")
CATALOG_HOSTS = ("raw.githubusercontent.com", "gist.githubusercontent.com", "github.com")

menu_req = 0         # bumped when the in-game menu hotkey is pressed
WINDOW = None        # native pywebview window, when running as a desktop app
lock = threading.RLock()
config = {}
jobs = {}            # job id -> state dict
running = {}         # run id -> {proc, game, fork, started, log}
release_cache = {}   # repo -> {"at": ts, "releases": [...], "error": str|None}
game_index = {}      # game id -> {"path", "art": {...}}
TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = set()


# ---------------------------------------------------------------- config ---

def load_config():
    global config
    DATA.mkdir(exist_ok=True)
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            shutil.copy(CONFIG_FILE, CONFIG_FILE.with_suffix(".bad"))
    cfg.setdefault("forks", [])
    cfg.setdefault("pluginsDisabled", [])
    for f in cfg["forks"]:
        f["builtin"] = f["id"] == "original"
    known = {f["id"] for f in cfg["forks"]}
    for f in DEFAULT_FORKS:
        if f["id"] not in known:
            cfg["forks"].append(dict(f, current=None))
    cfg["settings"] = {**DEFAULT_SETTINGS, **cfg.get("settings", {})}
    if not cfg.get("catalogDefaulted"):
        cfg["catalogDefaulted"] = True
        if not str(cfg["settings"].get("catalogUrl") or "").strip():
            cfg["settings"]["catalogUrl"] = DEFAULT_CATALOG
    cfg.setdefault("libraries", [])
    cfg.setdefault("games", {})
    if not isinstance(cfg.get("titleStats"), dict):
        cfg["titleStats"] = {}
    cfg.setdefault("defaultFork", "original")
    cfg.setdefault("users", [])
    if not cfg["users"]:  # first run / upgrade: the existing saves belong to user id 1000
        cfg["users"].append({"id": secrets.token_hex(4), "uid": 1000, "avatar": "blue",
                             "name": (cfg["settings"].get("userName") or "Player 1")[:16], "created": time.time()})
    if not any(u["id"] == cfg.get("activeUser") for u in cfg["users"]):
        cfg["activeUser"] = cfg["users"][0]["id"]
    config = cfg
    save_config()


def save_config():
    with lock:
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2), "utf-8")
        os.replace(tmp, CONFIG_FILE)


def get_fork(fid):
    for f in config["forks"]:
        if f["id"] == fid:
            return f
    return None


def safe_name(s):
    return re.sub(r"[^\w.\-]", "_", s)[:80] or "_"


# ---------------------------------------------------------------- GitHub ---

def http_get(url, timeout=20, extra=None):
    headers = {"User-Agent": "KytyPS5-Studio", "Accept": "application/vnd.github+json", **(extra or {})}
    token = config["settings"].get("githubToken") or os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = "Bearer " + token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def pick_asset(assets):
    """Choose the Windows x64 zip among a release's assets."""
    zips = [a for a in assets if a["name"].lower().endswith(".zip")]
    bad = ("linux", "macos", "mac-", "darwin", "source")
    zips = [a for a in zips if not any(b in a["name"].lower() for b in bad)]
    if not zips:
        return None
    zips.sort(key=lambda a: (("win" not in a["name"].lower()), -a["size"]))
    a = zips[0]
    return {"name": a["name"], "size": a["size"], "url": a["browser_download_url"]}


def fetch_releases(repo, force=False):
    with lock:
        c = release_cache.get(repo)
        if c and not force and time.time() - c["at"] < 300:
            return c
    entry = {"at": time.time(), "releases": [], "error": None}
    try:
        # conditional request: an unchanged answer (304) does not count against GitHub's hourly limit
        extra = {"If-None-Match": c["etag"]} if c and c.get("etag") and c.get("releases") else None
        with http_get(f"https://api.github.com/repos/{repo}/releases?per_page=10", extra=extra) as r:
            entry["etag"] = r.headers.get("ETag")
            raw = json.loads(r.read().decode("utf-8"))
        for rel in raw:
            if rel.get("draft"):
                continue
            entry["releases"].append({
                "tag": rel["tag_name"], "name": rel.get("name") or rel["tag_name"],
                "published": rel.get("published_at"), "prerelease": rel.get("prerelease", False),
                "notes": (rel.get("body") or "")[:1200], "url": rel.get("html_url"),
                "asset": pick_asset(rel.get("assets", [])),
            })
        entry["releases"] = [r for r in entry["releases"] if r["asset"]]
    except urllib.error.HTTPError as e:
        if e.code == 304 and c:
            entry = dict(c, at=time.time(), error=None)
        else:
            entry["error"] = "GitHub limit reached (60 requests/hour; add a token in Settings, Updates)" if e.code in (403, 429) else f"GitHub error {e.code}"
    except Exception as e:  # network down etc.
        entry["error"] = f"Offline ({e.__class__.__name__})"
    with lock:
        if entry["error"] and c and c["releases"]:
            entry["releases"] = c["releases"]  # keep stale data when a refresh fails
        release_cache[repo] = entry
        try:
            DATA.mkdir(exist_ok=True)
            (DATA / "release-cache.json").write_text(json.dumps(release_cache), "utf-8")
        except OSError:
            pass
    return entry


def refresh_all(force=False):
    for f in list(config["forks"]):
        fetch_releases(f["repo"], force)


def background_checker():
    while True:
        try:
            refresh_all(force=True)
        except Exception:
            pass
        time.sleep(1800)


# --------------------------------------------------------------- install ---

def version_dir(fork, tag):
    return FORKS_DIR / fork["id"] / safe_name(tag)


def read_meta(vdir):
    try:
        return json.loads((vdir / ".studio.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


def installed_versions(fork):
    out = []
    base = FORKS_DIR / fork["id"]
    if base.exists():
        for d in base.iterdir():
            meta = read_meta(d) if d.is_dir() else None
            if meta and (d / EXE_NAME).exists():
                out.append(meta)
    out.sort(key=lambda m: m.get("installedAt", 0), reverse=True)
    return out


def current_meta(fork):
    tag = fork.get("current")
    if not tag:
        return None
    d = version_dir(fork, tag)
    return read_meta(d) if (d / EXE_NAME).exists() else None


def help_text(exe):
    try:
        return subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=15,
                              cwd=str(exe.parent), creationflags=0x08000000).stdout
    except Exception:
        return ""


def probe_flags(exe):
    flags = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", help_text(exe))))
    return flags if "--game" in flags else None


# Flags Studio already drives from its own settings; the generic fork settings never repeat them.
MANAGED_FLAGS = {"--game", "--game-patch", "--screen-width", "--screen-height", "--user-name", "--user-id",
                 "--present-mode", "--gpu", "--fullscreen", "--hdr", "--hide-cursor", "--no-splash", "--amd-cpu",
                 "--shader-cache", "--trophy-notifications", "--no-trophy-notifications", "--trophy-duration",
                 "--addcont-dir", "--addcont", "--keymap", "--help"}
DEBUG_WORDS = ("validation", "dump", "profile", "debug", "log", "printf", "readback", "sync", "rd", "spirv")
_OPT_LINE = re.compile(r"^ {1,6}(--[a-z][a-z0-9-]*)(?:\s+(<[^>]*>\S*))?\s+(\S.*)$")


def parse_help(out):
    """Turns a fork's own --help into a list of settings Studio can show without knowing the fork:
    every flag it documents, its argument kind (switch / on-off / choice / number / text) and its description.
    A fork with new options (DLSS, frame generation, ...) therefore shows them with no change to Studio."""
    entries = []
    for line in out.splitlines():
        m = _OPT_LINE.match(line)
        if m:
            entries.append([m.group(1), m.group(2), m.group(3).strip()])
        elif entries and line.startswith(" " * 12) and line.strip():
            entries[-1][2] += " " + line.strip()
    opts = []
    for flag, arg, desc in entries:
        if flag in MANAGED_FLAGS or "repeatable" in desc.lower():
            continue
        words = flag[2:].split("-")
        o = {"flag": flag, "kind": "switch", "desc": desc,
             "label": flag[2:].replace("-", " ").capitalize(),
             "group": "Debug" if any(w in DEBUG_WORDS for w in words) else
                      "Audio" if words[0] in ("audio", "mic") else
                      "Controller" if words[0] in ("controller", "keyboard") else "Graphics and emulation"}
        d = re.search(r"Default:\s*([^.()]+?)(?:[.)]|$)", desc)
        if d:
            o["default"] = d.group(1).strip()
        if arg:
            inner = arg[1:].split(">")[0]
            if "|" in inner:
                ch = [c.strip() for c in inner.split("|")]
                o["kind"] = "bool" if {c.lower() for c in ch} <= {"true", "false", "on", "off", "t", "f"} else "choice"
                o["choices"] = ch
            elif inner == "value" and re.match(r"^\w+(, \w+)*,? or \w+\.", desc):  # "Fifo, Mailbox, or Immediate."
                o["kind"], o["choices"] = "choice", re.split(r",? or |, ", desc.split(".")[0])
            elif re.fullmatch(r"\d+-\d+", inner):
                lo, hi = inner.split("-")
                o.update(kind="num", min=int(lo), max=int(hi))
            elif inner in ("num", "seconds", "index", "number"):
                o["kind"] = "num"
            else:
                o["kind"] = "text"
        opts.append(o)
    order = ["Graphics and emulation", "Audio", "Controller", "Debug"]
    return sorted(opts, key=lambda o: order.index(o["group"]))


def fork_options(fork, meta=None):
    """The documented options of the installed version; probed once and kept in its .studio.json."""
    meta = meta or current_meta(fork)
    if not meta:
        return []
    vdir = version_dir(fork, meta["tag"])
    if meta.get("options") is None:
        meta["options"] = parse_help(help_text(vdir / EXE_NAME))
        try:
            (vdir / ".studio.json").write_text(json.dumps(meta), "utf-8")
        except OSError:
            pass
    return meta["options"]


def set_job(job, **kw):
    with lock:
        job.update(kw)


# ----------------------------------------------------------- ZAR conversion ---

zar_jobs = {}


def zarchive_tool():
    """Find an optional ZArchive-Next CLI shipped beside the app or installed on PATH."""
    names = ("zarchive.exe", "zarchive")
    roots = [ROOT, ROOT / "tools", Path(sys.executable).resolve().parent / "tools",
             Path(sys.executable).resolve().parent]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return shutil.which("zarchive.exe") or shutil.which("zarchive")


def zar_worker(job, source, output, tool):
    log_path = TMP_DIR / f"{job['id']}.zar.log"
    try:
        set_job(job, state="scanning", message="Calculating source size", writtenBytes=0, sourceBytes=0, fileCount=0)
        source_bytes = file_count = 0
        for root, _, files in os.walk(source):
            for name in files:
                try:
                    source_bytes += (Path(root) / name).stat().st_size
                    file_count += 1
                except OSError:
                    continue
        set_job(job, state="compressing", message="Compressing · starting ZArchive", sourceBytes=source_bytes, fileCount=file_count)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        command = [str(tool), str(source), str(output)]
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(command, cwd=str(tool.parent if isinstance(tool, Path) else source.parent),
                                       stdout=log, stderr=subprocess.STDOUT, shell=False,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            while process.poll() is None:
                try:
                    written = output.stat().st_size
                except OSError:
                    written = 0
                set_job(job, writtenBytes=written,
                        message=f"Compressing · {written / 1073741824:.2f} GB written")
                time.sleep(.6)
            return_code = process.wait()
        if return_code:
            try:
                with open(log_path, "rb") as log:
                    log.seek(max(0, log.seek(0, os.SEEK_END) - 8192))
                    detail = log.read().decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            raise RuntimeError((detail or f"ZArchive exited with code {return_code}")[-1200:])
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("ZArchive did not produce an archive")
        message = "ZAR archive created"
        metadata_saved = False
        try:
            save_zar_metadata(source, output)
            metadata_saved = True
        except OSError as e:
            message += f"; metadata backup failed: {e}"
        source_title_id = (game_index.get(job["game"]) or {}).get("titleId")
        promote_game_stats(job["game"], source_title_id)
        save_config()
        if job.get("deleteSource"):
            if not metadata_saved:
                message += "; original kept because metadata backup failed"
            elif source.resolve() in {Path(path).resolve() for path in config.get("libraries", [])}:
                message += "; original kept because it is itself a library root"
            elif is_link(source):
                message += "; original kept because it is a link or junction"
            elif any(run["game"] == job["game"] and run["proc"].poll() is None for run in running.values()):
                message += "; original kept because the game is still running"
            else:
                try:
                    shutil.rmtree(source)
                    message += "; original folder removed"
                except OSError as e:
                    message += f"; could not remove original folder: {e}"
        set_job(job, state="done", progress=1.0, message=message, output=str(output))
        scan_games()
    except Exception as e:
        output.unlink(missing_ok=True)
        set_job(job, state="error", message=str(e) or e.__class__.__name__)
    finally:
        log_path.unlink(missing_ok=True)


def start_zar_conversion(game_id, delete_source=False):
    game = game_index.get(str(game_id))
    if not game:
        scan_games()
        game = game_index.get(str(game_id))
    if not game or game.get("format") != "Folder":
        raise ValueError("Only extracted game folders can be converted to ZAR")
    tool = zarchive_tool()
    if not tool:
        raise ValueError("ZArchive-Next is not installed with the app or available on PATH")
    source = Path(game["path"])
    output = source.with_name(source.name + ".zar")
    if output.exists():
        raise ValueError(f"Output already exists: {output.name}")
    with lock:
        if any(j["game"] == game_id and j["state"] in ("queued", "scanning", "compressing") for j in zar_jobs.values()):
            return next(j for j in zar_jobs.values() if j["game"] == game_id and j["state"] in ("queued", "scanning", "compressing"))
        job = {"id": secrets.token_hex(6), "game": game_id, "item": source.name, "state": "queued",
               "message": "Queued", "progress": 0, "deleteSource": bool(delete_source), "started": time.time()}
        zar_jobs[job["id"]] = job
    threading.Thread(target=zar_worker, args=(job, source, output, tool), daemon=True).start()
    return job


def install_worker(job, fork, release):
    tmp_zip = TMP_DIR / f"{job['id']}.zip"
    tmp_dir = TMP_DIR / job["id"]
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        asset = release["asset"]
        host = urlparse(asset["url"]).hostname or ""
        if not host.endswith(ALLOWED_DOWNLOAD_HOSTS):
            raise RuntimeError("Untrusted download host")
        set_job(job, state="downloading", message="Downloading", progress=0.0)
        with http_get(asset["url"], timeout=30) as r, open(tmp_zip, "wb") as out:
            total = int(r.headers.get("Content-Length") or asset["size"] or 0)
            done, t0 = 0, time.time()
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                set_job(job, progress=(done / total * 0.85) if total else 0.0,
                        message=f"Downloading  {done / 1048576:.1f} / {total / 1048576:.1f} MB",
                        speed=done / max(time.time() - t0, 0.1))
        set_job(job, state="extracting", message="Extracting", progress=0.88)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        base = tmp_dir.resolve()
        with zipfile.ZipFile(tmp_zip) as z:
            for m in z.infolist():
                if not str((base / m.filename).resolve()).startswith(str(base)):
                    raise RuntimeError("Archive contains unsafe paths")
            z.extractall(tmp_dir)
        exes = list(tmp_dir.rglob(EXE_NAME))
        if not exes:
            raise RuntimeError(f"{EXE_NAME} not found in the archive")
        src = min(exes, key=lambda p: len(p.parts)).parent
        dest = version_dir(fork, release["tag"])
        if running_with(fork["id"], release["tag"]):
            raise RuntimeError("A game is running on this version; close it first")
        if dest.exists():
            rm_version(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        set_job(job, message="Verifying", progress=0.95)
        meta = {"tag": release["tag"], "name": release["name"], "published": release["published"],
                "asset": asset["name"], "size": asset["size"], "installedAt": time.time(),
                "flags": probe_flags(dest / EXE_NAME), "options": parse_help(help_text(dest / EXE_NAME))}
        (dest / ".studio.json").write_text(json.dumps(meta), "utf-8")
        with lock:
            fork["current"] = release["tag"]
            prune_versions(fork)
            save_config()
        set_job(job, state="done", message="Installed", progress=1.0)
    except Exception as e:
        set_job(job, state="error", message=str(e) or e.__class__.__name__)
    finally:
        tmp_zip.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def rm_version(vdir):
    """Delete a version folder; the _SaveData/_Trophies junctions are unlinked first, never followed."""
    for name in PERSIST:
        link = Path(vdir) / name
        if is_link(link):
            os.rmdir(link)
    shutil.rmtree(vdir, ignore_errors=True)


def prune_versions(fork):
    keep = max(1, int(config["settings"].get("keepVersions", 2)))
    for i, m in enumerate(installed_versions(fork)):
        if i >= keep and m["tag"] != fork.get("current") and not running_with(fork["id"], m["tag"]):
            rm_version(version_dir(fork, m["tag"]))


def start_install(fork, tag=None):
    with lock:
        for j in jobs.values():
            if j["fork"] == fork["id"] and j["state"] in ("queued", "downloading", "extracting"):
                return j
    entry = fetch_releases(fork["repo"])
    rels = entry["releases"]
    release = next((r for r in rels if r["tag"] == tag), None) if tag else (rels[0] if rels else None)
    if not release:
        raise ValueError(entry["error"] or "No Windows release found for this fork")
    job = {"id": secrets.token_hex(6), "fork": fork["id"], "tag": release["tag"], "state": "queued",
           "message": "Queued", "progress": 0.0, "speed": 0, "started": time.time()}
    with lock:
        jobs[job["id"]] = job
    threading.Thread(target=install_worker, args=(job, fork, release), daemon=True).start()
    return job


# ----------------------------------------------------------------- games ---

ART = {"icon": "icon0.png", "bg": ("pic1.png", "pic0.png")}


def parse_param(sce):
    try:
        p = json.loads((sce / "param.json").read_text("utf-8-sig"))
    except (OSError, ValueError):
        return None, None, None
    loc = p.get("localizedParameters") or {}
    lang = loc.get("defaultLanguage") or "en-US"
    name = (loc.get(lang) or loc.get("en-US") or next((v for v in loc.values() if isinstance(v, dict)), {})
            ).get("titleName") or p.get("titleName")
    return name, p.get("titleId") or p.get("title_id"), p.get("contentVersion")


ZAR_ART = ("icon0.png", "pic0.png", "pic1.png")


def zar_sidecar(archive):
    return Path(str(archive) + ".studio")


def save_zar_metadata(source, archive):
    """Keep game title metadata and display art beside a generated ZAR."""
    sce = Path(source) / "sce_sys"
    name, title_id, version = parse_param(sce)
    sidecar = zar_sidecar(archive)
    art_dir = sidecar / "art"
    art_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for art_name in ZAR_ART:
        src = sce / art_name
        if src.is_file():
            shutil.copy2(src, art_dir / art_name)
            copied.append(art_name)
    trophy_source = sce / "trophy2" / "trophy00.ucp"
    if trophy_source.is_file():
        trophy_dir = sidecar / "trophy"
        trophy_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(trophy_source, trophy_dir / "trophy00.ucp")
    data = {"title": name or Path(source).name, "titleId": title_id, "version": version, "art": copied}
    temp = sidecar / "metadata.tmp"
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(sidecar / "metadata.json")


def load_zar_metadata(archive):
    """Read a ZAR sidecar, or recover display metadata from its sibling dump."""
    archive = Path(archive)
    sidecar = zar_sidecar(archive)
    try:
        data = json.loads((sidecar / "metadata.json").read_text("utf-8"))
        art_dir = sidecar / "art"
        if data.get("title") and data.get("titleId"):
            return data, {name: art_dir / name for name in ZAR_ART}
    except (OSError, ValueError, TypeError):
        pass
    source = archive.with_suffix("")
    if source.is_dir():
        sce = source / "sce_sys"
        name, title_id, version = parse_param(sce)
        return {"title": name or source.name, "titleId": title_id, "version": version}, {
            art_name: sce / art_name for art_name in ZAR_ART}
    try:
        sce_sidecar = sidecar / "sce_sys"
        param = cached_zar_file(archive, "sce_sys/param.json", sce_sidecar / "param.json", 4 * 1024 * 1024)
        if param:
            name, title_id, version = parse_param(sce_sidecar)
            art = {}
            for art_name in ZAR_ART:
                art[art_name] = cached_zar_file(archive, f"sce_sys/{art_name}", sidecar / "art" / art_name,
                                                64 * 1024 * 1024)
            data = {"title": name or archive.stem, "titleId": title_id, "version": version}
            sidecar.mkdir(parents=True, exist_ok=True)
            (sidecar / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            return data, art
    except (OSError, ValueError, RuntimeError, struct.error):
        pass
    return {"title": archive.stem, "titleId": None, "version": None}, {
        art_name: None for art_name in ZAR_ART}


def scan_games():
    found, index = [], {}
    for lib in config["libraries"]:
        root = Path(lib)
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            is_game = any(e.name.lower() == "eboot.bin" for e in entries) or (d / "sce_sys" / "param.json").exists()
            if is_game:
                gid = hashlib.sha1(str(d).lower().encode()).hexdigest()[:12]
                sce = d / "sce_sys"
                name, title_id, cver = parse_param(sce)
                pic0, pic1 = sce / "pic0.png", sce / "pic1.png"
                bg = pic1 if pic1.exists() else pic0 if pic0.exists() else None
                fg = pic0 if pic1.exists() and pic0.exists() else None  # pic0 = transparent logo overlay
                icon = sce / ART["icon"]
                index[gid] = {"path": str(d), "format": "Folder", "icon": icon if icon.exists() else None,
                              "bg": bg, "fg": fg, "titleId": title_id, "version": cver}
                found.append({"id": gid, "title": name or d.name, "titleId": title_id, "path": str(d), "format": "Folder",
                              "icon": icon.exists(), "bg": bool(bg), "fg": bool(fg), "version": cver})
                continue
            for e in entries:
                if e.is_dir(follow_symlinks=False) and depth < 2:
                    if e.name.lower().endswith(".zar.studio"):
                        continue
                    stack.append((Path(e.path), depth + 1))
                elif e.is_file() and e.name.lower().endswith(".zar"):
                    gid = hashlib.sha1(e.path.lower().encode()).hexdigest()[:12]
                    source_dump = Path(e.path).with_suffix("")
                    if source_dump.is_dir() and not (zar_sidecar(e.path) / "metadata.json").is_file():
                        try:
                            save_zar_metadata(source_dump, e.path)
                        except OSError:
                            pass
                    metadata, art = load_zar_metadata(e.path)
                    icon = art.get("icon0.png")
                    pic0, pic1 = art.get("pic0.png"), art.get("pic1.png")
                    bg = pic1 if pic1 and pic1.exists() else pic0 if pic0 and pic0.exists() else None
                    fg = pic0 if pic1 and pic1.exists() and pic0 and pic0.exists() else None
                    index[gid] = {"path": e.path, "format": "ZAR", "icon": icon if icon and icon.exists() else None,
                                  "bg": bg, "fg": fg, "titleId": metadata.get("titleId"), "version": metadata.get("version")}
                    found.append({"id": gid, "title": metadata.get("title") or Path(e.name).stem,
                                  "titleId": metadata.get("titleId"), "path": e.path, "format": "ZAR",
                                  "icon": bool(icon and icon.exists()), "bg": bool(bg), "fg": bool(fg),
                                  "version": metadata.get("version")})
    with lock:
        game_index.clear()
        game_index.update(index)
        for g in found:
            st = config["games"].get(g["id"], {})
            us = user_stats(g["id"], active_user(), g.get("titleId"))
            g.update(fork=st.get("fork"), lastPlayed=us.get("lastPlayed"), playTime=us.get("playTime", 0), plays=us.get("plays", 0),
                     overrides=st.get("overrides", {}))
    found.sort(key=lambda g: (-(g["lastPlayed"] or 0), g["title"].lower()))
    return found


# ---------------------------------------------------------------- launch ---

ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env(text):
    out = {}
    for part in re.split(r"[;\r\n]+", str(text)):
        k, sep, v = part.partition("=")
        if sep and ENV_KEY.match(k.strip()):
            out[k.strip()] = v.strip()
    return out


# Features many forks switch with KYTY_* environment variables instead of command-line flags (their own
# launcher maps its checkboxes to these). The catalog describes them ("settings", shared or per fork); this list is
# only the fallback used when no catalog has been loaded. A shared entry is offered only for a fork whose preset or
# README names every variable it needs, so a fork without frame generation never shows it.
FALLBACK_FEATURES = [
    {"id": "framegen", "label": "Frame generation", "needs": ["KYTY_FRAMEGEN"], "kind": "choice",
     "desc": "Extra generated frames. DLSS-G needs an RTX 40/50 card and the Streamline files (see the fork's README); the AMD FSR option works on any GPU.",
     "choices": [{"label": "Off", "env": {"KYTY_FRAMEGEN": "0"}}, {"label": "2x (DLSS-G)", "env": {"KYTY_FRAMEGEN": "1"}},
                 {"label": "3x (DLSS-G, RTX 50)", "env": {"KYTY_FRAMEGEN": "2"}},
                 {"label": "4x (DLSS-G, RTX 50)", "env": {"KYTY_FRAMEGEN": "3"}},
                 {"label": "2x (AMD FSR 3.1)", "env": {"KYTY_FRAMEGEN": "1", "KYTY_FRAMEGEN_BACKEND": "fsr"}}]},
    {"id": "upscaler", "label": "Upscaling", "needs": ["KYTY_DLSS", "KYTY_FSR"], "kind": "choice",
     "desc": "Replaces the game's own anti-aliasing with DLSS (RTX) or FSR 3.1 (any GPU).",
     "choices": [{"label": "Game's own (TAA)", "env": {"KYTY_DLSS": "0", "KYTY_FSR": "0"}}, {"label": "NVIDIA DLSS", "env": {"KYTY_DLSS": "1", "KYTY_FSR": "0"}},
                 {"label": "AMD FSR 3.1", "env": {"KYTY_DLSS": "0", "KYTY_FSR": "1"}}]},
    {"id": "renderres", "label": "Render resolution", "needs": ["KYTY_ASTRO_RENDER_RES"], "kind": "choice",
     "desc": "The game's 3D resolution (lower is faster).",
     "choices": [{"label": f"{r}p", "env": {"KYTY_ASTRO_RENDER_RES": str(r)}} for r in (540, 720, 1080, 1368, 1872, 2160)]},
    {"id": "fpscap", "label": "Frame rate limit", "needs": ["KYTY_FRAMEGEN_FPS_CAP"], "kind": "num", "key": "KYTY_FRAMEGEN_FPS_CAP", "min": 0, "max": 1000,
     "desc": "With DLSS-G, holds the rendered frames to this rate so generated frames are evenly spaced. Pick one your PC holds. 0 = off."},
    {"id": "pacing", "label": "Smooth frame pacing", "needs": ["KYTY_PRESENT_ABS_VBLANK"], "kind": "switch", "on": {"KYTY_PRESENT_ABS_VBLANK": "1"}, "off": {"KYTY_PRESENT_ABS_VBLANK": "0"},
     "desc": "After a slow frame, skip a vblank instead of showing the next frames back to back."},
    {"id": "relaxed", "label": "Relaxed shader maths", "needs": ["KYTY_FLOAT_PROFILE"], "kind": "switch", "on": {"KYTY_FLOAT_PROFILE": "relaxed"},
     "desc": "Simpler shader maths: faster, experimental."},
    {"id": "vram", "label": "Video memory budget (MB)", "needs": ["KYTY_VRAM_BUDGET_MB"], "kind": "num", "key": "KYTY_VRAM_BUDGET_MB", "min": 0, "max": 1000000,
     "desc": "Leaves video memory free for DLSS-G (helps on 16 GB cards at 4K)."},
]


def documented_env(vdir):
    """KYTY_* names a build mentions: its preset and the tables in its own README files."""
    names = {k for k in fork_env(vdir) if k.startswith("KYTY")}
    for p in list(Path(vdir).glob("*.md"))[:8]:
        try:
            names.update(re.findall(r"`(KYTY_[A-Z0-9_]+)", p.read_text("utf-8", "replace")))
        except OSError:
            pass
    return names


SAFE_ENV = re.compile(r"^KYTY_[A-Z0-9_]{1,60}$")  # catalog entries may only set the emulator's own variables


def clean_feature(d):
    """One setting definition from a catalog (untrusted JSON) -> a checked copy, or None."""
    try:
        kind, fid = d["kind"], str(d["id"])
        if kind not in ("choice", "num", "switch", "text") or not re.fullmatch(r"[\w:.-]{1,60}", fid):
            return None
        env_ok = lambda e: isinstance(e, dict) and 0 < len(e) <= 8 and all(SAFE_ENV.match(str(k)) and isinstance(v, (str, int, float)) for k, v in e.items())
        out = {"id": fid, "label": _text(d.get("label") or fid, 60), "desc": _text(d.get("desc"), 400), "kind": kind}
        keys = []
        if kind == "choice":
            out["choices"] = [{"label": _text(c["label"], 60), "env": {str(k): str(v) for k, v in c["env"].items()}}
                              for c in d["choices"][:24] if isinstance(c, dict) and c.get("label") and env_ok(c.get("env"))]
            if not out["choices"]:
                return None
            keys = [k for c in out["choices"] for k in c["env"]]
        elif kind in ("num", "text"):
            if not SAFE_ENV.match(str(d["key"])):
                return None
            out["key"] = str(d["key"])
            keys = [out["key"]]
            if kind == "num":
                out["min"], out["max"] = int(d.get("min", 0)), int(d.get("max", 1000000))
        else:
            if not env_ok(d.get("on")):
                return None
            out["on"] = {str(k): str(v) for k, v in d["on"].items()}
            keys = list(out["on"])
            if env_ok(d.get("off")):
                out["off"] = {str(k): str(v) for k, v in d["off"].items()}
        out["needs"] = [str(n) for n in d["needs"] if SAFE_ENV.match(str(n))] if isinstance(d.get("needs"), list) else sorted(set(keys))
        return out
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def readme_vars(vdir):
    """KYTY_* variables a build documents in a README table (a row like: | `KYTY_X=1` | what it does |)."""
    found = {}
    for p in list(Path(vdir).glob("*.md"))[:8]:
        try:
            lines = p.read_text("utf-8", "replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            m = re.match(r"^\|\s*`(KYTY_[A-Z0-9_]{1,60})(?:=([^`]*))?`\s*\|\s*(.+?)\s*\|?\s*$", ln)
            if m:
                e = found.setdefault(m[1], {"desc": re.sub(r"[`*]", "", m[3])[:300], "examples": []})
                if m[2] and m[2] not in e["examples"]:
                    e["examples"].append(m[2])
    return found


def fork_features(fork, meta=None):
    """Settings for this build: the catalog's shared ones whose variables it documents, the catalog's entries for this
    very fork, then every other variable its README documents (shown as plain text fields)."""
    meta = meta or current_meta(fork)
    if not meta:
        return []
    vdir = version_dir(fork, meta["tag"])
    names = documented_env(vdir)
    cat = catalog()
    shared = [clean_feature(d) for d in cat.get("settings") or []] or [clean_feature(d) for d in FALLBACK_FEATURES]
    out = {f["id"]: f for f in shared if f and all(n in names for n in f["needs"])}
    entry = next((e for e in cat["forks"] if fork_of_entry(e) is fork), None)
    for d in (entry or {}).get("settings") or []:
        f = clean_feature({**d, "needs": []})
        if f:
            out[f["id"]] = f
    used = set()
    for f in out.values():
        for c in f.get("choices", []):
            used.update(c["env"])
        used.update(f.get("on", {}))
        if f.get("key"):
            used.add(f["key"])
    for key, e in sorted(readme_vars(vdir).items()):
        if key not in used:
            hint = f" Values seen in its README: {', '.join(e['examples'][:6])}." if e["examples"] else ""
            out["var:" + key] = {"id": "var:" + key, "label": key, "kind": "text", "key": key, "desc": e["desc"] + hint, "needs": [], "auto": True}
    return list(out.values())


def feature_env(feats, sel):
    env = {}
    for f in feats:
        v = (sel or {}).get(f["id"])
        if v in (None, ""):
            continue
        if f["kind"] == "choice":
            c = next((c for c in f["choices"] if c["label"] == v), None)
            env.update(c["env"] if c else {})
        elif f["kind"] == "num":
            env[f["key"]] = str(int(v))
        elif f["kind"] == "text":
            env[f["key"]] = str(v).strip().replace("\n", " ")[:100]
        elif v is True:
            env.update(f["on"])
        elif v is False:
            env.update(f.get("off") or {})
    return env


def fork_settings(fork_id, game_id=None, feats=()):
    """Fork-level values with the game's own values on top: {"args": {flag: value}, "env": {KEY: value}}."""
    own = config.get("forkOpts", {}).get(fork_id) or {}
    args, sel, env = dict(own.get("args") or {}), dict(own.get("sel") or {}), parse_env(own.get("env") or "")
    if game_id:
        g = config["games"].get(game_id, {}).get("forkOpts", {}).get(fork_id) or {}
        args.update(g.get("args") or {})
        sel.update(g.get("sel") or {})
        env.update(parse_env(g.get("env") or ""))
    return {"args": args, "env": {**feature_env(feats, sel), **env}}


def running_with(fid, tag):
    with lock:
        return any(r["fork"] == fid and r["tag"] == tag and r["proc"].poll() is None for r in running.values())


def build_args(meta, settings, ov, user=None, patch_file=None, fork_args=None):
    known = {o["flag"]: o for o in meta.get("options") or []}
    s = {**settings, **{k: v for k, v in ov.items() if v not in (None, "")}}
    flags = set(meta.get("flags") or [])
    ok = (lambda f: not flags or f in flags)
    a = []

    def add(flag, *vals):
        if ok(flag):
            a.append(flag)
            a.extend(str(v) for v in vals)

    if s["fullscreen"]:
        add("--fullscreen")
    else:
        add("--screen-width", int(s["width"]))
        add("--screen-height", int(s["height"]))
    if s.get("presentMode"):
        add("--present-mode", s["presentMode"])
    for key, flag in (("shaderCache", "--shader-cache"), ("hdr", "--hdr"), ("hideCursor", "--hide-cursor"),
                      ("noSplash", "--no-splash"), ("amdCpu", "--amd-cpu")):
        if s.get(key):
            add(flag)
    if user:  # saves and trophies are kept per emulator user id
        add("--user-id", user["uid"])
        add("--user-name", user["name"][:16])
    if patch_file:
        add("--game-patch", patch_file)
    if str(s.get("gpu", "")).strip().isdigit():
        add("--gpu", s["gpu"])
    if "trophyNotifications" in s:
        add("--trophy-notifications", "true" if s["trophyNotifications"] else "false")
    if s.get("trophyDuration"):
        add("--trophy-duration", max(5, min(10, int(s["trophyDuration"]))))
    for flag, val in (fork_args or {}).items():
        o = known.get(flag)
        if not o or val in (None, ""):
            continue
        if o["kind"] == "switch":
            if val is True:
                a.append(flag)
        else:
            a.extend([flag, str(val)])
    if s.get("extraArgs"):
        a.extend(shlex.split(s["extraArgs"], posix=False))
    return a


def fork_env(vdir, extra_env=None):
    """What the fork's own launcher would set: the *-preset.json next to the exe (KYTY_* variables)
    and launch.json's "environment". Without it the emulator misses its shader precompile/prefetch."""
    env = dict(os.environ)
    extra = {}
    for p in sorted(Path(vdir).glob("*-preset.json")):
        try:
            extra.update({k: str(v) for k, v in json.loads(p.read_text("utf-8")).items()})
        except (OSError, ValueError, AttributeError):
            pass
    try:
        extra.update({k: str(v) for k, v in json.loads((Path(vdir) / "launch.json").read_text("utf-8")).get("environment", {}).items()})
    except (OSError, ValueError, AttributeError):
        pass
    if extra:
        for k in [k for k in env if k.upper().startswith(("KYTY", "TRACY"))]:
            del env[k]
        env.update(extra)
    env.update(extra_env or {})  # the user's own fork settings win over the fork's preset
    return env


def launch(game_id, fork_id):
    g = game_index.get(game_id)
    if not g:
        raise ValueError("Game not found; rescan the library")
    fork = get_fork(fork_id or config["games"].get(game_id, {}).get("fork") or config["defaultFork"])
    if not fork:
        raise ValueError("Unknown fork")
    meta = current_meta(fork)
    if not meta:
        raise ValueError(f"{fork['name']} is not installed yet")
    vdir = version_dir(fork, meta["tag"])
    try:
        ensure_links(fork, vdir)
    except Exception as e:
        raise ValueError(f"Could not link the saves folder: {e}")
    st = config["games"].setdefault(game_id, {})
    fs = fork_settings(fork["id"], game_id, fork_features(fork, meta))
    args = [str(vdir / EXE_NAME), "--game", g["path"]] + build_args({**meta, "options": fork_options(fork, meta)}, config["settings"],
                                                                      st.get("overrides", {}), active_user(), patch_file_for_game(game_id), fs["args"])
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = LOGS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{fork['id']}-{game_id}.log"
    with open(log, "wb") as lf:
        lf.write((" ".join(args) + "\n\n").encode("utf-8", "replace"))
        lf.flush()
        proc = subprocess.Popen(args, cwd=str(vdir), env=fork_env(vdir, fs["env"]), stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    run = {"id": secrets.token_hex(4), "proc": proc, "game": game_id, "fork": fork["id"], "tag": meta["tag"],
           "started": time.time(), "log": str(log), "uid": active_user()["uid"], "titleId": g.get("titleId")}
    with lock:
        running[run["id"]] = run
        us = dict(user_stats(game_id, active_user(), g.get("titleId")))
        us.update(lastPlayed=time.time(), plays=us.get("plays", 0) + 1)
        record_user_stats(game_id, g.get("titleId"), active_user()["uid"], us)
        st["fork"] = fork["id"]
        save_config()
    threading.Thread(target=watch_run, args=(run,), daemon=True).start()
    threading.Thread(target=focus_game, args=(proc,), daemon=True).start()
    return run


def _game_windows(pid):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if owner.value == pid and r.right - r.left > 300 and r.bottom - r.top > 200:
                found.append(hwnd)
        return True

    user32.EnumWindows(enum(cb), 0)
    return found


def _foreground(hwnd):
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    cur = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    if fg_tid and fg_tid != cur:
        user32.AttachThreadInput(cur, fg_tid, True)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)  # a synthetic Alt lets SetForegroundWindow through
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.BringWindowToTop(hwnd)
    if fg_tid and fg_tid != cur:
        user32.AttachThreadInput(cur, fg_tid, False)


def focus_game(proc):
    """Studio steps aside and the emulator window gets the keyboard and the controller."""
    import ctypes
    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline and proc.poll() is None:
        wins = _game_windows(proc.pid)
        if wins:
            hwnd = wins[0]
            break
        time.sleep(0.5)
    if not hwnd:
        return
    ui_minimize()
    user32 = ctypes.windll.user32
    for _ in range(8):  # the window may be recreated after the splash: re-assert for a few seconds
        if proc.poll() is not None:
            return
        wins = _game_windows(proc.pid)
        if wins and user32.GetForegroundWindow() not in wins:
            _foreground(wins[0])
        time.sleep(1)


def ui_minimize():
    """Get the shell out of the way while a game runs (no stray key presses, no GPU use)."""
    try:
        if WINDOW:
            WINDOW.minimize()
    except Exception:
        pass


def ui_restore():
    try:
        if WINDOW:
            WINDOW.restore()
    except Exception:
        pass


def get_user_by_uid(uid):
    return next((u for u in config["users"] if u["uid"] == uid), None)


def watch_run(run):
    code = run["proc"].wait()
    with lock:
        run["exit"] = code
        run["ended"] = time.time()
        st = config["games"].setdefault(run["game"], {})
        who = get_user_by_uid(run.get("uid"))
        us = dict(user_stats(run["game"], who, run.get("titleId"))) if who else {}
        us["playTime"] = us.get("playTime", 0) + int(run["ended"] - run["started"])
        record_user_stats(run["game"], run.get("titleId"), run.get("uid"), us)
        save_config()
    ui_restore()


def public_run(r):
    code = r["proc"].poll()
    return {"id": r["id"], "game": r["game"], "fork": r["fork"], "tag": r["tag"], "started": r["started"],
            "alive": code is None, "exit": code, "log": Path(r["log"]).name}




# ------------------------------------------------------------------- users --

def get_user(uid):
    return next((u for u in config["users"] if u["id"] == uid), None)


def active_user():
    return get_user(config["activeUser"]) or config["users"][0]


def stats_title_key(title_id):
    return str(title_id or "").strip().upper()


def record_user_stats(game_id, title_id, uid, stats):
    by_user = config["games"].setdefault(game_id, {}).setdefault("byUser", {})
    by_user[str(uid)] = dict(stats)
    title_key = stats_title_key(title_id)
    if title_key:
        config.setdefault("titleStats", {}).setdefault(title_key, {})[str(uid)] = dict(stats)


def promote_game_stats(game_id, title_id):
    title_key = stats_title_key(title_id)
    if not title_key:
        return
    old = config["games"].get(game_id, {})
    dest = config.setdefault("titleStats", {}).setdefault(title_key, {})
    for uid, stats in old.get("byUser", {}).items():
        dest.setdefault(str(uid), dict(stats))
    if "byUser" not in old and old.get("lastPlayed"):
        first = min(config["users"], key=lambda user: user["created"])
        dest.setdefault(str(first["uid"]), {"lastPlayed": old.get("lastPlayed"),
                                            "playTime": old.get("playTime", 0), "plays": old.get("plays", 0)})


def user_stats(game_id, user, title_id=None):
    """Per-user play statistics; the pre-users totals belong to the first (original) user."""
    title_key = stats_title_key(title_id)
    if title_key:
        title_stats = config.setdefault("titleStats", {}).get(title_key, {}).get(str(user["uid"]))
        if title_stats is not None:
            return title_stats
    st = config["games"].get(game_id, {})
    bu = st.get("byUser", {}).get(str(user["uid"]))
    if bu is not None:
        if title_key:
            config.setdefault("titleStats", {}).setdefault(title_key, {})[str(user["uid"])] = dict(bu)
        return bu
    first = min(config["users"], key=lambda u: u["created"])
    if user["id"] == first["id"] and "byUser" not in st:
        legacy = {"lastPlayed": st.get("lastPlayed"), "playTime": st.get("playTime", 0), "plays": st.get("plays", 0)}
        if title_key and legacy.get("lastPlayed"):
            config.setdefault("titleStats", {}).setdefault(title_key, {})[str(user["uid"])] = dict(legacy)
        return legacy
    return {}


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:16]
    if not name:
        raise ValueError("Enter a name")
    return name


# ------------------------------------------------ persistent saves/trophies --

SAVE_DIRS = ("_SaveData", "_Trophies", "_DownloadData")
PERSIST = SAVE_DIRS + ("_PipelineCache",)  # shader caches and journals also survive updates


def data_dir(fork):
    return FORKS_DIR / fork["id"] / "_data"


def shared_user_data_dir():
    """Save data and trophies shared by every emulator fork, partitioned by Kyty user IDs."""
    return DATA / "shared-user-data"


def is_link(p):
    p = str(p)
    return os.path.islink(p) or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


def make_junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except Exception:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True,
                       creationflags=0x08000000)


def remove_directory_link(path):
    if os.path.islink(path):
        Path(path).unlink()
    else:  # Windows junction
        os.rmdir(path)


def ensure_data_link(link, target, fork_id):
    link, target = Path(link), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    if is_link(link):
        try:
            if link.resolve() == target.resolve():
                return
            src = link.resolve()
        except OSError:
            src = link
        if src.is_dir():
            merge_copy(src, target, fork_id, "newer")
        remove_directory_link(link)
    elif link.is_dir():
        merge_copy(link, target, fork_id, "newer")
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    make_junction(link, target)


def merge_copy(src, dst, fork_id="misc", prefer="dst"):
    """Copy src into dst without losing anything.

    prefer="dst": files already in dst win (adopting what an emulator wrote before the links existed).
    prefer="newer": the newer file wins (explicit import). Either way the losing copy is kept in
    userdata-conflicts/, and trophy JSONs are unioned instead of replaced."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    keep = DATA / "userdata-conflicts" / fork_id / stamp

    def park(path, rel):
        out = keep / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)

    for root, _, files in os.walk(src):
        rel_dir = Path(root).relative_to(src)
        out_dir = Path(dst) / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            a, b = Path(root) / name, out_dir / name
            rel = rel_dir / name
            if not b.exists():
                shutil.copy2(a, b)
                continue
            if name.startswith("trophies_") and name.endswith(".json"):
                try:
                    x, y = json.loads(a.read_text("utf-8")), json.loads(b.read_text("utf-8"))
                    at = {**x.get("unlockedAt", {}), **y.get("unlockedAt", {})}
                    for k, v in x.get("unlockedAt", {}).items():
                        at[k] = min(v, y.get("unlockedAt", {}).get(k, v))
                    ids = sorted(set(x.get("unlockedTrophies", [])) | set(y.get("unlockedTrophies", [])))
                    b.write_text(json.dumps({"unlockedAt": at, "unlockedTrophies": ids}), "utf-8")
                    continue
                except (OSError, ValueError):
                    pass
            sa, sb = a.stat(), b.stat()
            if sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2:
                continue  # same file
            if prefer == "newer" and sa.st_mtime > sb.st_mtime:
                park(b, rel)
                shutil.copy2(a, b)
            else:
                park(a, rel)


def ensure_links(fork, vdir):
    """Share saves/trophies across forks; keep pipeline caches fork-specific."""
    with lock:
        base = data_dir(fork)
        shared = shared_user_data_dir()
        ensure_shared_fork_data(fork)
        for name in SAVE_DIRS:
            ensure_data_link(Path(vdir) / name, shared / name, fork["id"])
        name = "_PipelineCache"
        (base / name).mkdir(parents=True, exist_ok=True)
        ensure_data_link(Path(vdir) / name, base / name, fork["id"])


def ensure_shared_fork_data(fork):
    """Migrate this fork's legacy per-fork save data into the shared user store."""
    with lock:
        base, shared = data_dir(fork), shared_user_data_dir()
        for name in SAVE_DIRS:
            ensure_data_link(base / name, shared / name, fork["id"])


# ------------------------------------------------------ in-game menu hotkey --

def ui_front():
    """Bring the Studio window back over a running game."""
    try:
        import ctypes
        if WINDOW:
            WINDOW.restore()
        hwnd = ctypes.windll.user32.FindWindowW(None, "KytyPS5 Studio")
        if hwnd:
            _foreground(hwnd)
    except Exception:
        pass


def trigger_menu():
    global menu_req
    with lock:
        live = any(r["proc"].poll() is None for r in running.values())
        if live:
            menu_req += 1
    if live:
        ui_front()


def resume_game():
    with lock:
        live = [r for r in running.values() if r["proc"].poll() is None]
    if live:
        threading.Thread(target=focus_game, args=(live[-1]["proc"],), daemon=True).start()


class PadWatcher(threading.Thread):
    """Watches the controllers even while a game has the focus: PS button, or Create + Options together.

    Steam (when it manages the controller) swallows the PS button; Create + Options always gets through."""

    def __init__(self):
        super().__init__(daemon=True)
        self.prev = False

    def run(self):
        import ctypes
        from ctypes import Structure, byref, c_short, c_ubyte, c_uint, c_ushort, sizeof
        try:
            winmm = ctypes.windll.winmm
        except OSError:
            winmm = None
        try:
            xin = ctypes.windll.xinput1_4
            xget = getattr(xin, "#100", None) or xin.XInputGetState  # ordinal 100 also reports the Guide button
        except (OSError, AttributeError):
            xget = None

        class XPad(Structure):
            _fields_ = [("wButtons", c_ushort), ("bLeftTrigger", c_ubyte), ("bRightTrigger", c_ubyte),
                        ("sThumbLX", c_short), ("sThumbLY", c_short), ("sThumbRX", c_short), ("sThumbRY", c_short)]

        class XState(Structure):
            _fields_ = [("dwPacketNumber", c_uint), ("Gamepad", XPad)]

        class JoyInfo(Structure):
            _fields_ = [(n, c_uint) for n in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos",
                                              "dwVpos", "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1",
                                              "dwReserved2")]

        sony = {}
        while True:
            hit = False
            try:
                if xget:
                    for u in range(4):
                        st = XState()
                        if xget(u, byref(st)) == 0:
                            b = st.Gamepad.wButtons
                            if b & 0x0400 or (b & 0x30) == 0x30:  # Guide, or Back + Start
                                hit = True
                if winmm:
                    for j in range(min(winmm.joyGetNumDevs(), 16)):
                        info = JoyInfo()
                        info.dwSize, info.dwFlags = sizeof(JoyInfo), 0xFF
                        if winmm.joyGetPosEx(j, byref(info)) != 0:
                            sony.pop(j, None)
                            continue
                        if j not in sony:
                            caps = ctypes.create_string_buffer(728)
                            winmm.joyGetDevCapsW(j, caps, 728)
                            sony[j] = struct.unpack("<H", caps.raw[:2])[0] == 0x054C
                        b = info.dwButtons
                        if sony[j] and ((b >> 12) & 1 or ((b >> 8) & 1 and (b >> 9) & 1)):  # PS, or Create + Options
                            hit = True
            except Exception:
                if os.environ.get("KYTY_DEBUG"):
                    import traceback
                    traceback.print_exc()
            if hit and not self.prev:
                trigger_menu()
            self.prev = hit
            time.sleep(0.03)



# --------------------------------------------------- community forks & patches --

def read_json(path):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _text(v, n=600):
    return str(v or "")[:n]


def catalog():
    """The bundled catalog, overlaid by the optional remote one (entries are matched by id)."""
    base, remote = read_json(CATALOG_FILE) or {}, read_json(DATA / "catalog-remote.json") or {}
    out = {"updated": remote.get("updated") or base.get("updated"), "forks": [], "patches": [],
           "settings": remote.get("settings") or base.get("settings") or []}
    for key in ("forks", "patches"):
        items = {}
        for src in (base, remote):
            for x in src.get(key, []):
                if isinstance(x, dict) and re.fullmatch(r"[\w.-]{1,40}", str(x.get("id", ""))):
                    items[x["id"]] = {**items.get(x["id"], {}), **x}
        out[key] = list(items.values())
    return out


def catalog_url():
    """The catalog URL setting; "owner/repo" or a github.com page link become the raw catalog.json link."""
    u = (config["settings"].get("catalogUrl") or "").strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", u):
        return f"https://raw.githubusercontent.com/{u}/main/catalog.json"
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)/blob/(.+)", u)
    return f"https://raw.githubusercontent.com/{m[1]}/{m[2]}/{m[3]}" if m else u


def refresh_remote_catalog():
    url = catalog_url()
    if not url:
        return False
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or host not in CATALOG_HOSTS:
        raise ValueError("The catalog URL must be an https link on github.com or raw.githubusercontent.com")
    try:
        with http_get(url, timeout=20) as r:
            raw = r.read(2_000_001)
    except urllib.error.HTTPError as e:
        raise ValueError(f"Catalog not found at {url}" if e.code == 404 else f"Catalog request failed (HTTP {e.code})")
    if len(raw) > 2_000_000:
        raise ValueError("The catalog is too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The catalog is not a JSON object")
    DATA.mkdir(exist_ok=True)
    (DATA / "catalog-remote.json").write_text(json.dumps(data), "utf-8")
    return True


def fork_of_entry(e):
    return next((f for f in config["forks"] if f.get("catalogId") == e["id"] or f["repo"].lower() == str(e.get("repo", "")).lower()), None)


def patch_index():
    return read_json(PATCH_DIR / "index.json") or {"installed": {}}


def save_patch_index(idx):
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PATCH_DIR / "index.tmp"
    tmp.write_text(json.dumps(idx, indent=2), "utf-8")
    os.replace(tmp, PATCH_DIR / "index.json")


def validate_patch(data):
    """ETAHEN-style game patch: {"id": "PPSA#####", "mods": [{"enabled": ..., "memory": [...]}]}."""
    if not isinstance(data, dict) or not re.fullmatch(r"[A-Z]{4}\d{5}", str(data.get("id", ""))):
        raise ValueError("Not a game patch: the file needs an \"id\" such as PPSA21564")
    mods = data.get("mods")
    if not isinstance(mods, list) or not mods or not all(isinstance(m, dict) for m in mods):
        raise ValueError("Not a game patch: the file needs a non-empty \"mods\" list")
    return data["id"]


def store_patch(pid, raw, meta):
    if len(raw) > 2_000_000:
        raise ValueError("The patch file is too large")
    data = json.loads(raw.decode("utf-8-sig"))
    tid = validate_patch(data)
    folder = PATCH_DIR / tid
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{safe_name(pid)}.json"
    (folder / name).write_bytes(raw)
    idx = patch_index()
    keep = idx["installed"].get(pid, {}).get("mods")  # option choices survive an update
    idx["installed"][pid] = {**meta, "titleId": tid, "file": f"{tid}/{name}", "installedAt": time.time(),
                             "gameVersion": data.get("version") or "", **({"mods": keep} if keep else {})}
    save_patch_index(idx)
    for gid, g in game_index.items():  # switch it on for the games it belongs to, unless one is already chosen
        gv = g.get("version")
        if g.get("titleId") == tid and not config["games"].get(gid, {}).get("patch") and not (gv and data.get("version") and gv != data["version"]):
            config["games"].setdefault(gid, {})["patch"] = pid
    save_config()
    return tid


def install_catalog_patch(cid):
    e = next((p for p in catalog()["patches"] if p["id"] == cid), None)
    if not e:
        raise ValueError("Unknown patch")
    remote_base = catalog_url().rsplit("/", 1)[0]
    local = (COMMUNITY_DIR / e["file"]).resolve() if e.get("file") else None
    if local and COMMUNITY_DIR.resolve() in local.parents and local.is_file():
        raw = local.read_bytes()
    elif e.get("file") and remote_base.startswith("https://") and ".." not in e["file"]:
        url = f"{remote_base}/{e['file']}"
        host = urlparse(url).hostname or ""
        if host not in CATALOG_HOSTS:
            raise ValueError("Patches can only be downloaded from github.com")
        with http_get(url, timeout=20) as r:
            raw = r.read(2_000_001)
    elif e.get("url"):
        host = urlparse(e["url"]).hostname or ""
        if urlparse(e["url"]).scheme != "https" or host not in CATALOG_HOSTS:
            raise ValueError("Patches can only be downloaded from github.com")
        with http_get(e["url"], timeout=20) as r:
            raw = r.read(2_000_001)
    else:
        raise ValueError("The patch file is not in your community folder. Put it in the patches folder next to catalog.json, or use a catalog URL that hosts it.")
    if e.get("sha256") and hashlib.sha256(raw).hexdigest().lower() != str(e["sha256"]).lower():
        raise ValueError("The patch does not match its checksum; it was not installed")
    meta = {"name": _text(e.get("name"), 120), "game": _text(e.get("game"), 80), "version": _text(e.get("version"), 40),
            "catalogId": cid, "author": _text(e.get("author"), 60)}
    tid = store_patch(cid, raw, meta)
    if e.get("titleId") and e["titleId"] != tid:
        remove_patch(cid)
        raise ValueError("The patch is for a different game than the catalog says")
    return tid


def remove_patch(pid):
    idx = patch_index()
    info = idx["installed"].pop(pid, None)
    if info:
        try:
            (PATCH_DIR / info["file"]).unlink()
        except OSError:
            pass
        save_patch_index(idx)
    for st in config["games"].values():
        if st.get("patch") == pid:
            st["patch"] = None
    save_config()


def patch_file_for_game(game_id):
    pid = config["games"].get(game_id, {}).get("patch")
    info = pid and patch_index()["installed"].get(pid)
    g = game_index.get(game_id)
    if not info or not g or info.get("titleId") != g.get("titleId"):
        return None
    p = (PATCH_DIR / info["file"]).resolve()
    if PATCH_DIR.resolve() not in p.parents or not p.is_file():
        return None
    choices = info.get("mods")
    if not choices:
        return str(p)
    data = json.loads(p.read_text("utf-8-sig"))  # a copy with the user's option choices applied
    for m in data["mods"]:
        if m.get("name") in choices:
            m["enabled"] = bool(choices[m["name"]])
    out = PATCH_DIR / "active"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{safe_name(pid)}.json"
    f.write_text(json.dumps(data), "utf-8")
    return str(f)


def patch_mods(pid):
    info = patch_index()["installed"].get(pid)
    if not info:
        raise ValueError("Patch not installed")
    data = json.loads((PATCH_DIR / info["file"]).read_text("utf-8-sig"))
    choices = info.get("mods") or {}
    return {"name": info.get("name") or data.get("name"), "gameVersion": info.get("gameVersion", ""),
            "mods": [{"name": _text(m.get("name"), 400), "default": bool(m.get("enabled")),
                      "enabled": bool(choices.get(m.get("name"), m.get("enabled")))} for m in data.get("mods", [])]}


def set_patch_mods(pid, changes):
    idx = patch_index()
    info = idx["installed"].get(pid)
    if not info:
        raise ValueError("Patch not installed")
    names = {m["name"] for m in patch_mods(pid)["mods"]}
    mods = dict(info.get("mods") or {})
    for k, v in changes.items():
        if k in names:
            mods[k] = bool(v)
    info["mods"] = mods
    save_patch_index(idx)


def community_state():
    cat = catalog()
    forks = []
    for e in cat["forks"]:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(e.get("repo", ""))):
            continue
        f = fork_of_entry(e)
        rels = (release_cache.get(e["repo"]) or {}).get("releases", [])
        latest = rels[0] if rels else None
        cur = current_meta(f) if f else None
        forks.append({
            "id": e["id"], "name": _text(e.get("name"), 120), "repo": e["repo"], "author": _text(e.get("author"), 60),
            "description": _text(e.get("description"), 1200), "notes": _text(e.get("notes"), 600),
            "games": [_text(x, 60) for x in e.get("games", [])][:8], "tags": [_text(x, 30) for x in e.get("tags", [])][:8],
            "forkId": f["id"] if f else None, "installed": cur["tag"] if cur else None,
            "latest": {"tag": latest["tag"], "published": latest["published"], "prerelease": latest["prerelease"],
                       "size": latest["asset"]["size"]} if latest else None,
            "updateAvailable": bool(latest and cur and latest["tag"] != cur["tag"]),
            "checkError": (release_cache.get(e["repo"]) or {}).get("error"),
        })
    idx = patch_index()["installed"]
    patches, seen = [], set()
    for e in cat["patches"]:
        inst = idx.get(e["id"])
        seen.add(e["id"])
        patches.append({"id": e["id"], "name": _text(e.get("name"), 120), "game": _text(e.get("game"), 80),
                        "titleId": _text(e.get("titleId"), 12), "description": _text(e.get("description"), 1000),
                        "author": _text(e.get("author"), 60), "version": _text(e.get("version"), 40),
                        "gameVersion": _text(e.get("gameVersion"), 20), "options": int(e.get("options") or 0),
                        "installed": bool(inst), "installedVersion": inst.get("version") if inst else None,
                        "updateAvailable": bool(inst and str(inst.get("version")) != str(e.get("version", inst.get("version")))),
                        "custom": False})
    for pid, inst in idx.items():
        if pid not in seen:
            patches.append({"id": pid, "name": inst.get("name") or pid, "game": inst.get("game") or inst["titleId"],
                            "titleId": inst["titleId"], "description": "Imported from a file on this PC.", "author": "", "version": "",
                            "installed": True, "installedVersion": "", "updateAvailable": False, "custom": True})
    return {"updated": cat["updated"], "forks": forks, "patches": patches, "catalogUrl": config["settings"].get("catalogUrl", ""),
            "empty": not cat["forks"] and not cat["patches"], "folder": str(COMMUNITY_DIR)}


def add_catalog_fork(cid):
    e = next((x for x in catalog()["forks"] if x["id"] == cid), None)
    if not e or not re.fullmatch(r"[\w.-]+/[\w.-]+", str(e.get("repo", ""))):
        raise ValueError("Unknown fork")
    f = fork_of_entry(e)
    if f:
        f.setdefault("catalogId", cid)
        save_config()
        return f
    fid = safe_name(cid.lower())
    while get_fork(fid):
        fid += "_"
    f = {"id": fid, "name": _text(e.get("name"), 120).split(" \u00b7 ")[0] or cid, "badge": "Community", "repo": e["repo"],
         "tagline": _text(e.get("description"), 90), "catalogId": cid, "current": None}
    config["forks"].append(f)
    save_config()
    return f


def pick_folder_for(title):
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title=title)
    finally:
        root.destroy()


def import_catalog(src):
    """Copy a catalog (catalog.json, plus an optional patches/ folder) into the user's community folder."""
    src = Path(src)
    if src.is_file():
        src, cat_file = src.parent, src
    else:
        cat_file = src / "catalog.json"
    data = read_json(cat_file)
    if not isinstance(data, dict) or not (isinstance(data.get("forks"), list) or isinstance(data.get("patches"), list)):
        raise ValueError("That folder has no valid catalog.json")
    COMMUNITY_DIR.mkdir(parents=True, exist_ok=True)
    if CATALOG_FILE.exists():
        shutil.copy2(CATALOG_FILE, CATALOG_FILE.with_suffix(".json.bak"))
    shutil.copy2(cat_file, CATALOG_FILE)
    n = 0
    pdir = src / "patches"
    if pdir.is_dir():
        (COMMUNITY_DIR / "patches").mkdir(exist_ok=True)
        for p in list(pdir.glob("*.json"))[:300]:
            if p.stat().st_size <= 2_000_000:
                shutil.copy2(p, COMMUNITY_DIR / "patches" / p.name)
                n += 1
    return {"forks": len(data.get("forks", [])), "patches": len(data.get("patches", [])), "patchFiles": n}


def pick_file():
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(title="Choose a game patch", filetypes=[("Game patch (JSON)", "*.json")])
    finally:
        root.destroy()


# ------------------------------------------------------------ shader cache --

def game_fork(game_id):
    st = config["games"].get(game_id, {})
    fork = get_fork(st.get("fork") or config["defaultFork"])
    if not (fork and current_meta(fork)):
        fork = next((f for f in config["forks"] if current_meta(f)), None)
    return fork


def shader_info(game_id):
    g = game_index.get(game_id)
    fork = game_fork(game_id)
    if not g or not fork:
        raise ValueError("Install a fork first")
    meta = current_meta(fork)
    vdir = version_dir(fork, meta["tag"])
    tid = g.get("titleId") or ""
    cache = data_dir(fork) / "_PipelineCache"
    size, files = 0, 0
    if cache.is_dir() and tid:
        for root, _, names in os.walk(cache):
            for n in names:
                if tid in n:
                    size += os.path.getsize(os.path.join(root, n))
                    files += 1
    env = fork_env(vdir)
    script = next((p for p in ("precompile-windows.ps1",) if (vdir / p).exists()), None)
    return {"fork": fork["name"], "forkId": fork["id"], "megabytes": round(size / 1048576, 1), "files": files,
            "auto": env.get("KYTY_SHADER_PRECOMPILE") == "1", "precompile": bool(script)}


def start_precompile(game_id, mode):
    g = game_index.get(game_id)
    fork = game_fork(game_id)
    info = shader_info(game_id)
    if not info["precompile"]:
        raise ValueError("This fork has no shader precompile tool")
    vdir = version_dir(fork, current_meta(fork)["tag"])
    ensure_links(fork, vdir)
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(vdir / "precompile-windows.ps1"),
            "-Game", g["path"]]
    if mode == "quick":
        args.append("-Quick")
    subprocess.Popen(args, cwd=str(vdir), env=fork_env(vdir), creationflags=0x00000010)  # its own console window


def clear_shader_cache(game_id):
    g = game_index.get(game_id)
    fork = game_fork(game_id)
    tid = g and g.get("titleId")
    if not (fork and tid):
        raise ValueError("Nothing to clear")
    if any(r["proc"].poll() is None for r in running.values()):
        raise ValueError("Close the game first")
    n = 0
    for root, _, names in os.walk(data_dir(fork) / "_PipelineCache"):
        for name in names:
            if tid in name:
                os.remove(os.path.join(root, name))
                n += 1
    return n


# ------------------------------------------------------------------ trophies --

TROPHY_POINTS = {"B": 15, "S": 30, "G": 90, "P": 300}
LEVEL_STEPS = [(1, 60), (100, 90), (200, 450), (300, 900), (400, 1350), (500, 1800), (600, 2250), (700, 2700),
               (800, 3150), (900, 3600)]
ucp_cache = {}
UCP_MAGIC = b"\xb2\x28\xc6\x0a"
zar_trophy_attempts = {}
ZAR_BLOCK_SIZE = 64 * 1024
ZAR_ENTRIES_PER_RECORD = 16
ZAR_FOOTER_SIZE = 144
ZAR_REQUIRED_SUFFIXES = ("sce_sys/param.json", "sce_sys/icon0.png", "sce_sys/pic0.png",
                         "sce_sys/pic1.png", "sce_sys/trophy2/trophy00.ucp")
zar_index_cache = {}


def zar_find_entry(archive, suffix):
    """Return the virtual path and byte range for a file inside a ZAR."""
    archive = Path(archive)
    cache_key = str(archive.resolve()).casefold()
    mtime = archive.stat().st_mtime_ns
    cached = zar_index_cache.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1].get(suffix.casefold())
    with open(archive, "rb") as f:
        length = os.fstat(f.fileno()).st_size
        if length < ZAR_FOOTER_SIZE:
            raise ValueError("ZAR is too small to contain a footer")
        f.seek(length - ZAR_FOOTER_SIZE)
        footer = f.read(ZAR_FOOTER_SIZE)
        if struct.unpack(">I", footer[136:140])[0] != 0x61BF3A01 or struct.unpack(">I", footer[140:144])[0] != 0x169F52D6:
            raise ValueError("Invalid ZAR footer")
        if struct.unpack(">Q", footer[128:136])[0] != length:
            raise ValueError("ZAR size does not match its footer")
        sections = struct.unpack(">12Q", footer[:96])
        data_offset, data_size = sections[0:2]
        records_offset, records_size = sections[2:4]
        names_offset, names_size = sections[4:6]
        tree_offset, tree_size = sections[6:8]
        limit = length - ZAR_FOOTER_SIZE
        for offset, size in ((data_offset, data_size), (records_offset, records_size),
                             (names_offset, names_size), (tree_offset, tree_size)):
            if offset + size > limit:
                raise ValueError("ZAR section is outside the file")
        if records_size % 40 or tree_size < 16 or tree_size % 16:
            raise ValueError("Malformed ZAR index")
        if names_size > 128 * 1024 * 1024 or tree_size > 512 * 1024 * 1024:
            raise ValueError("ZAR index is too large to inspect safely")
        f.seek(names_offset)
        names = f.read(names_size)
        f.seek(tree_offset)
        tree = f.read(tree_size)

    count = len(tree) // 16
    wanted_suffixes = tuple(value.casefold() for value in ZAR_REQUIRED_SUFFIXES)
    found = {}
    stack, guard = [(0, "")], 0
    while stack:
        index, parent = stack.pop()
        guard += 1
        if guard > count + 1:
            raise ValueError("ZAR directory tree contains a loop")
        name_field, a, b, c = struct.unpack_from(">4I", tree, index * 16)
        is_file = bool(name_field & 0x80000000)
        if index == 0:
            name, path = "", ""
        else:
            name_offset = name_field & 0x7FFFFFFF
            if name_offset >= len(names):
                raise ValueError("ZAR name points outside the name table")
            size = names[name_offset]
            start = name_offset + 1
            if size & 0x80:
                if start >= len(names):
                    raise ValueError("Truncated ZAR filename")
                size = (size & 0x7F) | (names[start] << 7)
                start += 1
            if start + size > len(names):
                raise ValueError("Truncated ZAR filename")
            name = names[start:start + size].decode("utf-8", "replace")
            path = f"{parent}/{name}" if parent else name
        if is_file:
            folded = path.casefold()
            if any(folded.endswith(wanted) for wanted in wanted_suffixes):
                file_offset = a | ((c & 0xFFFF) << 32)
                file_size = b | ((c & 0xFFFF0000) << 16)
                entry = (path, file_offset, file_size, data_offset, data_size, records_offset, records_size)
                for wanted in wanted_suffixes:
                    if folded.endswith(wanted):
                        found[wanted] = entry
        else:
            if a + b > count:
                raise ValueError("ZAR directory range is invalid")
            for child in range(a, a + b):
                stack.append((child, path))
    zar_index_cache[cache_key] = (mtime, found)
    return found.get(suffix.casefold())


def zar_extract_entry(archive, entry, output):
    path, start, file_size, data_offset, data_size, records_offset, records_size = entry
    if file_size > 128 * 1024 * 1024:
        raise ValueError("Trophy pack exceeds the safe extraction limit")
    try:
        import zstandard
    except ImportError as e:
        raise RuntimeError("The ZAR reader dependency is missing; reinstall Kyty Studio") from e
    decompressor = zstandard.ZstdDecompressor()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    position = start
    remaining = file_size
    try:
        with open(archive, "rb") as src, open(temporary, "wb") as dst:
            while remaining:
                block = position // ZAR_BLOCK_SIZE
                within = position % ZAR_BLOCK_SIZE
                record = block // ZAR_ENTRIES_PER_RECORD
                slot = block % ZAR_ENTRIES_PER_RECORD
                record_pos = records_offset + record * 40
                if record_pos + 40 > records_offset + records_size:
                    raise ValueError("ZAR block index is out of range")
                src.seek(record_pos)
                rec = src.read(40)
                base = struct.unpack_from(">Q", rec, 0)[0]
                sizes = struct.unpack_from(">16H", rec, 8)
                stored_offset = base + sum(value + 1 for value in sizes[:slot])
                stored_size = sizes[slot] + 1
                if stored_offset + stored_size > data_size:
                    raise ValueError("ZAR data block is out of range")
                src.seek(data_offset + stored_offset)
                stored = src.read(stored_size)
                if len(stored) != stored_size:
                    raise ValueError("ZAR data block is truncated")
                block_data = stored if stored_size == ZAR_BLOCK_SIZE else decompressor.decompress(
                    stored, max_output_size=ZAR_BLOCK_SIZE)
                if len(block_data) != ZAR_BLOCK_SIZE:
                    raise ValueError("ZAR block decompressed to an invalid size")
                take = min(remaining, ZAR_BLOCK_SIZE - within)
                dst.write(block_data[within:within + take])
                remaining -= take
                position += take
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def cached_zar_file(archive, virtual_path, output, max_size):
    output = Path(output)
    if output.is_file():
        return output
    entry = zar_find_entry(archive, virtual_path)
    if not entry:
        return None
    if entry[2] > max_size:
        raise ValueError(f"{virtual_path} exceeds the safe extraction limit")
    zar_extract_entry(archive, entry, output)
    return output


def cached_zar_trophy(archive):
    sidecar_path = zar_sidecar(archive) / "trophy" / "trophy00.ucp"
    if sidecar_path.is_file():
        return sidecar_path
    archive = Path(archive)
    mtime = archive.stat().st_mtime_ns
    cache_key = str(archive.resolve()).casefold()
    if zar_trophy_attempts.get(cache_key) == mtime:
        return None
    zar_trophy_attempts[cache_key] = mtime
    return cached_zar_file(archive, "sce_sys/trophy2/trophy00.ucp", sidecar_path, 128 * 1024 * 1024)



def ucp_open(path):
    """Index a PS5 trophy pack (trophy00.ucp): big-endian directory of name/offset/size entries."""
    key, mtime = str(path), os.stat(path).st_mtime
    c = ucp_cache.get(key)
    if c and c["mtime"] == mtime:
        return c
    with open(path, "rb") as f:
        head = f.read(0x60)
        if head[:4] != UCP_MAGIC:
            raise ValueError("not a trophy pack")
        count, esize = struct.unpack(">II", head[0x10:0x18])
        raw = f.read(count * esize)
    ents = {}
    for i in range(count):
        o = i * esize
        name = raw[o:o + 32].split(b"\0")[0].decode("utf-8", "replace")
        ents[name] = struct.unpack(">QQ", raw[o + 32:o + 48])
    c = ucp_cache[key] = {"mtime": mtime, "path": key, "ents": ents}
    return c


def ucp_read(c, name):
    off, size = c["ents"][name]
    with open(c["path"], "rb") as f:
        f.seek(off)
        return f.read(size)


def pick_language(c, want, default):
    have = {n[len("tropmeta_"):-5] for n in c["ents"] if n.startswith("tropmeta_")}
    base = (want or "en-US").split("-")[0].lower()
    order = [want, *[h for h in sorted(have) if h.lower().split("-")[0] == base], "en-US", "en-GB", default, *sorted(have)]
    return next(x for x in order if x in have)


def earned_map(title_id, uid=None):
    """Unlock state written by any installed fork/version. Values are unix seconds."""
    dirs = [shared_user_data_dir() / "_Trophies" / title_id]
    for f in config["forks"]:
        dirs.append(data_dir(f) / "_Trophies" / title_id)
        base = FORKS_DIR / f["id"]
        if base.exists():
            dirs += [v / "_Trophies" / title_id for v in base.iterdir() if v.is_dir() and v.name != "_data"
                     and not is_link(v / "_Trophies")]
    out = {}
    for d in dirs:
        for p in d.glob(f"trophies_{uid}_*.json" if uid is not None else "trophies_*.json") if d.is_dir() else []:
            try:
                j = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            for tid in j.get("unlockedTrophies", []):
                us = j.get("unlockedAt", {}).get(str(tid))
                ts = int(us / 1e6 - 62135596800) if us else 0  # microseconds since 0001-01-01
                out[int(tid)] = min(out.get(int(tid), ts or 1 << 60), ts or 1 << 60)
    return {k: (v if v < 1 << 60 else 0) for k, v in out.items()}


def trophy_pack_of(gid):
    g = game_index.get(gid)
    if not g:
        return None
    path = Path(g["path"])
    if g.get("format") == "ZAR":
        source_dump = path.with_suffix("")
        if (source_dump / "sce_sys" / "trophy2" / "trophy00.ucp").is_file():
            return source_dump / "sce_sys" / "trophy2" / "trophy00.ucp"
        try:
            return cached_zar_trophy(path)
        except (OSError, ValueError, RuntimeError, struct.error):
            return None
    p = path / "sce_sys" / "trophy2" / "trophy00.ucp"
    return p if p.is_file() else None


def trophy_level(points):
    left, level = points, 1
    while level < 999:
        cost = next(c for st, c in reversed(LEVEL_STEPS) if level >= st)
        if left < cost:
            return level, left / cost
        left -= cost
        level += 1
    return 999, 0.0


def trophy_set(gid, lang):
    pack = trophy_pack_of(gid)
    if not pack:
        return None
    c = ucp_open(pack)
    conf = json.loads(ucp_read(c, "tropconf.json").decode("utf-8", "replace"))
    use = pick_language(c, lang, conf.get("defaultLanguage"))
    meta = json.loads(ucp_read(c, f"tropmeta_{use}.json").decode("utf-8", "replace"))["metadata"]
    names = {t["id"]: t for t in meta.get("trophyMetadata", [])}
    groups = {g["id"]: g.get("name", "") for g in meta.get("groupMetadata", [])}
    title_id = game_title_id(gid)
    earned = earned_map(title_id, active_user()["uid"]) if title_id else {}
    out = []
    for t in conf["trophies"]:
        n = int(t["id"])
        m = names.get(t["id"], {})
        got = n in earned
        hidden = bool(t.get("hidden")) and not got
        out.append({"id": n, "grade": t["grade"], "hidden": bool(t.get("hidden")), "earned": got, "at": earned.get(n, 0),
                    "name": "Hidden Trophy" if hidden else m.get("name", f"Trophy {n}"),
                    "detail": "This is a hidden trophy. Keep playing to find out how to earn it." if hidden else m.get("detail", ""),
                    "icon": None if hidden else (f"trop{t['id']}.png" if f"trop{t['id']}.png" in c["ents"] else None),
                    "group": t.get("groupId", "default")})
    return {"title": meta.get("titleMetadata", {}).get("name"), "language": use, "groups": groups, "trophies": out}


def game_title_id(gid):
    g = game_index.get(gid)
    return g and g.get("titleId")


def tally(trophies):
    have, total = {g: 0 for g in "PGSB"}, {g: 0 for g in "PGSB"}
    pts = 0
    for t in trophies:
        total[t["grade"]] += 1
        if t["earned"]:
            have[t["grade"]] += 1
            pts += TROPHY_POINTS[t["grade"]]
    n, e = len(trophies), sum(have.values())
    return {"total": total, "earned": have, "points": pts, "count": n, "earnedCount": e,
            "progress": int(e * 100 / n) if n else 0}


def trophy_overview(lang):
    games = scan_games()
    rows, pts, have = [], 0, {g: 0 for g in "PGSB"}
    for g in games:
        try:
            ts = trophy_set(g["id"], lang)
        except Exception:
            continue
        if not ts:
            continue
        t = tally(ts["trophies"])
        for k in have:
            have[k] += t["earned"][k]
        pts += t["points"]
        rows.append({"game": g["id"], "title": ts["title"] or g["title"], "titleId": g["titleId"], "icon": g["icon"], **t})
    rows.sort(key=lambda r: (-r["earnedCount"], r["title"].lower()))
    level, frac = trophy_level(pts)
    return {"games": rows, "points": pts, "level": level, "levelProgress": int(frac * 100), "earned": have,
            "earnedCount": sum(have.values())}


# ------------------------------------------------------------------ state --

def fork_view(f):
    cur = current_meta(f)
    c = release_cache.get(f["repo"]) or {}
    rels = c.get("releases", [])
    latest = rels[0] if rels else None
    return {
        "id": f["id"], "name": f["name"], "badge": f.get("badge", "Fork"), "repo": f["repo"],
        "tagline": f.get("tagline", ""), "builtin": bool(f.get("builtin")), "community": bool(f.get("catalogId")),
        "current": cur, "versions": installed_versions(f),
        "latest": latest, "releases": [{k: r[k] for k in ("tag", "name", "published", "prerelease")} for r in rels],
        "updateAvailable": bool(latest and cur and latest["tag"] != cur["tag"]),
        "checkError": c.get("error"), "checkedAt": c.get("at"),
    }


def state():
    with lock:
        live = {k: v for k, v in jobs.items() if time.time() - v["started"] < 3600}
        jobs.clear()
        jobs.update(live)
        return {
            "forks": [fork_view(f) for f in config["forks"]],
            "settings": {k: v for k, v in config["settings"].items() if k != "githubToken"},
            "hasToken": bool(config["settings"].get("githubToken")),
            "libraries": config["libraries"], "defaultFork": config["defaultFork"],
            "users": config["users"], "activeUser": config["activeUser"],
            "jobs": list(jobs.values()), "running": [public_run(r) for r in running.values()],
            "zarJobs": list(zar_jobs.values()), "zarAvailable": bool(zarchive_tool()),
            "now": time.time(), "menuReq": menu_req,
        }


def pick_folder():
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title="Choose a folder")
    finally:
        root.destroy()


# ---------------------------------------------------------------- plugins ---
# Optional add-ons, installed by the user as DATA/plugins/<id>/ . Studio ships none and does not know what one does:
# it only lists them, loads the ones that are enabled and gives them an API (see PLUGINS.md). A plugin is trusted
# code: its script runs in the app window and its optional backend runs in this process with the user's permissions.

PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
PLUGIN_ASSET = re.compile(r"^[\w.-]+\.(js|css|png|jpg|jpeg|svg|webp|woff2|json)$", re.I)
plugin_routes = {}  # (plugin id, route name) -> callable
plugin_errors = {}


def plugin_log(pid, *parts):
    """DATA/logs/plugin-<id>.log: what a plugin logged, its backend errors and its script errors."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  " + " ".join(str(p) for p in parts)
    print(f"[plugin {pid}]", *parts, flush=True)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log = LOGS_DIR / f"plugin-{pid}.log"
        if log.exists() and log.stat().st_size > 1_000_000:  # keep one older copy, never grow without bound
            os.replace(log, log.with_suffix(".old.log"))
        with open(log, "a", encoding="utf-8", errors="replace") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def plugin_manifests():
    out = []
    if not PLUGINS_DIR.is_dir():
        return out
    for folder in sorted(PLUGINS_DIR.iterdir()):
        if not folder.is_dir() or not PLUGIN_ID.match(folder.name):
            continue
        try:
            m = json.loads((folder / "plugin.json").read_text("utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("id") != folder.name:
            continue

        def part(key, ext):
            v = m.get(key)
            return v if isinstance(v, str) and re.fullmatch(rf"[\w.-]+\.{ext}", v) and (folder / v).is_file() else None
        out.append({"id": folder.name, "name": _text(m.get("name") or folder.name, 60), "version": _text(m.get("version"), 20),
                    "author": _text(m.get("author"), 60), "description": _text(m.get("description"), 300),
                    "script": part("script", "js"), "styles": part("styles", "css"), "backend": part("backend", "py"),
                    "enabled": folder.name not in config.get("pluginsDisabled", [])})
    return out


class PluginHost:
    """What a plugin backend gets (as the name `studio`) instead of Studio's internals."""

    def __init__(self, pid):
        self.id = pid
        self.data_dir = PLUGIN_DATA_DIR / pid  # the plugin's own folder; created on first use

    def route(self, name, fn=None):
        """Expose fn(body: dict) -> JSON-able at /api/plugins/<id>/<name>; usable as a decorator."""
        if not re.fullmatch(r"[a-z0-9-]{1,40}", name):
            raise ValueError("route names are 1-40 characters of a-z, 0-9 and -")

        def register(f):
            plugin_routes[(self.id, name)] = f
            return f
        return register(fn) if fn else register

    def load(self, default=None):
        return read_json(self.data_dir / "state.json") or default

    def save(self, obj):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "state.json").write_text(json.dumps(obj), "utf-8")

    def log(self, *a):
        plugin_log(self.id, *a)


def load_plugin_backends():
    for p in plugin_manifests():
        if not p["enabled"] or not p["backend"]:
            continue
        src = PLUGINS_DIR / p["id"] / p["backend"]
        try:
            ns = {"__name__": "studio_plugin_" + p["id"].replace("-", "_"), "studio": PluginHost(p["id"])}
            exec(compile(src.read_text("utf-8-sig"), str(src), "exec"), ns)
        except Exception as e:  # a broken add-on must never stop Studio
            plugin_errors[p["id"]] = f"{e.__class__.__name__}: {e}"
            plugin_log(p["id"], "backend failed to load:\n" + traceback.format_exc())
            for k in [k for k in plugin_routes if k[0] == p["id"]]:
                del plugin_routes[k]


# ------------------------------------------------------------------- HTTP --

class Handler(BaseHTTPRequestHandler):
    server_version = "KytyStudio"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, mime=None, cache=False):
        try:
            f = open(path, "rb")
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        with f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
            self.end_headers()
            try:
                while chunk := f.read(256 * 1024):  # big art files: stream, tolerate aborted requests
                    self.wfile.write(chunk)
            except (ConnectionError, OSError):
                pass

    def send_bytes(self, data, mime, cache=False):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return (self.headers.get("Host") or "") in ALLOWED_HOSTS

    def authed(self, q):
        return secrets.compare_digest(self.headers.get("X-Studio-Token") or (q.get("t") or [""])[0], TOKEN)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self.host_ok():
            return self.send_json({"error": "bad host"}, 403)
        if u.path.startswith("/api/"):
            if not self.authed(q):
                return self.send_json({"error": "unauthorized"}, 401)
            return self.route("GET", u.path, q, {})
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (UI / rel).resolve()
        if UI.resolve() not in f.parents and f != UI.resolve():
            return self.send_json({"error": "forbidden"}, 403)
        if rel == "index.html":
            html = f.read_text("utf-8").replace("__TOKEN__", TOKEN)
            return self.send_file_bytes(html.encode())
        self.send_file(f)

    def send_file_bytes(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        if not self.host_ok() or not self.authed({}):
            return self.send_json({"error": "unauthorized"}, 401)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json({"error": "bad json"}, 400)
        self.route("POST", u.path, {}, body)

    def route(self, method, path, q, body):
        try:
            self.dispatch(method, path, q, body)
        except (ValueError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": f"{e.__class__.__name__}: {e}"}, 500)

    def plugin_api(self, method, path, q, body):
        parts = path.split("/")  # ["", "api", "plugins", ...]
        manifests = {p["id"]: p for p in plugin_manifests()}
        if len(parts) == 3:
            if method != "GET":
                raise ValueError("Unknown endpoint")
            return self.send_json({"plugins": [{**p, "error": plugin_errors.get(p["id"])} for p in manifests.values()], "dir": str(PLUGINS_DIR)})
        if parts[3] == "log" and method == "POST":  # script errors reported by the app window
            if str(body.get("id")) in manifests:
                plugin_log(str(body["id"]), "script:", str(body.get("text", ""))[:2000])
            return self.send_json({"ok": True})
        if parts[3] == "enable" and method == "POST":
            p = manifests.get(str(body.get("id")))
            if not p:
                raise ValueError("Unknown plugin")
            off = [i for i in config.get("pluginsDisabled", []) if i != p["id"]]
            if not body.get("enabled"):
                off.append(p["id"])
            config["pluginsDisabled"] = off
            save_config()
            return self.send_json({"ok": True, "restart": True})
        if parts[3] == "ui" and method == "GET" and len(parts) == 6:
            p = manifests.get(parts[4])
            f = (PLUGINS_DIR / parts[4] / parts[5]).resolve() if p and p["enabled"] and PLUGIN_ASSET.match(parts[5]) else None
            if not f or f.parent != (PLUGINS_DIR / parts[4]).resolve() or not f.is_file():
                return self.send_json({"error": "not found"}, 404)
            return self.send_file(f)
        if len(parts) == 5 and PLUGIN_ID.match(parts[3]):
            fn = plugin_routes.get((parts[3], parts[4])) if manifests.get(parts[3], {}).get("enabled") else None
            if not fn:
                return self.send_json({"error": "unknown plugin route"}, 404)
            try:
                return self.send_json(fn(body if method == "POST" else {k: v[0] for k, v in q.items() if k != "t"}))
            except Exception:
                plugin_log(parts[3], f"route {parts[4]} failed:\n" + traceback.format_exc())
                raise
        raise ValueError("Unknown endpoint")

    def dispatch(self, method, path, q, body):
        if path.startswith("/api/plugins"):
            return self.plugin_api(method, path, q, body)
        if method == "GET":
            if path == "/api/state":
                return self.send_json(state())
            if path == "/api/games":
                return self.send_json(scan_games())
            if path.startswith("/api/art/"):
                _, _, _, gid, kind = path.split("/")
                g = game_index.get(gid)
                p = g and g.get({"icon": "icon", "bg": "bg", "fg": "fg"}.get(kind, ""))
                return self.send_file(p, cache=True) if p else self.send_json({"error": "no art"}, 404)
            if path == "/api/community":
                return self.send_json(community_state())
            if path == "/api/patches":
                scan_games()
                gid = (q.get("game") or [""])[0]
                tid = (game_index.get(gid) or {}).get("titleId")
                idx = patch_index()["installed"]
                gver = (game_index.get(gid) or {}).get("version")
                return self.send_json({"selected": config["games"].get(gid, {}).get("patch"), "titleId": tid, "gameVersion": gver,
                                       "patches": [{"id": k, "name": v.get("name") or k, "version": v.get("version", ""),
                                                    "gameVersion": v.get("gameVersion", ""),
                                                    "compatible": not (v.get("gameVersion") and gver and v["gameVersion"] != gver)}
                                                   for k, v in idx.items() if v.get("titleId") == tid]})
            if path == "/api/patch/mods":
                return self.send_json(patch_mods((q.get("id") or [""])[0]))
            if path == "/api/shaders":
                scan_games()
                return self.send_json(shader_info((q.get("game") or [""])[0]))
            if path == "/api/trophies":
                return self.send_json(trophy_overview((q.get("lang") or ["en-US"])[0]))
            if path == "/api/trophies/game":
                scan_games()
                gid = (q.get("id") or [""])[0]
                ts = trophy_set(gid, (q.get("lang") or ["en-US"])[0])
                if not ts:
                    raise ValueError("This game has no trophy data")
                ts.update(tally(ts["trophies"]))
                return self.send_json(ts)
            if path.startswith("/api/trophy-art/"):
                _, _, _, gid, name = path.split("/")
                pack = trophy_pack_of(gid)
                if not pack or not re.fullmatch(r"[\w.-]+\.png", name):
                    return self.send_json({"error": "no art"}, 404)
                c = ucp_open(pack)
                if name not in c["ents"]:
                    return self.send_json({"error": "no art"}, 404)
                return self.send_bytes(ucp_read(c, name), "image/png", cache=True)
            if path == "/api/log":
                name = Path((q.get("name") or [""])[0]).name
                f = LOGS_DIR / name
                text = f.read_text("utf-8", "replace") if f.is_file() else ""
                return self.send_json({"text": "\n".join(text.splitlines()[-300:])})
        else:
            if path == "/api/check":
                refresh_all(force=True)
                return self.send_json(state())
            if path == "/api/install":
                fork = get_fork(body["fork"])
                if not fork:
                    raise ValueError("Unknown fork")
                return self.send_json(start_install(fork, body.get("tag")))
            if path == "/api/use":  # switch the active installed version
                fork = get_fork(body["fork"])
                if not any(m["tag"] == body["tag"] for m in installed_versions(fork)):
                    raise ValueError("Version not installed")
                fork["current"] = body["tag"]
                save_config()
                return self.send_json(state())
            if path == "/api/fork/add":
                m = re.fullmatch(r"\s*(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*", body["repo"])
                if not m:
                    raise ValueError("Use the form owner/repo")
                repo = f"{m[1]}/{m[2]}"
                if any(f["repo"].lower() == repo.lower() for f in config["forks"]):
                    raise ValueError("This fork is already added")
                fid = safe_name(m[1].lower())
                while get_fork(fid):
                    fid += "_"
                config["forks"].append({"id": fid, "name": body.get("name") or m[1], "badge": "Fork", "repo": repo,
                                        "tagline": f"github.com/{repo}", "current": None})
                save_config()
                threading.Thread(target=fetch_releases, args=(repo, True), daemon=True).start()
                return self.send_json(state())
            if path == "/api/fork/remove":
                fork = get_fork(body["fork"])
                if not fork or fork.get("builtin"):
                    raise ValueError("Built-in forks cannot be removed")
                if any(r["fork"] == fork["id"] and r["proc"].poll() is None for r in running.values()):
                    raise ValueError("A game is running on this fork")
                ensure_shared_fork_data(fork)
                for m in installed_versions(fork):
                    rm_version(version_dir(fork, m["tag"]))
                keep = data_dir(fork)
                if keep.exists():  # never throw saves away with a fork: park them in a backup folder
                    dst = DATA / "userdata-backup" / f"{fork['id']}-{time.strftime('%Y%m%d-%H%M%S')}"
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(keep), str(dst))
                shutil.rmtree(FORKS_DIR / fork["id"], ignore_errors=True)
                config["forks"].remove(fork)
                if config["defaultFork"] == fork["id"]:
                    config["defaultFork"] = "original"
                save_config()
                return self.send_json(state())
            if path == "/api/fork/default":
                if not get_fork(body["fork"]):
                    raise ValueError("Unknown fork")
                config["defaultFork"] = body["fork"]
                save_config()
                return self.send_json(state())
            if path == "/api/launch":
                return self.send_json(public_run(launch(body["game"], body.get("fork"))))
            if path == "/api/stop":
                r = running.get(body["run"])
                if r and r["proc"].poll() is None:
                    r["proc"].terminate()
                return self.send_json({"ok": True})
            if path == "/api/game/set":  # per-game fork + overrides
                scan = game_index.get(body["game"])
                if not scan:
                    raise ValueError("Game not found")
                st = config["games"].setdefault(body["game"], {})
                if "fork" in body:
                    st["fork"] = body["fork"] if get_fork(body["fork"]) else None
                if "overrides" in body:
                    st["overrides"] = {k: v for k, v in body["overrides"].items()
                                       if k in DEFAULT_SETTINGS and k != "githubToken"}
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/forkopts":  # a fork's own documented options + what is set (fork level and, if given, for a game)
                fork = get_fork(body["fork"])
                if not fork:
                    raise ValueError("Unknown fork")
                meta = current_meta(fork)
                gid = body.get("game")
                own = config.get("forkOpts", {}).get(fork["id"], {})
                gown = config["games"].get(gid, {}).get("forkOpts", {}).get(fork["id"], {}) if gid else {}
                preset = sorted(k for k in fork_env(version_dir(fork, meta["tag"])) if k.startswith("KYTY")) if meta else []
                return self.send_json({"installed": bool(meta), "tag": meta["tag"] if meta else None,
                                       "options": fork_options(fork, meta), "fork": own.get("args", {}), "forkEnv": own.get("env", ""),
                                       "game": gown.get("args", {}), "gameEnv": gown.get("env", ""), "preset": preset,
                                       "features": fork_features(fork, meta), "forkSel": own.get("sel", {}), "gameSel": gown.get("sel", {})})
            if path == "/api/forkopts/set":
                fork = get_fork(body["fork"])
                if not fork:
                    raise ValueError("Unknown fork")
                known = {o["flag"]: o for o in fork_options(fork)}
                args = {}
                for flag, val in (body.get("args") or {}).items():
                    o = known.get(flag)
                    if not o or val in (None, ""):
                        continue
                    if o["kind"] == "switch":
                        args[flag] = bool(val)
                    elif o["kind"] == "num":
                        n = int(str(val).strip())
                        if "min" in o and not o["min"] <= n <= o["max"]:
                            raise ValueError(f"{flag} takes {o['min']}-{o['max']}")
                        args[flag] = n
                    elif o["kind"] in ("bool", "choice"):
                        if val not in o["choices"]:
                            raise ValueError(f"{flag}: choose one of {', '.join(o['choices'])}")
                        args[flag] = val
                    else:
                        args[flag] = str(val)[:260]
                sel = {}
                for f in fork_features(fork):
                    v = (body.get("sel") or {}).get(f["id"])
                    if v in (None, ""):
                        continue
                    if f["kind"] == "choice":
                        if v not in [c["label"] for c in f["choices"]]:
                            raise ValueError(f"{f['label']}: unknown choice")
                    elif f["kind"] == "num":
                        v = int(str(v).strip())
                        if not f["min"] <= v <= f["max"]:
                            raise ValueError(f"{f['label']} takes {f['min']}-{f['max']}")
                    elif f["kind"] == "text":
                        v = str(v).strip()[:100]
                    else:
                        v = bool(v)
                    sel[f["id"]] = v
                entry = {"args": args, "sel": sel, "env": ";".join(f"{k}={v}" for k, v in parse_env(body.get("env", "")).items())}
                if body.get("game"):
                    if body["game"] not in game_index:
                        raise ValueError("Game not found")
                    config["games"].setdefault(body["game"], {}).setdefault("forkOpts", {})[fork["id"]] = entry
                else:
                    config.setdefault("forkOpts", {})[fork["id"]] = entry
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/settings":
                for k, v in body.items():
                    if k in DEFAULT_SETTINGS and type(v) == type(DEFAULT_SETTINGS[k]):
                        config["settings"][k] = v
                save_config()
                return self.send_json(state())
            if path == "/api/game/convert-zar":
                return self.send_json(start_zar_conversion(str(body.get("game") or ""), bool(body.get("deleteSource"))))
            if path == "/api/library/add":
                p = body.get("path") or pick_folder()
                if p and Path(p).is_dir():
                    p = str(Path(p).resolve())
                    if p not in config["libraries"]:
                        config["libraries"].append(p)
                        save_config()
                elif p:
                    raise ValueError("Folder does not exist")
                return self.send_json(state())
            if path == "/api/library/remove":
                if body["path"] in config["libraries"]:
                    config["libraries"].remove(body["path"])
                    save_config()
                return self.send_json(state())
            if path == "/api/user/add":
                if len(config["users"]) >= 16:
                    raise ValueError("Up to 16 users")
                used = {u["uid"] for u in config["users"]}
                uid = next(n for n in range(1000, 1100) if n not in used)
                u = {"id": secrets.token_hex(4), "uid": uid, "name": clean_name(body.get("name")),
                     "avatar": re.sub(r"[^a-z]", "", str(body.get("avatar", "blue")))[:12] or "blue", "created": time.time()}
                config["users"].append(u)
                save_config()
                return self.send_json({"user": u["id"]})
            if path == "/api/user/update":
                u = get_user(body["id"])
                if not u:
                    raise ValueError("Unknown user")
                if "name" in body:
                    u["name"] = clean_name(body["name"])
                if "avatar" in body:
                    u["avatar"] = re.sub(r"[^a-z]", "", str(body["avatar"]))[:12] or u["avatar"]
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/user/delete":
                u = get_user(body["id"])
                if not u or len(config["users"]) < 2:
                    raise ValueError("At least one user must remain")
                config["users"].remove(u)  # their saves and trophies stay on disk under their user id
                if config["activeUser"] == u["id"]:
                    config["activeUser"] = config["users"][0]["id"]
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/user/select":
                if not get_user(body["id"]):
                    raise ValueError("Unknown user")
                if any(r["proc"].poll() is None for r in running.values()):
                    raise ValueError("Close the running game before switching user")
                config["activeUser"] = body["id"]
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/community/refresh":
                msg = None
                try:
                    refresh_remote_catalog()
                except Exception as e:
                    msg = str(e)
                for e in catalog()["forks"]:
                    if re.fullmatch(r"[\w.-]+/[\w.-]+", str(e.get("repo", ""))):
                        fetch_releases(e["repo"], True)
                out = community_state()
                out["error"] = msg
                return self.send_json(out)
            if path == "/api/community/import":
                src = body.get("path") or pick_folder_for("Choose the folder that contains catalog.json")
                if not src:
                    return self.send_json({"cancelled": True})
                return self.send_json(import_catalog(src))
            if path == "/api/community/fork":  # add a catalog fork to the list of forks
                f = add_catalog_fork(body["id"])
                return self.send_json({"fork": f["id"]})
            if path == "/api/patch/install":
                scan_games()
                return self.send_json({"titleId": install_catalog_patch(body["id"])})
            if path == "/api/patch/import":
                scan_games()
                p = body.get("path") or pick_file()
                if not p:
                    return self.send_json({"titleId": None})
                raw = Path(p).read_bytes()
                tid = validate_patch(json.loads(raw.decode("utf-8-sig")))
                pid = f"local-{tid}-{safe_name(Path(p).stem)[:30]}"
                store_patch(pid, raw, {"name": Path(p).stem[:80], "game": tid, "version": ""})
                return self.send_json({"titleId": tid, "id": pid})
            if path == "/api/patch/mods":
                set_patch_mods(body["id"], body.get("changes") or {})
                return self.send_json({"ok": True})
            if path == "/api/patch/remove":
                remove_patch(body["id"])
                return self.send_json({"ok": True})
            if path == "/api/game/patch":
                if body["game"] not in game_index:
                    raise ValueError("Game not found")
                pid = body.get("patch")
                info = pid and patch_index()["installed"].get(pid)
                if pid and (not info or info.get("titleId") != game_index[body["game"]].get("titleId")):
                    raise ValueError("That patch is for another game")
                gv, pv = game_index[body["game"]].get("version"), info and info.get("gameVersion")
                if pid and gv and pv and gv != pv:
                    raise ValueError(f"This patch is for game version {pv}; your copy is {gv}")
                config["games"].setdefault(body["game"], {})["patch"] = pid or None
                save_config()
                return self.send_json({"ok": True})
            if path == "/api/resume":
                resume_game()
                return self.send_json({"ok": True})
            if path == "/api/precompile":
                scan_games()
                start_precompile(body["game"], body.get("mode", "quick"))
                return self.send_json({"ok": True})
            if path == "/api/shaders/clear":
                scan_games()
                return self.send_json({"removed": clear_shader_cache(body["game"])})
            if path == "/api/import":  # merge user data into the shared store and cache into this fork
                fork = get_fork(body["fork"])
                if not fork:
                    raise ValueError("Unknown fork")
                src = body.get("path") or pick_folder()
                if not src:
                    return self.send_json({"copied": []})
                src = Path(src)
                copied = []
                for name in PERSIST:
                    if (src / name).is_dir():
                        target = shared_user_data_dir() / name if name in SAVE_DIRS else data_dir(fork) / name
                        merge_copy(src / name, target, fork["id"], "newer")
                        copied.append(name)
                if not copied:
                    raise ValueError("No _SaveData, _Trophies, _DownloadData or _PipelineCache folder found there")
                return self.send_json({"copied": copied})
            if path == "/api/open":
                target = None
                if "game" in body and body["game"] in game_index:
                    target = game_index[body["game"]]["path"]
                elif "fork" in body and get_fork(body["fork"]):
                    meta = current_meta(get_fork(body["fork"]))
                    target = meta and str(version_dir(get_fork(body["fork"]), meta["tag"]))
                elif "dataFork" in body and get_fork(body["dataFork"]):
                    shared_user_data_dir().mkdir(parents=True, exist_ok=True)
                    target = str(shared_user_data_dir())
                elif body.get("what") == "community":
                    (COMMUNITY_DIR / "patches").mkdir(parents=True, exist_ok=True)
                    if not CATALOG_FILE.exists():  # a skeleton to edit
                        CATALOG_FILE.write_text(json.dumps({"schema": 1, "forks": [], "patches": []}, indent=2), "utf-8")
                    target = str(COMMUNITY_DIR)
                elif body.get("what") == "logs":
                    LOGS_DIR.mkdir(exist_ok=True)
                    target = str(LOGS_DIR)
                elif body.get("what") == "plugins":
                    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
                    target = str(PLUGINS_DIR)
                if not target or not os.path.exists(target):
                    raise ValueError("Nothing to open")
                os.startfile(os.path.dirname(target) if os.path.isfile(target) else target)
                return self.send_json({"ok": True})
            if path == "/api/quit":
                threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
                return self.send_json({"ok": True})
        self.send_json({"error": "unknown endpoint"}, 404)


# ------------------------------------------------------------------- main --

def find_browser():
    cands = [shutil.which("msedge"), shutil.which("chrome")]
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            cands += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                      Path(base) / "Google/Chrome/Application/chrome.exe"]
    return next((str(c) for c in cands if c and Path(c).exists()), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--browser", action="store_true", help="use an Edge/Chrome app window instead of the native one")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    load_config()
    for d in (FORKS_DIR, LOGS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    TMP_DIR.mkdir(exist_ok=True)
    load_plugin_backends()
    cached = read_json(DATA / "release-cache.json")
    if isinstance(cached, dict):  # last known releases (and ETags) from the previous session
        for repo, ent in cached.items():
            if isinstance(ent, dict) and isinstance(ent.get("releases"), list):
                release_cache[repo] = dict(ent, at=0, error=None)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = srv.server_address[1]
    ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=background_checker, daemon=True).start()
    PadWatcher().start()
    url = f"http://127.0.0.1:{port}/"
    print("KytyPS5 Studio on", url, flush=True)

    if args.no_browser:
        threading.Event().wait()
    if not args.browser:
        try:
            import webview
        except ImportError:
            webview = None
        if webview:
            # WebView2 is Chromium: Gamepad API works and audio may start without a click.
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--autoplay-policy=no-user-gesture-required"

            class Api:
                def toggle_fullscreen(self):
                    window.toggle_fullscreen()

            window = webview.create_window("KytyPS5 Studio", url, width=1600, height=900, min_size=(1100, 650),
                                           background_color="#02060f", fullscreen=True, js_api=Api())
            globals()["WINDOW"] = window
            webview.start(private_mode=False, storage_path=str(DATA / "webview"),
                          icon=str(ROOT / "icon.ico") if (ROOT / "icon.ico").exists() else None)
            os._exit(0)  # window closed: stop the server
    browser = find_browser()
    if not browser:
        import webbrowser
        webbrowser.open(url)
        threading.Event().wait()
    proc = subprocess.Popen([
        browser, f"--app={url}", f"--user-data-dir={DATA / 'browser'}", "--start-fullscreen",
        "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--disable-features=Translate",
        "--disable-pinch", "--overscroll-history-navigation=0"])
    proc.wait()


if __name__ == "__main__":
    main()
