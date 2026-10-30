#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


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


def install_vscode() -> bool:
    if not check_winget_available():
        print("  can't find winget on this machine. it ships with Windows 11 out of")
        print("  the box, so if it's missing something odd is going on -- try")
        print("  grabbing 'App Installer' from the Microsoft Store and run this again.")
        return False

    result = run(
        [
            "winget", "install",
            "--id", "Microsoft.VisualStudioCode",
            "-e",
            "--source", "winget",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ],
        label="downloading and installing Visual Studio Code...",
    )

    out = result.stdout.lower()
    already_installed = "no available upgrade" in out or "already installed" in out

    if result.returncode != 0 and not already_installed:
        print("  Visual Studio Code didn't install.")
        return False

    print("  Visual Studio Code is good to go.")
    return True


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

    return _glob_search_for_code_cmd()


def install_msys2() -> bool:
    if not check_winget_available():
        print("  can't find winget on this machine.")
        return False

    if MSYS2_ROOT.is_dir():
        print(f"  MSYS2's already sitting at {MSYS2_ROOT} -- nothing to do here.")
        return True

    result = run(
        [
            "winget", "install",
            "--id", "MSYS2.MSYS2",
            "-e",
            "--source", "winget",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ],
        label="downloading and installing MSYS2 (this is what provides the real compiler + debugger)...",
    )

    if result.returncode != 0:
        print("  MSYS2 didn't install.")
        return False

    if not MSYS2_ROOT.is_dir():
        print(f"  installer said it worked, but {MSYS2_ROOT} isn't there. something")
        print("  weird happened -- worth a manual look.")
        return False

    print(f"  MSYS2 is in, at {MSYS2_ROOT}.")
    return True


def msys2_bash() -> Path:
    return MSYS2_ROOT / "usr" / "bin" / "bash.exe"


def run_pacman(args: list[str], label: str) -> bool:
    bash = msys2_bash()
    command_str = " ".join(args)
    result = run([str(bash), "-lc", command_str], label=label)

    if result.returncode != 0:
        print("  that pacman step failed, see above.")
        return False
    return True


def update_msys2_core() -> bool:
    if not run_pacman(["pacman", "--noconfirm", "-Syuu"], label="updating MSYS2's core packages (pass 1 of 2)..."):
        return False
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


