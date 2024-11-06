#!/usr/bin/env python3
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

DIST_DIR = Path("dist")
ZIPS_DIR = DIST_DIR / "zips"


def generate_md5(file_path):
    """Generate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def generate_addons_xml():
    """Generate addons.xml file."""
    addon_xmls = []

    # Get all addon.xml files from the zips directory
    for addon_dir in ZIPS_DIR.iterdir():
        if addon_dir.is_dir():
            addon_xml_path = addon_dir / "addon.xml"
            if addon_xml_path.exists():
                tree = ET.parse(addon_xml_path)
                addon_xmls.append(tree.getroot())

    # Create addons.xml
    xml_root = ET.Element("addons")
    for addon in addon_xmls:
        xml_root.append(addon)

    # Convert to string
    xml_str = ET.tostring(xml_root, encoding="unicode")

    # Save addons.xml
    addons_xml_path = ZIPS_DIR / "addons.xml"
    with open(addons_xml_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        f.write(xml_str)

    # Generate MD5
    md5_path = ZIPS_DIR / "addons.xml.md5"
    with open(md5_path, "w") as f:
        f.write(generate_md5(addons_xml_path))


if __name__ == "__main__":
    generate_addons_xml()
