"""
网络请求工具模块 - 包含SSRF防护
"""

import asyncio
import socket
import ipaddress
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse
from astrbot.api import logger

import aiohttp


class FixedDNSResolver:
    """固定DNS解析器，防止DNS重绑定攻击"""

    def __init__(self, safe_resolutions: Dict[str, str]):
        self._safe_resolutions = safe_resolutions
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, hostname: str, port=0, family=socket.AF_INET):
        if hostname in self._safe_resolutions:
            safe_ip = self._safe_resolutions[hostname]
            try:
                ip_obj = ipaddress.ip_address(safe_ip)
            except ValueError:
                return []

            if family == socket.AF_INET and ip_obj.version != 4:
                return []
            elif family == socket.AF_INET6 and ip_obj.version != 6:
                return []

            return [{
                "hostname": hostname,
                "host": safe_ip,
                "port": port,
                "family": family,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }]
        return await self._resolver.resolve(hostname, port, family)


class NetworkUtils:
    """网络请求工具类"""

    DANGEROUS_PATTERNS = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.",
        "metadata.",
        ".internal",
        ".local",
        ".localdomain",
        "10.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
        "192.168.",
    ]

    def __init__(self, timeout: int = 30, max_size: int = 10 * 1024 * 1024):
        self.timeout = timeout
        self.max_size = max_size
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=self.timeout)
                    self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _is_private_ip(self, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            return False

    def _is_ip_format(self, hostname: str) -> bool:
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            pass
        try:
            ip_int = int(hostname)
            if 0 <= ip_int <= 0xFFFFFFFF:
                ipaddress.ip_address(ip_int)
                return True
        except (ValueError, ipaddress.AddressValueError):
            pass
        return False

    async def _resolve_hostname(self, hostname: str) -> Optional[str]:
        try:
            loop = asyncio.get_running_loop()
            try:
                addrinfo = await loop.getaddrinfo(
                    hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM
                )
                if addrinfo:
                    return addrinfo[0][4][0]
            except socket.gaierror:
                pass
            try:
                addrinfo = await loop.getaddrinfo(
                    hostname, None, family=socket.AF_INET6, type=socket.SOCK_STREAM
                )
                if addrinfo:
                    return addrinfo[0][4][0]
            except socket.gaierror:
                pass
        except (socket.gaierror, asyncio.CancelledError, Exception) as e:
            logger.debug(f"DNS解析失败 {hostname}: {e}")
        return None

    async def _is_safe_url_with_ip(self, url: str) -> Optional[Tuple[str, str]]:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return None
            hostname = parsed.hostname
            if not hostname:
                return None

            if self._is_ip_format(hostname):
                if self._is_private_ip(hostname):
                    return None
                return (hostname, hostname)

            if hostname.startswith("[") and hostname.endswith("]"):
                hostname_clean = hostname[1:-1]
                if self._is_ip_format(hostname_clean):
                    if self._is_private_ip(hostname_clean):
                        return None
                    return (hostname_clean, hostname)

            for pattern in self.DANGEROUS_PATTERNS:
                clean_pattern = pattern[1:] if pattern.startswith(".") else pattern
                if (hostname == pattern or hostname.endswith("." + clean_pattern) or
                        hostname.startswith(pattern)):
                    return None

            resolved_ip = await self._resolve_hostname(hostname)
            if not resolved_ip:
                return None

            if self._is_private_ip(resolved_ip):
                return None

            return (resolved_ip, hostname)
        except Exception as e:
            logger.warning(f"URL安全检查失败 {url}: {e}")
            return None

    async def download_image(self, url: str) -> Optional[bytes]:
        """下载图片（防SSRF版本）"""
        safe_info = await self._is_safe_url_with_ip(url)
        if not safe_info:
            logger.warning(f"拒绝不安全的URL: {url}")
            return None

        safe_ip, hostname = safe_info

        try:
            resolver = FixedDNSResolver({hostname: safe_ip})
            connector = aiohttp.TCPConnector(
                resolver=resolver,
                limit_per_host=3,
                ttl_dns_cache=300,
            )
            timeout = aiohttp.ClientTimeout(total=self.timeout)

            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"下载失败，状态码: {response.status}")
                        return None

                    buffer = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        buffer.extend(chunk)
                        if len(buffer) > self.max_size:
                            logger.error(f"图片超过大小限制: {len(buffer)} bytes")
                            return None

                    logger.info(f"成功下载图片，大小: {len(buffer)} bytes")
                    return bytes(buffer)

        except asyncio.TimeoutError:
            logger.error(f"下载超时: {url}")
            return None
        except Exception as e:
            logger.error(f"下载图片失败 {url}: {str(e)}")
            return None

    async def cleanup(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
