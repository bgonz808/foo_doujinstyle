"""Build and package x86 and x64 Windows components using clang-cl and xwin."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from analyze_native import run_analysis, write_database
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.build_identity import identify, header
from tools.package_component import pack


NATIVE = Path(__file__).resolve().parent
TARGETS = {
    "x86": ("i686-pc-windows-msvc", "x86", "Win32", ""),
    "x64": ("x86_64-pc-windows-msvc", "x86_64", "x64", "x64/"),
}
SDK_PROJECTS = (
    "pfc/pfc.vcxproj",
    "foobar2000/SDK/foobar2000_SDK.vcxproj",
    "foobar2000/foobar2000_component_client/foobar2000_component_client.vcxproj",
)
SYSTEM_LIBRARIES = (
    "bcrypt", "comctl32", "comdlg32", "gdi32", "ole32", "shell32", "shlwapi",
    "user32", "uuid", "uxtheme", "windowscodecs", "winhttp", "winmm", "ws2_32",
)


def compile_items(project):
    for entry in ET.parse(project).findall(".//{*}ClCompile"):
        include = entry.get("Include")
        if include and include != "nix-objects.cpp":
            yield project.parent / include.replace("\\", "/")


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(map(str, command))}\n{result.stdout}")
    return result.stdout


def build_architecture(args, arch, sources, common):
    triple, msvc_arch, platform, _ = TARGETS[arch]
    output = args.output / arch
    output.mkdir(parents=True, exist_ok=True)
    generated = output / "generated"
    includes = [args.xwin / "crt/include", *(args.xwin / f"sdk/include/{part}"
                for part in ("ucrt", "um", "shared", "winrt"))]
    flags = [f"--target={triple}", "/nologo", "/std:c++20", "/MD", "/EHsc", "/O2", "/utf-8",
             "/guard:cf", "/DNDEBUG", "/DNOMINMAX", "/DUNICODE", "/D_UNICODE", "/D_WINDOWS", "/D_WINDLL",
             f"/imsvc{args.sdk}", f"/imsvc{args.sdk / 'foobar2000'}", f"/I{generated}", f"/I{common}",
             f"/I{common / 'sqlite'}", f"/FI{common / 'windows_first.h'}",
             *(f"/imsvc{path}" for path in includes)]
    libs = [args.xwin / f"{relative}/{msvc_arch}" for relative in ("crt/lib", "sdk/lib/ucrt", "sdk/lib/um")]
    link = [args.llvm_bin / "lld-link", "/nologo", "/WX", f"/machine:{arch}", "/incremental:no",
            "/opt:ref,icf", "/dynamicbase", "/nxcompat", "/guard:cf", "/timestamp:0",
            *(f"/libpath:{path}" for path in libs)]
    sqlite = output / "sqlite_amalgamation.obj"
    writer_sqlite = output / "publisher_sqlite.obj"
    unicode = output / "utf8proc.obj"
    writer = output / "publish_references.obj"
    writer_zip = output / "publisher_descriptor.obj"
    writer_identity = output / "worker_build_identity.obj"
    publisher = output / "publish_references.exe"
    worker_flags = [flag for flag in flags if flag != "/D_WINDLL" and not flag.startswith("/FI")] + ["/DUTF8PROC_STATIC"]

    first_party = ["/W4", "/WX"]
    compiler = args.llvm_bin / "clang-cl"

    def cpp_command(source, destination, options):
        return [compiler, *options, "/c", f"/Tp{source}", f"/Fo{destination}"]

    def compile_sqlite(options, target):
        run([compiler, *(flag for flag in options if flag != "/std:c++20"), "/W0", "/c",
             f"/Tc{NATIVE / 'sqlite_amalgamation.c'}", f"/Fo{target}"])

    worker_commands = [
        (NATIVE / "publish_references.cpp", cpp_command(NATIVE / "publish_references.cpp", writer, [*worker_flags, *first_party])),
        (NATIVE / "descriptor.cpp", cpp_command(NATIVE / "descriptor.cpp", writer_zip, [*worker_flags, *first_party])),
        (NATIVE / "worker_build_identity.cpp", cpp_command(NATIVE / "worker_build_identity.cpp", writer_identity, [*worker_flags, *first_party])),
    ]
    worker_database = write_database(output / "worker", worker_commands)
    if args.analyze:
        run_analysis(worker_database, args.llvm_bin / "clang-tidy", strict=args.analyze == "strict")

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_sqlite_task = pool.submit(compile_sqlite, worker_flags, writer_sqlite)
        sqlite_task = pool.submit(compile_sqlite, flags, sqlite)
        for _, command in worker_commands:
            run(command)
        run([compiler, *[flag for flag in worker_flags if flag != "/std:c++20"], "/W0", "/c",
             f"/Tc{NATIVE / 'third_party/utf8proc/utf8proc.c'}", f"/Fo{unicode}"])
        writer_sqlite_task.result()
        sqlite_task.result()
    run([*link, "/subsystem:console", f"/out:{publisher}", writer, writer_zip, writer_identity, writer_sqlite, unicode, "kernel32.lib", "shlwapi.lib", "winhttp.lib", "bcrypt.lib"])
    run([sys.executable, NATIVE / "pack_search_worker.py", generated / "search_worker_payload.inc", publisher])

    objects = []
    commands = []
    for index, source in enumerate(sources):
        obj = output / f"{index:03d}-{source.stem}.obj"
        extra = first_party if source.parent == NATIVE else ["/W0"]
        if source == args.sdk / "pfc/audio_math.cpp":
            extra = [*extra, "/arch:AVX"]
        objects.append(obj)
        commands.append((source, cpp_command(source, obj, [*flags, *extra])))
    database = write_database(output, commands)
    if args.analyze:
        run_analysis(database, args.llvm_bin / "clang-tidy", strict=args.analyze == "strict")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for done, _ in enumerate(pool.map(run, (command for _, command in commands)), 1):
            if done in (len(commands), 3) or done % 25 == 0:
                print(f"{arch}: compiled {done}/{len(commands)} translation units", flush=True)

    dll = output / "foo_doujinstyle.dll"
    run([*link, "/dll", "/subsystem:windows", f"/out:{dll}", *objects, sqlite,
         args.sdk / f"foobar2000/shared/shared-{platform}.lib", *(f"{lib}.lib" for lib in SYSTEM_LIBRARIES)])
    return dll


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--xwin", type=Path, required=True)
    parser.add_argument("--llvm-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=NATIVE / "build-cross")
    parser.add_argument("--arch", nargs="+", default=list(TARGETS), choices=TARGETS)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--analyze", nargs="?", const="strict", default="strict", choices=("report", "strict"),
                        help="run clang-tidy and Clang Static Analyzer before compiling first-party C++")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for name in ("sdk", "xwin", "llvm_bin", "output"):
        setattr(args, name, getattr(args, name).resolve())
    identity = identify(NATIVE.parent)
    version = identity["version"]
    generated = args.output / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "doujinstyle_build_version.hpp").write_text(header(identity), encoding="utf-8")
    run([sys.executable, NATIVE / "sqlite_source.py", generated / "sqlite"])
    # Leave GUID equality to PFC so it can be used in its static_asserts.
    (generated / "windows_first.h").write_text(
        "#define _NO_SYS_GUID_OPERATOR_EQ_\n#include <winsock2.h>\n#include <windows.h>\n", encoding="ascii")
    for header in ("MMReg.h", "Objbase.h", "SDKDDKVer.h", "ShlObj.h", "Shlwapi.h"):
        (generated / header).write_text(f"#include <{header.lower()}>\n", encoding="ascii")
    sources = [source for source in compile_items(NATIVE / "foo_doujinstyle.vcxproj") if source.suffix == ".cpp"]
    sources.extend(source for project in SDK_PROJECTS for source in compile_items(args.sdk / project))
    dlls = {arch: build_architecture(args, arch, sources, generated) for arch in dict.fromkeys(args.arch)}
    suffix = "" if set(dlls) == set(TARGETS) else "-" + next(iter(dlls))
    package = args.output / f"foo_doujinstyle-v{version}{suffix}.fb2k-component"
    entries = [("LICENSE.nlohmann-json", NATIVE / "third_party/LICENSE.nlohmann-json"),
               ("LICENSE.utf8proc", NATIVE / "third_party/utf8proc/LICENSE.md")]
    entries.extend((TARGETS[arch][3] + dll.name, dll) for arch, dll in dlls.items())
    pack(package, entries)
    print(package)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
