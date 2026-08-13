# Use a lightweight Python base image
FROM python:3.10-slim-bullseye
# Install required system packages
# This includes networking tools, OpenVPN, WireGuard, and dependencies for N_m3
RUN apt-get update && apt-get install -y \
 wget \
 curl \
 iproute2 \
 iptables \
 openvpn \
 wireguard \
 openresolv \
 ffmpeg \
 && rm -rf /var/lib/apt/lists/*
# Install N_m3u8DL-RE (Update the URL to the latest release if necessary)
# This example downloads a common Linux binary. Adjust if your architecture is
RUN wget https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.2.0-beta/N
 && tar -xzf nm3u8.tar.gz \
 && mv N_m3u8DL-RE_Beta_linux-x64/N_m3u8DL-RE /usr/local/bin/ \
 && chmod +x /usr/local/bin/N_m3u8DL-RE \
 && rm -rf nm3u8.tar.gz N_m3u8DL-RE_Beta_linux-x64
# Set the working directory inside the container
WORKDIR /app
# Copy your Python requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy all your bot files into the container
COPY . .
# IMPORTANT: Create the download directory inside the container
RUN mkdir -p /root/drm/downloads
# Command to run your bot
CMD ["python", "bot.py"]
