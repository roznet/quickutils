#!/usr/bin/env python3
#
#  MIT Licence
#
#  Copyright (c) 2025.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#  
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.
#  

"""
Utility to build or update an AppIcon.appiconset Contents.json from PNG files.

Two modes:

* If a ``Contents.json`` already exists, the file is loaded and preserved.
  Only the ``filename`` fields of matching image entries are updated to point
  to the supplied PNGs; other tags are left untouched.
* If there is no ``Contents.json``, one is generated from the PNG sizes using
  Apple's App Icon type documentation
  (see “App Icon Type” in the Asset Catalog Format Reference:
  https://developer.apple.com/library/archive/documentation/Xcode/Reference/xcode_ref-Asset_Catalog_Format/AppIconType.html).

In both cases, simple patterns in the input filenames like ``dark`` or
``light`` are used to infer the luminosity appearance when possible.
"""

import argparse
import json
import logging
import os
import shutil
import struct
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


# Mapping from (width, height) in pixels to one or more AppIcon image entries.
# This covers common iOS / iPadOS sizes plus the 1024x1024 App Store icon.
#
# Each entry is a dict with keys: idiom, size, scale.
PIXEL_SIZE_MAP: Dict[Tuple[int, int], List[Dict[str, str]]] = {
    # iPhone
    (40, 40): [
        {"idiom": "iphone", "size": "20x20", "scale": "2x"},
        {"idiom": "ipad", "size": "20x20", "scale": "2x"},
    ],
    (60, 60): [{"idiom": "iphone", "size": "20x20", "scale": "3x"}],
    (58, 58): [
        {"idiom": "iphone", "size": "29x29", "scale": "2x"},
        {"idiom": "ipad", "size": "29x29", "scale": "2x"},
    ],
    (87, 87): [{"idiom": "iphone", "size": "29x29", "scale": "3x"}],
    (80, 80): [
        {"idiom": "iphone", "size": "40x40", "scale": "2x"},
        {"idiom": "ipad", "size": "40x40", "scale": "2x"},
    ],
    (120, 120): [
        {"idiom": "iphone", "size": "40x40", "scale": "3x"},
        {"idiom": "iphone", "size": "60x60", "scale": "2x"},
        {"idiom": "ipad", "size": "40x40", "scale": "3x"},
    ],
    (180, 180): [{"idiom": "iphone", "size": "60x60", "scale": "3x"}],
    # iPad
    (20, 20): [{"idiom": "ipad", "size": "20x20", "scale": "1x"}],
    (29, 29): [{"idiom": "ipad", "size": "29x29", "scale": "1x"}],
    (40, 40): [
        {"idiom": "iphone", "size": "20x20", "scale": "2x"},
        {"idiom": "ipad", "size": "20x20", "scale": "2x"},
    ],
    (76, 76): [{"idiom": "ipad", "size": "76x76", "scale": "1x"}],
    (152, 152): [{"idiom": "ipad", "size": "76x76", "scale": "2x"}],
    (167, 167): [{"idiom": "ipad", "size": "83.5x83.5", "scale": "2x"}],
    # App Store
    (1024, 1024): [{"idiom": "ios-marketing", "size": "1024x1024", "scale": "1x"}],
}


logger = logging.getLogger(__name__)


@dataclass
class PNGImage:
    """Model for a PNG image file with its detected attributes."""
    path: str
    width: int
    height: int
    appearance: Optional[str]  # "dark", "light", "tinted", or None
    is_clear: bool  # True if "clear" keyword found in filename
    
    @property
    def pixel_size(self) -> Tuple[int, int]:
        return (self.width, self.height)
    
    @property
    def basename(self) -> str:
        return os.path.basename(self.path)


@dataclass
class ContentsEntry:
    """Model for a Contents.json image entry."""
    entry_dict: Dict[str, object]  # The original dict from Contents.json
    expected_pixel_size: Optional[Tuple[int, int]]  # Computed from size and scale
    required_appearance: Optional[str]  # "dark", "light", "tinted", or None if not required
    
    @property
    def has_filename(self) -> bool:
        return bool(self.entry_dict.get("filename"))
    
    def set_filename(self, filename: str) -> None:
        """Set the filename in the entry dict."""
        self.entry_dict["filename"] = filename


def detect_appearance(name: str) -> Optional[str]:
    """Detect appearance (light/dark/tinted) from filename.

    Returns ``"dark"``, ``"light"``, or ``"tinted"`` if matched, otherwise None.
    """
    lower = name.lower()
    if "tinted" in lower:
        return "tinted"
    if "dark" in lower:
        return "dark"
    if "light" in lower:
        return "light"
    return None


