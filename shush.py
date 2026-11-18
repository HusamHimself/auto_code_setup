#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


# printed at the start of every run, so it is obvious which version ran.
SCRIPT_VERSION = "2026-11-18d (play button installs anything missing)"

MSYS2_ROOT = Path(r"C:\msys64")

VSCODE_CANDIDATES = [
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd")),
    Path(os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\bin\code.cmd")),
    Path(os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code\bin\code.cmd")),
]

BASE_PACKAGES = [
    "mingw-w64-ucrt-x86_64-gcc",
    "mingw-w64-ucrt-x86_64-gdb",
]

ALLEGRO_PACKAGES = [
    "mingw-w64-ucrt-x86_64-allegro",
    "mingw-w64-ucrt-x86_64-cmake",
    "mingw-w64-ucrt-x86_64-ninja",
    "mingw-w64-ucrt-x86_64-pkgconf",
]

EXTENSIONS = [
    "ms-vscode.cpptools",
    "ms-vscode.cpptools-extension-pack",
    "qwtel.sqlite-viewer",
    "eamodio.gitlens",
    "usernamehw.errorlens",
    "streetsidesoftware.code-spell-checker",
    "esbenp.prettier-vscode",
    "christian-kohler.path-intellisense",
    "github.copilot",
    "formulahendry.code-runner",
]


def print_step(msg: str) -> None:
    print(f"\n==> {msg}")


def run(cmd: list[str], timeout: int = 1800, label: str | None = None, **kwargs) -> subprocess.CompletedProcess:
    print(f"  {label}" if label else f"  running: {' '.join(cmd)}")
    kwargs.setdefault("stdin", subprocess.DEVNULL)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        print(f"  this step timed out after {timeout // 60} minutes.")
        print(f"  command: {' '.join(cmd)}")
        print("  usually means it's silently waiting on some prompt it has no way")
        print("  to show you -- a source mix-up, a Windows permission dialog, a")
        print("  license screen, that kind of thing. try running that exact line")
        print("  yourself in a normal terminal and see what it's actually stuck on.")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")

    if result.returncode != 0 and result.stderr != "TIMEOUT":
        print(f"  that didn't work (exit code {result.returncode}):")
        print(f"  command: {' '.join(cmd)}")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)

    return result


def check_winget_available() -> bool:
    return shutil.which("winget") is not None


# official download links, used when winget is missing or fails (common on
# fresh PCs and VMs, where winget often isn't working yet).
VSCODE_DOWNLOAD_URL = "https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user"
MSYS2_DOWNLOAD_URL = "https://repo.msys2.org/distrib/msys2-x86_64-latest.sfx.exe"

# installer switches: silent, don't launch VS Code at the end, and don't add
# VS Code to PATH (VS Code's installer does that by default -- not allowed here).
VSCODE_INSTALLER_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS=!runcode,!addtopath"


def download(url: str, dest: Path) -> bool:
    print(f"  downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, dest.open("wb") as f:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last_tenth = -1
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    tenth = done * 10 // total
                    if tenth != last_tenth:
                        print(f"    {tenth * 10}%")
                        last_tenth = tenth
    except OSError as e:
        print(f"  the download failed: {e}")
        return False
    return True


def install_vscode() -> Path | None:
    # VS Code already being installed is the normal case: find it and move on.
    # success is judged by actually finding code.cmd afterwards, not by what
    # winget prints (that text changes between versions and languages).
    existing = find_code_cmd()
    if existing is not None:
        print(f"  VS Code's already installed ({existing}), so skipping the install.")
        return existing

    if check_winget_available():
        run(
            [
                "winget", "install",
                "--id", "Microsoft.VisualStudioCode",
                "-e",
                "--source", "winget",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--override", VSCODE_INSTALLER_ARGS,
            ],
            label="downloading and installing Visual Studio Code (via winget)...",
        )
        found = find_code_cmd()
        if found is not None:
            print("  Visual Studio Code is good to go.")
            return found
        print("  winget didn't manage it, so trying Microsoft's own installer instead.")
    else:
        print("  winget isn't available here, so using Microsoft's own installer instead.")

    installer = Path(tempfile.gettempdir()) / "VSCodeUserSetup-x64.exe"
    if download(VSCODE_DOWNLOAD_URL, installer):
        run([str(installer), *VSCODE_INSTALLER_ARGS.split()],
            label="installing Visual Studio Code...")
    found = find_code_cmd()
    if found is not None:
        print("  Visual Studio Code is good to go.")
    return found


def _scan_uninstall_registry_for_vscode() -> Path | None:
    if winreg is None:
        return None

    uninstall_subpaths = [
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]

    for root in roots:
        for subpath in uninstall_subpaths:
            try:
                key = winreg.OpenKey(root, subpath)
            except OSError:
                continue
            with key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            if "Visual Studio Code" not in display_name:
                                continue
                            if "Insiders" in display_name:
                                continue
                            install_loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                    except OSError:
                        continue

                    candidate = Path(install_loc) / "bin" / "code.cmd"
                    if candidate.is_file():
                        return candidate
    return None


def _glob_search_for_code_cmd() -> Path | None:
    search_roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        os.path.expandvars(r"%ProgramFiles%"),
        os.path.expandvars(r"%ProgramFiles(x86)%"),
    ]
    for root in search_roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for candidate in root_path.glob("*Visual Studio Code*/bin/code.cmd"):
            if candidate.is_file():
                return candidate
    return None


def find_code_cmd() -> Path | None:
    for path in VSCODE_CANDIDATES:
        if path.is_file():
            return path

    hit = _scan_uninstall_registry_for_vscode()
    if hit is not None:
        return hit

    hit = _glob_search_for_code_cmd()
    if hit is not None:
        return hit

    # installed somewhere unusual but on PATH: code.cmd sits in VS Code's bin
    # folder (next to a "code" shell script, so look for code.cmd by name).
    on_path = shutil.which("code")
    if on_path is not None:
        candidate = Path(on_path).parent / "code.cmd"
        if candidate.is_file():
            return candidate
    return None


def install_msys2() -> bool:
    if (MSYS2_ROOT / "usr" / "bin" / "bash.exe").is_file():
        print(f"  MSYS2's already installed at {MSYS2_ROOT}, so skipping the install.")
        print("  next up is updating its packages, which can take several minutes.")
        return True

    if check_winget_available():
        run(
            [
                "winget", "install",
                "--id", "MSYS2.MSYS2",
                "-e",
                "--source", "winget",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            label="downloading and installing MSYS2 (via winget)...",
        )
        if (MSYS2_ROOT / "usr" / "bin" / "bash.exe").is_file():
            print(f"  MSYS2 is in, at {MSYS2_ROOT}.")
            return True
        print("  winget didn't manage it, so using MSYS2's own archive instead.")
    else:
        print("  winget isn't available here, so using MSYS2's own archive instead.")

    # the official self-extracting archive: unpacks straight into C:\msys64.
    archive = Path(tempfile.gettempdir()) / "msys2-x86_64-latest.sfx.exe"
    if download(MSYS2_DOWNLOAD_URL, archive):
        run([str(archive), "-y", f"-o{MSYS2_ROOT.parent}"], label="unpacking MSYS2...")
    if (MSYS2_ROOT / "usr" / "bin" / "bash.exe").is_file():
        print(f"  MSYS2 is in, at {MSYS2_ROOT}.")
        return True
    print(f"  MSYS2 still isn't at {MSYS2_ROOT} after trying both ways.")
    return False


def msys2_bash() -> Path:
    return MSYS2_ROOT / "usr" / "bin" / "bash.exe"


# pacman creates this file while it works and deletes it when done. if pacman
# gets killed partway (say, the window was closed mid-update), the file stays
# behind and every later pacman run fails with "unable to lock database".
def pacman_lock() -> Path:
    return MSYS2_ROOT / "var" / "lib" / "pacman" / "db.lck"


def pacman_is_running() -> bool:
    # tasklist lists every running program, one per line, as "name","pid",...
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in result.stdout.splitlines():
        name = line.split(",")[0].strip().strip('"').lower()
        if name == "pacman.exe":
            return True
    return False


def clear_stale_pacman_lock(max_wait: int = 600) -> bool:
    lock = pacman_lock()
    if not lock.is_file():
        return True
    # a pacman that's really running (an open MSYS2 window, say) gets to finish.
    waited = 0
    while pacman_is_running():
        if waited == 0:
            print("  another pacman is running right now -- waiting for it to finish...")
        if waited >= max_wait:
            print(f"  it's still running after {max_wait // 60} minutes, so giving up.")
            return False
        time.sleep(10)
        waited += 10
    # nothing is running, so the lock is a leftover from an interrupted run.
    if lock.is_file():
        try:
            lock.unlink()
        except OSError as e:
            print(f"  couldn't remove the leftover lock file {lock}: {e}")
            return False
        print("  removed a leftover pacman lock (from an earlier run that got interrupted).")
    return True


class ConsoleModeGuard:
    # MSYS2's bash changes this window's output settings and doesn't put them
    # back when it exits, which makes every later line break show up as odd
    # symbols. this saves the settings before bash runs and restores them after.
    def __init__(self) -> None:
        self.saved = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetStdHandle.argtypes = [wintypes.DWORD]
            k32.GetStdHandle.restype = wintypes.HANDLE
            k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.GetConsoleMode.restype = wintypes.BOOL
            k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.SetConsoleMode.restype = wintypes.BOOL
            handles = []
            for std_id in (-11, -12):  # this window's normal and error output
                handle = k32.GetStdHandle(std_id & 0xFFFFFFFF)
                mode = wintypes.DWORD()
                if handle and k32.GetConsoleMode(handle, ctypes.byref(mode)):
                    handles.append((handle, mode.value))
            self.saved = (k32, handles)
        except Exception:
            self.saved = None

    def restore(self) -> None:
        if self.saved is None:
            return
        k32, handles = self.saved
        for handle, mode in handles:
            try:
                k32.SetConsoleMode(handle, mode)
            except Exception:
                pass


def run_pacman_live(args: list[str], label: str, timeout: int = 1800) -> int:
    # pacman's output goes straight to this window instead of being captured,
    # so you can watch the downloads. it also means a background helper that
    # pacman leaves running (like gpg-agent) can't keep this script waiting.
    bash = msys2_bash()
    command_str = " ".join(args)
    print(f"  {label}")
    if not clear_stale_pacman_lock():
        return -1
    sys.stdout.flush()
    guard = ConsoleModeGuard()
    try:
        result = subprocess.run(
            [str(bash), "-lc", command_str],
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        guard.restore()
        print(f"  pacman was still going after {timeout // 60} minutes, so giving up.")
        return -1
    guard.restore()
    sys.stdout.flush()
    return result.returncode


def run_pacman(args: list[str], label: str) -> bool:
    if run_pacman_live(args, label) != 0:
        print("  that pacman step failed, see above.")
        return False
    return True


def update_msys2_core() -> bool:
    # pass 1 can update MSYS2's own runtime. when that happens MSYS2 closes all
    # of its own programs to finish -- including the one running pacman -- so
    # pass 1 ending with an error is normal. pass 2 is the one that must work.
    code = run_pacman_live(["pacman", "--noconfirm", "-Syuu"], label="updating MSYS2's core packages (pass 1 of 2)...")
    if code != 0:
        print("  pass 1 ended early -- normal when MSYS2 updates itself. carrying on.")

    if not run_pacman(["pacman", "--noconfirm", "-Syuu"], label="updating MSYS2's core packages (pass 2 of 2)..."):
        return False
    return True


def install_packages(packages: list[str]) -> bool:
    if not packages:
        return True
    friendly = ", ".join(p.replace("mingw-w64-ucrt-x86_64-", "") for p in packages)
    return run_pacman(
        ["pacman", "--noconfirm", "--needed", "-S", *packages],
        label=f"installing: {friendly}...",
    )


# files that must exist after installing, and the package each comes from.
# pacman can report success yet leave something out (an interrupted earlier
# run, say), so this checks the files themselves.
BASE_FILES = {
    "bin/g++.exe": "mingw-w64-ucrt-x86_64-gcc",
    "bin/gcc.exe": "mingw-w64-ucrt-x86_64-gcc",
    "bin/objdump.exe": "mingw-w64-ucrt-x86_64-binutils",
    "bin/gdb.exe": "mingw-w64-ucrt-x86_64-gdb",
}
ALLEGRO_FILES = {
    "bin/cmake.exe": "mingw-w64-ucrt-x86_64-cmake",
    "bin/ninja.exe": "mingw-w64-ucrt-x86_64-ninja",
    "include/allegro5/allegro.h": "mingw-w64-ucrt-x86_64-allegro",
    "include/allegro5/allegro_primitives.h": "mingw-w64-ucrt-x86_64-allegro",
    "lib/liballegro.dll.a": "mingw-w64-ucrt-x86_64-allegro",
}


def missing_files(required: dict) -> dict:
    ucrt64 = MSYS2_ROOT / "ucrt64"
    return {f: pkg for f, pkg in required.items() if not (ucrt64 / f).is_file()}


def verify_packages(required: dict) -> bool:
    missing = missing_files(required)
    if not missing:
        print("  checked: the compiler, debugger and libraries are all in place.")
        return True

    print("  these files didn't land:")
    for f in missing:
        print(f"    {MSYS2_ROOT / 'ucrt64' / f}")
    packages = []
    for pkg in missing.values():
        if pkg not in packages:
            packages.append(pkg)
    # no --needed here: reinstall even if pacman thinks they're already in.
    run_pacman(["pacman", "--noconfirm", "-S", *packages], label="reinstalling those packages...")

    still_missing = missing_files(required)
    if still_missing:
        print("  still missing after reinstalling:")
        for f in still_missing:
            print(f"    {MSYS2_ROOT / 'ucrt64' / f}")
        return False
    print("  fixed: everything's in place now.")
    return True


def install_extensions(code_cmd: Path | None) -> bool:
    if code_cmd is None:
        print("  skipped: VS Code's command-line tool (code.cmd) couldn't be found.")
        return False
    ok = True
    total = len(EXTENSIONS)
    for i, ext_id in enumerate(EXTENSIONS, start=1):
        result = run(
            [str(code_cmd), "--install-extension", ext_id, "--force"],
            label=f"installing extension {i}/{total}: {ext_id}",
        )
        if result.returncode != 0:
            print(f"  couldn't install {ext_id}, moving on anyway.")
            ok = False
    return ok


def msys2_bin_forward_slash() -> str:
    return str(MSYS2_ROOT / "ucrt64" / "bin").replace("\\", "/")


def build_console_tasks_json() -> dict:
    bin_path = msys2_bin_forward_slash()
    return {
        "version": "2.0.0",
        "tasks": [
            {
                # compiles every .cpp in the open file's folder into one app.exe,
                # so files that #include each other's headers link together.
                # "*.cpp" isn't expanded by a shell here -- MSYS2's g++ expands it.
                "label": "Build active C++ file",
                "type": "process",
                "command": f"{bin_path}/g++.exe",
                "args": [
                    "-std=c++17", "-Wall", "-Wextra", "-g", "-O0", "-static",
                    "*.cpp", "-o", "app.exe",
                ],
                "options": {
                    "cwd": "${fileDirname}",
                    "env": {"PATH": f"{bin_path};${{env:PATH}}"},
                },
                "osx": {
                    "command": "/usr/bin/clang++",
                    "args": [
                        "-std=c++17", "-Wall", "-Wextra", "-g", "-O0",
                        "${file}", "-o",
                        "${fileDirname}/${fileBasenameNoExtension}.out",
                    ],
                    "options": {"cwd": "${workspaceFolder}"},
                },
                # g++ reports errors relative to the folder it ran in, so point
                # VS Code's error underlining at that folder.
                "problemMatcher": {"base": "$gcc", "fileLocation": ["autoDetect", "${fileDirname}"]},
                "group": {"kind": "build", "isDefault": True},
            }
        ],
    }


def build_console_launch_json() -> dict:
    bin_path = msys2_bin_forward_slash()
    return {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Console Windows",
                "type": "cppdbg",
                "request": "launch",
                "program": "${fileDirname}/app.exe",
                "cwd": "${fileDirname}",
                "preLaunchTask": "Build active C++ file",
                "MIMode": "gdb",
                "miDebuggerPath": f"{bin_path}/gdb.exe",
                "externalConsole": False,
                "environment": [{"name": "PATH", "value": f"{bin_path};${{env:PATH}}"}],
            },
            {
                "name": "Console macOS",
                "type": "lldb",
                "request": "launch",
                "program": "${fileDirname}/${fileBasenameNoExtension}.out",
                "cwd": "${workspaceFolder}",
                "preLaunchTask": "Build active C++ file",
                "terminal": "integrated",
            },
        ],
    }


def build_console_settings_json() -> dict:
    bin_path = msys2_bin_forward_slash()
    return {
        "C_Cpp.default.compilerPath": f"{bin_path}/g++.exe",
        "C_Cpp.default.cppStandard": "c++17",
        "files.autoSave": "onFocusChange",
    }


def build_allegro_tasks_json() -> dict:
    bin_path = msys2_bin_forward_slash()
    lib_pkgconfig = str(MSYS2_ROOT / "ucrt64" / "lib" / "pkgconfig").replace("\\", "/")
    return {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "Configure Allegro",
                "type": "process",
                "command": f"{bin_path}/cmake.exe",
                "args": [
                    "-S", "${workspaceFolder}",
                    "-B", "${workspaceFolder}/build", "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Debug",
                    f"-DCMAKE_CXX_COMPILER={bin_path}/g++.exe",
                    f"-DCMAKE_MAKE_PROGRAM={bin_path}/ninja.exe",
                ],
                "options": {
                    "cwd": "${workspaceFolder}",
                    "env": {
                        "PATH": f"{bin_path};${{env:PATH}}",
                        "PKG_CONFIG_PATH": lib_pkgconfig,
                    },
                },
                "osx": {
                    "command": "/opt/homebrew/bin/cmake",
                    "args": [
                        "-S", "${workspaceFolder}",
                        "-B", "${workspaceFolder}/build", "-G", "Ninja",
                        "-DCMAKE_BUILD_TYPE=Debug",
                        "-DCMAKE_CXX_COMPILER=/usr/bin/clang++",
                        "-DCMAKE_MAKE_PROGRAM=/opt/homebrew/bin/ninja",
                        "-DPKG_CONFIG_EXECUTABLE=/opt/homebrew/bin/pkgconf",
                    ],
                    "options": {
                        "cwd": "${workspaceFolder}",
                        "env": {
                            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:${env:PATH}",
                            "PKG_CONFIG_PATH": "/opt/homebrew/lib/pkgconfig",
                        },
                    },
                },
                "problemMatcher": [],
            },
            {
                "label": "Build Allegro",
                "type": "process",
                "command": f"{bin_path}/cmake.exe",
                "args": ["--build", "${workspaceFolder}/build", "--target", "app"],
                "options": {
                    "cwd": "${workspaceFolder}",
                    "env": {"PATH": f"{bin_path};${{env:PATH}}"},
                },
                "osx": {
                    "command": "/opt/homebrew/bin/cmake",
                    "options": {
                        "cwd": "${workspaceFolder}",
                        "env": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:${env:PATH}"},
                    },
                },
                "dependsOn": "Configure Allegro",
                "dependsOrder": "sequence",
                "problemMatcher": "$gcc",
                "group": {"kind": "build", "isDefault": True},
            },
        ],
    }


