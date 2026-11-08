"""Fixtures communes."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Active le chargement de custom_components/ dans les tests."""
    yield


@pytest.fixture(autouse=True)
async def light_dependencies(hass: HomeAssistant):
    """Marque frontend et ai_task comme chargés (paquets lourds absents en test).

    Le service ai_task.generate_data est simulé dans chaque test.
    """
    assert await async_setup_component(hass, "http", {})
    hass.config.components.update({"frontend", "ai_task"})
    with patch("custom_components.bar_ia.add_extra_js_url") as js:
        yield js
