"""Images des bouteilles et recherche par photo."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.bar_ia import image_finder
from custom_components.bar_ia.images import ImageError, decode_jpeg, valid_name, valid_url

from .test_init import FICHE, _setup, _svc

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
JPEG_B64 = base64.b64encode(JPEG).decode()


def test_decode_and_validation():
    assert decode_jpeg(JPEG_B64) == JPEG
    assert decode_jpeg("data:image/jpeg;base64," + JPEG_B64) == JPEG
    for bad in ("", "pas du base64!!", base64.b64encode(b"\x89PNG....").decode(),
                "data:image/png;base64," + JPEG_B64):
        with pytest.raises(ImageError):
            decode_jpeg(bad)
    with pytest.raises(ImageError):
        decode_jpeg(base64.b64encode(b"\xff\xd8\xff" + b"0" * 2_100_000).decode())
    assert valid_name("spirit_abc-1") and not valid_name("../etc/passwd") and not valid_name("a/b")
    assert valid_url("https://exemple.fr/a.jpg")
    for bad in ("http://exemple.fr/a.jpg", "javascript:alert(1)", "https://x.fr/\"onerror=", "data:image/png,"):
        assert not valid_url(bad)


async def _bottle(hass, ai_calls):
    _, responses = ai_calls
    responses["fiche"] = json.dumps(FICHE)
    entry = await _setup(hass, ai_calls)
    await _svc(hass, "search", {"nom": "Diplomatico", "type": "Rhum"})
    sid = (await _svc(hass, "add_to_inventory", response=True))["spirit_id"]
    return entry, sid


async def test_upload_serve_url_and_delete(
    hass: HomeAssistant, ai_calls, hass_ws_client, hass_client, hass_client_no_auth, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry, sid = await _bottle(hass, ai_calls)
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "bar_ia/upload_image", "spirit_id": sid, "data": "abcd"})
    res = await ws.receive_json()
    assert not res["success"]
    assert "Encodage" in res["error"]["message"] or "JPEG" in res["error"]["message"]

    await ws.send_json({"id": 2, "type": "bar_ia/upload_image", "spirit_id": sid, "data": JPEG_B64})
    assert (await ws.receive_json())["success"]
    file = Path(tmp_path, "bar_ia", "images", f"{sid}.jpg")
    assert file.read_bytes() == JPEG
    img = entry.runtime_data.snapshot()["spirits"][0]["image"]
    assert img["type"] == "local" and img["path"].startswith(f"/api/bar_ia/image/{sid}.jpg?v=")

    # Route authentifiée
    anon = await hass_client_no_auth()
    assert (await anon.get(f"/api/bar_ia/image/{sid}.jpg")).status == 401
    client = await hass_client()
    resp = await client.get(f"/api/bar_ia/image/{sid}.jpg")
    assert resp.status == 200 and await resp.read() == JPEG
    assert (await client.get("/api/bar_ia/image/..%2Fsecret.jpg")).status in (400, 404)
    assert (await client.get("/api/bar_ia/image/inconnu.jpg")).status == 404

    # Lien web : https seulement ; l'image est téléchargée et stockée localement
    with pytest.raises(ServiceValidationError):
        await _svc(hass, "set_image_url", {"spirit_id": sid, "url": "http://x.fr/a.jpg"})
    with patch.object(image_finder, "download", AsyncMock(return_value=b"\xff\xd8\xffNEW")):
        await _svc(hass, "set_image_url", {"spirit_id": sid, "url": "https://x.fr/a.jpg"})
    img = entry.runtime_data.snapshot()["spirits"][0]["image"]
    assert img["type"] == "local" and img["source"] == "https://x.fr/a.jpg"
    assert file.read_bytes() == b"\xff\xd8\xffNEW"
    with patch.object(image_finder, "download", AsyncMock(side_effect=ImageError("Le site a répondu 403"))), \
            pytest.raises(ServiceValidationError):
        await _svc(hass, "set_image_url", {"spirit_id": sid, "url": "https://x.fr/b.jpg"})

    await _svc(hass, "remove_image", {"spirit_id": sid})
    assert entry.runtime_data.snapshot()["spirits"][0]["image"] is None

    # Suppression de la bouteille -> fichier supprimé
    await ws.send_json({"id": 3, "type": "bar_ia/upload_image", "spirit_id": sid, "data": JPEG_B64})
    assert (await ws.receive_json())["success"]
    await _svc(hass, "delete_spirit", {"spirit_id": sid})
    assert not file.exists()


async def test_search_by_photo(hass: HomeAssistant, ai_calls, hass_ws_client, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    calls, responses = ai_calls
    responses["fiche"] = json.dumps(FICHE)
    entry = await _setup(hass, ai_calls)
    photo_entity = entry.runtime_data.photo_entity_id
    assert photo_entity and photo_entity.startswith("image.")

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "bar_ia/search_photo", "data": JPEG_B64, "pays": "Venezuela"})
    assert (await ws.receive_json())["success"]
    call = calls[-1]
    assert call["attachments"] == [
        {"media_content_id": f"media-source://image/{photo_entity}", "media_content_type": "image/jpeg"}
    ]
    assert "PHOTO de la bouteille est jointe" in call["instructions"]
    search = entry.runtime_data.snapshot()["search"]
    assert search["image"]["path"].startswith("/api/bar_ia/image/_search.jpg")

    # L'entité image fournit bien la photo à la Tâche IA
    from homeassistant.components.image import async_get_image

    assert (await async_get_image(hass, photo_entity)).content == JPEG

    # La photo devient l'image de la bouteille ajoutée
    sid = (await _svc(hass, "add_to_inventory", response=True))["spirit_id"]
    assert Path(tmp_path, "bar_ia", "images", f"{sid}.jpg").read_bytes() == JPEG
    assert entry.runtime_data.snapshot()["spirits"][0]["image"]["type"] == "local"

    # Photo non reconnue -> erreur claire
    responses["fiche"] = json.dumps({"nom": {"valeur": "-"}})
    await ws.send_json({"id": 2, "type": "bar_ia/search_photo", "data": JPEG_B64})
    res = await ws.receive_json()
    assert not res["success"]
    assert hass.states.get("sensor.bar_ia_ai_status").attributes["derniere_erreur"] == (
        "Bouteille non reconnue sur la photo"
    )
