# MediaFusion Quick Start Guide

Get MediaFusion running on Home Assistant OS in 10 minutes.

## Prerequisites Checklist

- [ ] Home Assistant OS installed and running
- [ ] Access to Home Assistant web UI
- [ ] Debrid service account (Real-Debrid/AllDebrid/Premiumize)
- [ ] Terminal/command line access (for generating secret key)

## 5-Step Installation

### 1. Add Repository (2 minutes)

1. Open Home Assistant → **Settings** → **Add-ons** → **Add-on Store**
2. Click **⋮** (three dots) → **Repositories**
3. Add: `https://github.com/YOUR-USERNAME/haos-mediafusion-addon`
4. Click **Add** and close

### 2. Generate Secret Key (1 minute)

On your computer, open terminal and run:

```bash
openssl rand -hex 16
```

**Copy the output** (32-character string like `3f8a9c7e2d1b6f4a8c9e7d5b3a1f8e6c`)

### 3. Install Add-on (5 minutes)

1. In Add-on Store, find **MediaFusion**
2. Click **INSTALL** (wait for completion)
3. Click **Configuration** tab
4. Set:
   ```yaml
   host_url: "http://homeassistant.local:8000"
   secret_key: "PASTE_YOUR_KEY_FROM_STEP_2"
   ```
5. Click **SAVE**

### 4. Start Add-on (1 minute)

1. Go to **Info** tab
2. Click **START**
3. Enable **Start on boot**
4. Enable **Watchdog**
5. Wait for startup (check **Log** tab)

### 5. Add to Stremio (1 minute)

1. Open Stremio on any device
2. Go to **⚙️ Settings** → **Addons**
3. Add manifest URL: `http://homeassistant.local:8000/manifest.json`
4. Click **Install**
5. Click **Configure** → Select your debrid service → Enter API key
6. **Done!** Start streaming

## Test It Works

1. In Stremio, search for a popular movie (e.g., "Inception")
2. Click on it
3. You should see "MediaFusion" streams
4. Click to play
5. 🎉 Success!

## Common Issues

**Can't access `http://homeassistant.local:8000`**
- Try your HAOS IP instead: `http://192.168.1.X:8000`
- Check port 8000 isn't blocked by firewall

**No streams appear in Stremio**
- Verify debrid API key is correct
- Check add-on is running (green in Info tab)
- Check logs for errors

**Add-on won't start**
- Verify `secret_key` is 32+ characters
- Check logs for specific error
- Try regenerating secret key

## Next Steps

### For Remote Access (Family Sharing)

Follow Cloudflare Tunnel guide in [DOCS.md](mediafusion/DOCS.md#cloudflare-tunnel-setup)

### For Extra Privacy (VPN)

Follow VPN configuration guide in [DOCS.md](mediafusion/DOCS.md#vpn-configuration)

### For Better Search Results

Install Prowlarr add-on and configure integration in [DOCS.md](mediafusion/DOCS.md#prowlarr-integration)

## Configuration Cheat Sheet

**Minimal (Local Only):**
```yaml
host_url: "http://homeassistant.local:8000"
secret_key: "YOUR_32_CHAR_KEY"
enable_vpn: false
cloudflare_tunnel_enabled: false
```

**With VPN:**
```yaml
host_url: "http://homeassistant.local:8000"
secret_key: "YOUR_32_CHAR_KEY"
enable_vpn: true
vpn_config: |
  [Interface]
  PrivateKey = YOUR_KEY
  Address = 10.64.0.2/32
  [Peer]
  PublicKey = SERVER_KEY
  Endpoint = vpn.server.com:51820
  AllowedIPs = 0.0.0.0/0
vpn_fail_closed: true
```

**With Cloudflare Tunnel (Remote):**
```yaml
host_url: "https://mediafusion.yourdomain.com"
secret_key: "YOUR_32_CHAR_KEY"
api_password: "family_access_password"
cloudflare_tunnel_enabled: true
cloudflare_tunnel_token: "YOUR_CF_TOKEN"
```

## Debrid Service Setup

### Real-Debrid (Recommended)

1. Sign up: https://real-debrid.com
2. Subscribe (~€16 for 6 months)
3. Get API token: https://real-debrid.com/apitoken
4. Add to Stremio → MediaFusion settings

### AllDebrid

1. Sign up: https://alldebrid.com
2. Subscribe (~€3/month)
3. Get API key: https://alldebrid.com/apikeys
4. Add to Stremio → MediaFusion settings

### Premiumize

1. Sign up: https://www.premiumize.me
2. Subscribe (~€10/month)
3. Get API key from dashboard
4. Add to Stremio → MediaFusion settings

## Family Usage Instructions

Send this to family members:

---

**How to use our MediaFusion streaming service:**

1. **Download Stremio** from https://www.stremio.com/downloads

2. **Create Stremio account** and verify email

3. **Add MediaFusion:**
   - Open Stremio
   - Click your profile → Addons
   - Add: `http://homeassistant.local:8000/manifest.json`
   - (If remote: `https://mediafusion.yourdomain.com/manifest.json`)

4. **Setup your debrid account:**
   - Get your own Real-Debrid account (€16/6 months)
   - In Stremio, configure MediaFusion with your API key

5. **Start watching:**
   - Search any movie/show
   - Click MediaFusion streams
   - Enjoy!

---

## Useful Commands

**Check add-on status:**
- Look at Info tab → should show green running state

**View logs:**
- Click Log tab in add-on

**Restart add-on:**
- Info tab → RESTART button

**Test API:**
```bash
curl http://homeassistant.local:8000/health
# Should return: {"status":"healthy"}
```

**Reset database (if corrupted):**
1. Stop add-on
2. Terminal: `rm -rf /data/postgres /data/redis`
3. Start add-on (will recreate)

## Resource Usage

**Typical:**
- Memory: 300-500MB
- CPU: 1-5%
- Disk: 200-650MB

**During searches:**
- Memory: 400-600MB
- CPU: 5-15%

**MacBook Air should handle this easily.**

## Security Tips

✅ **Do:**
- Use strong `secret_key` (32+ random characters)
- Set `api_password` for public instances
- Enable VPN for extra privacy
- Use Cloudflare Tunnel (not port forwarding)
- Keep add-on updated

❌ **Don't:**
- Share your `secret_key`
- Expose port 8000 to internet without Cloudflare
- Use weak passwords
- Share debrid API keys

## Legal & Privacy

- ✅ No torrenting from your IP (debrid-only)
- ✅ Private family use
- ⚠️ Check local laws regarding debrid services
- 🔒 VPN recommended for privacy
- 📝 Minimal logging by default

## Getting Help

1. **Check logs** - Most issues show errors in logs
2. **Read DOCS.md** - Comprehensive documentation
3. **Search issues** - GitHub repository issues tab
4. **Create issue** - Include logs (remove sensitive data!)

## Full Documentation

- **Installation Guide:** [INSTALL.md](mediafusion/INSTALL.md)
- **Configuration:** [DOCS.md](mediafusion/DOCS.md)
- **Deployment:** [DEPLOYMENT.md](DEPLOYMENT.md)
- **Changelog:** [CHANGELOG.md](mediafusion/CHANGELOG.md)

---

**Enjoy streaming with MediaFusion!** 🎬🍿

For questions: [GitHub Issues](https://github.com/YOUR-USERNAME/haos-mediafusion-addon/issues)
