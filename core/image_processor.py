"""
图像处理核心模块 - 包含水印算法和安全检查
"""

from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image
from astrbot.api import logger

from ..constants import DOUBAN_ASPECT_RATIO


class ImageProcessor:
    """图像处理器"""

    MAX_IMAGE_PIXELS = 10000 * 10000
    WARNING_PIXELS = 5000 * 5000
    LARGE_IMAGE_MARGIN = 64
    SMALL_IMAGE_MARGIN = 32
    LARGE_IMAGE_THRESHOLD = 1024
    ALPHA_THRESHOLD = 10
    DOUBAO_SIZE_RATIO = 0.13
    DOUBAO_MARGIN_RATIO = 0.03

    def __init__(self, watermark_dir: Path):
        self.watermark_dir = watermark_dir
        self._watermark_cache = {}

    def _get_watermark_path(self, filename: str) -> Path:
        return self.watermark_dir / filename

    def load_watermark(self, filename: str) -> Optional[Image.Image]:
        if filename in self._watermark_cache:
            return self._watermark_cache[filename].copy()

        watermark_path = self._get_watermark_path(filename)
        if watermark_path.exists():
            try:
                watermark = Image.open(watermark_path).convert("RGBA")
                self._watermark_cache[filename] = watermark.copy()
                return watermark.copy()
            except Exception as e:
                logger.error(f"加载水印失败 {filename}: {e}")
                return None
        else:
            logger.error(f"水印文件不存在: {watermark_path}")
            return None

    def check_image_safety(self, img: Image.Image) -> Tuple[bool, str]:
        pixels = img.width * img.height
        if pixels > self.MAX_IMAGE_PIXELS:
            return False, f"图像尺寸过大（{pixels}像素），安全限制: {self.MAX_IMAGE_PIXELS}像素"
        if pixels > self.WARNING_PIXELS:
            logger.warning(f"处理大图像: {pixels}像素 ({img.width}x{img.height})")
        return True, ""

    def preprocess_image(self, image_data: bytes) -> Optional[Image.Image]:
        try:
            img = Image.open(BytesIO(image_data))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            is_safe, error_msg = self.check_image_safety(img)
            if not is_safe:
                logger.error(f"图片安全检查失败: {error_msg}")
                return None
            return img
        except Exception as e:
            logger.error(f"图片预处理失败: {e}")
            return None

    def _apply_opacity(self, watermark: Image.Image, opacity: float) -> Image.Image:
        """应用透明度"""
        alpha = watermark.getchannel("A")
        new_alpha = alpha.point(lambda v: int(255 * opacity) if v > self.ALPHA_THRESHOLD else 0)
        watermark.putalpha(new_alpha)
        return watermark

    def _calculate_gemini_position(self, img_width: int, img_height: int, watermark: Image.Image) -> Tuple[int, int]:
        wm_width, wm_height = watermark.size
        if img_width > self.LARGE_IMAGE_THRESHOLD and img_height > self.LARGE_IMAGE_THRESHOLD:
            margin = self.LARGE_IMAGE_MARGIN
        else:
            margin = self.SMALL_IMAGE_MARGIN
        x = img_width - margin - wm_width
        y = img_height - margin - wm_height
        return x, y

    def apply_gemini_watermark(self, image: Image.Image, watermark: Image.Image, opacity: float = 0.25) -> Optional[Image.Image]:
        is_safe, error_msg = self.check_image_safety(image)
        if not is_safe:
            logger.error(f"图片安全检查失败: {error_msg}")
            return None

        result = image.copy()
        if result.mode != "RGBA":
            result = result.convert("RGBA")

        width, height = result.size
        wm_width, wm_height = watermark.size
        x, y = self._calculate_gemini_position(width, height, watermark)

        if x < 0 or y < 0:
            logger.warning("水印尺寸超过图片尺寸，跳过处理")
            return image.convert("RGB")

        resized_watermark = watermark.resize((wm_width, wm_height), Image.Resampling.LANCZOS)
        if resized_watermark.mode != "RGBA":
            resized_watermark = resized_watermark.convert("RGBA")

        resized_watermark = self._apply_opacity(resized_watermark, opacity)

        result.paste(resized_watermark, (x, y), resized_watermark)
        return result.convert("RGB")

    def _calculate_doubao_size(self, img_width: int, img_height: int) -> Tuple[int, int]:
        wm_width = int(img_width * self.DOUBAO_SIZE_RATIO)
        wm_height = int(wm_width / DOUBAN_ASPECT_RATIO)
        return wm_width, wm_height

    def _calculate_doubao_margin(self, img_width: int, img_height: int) -> Tuple[int, int]:
        margin_x = int(img_width * self.DOUBAO_MARGIN_RATIO)
        margin_y = int(img_height * self.DOUBAO_MARGIN_RATIO)
        return margin_x, margin_y

    def _calculate_doubao_position(self, img_width: int, img_height: int, wm_width: int, wm_height: int) -> Tuple[int, int]:
        margin_right = int(img_width * self.DOUBAO_MARGIN_RATIO)
        margin_bottom = int(img_height * self.DOUBAO_MARGIN_RATIO)
        x = img_width - margin_right - wm_width
        y = img_height - margin_bottom - wm_height
        return x, y

    def apply_doubao_watermark(self, image: Image.Image, watermark: Optional[Image.Image], opacity: float = 0.7) -> Optional[Image.Image]:
        if watermark is None:
            logger.error("水印素材为空")
            return None

        is_safe, error_msg = self.check_image_safety(image)
        if not is_safe:
            logger.error(f"图片安全检查失败: {error_msg}")
            return None

        result = image.copy()
        if result.mode != "RGBA":
            result = result.convert("RGBA")

        wm_width, wm_height = self._calculate_doubao_size(image.width, image.height)

        resized_watermark = watermark.resize((wm_width, wm_height), Image.Resampling.LANCZOS)
        if resized_watermark.mode != "RGBA":
            resized_watermark = resized_watermark.convert("RGBA")

        resized_watermark = self._apply_opacity(resized_watermark, opacity)

        x, y = self._calculate_doubao_position(image.width, image.height, wm_width, wm_height)
        result.paste(resized_watermark, (x, y), resized_watermark)
        return result.convert("RGB")

    def generate_output_path(self, data_dir: Path, source_info: str, watermark_type: str) -> Path:
        from ..utils.file_utils import FileUtils
        filename = FileUtils.generate_filename(source_info, f"watermark_{watermark_type}")
        return data_dir / filename
