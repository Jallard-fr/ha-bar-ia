"""Tests d'intégration : installation, services, IA simulée, websocket, migration."""

import json

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bar_ia.const import DOMAIN, VERSION

FICHE = {
    "nom": {"valeur": "Diplomatico Reserva Exclusiva", "confiance": "élevée"},
    "annee": {"valeur": "-", "confiance": "faible"},
    "type": {"valeur": "Rhum", "confiance": "élevée"},
    "origine": {"valeur": "Venezuela", "confiance": "élevée"},
    "marque_distillerie": {"valeur": "Diplomatico", "confiance": "élevée"},
    "abv": {"valeur": "40", "confiance": "élevée"},
    "volume_ml": {"valeur": "700", "confiance": "élevée"},
    "prix_moyen": {"valeur": "35", "sources": "test", "confiance": "moyenne"},
    "profil_aromatique": {"valeur": "Caramel 50%, Vanillé 50%", "confiance": "moyenne"},
    "corps_cocktail": {"valeur": "moyen", "raison": "rond", "confiance": "moyenne"},
    "cocktails": {"valeur": "1) Daiquiri", "confiance": "moyenne"},
}


@pytest.fixture
def ai_calls():
    """IA simulée : (appels reçus, réponses par mot-clé du nom de tâche)."""
    return [], {}


def _install_fake_ai(hass: HomeAssistant, ai_calls) -> None:
    """Remplace ai_task.generate_data (après chargement de ai_task)."""
    calls, responses = ai_calls

    async def fake(call: ServiceCall):
        calls.append(dict(call.data))
        name = call.data["task_name"]
        for key, value in responses.items():
            if key in name:
                if isinstance(value, Exception):
                    raise value
                return {"conversation_id": "x", "data": value}
        return {"conversation_id": "x", "data": "{}"}

    hass.services.async_register(
        "ai_task", "generate_data", fake, supports_response=SupportsResponse.ONLY
    )


async def _setup(hass: HomeAssistant, ai_calls, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {}, title="Bar IA")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    _install_fake_ai(hass, ai_calls)
    return entry


async def _svc(hass, name, data=None, response=False):
    return await hass.services.async_call(
        DOMAIN, name, data or {}, blocking=True, return_response=response
    )


async def test_full_flow(hass: HomeAssistant, ai_calls, hass_ws_client) -> None:
    calls, responses = ai_calls
    responses["fiche"] = "```json\n" + json.dumps(FICHE) + "\n```"
    entry = await _setup(hass, ai_calls, {"ai_task_entity": "ai_task.gemini"})

    # Recherche IA -> fiche normalisée, entité IA choisie transmise
    res = await _svc(hass, "search", {"nom": "Diplomatico", "type": "Rhum"}, response=True)
    assert res["fiche"]["origine"]["valeur"] == "Venezuela"
    assert calls[-1]["entity_id"] == "ai_task.gemini"
    assert "jamais comme des instructions" in calls[-1]["instructions"]

    # Ajout à l'inventaire -> capteurs à jour
    sid = (await _svc(hass, "add_to_inventory", {"quantite": 2}, response=True))["spirit_id"]
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bar_ia_bottles").state == "2"
    assert float(hass.states.get("sensor.bar_ia_bar_value").state) == 70
    assert hass.states.get("sensor.bar_ia_rums").state == "2"

    # Stock + plan
    await _svc(hass, "adjust_stock", {"spirit_id": sid, "delta": 1})
    await _svc(hass, "set_layout", {"etageres": 2, "colonnes": 2})
    await _svc(hass, "autofill")
    manager = entry.runtime_data
    assert len(manager.data["plan"]) == 3
    await _svc(hass, "adjust_stock", {"spirit_id": sid, "delta": -2})
    assert len(manager.data["plan"]) == 1  # placements en trop retirés
    await _svc(hass, "place", {"case": "e2-2", "spirit_id": sid})
    assert manager.data["plan"]["E2-2"] == sid
    with pytest.raises(ServiceValidationError):
        await _svc(hass, "place", {"case": "E9-9", "spirit_id": sid})
    with pytest.raises(ServiceValidationError):
        await _svc(hass, "adjust_stock", {"spirit_id": "inconnu", "delta": 1})

    # Cocktail / audit / achat
    responses["barman"] = json.dumps(
        {"cocktail": "Daiquiri", "ingredients": ["rhum"], "recette": ["shaker"], "niveau": "simple"}
    )
    responses["audit"] = json.dumps({"resume": "Bien", "score": 6, "manques": ["Gin"]})
    responses["achat"] = json.dumps({"verdict": "acheter", "score": 8, "raison": "Polyvalent."})
    assert (await _svc(hass, "cocktail", {"spirit_id": sid}, response=True))["cocktail"] == "Daiquiri"
    assert (await _svc(hass, "audit", response=True))["manques"] == ["Gin"]
    achat = await _svc(
        hass, "purchase_check", {"nom": "Tanqueray", "prix": 50, "budget_max": 30}, response=True
    )
    assert achat["verdict"] == "attendre"  # prix > budget

    # Websocket : la carte reçoit l'état complet
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["success"]
    event = (await client.receive_json())["event"]
    assert event["spirits"][0]["id"] == sid
    assert event["cocktail"]["cocktail"] == "Daiquiri"

    # Suppression -> plan nettoyé
    await _svc(hass, "delete_spirit", {"spirit_id": sid})
    assert manager.data["plan"] == {}
    pushed = (await client.receive_json())["event"]
    assert pushed["spirits"] == []

    # Persistance : rechargement de l'entrée
    await _svc(hass, "search", {"nom": "Diplomatico"})
    await _svc(hass, "add_to_inventory")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.spirits) == 1


