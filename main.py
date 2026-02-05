"""
仿制AI水印插件主入口模块
"""

from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger
from astrbot.api import message_components as Comp

from .constants import PLUGIN_NAME
from .utils.network_utils import NetworkUtils
from .utils.file_utils import FileUtils
from .core.image_processor import ImageProcessor


WATERMARK_COMMANDS = {
    "gemini水印": "gemini",
    "豆包水印": "doubao",
}


@register("仿制AI水印", "AI Developer", "仿制AI水印处理插件", "1.0.0")
class FakeAIWatermarkPlugin(Star):
    """仿制AI水印处理插件"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_dir = Path(__file__).parent
        self.watermark_dir = self.plugin_dir / "watermark_PNG"
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.gemini_opacity = context.config.get("gemini_opacity", 0.25)
            self.doubao_opacity = context.config.get("doubao_opacity", 0.7)
        except (AttributeError, TypeError) as e:
            logger.warning(f"读取配置失败，使用默认值: {e}")
            self.gemini_opacity = 0.25
            self.doubao_opacity = 0.7

        self.network_utils = NetworkUtils(timeout=30, max_size=10 * 1024 * 1024)
        self.image_processor = ImageProcessor(self.watermark_dir)

        logger.info(f"仿制AI水印插件已加载 - Gemini透明度: {self.gemini_opacity}, 豆包透明度: {self.doubao_opacity}")

    @staticmethod
    def _extract_command(message_str: str) -> str:
        """提取实际命令"""
        if " @" in message_str:
            return message_str.split("@", 1)[0].strip()
        elif message_str.startswith("@"):
            parts = message_str.split(None, 2)
            return parts[1].strip() if len(parts) >= 2 else message_str
        return message_str

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_plain_commands(self, event: AstrMessageEvent):
        """处理无斜杠的水印指令"""
        message_str = event.message_str.strip()

        if message_str.startswith("/"):
            return

        actual_command = self._extract_command(message_str)

        if actual_command in WATERMARK_COMMANDS:
            watermark_type = WATERMARK_COMMANDS[actual_command]
            logger.info(f"收到无斜杠指令: {actual_command} -> {watermark_type}")
            async for result in self._process_watermark(event, watermark_type):
                yield result

    def _extract_image_from_event(self, event: AstrMessageEvent) -> Optional[str]:
        try:
            messages = event.get_messages()
        except AttributeError:
            messages = event.message_obj.message

        for comp in messages:
            if isinstance(comp, Comp.Image):
                return comp.url or getattr(comp, "file", None) or getattr(comp, "data", {}).get("url")
            elif isinstance(comp, Comp.Reply):
                if hasattr(comp, "chain") and comp.chain:
                    for reply_comp in comp.chain:
                        if isinstance(reply_comp, Comp.Image):
                            return reply_comp.url or getattr(reply_comp, "file", None) or getattr(reply_comp, "data", {}).get("url")
        return None

    async def _process_watermark(self, event: AstrMessageEvent, watermark_type: str):
        output_path = None
        try:
            image_url = self._extract_image_from_event(event)
            if not image_url:
                yield event.plain_result("❌ 未检测到图片，请发送图片后重试")
                return

            image_data = await self.network_utils.download_image(image_url)
            if not image_data:
                yield event.plain_result("❌ 图片下载失败或URL不安全")
                return

            image = self.image_processor.preprocess_image(image_data)
            if not image:
                yield event.plain_result("❌ 图片处理失败")
                return

            if watermark_type == "gemini":
                if image.width > 1024 and image.height > 1024:
                    watermark = self.image_processor.load_watermark("gemini_96px.png")
                else:
                    watermark = self.image_processor.load_watermark("gemini_48px.png")

                if not watermark:
                    yield event.plain_result("❌ 水印素材加载失败")
                    return

                result = self.image_processor.apply_gemini_watermark(image, watermark, self.gemini_opacity)
            else:
                watermark = self.image_processor.load_watermark("doubao.png")

                if not watermark:
                    yield event.plain_result("❌ 水印素材加载失败")
                    return

                result = self.image_processor.apply_doubao_watermark(image, watermark, self.doubao_opacity)

                if not result:
                    yield event.plain_result("❌ 水印应用失败")
                    return

            if result is None:
                yield event.plain_result("❌ 水印处理失败")
                return

            output_path = self.image_processor.generate_output_path(self.data_dir, "user_image", watermark_type)
            result.save(str(output_path), quality=95)
            logger.info(f"水印处理完成: {output_path}")

            yield event.chain_result([Comp.Image(file=str(output_path))])

        except Exception as e:
            logger.error(f"水印处理异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 处理失败: {str(e)}")
        finally:
            if output_path and output_path.exists():
                FileUtils.cleanup_file(output_path)

    async def cleanup(self):
        await self.network_utils.cleanup()
        logger.info("仿制AI水印插件资源清理完成")