def build_allegro_launch_json() -> dict:
    bin_path = msys2_bin_forward_slash()
    return {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Allegro Windows",
                "type": "cppdbg",
                "request": "launch",
                "program": "${workspaceFolder}/build/app.exe",
                "cwd": "${workspaceFolder}",
                "preLaunchTask": "Build Allegro",
                "MIMode": "gdb",
                "miDebuggerPath": f"{bin_path}/gdb.exe",
                "externalConsole": False,
                "environment": [{"name": "PATH", "value": f"{bin_path};${{env:PATH}}"}],
            },
            {
                "name": "Allegro macOS",
                "type": "lldb",
                "request": "launch",
                "program": "${workspaceFolder}/build/app",
                "cwd": "${workspaceFolder}",
                "preLaunchTask": "Build Allegro",
                "terminal": "integrated",
            },
        ],
    }


def build_allegro_settings_json() -> dict:
    # the play button's command lives in the global settings now and spots the
    # CMakeLists.txt by itself, so this folder doesn't need its own copy.
    settings = build_console_settings_json()
    settings["C_Cpp.default.compileCommands"] = ["${workspaceFolder}/build/compile_commands.json"]
    return settings


ALLEGRO_CMAKELISTS = """\
cmake_minimum_required(VERSION 3.20)
project(AllegroProject LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# MSYS2's compiler already searches its own include/ and lib/ folders, where
# Allegro lives, so the libraries can be linked by name with nothing else.
get_filename_component(TOOLCHAIN_BIN "${CMAKE_CXX_COMPILER}" DIRECTORY)

# every .cpp in the project folder; CONFIGURE_DEPENDS re-checks on each build,
# so a new file gets picked up without editing this.
file(GLOB SOURCES CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/*.cpp")
add_executable(app ${SOURCES})
# lets any file #include "player.h" etc. from the project folder.
target_include_directories(app PRIVATE "${CMAKE_SOURCE_DIR}")
# every Allegro library (fonts, images, audio...). ones you don't use cost
# nothing: app.exe only ends up needing the DLLs whose functions it calls.
target_link_libraries(app PRIVATE
    allegro_ttf allegro_font allegro_acodec allegro_audio allegro_video
    allegro_image allegro_color allegro_dialog allegro_memfile allegro_physfs
    allegro_primitives allegro_main allegro)
target_compile_options(app PRIVATE -Wall -Wextra)
target_link_options(app PRIVATE -static-libgcc -static-libstdc++)

# after every build (F5 or play button), copy the DLLs app.exe needs and the
# assets folder next to it, so build/ runs on any Windows 11 PC by double-click.
add_custom_command(TARGET app POST_BUILD
    COMMAND "${CMAKE_COMMAND}"
        "-DAPP=$<TARGET_FILE:app>"
        "-DASSETS=${CMAKE_SOURCE_DIR}/assets"
        "-DSEARCH_DIR=${TOOLCHAIN_BIN}"
        "-DOBJDUMP=${TOOLCHAIN_BIN}/objdump.exe"
        -P "${CMAKE_SOURCE_DIR}/copy_dlls.cmake"
    VERBATIM)
"""

