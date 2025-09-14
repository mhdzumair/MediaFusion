#!/bin/bash

# MediaFusion Browser Extension - Universal Build Script
# This script builds the extension for all supported browsers

set -e  # Exit on any error

echo "🚀 MediaFusion Extension Universal Builder"
echo "=========================================="

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf dist/
mkdir -p dist

# Create base structure
echo "📁 Creating base structure..."
mkdir -p dist/firefox
mkdir -p dist/chrome
mkdir -p dist/edge

# Copy common files to all builds
copy_common_files() {
    local target_dir=$1
    echo "📋 Copying common files to $target_dir..."

    cp -r background "$target_dir/"
    cp -r content "$target_dir/"
    cp -r popup "$target_dir/"
    cp -r icons "$target_dir/"
}

# Build Firefox version
echo ""
echo "🦊 Building Firefox version..."
copy_common_files "dist/firefox"
cp manifest.json dist/firefox/manifest.json
echo "✅ Firefox build completed"

# Build Chrome version
echo ""
echo "🔵 Building Chrome version..."
copy_common_files "dist/chrome"
cp manifest_chrome.json dist/chrome/manifest.json
echo "✅ Chrome build completed"

# Build Edge version (same as Chrome)
echo ""
echo "🟦 Building Edge version..."
copy_common_files "dist/edge"
cp manifest_chrome.json dist/edge/manifest.json
echo "✅ Edge build completed"

# Create distribution packages
echo ""
echo "📦 Creating distribution packages..."

cd dist

# Firefox package
echo "🦊 Creating Firefox package..."
cd firefox
zip -r ../mediafusion-extension-firefox.zip . -x "*.DS_Store*" > /dev/null
cd ..

# Chrome package
echo "🔵 Creating Chrome package..."
cd chrome
zip -r ../mediafusion-extension-chrome.zip . -x "*.DS_Store*" > /dev/null
cd ..

# Edge package
echo "🟦 Creating Edge package..."
cd edge
zip -r ../mediafusion-extension-edge.zip . -x "*.DS_Store*" > /dev/null
cd ..

cd ..

echo ""
echo "✅ Build completed successfully!"
echo ""
echo "📁 Build artifacts:"
echo "   - dist/firefox/           (Firefox development files)"
echo "   - dist/chrome/            (Chrome development files)"
echo "   - dist/edge/              (Edge development files)"
echo "   - dist/mediafusion-extension-firefox.zip"
echo "   - dist/mediafusion-extension-chrome.zip"
echo "   - dist/mediafusion-extension-edge.zip"
echo ""
echo "🔧 Installation Instructions:"
echo ""
echo "Firefox (Recommended):"
echo "  Option 1: Visit https://addons.mozilla.org/en-US/firefox/addon/mediafusion-torrent-uploader/"
echo "  Option 2: Load dist/firefox/ as temporary add-on in about:debugging"
echo ""
echo "Chrome (Manual - Web Store pending):"
echo "  1. Go to chrome://extensions/"
echo "  2. Enable Developer mode"
echo "  3. Click 'Load unpacked' and select dist/chrome/"
echo ""
echo "Edge:"
echo "  1. Go to edge://extensions/"
echo "  2. Enable Developer mode"
echo "  3. Click 'Load unpacked' and select dist/edge/"
echo ""
