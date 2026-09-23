#!/usr/bin/env python3
"""
Скрипт для сборки и упаковки Chrome-расширения SunoSaver в .zip файл.
Готов для загрузки в Chrome Web Store или ручной установки.
"""
import json
import os
import zipfile

EXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunosaver-extension")
OUTPUT_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunosaver-chrome-extension.zip")

def package():
    manifest_path = os.path.join(EXT_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        print("❌ Error: manifest.json not found!")
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"📦 Packaging {manifest.get('name')} v{manifest.get('version')}...")

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(EXT_DIR):
            for file in files:
                if file.startswith("."):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, EXT_DIR)
                z.write(file_path, arcname)
                print(f"  + Added: {arcname}")

    size_kb = os.path.getsize(OUTPUT_ZIP) / 1024
    print(f"✅ Success! Created {OUTPUT_ZIP} ({size_kb:.1f} KB)")
    return True

if __name__ == "__main__":
    package()