ALLEGRO_COPY_DLLS_CMAKE = """\
# copies the DLLs app.exe needs (and assets/) into the folder app.exe is in.
# CMakeLists.txt runs this after each build; you never run it yourself.
cmake_minimum_required(VERSION 3.20)

# run.cmd passes Windows-style paths (with backslashes); switch them to the
# forward-slash form CMake expects.
file(TO_CMAKE_PATH "${APP}" APP)
file(TO_CMAKE_PATH "${ASSETS}" ASSETS)
file(TO_CMAKE_PATH "${SEARCH_DIR}" SEARCH_DIR)
file(TO_CMAKE_PATH "${OBJDUMP}" OBJDUMP)

# reading a Windows .exe's list of needed DLLs, using MSYS2's objdump.
set(CMAKE_GET_RUNTIME_DEPENDENCIES_PLATFORM "windows+pe")
set(CMAKE_GET_RUNTIME_DEPENDENCIES_TOOL "objdump")
set(CMAKE_GET_RUNTIME_DEPENDENCIES_COMMAND "${OBJDUMP}")

get_filename_component(app_dir "${APP}" DIRECTORY)

# clear out the DLLs the last build copied, so the lookup below finds the real
# MSYS2 ones instead of our old copies (and an updated MSYS2 gets picked up).
file(GLOB old_dlls "${app_dir}/*.dll")
if(old_dlls)
    file(REMOVE ${old_dlls})
endif()

# recursive: the DLLs app.exe needs, the DLLs those need, and so on.
# if a DLL name turns up in two folders (say, MSYS2 and some other program on
# PATH), CMake lists it under conflict_* instead of stopping the build.
file(GET_RUNTIME_DEPENDENCIES
    EXECUTABLES "${APP}"
    DIRECTORIES "${SEARCH_DIR}"
    PRE_EXCLUDE_REGEXES "^api-ms-" "^ext-ms-"
    RESOLVED_DEPENDENCIES_VAR found
    UNRESOLVED_DEPENDENCIES_VAR missing
    CONFLICTING_DEPENDENCIES_PREFIX conflict)
foreach(name IN LISTS conflict_FILENAMES)
    list(APPEND found ${conflict_${name}})
endforeach()

# only copy DLLs that came from MSYS2. kernel32.dll and friends are part of
# Windows itself and already on every PC.
file(TO_CMAKE_PATH "${SEARCH_DIR}" search_dir)
string(TOLOWER "${search_dir}/" search_dir)
foreach(dll IN LISTS found)
    file(TO_CMAKE_PATH "${dll}" dll_path)
    string(TOLOWER "${dll_path}" dll_lower)
    string(FIND "${dll_lower}" "${search_dir}" pos)
    if(pos EQUAL 0)
        file(COPY "${dll}" DESTINATION "${app_dir}")
    endif()
endforeach()

if(IS_DIRECTORY "${ASSETS}")
    file(COPY "${ASSETS}" DESTINATION "${app_dir}")
endif()
"""

