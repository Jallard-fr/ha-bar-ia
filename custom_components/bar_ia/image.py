"""Entité image « Photo de recherche » : transmet la photo d'étiquette à la Tâche IA."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BarConfigEntry
from .const import DOMAIN, SIGNAL_PHOTO, VERSION
from .images import SEARCH_IMAGE
from .manager import BarManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SearchPhotoImage(hass, entry.runtime_data, entry.entry_id)])


class SearchPhotoImage(ImageEntity):
    """Dernière photo utilisée pour une recherche par photo."""

    _attr_has_entity_name = True
    _attr_translation_key = "photo_recherche"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, manager: BarManager, entry_id: str) -> None:
        super().__init__(hass)
        self._manager = manager
        self._attr_unique_id = f"{entry_id}_photo_recherche"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Bar IA",
            manufacturer="Bar IA",
            model="Gestionnaire de bar",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self._manager.photo_entity_id = self.entity_id
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PHOTO, self._updated))

    async def async_will_remove_from_hass(self) -> None:
        self._manager.photo_entity_id = None

    @callback
    def _updated(self) -> None:
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        if self._manager.search_photo is None:
            self._manager.search_photo = await self._manager.images.async_read(SEARCH_IMAGE)
        return self._manager.search_photo
