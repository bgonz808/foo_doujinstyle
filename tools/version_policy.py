"""Source-controlled release coordinates; no wall clock or CI execution inputs."""
import re
from dataclasses import dataclass
from pathlib import Path

VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
REVISION_RE = re.compile(r"[1-9][0-9]{0,9}")
STAGE_RE = re.compile(r"dev|stable|rc\.[1-9][0-9]*")
MAX_REVISION = 2**31 - 1


def parse_revision(value):
    if not REVISION_RE.fullmatch(value) or int(value) > MAX_REVISION:
        raise ValueError("Invalid REVISION: expected a positive bounded decimal")
    return int(value)


@dataclass(frozen=True)
class VersionPolicy:
    base: str
    revision: int
    stage: str

    @property
    def product_version(self):
        if self.stage == "dev":
            return f"{self.base}-dev.{self.revision}"
        if self.stage == "stable":
            return self.base
        return f"{self.base}-{self.stage}"

    def diagnostic_version(self, *, commit="", source_sha256, dirty=False):
        if not commit:
            return f"{self.base}-dev.{self.revision}+src.{source_sha256[:12]}"
        metadata = f"g{commit[:12]}"
        if self.stage != "dev":
            metadata = f"rev.{self.revision}." + metadata
        if dirty:
            metadata += f".dirty.src.{source_sha256[:12]}"
        return f"{self.product_version}+{metadata}"


def read_policy(root):
    root = Path(root)
    base, rev, stage = ((root / name).read_text(encoding="ascii").strip()
                        for name in ("VERSION", "REVISION", "RELEASE_STAGE"))
    if not VERSION_RE.fullmatch(base) or not STAGE_RE.fullmatch(stage):
        raise ValueError("Invalid VERSION or RELEASE_STAGE")
    return VersionPolicy(base, parse_revision(rev), stage)