ALLEGRO_MAIN_CPP = """\
#include <allegro5/allegro.h>
#include <allegro5/allegro_primitives.h>
#include <iostream>

int main() {
    if (!al_init()) return 1;
    if (!al_init_primitives_addon()) return 1;

    ALLEGRO_DISPLAY* display = al_create_display(640, 480);
    if (!display) {
        std::cerr << "Cannot create display\\n";
        return 1;
    }

    ALLEGRO_EVENT_QUEUE* queue = al_create_event_queue();
    if (!queue) {
        al_destroy_display(display);
        return 1;
    }
    al_register_event_source(queue, al_get_display_event_source(display));

    al_clear_to_color(al_map_rgb(30, 35, 45));
    al_draw_filled_circle(320, 240, 60, al_map_rgb(80, 180, 240));
    al_flip_display();

    bool running = true;
    while (running) {
        ALLEGRO_EVENT event;
        al_wait_for_event(queue, &event);
        if (event.type == ALLEGRO_EVENT_DISPLAY_CLOSE)
            running = false;
    }

    al_destroy_event_queue(queue);
    al_destroy_display(display);
    return 0;
}
"""

HELLO_CPP = """\
#include <iostream>

int main() {
    int number;
    std::cout << "Enter a number: " << std::flush;
    if (!(std::cin >> number)) return 1;
    std::cout << "Twice that is " << 2 * number << '\\n';
}
"""


