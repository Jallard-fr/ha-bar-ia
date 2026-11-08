"""Déclare automatiquement la carte comme ressource Lovelace (mode storage)."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import CARD_FILENAME, STATIC_URL, VERSION

_LOGGER = logging.getLogger(__name__)

CARD_BASE_URL = f"{STATIC_URL}/{CARD_FILENAME}"
CARD_URL = f"{CARD_BASE_URL}?v={VERSION}"


def _resources(hass: HomeAssistant):
    data = hass.data.get("lovelace")
    if data is None:
        return None
    if isinstance(data, dict):  # anciennes versions de HA
        return data.get("resources")
    return getattr(data, "resources", None)


async def async_register_card_resource(hass: HomeAssistant) -> None:
    """Ajoute (ou met à jour la version de) la ressource de la carte.

    Sans effet si les ressources sont gérées en YAML : la carte reste alors
    chargée via add_extra_js_url.
    """
    resources = _resources(hass)
    if resources is None or not hasattr(resources, "async_create_item"):
        return
    try:
        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True
        for item in resources.async_items():
            url = str(item.get("url", ""))
            if url.split("?")[0] != CARD_BASE_URL:
                continue
            if url != CARD_URL:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": CARD_URL})
            return
        await resources.async_create_item({"res_type": "module", "url": CARD_URL})
    except Exception as err:  # noqa: BLE001 - ne doit jamais bloquer l'installation
        _LOGGER.warning("Impossible d'enregistrer la ressource de la carte Bar IA : %s", err)