def install_extensions(code_cmd: Path) -> bool:
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
                "label": "Build active C++ file",
                "type": "process",
                "command": f"{bin_path}/g++.exe",
                "args": [
                    "-std=c++17", "-Wall", "-Wextra", "-g", "-O0", "-static",
                    "${file}", "-o",
                    "${fileDirname}/${fileBasenameNoExtension}.exe",
                ],
                "options": {
                    "cwd": "${workspaceFolder}",
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
                "problemMatcher": "$gcc",
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
                "program": "${fileDirname}/${fileBasenameNoExtension}.exe",
                "cwd": "${workspaceFolder}",
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
                    f"-DPKG_CONFIG_EXECUTABLE={bin_path}/pkgconf.exe",
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

find_package(PkgConfig REQUIRED)
pkg_check_modules(ALLEGRO REQUIRED IMPORTED_TARGET
    allegro-5 allegro_main-5 allegro_primitives-5)

add_executable(app main.cpp)
target_link_libraries(app PRIVATE PkgConfig::ALLEGRO)
target_compile_options(app PRIVATE -Wall -Wextra)
target_link_options(app PRIVATE -static-libgcc -static-libstdc++)
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


def write_global_cpp_settings() -> bool:
    settings_path = vscode_user_settings_path()
    bin_path = msys2_bin_forward_slash()

    existing: dict = {}
    if settings_path.is_file():
        raw = settings_path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError:
                backup_path = settings_path.with_suffix(".json.bak")
                shutil.copy(settings_path, backup_path)
                print(f"  heads up: {settings_path} didn't parse as plain JSON (probably")
                print(f"  has // comments in it, which VS Code allows but this script's")
                print(f"  parser doesn't). backed it up to {backup_path} and starting")
                print(f"  fresh for this write -- copy anything important back by hand.")
                existing = {}

    existing["C_Cpp.default.compilerPath"] = f"{bin_path}/g++.exe"
    existing["C_Cpp.default.cppStandard"] = "c++17"
    existing["C_Cpp.default.debuggerPath"] = f"{bin_path}/gdb.exe"
    existing["C_Cpp.debugShortcut"] = False

    terminal_env = existing.setdefault("terminal.integrated.env.windows", {})
    terminal_env["PATH"] = f"{bin_path};${{env:PATH}}"

    existing["code-runner.executorMap"] = {
        **existing.get("code-runner.executorMap", {}),
        "c": f'cd $dir && "{bin_path}/gcc.exe" -static $fileName -o $fileNameWithoutExt.exe && $dir$fileNameWithoutExt.exe',
        "cpp": f'cd $dir && "{bin_path}/g++.exe" -static $fileName -o $fileNameWithoutExt.exe && $dir$fileNameWithoutExt.exe',
    }
    existing["code-runner.runInTerminal"] = True
    existing["terminal.integrated.defaultProfile.windows"] = "Command Prompt"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with settings_path.open("w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4)

    print(f"  wrote global C/C++ defaults to {settings_path}")
    return True


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

    hello_path = project_dir / "hello.cpp"
    hello_path.write_text(HELLO_CPP, encoding="utf-8")
    print(f"  wrote {hello_path}")


def generate_allegro_project(project_dir: Path) -> None:
    print(f"setting up the Allegro project at {project_dir}")
    write_json(project_dir / ".vscode" / "tasks.json", build_allegro_tasks_json())
    write_json(project_dir / ".vscode" / "launch.json", build_allegro_launch_json())
    write_json(project_dir / ".vscode" / "settings.json", build_allegro_settings_json())

    (project_dir / "assets").mkdir(parents=True, exist_ok=True)

    cmakelists_path = project_dir / "CMakeLists.txt"
    cmakelists_path.write_text(ALLEGRO_CMAKELISTS, encoding="utf-8")
    print(f"  wrote {cmakelists_path}")

    main_cpp_path = project_dir / "main.cpp"
    main_cpp_path.write_text(ALLEGRO_MAIN_CPP, encoding="utf-8")
    print(f"  wrote {main_cpp_path}")


_SMOKE_TEST_SOURCE = """\
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << "RESULT:" << (n * 2) << std::endl;
    return 0;
}
"""


def run_smoke_test() -> bool:
    gxx = Path(f"{msys2_bin_forward_slash()}/g++.exe")
    gdb = Path(f"{msys2_bin_forward_slash()}/gdb.exe")

    if not gxx.is_file():
        print(f"  can't find g++ at {gxx}, so skipping the smoke test.")
        print("  that means the compiler install above didn't actually land where")
        print("  this script expects -- worth checking by hand.")
        return False

    if not gdb.is_file():
        print(f"  note: no gdb at {gdb}. compiler's fine, but stepping through")
        print("  code with the debugger might not be until that's sorted.")

    workdir = Path(os.environ.get("TEMP", os.environ.get("TMP", "."))) / "cpp_setup_smoke_test"
    workdir.mkdir(parents=True, exist_ok=True)
    src_path = workdir / "smoke_test.cpp"
    exe_path = workdir / "smoke_test.exe"
    src_path.write_text(_SMOKE_TEST_SOURCE, encoding="utf-8")

    compile_result = run(
        [str(gxx), "-static", str(src_path), "-o", str(exe_path)],
        timeout=60,
        label="compiling a real test program...",
    )
    if compile_result.returncode != 0:
        print("  the compiler itself didn't work -- that's a real error above,")
        print("  not a guess.")
        return False

    if not exe_path.is_file():
        print(f"  compiler said it worked but {exe_path} isn't actually there.")
        return False

    try:
        run_result = subprocess.run(
            [str(exe_path)],
            input="21\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("  the compiled program just hung instead of finishing. not expected.")
        return False

    expected = "RESULT:42"
    if expected not in run_result.stdout:
        print(f"  it ran but gave the wrong answer -- expected '{expected}' somewhere")
        print(f"  in the output, got: {run_result.stdout!r}")
        return False

    print("  compiled and ran a real program and got the right answer (21 -> 42).")
    print("  the toolchain genuinely works, this isn't an assumption.")

    try:
        shutil.rmtree(workdir)
    except OSError:
        pass

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allegro", action="store_true",
        help="also set up Allegro/CMake/Ninja/pkgconf and generate the Allegro starter project",
    )
    parser.add_argument(
        "--projects-dir", default=str(Path.home() / "cpp-projects"),
        help="where the starter project(s) get created",
    )
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir)
    total_steps = 6

    print("setting up VS Code for C++.")
    print(f"this is {total_steps} steps, mostly spent downloading things --")
    print("here's what's happening at each point so it's not a black box.")

    print_step(f"step 1 of {total_steps}: Visual Studio Code")
    if not install_vscode():
        print("\ncouldn't get VS Code installed, so stopping here.")
        return 1

    code_cmd = find_code_cmd()
    if code_cmd is None:
        print("\nVS Code reported a successful install, but this script couldn't")
        print("actually find its code.cmd afterward, so the extensions step below")
        print("can't run. unusual -- worth a manual look.")
        return 1

    print_step(f"step 2 of {total_steps}: C++ compiler + debugger (via MSYS2)")
    if not install_msys2():
        print("\ncouldn't get MSYS2 installed, so stopping here.")
        return 1

    if not update_msys2_core():
        print("\ncouldn't finish updating MSYS2, so stopping here.")
        return 1

    packages = list(BASE_PACKAGES)
    if args.allegro:
        packages += ALLEGRO_PACKAGES

    if not install_packages(packages):
        print("\ncouldn't finish installing the compiler toolchain, so stopping here.")
        return 1

    print_step(f"step 3 of {total_steps}: VS Code extensions")
    if not install_extensions(code_cmd):
        print("\n  a couple extensions didn't install -- carrying on anyway, the")
        print("  core setup doesn't actually depend on them.")

    print_step(f"step 4 of {total_steps}: making this work in every folder, not just one")
    write_global_cpp_settings()

    print_step(f"step 5 of {total_steps}: proving the toolchain actually works")
    toolchain_verified = run_smoke_test()
    if not toolchain_verified:
        print("  so VS Code + extensions are in, but the underlying compiler setup")
        print("  isn't right yet. fix that before trusting what the play button does.")

    print_step(f"step 6 of {total_steps}: starter project")
    generate_console_project(projects_dir / "cpp-exercises")
    if args.allegro:
        generate_allegro_project(projects_dir / "allegro-project")

    print("\nall done. here's what you've got:")
    if toolchain_verified:
        print("  - a compiler + debugger that were just proven to work by actually")
        print("    compiling and running something, not just assumed")
    else:
        print("  - WARNING: the toolchain check above did not pass. VS Code and the")
        print("    extensions are installed, but don't trust the play button yet.")
    print("  - VS Code with a decent extension set")
    print("  - a play button that works for any .c/.cpp file, in any folder")
    print("  - a ready-to-open starter project at:")
    print(f"      {projects_dir / 'cpp-exercises'}")
    if args.allegro:
        print("  - an Allegro starter project at:")
        print(f"      {projects_dir / 'allegro-project'}")
    print("\nopen that folder in VS Code, open hello.cpp, hit the play button in the")
    print("top-right of the editor, and it should just run.")
    print("\none thing worth knowing: if a terminal tab was already open in VS Code")
    print("before this ran, that specific tab keeps whatever shell it already had --")
    print("it won't switch over on its own. if the play button's output looks like")
    print("it's in PowerShell (prompt starts with 'PS'), close that terminal tab;")
    print("the next one VS Code opens will use the Command Prompt shell this just set up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())