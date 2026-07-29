# syntax=docker/dockerfile:1
FROM ubuntu:22.04

# Avoid interactive prompts during package installation.
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Build metadata.
LABEL maintainer="GhostBrowser Engineering"
LABEL description="Containerized build environment for the GhostBrowser custom Chromium engine."

# Install Chromium build dependencies and general tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    lsb-release \
    sudo \
    pkg-config \
    bison \
    flex \
    gperf \
    ninja-build \
    ccache \
    clang \
    lld \
    build-essential \
    libnss3-dev \
    libatk1.0-dev \
    libatk-bridge2.0-dev \
    libdrm-dev \
    libxcomposite-dev \
    libxdamage-dev \
    libxrandr-dev \
    libgbm-dev \
    libxkbcommon-dev \
    libasound2-dev \
    libcups2-dev \
    libdbus-1-dev \
    libglib2.0-dev \
    libgtk-3-dev \
    libnspr4-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libatspi2.0-dev \
    libpulse-dev \
    libpci-dev \
    && rm -rf /var/lib/apt/lists/*

# depot_tools location.
ENV DEPOT_TOOLS=/opt/depot_tools
ENV PATH="${DEPOT_TOOLS}:${PATH}"

# Clone depot_tools (shallow).
RUN git clone --depth=1 https://chromium.googlesource.com/chromium/tools/depot_tools.git "${DEPOT_TOOLS}"

# Create the working mount point for the Chromium source tree.
RUN mkdir -p /engine/src /engine/out

# Copy build orchestration into the image so the container can run standalone.
# The source tree itself should be mounted at /engine/src for persistence.
COPY build.py /engine/build.py
COPY patches /engine/patches
COPY gn /engine/gn

WORKDIR /engine

# Default entrypoint lists available options.
ENTRYPOINT ["python3", "/engine/build.py"]
CMD ["--help"]
