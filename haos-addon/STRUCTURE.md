# Add-on Repository Structure

Complete file structure of the MediaFusion HAOS add-on repository.

## Directory Tree

```
haos-addon/
├── mediafusion/                    # Main add-on directory
│   ├── config.yaml                 # Add-on configuration (REQUIRED)
│   ├── build.yaml                  # Build configuration
│   ├── Dockerfile                  # Container definition (REQUIRED)
│   ├── README.md                   # Add-on description
│   ├── DOCS.md                     # Detailed documentation
│   ├── INSTALL.md                  # Installation guide
│   ├── CHANGELOG.md                # Version history
│   ├── config.example.yaml         # Example configuration
│   ├── icon.png                    # Add-on icon (96x96) - OPTIONAL
│   ├── logo.png                    # Add-on logo (750x200) - OPTIONAL
│   └── rootfs/                     # Container filesystem overlay
│       ├── run.sh                  # Main startup script
│       ├── vpn-setup.sh            # VPN configuration script
│       ├── cloudflare-setup.sh     # Cloudflare Tunnel script
│       └── healthcheck.sh          # Health check script
├── repository.yaml                 # Repository metadata (REQUIRED)
├── README.md                       # Repository documentation
├── DEPLOYMENT.md                   # Deployment instructions
├── QUICK_START.md                  # Quick start guide
├── STRUCTURE.md                    # This file
└── .gitignore                      # Git ignore rules
```

## File Descriptions

### Required Files

#### `mediafusion/config.yaml`
**Purpose:** Home Assistant add-on configuration
**Required:** YES
**Contains:**
- Add-on metadata (name, version, description)
- Architecture support (amd64)
- Port mappings
- Configuration schema
- Default options

#### `mediafusion/Dockerfile`
**Purpose:** Container build instructions
**Required:** YES
**Contains:**
- Base image selection
- System dependencies installation
- Python package installation
- Application code copying
- Script setup
- Health check definition

#### `mediafusion/rootfs/run.sh`
**Purpose:** Main entrypoint script
**Required:** YES
**Contains:**
- Configuration loading
- Service startup (PostgreSQL, Redis)
- VPN setup (if enabled)
- Cloudflare Tunnel setup (if enabled)
- Database migrations
- MediaFusion API startup

#### `repository.yaml`
**Purpose:** Add-on repository metadata
**Required:** YES (for repository)
**Contains:**
- Repository name
- Repository URL
- Maintainer info

### Documentation Files

#### `mediafusion/README.md`
**Purpose:** Add-on overview (shown in HAOS add-on store)
**Contains:**
- Brief description
- Key features
- Installation steps
- Quick start guide
- Legal notice

#### `mediafusion/DOCS.md`
**Purpose:** Comprehensive documentation (shown in HAOS docs tab)
**Contains:**
- All configuration options explained
- Cloudflare Tunnel setup guide
- VPN configuration guide
- Debrid service setup
- Family sharing instructions
- Prowlarr integration
- Performance optimization
- Troubleshooting
- Security & privacy information

#### `mediafusion/INSTALL.md`
**Purpose:** Step-by-step installation guide
**Contains:**
- Prerequisites
- Detailed installation steps
- Post-installation setup
- Configuration examples
- Troubleshooting

#### `mediafusion/CHANGELOG.md`
**Purpose:** Version history
**Contains:**
- Release notes
- Feature additions
- Bug fixes
- Breaking changes

#### `README.md` (repository root)
**Purpose:** Repository overview (shown on GitHub)
**Contains:**
- Repository description
- Available add-ons
- Installation instructions
- Features comparison
- Support information

#### `DEPLOYMENT.md`
**Purpose:** Deployment guide for developers
**Contains:**
- GitHub repository setup
- Local testing instructions
- Docker build process
- Publishing to GHCR
- Update procedures

#### `QUICK_START.md`
**Purpose:** Fast setup guide for users
**Contains:**
- 5-step installation
- Minimal configuration
- Common issues
- Configuration examples

### Support Scripts

#### `mediafusion/rootfs/vpn-setup.sh`
**Purpose:** WireGuard VPN configuration
**Executable:** YES
**Contains:**
- WireGuard interface setup
- Split-tunnel routing rules
- Fail-closed iptables rules
- VPN monitoring

#### `mediafusion/rootfs/cloudflare-setup.sh`
**Purpose:** Cloudflare Tunnel launcher
**Executable:** YES
**Contains:**
- cloudflared startup
- Tunnel token configuration
- Logging setup

