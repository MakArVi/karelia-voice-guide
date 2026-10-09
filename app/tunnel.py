"""Публичный HTTPS-адрес для мини-приложения через Cloudflare Quick Tunnel (без регистрации)."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
import time

import httpx

from .config import BIN_DIR, LOGS_DIR

log = logging.getLogger(__name__)
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class CloudflaredTunnel:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self._log_file = None
        self._pid_file = LOGS_DIR / "cloudflared.pid"

    @staticmethod
    def executable() -> str | None:
        local = BIN_DIR / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")
        return str(local) if local.exists() else shutil.which("cloudflared")

    @staticmethod
    async def download() -> str:
        """Скачивает официальный cloudflared с GitHub в папку bin (только Windows)."""
        if sys.platform != "win32":
            raise RuntimeError("установите cloudflared: https://github.com/cloudflare/cloudflared")
        target = BIN_DIR / "cloudflared.exe"
        BIN_DIR.mkdir(exist_ok=True)
        log.info("Скачиваю cloudflared (около 60 МБ)…")
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            response = await client.get(url)
            response.raise_for_status()
        target.write_bytes(response.content)
        return str(target)

    async def start(self, timeout: float = 60) -> str:
        exe = self.executable() or await self.download()
        self._kill_stale()
        LOGS_DIR.mkdir(exist_ok=True)
        log_path = LOGS_DIR / "cloudflared.log"
        self._log_file = open(log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=self._log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        self._pid_file.write_text(str(self.proc.pid))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("cloudflared завершился, подробности в logs/cloudflared.log")
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if not self.url and (match := _URL.search(text)):
                self.url = match.group(0)
            if self.url and "Registered tunnel connection" in text:
                break
            await asyncio.sleep(0.5)
        if not self.url:
            raise TimeoutError("cloudflared не выдал адрес туннеля, см. logs/cloudflared.log")
        await self._wait_reachable()
        return self.url

    async def _wait_reachable(self, attempts: int = 15) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(attempts):
                try:
                    if (await client.get(f"{self.url}/api/health")).status_code == 200:
                        log.info("Туннель доступен: %s", self.url)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2)
        log.warning("Туннель создан, но с этого компьютера пока не открывается (DNS может обновиться позже): %s",
                    self.url)

    def _kill_stale(self) -> None:
        """Останавливает cloudflared, оставшийся от прошлого запуска (только если это действительно он)."""
        if not self._pid_file.exists():
            return
        pid = self._pid_file.read_text().strip()
        self._pid_file.unlink(missing_ok=True)
        if not pid.isdigit() or sys.platform != "win32":
            return
        info = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                              capture_output=True, text=True, encoding="cp866", errors="replace")
        if "cloudflared" in info.stdout.lower():
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            log.info("Остановлен старый процесс cloudflared (PID %s)", pid)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_file:
            self._log_file.close()
        self._pid_file.unlink(missing_ok=True)
