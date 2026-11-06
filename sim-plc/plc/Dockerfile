FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. Install build tools and dependencies for OpenPLC v3
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    pkg-config \
    bison \
    flex \
    autoconf \
    automake \
    libtool \
    python3 \
    python3-pip \
    sqlite3 \
    libsqlite3-dev \
    && rm -rf /var/lib/apt-get/lists/*

# 2. Clone the official OpenPLC v3 repository
RUN git clone https://github.com/thiagoralves/OpenPLC_v3 /workdir

WORKDIR /workdir

# 3. Run non-interactive installation for Linux
RUN ./install.sh linux

# 4. Copy the pump station Structured Text logic into OpenPLC
COPY pump_station.st /workdir/webserver/st_files/pump_station.st

EXPOSE 502 8080

CMD ["./start_openplc.sh"]