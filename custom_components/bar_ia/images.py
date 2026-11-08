"""Images des bouteilles : stockage local (/config/bar_ia/images) et diffusion authentifiée."""

from __future__ import annotations

import base64
import binascii
import re
import shutil
from http import HTTPStatus
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

IMAGE_URL = "/api/bar_ia/image"
SEARCH_IMAGE = "_search"
MAX_IMAGE_BYTES = 2_000_000
JPEG_MAGIC = b"\xff\xd8\xff"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_URL_RE = re.compile(r"^https://[^\s\"'<>]{4,2000}$", re.IGNORECASE)


class ImageError(ValueError):
    """Image refusée."""


def valid_name(name: str) -> bool:
    """Nom de fichier sûr (pas de chemin, pas d'extension)."""
    return bool(_NAME_RE.match(name or ""))


def decode_jpeg(data: str) -> bytes:
    """Décode une image JPEG en base64 (avec ou sans préfixe data:) et la contrôle."""
    if not isinstance(data, str) or not data:
        raise ImageError("Image vide")
    if data.startswith("data:"):
        header, _, data = data.partition(",")
        if "image/jpeg" not in header:
            raise ImageError("Seules les images JPEG sont acceptées")
    if len(data) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        raise ImageError("Image trop volumineuse (2 Mo maximum)")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ImageError("Encodage de l'image invalide") from err
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError("Image trop volumineuse (2 Mo maximum)")
    if not raw.startswith(JPEG_MAGIC):
        raise ImageError("Le fichier n'est pas une image JPEG")
    return raw


def valid_url(url: str) -> bool:
    """Seules les adresses https:// sont acceptées (pas de javascript:, data:, http:)."""
    return bool(_URL_RE.match(url or ""))


class ImageStore:
    """Fichiers JPEG dans /config/bar_ia/images."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.directory = Path(hass.config.path("bar_ia", "images"))

    def path(self, name: str) -> Path:
        if not valid_name(name):
            raise ImageError("Nom d'image invalide")
        return self.directory / f"{name}.jpg"

    async def async_save(self, name: str, raw: bytes) -> None:
        target = self.path(name)

        def _write() -> None:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(target)

        await self.hass.async_add_executor_job(_write)

    async def async_read(self, name: str) -> bytes | None:
        target = self.path(name)
        return await self.hass.async_add_executor_job(
            lambda: target.read_bytes() if target.is_file() else None
        )

    async def async_copy(self, src: str, dst: str) -> bool:
        source, target = self.path(src), self.path(dst)

        def _copy() -> bool:
            if not source.is_file():
                return False
            self.directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return True

        return await self.hass.async_add_executor_job(_copy)

    async def async_delete(self, name: str) -> None:
        target = self.path(name)
        await self.hass.async_add_executor_job(lambda: target.unlink(missing_ok=True))


class BarImageView(HomeAssistantView):
    """Sert les images ; authentification obligatoire (jeton ou chemin signé)."""

    url = IMAGE_URL + "/{name}"
    name = "api:bar_ia:image"
    requires_auth = True

    def __init__(self, store: ImageStore) -> None:
        self.store = store

    async def get(self, request: web.Request, name: str) -> web.StreamResponse:
        base = name.removesuffix(".jpg")
        if not name.endswith(".jpg") or not valid_name(base):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        path = self.store.path(base)
        if not await self.store.hass.async_add_executor_job(path.is_file):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "image/jpeg",
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )
