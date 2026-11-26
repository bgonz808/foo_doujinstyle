# Secret detection baseline

GitHub provider-pattern scanning and push protection remain enabled. Gitleaks
8.30.1 Linux x64 is acquired from the upstream release, with its archive pinned
to SHA-256 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
(corroborated by public mirror and independent tool lock). No extra Action or
credentials. Built-in rules only; no custom rule tuning, repository ignore
files, inline allow comments, remote validity checks, or auto-baseline.
Scan the complete reachable HEAD history including merge-parent diffs in the
existing required security job. Synthetic positive check, full-history checkout
and commit binding; scanner failures fail the job. All unadjudicated findings
block. Report counts and elapsed time only; no raw findings or logs uploaded.

## Historical adjudication

On 2026-11-26 the owner reproduced the sole finding from the baseline run.
The exact historical commit, file, rule and line were inspected: ordinary prose
about rejecting malformed responses and archive headers, not a credential.
The current sentence is reworded and scanned separately. The pinned detector
still reports the historical occurrence; the post-scan policy admits exactly
one matching record at the immutable historical coordinates, never a file-wide,
line-wide or rule-wide exemption. Changes in commit, path, rule or line remain
blocking. Review the exception when updating the scanner or its rules; fail if
it disappears unexpectedly. No Git history rewrite or detector suppression.
Archives/binaries and untracked files are not scanned by this Git mode. Track
tool updates and dependency risk under #60; repo governance #15.
