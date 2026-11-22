# foo_doujinstyle

Experimental **DoujinStyle integration for [foobar2000](https://www.foobar2000.org/)** on Windows: find and index music releases, publish playable track references, and stream selected audio members from supported remote archives directly into foobar2000's installed decoders.

**Status: early development / unsigned test builds.** The component has passed Windows x86/x64 CI compilation and a developer-reported one-song MediaFire playback smoke test. That is **not** a completed host compatibility, security, or release qualification. No stable or signed public release has been published.

## What it does

- **Search and browse** DoujinStyle releases from within foobar2000, including cached searches and popularity views.
- **Index remotely hosted music** into a local SQLite catalog, including track metadata, artwork hints and ZIP member inventory where available.
- **Publish `.djt` references**—small local UTF-8 files you can add to foobar2000's Media Library. They retain playlist identity while pointing to a remote audio member.
- **Play from remote archives** using bounded HTTP byte-range requests and ZIP STORED/DEFLATE readers. Audio decoding is delegated to your existing foobar2000 decoders.
- **Inspect and repair metadata** through the component's background native worker.

### Requirements and limitations

| Area | Current state |
| --- | --- |
| Player | foobar2000 2.x for Windows |
| Architectures | Windows **x86 and x64** test builds; **ARM64EC** target exists but is not yet CI/host-qualified. Windows-on-ARM foobar2000 can load x64 components. No ARM32 target. |
| Providers | DoujinStyle metadata, MediaFire archives, supported public MEGA links; third-party sites can change without notice |
| Catalog | Local SQLite; indexing and catalog writes are separate from foobar2000 audio decoding |
| Python | **Still required for foreground search/chart operations** in the current experimental package. Native migration is in progress; some background operations already run in C++ |
| Signing | Experimental Actions artifacts are **unsigned**, expire, and are not formal GitHub Releases |

The component processes third-party web content and may transfer archive ranges over the network. Remote media may be unavailable, modified, access-controlled or subject to host policies. Do not treat catalog metadata as independently verified.

## Install a test build

1. Open [GitHub Actions](https://github.com/bgonz808/foo_doujinstyle/actions) and select a **successful Windows component compile** run for the source revision you intend to test.
2. Download the `foo_doujinstyle-windows-wip` Actions artifact and extract the contained `.fb2k-component` package. Review the workflow, source SHA and published checksum; the artifacts are not signed.
3. In foobar2000, open **Preferences → Components → Install**, select the `.fb2k-component`, and restart.
4. Until native foreground cutover finishes, install Python 3.10+ and the declared source-tree runtime dependencies with `py -3 -m pip install --group runtime` from a checked-out source directory.
5. Set catalog and published reference locations in **Preferences → Tools → DoujinStyle**; use a folder watched by **Preferences → Media Library → Music folders**.

These are developer/tester builds. Prefer a disposable foobar2000 profile and catalog for experiments.

## First use

Open **Library → Search DoujinStyle…** and search for a release or tag. Indexed releases with eligible archive members can publish one `.djt` file per playable track. The library then treats those references as ordinary file paths. Playback opens the designated remote member through the component; it does **not** download a permanent local music copy by default.

For troubleshooting, enable **View → Console** and look for HTTP response/protocol, ZIP range transfer, decoder and cancellation events. The diagnostic log intentionally omits token-bearing remote URLs.

## Security, development and reference documentation

- [Security policy and vulnerability reporting](SECURITY.md) — security claims are limited to evidence actually produced by CI and host tests.
- [Build and verification](docs/BUILDING.md) — native SDK requirements, current CI gates, and test artifacts.
- [Native architecture and developer notes](NATIVE.md) — detailed engineering notes (some sections describe historical migration checkpoints).
- [Development and migration backlog](docs/DEVELOPMENT_PLAN.md) — historical baseline plus active migration constraints.
- [Third-party dependencies and licenses](docs/THIRD_PARTY.md).
- [Historical long-form project README](docs/developer/README-v0.0.65-technical-snapshot.md) and [validation history](docs/history/validation/README.md).

Contributions and bug reports: [GitHub Issues](https://github.com/bgonz808/foo_doujinstyle/issues). Include the exact component build identity and source commit, but redact local paths, access tokens and signed remote URLs before attaching logs.

**License:** [MIT](LICENSE). Third-party components have their own license notices.
