"""Enregistrement automatique de la carte comme ressource Lovelace."""

from types import SimpleNamespace

from homeassistant.core import HomeAssistant

from custom_components.bar_ia.frontend_resource import CARD_URL, async_register_card_resource


class FakeResources:
    def __init__(self, items):
        self.items = items
        self.loaded = False

    async def async_load(self):
        pass

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        self.items.append({"id": "new", "url": data["url"], "type": data["res_type"]})

    async def async_update_item(self, item_id, data):
        for item in self.items:
            if item["id"] == item_id:
                item["url"] = data["url"]


async def test_creates_resource(hass: HomeAssistant) -> None:
    res = FakeResources([{"id": "a", "url": "/local/autre.js"}])
    hass.data["lovelace"] = SimpleNamespace(resources=res)
    await async_register_card_resource(hass)
    assert [i["url"] for i in res.items] == ["/local/autre.js", CARD_URL]
    # Idempotent
    await async_register_card_resource(hass)
    assert len(res.items) == 2


async def test_updates_old_version(hass: HomeAssistant) -> None:
    res = FakeResources([{"id": "a", "url": "/bar_ia_static/bar-ia-card.js?v=2.0.0"}])
    hass.data["lovelace"] = SimpleNamespace(resources=res)
    await async_register_card_resource(hass)
    assert res.items == [{"id": "a", "url": CARD_URL}]


async def test_yaml_mode_or_missing_is_noop(hass: HomeAssistant) -> None:
    hass.data.pop("lovelace", None)
    await async_register_card_resource(hass)
    hass.data["lovelace"] = SimpleNamespace(resources=SimpleNamespace(async_items=lambda: []))
    await async_register_card_resource(hass)
