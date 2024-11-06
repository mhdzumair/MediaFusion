# Define the addon directory, build directory, and zip file name
ADDON_DIR = plugin.video.mediafusion
REPO_DIR = repository.mediafusion
BUILD_DIR = build
DIST_DIR = dist
VERSION := $(shell sed -ne "s/.*version=\"\([0-9a-z\.\-]*\)\"\sname=.*/\1/p" $(ADDON_DIR)/addon.xml)
RANDOM_STRING = $(shell head /dev/urandom | tr -dc A-Za-z0-9 | head -c 8)
NEW_VERSION := $(VERSION)-$(RANDOM_STRING)
ZIP_FILE := $(ADDON_DIR)-$(VERSION).zip
DEV_ZIP_FILE := $(ADDON_DIR)-dev-$(NEW_VERSION).zip
REPO_ZIP_FILE := $(REPO_DIR)-$(VERSION).zip

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
	@pip install -r requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib

# Prepare build directory
prepare_build: clean
	@echo "Preparing build directory..."
	@mkdir -p $(BUILD_DIR)/$(ADDON_DIR)
	@mkdir -p $(BUILD_DIR)/$(REPO_DIR)
	@mkdir -p $(DIST_DIR)/zips/$(ADDON_DIR)
	@mkdir -p $(DIST_DIR)/zips/$(REPO_DIR)
	@cp -r $(ADDON_DIR)/* $(BUILD_DIR)/$(ADDON_DIR)

# Repository setup
repository: $(ZIP_FILE)
	@echo "Setting up repository..."
	@cp -r $(REPO_DIR)/* $(BUILD_DIR)/$(REPO_DIR)
	@cd $(BUILD_DIR) && zip -r ../$(REPO_ZIP_FILE) $(REPO_DIR)

	# Copy files to dist structure
	@cp $(ZIP_FILE) $(DIST_DIR)/zips/$(ADDON_DIR)/
	@cp $(ADDON_DIR)/addon.xml $(DIST_DIR)/zips/$(ADDON_DIR)/
	@cp $(REPO_ZIP_FILE) $(DIST_DIR)/zips/$(REPO_DIR)/
	@cp $(REPO_DIR)/addon.xml $(DIST_DIR)/zips/$(REPO_DIR)/

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
	@echo "Creating dev zip file for $(ADDON_DIR)..."
	@cd build && zip -r ../$(DEV_ZIP_FILE) $(ADDON_DIR)
	@echo "Dev zip file created: $(DEV_ZIP_FILE)"
	@cp -f $(DEV_ZIP_FILE) $(HOME)/Downloads/$(DEV_ZIP_FILE)
	@echo "Dev zip file copied to $(HOME)/Downloads/$(DEV_ZIP_FILE)"

# Install dev dependencies
install_dev_dependencies: prepare_build
	@echo "Installing dev dependencies..."
	@pip install -r requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib
	@pip install -r dev-requirements.txt -t $(BUILD_DIR)/$(ADDON_DIR)/lib

# Phony targets
.PHONY: all clean dev install_dependencies install_dev_dependencies repository prepare_build