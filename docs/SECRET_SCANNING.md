# Secret detection baseline

GitHub provider-pattern scanning and push protection remain enabled. Gitleaks
8.30.1 Linux x64 is acquired from the upstream release, with its archive pinned
to SHA-256 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
(corroborated by public mirror and independent tool lock). No extra Action or
credentials. Built-in rules only; no custom rule tuning, repository ignore
files, inline allow comments, remote validity checks, or auto-baseline.
Scan the complete reachable HEAD history including merge-parent diffs in the
existing required security job. Synthetic positive check, full-history checkout
and commit binding; scanner failures fail the job. Initially report only count
and elapsed time: findings are observations until privately triaged, not a
passing secret-policy verdict. No raw findings or logs uploaded. Review results
before making new findings blocking; never silently suppress historic findings.
Archives/binaries and untracked files are not scanned by this Git mode. Track
tool updates and dependency risk under #60; repo governance #15.
