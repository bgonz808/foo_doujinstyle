#!/usr/bin/env bash
# Shared, signed APT provisioning for the exact observed LLVM package closure.
# Only update LLVM metadata; do not refresh unrelated distro/mirror indexes.
# The hosted runner base supplies OpenSSL/zlib/Python development prerequisites.
set -euo pipefail
mode="${1:?usage: provision_llvm.sh compile|analysis}"
case "$mode" in
  compile|analysis) ;;
  *) echo "Invalid LLVM provisioning mode: $mode" >&2; exit 2 ;;
esac
: "${RUNNER_TEMP:?requires isolated runner temp}"
[[ "$(dpkg --print-architecture)" == amd64 ]]
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_CODENAME" == noble ]]
version='1:23.1.6~++20261116013850+5b10d2d74929-1~exp1~20261116133903.98'
packages=(clang-23 libclang-common-23-dev libclang-cpp23 libclang1-23 libllvm23 llvm-23-linker-tools)
if [[ "$mode" == analysis ]]; then
  packages+=(clang-tools-23 clang-tidy-23)
fi
baseline=(libssl-dev zlib1g-dev)
if [[ "$mode" == compile ]]; then baseline+=(python3-venv); fi
for package in "${baseline[@]}"; do
  status="$(dpkg-query -W -f='${Status} ${Version}' "$package")"
  [[ "$status" == 'install ok installed '* ]] || { echo "Missing runner prerequisite $package" >&2; exit 1; }
  echo "RUNNER-INPUT $package $status"
done
pins=()
ready=true
for package in "${packages[@]}"; do
  pins+=("$package=$version")
  if [[ "$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null || true)" != "install ok installed $version" ]]; then ready=false; fi
done
start=$SECONDS
if [[ "$ready" != true ]]; then
  curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    https://apt.llvm.org/llvm-snapshot.gpg.key -o "$RUNNER_TEMP/llvm-snapshot.key"
  expected='6084F3CF814B57C1CF12EFD515CF4D18AF4F7421'
  actual="$(gpg --show-keys --with-colons --fingerprint "$RUNNER_TEMP/llvm-snapshot.key" |
    awk -F: '$1 == "fpr" { print $10; exit }')"
  [[ "$actual" == "$expected" ]] || { echo 'LLVM apt signing key mismatch' >&2; exit 1; }
  gpg --dearmor --batch --yes -o "$RUNNER_TEMP/llvm-snapshot.gpg" "$RUNNER_TEMP/llvm-snapshot.key"
  sudo install -m 0644 "$RUNNER_TEMP/llvm-snapshot.gpg" /usr/share/keyrings/llvm-snapshot.gpg
  source_file=/etc/apt/sources.list.d/llvm-23.list
  echo 'deb [signed-by=/usr/share/keyrings/llvm-snapshot.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-23 main' |
    sudo tee "$source_file" >/dev/null
  sudo apt-get update -qq -o "Dir::Etc::sourcelist=$source_file" -o Dir::Etc::sourceparts=- \
    -o APT::Get::List-Cleanup=0 -o APT::Update::Error-Mode=any
  echo "PROVISION-METRIC stage=metadata seconds=$((SECONDS-start)) mode=$mode"
  # Only the named exact LLVM package closure may be installed or changed.
  # A new dependency or a changed runner baseline requires explicit review.
  plan="$(apt-get -s install --no-install-recommends --no-remove "${pins[@]}")"
  printf '%s\n' "$plan"
  while read -r action package rest; do
    [[ "$action" == Inst ]] || continue
    package="${package%:amd64}"
    allowed=false
    for candidate in "${packages[@]}"; do
      if [[ "$package" == "$candidate" ]]; then allowed=true; break; fi
    done
    [[ "$allowed" == true ]] || { echo "Unpinned package in installation plan: $package" >&2; exit 1; }
    [[ "$rest" == *"($version "* ]] || { echo "Unpinned version in plan: $package" >&2; exit 1; }
  done <<< "$plan"
  install_start=$SECONDS
  sudo apt-get install -y --no-install-recommends --no-remove "${pins[@]}"
  echo "PROVISION-METRIC stage=install seconds=$((SECONDS-install_start)) mode=$mode"
else
  echo 'PROVISION-METRIC stage=already-installed seconds=0'
fi
for package in "${packages[@]}"; do
  [[ "$(dpkg-query -W -f='${Status} ${Version}' "$package")" == "install ok installed $version" ]]
  echo "LLVM-INPUT $package=$version"
done
clang++-23 --version | grep -E 'clang version 23[.]1[.]6([[:space:]]|$)'
if [[ "$mode" == analysis ]]; then
  clang-tidy-23 --version | grep -E 'version 23[.]1[.]6([[:space:]]|$)'
fi
echo "PROVISION-METRIC stage=total seconds=$((SECONDS-start)) mode=$mode"
