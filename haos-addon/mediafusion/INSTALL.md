# MediaFusion Add-on Installation Guide

Complete step-by-step guide to install and configure MediaFusion on Home Assistant OS.

## Prerequisites

Before you begin, ensure you have:

1. ✅ **Home Assistant OS** running on your Intel MacBook Air (amd64)
2. ✅ **Supervisor** access (check at Settings → System → Supervisor)
3. ✅ **SSH access** (optional, for advanced troubleshooting)
4. ✅ **Debrid service account** (Real-Debrid, AllDebrid, or Premiumize)
5. ✅ **Network access** to your Home Assistant instance

## Installation Steps

### Step 1: Add the Add-on Repository

1. Open **Home Assistant** in your browser
2. Navigate to **Settings** → **Add-ons**
3. Click **Add-on Store** (bottom right)
4. Click the **⋮** (three dots menu, top right)
5. Select **Repositories**
6. Add this repository URL:
   ```
   https://github.com/YOUR-USERNAME/haos-mediafusion-addon
   ```
7. Click **Add**
8. Click **Close**

The repository will refresh automatically.

### Step 2: Install MediaFusion Add-on

1. In the Add-on Store, refresh the page
2. Scroll down to find **MediaFusion**
3. Click on **MediaFusion**
4. Click **INSTALL**
5. Wait for installation to complete (may take 5-10 minutes)

### Step 3: Generate Required Keys

Before configuring, generate a secret key.

**On your computer** (Mac/Linux/Windows with Git Bash):

```bash
openssl rand -hex 16
```

This will output something like:
```
3f8a9c7e2d1b6f4a8c9e7d5b3a1f8e6c
```

**Copy this key** - you'll need it in the next step.

### Step 4: Basic Configuration

1. Go to the **Configuration** tab
2. Fill in the required fields:

```yaml
host_url: "http://homeassistant.local:8000"
secret_key: "YOUR_GENERATED_KEY_FROM_STEP_3"
api_password: ""  # Optional - leave empty for now
enable_vpn: false
cloudflare_tunnel_enabled: false
postgres_max_connections: 20
metadata_cache_ttl: 300
enable_prowlarr: false
log_level: "info"
```

3. Click **SAVE**

### Step 5: Start the Add-on

1. Go to the **Info** tab
2. Click **START**
3. Enable **Start on boot** (toggle switch)
4. Enable **Watchdog** (toggle switch)
5. Enable **Show in sidebar** (optional, for easy access)

### Step 6: Verify Installation

1. Go to the **Log** tab
2. Wait for the startup sequence to complete (30-60 seconds)
3. Look for this message:
   ```
   [INFO] Starting MediaFusion API on port 8000...
   [INFO] Access your MediaFusion instance at: http://homeassistant.local:8000
   ```

4. Open a browser and visit: `http://homeassistant.local:8000`
5. You should see the MediaFusion interface

✅ **Success!** MediaFusion is now running.

## Post-Installation Setup

### Option A: Local Use Only

If you only want to use MediaFusion on your home network:

1. **Configure Stremio on each device:**
   - Open Stremio
   - Go to Settings → Addons
   - Add manifest: `http://homeassistant.local:8000/manifest.json`
   - Configure your debrid service

2. **Share with family members on your network:**
   - Give them the URL: `http://homeassistant.local:8000`
   - Help them set up Stremio (see DOCS.md)

### Option B: Remote Access with Cloudflare Tunnel

If you want family members to access MediaFusion from anywhere:

#### 1. Create Cloudflare Tunnel

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Sign up or log in (free tier is fine)
3. Navigate to **Access** → **Tunnels**
4. Click **Create a tunnel**
5. Choose **Cloudflared**
6. Name it: `mediafusion`
7. Click **Save tunnel**
8. **Copy the token** (long string starting with `eyJ...`)

#### 2. Configure Public Hostname

1. In the tunnel configuration, click **Public Hostname**
2. Click **Add a public hostname**
3. Fill in:
   - **Subdomain**: `mediafusion` (or your choice)
   - **Domain**: Select your domain
   - **Service**:
     - Type: `HTTP`
     - URL: `localhost:8000`
4. Click **Save hostname**

Your public URL is now: `https://mediafusion.yourdomain.com`

#### 3. Update Add-on Configuration

1. Go back to MediaFusion add-on → **Configuration**
2. Update settings:
   ```yaml
   host_url: "https://mediafusion.yourdomain.com"
   cloudflare_tunnel_enabled: true
   cloudflare_tunnel_token: "YOUR_TUNNEL_TOKEN"
   api_password: "choose_a_strong_password"  # Highly recommended for public instances
   ```
3. Click **SAVE**
4. **Restart** the add-on

#### 4. Test Remote Access

1. From your phone (disable Wi-Fi to test cellular):
   - Visit `https://mediafusion.yourdomain.com`
   - You should see MediaFusion interface

2. Configure Stremio:
   - Use `https://mediafusion.yourdomain.com/manifest.json`
   - Enter API password if you set one

✅ **Success!** Your family can now access MediaFusion from anywhere.

### Option C: Add VPN for Extra Privacy

If you want to route MediaFusion traffic through a VPN:

#### 1. Get WireGuard Config

