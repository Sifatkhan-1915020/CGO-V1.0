"""
Image preprocessing and enhancement engine using OpenCV.
Handles poor quality scans, deskewing, noise reduction, shadow removal, and contrast enhancement.
"""

import io
from typing import Tuple, List, Optional
import numpy as np
import cv2
from PIL import Image, ImageFile

# Enable partial recovery for truncated/corrupt images
ImageFile.LOAD_TRUNCATED_IMAGES = True


class ImageEnhancer:
    """Preprocesses images and rendered document pages to maximize OCR accuracy."""

    @staticmethod
    def bytes_to_cv2(image_bytes: bytes) -> np.ndarray:
        """Decode image bytes to OpenCV BGR format with fallback."""
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            # Fallback to Pillow in case OpenCV fails on specific color profiles / TIFF formats
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return img

    @staticmethod
    def cv2_to_bytes(cv2_img: np.ndarray, format: str = ".jpg") -> bytes:
        """Encode OpenCV image back to bytes with compression."""
        params = []
        if format.lower() in (".jpg", ".jpeg"):
            params = [int(cv2.IMWRITE_JPEG_QUALITY), 90]
        success, encoded = cv2.imencode(format, cv2_img, params)
        if not success:
            raise ValueError(f"Failed to encode image to {format}")
        return encoded.tobytes()

    @classmethod
    def enhance(
        cls,
        image_bytes: bytes,
        deskew: bool = True,
        remove_shadows: bool = True,
        adaptive_threshold: bool = True,
        denoise: bool = True,
        target_dpi: int = 300,
        current_dpi: Optional[int] = None,
        output_format: str = ".jpg"
    ) -> Tuple[bytes, List[str]]:
        """
        Run the complete enhancement pipeline.
        Returns: (enhanced_image_bytes, list_of_applied_transforms)
        """
        img = cls.bytes_to_cv2(image_bytes)
        applied_ops = []

        # 1. Resolution Optimization (Downscale if overly large > 1600px, Upscale if < 1000px)
        # 1600px preserves 100% of fine text & table lines while reducing network payload by ~85%
        h, w = img.shape[:2]
        max_dim = max(h, w)
        if max_dim > 1600:
            scale_factor = 1600.0 / max_dim
            img = cv2.resize(img, (int(w * scale_factor), int(h * scale_factor)), interpolation=cv2.INTER_AREA)
            applied_ops.append(f"downscale_{round(scale_factor, 2)}x")
        elif max_dim < 1000:
            scale_factor = 1200.0 / max_dim
            img = cv2.resize(img, (int(w * scale_factor), int(h * scale_factor)), interpolation=cv2.INTER_CUBIC)
            applied_ops.append(f"upscale_{round(scale_factor, 2)}x")

        # 2. Deskewing
        if deskew:
            img, angle = cls._deskew(img)
            if abs(angle) > 0.5:
                applied_ops.append(f"deskew_{round(angle, 1)}deg")

        # 3. Shadow Removal / Illumination Equalization
        if remove_shadows:
            img = cls._remove_shadows(img)
            applied_ops.append("shadow_removal")

        # 4. Denoising
        if denoise:
            img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21) if len(img.shape) == 3 else cv2.fastNlMeansDenoising(img, None, 5, 7, 21)
            applied_ops.append("denoise")

        # 5. Contrast & Binarization (Convert to high-contrast grayscale)
        if adaptive_threshold:
            img = cls._adaptive_binarize(img)
            applied_ops.append("adaptive_binarization")

        output_bytes = cls.cv2_to_bytes(img, format=output_format)
        return output_bytes, applied_ops

    @staticmethod
    def _deskew(image: np.ndarray) -> Tuple[np.ndarray, float]:
        """Detect and correct document skew using contour minimum bounding box."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        # Invert colors (text as foreground)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 100:
            return image, 0.0

        angle = cv2.minAreaRect(coords)[-1]
        # minAreaRect returns angle in [-90, 0)
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = -(angle - 90)
        else:
            angle = -angle

        # If tilt is negligible, skip rotation
        if abs(angle) < 0.5 or abs(angle) > 45.0:
            return image, 0.0

        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated, angle

    @staticmethod
    def _remove_shadows(image: np.ndarray) -> np.ndarray:
        """Flatten uneven lighting and phone camera shadows using morphological closing."""
        rgb_planes = cv2.split(image)
        result_planes = []
        for plane in rgb_planes:
            dilated_img = cv2.dilate(plane, np.ones((7, 7), np.uint8))
            bg_img = cv2.medianBlur(dilated_img, 21)
            diff_img = 255 - cv2.absdiff(plane, bg_img)
            norm_img = cv2.normalize(diff_img, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
            result_planes.append(norm_img)
        return cv2.merge(result_planes)

    @staticmethod
    def _adaptive_binarize(image: np.ndarray) -> np.ndarray:
        """Apply adaptive Gaussian thresholding for crisp character edges."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        # Local adaptive thresholding
        binarized = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
        )
        return cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)