def vscode_user_settings_path() -> Path:
    return Path(os.path.expandvars(r"%APPDATA%\Code\User\settings.json"))


RUNNER_DIR = MSYS2_ROOT / "vscode-runner"

# every Allegro library MSYS2 ships. linking one the program doesn't use costs
# nothing: the exe only ends up needing the DLLs whose functions it calls.
ALLEGRO_LIBS = [
    "allegro_ttf", "allegro_font", "allegro_acodec", "allegro_audio",
    "allegro_video", "allegro_image", "allegro_color", "allegro_dialog",
    "allegro_memfile", "allegro_physfs", "allegro_primitives",
    "allegro_main", "allegro",
]

# what the play button runs for C and C++, in every folder. it lives inside the
# MSYS2 folder (a path with no spaces, so it needs no quotes to run).
RUN_CMD_TEMPLATE = r"""@echo off
rem What the VS Code play button runs for C and C++ (written by setup_cpp_vscode).
rem   usage: run.cmd c-or-cpp "folder of the open file" "folder open in VS Code"
rem setlocal: the PATH change below only lasts until this script ends.
rem Windows' own PATH is never touched.
setlocal

set "MSYS_ROOT=@MSYS_ROOT@"
set "UCRT=%MSYS_ROOT%\ucrt64"
set "TOOLS=%UCRT%\bin"
set "TOOLS_FWD=@BIN_FWD@"
set "MSYS_BASH=%MSYS_ROOT%\usr\bin\bash.exe"
set "PACMAN_LOCK=%MSYS_ROOT%\var\lib\pacman\db.lck"
set "RUNNER=@RUNNER@"
set "ALLEGRO_LIBS=@ALLEGRO_LIBS@"
set "LANGUAGE=%~1"
set "SRC=%~2"
set "ROOT=%~3"
set "ORIGINAL_PATH=%PATH%"
set "PATH=%TOOLS%;%PATH%"

if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
if defined ROOT if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if /i "%LANGUAGE%"=="c" goto pick_c
set "COMPILER=%TOOLS%\g++.exe"
set "SOURCES=*.cpp"
set "STANDARD=-std=c++17"
goto picked
:pick_c
set "COMPILER=%TOOLS%\gcc.exe"
set "SOURCES=*.c"
set "STANDARD=-std=c17"
:picked

rem anything missing gets installed right here, so it works even if the
rem setup was interrupted before it finished installing packages.
call :need "%COMPILER%" mingw-w64-ucrt-x86_64-gcc || goto install_failed

rem 1. a CMake project (like allegro-project): build it the way F5 does.
set "PROJECT="
if exist "%SRC%\CMakeLists.txt" set "PROJECT=%SRC%"
if not defined PROJECT if defined ROOT if exist "%ROOT%\CMakeLists.txt" set "PROJECT=%ROOT%"
if defined PROJECT goto cmake_project

cd /d "%SRC%" || goto bad_folder

rem 2. if any file here mentions allegro5, build with Allegro.
call :detect_allegro "%SRC%"
if defined USES_ALLEGRO goto allegro_folder

rem 3. plain program: every source file in this folder becomes one app.exe
rem that needs nothing else to run (-static).
echo [building every %SOURCES% in this folder]
"%COMPILER%" %STANDARD% -Wall -Wextra -static %SOURCES% -o app.exe || goto build_failed
set "PATH=%ORIGINAL_PATH%"
"%SRC%\app.exe"
exit /b %ERRORLEVEL%

:allegro_folder
call :need_allegro || goto install_failed
echo [building every %SOURCES% in this folder with Allegro]
if not exist "%SRC%\build" mkdir "%SRC%\build"
"%COMPILER%" %STANDARD% -Wall -Wextra %SOURCES% -o build\app.exe -static-libgcc -static-libstdc++ %ALLEGRO_LIBS% || goto build_failed
"%TOOLS%\cmake.exe" "-DAPP=%SRC%\build\app.exe" "-DASSETS=%SRC%\assets" "-DSEARCH_DIR=%TOOLS_FWD%" "-DOBJDUMP=%TOOLS_FWD%/objdump.exe" -P "%RUNNER%\copy_dlls.cmake" || goto build_failed
rem run it without MSYS2 on PATH, the way it would run on any other PC.
set "PATH=%ORIGINAL_PATH%"
"%SRC%\build\app.exe"
exit /b %ERRORLEVEL%

:cmake_project
call :need "%TOOLS%\cmake.exe" mingw-w64-ucrt-x86_64-cmake || goto install_failed
call :need "%TOOLS%\ninja.exe" mingw-w64-ucrt-x86_64-ninja || goto install_failed
call :detect_allegro "%PROJECT%"
if not defined USES_ALLEGRO goto cmake_build
call :need_allegro || goto install_failed
:cmake_build
echo [building the CMake project in this folder]
cd /d "%PROJECT%" || goto bad_folder
"%TOOLS%\cmake.exe" -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug "-DCMAKE_CXX_COMPILER=%TOOLS_FWD%/g++.exe" "-DCMAKE_MAKE_PROGRAM=%TOOLS_FWD%/ninja.exe" || goto build_failed
"%TOOLS%\cmake.exe" --build build || goto build_failed
if not exist "%PROJECT%\build\app.exe" goto no_app
set "PATH=%ORIGINAL_PATH%"
"%PROJECT%\build\app.exe"
exit /b %ERRORLEVEL%

:no_app
echo [built, but there is no build\app.exe to run]
exit /b 0

:bad_folder
echo [could not open the folder "%SRC%"]
exit /b 1

:build_failed
echo.
echo [build failed -- see the errors above]
exit /b 1

:install_failed
echo.
echo [a package this program needs could not be installed -- see the messages above]
exit /b 1

rem ---- helpers, reached only through "call" ----

:detect_allegro
set "USES_ALLEGRO="
for %%F in ("%~1\*.c" "%~1\*.cpp" "%~1\*.h" "%~1\*.hpp") do (
    findstr /i /c:"allegro5" "%%~F" >nul 2>&1 && set "USES_ALLEGRO=1"
)
exit /b 0

:need_allegro
call :need "%UCRT%\include\allegro5\allegro.h" mingw-w64-ucrt-x86_64-allegro || exit /b 1
call :need "%TOOLS%\cmake.exe" mingw-w64-ucrt-x86_64-cmake || exit /b 1
call :need "%TOOLS%\objdump.exe" mingw-w64-ucrt-x86_64-binutils || exit /b 1
exit /b 0

:need
rem first value: a file that must exist. second value: the package it comes from.
if exist "%~1" exit /b 0
echo [installing %~2 -- one time only, needs internet, can take a few minutes]
rem -Syu updates MSYS2 and installs in one go. if MSYS2 has to update itself
rem first, that closes pacman early, so a second round finishes the job.
for %%N in (1 2) do (
    if not exist "%~1" (
        call :clear_stale_lock
        "%MSYS_BASH%" -lc "pacman --noconfirm --needed -Syu %~2"
    )
)
if exist "%~1" exit /b 0
echo [still missing after installing: %~1]
exit /b 1

:clear_stale_lock
rem pacman's lock file is left behind if pacman was ever killed mid-run; with
rem no pacman running it is a leftover, so remove it. a real one gets to finish.
set /a WAITED=0
:lock_wait
if not exist "%PACMAN_LOCK%" exit /b 0
tasklist /FI "IMAGENAME eq pacman.exe" 2>nul | find /i "pacman.exe" >nul
if errorlevel 1 goto lock_stale
if %WAITED% GEQ 600 exit /b 1
if %WAITED%==0 echo [another pacman is running -- waiting for it to finish]
ping -n 6 127.0.0.1 >nul
set /a WAITED+=5
goto lock_wait
:lock_stale
del /f /q "%PACMAN_LOCK%" >nul 2>&1
exit /b 0
"""


