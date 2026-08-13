#!/bin/bash
# Define the config file name here
VPN_CONFIG="my_vpn_config.ovpn"
echo "Starting VPN connection..."
if [[ "$VPN_CONFIG" == *.ovpn ]]; then
 # Start OpenVPN in the background
 openvpn --config "$VPN_CONFIG" --daemon
elif [[ "$VPN_CONFIG" == *.conf ]]; then
 # Start WireGuard
 wg-quick up "./$VPN_CONFIG"
else
 echo "No valid VPN config found. Running bot without VPN."
fi
# Wait a few seconds for the VPN interface (tun0 or wg0) to come up
sleep 10
echo "Checking IP address (Should be VPN IP):"
curl ifconfig.me
echo ""
echo "Starting Telegram Bot..."
python bot.py
