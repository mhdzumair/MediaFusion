#!/bin/bash

# MediaFusion Browser Extension - Edge Installation Preparation

echo "🌐 Preparing MediaFusion Extension for Microsoft Edge..."
echo "======================================================="

# Create a temporary Edge directory
if [ -d "edge-build" ]; then
    rm -rf edge-build
fi

mkdir edge-build
echo "✅ Created edge-build directory"

# Copy all files (Edge uses Chrome manifest)
cp -r background edge-build/
cp -r content edge-build/
cp -r popup edge-build/
cp -r styles edge-build/
cp -r icons edge-build/
cp manifest_chrome.json edge-build/manifest.json
cp README.md edge-build/
cp INSTALLATION.md edge-build/
echo "✅ Copied extension files"

# Create Edge package
cd edge-build
zip -r ../mediafusion-extension-edge.zip . -x "*.DS_Store*"
cd ..

echo "✅ Created mediafusion-extension-edge.zip"
echo ""
echo "🔧 Microsoft Edge Installation Steps:"
echo ""
echo "Option 1: Developer Mode"
echo "1. Open Edge and go to edge://extensions/"
echo "2. Enable 'Developer mode' in the left sidebar"
echo "3. Click 'Load unpacked' and select edge-build folder"
echo ""
echo "Option 2: Packaged Installation"
echo "1. Extract mediafusion-extension-edge.zip to a folder"
echo "2. Open Edge and go to edge://extensions/"
echo "3. Drag and drop the extracted folder onto the page"
echo ""
echo "Option 3: Edge Add-ons Store (future)"
echo "1. The extension can be published to Edge Add-ons"
echo "2. This bypasses corporate policies"
echo ""
echo "💡 Edge often has more relaxed policies than Chrome"
echo "   Try Edge if Chrome installation is blocked"