1. Sign up for a WireGuard VPN provider:
   - [Mullvad](https://mullvad.net/) (recommended, €5/month)
   - [IVPN](https://www.ivpn.net/)
   - [ProtonVPN](https://protonvpn.com/)

2. Download your WireGuard configuration file (`wg0.conf`)

3. Open it in a text editor - it looks like this:
   ```ini
   [Interface]
   PrivateKey = YOUR_PRIVATE_KEY
   Address = 10.64.0.2/32
   DNS = 1.1.1.1

   [Peer]
   PublicKey = SERVER_PUBLIC_KEY
   Endpoint = vpn.server.com:51820
   AllowedIPs = 0.0.0.0/0
   ```

#### 2. Configure VPN in Add-on

1. Go to MediaFusion add-on → **Configuration**
2. Update settings:
   ```yaml
   enable_vpn: true
   vpn_config: |
     [Interface]
     PrivateKey = YOUR_PRIVATE_KEY
     Address = 10.64.0.2/32
     DNS = 1.1.1.1

     [Peer]
     PublicKey = SERVER_PUBLIC_KEY
     Endpoint = vpn.server.com:51820
     AllowedIPs = 0.0.0.0/0
   vpn_fail_closed: true
   ```
3. Click **SAVE**
4. **Restart** the add-on

#### 3. Verify VPN Connection

1. Check the **Log** tab for:
   ```
   [INFO] WireGuard VPN connected successfully
   [INFO] VPN IP: 10.64.0.2
   ```

2. Test that Home Assistant is still accessible:
   - Local: `http://homeassistant.local:8123` ✅ Should work
   - NAS: `http://your-nas-ip` ✅ Should work

3. MediaFusion traffic now goes through VPN, but HA and NAS stay local!

✅ **Success!** VPN is active with split-tunneling.

## Debrid Service Setup

### Real-Debrid (Recommended)

1. Go to [Real-Debrid](https://real-debrid.com)
2. Create an account (~€16 for 6 months)
3. Log in and go to [API](https://real-debrid.com/apitoken)
4. Copy your API token
5. In Stremio:
   - Go to MediaFusion addon settings
   - Select "Real-Debrid"
   - Paste your API token
   - Click Save

### AllDebrid

1. Go to [AllDebrid](https://alldebrid.com)
2. Create an account (~€3/month)
3. Go to [API](https://alldebrid.com/apikeys/)
4. Generate and copy API key
5. Configure in Stremio (same as above)

### Premiumize

1. Go to [Premiumize](https://www.premiumize.me)
2. Create an account (~€10/month)
3. For OAuth setup, you need to configure in add-on:
   ```yaml
   premiumize_client_id: "your_client_id"
   premiumize_client_secret: "your_client_secret"
   ```
   (Get these from Premiumize dashboard → API)

## Stremio Setup for Family Members

Send these instructions to family:

---

### How to Use MediaFusion with Stremio

1. **Download Stremio**
   - Go to https://www.stremio.com/downloads
   - Install for your device (Windows/Mac/Linux/Android/iOS/TV)

2. **Create Stremio Account**
   - Open Stremio
   - Sign up with email
   - Verify your email

3. **Add MediaFusion**
   - Click your profile icon (top right)
   - Select "Addons"
   - Scroll to bottom → "Community Addons"
   - Click the URL bar
   - Enter: `https://mediafusion.yourdomain.com/manifest.json`
     (Or `http://homeassistant.local:8000/manifest.json` for local use)
   - Click **Install**

4. **Configure Debrid Service**
   - Click **Configure** on MediaFusion
   - Select your debrid service (Real-Debrid recommended)
   - Enter your API key
   - Click **Save**

5. **Start Watching**
   - Search for any movie or TV show
   - Streams labeled "MediaFusion" will appear
   - Click to play
   - Enjoy!

---

## Troubleshooting

### Add-on won't start

**Check logs for errors:**
```
Go to Log tab → Look for red [ERROR] messages
```

**Common fixes:**
- Ensure `secret_key` is set and 32+ characters
- Check port 8000 isn't used by another service
- Restart Home Assistant if needed

### Can't access MediaFusion UI

**Try different URLs:**
1. `http://homeassistant.local:8000`
2. `http://[YOUR_HAOS_IP]:8000` (e.g., `http://192.168.1.50:8000`)
3. Check firewall isn't blocking port 8000

### VPN not working

**Verify config:**
- Ensure WireGuard config is complete (all fields)
- Check VPN provider status
- Look for VPN errors in logs

**Test without VPN:**
- Set `enable_vpn: false` temporarily
- If it works, VPN config issue

### Cloudflare Tunnel issues

**Check tunnel status:**
- Go to Cloudflare dashboard → Tunnels
- Ensure tunnel shows "HEALTHY"
- Check logs for connection errors

**Common fixes:**
- Regenerate tunnel token
- Ensure MediaFusion is running before tunnel connects
- Check firewall allows outbound connections

### Slow performance

**Optimization:**
- Reduce `postgres_max_connections: 10`
- Lower `metadata_cache_ttl: 120`
- Check MacBook Air isn't thermal throttling
- Ensure good network connection

### Database errors

**Reset database** (⚠️ loses cache data):
```bash
# Stop add-on
# Access terminal: Settings → Add-ons → MediaFusion → Terminal
rm -rf /data/postgres
rm -rf /data/redis
# Start add-on
```

## Getting Help

If you need assistance:

1. **Check DOCS.md** - Comprehensive documentation
2. **Search issues** - GitHub repository issues tab
3. **Create issue** - Provide logs and configuration (remove sensitive info!)
4. **Home Assistant Community** - Post in add-ons forum

## Next Steps

Now that MediaFusion is running:

1. **Test streaming** - Search for a movie in Stremio
2. **Share with family** - Send them setup instructions
3. **Optimize settings** - Adjust cache and performance settings
4. **Enable VPN** (optional) - For extra privacy
5. **Setup Prowlarr** (optional) - For more indexers
6. **Monitor performance** - Check logs occasionally

**Enjoy your debrid-based streaming setup!** 🎬🍿
