"""Shared upload validation + processing for ImageField uploads.

Used by Product, Supplier logo, Image Library. Single source of truth for:
- size/type/dimension validation (defeats spoofing + decompression bombs)
- resize to a max dimension, re-encode (JPEG, or PNG when transparent)
- EXIF stripping (kills location/device metadata from phone photos)
- unique filename (uuid) so HTTP caches stay long-lived without staleness
- ID-based folder paths per vertical → business (immutable)
"""

import io
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile


# ─── Validation constants ───────────────────────────────────────────────
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp'}

MAX_FILE_SIZE = 5 * 1024 * 1024          # 5 MB
MAX_INPUT_DIMENSION = 6000               # reject anything weirdly huge (bomb defense)
DEFAULT_OUTPUT_DIMENSION = 800           # resized output cap
DEFAULT_QUALITY = 80                     # JPEG/WEBP compression


# ─── Main processor ─────────────────────────────────────────────────────
def process_uploaded_image(
    uploaded_file,
    *,
    max_dim: int = DEFAULT_OUTPUT_DIMENSION,
    quality: int = DEFAULT_QUALITY,
) -> ContentFile:
    """Validate, resize, and re-encode an uploaded image.

    Returns a Django ContentFile ready to assign to an ImageField.
    Raises ValidationError on any check failure.
    """
    # 1. Size cap
    if uploaded_file.size > MAX_FILE_SIZE:
        raise ValidationError(
            f"Image is too large ({uploaded_file.size // 1024} KB). "
            f"Max {MAX_FILE_SIZE // 1024 // 1024} MB."
        )

    # 2. Extension whitelist
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Use JPG, PNG, or WEBP."
        )

    # 3. Content-type whitelist (defeats extension spoofing)
    content_type = getattr(uploaded_file, 'content_type', '') or ''
    if content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"File reports as '{content_type}', not an allowed image type."
        )

    # 4. Pillow.verify() — confirms it's actually a valid image
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        img.verify()
    except (UnidentifiedImageError, Exception) as e:
        raise ValidationError("This file isn't a valid image.") from e

    # verify() consumes the file — must reopen
    uploaded_file.seek(0)
    img = Image.open(uploaded_file)

    # 5. Dimension cap (decompression bomb defense)
    if img.width > MAX_INPUT_DIMENSION or img.height > MAX_INPUT_DIMENSION:
        raise ValidationError(
            f"Image dimensions too large ({img.width}x{img.height}). "
            f"Max {MAX_INPUT_DIMENSION}px on either side."
        )

    # 6. Normalize mode — keep real transparency (transparent product photos
    #    blend with dark mode); only flatten when the alpha carries nothing.
    if img.mode == 'P':
        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
    elif img.mode == 'LA':
        img = img.convert('RGBA')
    if img.mode == 'RGBA' and img.getchannel('A').getextrema()[0] == 255:
        img = img.convert('RGB')      # fully opaque — drop the useless alpha
    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGB')

    # 7. Resize (thumbnail preserves aspect ratio)
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    # 8. Strip EXIF by re-saving without it. JPEG can't hold transparency,
    #    so images with alpha go out as PNG; everything else stays JPEG.
    output = io.BytesIO()
    if img.mode == 'RGBA':
        img.save(output, format='PNG', optimize=True)
        out_ext = 'png'
    else:
        img.save(output, format='JPEG', quality=quality, optimize=True)
        out_ext = 'jpg'
    output.seek(0)

    # 9. Unique filename — uuid means cached URLs never stale
    new_filename = f"{uuid.uuid4().hex}.{out_ext}"

    return ContentFile(output.read(), name=new_filename)


# ─── Path helpers (ID-based, immutable) ─────────────────────────────────
def _safe_vertical(business):
    if not business:
        return 'misc'
    return getattr(business, 'business_type', None) or 'misc'

def _biz_folder(business):
    if not business:
        return 'unassigned'
    return f"biz-{business.id}"

def product_image_path(instance, filename):
    """e.g. pharmacy/biz-45/products/{uuid}.jpg"""
    return f"{_safe_vertical(instance.business)}/{_biz_folder(instance.business)}/products/{filename}"

def supplier_image_path(instance, filename):
    """e.g. retail/biz-12/suppliers/{uuid}.jpg"""
    return f"{_safe_vertical(instance.business)}/{_biz_folder(instance.business)}/suppliers/{filename}"

def library_image_path(instance, filename):
    """Image Library — shared by category, not per-business.
    e.g. library/pharmacy/{uuid}.jpg"""
    category = getattr(instance, 'category', None) or 'general'
    return f"library/{category}/{filename}"
