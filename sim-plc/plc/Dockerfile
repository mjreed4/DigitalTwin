FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 1. Install system tools & compilation libraries
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    sudo \
    git \
    bison \
    flex \
    autoconf \
    automake \
    libtool \
    python3 \
    python3-pip \
    python3-setuptools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workdir
RUN git clone https://github.com/thiagoralves/OpenPLC_v3 /workdir

# 2. Explicitly install webserver dependencies
RUN pip3 install flask flask-login pyserial pymodbus scapy

# 3. Run OpenPLC Linux installer
RUN ./install.sh linux

EXPOSE 502 8080

CMD ["./start_openplc.sh"]