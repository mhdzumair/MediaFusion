#!/bin/bash

# MediaFusion Browser Extension - Firefox Installation Preparation

echo "🦊 Preparing MediaFusion Extension for Firefox..."
echo "================================================"

# Create a temporary Firefox directory
if [ -d "firefox-build" ]; then
    rm -rf firefox-build
fi

mkdir firefox-build
echo "✅ Created firefox-build directory"

# Copy all files except Chrome manifest
cp -r background firefox-build/
cp -r content firefox-build/
cp -r popup firefox-build/
cp -r styles firefox-build/
cp -r icons firefox-build/
cp README.md firefox-build/
cp INSTALLATION.md firefox-build/
echo "✅ Copied extension files"

# Use Firefox manifest
cp manifest.json firefox-build/manifest.json
echo "✅ Using Firefox manifest (Manifest V2)"

# Create Firefox-specific package
cd firefox-build
zip -r ../mediafusion-extension-firefox.zip . -x "*.DS_Store*"
cd ..

echo "✅ Created mediafusion-extension-firefox.zip"
echo ""
echo "🔧 Firefox Installation Steps:"
echo "1. Open Firefox and go to about:debugging"
echo "2. Click 'This Firefox' in the left sidebar"
echo "3. Click 'Load Temporary Add-on'"
echo "4. Navigate to the firefox-build folder"
echo "5. Select manifest.json"
echo ""
echo "📦 Or install the packaged version:"
echo "1. Go to about:addons"
echo "2. Click the gear icon"
echo "3. Select 'Install Add-on From File'"
echo "4. Choose mediafusion-extension-firefox.zip"
echo ""
echo "⚠️  Note: Temporary add-ons are removed when Firefox restarts"
echo "   For permanent installation, the extension needs to be signed by Mozilla"

