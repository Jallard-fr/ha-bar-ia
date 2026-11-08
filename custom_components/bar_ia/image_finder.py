"""Recherche automatique de l'image d'une bouteille.

1. Candidats : Open Food Facts, Wikimedia Commons, puis adresses proposées par l'IA.
2. Chaque candidat est téléchargé de façon contrôlée (https, hôte public, taille limitée)
   et converti en JPEG.
3. L'appelant fait vérifier l'image par l'IA (vision) avant de la garder.
"""

from __future__ import annotations

import io
import ipaddress
import logging
import re
import socket
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import VERSION
from .images import ImageError, valid_url

_LOGGER = logging.getLogger(__name__)

USER_AGENT = f"HomeAssistant-BarIA/{VERSION} (+https://github.com/jallard-fr/ha-bar-ia)"
MAX_DOWNLOAD = 6_000_000
MIN_SIDE = 150
OUT_MAX_DIM = 800
TIMEOUT = aiohttp.ClientTimeout(total=15)

OFF_URL = "https://world.openfoodfacts.org/cgi/search.pl"
COMMONS_URL = "https://commons.wikimedia.org/w/api.php"

_STOP = {"de", "du", "la", "le", "les", "des", "the", "and", "et", "rhum", "rum", "gin", "vodka", "whisky",
         "whiskey", "liqueur", "ans", "years", "year", "old", "cl", "ml"}


@dataclass
class Candidate:
    """Image candidate et sa provenance."""

    url: str
    source: str
    titre: str = ""


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 2 and t not in _STOP}


def _score(query: set[str], text: str) -> int:
    return len(query & _tokens(text))


async def _get_json(hass: HomeAssistant, url: str, params: dict) -> dict:
    session = async_get_clientsession(hass)
    async with session.get(
        f"{url}?{urlencode(params)}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    ) as resp:
        if resp.status != 200:
            return {}
        data = await resp.json(content_type=None)
        return data if isinstance(data, dict) else {}


async def candidates_openfoodfacts(hass: HomeAssistant, query: str, limit: int = 3) -> list[Candidate]:
    """Produits Open Food Facts dont le nom/marque correspond à la requête."""
    words = _tokens(query)
    if not words:
        return []
    try:
        data = await _get_json(
            hass,
            OFF_URL,
            {
                "search_terms": query,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": 12,
                "fields": "product_name,brands,image_front_url,image_url",
            },
        )
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Open Food Facts indisponible : %s", err)
        return []
    scored = []
    for product in data.get("products") or []:
        if not isinstance(product, dict):
            continue
        url = product.get("image_front_url") or product.get("image_url")
        titre = f"{product.get('brands', '')} {product.get('product_name', '')}".strip()
        score = _score(words, titre)
        if url and score:
            scored.append((score, Candidate(str(url), "openfoodfacts.org", titre)))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


async def candidates_commons(hass: HomeAssistant, query: str, limit: int = 2) -> list[Candidate]:
    """Fichiers Wikimedia Commons correspondant à la requête."""
    words = _tokens(query)
    if not words:
        return []
    try:
        data = await _get_json(
            hass,
            COMMONS_URL,
            {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrnamespace": 6,
                "gsrsearch": f"{query} bottle",
                "gsrlimit": 6,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "iiurlwidth": OUT_MAX_DIM,
            },
        )
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Wikimedia Commons indisponible : %s", err)
        return []
    out = []
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if not str(info.get("mime", "")).startswith("image/") or info.get("mime") == "image/svg+xml":
            continue
        url = info.get("thumburl") or info.get("url")
        titre = str(page.get("title", ""))
        score = _score(words, titre)
        if url and score:
            out.append((score, Candidate(str(url), "commons.wikimedia.org", titre)))
    out.sort(key=lambda x: -x[0])
    return [c for _, c in out[:limit]]


async def _host_is_public(hass: HomeAssistant, host: str) -> bool:
    """Refuse les hôtes résolus vers un réseau privé, local ou réservé (anti-SSRF)."""
    try:
        infos = await hass.loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def to_jpeg(raw: bytes, max_dim: int = OUT_MAX_DIM) -> bytes:
    """Convertit n'importe quelle image lisible par Pillow en JPEG réduit (exécuteur)."""
    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415 - import lourd, à la demande

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            if min(img.size) < MIN_SIDE:
                raise ImageError("Image trop petite")
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
            img.thumbnail((max_dim, max_dim))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise ImageError("Image illisible") from err


MAX_REDIRECTS = 3
_REDIRECTS = (301, 302, 303, 307, 308)
_DATA_URI = re.compile(r"^data:image/(?:jpeg|jpg|png|webp|gif);base64,", re.IGNORECASE)


def from_data_uri(uri: str) -> bytes:
    """Image collée sous forme data:image/...;base64 (vignettes Google Images)."""
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415

    if not _DATA_URI.match(uri or ""):
        raise ImageError("Format d'image non pris en charge")
    payload = uri.split(",", 1)[1]
    if len(payload) > MAX_DOWNLOAD * 4 // 3 + 8:
        raise ImageError("Image trop volumineuse")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ImageError("Encodage de l'image invalide") from err


async def download(hass: HomeAssistant, url: str) -> bytes:
    """Télécharge une image distante (ou décode une image data:) et la renvoie en JPEG.

    Les redirections sont suivies manuellement (3 au plus) : chaque étape doit rester
    en https vers un hôte public.
    """
    url = (url or "").strip()
    if url.lower().startswith("data:"):
        return await hass.async_add_executor_job(to_jpeg, from_data_uri(url))
    session = async_get_clientsession(hass)
    for _ in range(MAX_REDIRECTS + 1):
        if not valid_url(url):
            raise ImageError("Adresse https:// attendue")
        host = urlparse(url).hostname or ""
        if not host or not await _host_is_public(hass, host):
            raise ImageError("Hôte non autorisé")
        try:
            async with session.get(
                url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"}, timeout=TIMEOUT,
                allow_redirects=False,
            ) as resp:
                if resp.status in _REDIRECTS and resp.headers.get("Location"):
                    url = urljoin(url, resp.headers["Location"])
                    continue
                if resp.status != 200:
                    raise ImageError(f"Le site a répondu {resp.status}")
                ctype = resp.headers.get("Content-Type", "").lower()
                if ctype and not ctype.startswith(("image/", "application/octet-stream", "binary/")):
                    raise ImageError("Cette adresse ne mène pas à une image (copiez l'adresse de l'image, pas de la page)")
                chunks, size = [], 0
                async for chunk in resp.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise ImageError("Image trop volumineuse")
                    chunks.append(chunk)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ImageError("Téléchargement impossible") from err
        return await hass.async_add_executor_job(to_jpeg, b"".join(chunks))
    raise ImageError("Trop de redirections")
