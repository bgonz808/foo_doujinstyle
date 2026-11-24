# Benchmark candidate only. Do not publish or consume as a qualified image.
FROM ubuntu@sha256:9a04f13b3f80ef056361e30868593b1cfd2279fe19e4081cb93a90bf56819d6a
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
# Bootstrap the CA package using the digest-pinned base's existing signed Ubuntu
# archive configuration. HTTPS verification stays enabled; the snapshot service
# redirects HTTP to HTTPS. Record the bootstrap package version in the inventory.
# The bootstrap version must be locked before promoting this experiment.
RUN apt-get update -o APT::Update::Error-Mode=any && \
    apt-get install -y --no-install-recommends --no-remove ca-certificates && \
    test -s /etc/ssl/certs/ca-certificates.crt && rm -rf /var/lib/apt/lists/*
# Ubuntu signed archive pinned by snapshot timestamp (full resolved inventory retained).
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* && \
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261123T000000Z/ noble main universe' \
    'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261123T000000Z/ noble-updates main universe' \
    'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261123T000000Z/ noble-security main universe' \
    > /etc/apt/sources.list.d/ubuntu-snapshot.list && \
    apt-get update -o APT::Update::Error-Mode=any && \
    apt-get install -y --no-install-recommends --no-remove ca-certificates curl gnupg git g++ binutils make pkg-config python3 python3-venv libssl-dev zlib1g-dev
# Signed LLVM repo and exact observed Debian build revision. A missing version
# fails instead of silently selecting a different compiler.
RUN curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 https://apt.llvm.org/llvm-snapshot.gpg.key -o /tmp/llvm.key && \
    test "$(gpg --show-keys --with-colons --fingerprint /tmp/llvm.key | awk -F: '$1 == "fpr" { print $10; exit }')" = 6084F3CF814B57C1CF12EFD515CF4D18AF4F7421 && \
    gpg --dearmor --batch --yes -o /usr/share/keyrings/llvm-snapshot.gpg /tmp/llvm.key && \
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/llvm-snapshot.gpg] https://apt.llvm.org/noble/ llvm-toolchain-noble-23 main' > /etc/apt/sources.list.d/llvm-23.list && \
    apt-get update -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/llvm-23.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 -o APT::Update::Error-Mode=any && \
    version='1:23.1.6~++20261116013850+5b10d2d74929-1~exp1~20261116133903.98' && \
    apt-get install -y --no-install-recommends --no-remove "clang-23=$version" "clang-tidy-23=$version" "clang-tools-23=$version" "libclang-common-23-dev=$version" "libclang-cpp23=$version" "libclang1-23=$version" "libllvm23=$version" "llvm-23-linker-tools=$version" && \
    clang++-23 --version | grep -E 'clang version 23[.]1[.]6([[:space:]]|$)' && \
    clang-tidy-23 --version | grep -E 'version 23[.]1[.]6([[:space:]]|$)' && \
    rm -f /tmp/llvm.key && rm -rf /var/lib/apt/lists/*
COPY requirements-portable.lock /tmp/requirements-portable.lock
RUN python3 -m venv /opt/ci-venv && \
    /opt/ci-venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements-portable.lock && \
    mkdir -p /opt/ci-evidence && \
    dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' | LC_ALL=C sort > /opt/ci-evidence/packages.tsv && \
    /opt/ci-venv/bin/python -m pip freeze --all > /opt/ci-evidence/python.txt && \
    sha256sum /usr/bin/clang-23 /usr/bin/clang++-23 /usr/bin/clang-tidy-23 /usr/bin/python3 > /opt/ci-evidence/tools.sha256 && \
    cp /tmp/requirements-portable.lock /opt/ci-evidence/requirements.lock && rm /tmp/requirements-portable.lock
ENV CC=clang-23 CXX=clang++-23 PYTHON=/opt/ci-venv/bin/python
WORKDIR /src
