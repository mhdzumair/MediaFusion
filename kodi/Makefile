# Define the addon directory, build directory, and zip file name
ADDON_DIR = plugin.video.mediafusion
BUILD_DIR = build/$(ADDON_DIR)
LIB_DIR = $(BUILD_DIR)/lib
VERSION := $(shell sed -ne "s/.*version=\"\([0-9a-z\.\-]*\)\"\sname=.*/\1/p" $(ADDON_DIR)/addon.xml)
RANDOM_STRING = $(shell head /dev/urandom | tr -dc A-Za-z0-9 | head -c 8)
NEW_VERSION := $(VERSION)-$(RANDOM_STRING)
ZIP_FILE := $(ADDON_DIR)-$(VERSION).zip
DEV_ZIP_FILE := $(ADDON_DIR)-dev-$(NEW_VERSION).zip

# Default target
all: $(ZIP_FILE)

# Create the zip file
$(ZIP_FILE): install_dependencies
	@echo "Creating zip file for $(ADDON_DIR)..."
	@cd build && zip -r ../$(ZIP_FILE) $(ADDON_DIR)
	@echo "Zip file created: $(ZIP_FILE)"

# Install dependencies
install_dependencies: $(BUILD_DIR)
	@echo "Installing dependencies..."
	@pip install -r requirements.txt -t $(LIB_DIR)

# Prepare build directory
$(BUILD_DIR): clean
	@echo "Preparing build directory..."
	@mkdir -p $(BUILD_DIR)
	@cp -r $(ADDON_DIR)/* $(BUILD_DIR)

# Clean up generated files
clean:
	@echo "Cleaning up..."
	@rm -rf $(ADDON_DIR)-*.zip build
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
install_dev_dependencies: $(BUILD_DIR)
	@echo "Installing dev dependencies..."
	@pip install -r requirements.txt -t $(LIB_DIR)
	@pip install -r dev-requirements.txt -t $(LIB_DIR)

# Phony targets
.PHONY: all clean dev install_dependencies install_dev_dependencies