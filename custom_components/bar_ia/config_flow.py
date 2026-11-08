"""Assistant de configuration Bar IA (100 % interface graphique)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_AI_ENTITY, DOMAIN


def _schema(default: str | None) -> vol.Schema:
    key = (
        vol.Optional(CONF_AI_ENTITY, description={"suggested_value": default})
        if default
        else vol.Optional(CONF_AI_ENTITY)
    )
    return vol.Schema(
        {key: selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))}
    )


class BarIAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Création de l'entrée Bar IA."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Bar IA", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(None))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return BarIAOptionsFlow()


class BarIAOptionsFlow(OptionsFlow):
    """Permet de changer l'entité IA après installation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_AI_ENTITY, self.config_entry.data.get(CONF_AI_ENTITY)
        )
        return self.async_show_form(step_id="init", data_schema=_schema(current))
