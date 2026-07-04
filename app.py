#!/usr/bin/env python3
import argparse
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_PATH = APP_DIR / "config.json"
CATALOG_CACHE = APP_DIR / "catalog-cache.json"
INSTALLED_CACHE = APP_DIR / "installed-cache.json"
REPOSITORY_XML = "https://dl.google.com/android/repository/repository2-1.xml"
REPOSITORY_BASE = "https://dl.google.com/android/repository/"

STATE_LOCK = threading.Lock()
STATE = {
    "busy": False,
    "job": None,
    "ok": None,
    "startedAt": None,
    "finishedAt": None,
    "logs": [],
}


def now_stamp():
    return time.strftime("%H:%M:%S")


def log(message):
    text = str(message).rstrip()
    with STATE_LOCK:
        STATE["logs"].append({"time": now_stamp(), "message": text})
        STATE["logs"] = STATE["logs"][-1200:]


def begin_job(name):
    with STATE_LOCK:
        if STATE["busy"]:
            return False
        STATE.update({
            "busy": True,
            "job": name,
            "ok": None,
            "startedAt": time.time(),
            "finishedAt": None,
            "logs": [],
        })
    log("Started: %s" % name)
    return True


def finish_job(ok, message=None):
    if message:
        log(message)
    with STATE_LOCK:
        STATE["busy"] = False
        STATE["ok"] = bool(ok)
        STATE["finishedAt"] = time.time()


def read_json(path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_sdk_root():
    system = platform.system().lower()
    home = Path.home()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return str(Path(base) / "Android" / "Sdk")
    if system == "darwin":
        return str(home / "Library" / "Android" / "sdk")
    return str(home / "Android" / "Sdk")


def load_config():
    config = read_json(CONFIG_PATH, {})
    if not config.get("sdkRoot"):
        config["sdkRoot"] = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or default_sdk_root()
    return config


def save_config(config):
    clean = dict(load_config())
    clean.update(config)
    clean["sdkRoot"] = str(Path(clean["sdkRoot"]).expanduser())
    write_json(CONFIG_PATH, clean)
    return clean


def select_directory(initial_dir=None):
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("Folder picker is not available because tkinter could not be loaded: %s" % exc)

    root = tk.Tk()
    root.withdraw()
    root.update()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose Android SDK folder",
            initialdir=str(Path(initial_dir or default_sdk_root()).expanduser()),
            mustexist=False,
        )
    finally:
        root.destroy()
    return selected


def host_os():
    system = platform.system().lower()
    if system == "darwin":
        return "macosx"
    if system == "windows":
        return "windows"
    return "linux"


def sdkmanager_names():
    return ["sdkmanager.bat"] if platform.system().lower() == "windows" else ["sdkmanager"]


