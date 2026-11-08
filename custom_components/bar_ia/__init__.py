"""Bar IA : gestionnaire de bar piloté par IA pour Home Assistant.

L'intégration remplace l'ancien package YAML + scripts Python :
- aucune copie manuelle de fichiers, aucun jeton d'accès à créer ;
- inventaire et plan stockés dans .storage (persistants) ;
- services natifs (plus de shell_command) ;
- carte Lovelace servie et chargée automatiquement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.persistent_notification import async_create as notify
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_FILENAME,
    CONF_AI_ENTITY,
    DOMAIN,
    MAX_COLS,
    MAX_SHELVES,
    SIGNAL_UPDATED,
    STATIC_URL,
    VERSION,
)
from .frontend_resource import async_register_card_resource
from .logic import CATEGORIES, TASTING_CRITERIA
from .manager import BarManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BarConfigEntry = ConfigEntry[BarManager]

_TEXT = vol.All(cv.string, vol.Length(max=255))
_SPIRIT_ID = vol.All(cv.string, vol.Length(min=1, max=64))
_CELL = vol.All(cv.string, vol.Match(r"^[Ee]\d{1,2}-\d{1,2}$"))
_POS = vol.All(vol.Coerce(float), vol.Range(min=0, max=100000))
_NOTE = vol.All(vol.Coerce(float), vol.Range(min=0, max=10))

SERVICE_SCHEMAS: dict[str, vol.Schema] = {
    "search": vol.Schema(
        {
            vol.Required("nom"): vol.All(cv.string, vol.Length(min=1, max=255)),
            vol.Optional("type", default=""): _TEXT,
            vol.Optional("annee", default=""): vol.All(cv.string, vol.Length(max=20)),
            vol.Optional("maison", default=False): cv.boolean,
            vol.Optional("base_utilisee", default=""): vol.All(cv.string, vol.Length(max=50)),
            vol.Optional("ingredients", default=""): _TEXT,
            vol.Optional("abv_base", default=0): vol.All(vol.Coerce(float), vol.Range(0, 96)),
            vol.Optional("volume_base_ml", default=0): vol.All(vol.Coerce(float), vol.Range(0, 20000)),
            vol.Optional("volume_eau_ml", default=0): vol.All(vol.Coerce(float), vol.Range(0, 20000)),
            vol.Optional("sucre_g", default=0): vol.All(vol.Coerce(float), vol.Range(0, 20000)),
            vol.Optional("coef_sucre_ml_par_g", default=0.62): vol.All(vol.Coerce(float), vol.Range(0, 2)),
            vol.Optional("prix_base_litre", default=0): vol.All(vol.Coerce(float), vol.Range(0, 1000)),
            vol.Optional("volume_bouteille_ml", default=0): vol.All(vol.Coerce(float), vol.Range(0, 20000)),
        }
    ),
    "add_to_inventory": vol.Schema(
        {vol.Optional("quantite", default=1): vol.All(vol.Coerce(int), vol.Range(1, 100))}
    ),
    "adjust_stock": vol.Schema(
        {
            vol.Required("spirit_id"): _SPIRIT_ID,
            vol.Optional("delta", default=1): vol.All(vol.Coerce(int), vol.Range(-100, 100)),
        }
    ),
    "set_stock": vol.Schema(
        {
            vol.Required("spirit_id"): _SPIRIT_ID,
            vol.Required("quantite"): vol.All(vol.Coerce(int), vol.Range(0, 1000)),
        }
    ),
    "delete_spirit": vol.Schema({vol.Required("spirit_id"): _SPIRIT_ID}),
    "cocktail": vol.Schema(
        {
            vol.Optional("spirit_id"): vol.Any(None, "", _SPIRIT_ID),
            vol.Optional("type_alcool", default=""): vol.All(cv.string, vol.Length(max=50)),
            vol.Optional("ingredients", default=""): _TEXT,
            vol.Optional("occasion", default=""): _TEXT,
        }
    ),
    "audit": vol.Schema({}),
    "purchase_check": vol.Schema(
        {
            vol.Required("nom"): vol.All(cv.string, vol.Length(min=1, max=255)),
            vol.Optional("type", default=""): _TEXT,
            vol.Optional("prix"): vol.Any(None, _POS),
            vol.Optional("budget_max"): vol.Any(None, _POS),
        }
    ),
    "clear_result": vol.Schema(
        {
            vol.Required("resultat"): vol.In(
                ["search", "cocktail", "audit", "purchase", *(f"profil_{c}" for c in CATEGORIES)]
            )
        }
    ),
    "add_tasting": vol.Schema(
        {
            vol.Required("spirit_id"): _SPIRIT_ID,
            **{vol.Optional(c): vol.Any(None, _NOTE) for c in TASTING_CRITERIA},
            vol.Optional("commentaire", default=""): vol.All(cv.string, vol.Length(max=1000)),
            vol.Optional("date"): cv.date,
        }
    ),
    "delete_tasting": vol.Schema(
        {
            vol.Required("spirit_id"): _SPIRIT_ID,
            vol.Required("degustation_id"): vol.All(cv.string, vol.Length(min=1, max=32)),
        }
    ),
    "taste_profile": vol.Schema({vol.Optional("categorie", default="rhum"): vol.In(list(CATEGORIES))}),
    "place": vol.Schema({vol.Required("case"): _CELL, vol.Required("spirit_id"): _SPIRIT_ID}),
    "clear_cell": vol.Schema({vol.Required("case"): _CELL}),
    "autofill": vol.Schema({}),
    "empty_plan": vol.Schema({}),
    "set_layout": vol.Schema(
        {
            vol.Required("etageres"): vol.All(vol.Coerce(int), vol.Range(1, MAX_SHELVES)),
            vol.Required("colonnes"): vol.All(vol.Coerce(int), vol.Range(1, MAX_COLS)),
        }
    ),
    "import_legacy": vol.Schema({vol.Optional("force", default=False): cv.boolean}),
}

RESPONSE_SERVICES = {
    "search",
    "add_to_inventory",
    "cocktail",
    "audit",
    "purchase_check",
    "import_legacy",
    "add_tasting",
    "taste_profile",
}


def _get_manager(hass: HomeAssistant) -> BarManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Enregistre carte, websocket et services (une seule fois)."""
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL,
                str(Path(__file__).parent / "frontend"),
                cache_headers=False,
            )
        ]
    )
    # Charge la carte dans toutes les interfaces : pas de ressource à déclarer.
    add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILENAME}?v={VERSION}")

    websocket_api.async_register_command(hass, ws_subscribe)
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    async def handle(call: ServiceCall) -> ServiceResponse:
        manager = _get_manager(hass)
        d = call.data
        result: Any = None
        match call.service:
            case "search":
                maison = None
                if d["maison"]:
                    maison = {
                        k: d[k]
                        for k in (
                            "base_utilisee",
                            "ingredients",
                            "abv_base",
                            "volume_base_ml",
                            "volume_eau_ml",
                            "sucre_g",
                            "coef_sucre_ml_par_g",
                            "prix_base_litre",
                            "volume_bouteille_ml",
                        )
                    }
                result = await manager.async_search(d["nom"], d["type"], d["annee"], maison)
            case "add_to_inventory":
                result = {"spirit_id": await manager.async_add_from_search(d["quantite"])}
            case "adjust_stock":
                await manager.async_adjust_stock(d["spirit_id"], d["delta"])
            case "set_stock":
                await manager.async_set_stock(d["spirit_id"], d["quantite"])
            case "delete_spirit":
                await manager.async_delete(d["spirit_id"])
            case "cocktail":
                result = await manager.async_cocktail(
                    d.get("spirit_id") or None, d["type_alcool"], d["ingredients"], d["occasion"]
                )
            case "audit":
                result = await manager.async_audit()
            case "purchase_check":
                result = await manager.async_purchase(
                    d["nom"], d["type"], d.get("prix"), d.get("budget_max")
                )
            case "clear_result":
                await manager.async_clear_result(d["resultat"])
            case "place":
                await manager.async_place(d["case"], d["spirit_id"])
            case "clear_cell":
                await manager.async_clear_cell(d["case"])
            case "autofill":
                await manager.async_autofill()
            case "empty_plan":
                await manager.async_empty_plan()
            case "set_layout":
                await manager.async_set_layout(d["etageres"], d["colonnes"])
            case "add_tasting":
                params = {c: d.get(c) for c in TASTING_CRITERIA}
                params["commentaire"] = d["commentaire"]
                if d.get("date"):
                    params["date"] = d["date"].isoformat()
                result = await manager.async_add_tasting(d["spirit_id"], params)
            case "delete_tasting":
                await manager.async_delete_tasting(d["spirit_id"], d["degustation_id"])
            case "taste_profile":
                result = await manager.async_taste_profile(d["categorie"])
            case "import_legacy":
                result = await manager.async_import_legacy(force=d["force"])
        if call.service in RESPONSE_SERVICES and call.return_response:
            return result
        return None

    for name, schema in SERVICE_SCHEMAS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=schema,
            supports_response=(
                SupportsResponse.OPTIONAL if name in RESPONSE_SERVICES else SupportsResponse.NONE
            ),
        )