def runner_dir_backslash() -> str:
    return str(RUNNER_DIR).replace("/", "\\")


def build_run_cmd() -> str:
    bin_fwd = msys2_bin_forward_slash()
    text = RUN_CMD_TEMPLATE.replace("@MSYS_ROOT@", str(MSYS2_ROOT).replace("/", "\\"))
    text = text.replace("@BIN_FWD@", bin_fwd)
    text = text.replace("@RUNNER@", runner_dir_backslash())
    text = text.replace("@ALLEGRO_LIBS@", " ".join("-l" + name for name in ALLEGRO_LIBS))
    return text


def write_runner_files() -> bool:
    try:
        RUNNER_DIR.mkdir(parents=True, exist_ok=True)
        # batch files want Windows line endings; with plain \n, "goto" can
        # land on the wrong line in longer scripts.
        with open(RUNNER_DIR / "run.cmd", "w", encoding="ascii", newline="\r\n") as f:
            f.write(build_run_cmd())
        with open(RUNNER_DIR / "copy_dlls.cmake", "w", encoding="utf-8") as f:
            f.write(ALLEGRO_COPY_DLLS_CMAKE)
    except OSError as e:
        print(f"  couldn't write the play button's build script into {RUNNER_DIR}: {e}")
        return False
    print(f"  wrote the play button's build script to {RUNNER_DIR}")
    return True


def strip_jsonc(text: str) -> str:
    # VS Code's settings.json may contain // comments, /* */ comments and
    # trailing commas, which Python's json module rejects. this removes them
    # with a plain character loop, leaving anything inside "strings" alone.
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def read_settings(path: Path, quiet: bool = False) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    try:
        data = json.loads(strip_jsonc(raw))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    if quiet:
        return {}
    print(f"  heads up: couldn't read {path} even after removing comments,")
    print("  so starting it fresh. your old copy is in the backup next to it.")
    return {}


C_FAMILY_EXTENSIONS = ["c", "cpp", "cc", "cxx", "c++", "h", "hpp", "hh", "hxx"]


def glob_targets_c_family(pattern: str) -> bool:
    # executorMapByGlob keys look like "*.cpp", "**/*.c" or "*.{c,cpp}".
    dot = pattern.rfind(".")
    if dot == -1:
        return False
    tail = pattern[dot + 1:].lower()
    if tail[:1] == "{" and tail[-1:] == "}":
        tail = tail[1:-1]
    for ext in tail.split(","):
        if ext.strip() in C_FAMILY_EXTENSIONS:
            return True
    return False