#### `mediafusion/rootfs/healthcheck.sh`
**Purpose:** Container health check
**Executable:** YES
**Contains:**
- API availability test
- Health endpoint check

### Build Configuration

#### `mediafusion/build.yaml`
**Purpose:** Multi-architecture build settings
**Contains:**
- Base image definitions per architecture
- Build labels
- Build arguments

### Configuration Examples

#### `mediafusion/config.example.yaml`
**Purpose:** Example configurations for users
**Contains:**
- Commented configuration options
- Example values
- Multiple configuration scenarios

### Optional Assets

#### `mediafusion/icon.png`
**Purpose:** Add-on icon (shown in HAOS add-on list)
**Specifications:**
- Size: 96x96 pixels
- Format: PNG
- Transparent background recommended

#### `mediafusion/logo.png`
**Purpose:** Add-on logo (shown in HAOS add-on store)
**Specifications:**
- Size: 750x200 pixels
- Format: PNG
- Can include branding/text

### Git Configuration

#### `.gitignore`
**Purpose:** Exclude files from version control
**Contains:**
- Log files
- Secret files
- Test data
- IDE configs
- OS files

## File Permissions

All scripts in `rootfs/` must be executable:

```bash
chmod +x mediafusion/rootfs/*.sh
```

## File Sizes

Typical file sizes:

| File | Size |
|------|------|
| config.yaml | ~2 KB |
| Dockerfile | ~3 KB |
| run.sh | ~4 KB |
| README.md | ~5 KB |
| DOCS.md | ~30 KB |
| INSTALL.md | ~15 KB |
| icon.png | ~10 KB |
| logo.png | ~30 KB |

**Total repository size:** ~100-150 KB (without icons)

## Critical Files Checklist

Before deploying, ensure these files exist and are valid:

- [ ] `mediafusion/config.yaml` - Valid YAML, correct version
- [ ] `mediafusion/Dockerfile` - No syntax errors, correct paths
- [ ] `mediafusion/rootfs/run.sh` - Executable, correct bashio syntax
- [ ] `repository.yaml` - Correct repository URL
- [ ] `mediafusion/README.md` - Updated with your info
- [ ] All scripts are executable (`chmod +x`)
- [ ] No sensitive data in committed files

## Customization Points

Files you should customize:

1. **repository.yaml**
   - Update `url` with your GitHub repo

2. **All README/DOCS files**
   - Replace `YOUR-USERNAME` with actual username
   - Update domain examples if needed

3. **mediafusion/config.yaml**
   - Adjust default options if needed
   - Update image URL if using custom registry

4. **Icons** (optional but recommended)
   - Add custom icon.png (96x96)
   - Add custom logo.png (750x200)

## Testing Checklist

Before publishing:

- [ ] All YAML files validate (use yamllint)
- [ ] All shell scripts have correct syntax (use shellcheck)
- [ ] Dockerfile builds successfully
- [ ] All URLs are updated (no YOUR-USERNAME)
- [ ] Documentation is clear and accurate
- [ ] Scripts are executable
- [ ] .gitignore excludes sensitive files

## Directory Creation

To create this structure from scratch:

```bash
cd /home/user/mediafusion-local

# Create directories
mkdir -p haos-addon/mediafusion/rootfs

# All files should already be created if you followed this guide
```

## Maintenance

Files to update when MediaFusion releases new version:

1. `mediafusion/config.yaml` - Update version number
2. `mediafusion/CHANGELOG.md` - Add new version entry
3. `mediafusion/Dockerfile` - Update dependencies if needed
4. Test thoroughly before publishing

## Additional Notes

### Why this structure?

This follows Home Assistant's official add-on structure:
- `config.yaml` - Required by HAOS
- `Dockerfile` - Standard container build
- `rootfs/` - Files copied to container root
- Executable scripts in `rootfs/`

### Bashio

Scripts use `bashio` library for HAOS integration:
- `bashio::config` - Read add-on configuration
- `bashio::log.info` - Structured logging
- `bashio::log.error` - Error logging
- Available in Home Assistant base images

### Best Practices

✅ **Do:**
- Keep scripts simple and readable
- Comment complex logic
- Use bashio for logging
- Validate all inputs
- Handle errors gracefully

❌ **Don't:**
- Hardcode secrets
- Skip error checking
- Use complex nested scripts
- Commit sensitive data

---

This structure provides a complete, production-ready Home Assistant add-on for MediaFusion.