def find_sdkmanager(sdk_root):
    root = Path(sdk_root).expanduser()
    candidates = []
    for name in sdkmanager_names():
        candidates.append(root / "cmdline-tools" / "latest" / "bin" / name)
    tools = root / "cmdline-tools"
    if tools.exists():
        for child in sorted(tools.iterdir(), reverse=True):
            for name in sdkmanager_names():
                candidates.append(child / "bin" / name)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def check_java():
    java = shutil.which("java")
    if not java:
        return {"ok": False, "path": None, "version": "Java/JDK not found"}
    try:
        proc = subprocess.run([java, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=8)
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else "Java available"
        return {"ok": proc.returncode == 0, "path": java, "version": first}
    except Exception as exc:
        return {"ok": False, "path": java, "version": str(exc)}


def env_lines(sdk_root):
    root = str(Path(sdk_root).expanduser())
    bin_paths = [
        str(Path(root) / "platform-tools"),
        str(Path(root) / "cmdline-tools" / "latest" / "bin"),
        str(Path(root) / "emulator"),
    ]
    if platform.system().lower() == "windows":
        return [
            "setx ANDROID_SDK_ROOT %s" % root,
            "setx ANDROID_HOME %s" % root,
            "Add to Path: " + ";".join(bin_paths),
        ]
    return [
        "export ANDROID_SDK_ROOT=%s" % shlex.quote(root),
        "export ANDROID_HOME=%s" % shlex.quote(root),
        "export PATH=\"$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/emulator:$PATH\"",
    ]


def status_payload():
    config = load_config()
    sdk_root = str(Path(config["sdkRoot"]).expanduser())
    manager = find_sdkmanager(sdk_root)
    java = check_java()
    installed = read_json(INSTALLED_CACHE, {"packages": [], "updatedAt": None})
    return {
        "sdkRoot": sdk_root,
        "sdkRootExists": Path(sdk_root).exists(),
        "defaultSdkRoot": default_sdk_root(),
        "platform": platform.system(),
        "hostOs": host_os(),
        "java": java,
        "sdkmanager": {"ok": bool(manager), "path": manager},
        "env": env_lines(sdk_root),
        "installed": installed,
        "state": get_state(),
    }


def local_name(tag):
    return tag.split("}", 1)[-1]


def child_text(node, name):
    for child in node:
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return None


def descendant_text(node, name):
    for child in node.iter():
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return None


def find_children(node, name):
    return [child for child in node if local_name(child.tag) == name]


def archive_for_host(remote_package, target_host):
    archives = []
    for child in remote_package.iter():
        if local_name(child.tag) == "archive":
            archives.append(child)
    if not archives:
        return {"url": None, "host": None, "size": None}

    fallback = None
    for archive in archives:
        host = child_text(archive, "host-os")
        url = descendant_text(archive, "url")
        size = descendant_text(archive, "size")
        item = {"url": url, "host": host, "size": int(size) if size and size.isdigit() else None}
        if not host:
            fallback = item
        if host == target_host:
            return item
    return fallback


def revision_text(remote_package):
    revision = None
    for child in remote_package:
        if local_name(child.tag) == "revision":
            revision = child
            break
    if revision is None:
        return ""
    parts = []
    for key in ["major", "minor", "micro", "preview"]:
        value = child_text(revision, key)
        if value is not None:
            parts.append(value)
    return ".".join(parts)


def classify_package(path):
    if path == "platform-tools":
        return "Tools"
    if path == "emulator":
        return "Tools"
    if path.startswith("cmdline-tools"):
        return "Tools"
    if path.startswith("platforms;"):
        return "Platforms"
    if path.startswith("build-tools;"):
        return "Build Tools"
    if path.startswith("system-images;"):
        return "System Images"
    if path.startswith("ndk;"):
        return "NDK"
    if path.startswith("extras;"):
        return "Extras"
    return "Other"


def version_tuple(text):
    nums = [int(x) for x in re.findall(r"\d+", text)]
    return tuple(nums or [0])


def fetch_catalog(force=False):
    if not force and CATALOG_CACHE.exists():
        cached = read_json(CATALOG_CACHE, None)
        if cached and time.time() - cached.get("fetchedAt", 0) < 6 * 60 * 60:
            return cached

    log("Fetching the Android SDK catalog from Google...")
    with urllib.request.urlopen(REPOSITORY_XML, timeout=45) as response:
        xml_data = response.read()
    root = ET.fromstring(xml_data)
    target_host = host_os()
    packages_by_path = {}
    for node in root.iter():
        if local_name(node.tag) != "remotePackage":
            continue
        path = node.attrib.get("path")
        if not path:
            continue
        archive = archive_for_host(node, target_host)
        if archive["url"] is None and list(node.iter()):
            continue
        display = child_text(node, "display-name") or path
        package = {
            "path": path,
            "name": display,
            "type": classify_package(path),
            "revision": revision_text(node),
            "url": archive["url"],
            "size": archive["size"],
        }
        if path not in packages_by_path:
            packages_by_path[path] = package

    packages = list(packages_by_path.values())

    platform_paths = [pkg["path"] for pkg in packages if re.match(r"^platforms;android-\d+$", pkg["path"])]
    build_tools = [pkg["path"] for pkg in packages if re.match(r"^build-tools;[\d.]+$", pkg["path"])]
    latest_platform = max(platform_paths, key=version_tuple) if platform_paths else None
    latest_build_tools = max(build_tools, key=version_tuple) if build_tools else None
    recommended = []
    for path in ["platform-tools", "cmdline-tools;latest", latest_platform, latest_build_tools, "emulator"]:
        if path and any(pkg["path"] == path for pkg in packages) and path not in recommended:
            recommended.append(path)

    payload = {
        "fetchedAt": time.time(),
        "hostOs": target_host,
        "packages": sorted(packages, key=lambda p: (p["type"], p["path"])),
        "recommended": recommended,
    }
    write_json(CATALOG_CACHE, payload)
    return payload


def cmdline_tools_url():
    catalog = fetch_catalog(force=True)
    for pkg in catalog["packages"]:
        if pkg["path"] == "cmdline-tools;latest" and pkg.get("url"):
            return REPOSITORY_BASE + pkg["url"]
    raise RuntimeError("Could not find command-line tools for this operating system.")


def download_file(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Standalone Android SDK Manager"})
    with urllib.request.urlopen(req, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last_report = 0
        with open(dest, "wb") as handle:
            while True:
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if total:
                    percent = int(done * 100 / total)
                    if percent >= last_report + 10:
                        last_report = percent
                        log("Download progress: %s%%" % percent)


def install_commandline_tools():
    config = load_config()
    sdk_root = Path(config["sdkRoot"]).expanduser()
    sdk_root.mkdir(parents=True, exist_ok=True)
    url = cmdline_tools_url()
    log("Download source: %s" % url)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "commandlinetools.zip"
        extract_path = tmp_path / "extract"
        download_file(url, zip_path)
        log("Extracting command-line tools...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_path)
        source = extract_path / "cmdline-tools"
        if not source.exists():
            matches = [p for p in extract_path.iterdir() if p.is_dir()]
            if not matches:
                raise RuntimeError("The downloaded archive does not contain the expected cmdline-tools structure.")
            source = matches[0]
        target_parent = sdk_root / "cmdline-tools"
        target = target_parent / "latest"
        target_parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))
    log("Command-line tools installed at: %s" % target)


def sdk_env(sdk_root):
    env = os.environ.copy()
    env["ANDROID_SDK_ROOT"] = str(Path(sdk_root).expanduser())
    env["ANDROID_HOME"] = str(Path(sdk_root).expanduser())
    return env


def run_sdkmanager(args, input_text=None):
    sdk_root = load_config()["sdkRoot"]
    manager = find_sdkmanager(sdk_root)
    if not manager:
        raise RuntimeError("sdkmanager is not installed yet. Install command-line tools first.")
    command = [manager, "--sdk_root=%s" % str(Path(sdk_root).expanduser())] + args
    log("$ " + " ".join(shlex.quote(str(x)) for x in command))
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if input_text is not None else None,
        text=True,
        env=sdk_env(sdk_root),
    )
    if input_text is not None:
        output, _ = proc.communicate(input=input_text)
        for line in (output or "").splitlines():
            log(line)
    else:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            log(line.rstrip())
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("sdkmanager exited with code %s" % proc.returncode)


def accept_licenses():
    run_sdkmanager(["--licenses"], input_text="y\n" * 200)


def install_packages(packages, accept=False):
    if accept:
        accept_licenses()
    run_sdkmanager(list(packages))


def uninstall_packages(packages):
    run_sdkmanager(["--uninstall"] + list(packages))


def parse_installed_from_logs(lines):
    installed = []
    for line in lines:
        text = line.get("message", "") if isinstance(line, dict) else str(line)
        if "|" not in text:
            continue
        path = text.split("|", 1)[0].strip()
        if not path or path.lower() in ("path", "-------"):
            continue
        if ";" in path or path in ("platform-tools", "emulator"):
            installed.append(path)
    return sorted(set(installed))


def refresh_installed():
    run_sdkmanager(["--list_installed"])
    with STATE_LOCK:
        lines = list(STATE["logs"])
    packages = parse_installed_from_logs(lines)
    write_json(INSTALLED_CACHE, {"packages": packages, "updatedAt": time.time()})
    log("Installed package list updated: %s packages" % len(packages))


def run_async(name, target, *args, **kwargs):
    if not begin_job(name):
        return False

    def wrapper():
        try:
            target(*args, **kwargs)
            finish_job(True, "Completed.")
        except Exception as exc:
            finish_job(False, "Error: %s" % exc)

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return True


def get_state():
    with STATE_LOCK:
        return dict(STATE)


def open_sdk_folder():
    root = Path(load_config()["sdkRoot"]).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    system = platform.system().lower()
    if system == "darwin":
        subprocess.Popen(["open", str(root)])
    elif system == "windows":
        os.startfile(str(root))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(root)])


