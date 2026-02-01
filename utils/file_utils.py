"""
文件处理工具模块 - 包含解压炸弹防护
"""

import os
import re
import base64
import hashlib
import secrets
import time as time_module
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs
from astrbot.api import logger


class FileUtils:
    """文件处理工具类"""

    SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
    DEFAULT_IMAGE_SIZE_LIMIT = 10 * 1024 * 1024
    DEFAULT_GIF_SIZE_LIMIT = 15 * 1024 * 1024

    MAX_IMAGE_PIXELS = 10000 * 10000
    WARNING_PIXELS = 5000 * 5000

    URL_LENGTH_THRESHOLD = 1000

    MAGIC_BYTES = {
        "gif": ([b"GIF87a", b"GIF89a"], 6),
        "png": (b"\x89PNG\r\n\x1a\n", 8),
        "jpeg": (b"\xff\xd8\xff", 3),
        "webp": (b"RIFF", 4, b"WEBP", 8),
        "bmp": (b"BM", 2),
    }

    @staticmethod
    def get_file_extension(url_or_path: str) -> Optional[str]:
        try:
            parsed = urlparse(url_or_path)
            path = parsed.path
            match = re.search(r"\.([a-zA-Z0-9]+)$", path)
            if match:
                ext = f".{match.group(1).lower()}"
                if ext in FileUtils.SUPPORTED_FORMATS:
                    return ext
            query_params = parse_qs(parsed.query)
            for param_name in ["format", "type", "ext"]:
                if param_name in query_params:
                    param_value = query_params[param_name][0].lower()
                    if param_value.startswith("."):
                        if param_value in FileUtils.SUPPORTED_FORMATS:
                            return param_value
                    else:
                        ext = f".{param_value}"
                        if ext in FileUtils.SUPPORTED_FORMATS:
                            return ext
            return None
        except Exception:
            return None

    @staticmethod
    def is_image_url(url: str) -> bool:
        ext = FileUtils.get_file_extension(url)
        return ext in FileUtils.SUPPORTED_FORMATS if ext else False

    @staticmethod
    def generate_filename(original_url: str, prefix: str) -> str:
        if len(original_url) > FileUtils.URL_LENGTH_THRESHOLD:
            content_hash = hashlib.md5(original_url.encode()).hexdigest()[:16]
            hash_input = f"{content_hash}_{prefix}"
        else:
            hash_input = f"{original_url}_{prefix}"

        timestamp = int(time_module.time())
        random_token = secrets.token_hex(4)
        hash_input = f"{hash_input}_{timestamp}_{random_token}"
        file_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:12]

        ext = FileUtils.get_file_extension(original_url) or ".png"
        filename = f"{prefix}_{file_hash}{ext}"

        return filename

    @staticmethod
    def validate_image_size(file_path: str) -> Tuple[bool, str]:
        try:
            file_size = os.path.getsize(file_path)
            ext = FileUtils.get_file_extension(file_path)

            max_size = FileUtils.DEFAULT_GIF_SIZE_LIMIT if ext == ".gif" else FileUtils.DEFAULT_IMAGE_SIZE_LIMIT
            max_size_mb = 15 if ext == ".gif" else 10

            if file_size > max_size:
                file_size_mb = file_size / 1024 / 1024
                return False, f"文件过大（{file_size_mb:.1f}MB），最大允许：{max_size_mb}MB"

            return True, ""
        except Exception as e:
            return False, f"无法获取文件大小: {str(e)}"

    @staticmethod
    def is_base64_image(data: str) -> bool:
        return isinstance(data, str) and data.startswith("base64://")

    @staticmethod
    def decode_base64_image(base64_data: str) -> Optional[bytes]:
        try:
            if base64_data.startswith("base64://"):
                base64_data = base64_data[len("base64://"):]
            return base64.b64decode(base64_data, validate=True)
        except Exception as e:
            logger.error(f"Base64解码失败: {e}")
            return None

    @staticmethod
    def detect_image_format_by_magic(data: bytes) -> Optional[str]:
        if len(data) < 12:
            return None

        magic = FileUtils.MAGIC_BYTES

        if data[:6] in magic["gif"][0]:
            return ".gif"
        if data[:8] == magic["png"][0]:
            return ".png"
        if data[:3] == magic["jpeg"][0]:
            return ".jpg"
        if data[:4] == magic["webp"][0] and data[8:12] == magic["webp"][2]:
            return ".webp"
        if data[:2] == magic["bmp"][0]:
            return ".bmp"

        return None

    @staticmethod
    def cleanup_file(file_path: Path):
        """安全清理文件"""
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"已清理临时文件: {file_path.name}")
            except (OSError, PermissionError) as e:
                logger.warning(f"清理文件失败 {file_path}: {e}")
