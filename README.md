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

Put your NordVPN config file in the same directory as `bot.py`. For example:

```text
/root/drm/us1234.nordvpn.com.udp.ovpn
/root/drm/bot.py
```

You can also send the `.ovpn` or `.conf` file to the bot in a private chat; the owner account will get a confirmation when it is saved.

Simple Telegram owner commands:

```text
/connect
/vpnstatus
/disconnect
```

`/connect` starts the VPN and keeps it connected for the whole bot until you type `/disconnect`. `/connect` and `/vpnstatus` show the server parsed from the config file plus the current public IP/location/ISP so you can verify that the VPS traffic is going through the VPN.

## M3U8 quality picker

Add `--quality-select` to an `/m3u8` command to fetch the master playlist variants and show inline buttons for available qualities.
You can select multiple qualities before pressing **Start downloads**.

```text
/m3u8 https://example.com/master.m3u8 --save-name movie --quality-select
```