def apply_cpp_settings(existing: dict) -> dict:
    bin_path = msys2_bin_forward_slash()

    existing["C_Cpp.default.compilerPath"] = f"{bin_path}/g++.exe"
    existing["C_Cpp.default.cppStandard"] = "c++17"
    existing["C_Cpp.default.debuggerPath"] = f"{bin_path}/gdb.exe"
    existing["C_Cpp.debugShortcut"] = False

    terminal_env = existing.get("terminal.integrated.env.windows")
    if not isinstance(terminal_env, dict):
        terminal_env = {}
    terminal_env["PATH"] = f"{bin_path};${{env:PATH}}"
    existing["terminal.integrated.env.windows"] = terminal_env

    # the play button hands everything to run.cmd, which works out whether the
    # folder is a CMake project, an Allegro program or a plain one.
    # $dir arrives from Code Runner already in quotes; $workspaceRoot doesn't.
    runner = runner_dir_backslash() + "\\run.cmd"
    executor_map = existing.get("code-runner.executorMap")
    if not isinstance(executor_map, dict):
        executor_map = {}
    executor_map["c"] = f'{runner} c $dir "$workspaceRoot"'
    executor_map["cpp"] = f'{runner} cpp $dir "$workspaceRoot"'
    existing["code-runner.executorMap"] = executor_map

    # Code Runner checks these two maps BEFORE executorMap, so any C/C++ entry
    # in them would quietly win. take those entries out.
    by_glob = existing.get("code-runner.executorMapByGlob")
    if isinstance(by_glob, dict):
        for key in list(by_glob):
            if glob_targets_c_family(key):
                del by_glob[key]
    by_ext = existing.get("code-runner.executorMapByFileExtension")
    if isinstance(by_ext, dict):
        for key in list(by_ext):
            if key.lower().lstrip(".") in C_FAMILY_EXTENSIONS:
                del by_ext[key]

    existing["code-runner.saveAllFilesBeforeRun"] = True
    existing["code-runner.runInTerminal"] = True
    existing["terminal.integrated.defaultProfile.windows"] = "Command Prompt"
    return existing


def vscode_user_dirs(code_cmd: Path | None) -> list[Path]:
    # VS Code keeps settings in a different place depending on how it was
    # installed. cover them all: normal, Insiders, VSCodium, and portable mode.
    dirs = [vscode_user_settings_path().parent]
    appdata = Path(os.path.expandvars("%APPDATA%"))
    for flavour in ["Code - Insiders", "VSCodium"]:
        user_dir = appdata / flavour / "User"
        if user_dir.is_dir():
            dirs.append(user_dir)
    if code_cmd is not None:
        # portable VS Code: <install folder>/data/user-data/User
        portable = code_cmd.parent.parent / "data"
        if portable.is_dir():
            dirs.append(portable / "user-data" / "User")
    return dirs


def all_vscode_settings_files(code_cmd: Path | None = None) -> list[Path]:
    # each VS Code profile keeps its own settings.json, and whichever profile
    # is active is the one that counts -- so write every one of them.
    paths = []
    for user_dir in vscode_user_dirs(code_cmd):
        paths.append(user_dir / "settings.json")
        profiles_dir = user_dir / "profiles"
        if profiles_dir.is_dir():
            for profile in sorted(profiles_dir.iterdir()):
                if profile.is_dir():
                    paths.append(profile / "settings.json")
    return paths


def back_up_once(path: Path) -> None:
    backup = path.with_name(path.name + ".before-cpp-setup")
    if path.is_file() and not backup.exists():
        shutil.copy(path, backup)


def write_global_cpp_settings(code_cmd: Path | None = None) -> bool:
    for settings_path in all_vscode_settings_files(code_cmd):
        back_up_once(settings_path)
        settings = apply_cpp_settings(read_settings(settings_path))
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with settings_path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        print(f"  wrote C/C++ settings to {settings_path}")
    return True


RUNNER_KEYS = ["code-runner.executorMap", "code-runner.executorMapByGlob",
               "code-runner.executorMapByFileExtension"]


def remove_c_family_runner_entries(settings: dict) -> bool:
    # takes out only the C/C++ play-button entries; other languages stay.
    changed = False
    executor_map = settings.get("code-runner.executorMap")
    if isinstance(executor_map, dict):
        for key in ["c", "cpp"]:
            if key in executor_map:
                del executor_map[key]
                changed = True
    by_glob = settings.get("code-runner.executorMapByGlob")
    if isinstance(by_glob, dict):
        for key in list(by_glob):
            if glob_targets_c_family(key):
                del by_glob[key]
                changed = True
    by_ext = settings.get("code-runner.executorMapByFileExtension")
    if isinstance(by_ext, dict):
        for key in list(by_ext):
            if key.lower().lstrip(".") in C_FAMILY_EXTENSIONS:
                del by_ext[key]
                changed = True
    for key in RUNNER_KEYS:
        if isinstance(settings.get(key), dict) and not settings[key]:
            del settings[key]
    return changed


# folders never worth searching for project settings (huge, or not projects).
SKIP_FOLDERS = {"appdata", "node_modules", "__pycache__", "site-packages",
                "venv", ".venv", "env", ".git", ".cache", "scoop", "go"}


def clean_folder_overrides(top: Path, max_depth: int = 7, max_folders: int = 40000) -> list[Path]:
    # a folder's own .vscode/settings.json (or a .code-workspace file) beats the
    # global settings, so an old play-button command in one would keep winning
    # there. look through the user's folders and take out just those entries.
    cleaned = []
    seen = 0
    top_depth = len(top.parts)
    for folder, subfolders, files in os.walk(top):
        seen += 1
        if seen > max_folders:
            break
        here = Path(folder)
        candidates = []
        if ".vscode" in subfolders:
            candidates.append((here / ".vscode" / "settings.json", False))
        for name in files:
            if name.lower()[-15:] == ".code-workspace":
                candidates.append((here / name, True))
        for path, is_workspace_file in candidates:
            if not path.is_file():
                continue
            data = read_settings(path, quiet=True)
            settings = data.get("settings") if is_workspace_file else data
            if isinstance(settings, dict) and remove_c_family_runner_entries(settings):
                back_up_once(path)
                with path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                cleaned.append(path)
        if len(here.parts) - top_depth >= max_depth:
            subfolders[:] = []
        else:
            subfolders[:] = [d for d in subfolders
                             if d.lower() not in SKIP_FOLDERS and d[:1] != "."]
    return cleaned


def report_play_button(code_cmd: Path | None) -> bool:
    # read the settings back from disk and show what VS Code will really run.
    expected = runner_dir_backslash() + "\\run.cmd"
    all_good = True
    for path in all_vscode_settings_files(code_cmd):
        settings = read_settings(path)
        executor_map = settings.get("code-runner.executorMap")
        command = executor_map.get("cpp") if isinstance(executor_map, dict) else None
        ok = isinstance(command, str) and command[:len(expected)] == expected
        print(f"  {'[ok]  ' if ok else '[FAIL]'} {path}")
        print(f"         C++ play button runs: {command}")
        all_good = all_good and ok
    if not (RUNNER_DIR / "run.cmd").is_file():
        print(f"  [FAIL] {RUNNER_DIR / 'run.cmd'} is missing")
        all_good = False

    # the play button itself comes from the Code Runner extension.
    if code_cmd is None:
        print("  [FAIL] couldn't find VS Code to check the Code Runner extension")
        return False
    result = run([str(code_cmd), "--list-extensions"], timeout=180,
                 label="checking VS Code's installed extensions...")
    installed = [line.strip().lower() for line in result.stdout.splitlines()]
    for ext_id in ["formulahendry.code-runner", "ms-vscode.cpptools"]:
        ok = ext_id in installed
        print(f"  {'[ok]  ' if ok else '[FAIL]'} extension {ext_id}")
        all_good = all_good and ok
    return all_good


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"  wrote {path}")


