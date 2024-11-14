#!/usr/bin/env python3
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

DIST_DIR = Path("dist")


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
    errors = []
    # Get all addon.xml files from the zips directory
    for addon_dir in DIST_DIR.iterdir():
        if addon_dir.is_dir():
            addon_xml_path = addon_dir / "addon.xml"
            if addon_xml_path.exists():
                try:
                    tree = ET.parse(addon_xml_path)
                    root = tree.getroot()
                    # Basic validation
                    if root.tag != "addon":
                        raise ValueError(f"Invalid root tag in {addon_xml_path}")
                    addon_xmls.append(root)
                except (ET.ParseError, ValueError) as e:
                    errors.append(f"Error processing {addon_xml_path}: {e}")

    if errors:
        raise ValueError("\n".join(errors))

    # Sort addons by ID for consistent output
    addon_xmls.sort(key=lambda x: x.get("id", ""))

    # Create addons.xml
    xml_root = ET.Element("addons")
    for addon in addon_xmls:
        if addon.get("id").startswith("repository."):
            # append repository twice
            xml_root.insert(0, addon)
        xml_root.append(addon)

    # Use proper XML declaration with encoding
    xml_str = ET.tostring(xml_root, encoding="utf-8", xml_declaration=True)

    # Save addons.xml
    addons_xml_path = DIST_DIR / "addons.xml"
    with open(addons_xml_path, "wb") as f:
        f.write(xml_str)

    # Generate MD5
    md5_path = DIST_DIR / "addons.xml.md5"
    with open(md5_path, "w") as f:
        f.write(generate_md5(addons_xml_path))


if __name__ == "__main__":
    generate_addons_xml()
