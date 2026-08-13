# Installation Guide

## Install the Python Library
Install the necessary library on your VPS:

```shell
pip install python-telegram-bot
```

## Install Google API Client Library

```shell
pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## Install ffmpeg and unzip

```shell
apt update
apt install ffmpeg unzip -y
```

## Download the latest N_m3u8DL-RE release
Go to the [releases page](https://github.com/nilaoda/N_m3u8DL-RE/releases) and find the latest version for linux-x64. Copy the link to the `.zip` file, then use `wget` to download it.

```shell
wget <LINK>
```

## Extract the .tar.gz file

```shell
tar -xzvf N_m3u8DL-RE_v0.3.0-beta_linux-x64_20241203.tar.gz
```

## Make the program executable

```shell
chmod +x N_m3u8DL-RE
```

## Copy it to your system PATH

```shell
cp N_m3u8DL-RE /usr/local/bin/
```

## Verify the installation

```shell
N_m3u8DL-RE --version
```

## Also need to get Shaka Packager
Go to the [releases page](https://github.com/shaka-project/shaka-packager/releases/) and find the latest version for linux-x64.

```shell
wget <LINK>
```

Need both `mpd_generator-linux-x64` and `packager-linux-x64`.

---

> **REMEMBER:** Keep every file in a DRM folder that you create.

## Keep bot running 24x7

### Start a new screen session

```shell
screen -S telebot
```

### Go to the DRM directory where bot.py exists

```shell
python3 bot.py
```

## VPN-routed downloads

The bot can route download subprocesses through a NordVPN config file.
OpenVPN `.ovpn` files use `openvpn --config <file> --daemon`; WireGuard `.conf` files use `wg-quick up <file>`.

Telegram owner commands:

```shell
/setvpn /path/to/nordvpn/config.ovpn
# Optional: pass a proxy URL if your VPN config exposes a local proxy for subprocesses
/setvpn /path/to/nordvpn/config.ovpn socks5://127.0.0.1:1080
/vpnstatus
/disablevpn
```

When VPN routing is enabled, each download starts the configured tunnel before `N_m3u8DL-RE` runs and stops it after the download finishes. If a proxy URL is configured, it is also exported to the downloader as `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY`.

## M3U8 quality picker

Add `--quality-select` to an `/m3u8` command to fetch the master playlist variants and show inline buttons for available qualities.
You can select multiple qualities before pressing **Start downloads**.

```text
/m3u8 https://example.com/master.m3u8 --save-name movie --quality-select
```
