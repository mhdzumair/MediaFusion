# Deployment Guide

This guide explains how to deploy your MediaFusion add-on repository.

## Option 1: GitHub Repository (Recommended)

### Step 1: Create GitHub Repository

1. Go to [GitHub](https://github.com/new)
2. Create a new repository:
   - Name: `haos-mediafusion-addon`
   - Description: "MediaFusion add-on for Home Assistant OS"
   - Public or Private (public recommended for sharing)
   - Don't initialize with README (we have one)

### Step 2: Push Add-on to GitHub

```bash
cd /home/user/mediafusion-local/haos-addon

# Initialize git repository
git init

# Add all files
git add .

# Create first commit
git commit -m "Initial MediaFusion HAOS add-on release"

# Add remote (replace YOUR-USERNAME)
git remote add origin https://github.com/YOUR-USERNAME/haos-mediafusion-addon.git

# Push to GitHub
git branch -M main
git push -u origin main
```

### Step 3: Add Icons (Optional but Recommended)

Home Assistant add-ons look better with icons. Create two images:

**icon.png** (96x96 pixels):
- Simple icon for the add-on
- Square, transparent background
- Represents MediaFusion

**logo.png** (750x200 pixels):
- Banner for the add-on store
- Can include text/branding

Place these in `/home/user/mediafusion-local/haos-addon/mediafusion/`:
```bash
# Download or create your icons
cp /path/to/your/icon.png haos-addon/mediafusion/icon.png
cp /path/to/your/logo.png haos-addon/mediafusion/logo.png

# Commit icons
git add mediafusion/icon.png mediafusion/logo.png
git commit -m "Add add-on icons"
git push
```

**Quick icon generation** (using ImageMagick):
```bash
# Simple gradient icon (96x96)
convert -size 96x96 gradient:blue-purple \
  -gravity center -pointsize 32 -fill white \
  -annotate +0+0 'MF' \
  haos-addon/mediafusion/icon.png

# Simple logo (750x200)
convert -size 750x200 gradient:blue-purple \
  -gravity center -pointsize 48 -fill white \
  -annotate +0+0 'MediaFusion for HAOS' \
  haos-addon/mediafusion/logo.png
```

### Step 4: Update Repository URL

Edit `haos-addon/repository.yaml`:
```yaml
url: https://github.com/YOUR-USERNAME/haos-mediafusion-addon
```

Edit `haos-addon/README.md` and update all instances of:
```
YOUR-USERNAME → your-actual-github-username
```

Commit changes:
```bash
git add .
git commit -m "Update repository URLs"
git push
```

### Step 5: Create GitHub Release (Optional)

1. Go to your repository on GitHub
2. Click **Releases** → **Create a new release**
3. Tag version: `v4.3.35`
4. Release title: `MediaFusion Add-on v4.3.35`
5. Description: Copy from CHANGELOG.md
6. Click **Publish release**

### Step 6: Add to Home Assistant

Now you can add your repository to HAOS:

1. Open Home Assistant
2. Go to **Settings** → **Add-ons** → **Add-on Store**
3. Click **⋮** → **Repositories**
4. Add: `https://github.com/YOUR-USERNAME/haos-mediafusion-addon`
5. Click **Add**
6. Refresh the add-on store
7. Find "MediaFusion" and install!

## Option 2: Local Add-on (Testing)

For testing before publishing to GitHub:

### Step 1: Copy to HAOS Add-ons Folder

**Method A: SSH/Terminal Access**

If you have SSH access to Home Assistant:

```bash
# From your MediaFusion repo
cd /home/user/mediafusion-local

# Copy to HAOS add-ons directory
scp -r haos-addon/mediafusion root@homeassistant.local:/addons/
```

**Method B: Samba Share**

1. Enable Samba add-on in Home Assistant
2. Connect to `\\homeassistant\addons` (Windows) or `smb://homeassistant/addons` (Mac)
3. Copy `haos-addon/mediafusion` folder to `addons/`

**Method C: File Editor Add-on**

1. Install File Editor add-on
2. Use it to create folder structure manually
3. Copy/paste file contents (tedious but works)

### Step 2: Reload Add-ons

1. Go to **Settings** → **Add-ons**
2. Click **⋮** → **Reload**
3. MediaFusion should appear under "Local add-ons"
4. Install and test

## Option 3: Build Local Docker Image

For advanced users who want to test the Docker image:

### Step 1: Build Image Locally

```bash
cd /home/user/mediafusion-local

# Build the add-on image
docker build \
  -f haos-addon/mediafusion/Dockerfile \
  -t local/addon-mediafusion:test \
  .
```

### Step 2: Test Image

```bash
# Run container for testing
docker run --rm \
  -p 8000:8000 \
  -e HOST_URL="http://localhost:8000" \
  -e SECRET_KEY="$(openssl rand -hex 16)" \
  -e POSTGRES_URI="postgresql+asyncpg://user:pass@localhost/db" \
  -e REDIS_URL="redis://localhost:6379" \
  local/addon-mediafusion:test
```

**Note:** This won't work fully without PostgreSQL and Redis running separately.

### Step 3: Full Stack with Docker Compose

For complete local testing:

```bash
# Use the existing docker-compose setup
cd deployment/docker-compose

# Modify docker-compose.yml to use local build
# Replace image: mhdzumair/mediafusion:4.3.35
# With build: ../../

docker-compose up
```

## Publishing to GitHub Container Registry

For custom builds with GitHub Actions:

### Step 1: Create GitHub Workflow

Create `.github/workflows/build.yml`:

```yaml
name: Build Add-on

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Build add-on
        uses: home-assistant/builder@master
        with:
          args: |
            --amd64 \
            --target mediafusion
```

### Step 2: Configure Secrets

1. Go to repository **Settings** → **Secrets**
2. Add `GHCR_TOKEN` (GitHub Personal Access Token with package write permissions)

### Step 3: Trigger Build

Push to main branch or create a tag:
```bash
git tag v4.3.35
git push origin v4.3.35
```

GitHub Actions will build and publish to GHCR.

## Updating the Add-on

When MediaFusion releases a new version:

### Step 1: Update Version

Edit `haos-addon/mediafusion/config.yaml`:
```yaml
version: "4.3.36"  # Update version
```

### Step 2: Update Changelog

Edit `haos-addon/mediafusion/CHANGELOG.md`:
```markdown
## Version 4.3.36 (2026-02-01)

### Changes
- Updated to MediaFusion 4.3.36
- Bug fixes and improvements
```

### Step 3: Rebuild and Test

```bash
# Build new image locally
docker build -f haos-addon/mediafusion/Dockerfile -t local/addon-mediafusion:4.3.36 .

# Test it works
```

### Step 4: Commit and Push

```bash
git add .
git commit -m "Update to MediaFusion v4.3.36"
git push

# Optionally create release tag
git tag v4.3.36
git push origin v4.3.36
```

Home Assistant users will see the update available.

## Troubleshooting

### Build fails: "unable to find package"

**Issue:** Alpine package not available

**Fix:** Update package name in Dockerfile, or pin to specific Alpine version

### Add-on doesn't appear in HAOS

**Check:**
1. Repository URL is correct in HAOS
2. Repository.yaml exists and is valid
3. config.yaml is valid YAML (check with yamllint)
4. Clicked "Reload" in add-on store

### Icon doesn't show

**Check:**
1. icon.png is 96x96 pixels
2. File is in correct location: `mediafusion/icon.png`
3. File is pushed to GitHub
4. Refreshed browser cache

### Build errors

**Check:**
1. All files copied correctly
2. File permissions (scripts should be executable)
3. No syntax errors in shell scripts
4. Paths in Dockerfile are correct

## Distribution

Once published to GitHub:

1. **Share repository URL:**
   ```
   https://github.com/YOUR-USERNAME/haos-mediafusion-addon
   ```

2. **Users add to HAOS:**
   - Settings → Add-ons → Repositories → Add URL

3. **They install MediaFusion:**
   - Add-on Store → MediaFusion → Install

4. **Keep updated:**
   - Push updates to GitHub
   - Users get update notifications in HAOS

## Legal Considerations

When publishing publicly:

1. **Include disclaimers** (already in README.md)
2. **Emphasize debrid-only use** (no torrenting)
3. **Recommend legal content sources**
4. **State educational purpose**
5. **Link to MediaFusion's license** (MIT)

## Support

When users report issues:

1. Ask for **logs** (redact sensitive info)
2. Check **HAOS version** compatibility
3. Verify **configuration** is correct
4. Test **locally** if possible
5. Report upstream issues to MediaFusion project

---

**Your add-on is ready to deploy!** 🚀

Choose your deployment method above and get started.
