#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

VPN_CONFIG="$1"
FAIL_CLOSED="$2"

bashio::log.info "Configuring WireGuard VPN..."

# Create WireGuard config directory
mkdir -p /etc/wireguard

# Write VPN configuration
if [ -z "$VPN_CONFIG" ]; then
    bashio::log.error "VPN enabled but no configuration provided!"
    exit 1
fi

# Decode base64 config if needed, or write directly
echo "$VPN_CONFIG" > /etc/wireguard/wg0.conf
chmod 600 /etc/wireguard/wg0.conf

# Validate config
if ! wg-quick up wg0; then
    bashio::log.error "Failed to start WireGuard VPN!"
    if [ "$FAIL_CLOSED" = "true" ]; then
        bashio::log.fatal "VPN fail-closed enabled - stopping add-on"
        exit 1
    fi
    bashio::log.warning "Continuing without VPN..."
    exit 0
fi

bashio::log.info "WireGuard VPN connected successfully"

# Get VPN interface IP
VPN_IP=$(ip -4 addr show wg0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
bashio::log.info "VPN IP: ${VPN_IP}"

# Setup routing - route only MediaFusion traffic through VPN
# Keep local network traffic (HA, NAS) on the default interface

# Get local network details
LOCAL_NETWORK=$(ip route | grep "src $(hostname -I | awk '{print $1}')" | awk '{print $1}')
DEFAULT_GW=$(ip route | grep default | awk '{print $3}')

bashio::log.info "Local network: ${LOCAL_NETWORK}"
bashio::log.info "Default gateway: ${DEFAULT_GW}"

# Create routing table for VPN
echo "200 vpn" >> /etc/iproute2/rt_tables

# Mark packets from MediaFusion app
iptables -t mangle -N MEDIAFUSION_MARK || true
iptables -t mangle -F MEDIAFUSION_MARK
iptables -t mangle -A OUTPUT -p tcp --dport 80 -j MEDIAFUSION_MARK
iptables -t mangle -A OUTPUT -p tcp --dport 443 -j MEDIAFUSION_MARK
iptables -t mangle -A MEDIAFUSION_MARK -j MARK --set-mark 200

# Route marked packets through VPN
ip rule add fwmark 200 table vpn || true
ip route add default dev wg0 table vpn

# Ensure local traffic stays local
ip rule add to ${LOCAL_NETWORK} lookup main priority 100 || true

# Kill switch if fail-closed is enabled
if [ "$FAIL_CLOSED" = "true" ]; then
    bashio::log.info "VPN kill switch enabled - all internet traffic will stop if VPN fails"

    # Drop all outbound internet traffic except:
    # 1. Local network
    # 2. VPN connection itself
    # 3. Traffic through VPN interface

    iptables -P OUTPUT DROP
    iptables -A OUTPUT -o lo -j ACCEPT
    iptables -A OUTPUT -o wg0 -j ACCEPT
    iptables -A OUTPUT -d ${LOCAL_NETWORK} -j ACCEPT
    iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

    # Allow VPN connection establishment
    VPN_SERVER=$(grep Endpoint /etc/wireguard/wg0.conf | awk -F'[ :]' '{print $3}')
    if [ -n "$VPN_SERVER" ]; then
        iptables -A OUTPUT -d ${VPN_SERVER} -j ACCEPT
    fi
fi

# Monitor VPN connection
(
    while true; do
        sleep 30
        if ! ip link show wg0 > /dev/null 2>&1; then
            bashio::log.error "VPN connection lost!"
            if [ "$FAIL_CLOSED" = "true" ]; then
                bashio::log.fatal "VPN fail-closed enabled - stopping services"
                pkill -9 gunicorn
                exit 1
            else
                bashio::log.warning "Attempting to reconnect VPN..."
                wg-quick down wg0 || true
                wg-quick up wg0 || bashio::log.error "VPN reconnection failed"
            fi
        fi
    done
) &

bashio::log.info "VPN setup complete"
