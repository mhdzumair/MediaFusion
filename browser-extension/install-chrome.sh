#!/bin/bash

# MediaFusion Browser Extension - Chrome Installation Preparation

echo "🔧 Preparing MediaFusion Extension for Chrome..."
echo "==============================================="

# Create a temporary Chrome directory
if [ -d "chrome-build" ]; then
    rm -rf chrome-build
fi

mkdir chrome-build
echo "✅ Created chrome-build directory"

# Copy all files except Firefox manifest
cp -r background chrome-build/
cp -r content chrome-build/
cp -r popup chrome-build/
cp -r styles chrome-build/
cp -r icons chrome-build/
cp manifest_chrome.json chrome-build/manifest.json
cp README.md chrome-build/
cp INSTALLATION.md chrome-build/
echo "✅ Copied extension files"

# Create Chrome package
cd chrome-build
zip -r ../mediafusion-extension-chrome.zip . -x "*.DS_Store*"
cd ..

echo "✅ Created mediafusion-extension-chrome.zip"
echo ""
echo "🔧 Chrome Installation Options:"
echo ""
echo "Option 1: Developer Mode (if allowed by policy)"
echo "1. Open Chrome and go to chrome://extensions/"
echo "2. Enable 'Developer mode'"
echo "3. Click 'Load unpacked' and select chrome-build folder"
echo ""
echo "Option 2: Packaged Extension (bypasses some policies)"
echo "1. Extract mediafusion-extension-chrome.zip to a folder"
echo "2. Open Chrome and go to chrome://extensions/"
echo "3. Drag and drop the extracted folder onto the page"
echo ""
echo "Option 3: Enterprise Installation (if you have admin access)"
echo "1. Contact your IT administrator"
echo "2. Provide them with mediafusion-extension-chrome.zip"
echo "3. They can install it via Group Policy"
echo ""
echo "Option 4: Chrome Web Store (requires publishing)"
echo "1. The extension can be published to Chrome Web Store"
echo "2. This bypasses all enterprise policies"
echo "3. Contact MediaFusion developers for official publishing"
echo ""
echo "⚠️  If corporate policies block extension installation:"
echo "   - Try using Firefox or Edge instead"
echo "   - Use a personal Chrome profile"
echo "   - Contact IT for exception approval"

