"""Deterministic SPDX 2.3 *source input inventory*, never a complete binary SBOM.

Fail closed if vendored version macros drift from the independently declared
package versions. SHA256 references are for the exact tracked source inputs.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

COMPONENTS = (
    ("nlohmann-json", "3.12.0", "native/third_party/json.hpp",
     (r"#define NLOHMANN_JSON_VERSION_MAJOR 3", r"#define NLOHMANN_JSON_VERSION_MINOR 12", r"#define NLOHMANN_JSON_VERSION_PATCH 0")),
    ("utf8proc", "2.9.0", "native/third_party/utf8proc/utf8proc.h",
     (r"#define UTF8PROC_VERSION_MAJOR 2", r"#define UTF8PROC_VERSION_MINOR 9", r"#define UTF8PROC_VERSION_PATCH 0")),
    ("zlib-headers", "1.3.1", "native/zlib/zlib.h",
     (r'#define ZLIB_VERSION "1.3.1"',)),
    ("sqlite-amalgamation", "3.54.1", "native/sqlite-amalgamation-3540100.zip", ()),
)

def sha(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def generate(root: Path, commit: str, epoch: int) -> dict:
    if re.fullmatch("[a-f0-9]{40}", commit) is None:
        raise ValueError("Full lowercase git commit needed")
    date = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    packages, files, relations = [], [], []
    for idx, (name, version, relative, patterns) in enumerate(COMPONENTS, 1):
        file = root / relative
        if not file.is_file() or file.is_symlink():
            raise ValueError("Required source input absent: " + relative)
        if patterns:
            data = file.read_text(encoding="utf-8")
            if any(re.search(pattern, data) is None for pattern in patterns):
                raise ValueError("Vendored version drift: " + relative)
        package_id, file_id = f"SPDXRef-Package-{idx}", f"SPDXRef-File-{idx}"
        files.append({"SPDXID": file_id, "fileName": "./"+relative, "checksums": [
            {"algorithm": "SHA256", "checksumValue": sha(file)}],
            "licenseConcluded": "NOASSERTION", "copyrightText": "NOASSERTION"})
        packages.append({"name": name, "SPDXID": package_id, "versionInfo": version,
            "downloadLocation": "NOASSERTION", "filesAnalyzed": True,
            "licenseConcluded": "NOASSERTION", "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "packageComment": "Vendored source-input reference; not independently verified linked runtime contents."})
        relations += [{"spdxElementId":"SPDXRef-DOCUMENT","relatedSpdxElement":package_id,"relationshipType":"DESCRIBES"},
                      {"spdxElementId":package_id,"relatedSpdxElement":file_id,"relationshipType":"CONTAINS"}]
    return {"spdxVersion":"SPDX-2.3", "dataLicense":"CC0-1.0", "SPDXID":"SPDXRef-DOCUMENT",
       "name":"foo_doujinstyle-vendored-source-input-inventory",
       "documentNamespace":"https://github.com/bgonz808/foo_doujinstyle/spdx/source/"+commit,
       "documentComment":"PARTIAL source-input inventory only; excludes foobar2000 SDK, MSVC/CRT, dynamic packages, Python runtime, and independently proven static link membership.",
       "creationInfo":{"creators":["Tool: foo_doujinstyle-source-sbom"],"created":date},
       "packages":packages,"files":files,"relationships":relations}

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=Path.cwd())
    p.add_argument("--commit",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    epoch=int(subprocess.check_output(["git","-C",str(a.root),"log","-1","--format=%ct"],text=True).strip())
    obj=generate(a.root,a.commit,epoch)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(f"SPDX 2.3 *partial source-input* inventory: {len(obj['packages'])} declared entries")

if __name__=="__main__":
    main()