def detect_clear(name: str) -> bool:
    """Detect if 'clear' keyword is present in filename."""
    return "clear" in name.lower()


def get_png_size(path: str) -> Tuple[int, int]:
    """Return (width, height) for a PNG file without external dependencies.

    The function reads the PNG signature and IHDR chunk header.
    """
    logger.debug("Reading PNG header for %s", path)
    with open(path, "rb") as f:
        header = f.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a valid PNG file")
    # IHDR chunk: 4 bytes length, 4 bytes type, then 4 bytes width, 4 bytes height
    # We already read 8 (signature) + 8 (len+type) = 16 bytes, so bytes 16:24
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def detect_appearance(name: str) -> Optional[str]:
    """Detect appearance (light/dark/tinted) from filename.

    Returns ``"dark"``, ``"light"``, or ``"tinted"`` if matched, otherwise None.
    """
    lower = name.lower()
    if "tinted" in lower:
        return "tinted"
    if "dark" in lower:
        return "dark"
    if "light" in lower:
        return "light"
    return None


def build_image_entries_from_scratch(
    png_paths: Iterable[str], appicon_dir: str, dry_run: bool = False
) -> List[Dict[str, object]]:
    """Build a new list of image entries for Contents.json and copy PNGs.

    This is used only when no Contents.json exists yet.
    """
    logger.info("Building AppIcon entries in %s from scratch", appicon_dir)
    if not dry_run:
        os.makedirs(appicon_dir, exist_ok=True)
    images: List[Dict[str, object]] = []

    # Counter to avoid filename collisions per (idiom, size, scale, appearance)
    counters: Dict[Tuple[str, str, str, Optional[str]], int] = {}

    for src in png_paths:
        if not os.path.isfile(src):
            logger.error("Input PNG not found: %s", os.path.abspath(src))
            # Skip missing files but continue processing others
            continue
        
        width, height = get_png_size(src)
        key = (width, height)
        slots = PIXEL_SIZE_MAP.get(key)
        if not slots:
            filename_only = os.path.basename(src)
            logger.warning(
                "No AppIcon slot mapping for %s (%dx%d), skipping", filename_only, width, height
            )
            continue

        base_name = os.path.splitext(os.path.basename(src))[0]
        appearance = detect_appearance(base_name)
        idiom_hint = _detect_idiom_hint(base_name)
        
        # Build attributes string for logging
        attrs = []
        if appearance:
            attrs.append(f"appearance={appearance}")
        if idiom_hint:
            attrs.append(f"idiom_hint={idiom_hint}")
        attrs_str = ", ".join(attrs) if attrs else "no attributes"
        
        filename_only = os.path.basename(src)
        logger.info("Processing PNG %s (%dx%d, %s)", filename_only, width, height, attrs_str)

        for slot in slots:
            idiom = slot["idiom"]
            size = slot["size"]
            scale = slot["scale"]
            logger.info("  -> Mapped to slot: idiom=%s, size=%s, scale=%s", idiom, size, scale)
            counter_key = (idiom, size, scale, appearance)
            counters[counter_key] = counters.get(counter_key, 0) + 1
            idx = counters[counter_key]

            appearance_suffix = f"-{appearance}" if appearance else ""
            index_suffix = f"-{idx}" if idx > 1 else ""
            filename = (
                f"AppIcon-{idiom}-{size}@{scale}{appearance_suffix}{index_suffix}.png"
            )

            dst = os.path.join(appicon_dir, filename)
            if dry_run:
                logger.info("  -> [DRY RUN] Would copy %s -> %s", src, dst)
            else:
                logger.debug("Copying %s -> %s", src, dst)
                shutil.copy2(src, dst)

            entry: Dict[str, object] = {
                "idiom": idiom,
                "size": size,
                "scale": scale,
                "filename": filename,
            }
            if appearance:
                # Follow Image Set / App Icon appearances convention:
                # [{ "appearance": "luminosity", "value": "dark"|"light" }]
                entry["appearances"] = [
                    {"appearance": "luminosity", "value": appearance}
                ]
            images.append(entry)

    return images


def load_png_images(png_paths: Iterable[str]) -> List[PNGImage]:
    """Load all PNG files and create PNGImage models."""
    png_images: List[PNGImage] = []
    
    for src in png_paths:
        if not os.path.isfile(src):
            logger.error("Input PNG not found: %s", os.path.abspath(src))
            continue
        
        try:
            width, height = get_png_size(src)
            base_name = os.path.splitext(os.path.basename(src))[0]
            appearance = detect_appearance(base_name)
            is_clear = detect_clear(base_name)
            
            png_images.append(PNGImage(
                path=src,
                width=width,
                height=height,
                appearance=appearance,
                is_clear=is_clear
            ))
        except Exception as e:
            logger.error("Failed to process PNG %s: %s", src, e)
            continue
    
    return png_images


