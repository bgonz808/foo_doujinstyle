"""Build the small Python zipapp embedded in the foobar2000 component."""
import ast
import copy
import textwrap
import io
import json
from pathlib import Path
import struct
import sys
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo


ENTRY = b"""import sys
from native.search_worker import main
raise SystemExit(main(publisher_bundle=sys.argv[0]))
"""
SOURCES = ("catalog_cache.py", "native/runtime_contract.py",
           "native/search_worker.py",
           "native/reference_publisher.py")


def main_guard(node):
    return (isinstance(node, ast.If) and not node.orelse and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
            and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)
            and isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__")


def prune_module(contents, filename):
    tree = ast.parse(contents, filename=filename)
    exclude = set()
    fallback_lines = set()
    deprecated_lines = set()
    deprecations = {}
    standalone = []
    fallbacks = {}
    native_methods = {}

    def strip(first, last):
        exclude.update(range(first, last + 1))

    def inspect(node, qualified):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        for marker in node.decorator_list:
            if isinstance(marker, ast.Name) and marker.id == "native_worker_only":
                if not qualified.startswith("CatalogCache."):
                    raise ValueError("native_worker_only is reserved for audited native catalog methods")
                native_branch = next((statement for statement in node.body
                    if isinstance(statement, ast.If) and isinstance(statement.test, ast.Attribute)
                    and isinstance(statement.test.value, ast.Name)
                    and statement.test.value.id == "self" and statement.test.attr == "native"), None)
                if native_branch is None or native_branch.orelse or not any(
                        isinstance(statement, (ast.Return, ast.Raise)) for statement in native_branch.body):
                    raise ValueError(f"{filename}:{node.lineno}: native-only method needs a terminating native branch")
                original = copy.deepcopy(node)
                original.decorator_list = []
                prefix = node.body[:node.body.index(native_branch)]
                fail = ast.parse('if not self.native: raise RuntimeError("Bundled catalog requires the native helper")').body[0]
                original.body = [*prefix, fail, *native_branch.body]
                ast.fix_missing_locations(original)
                start = min(decoration.lineno for decoration in node.decorator_list)
                native_methods[start] = (node.end_lineno, textwrap.indent(ast.unparse(original), "    ") + "\n")
                strip(start, node.end_lineno)
                standalone.append(qualified + " [Python compatibility arm]")
                return
        for marker in node.decorator_list:
            if isinstance(marker, ast.Call) and isinstance(marker.func, ast.Name) and marker.func.id == "runtime_deprecated":
                if (len(marker.args) != 1 or marker.keywords or not isinstance(marker.args[0], ast.Constant)
                        or not isinstance(marker.args[0].value, str) or not marker.args[0].value.strip()):
                    raise ValueError(f"{filename}:{marker.lineno}: runtime deprecation needs a literal reason")
                deprecations[qualified] = marker.args[0].value
                strip(marker.lineno, marker.end_lineno)
                deprecated_lines.update(range(node.lineno, node.end_lineno + 1))
        for marker in node.decorator_list:
            if isinstance(marker, ast.Name) and marker.id == "standalone_only":
                first = min(decoration.lineno for decoration in node.decorator_list)
                strip(first, node.end_lineno)
                standalone.append(qualified)
                return
            if isinstance(marker, ast.Call) and isinstance(marker.func, ast.Name) and marker.func.id == "worker_fallback":
                if (len(marker.args) != 1 or marker.keywords or not isinstance(marker.args[0], ast.Constant)
                        or not isinstance(marker.args[0].value, str) or not marker.args[0].value.strip()):
                    raise ValueError(f"{filename}:{marker.lineno}: expected a literal fallback reason")
                fallbacks[qualified] = marker.args[0].value
                strip(marker.lineno, marker.end_lineno)
                first = min(decoration.lineno for decoration in node.decorator_list)
                fallback_lines.update(range(first, node.end_lineno + 1))

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "native.worker_scope":
            strip(node.lineno, node.end_lineno)
        elif main_guard(node):
            strip(node.lineno, node.end_lineno)
        elif isinstance(node, ast.ClassDef):
            if any(isinstance(marker, ast.Name) and marker.id == "standalone_only"
                   for marker in node.decorator_list):
                first = min(decoration.lineno for decoration in node.decorator_list)
                strip(first, node.end_lineno)
                standalone.append(node.name)
                continue
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inspect(method, f"{node.name}.{method.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inspect(node, node.name)

    lines = contents.splitlines(keepends=True)
    packed = b"".join((native_methods[i][1].encode("utf-8") if i in native_methods else b"") +
                      (line if i not in exclude else b"") for i, line in enumerate(lines, 1))
    tree = ast.parse(packed, filename=filename)
    return packed, tree, {"python_lines": len(packed.splitlines()), "excluded_lines": len(exclude),
                          "fallback_python_lines": len(fallback_lines - exclude),
                          "deprecated_active_lines": len(deprecated_lines - exclude),
                          "standalone": standalone, "fallbacks": fallbacks, "deprecated_runtime": deprecations}


def worker_sources(source):
    members = {"__main__.py": ENTRY, "native/__init__.py": b""}
    retained = {}
    inventory = {}
    standalone = {}
    for name in SOURCES:
        output, tree, report = prune_module((source / name).read_bytes(), name)
        module = name.removesuffix(".py").replace("/", ".")
        members[name], retained[module], inventory[name] = output, tree, report
        standalone[module] = set(report["standalone"])

    # These checks cover direct name references. Keep integration fixtures for
    # attributes reached through dynamic dispatch and optional worker modes.
    for module, tree in retained.items():
        symbols = {name for name in standalone[module] if "." not in name}
        for top_level in tree.body:
            if isinstance(top_level, ast.ImportFrom) and top_level.module in {
                    "doujin", "native.archive_zip", "native.archive_providers", "native.idle_backfill"}:
                raise ValueError(f"{module}:{top_level.lineno}: installed worker imports retired module "
                                 f"{top_level.module}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in symbols:
                raise ValueError(f"{module}:{node.lineno}: installed worker refers to source-only {node.id}")
            if isinstance(node, ast.ImportFrom) and node.module in standalone:
                prohibited = {name for name in standalone[node.module] if "." not in name}
                for alias in node.names:
                    if alias.name in prohibited or (alias.name == "*" and prohibited):
                        raise ValueError(f"{module}:{node.lineno}: installed worker imports source-only "
                                         f"{node.module}.{alias.name}")
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for node in ast.walk(cls):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "self" and f"{cls.name}.{node.attr}" in standalone[module]):
                    raise ValueError(f"{module}:{node.lineno}: installed worker refers to source-only "
                                     f"{cls.name}.{node.attr}")

    members["native/catalog_schema.sql"] = (source / "native/catalog_schema.sql").read_bytes()
    report = {"python_files": sum(name.endswith(".py") for name in members),
              "python_lines": sum(len(data.splitlines()) for name, data in members.items() if name.endswith(".py")),
              "fallback_python_lines": sum(module["fallback_python_lines"] for module in inventory.values()),
              "deprecated_active_lines": sum(module["deprecated_active_lines"] for module in inventory.values()),
              "modules": inventory}
    report["unmarked_python_lines"] = report["python_lines"] - report["fallback_python_lines"]
    return members, report


def payload(source, publisher):
    members, _ = worker_sources(source)
    members["publish_references.exe"] = publisher.read_bytes()
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as bundle:
        for name, data in members.items():
            compression = ZIP_STORED if name == "publish_references.exe" else ZIP_DEFLATED
            bundle.writestr(ZipInfo(name), data, compress_type=compression)
    return out.getvalue()


def main():
    source = Path(__file__).resolve().parent.parent
    if sys.argv[1:] == ["--inventory"]:
        _, report = worker_sources(source)
        print(json.dumps(report, indent=2))
        return
    target = Path(sys.argv[1])
    data = payload(source, Path(sys.argv[2]))
    with ZipFile(io.BytesIO(data)) as bundle:
        worker = bundle.getinfo("publish_references.exe")
    name_length, extra_length = struct.unpack_from("<HH", data, worker.header_offset + 26)
    offset = worker.header_offset + 30 + name_length + extra_length
    if data[offset:offset + worker.file_size] != Path(sys.argv[2]).read_bytes():
        raise ValueError("The native executable must be stored verbatim in the worker archive")
    lines = ["    " + ", ".join(f"0x{byte:02x}" for byte in data[offset:offset + 16]) + ","
             for offset in range(0, len(data), 16)]
    generated = ("constexpr unsigned char search_worker_payload[] = {\n" + "\n".join(lines) + "\n};\n"
                 f"constexpr std::size_t native_worker_offset = {offset};\n"
                 f"constexpr std::size_t native_worker_size = {worker.file_size};\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="ascii") != generated:
        target.write_text(generated, encoding="ascii")


if __name__ == "__main__":
    main()
