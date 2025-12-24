import json
import os
import struct
from pathlib import Path

from bin import xcappicon


def _write_png(path: Path, width: int, height: int) -> None:
    """Create a minimal PNG file of the given size for testing."""
    # PNG signature
    data = [b"\x89PNG\r\n\x1a\n"]

    # IHDR chunk
    ihdr_data = struct.pack(">II5B", width, height, 8, 6, 0, 0, 0)
    ihdr_len = struct.pack(">I", len(ihdr_data))
    ihdr_type = b"IHDR"
    ihdr_crc = struct.pack(">I", 0)  # CRC is ignored by our size reader
    data.append(ihdr_len + ihdr_type + ihdr_data + ihdr_crc)

    # IDAT chunk with empty data
    idat_data = b""
    idat_len = struct.pack(">I", len(idat_data))
    idat_type = b"IDAT"
    idat_crc = struct.pack(">I", 0)
    data.append(idat_len + idat_type + idat_data + idat_crc)

    # IEND chunk
    iend_len = struct.pack(">I", 0)
    iend_type = b"IEND"
    iend_crc = struct.pack(">I", 0)
    data.append(iend_len + iend_type + iend_crc)

    path.write_bytes(b"".join(data))


def test_builds_contents_json_with_basic_and_dark_icons(tmp_path):
    appicon_dir = tmp_path / "AppIcon.appiconset"
    appicon_dir.mkdir()

    # 1024x1024 -> ios-marketing 1024x1024 1x
    marketing = tmp_path / "icon-marketing.png"
    _write_png(marketing, 1024, 1024)

    # 60x60 -> iphone 20x20 3x, test dark appearance
    dark_icon = tmp_path / "icon-20pt-3x-dark.png"
    _write_png(dark_icon, 60, 60)

    exit_code = xcappicon.main(
        [
            str(appicon_dir),
            str(marketing),
            str(dark_icon),
            "--author",
            "com.example.app",
        ]
    )

    assert exit_code == 0

    contents_path = appicon_dir / "Contents.json"
    assert contents_path.is_file()

    contents = json.loads(contents_path.read_text(encoding="utf-8"))
    images = contents.get("images", [])

    # Expect at least two entries: ios-marketing and iphone dark
    ios_marketing = [
        img
        for img in images
        if img.get("idiom") == "ios-marketing"
        and img.get("size") == "1024x1024"
        and img.get("scale") == "1x"
    ]
    assert len(ios_marketing) == 1
    assert (appicon_dir / ios_marketing[0]["filename"]).is_file()

    dark_entries = [
        img
        for img in images
        if img.get("idiom") == "iphone"
        and img.get("size") == "20x20"
        and img.get("scale") == "3x"
    ]
    assert len(dark_entries) == 1

    dark_img = dark_entries[0]
    assert "appearances" in dark_img
    assert any(
        a.get("appearance") == "luminosity" and a.get("value") == "dark"
        for a in dark_img["appearances"]
    )
    assert (appicon_dir / dark_img["filename"]).is_file()

    # Check info block
    info = contents.get("info", {})
    assert info.get("author") == "com.example.app"
    assert info.get("version") == 1