def load_contents_entries(contents: Dict[str, object]) -> List[ContentsEntry]:
    """Load all image entries from Contents.json and create ContentsEntry models."""
    images = contents.get("images") or []
    if not isinstance(images, list):
        logger.error("Contents.json has invalid images array")
        return []
    
    entries: List[ContentsEntry] = []
    
    for img in images:
        if not isinstance(img, dict):
            continue
        
        # Compute expected pixel size
        size_str = img.get("size")
        if not isinstance(size_str, str):
            continue
        
        scale_str = img.get("scale")
        if not isinstance(scale_str, str):
            scale_str = "1x"  # Default to 1x if missing
        
        try:
            w_str, h_str = size_str.split("x")
            w_pt = float(w_str)
            h_pt = float(h_str)
            if not scale_str.endswith("x"):
                continue
            factor = float(scale_str[:-1])
            expected_size = (int(w_pt * factor), int(h_pt * factor))
        except Exception:
            logger.debug("Could not parse size/scale for entry %r", img)
            continue
        
        # Extract required appearance
        appearances = img.get("appearances")
        required_appearance = None
        if isinstance(appearances, list):
            for app in appearances:
                if (
                    isinstance(app, dict)
                    and app.get("appearance") == "luminosity"
                    and isinstance(app.get("value"), str)
                ):
                    required_appearance = app["value"]
                    break
        
        entries.append(ContentsEntry(
            entry_dict=img,
            expected_pixel_size=expected_size,
            required_appearance=required_appearance
        ))
    
    return entries


def _detect_idiom_hint(name: str) -> Optional[str]:
    """Guess idiom from filename."""
    lower = name.lower()
    if "iphone" in lower:
        return "iphone"
    if "ipad" in lower:
        return "ipad"
    if "mac" in lower:
        return "mac"
    if "watch" in lower:
        return "watch"
    if "car" in lower or "carplay" in lower:
        return "car"
    return None


def assign_pngs_to_existing_contents(
    png_paths: Iterable[str], contents: Dict[str, object], appicon_dir: str, dry_run: bool = False
) -> int:
    """Assign PNGs to existing Contents.json image entries using smart matching.

    Matching algorithm:
    1. Find all entries matching the required pixel size
    2. If entry requires a specific appearance (dark/light/tinted), only match PNGs with that appearance
    3. If entry has no appearance requirement, prefer PNGs with no appearance (default) over specialized ones
    4. Only assign to entries that don't already have a filename

    Returns the number of image entries that were updated with a filename.
    """
    if not dry_run:
        os.makedirs(appicon_dir, exist_ok=True)
    
    # Load all PNG images into models
    png_images = load_png_images(png_paths)
    if not png_images:
        logger.warning("No valid PNG images found")
        return 0
    
    # Load all Contents.json entries into models
    contents_entries = load_contents_entries(contents)
    if not contents_entries:
        logger.warning("No valid entries found in Contents.json")
        return 0
    
    updated = 0
    
    # Process each entry that needs a filename
    for entry in contents_entries:
        if entry.has_filename:
            continue  # Skip entries that already have a filename
        
        if entry.expected_pixel_size is None:
            continue  # Skip entries with invalid size/scale
        
        # Find all PNGs matching the required pixel size
        size_matches = [
            png for png in png_images
            if png.pixel_size == entry.expected_pixel_size
        ]
        
        if not size_matches:
            logger.debug(
                "No PNG found for entry size %dx%d (required appearance: %s)",
                entry.expected_pixel_size[0],
                entry.expected_pixel_size[1],
                entry.required_appearance or "none"
            )
            continue
        
        # Filter by appearance requirement
        candidates: List[PNGImage] = []
        
        if entry.required_appearance:
            # Entry requires a specific appearance - only match PNGs with that appearance
            candidates = [
                png for png in size_matches
                if png.appearance == entry.required_appearance
            ]
        else:
            # Entry has no appearance requirement - prefer PNGs with no appearance
            # (default images) over specialized ones (dark/light/tinted)
            default_images = [png for png in size_matches if png.appearance is None]
            if default_images:
                candidates = default_images
            else:
                # If no default available, use any matching size (fallback)
                candidates = size_matches
        
        if not candidates:
            logger.warning(
                "No matching PNG found for entry (size=%dx%d, required_appearance=%s)",
                entry.expected_pixel_size[0],
                entry.expected_pixel_size[1],
                entry.required_appearance or "none"
            )
            continue
        
        # Prefer non-clear images if both clear and non-clear are available
        non_clear = [png for png in candidates if not png.is_clear]
        if non_clear:
            chosen = non_clear[0]
        else:
            chosen = candidates[0]
        
        # Log the match
        attrs = []
        if chosen.appearance:
            attrs.append(f"appearance={chosen.appearance}")
        if chosen.is_clear:
            attrs.append("clear")
        attrs_str = ", ".join(attrs) if attrs else "default"
        
        logger.info(
            "Processing PNG %s (%dx%d, %s)",
            chosen.basename,
            chosen.width,
            chosen.height,
            attrs_str
        )
        
        # Build match description
        match_parts = []
        if entry.entry_dict.get("idiom"):
            match_parts.append(f"idiom={entry.entry_dict['idiom']}")
        if entry.entry_dict.get("platform"):
            match_parts.append(f"platform={entry.entry_dict['platform']}")
        if entry.entry_dict.get("size"):
            match_parts.append(f"size={entry.entry_dict['size']}")
        if entry.entry_dict.get("scale"):
            match_parts.append(f"scale={entry.entry_dict['scale']}")
        if entry.required_appearance:
            match_parts.append(f"appearance={entry.required_appearance}")
        match_str = ", ".join(match_parts)
        logger.info("  -> Matched to entry: %s", match_str)
        
        # Copy PNG and set filename
        dst_name = chosen.basename
        dst = os.path.join(appicon_dir, dst_name)
        if dry_run:
            logger.info("  -> [DRY RUN] Would copy %s -> %s", chosen.path, dst)
            logger.info("  -> [DRY RUN] Would set filename: %s", dst_name)
        else:
            logger.debug("Copying %s -> %s", chosen.path, dst)
            shutil.copy2(chosen.path, dst)
            entry.set_filename(dst_name)
        updated += 1
    
    return updated