def generate_console_project(project_dir: Path) -> None:
    print(f"setting up a plain console project at {project_dir}")
    write_json(project_dir / ".vscode" / "tasks.json", build_console_tasks_json())
    write_json(project_dir / ".vscode" / "launch.json", build_console_launch_json())
    write_json(project_dir / ".vscode" / "settings.json", build_console_settings_json())

    write_starter_code(project_dir / "hello.cpp", HELLO_CPP)


def write_starter_code(path: Path, text: str) -> None:
    # running the setup again shouldn't wipe code you've written since.
    if path.is_file():
        print(f"  kept your existing {path}")
        return
    path.write_text(text, encoding="utf-8")
    print(f"  wrote {path}")


def generate_allegro_project(project_dir: Path) -> None:
    print(f"setting up the Allegro project at {project_dir}")
    write_json(project_dir / ".vscode" / "tasks.json", build_allegro_tasks_json())
    write_json(project_dir / ".vscode" / "launch.json", build_allegro_launch_json())
    write_json(project_dir / ".vscode" / "settings.json", build_allegro_settings_json())

    (project_dir / "assets").mkdir(parents=True, exist_ok=True)

    cmakelists_path = project_dir / "CMakeLists.txt"
    cmakelists_path.write_text(ALLEGRO_CMAKELISTS, encoding="utf-8")
    print(f"  wrote {cmakelists_path}")

    copy_dlls_path = project_dir / "copy_dlls.cmake"
    copy_dlls_path.write_text(ALLEGRO_COPY_DLLS_CMAKE, encoding="utf-8")
    print(f"  wrote {copy_dlls_path}")

    write_starter_code(project_dir / "main.cpp", ALLEGRO_MAIN_CPP)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-allegro", action="store_false", dest="allegro",
        help="don't set up Allegro/CMake/Ninja/pkgconf or generate the Allegro starter project",
    )
    parser.add_argument(
        "--projects-dir", default=str(Path.home() / "cpp-projects"),
        help="where the starter project(s) get created",
    )
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir)
    total_steps = 6

    print(f"setup_cpp_vscode -- version {SCRIPT_VERSION}")
    print("setting up VS Code for C++.")
    print(f"this is {total_steps} steps, mostly spent downloading things --")
    print("here's what's happening at each point so it's not a black box.")

    print_step(f"step 1 of {total_steps}: Visual Studio Code")
    code_cmd = install_vscode()
    if code_cmd is None:
        # not a reason to stop: the settings below don't need VS Code to exist
        # yet, and the final check will say clearly what's still missing.
        print("  couldn't find or install VS Code -- carrying on with everything else.")

    print_step(f"step 2 of {total_steps}: MSYS2 (where the compiler comes from)")
    if not install_msys2():
        print("\ncouldn't get MSYS2 installed, so stopping here.")
        return 1

    # done before the long MSYS2 update on purpose: if anything later fails,
    # the play button is still switched over instead of left on an old command.
    print_step(f"step 3 of {total_steps}: making the play button work in every folder")
    if not write_runner_files():
        print("\ncouldn't set up the play button's build script, so stopping here.")
        return 1
    write_global_cpp_settings(code_cmd)
    print("  looking through your folders for old per-folder play-button settings...")
    cleaned = clean_folder_overrides(Path.home())
    if cleaned:
        for path in cleaned:
            print(f"  removed an old C/C++ play-button setting from {path}")
    else:
        print("  none found.")

    print_step(f"step 4 of {total_steps}: C++ compiler + debugger + libraries")
    if not update_msys2_core():
        print("\ncouldn't finish updating MSYS2, so stopping here.")
        return 1

    packages = list(BASE_PACKAGES)
    if args.allegro:
        packages += ALLEGRO_PACKAGES

    if not install_packages(packages):
        print("\ncouldn't finish installing the compiler toolchain, so stopping here.")
        return 1

    required = dict(BASE_FILES)
    if args.allegro:
        required.update(ALLEGRO_FILES)
    if not verify_packages(required):
        print("\nthe toolchain is incomplete even after reinstalling, so stopping here.")
        return 1

    print_step(f"step 5 of {total_steps}: VS Code extensions")
    if not install_extensions(code_cmd):
        print("\n  a couple extensions didn't install -- carrying on anyway, the")
        print("  core setup doesn't actually depend on them.")

    print_step(f"step 6 of {total_steps}: starter projects")
    generate_console_project(projects_dir / "cpp-exercises")
    if args.allegro:
        generate_allegro_project(projects_dir / "allegro-project")

    print("\nchecking what the play button will actually run (read back from disk):")
    if not report_play_button(code_cmd):
        print("\nsomething above is marked FAIL -- the play button is NOT set up right.")
        return 1

    print("\nall done. here's what you've got:")
    print("  - a C++ compiler + debugger (MSYS2)")
    print("  - VS Code with a decent extension set")
    print("  - a play button that works in any folder: it compiles every .cpp (or .c)")
    print("    in the open file's folder into one app.exe, so files can #include")
    print("    each other's headers. if the code uses Allegro, it builds with Allegro")
    print("    and puts the DLLs it needs next to the exe automatically.")
    print("  - one main() per folder: each separate program needs its own folder")
    print("  - a ready-to-open starter project at:")
    print(f"      {projects_dir / 'cpp-exercises'}")
    if args.allegro:
        print("  - an Allegro starter project (with F5 debugging) at:")
        print(f"      {projects_dir / 'allegro-project'}")
    print("\nAllegro programs end up in a build folder next to the code, holding")
    print("app.exe + the DLLs it needs -- that folder runs on any Windows 11 PC.")
    print("\nVS Code picks all of this up straight away, even if it was already open.")
    return 0


if __name__ == "__main__":
    code = main()
    input("\npress Enter to close this window...")
    sys.exit(code)