# Define the addon directory, build directory, and zip file name
ADDON_DIR = plugin.video.mediafusion
REPO_DIR = repository.mediafusion
BUILD_DIR = build
DIST_DIR = dist
VERSION := $(shell python3 -c 'import xml.etree.ElementTree as ET; print(ET.parse("$(ADDON_DIR)/addon.xml").getroot().attrib.get("version",""))')
DEV_BUILD_TS := $(shell date +%y%m%d%H%M%S)
RANDOM_HASH := $(shell head /dev/urandom | tr -dc a-z0-9 | head -c 8)
NEW_VERSION := $(VERSION).$(DEV_BUILD_TS)
ZIP_FILE := $(ADDON_DIR)-$(VERSION).zip
DEV_ZIP_FILE := $(ADDON_DIR)-dev-$(NEW_VERSION)-$(RANDOM_HASH).zip
REPO_ZIP_FILE := $(REPO_DIR)-$(VERSION).zip

ifeq ($(strip $(VERSION)),)
$(error Failed to detect addon version from $(ADDON_DIR)/addon.xml)
endif

# Default target
all: $(ZIP_FILE) repository

# Create the plugin zip file
$(ZIP_FILE): install_dependencies
	@echo "Creating zip file for $(ADDON_DIR)..."
	@cd build && zip -r ../$(ZIP_FILE) $(ADDON_DIR)
	@echo "Zip file created: $(ZIP_FILE)"

# Install dependencies
install_dependencies: prepare_build
	@echo "Installing dependencies..."
	@pip3 install -r requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib

# Prepare build directory
prepare_build: clean
	@echo "Preparing build directory..."
	@mkdir -p $(BUILD_DIR)/$(ADDON_DIR)
	@mkdir -p $(BUILD_DIR)/$(REPO_DIR)
	@mkdir -p $(DIST_DIR)/$(ADDON_DIR)
	@mkdir -p $(DIST_DIR)/$(REPO_DIR)
	@cp -r $(ADDON_DIR)/* $(BUILD_DIR)/$(ADDON_DIR)

# Repository setup
repository: $(ZIP_FILE)
	@echo "Setting up repository..."
	@cp -r $(REPO_DIR)/* $(BUILD_DIR)/$(REPO_DIR)
	@cd $(BUILD_DIR) && zip -r ../$(REPO_ZIP_FILE) $(REPO_DIR)

	# Copy files to dist structure
	@cp $(ZIP_FILE) $(DIST_DIR)/$(ADDON_DIR)/
	@cp $(ADDON_DIR)/addon.xml $(DIST_DIR)/$(ADDON_DIR)/
	@cp $(REPO_ZIP_FILE) $(DIST_DIR)/$(REPO_DIR)/
	@cp $(REPO_DIR)/addon.xml $(DIST_DIR)/$(REPO_DIR)/
	@cp $(REPO_ZIP_FILE) $(DIST_DIR)/
	@rm -rf $(BUILD_DIR) $(ZIP_FILE) $(REPO_ZIP_FILE)

	# Create index.html
	@echo "<!DOCTYPE html>" > $(DIST_DIR)/index.html
	@echo "<a href=\"$(REPO_ZIP_FILE)\">$(REPO_ZIP_FILE)</a>" >> $(DIST_DIR)/index.html
	@echo "<a href=\"$(REPO_DIR)/$(REPO_ZIP_FILE)\">$(REPO_DIR)/$(REPO_ZIP_FILE)</a>" >> $(DIST_DIR)/index.html

	# Generate repository files
	@python3 generate_repository.py

# Clean up generated files
clean:
	@echo "Cleaning up..."
	@rm -rf $(ADDON_DIR)-*.zip build dist
	@rm -rf $(HOME)/Downloads/$(ADDON_DIR)-*.zip

# Dev target
dev: $(DEV_ZIP_FILE)

# Create the dev zip file
$(DEV_ZIP_FILE): install_dev_dependencies
	@echo "Stamping dev addon version: $(NEW_VERSION)"
	@python3 -c 'import pathlib,xml.etree.ElementTree as ET; p=pathlib.Path("$(BUILD_DIR)/$(ADDON_DIR)/addon.xml"); tree=ET.parse(p); root=tree.getroot(); assert root.attrib.get("id")=="plugin.video.mediafusion", "Unexpected addon id"; root.set("version","$(NEW_VERSION)"); tree.write(p, encoding="UTF-8", xml_declaration=True)'
	@echo "Creating dev zip file for $(ADDON_DIR)..."
	@cd build && zip -r ../$(DEV_ZIP_FILE) $(ADDON_DIR)
	@echo "Dev zip file created: $(DEV_ZIP_FILE)"
	@cp -f $(DEV_ZIP_FILE) $(HOME)/Downloads/$(DEV_ZIP_FILE)
	@echo "Dev zip file copied to $(HOME)/Downloads/$(DEV_ZIP_FILE)"

# Install dev dependencies
install_dev_dependencies: prepare_build
	@echo "Installing dev dependencies..."
	@pip3 install -r requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib
	@pip3 install -r dev-requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib

# Phony targets
.PHONY: all clean dev install_dependencies install_dev_dependencies repository prepare_build