def write_contents_json(appicon_dir: str, contents: Dict[str, object]) -> None:
    """Write the Contents.json dict to ``appicon_dir``."""
    images = contents.get("images") or []
    logger.info(
        "Writing Contents.json to %s with %d image entries", appicon_dir, len(images)
    )
    contents_path = os.path.join(appicon_dir, "Contents.json")
    with open(contents_path, "w", encoding="utf-8") as f:
        json.dump(contents, f, indent=2, sort_keys=False)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an AppIcon.appiconset folder and Contents.json from PNG files.\n\n"
            "Example:\n"
            "  xcappicon.py AppIcon.appiconset exported/*.png\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "output",
        metavar="OUTPUT_DIR",
        help="Path to the AppIcon.appiconset directory to create or update",
    )
    parser.add_argument(
        "png",
        metavar="PNG",
        nargs="+",
        help="Input PNG files exported from design tools",
    )
    parser.add_argument(
        "--author",
        default="quickutils.xcappicon",
        help="Author string for Contents.json info.author (default: %(default)s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually copying files or modifying Contents.json",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # Basic logging setup for CLI usage; do not override if already configured.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(levelname)s: %(message)s",
        )

    if args.dry_run:
        logger.info("DRY RUN MODE: No files will be copied or modified")

    appicon_dir = args.output
    if not args.dry_run:
        os.makedirs(appicon_dir, exist_ok=True)

    contents_path = os.path.join(appicon_dir, "Contents.json")
    if os.path.exists(contents_path):
        logger.info("Loading existing Contents.json from %s", contents_path)
        with open(contents_path, "r", encoding="utf-8") as f:
            contents = json.load(f)

        before_filenames = {
            id(img)
            for img in contents.get("images") or []
            if isinstance(img, dict) and img.get("filename")
        }

        updated = assign_pngs_to_existing_contents(args.png, contents, appicon_dir, args.dry_run)
        if not updated and not before_filenames:
            logger.error(
                "No existing image entries could be matched. Nothing was updated."
            )
            return 1

        if args.dry_run:
            logger.info("[DRY RUN] Would write Contents.json to %s", contents_path)
        else:
            write_contents_json(appicon_dir, contents)
    else:
        logger.info("No Contents.json found; generating a new one")
        images = build_image_entries_from_scratch(args.png, appicon_dir, args.dry_run)
        if not images:
            logger.error("No valid PNG sizes were mapped to AppIcon slots")
            return 1

        contents = {
            "images": images,
            "info": {
                "author": args.author,
                "version": 1,
            },
        }
        if args.dry_run:
            logger.info("[DRY RUN] Would write Contents.json to %s", contents_path)
        else:
            write_contents_json(appicon_dir, contents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