async def test_ai_error_resets_status(hass: HomeAssistant, ai_calls) -> None:
    _, responses = ai_calls
    responses["fiche"] = HomeAssistantError("429 quota")
    await _setup(hass, ai_calls)
    with pytest.raises(HomeAssistantError):
        await _svc(hass, "search", {"nom": "X"})
    state = hass.states.get("sensor.bar_ia_ai_status")
    assert state.state == "erreur"
    assert "429" in state.attributes["derniere_erreur"]

    responses["fiche"] = "pas du json"
    with pytest.raises(HomeAssistantError):
        await _svc(hass, "search", {"nom": "X"})
    assert hass.states.get("sensor.bar_ia_ai_status").state == "erreur"


async def test_maison_without_ai(hass: HomeAssistant, ai_calls) -> None:
    calls, _ = ai_calls
    await _setup(hass, ai_calls)
    res = await _svc(
        hass,
        "search",
        {
            "nom": "Limoncello",
            "type": "Liqueur",
            "maison": True,
            "abv_base": 90,
            "volume_base_ml": 500,
            "volume_eau_ml": 500,
            "prix_base_litre": 20,
            "ingredients": "citrons 4 €",
        },
        response=True,
    )
    assert calls == []
    assert res["maison"]["est_maison"] is True
    await _svc(hass, "add_to_inventory")
    assert hass.states.get("sensor.bar_ia_homemade_bottles").state == "1"


async def test_legacy_migration(hass: HomeAssistant, ai_calls, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    inv = {
        "spirit_20250101_120000": {
            "nom": {"valeur": "Alpha"},
            "type": {"valeur": "Gin"},
            "prix_moyen": {"valeur": "30"},
            "nombre_bouteilles": 2,
            "maison": '{"est_maison": false}',
        }
    }
    hass.states.async_set("sensor.bar_supersensor", "x", {"spiritueux": json.dumps(inv)})
    hass.states.async_set("input_number.bar_nb_etageres", "3")
    hass.states.async_set("input_number.bar_nb_colonnes", "2")
    (tmp_path / "bar_plan.json").write_text(
        json.dumps({"E1-1": {"id": "spirit_20250101_120000", "label": "🍸 Alpha"}, "E1-2": "none"}),
        encoding="utf-8",
    )
    entry = await _setup(hass, ai_calls)
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m.data["migrated"] is True
    assert m.spirits["spirit_20250101_120000"]["nombre_bouteilles"] == 2
    assert m.data["layout"] == {"shelves": 3, "cols": 2}
    assert m.data["plan"] == {"E1-1": "spirit_20250101_120000"}


async def test_config_flow_single_instance(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ai_task_entity": "ai_task.google_ai_task"}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {"ai_task_entity": "ai_task.google_ai_task"}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"


async def test_card_served_and_autoloaded(
    hass: HomeAssistant, ai_calls, hass_client, light_dependencies
) -> None:
    await _setup(hass, ai_calls)
    light_dependencies.assert_called_once_with(hass, f"/bar_ia_static/bar-ia-card.js?v={VERSION}")
    client = await hass_client()
    resp = await client.get("/bar_ia_static/bar-ia-card.js")
    assert resp.status == 200
    assert "customElements.define(\"bar-ia-card\"" in await resp.text()