async def async_setup_entry(hass: HomeAssistant, entry: BarConfigEntry) -> bool:
    """Initialise le gestionnaire pour l'entrée de configuration."""
    ai_entity = entry.options.get(CONF_AI_ENTITY, entry.data.get(CONF_AI_ENTITY))
    manager = BarManager(hass, ai_entity)
    await manager.async_load()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Déclare la carte comme ressource du tableau de bord (comme le ferait HACS).
    async def _register_card(_hass: HomeAssistant) -> None:
        await async_register_card_resource(hass)

    entry.async_on_unload(async_at_started(hass, _register_card))
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    if not manager.data.get("migrated"):

        async def _migrate(_hass: HomeAssistant) -> None:
            result = await manager.async_import_legacy()
            if result["source"]:
                notify(
                    hass,
                    (
                        f"{result['importees']} bouteille(s) et {result['cases']} case(s) du plan "
                        "ont été importées depuis l'ancienne version.\n\n"
                        "Vous pouvez maintenant supprimer `packages/bar_cellier_ia_ultra.yaml`, "
                        "`generate_bar_plan.py`, `bar_plan.json`, `www/bar_plan.html` et les "
                        "scripts `python_scripts/bar_*.py`, puis **révoquer le jeton d'accès "
                        "longue durée** qui y était inscrit."
                    ),
                    title="Bar IA : migration terminée",
                    notification_id=f"{DOMAIN}_migration",
                )

        entry.async_on_unload(async_at_started(hass, _migrate))

    return True


async def _async_reload(hass: HomeAssistant, entry: BarConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BarConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@websocket_api.async_response
async def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Envoie l'état complet du bar puis chaque mise à jour."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "Bar IA n'est pas configuré")
        return

    @callback
    def _send() -> None:
        current = hass.config_entries.async_loaded_entries(DOMAIN)
        if current:
            connection.send_message(
                websocket_api.event_message(msg["id"], current[0].runtime_data.snapshot())
            )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATED, _send)
    connection.send_result(msg["id"])
    _send()
