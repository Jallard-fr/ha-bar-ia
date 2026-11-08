"""Capteurs Bar IA (statistiques du bar, plan, statut IA)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BarConfigEntry
from .const import DOMAIN, SIGNAL_UPDATED, STATUS_ERROR, STATUS_IDLE, STATUS_RUNNING, VERSION
from .logic import CATEGORIES
from .manager import BarManager


@dataclass(frozen=True, kw_only=True)
class BarSensorDescription(SensorEntityDescription):
    """Description d'un capteur Bar IA."""

    value_fn: Callable[[BarManager], Any]
    attrs_fn: Callable[[BarManager], dict[str, Any]] | None = None


BOTTLES = "bouteilles"


def _plan_attrs(m: BarManager) -> dict[str, Any]:
    a = m.plan_analysis()
    return {
        "total_inventaire": a["total_inventaire"],
        "cases_total": a["cases_total"],
        "cases_libres": a["cases_libres"],
        "non_placees": len(a["non_placees"]),
        "suroccupees": len(a["suroccupees"]),
    }


DESCRIPTIONS: tuple[BarSensorDescription, ...] = (
    BarSensorDescription(
        key="bouteilles",
        translation_key="bouteilles",
        native_unit_of_measurement=BOTTLES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.stats()["bouteilles"],
        attrs_fn=lambda m: {"references": m.stats()["references"]},
    ),
    BarSensorDescription(
        key="valeur",
        translation_key="valeur",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=0,
        value_fn=lambda m: m.stats()["valeur"],
    ),
    BarSensorDescription(
        key="bouteilles_maison",
        translation_key="bouteilles_maison",
        native_unit_of_measurement=BOTTLES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.stats()["bouteilles_maison"],
    ),
    BarSensorDescription(
        key="valeur_maison",
        translation_key="valeur_maison",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=0,
        value_fn=lambda m: m.stats()["valeur_maison"],
    ),
    BarSensorDescription(
        key="bouteilles_industrielles",
        translation_key="bouteilles_industrielles",
        native_unit_of_measurement=BOTTLES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.stats()["bouteilles_industrielles"],
    ),
    BarSensorDescription(
        key="valeur_industrielle",
        translation_key="valeur_industrielle",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=0,
        value_fn=lambda m: m.stats()["valeur_industrielle"],
    ),
    BarSensorDescription(
        key="plan",
        translation_key="plan",
        native_unit_of_measurement=BOTTLES,
        value_fn=lambda m: m.plan_analysis()["total_plan"],
        attrs_fn=_plan_attrs,
    ),
    BarSensorDescription(
        key="statut_ia",
        translation_key="statut_ia",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_IDLE, STATUS_RUNNING, STATUS_ERROR],
        value_fn=lambda m: m.status,
        attrs_fn=lambda m: {"tache": m.running_task, "derniere_erreur": m.last_error},
    ),
    *(
        BarSensorDescription(
            key=f"categorie_{cat}",
            translation_key=f"categorie_{cat}",
            native_unit_of_measurement=BOTTLES,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda m, c=cat: m.stats()["categories"][c],
        )
        for cat in CATEGORIES
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(BarSensor(manager, entry.entry_id, d) for d in DESCRIPTIONS)


class BarSensor(SensorEntity):
    """Capteur alimenté par le gestionnaire du bar."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: BarSensorDescription

    def __init__(self, manager: BarManager, entry_id: str, description: BarSensorDescription) -> None:
        self._manager = manager
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Bar IA",
            manufacturer="Bar IA",
            model="Gestionnaire de bar",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._manager)
