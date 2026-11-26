"""AST inventory of the legacy Python parity fixture, not the shipped worker."""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path

from native.pack_search_worker import SOURCES, worker_sources

CATEGORIES = {
    "foreground-search": {"run_search", "index_results", "schedule_index", "dispatch_archives", "present_archive_event"},
    "charts-and-prefetch": {"_consume_native_chart", "run_popular", "prefetch_charts", "chart_snapshot", "chart_listings"},
    "ui-and-artwork": {"result_record", "display_date", "cache_cover", "cache_brand", "write_reference", "size_text"},
    "download-and-repair": {"download_archive", "repair_release", "export", "native_offer_batch"},
    "startup-and-runtime": {"main", "open_worker_catalog", "SearchHttp", "discovery_worker"},
}

def classify(module, function):
    if module == "catalog_cache":
        return "catalog-read-model-and-adapter"
    if module == "native.reference_publisher":
        return "native-ipc-transport"
    if module == "native.runtime_contract":
        return "compatibility-interface"
    for label, names in CATEGORIES.items():
        if function in names:
            return label
    return "other-search-runtime"


def inspect(root):
    members, pruned = worker_sources(root)
    modules, symbols, imports = {}, {}, set()
    for filename in SOURCES:
        module = filename[:-3].replace("/", ".")
        tree = ast.parse(members[filename], filename=filename)
        symbols[module] = {node.name for node in tree.body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for filename in SOURCES:
        module = filename[:-3].replace("/", ".")
        source = members[filename]
        tree = ast.parse(source, filename=filename)
        lines = source.splitlines()
        occupied, blocks, labels = set(), [], Counter()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            span = set(range(start, node.end_lineno + 1))
            if occupied & span:
                raise ValueError(f"Overlapping AST spans {module}:{node.name}")
            occupied.update(span)
            label = classify(module, node.name)
            labels[label] += len(span)
            names = set()
            ipc = set()
            dynamic = False
            for item in ast.walk(node):
                if isinstance(item, ast.Call):
                    if isinstance(item.func, ast.Name):
                        names.add(item.func.id)
                        dynamic |= item.func.id in {"globals", "getattr", "eval", "exec"}
                    elif isinstance(item.func, ast.Attribute):
                        names.add(item.func.attr)
                        dynamic |= item.func.attr in {"request", "submit", "getattr"}
                        if item.func.attr == "request" and len(item.args) >= 2:
                            if isinstance(item.args[1], ast.Constant) and isinstance(item.args[1].value, str):
                                ipc.add(item.args[1].value)
            blocks.append({"name": node.name, "category": label,
                           "first_line": start, "last_line": node.end_lineno,
                           "physical_lines": len(span),
                           "same_module_calls": sorted(names & symbols[module]),
                           "native_ipc_operations": sorted(ipc),
                           "dynamic_dispatch_possible": bool(dynamic)})
        labels["imports-constants-and-unscoped"] = len(lines) - len(occupied)
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom) and item.module in symbols:
                imports.add((module, item.module))
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    if alias.name in symbols:
                        imports.add((module, alias.name))
        modules[module] = {"path": filename, "packaged_lines": len(lines),
                           "source_lines": len((root / filename).read_bytes().splitlines()),
                           "categories": dict(labels), "blocks": blocks}
    total = sum(m["packaged_lines"] for m in modules.values())
    entry = len(members["__main__.py"].splitlines())
    if total + entry != pruned["python_lines"]:
        raise ValueError("AST tally does not equal packed zipapp bytes")
    cat = Counter()
    for value in modules.values():
        cat.update(value["categories"])
    return {"schema_version": 1, "kind": "legacy-parity-fixture-python-ast",
            "packaged_python_lines": pruned["python_lines"],
            "packaged_python_files": pruned["python_files"],
            "entry_python_lines": entry, "fallback_python_lines": pruned["fallback_python_lines"],
            "unmarked_python_lines": pruned["unmarked_python_lines"],
            "deprecated_active_lines": pruned["deprecated_active_lines"],
            "category_lines": dict(sorted(cat.items())),
            "static_module_imports": sorted(map(list, imports)), "modules": modules,
            "limitations": [
                "AST call edges underapproximate dynamic and callback dispatch",
                "Category counts include comments and docstrings, not executable coverage",
                "Only modules in the development parity zipapp are counted; production embeds a native executable"
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(args.root)
    print(f"LEGACY_FIXTURE_PYTHON_LINES={result['packaged_python_lines']}")
    print(f"LEGACY_FIXTURE_PYTHON_MODULES={result['packaged_python_files']}")
    print(f"FALLBACK_PYTHON_LINES={result['fallback_python_lines']}")
    print(f"DEPRECATED_ACTIVE_LINES={result['deprecated_active_lines']}")
    for group, count in sorted(result["category_lines"].items(), key=lambda x: (-x[1], x[0])):
        print(f"  {group}: {count}")
    for name, module in result["modules"].items():
        print(f"  {name}: packed={module['packaged_lines']} source={module['source_lines']}")
    if args.output:
        if args.output.exists():
            parser.error("Refusing to overwrite inventory evidence")
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
