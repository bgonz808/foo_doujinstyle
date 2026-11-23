# Vendored source-input SPDX inventory

This is a **partial source-input** inventory, not a complete binary SBOM.
It is independently mergeable because it does not change the Windows linker,
release signer, or binary-security waiver policy.

Run `python -m tools.source_sbom --root . --commit <full-40-character-sha> --output /tmp/source-inputs.spdx.json` from a clean checkout. The generator records the exact tracked vendored-source SHA-256 and verifies nlohmann-json 3.12.0, utf8proc 2.9.0, and zlib 1.3.1 header versions plus the SQLite 3.54.1 source archive. It deliberately does not assert full package-file enumeration, dependency or linked-artifact inclusion, nor runtime correctness.

A complete release SBOM still requires material provenance for foobar2000 SDK, compiled SDK static archives, MSVC/CRT, Windows system imports, generated worker, runtime Python, and link-extracted objects. Bind those to the exact packaged DLL and publish them only with the approved signed release predicate in #31. See #35 for the ARM64EC Spectre runtime blocker and #32 for required release governance.
