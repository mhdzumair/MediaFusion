# Kodi Integration

MediaFusion is available as a native Kodi video addon. It provides the same catalogs and streams as the Stremio addon.

## Method 1: Install via repository (recommended)

Installing via the MediaFusion repository enables automatic updates.

1. Launch Kodi → **Settings** (⚙️ gear icon) → **File manager**
2. Click **Add source** → click `<None>` and enter exactly:
   ```
   https://mhdzumair.github.io/MediaFusion
   ```
3. Name the source `MediaFusion` → **OK**
4. Return to the Kodi home screen
5. Click **Add-ons** → **Add-on browser** (box icon) → **Install from zip file**
6. Click **MediaFusion** → select the repository zip file (e.g. `repository.mediafusion-x.x.x.zip`)
7. Wait for the "MediaFusion Repository add-on installed" notification
8. Click **Install from repository** → **MediaFusion Repository**
   > If you see "Could not connect to repository", close and reopen Kodi, then return to this step.
9. Go to **Video add-ons** → **MediaFusion** → **Install**
10. Go to **Add-ons** → **My add-ons** → **Video add-ons** → **MediaFusion** → **Configure**

## Method 2: Manual installation

1. Download the latest `plugin.video.mediafusion.zip` from [GitHub Releases](https://github.com/mhdzumair/MediaFusion/releases)
2. Launch Kodi → **Add-ons** → **Add-on browser** → **Install from zip file**
3. Navigate to the downloaded zip and select it
4. Wait for the installation notification
5. Configure: **Add-ons** → **My add-ons** → **Video add-ons** → **MediaFusion** → **Configure**

---

## Configuring MediaFusion in Kodi

1. In Kodi, go to **Add-ons** → **My add-ons** → **Video add-ons** → **MediaFusion** → **Configure**
2. Select **Configure/Reconfigure Secret String**
3. A QR code and 6-digit key will be displayed
4. On your computer or phone, open the MediaFusion configure page:
   - Scan the QR code, or
   - Go to `https://mediafusion.elfhosted.com/configure` (or your self-hosted instance)
5. Configure your preferences (catalogs, provider, filters)
6. Click **Setup Kodi Addon**
7. Enter the 6-digit key shown in Kodi
   > Keys expire — if it expires, click **Configure Secret** in Kodi settings to refresh it.
8. Submit the configuration
9. Verify that the **secret string** value is populated in Kodi settings
10. Return to the main menu — your catalogs will appear

---

## Updating the addon

If installed via repository: Kodi will update MediaFusion automatically when new versions are released.

If installed manually: repeat the manual installation steps with the new zip file.