class Handler(SimpleHTTPRequestHandler):
    server_version = "AndroidSDKManager/1.0"

    def translate_path(self, path):
        parsed = urlparse(path)
        clean = unquote(parsed.path)
        if clean == "/":
            clean = "/index.html"
        if clean.startswith("/static/"):
            return str(APP_DIR / clean.lstrip("/"))
        return str(STATIC_DIR / clean.lstrip("/"))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/status":
                return self.send_json(status_payload())
            if path == "/api/state":
                return self.send_json(get_state())
            if path == "/api/catalog":
                return self.send_json(fetch_catalog(force=False))
            if path == "/api/installed":
                return self.send_json(read_json(INSTALLED_CACHE, {"packages": [], "updatedAt": None}))
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=500)
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/api/config":
                sdk_root = body.get("sdkRoot")
                if not sdk_root:
                    return self.send_json({"error": "Missing sdkRoot"}, status=400)
                return self.send_json(save_config({"sdkRoot": sdk_root}))
            if path == "/api/select-sdk-folder":
                current_root = body.get("sdkRoot") or load_config()["sdkRoot"]
                selected = select_directory(current_root)
                if not selected:
                    return self.send_json({"selected": False, "config": load_config()})
                config = save_config({"sdkRoot": selected})
                return self.send_json({"selected": True, "config": config})
            if path == "/api/install-tools":
                return self.send_json({"started": run_async("Install command-line tools", install_commandline_tools)})
            if path == "/api/refresh-catalog":
                return self.send_json({"started": run_async("Refresh SDK catalog", fetch_catalog, True)})
            if path == "/api/accept-licenses":
                return self.send_json({"started": run_async("Accept licenses", accept_licenses)})
            if path == "/api/install-packages":
                packages = body.get("packages") or []
                if not packages:
                    return self.send_json({"error": "No packages selected"}, status=400)
                return self.send_json({"started": run_async("Install packages", install_packages, packages, bool(body.get("acceptLicenses")))})
            if path == "/api/uninstall-packages":
                packages = body.get("packages") or []
                if not packages:
                    return self.send_json({"error": "No packages selected"}, status=400)
                return self.send_json({"started": run_async("Uninstall packages", uninstall_packages, packages)})
            if path == "/api/list-installed":
                return self.send_json({"started": run_async("Refresh installed packages", refresh_installed)})
            if path == "/api/open-sdk-folder":
                open_sdk_folder()
                return self.send_json({"ok": True})
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=500)
        return self.send_json({"error": "Not found"}, status=404)


def main():
    parser = argparse.ArgumentParser(description="Standalone Android SDK Manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    save_config(load_config())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%s/" % (args.host, args.port)
    print("Android SDK Manager is running at %s" % url)
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping app...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
