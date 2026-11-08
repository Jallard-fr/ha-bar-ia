"""Recherche automatique d'images (sources, téléchargement contrôlé, vérification IA)."""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from PIL import Image

from custom_components.bar_ia import image_finder, logic
from custom_components.bar_ia.images import ImageError

from .test_init import FICHE, _setup, _svc

OFF = "https://world.openfoodfacts.org/cgi/search.pl"
IMG_OK = "https://images.openfoodfacts.org/diplo.png"
IMG_AUTRE = "https://images.openfoodfacts.org/autre.png"


def _png(w=400, h=600, color=(120, 60, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def public_hosts():
    async def _public(hass, host):
        return not host.startswith(("192.168.", "127.", "localhost"))

    with patch.object(image_finder, "_host_is_public", _public), patch(
        "custom_components.bar_ia.manager.JOB_DELAY", 0
    ):
        yield


def test_logic_helpers():
    q = logic.image_query(FICHE)
    assert q["marque"] == "Diplomatico" and logic.image_search_text(q) == "Diplomatico Reserva Exclusiva"
    assert logic.image_urls({"images": ["https://a.fr/x.jpg", "http://b.fr/y.jpg", "javascript:x", "https://a.fr/x.jpg"]}) == [
        "https://a.fr/x.jpg"
    ]
    assert logic.image_matches({"correspond": True, "confiance": 85})
    assert not logic.image_matches({"correspond": True, "confiance": 30})
    assert not logic.image_matches({"correspond": "non", "confiance": 99})
    assert "IMAGE est jointe" in logic.prompt_verify_image(q)


def test_to_jpeg():
    out = image_finder.to_jpeg(_png(1600, 2400))
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "JPEG" and max(img.size) == 800
    with pytest.raises(ImageError):
        image_finder.to_jpeg(_png(50, 50))
    with pytest.raises(ImageError):
        image_finder.to_jpeg(b"<html>pas une image</html>")


async def test_download_rules(hass: HomeAssistant, aioclient_mock) -> None:
    with pytest.raises(ImageError):
        await image_finder.download(hass, "http://images.openfoodfacts.org/a.png")
    with pytest.raises(ImageError):
        await image_finder.download(hass, "https://192.168.1.10/a.png")  # réseau local refusé
    aioclient_mock.get("https://site.fr/page", text="<html>", headers={"Content-Type": "text/html"})
    with pytest.raises(ImageError):
        await image_finder.download(hass, "https://site.fr/page")
    aioclient_mock.get(IMG_OK, content=_png(), headers={"Content-Type": "image/png"})
    assert (await image_finder.download(hass, IMG_OK)).startswith(b"\xff\xd8\xff")


def _off(aioclient_mock, products):
    aioclient_mock.get(OFF, json={"products": products})
    aioclient_mock.get("https://commons.wikimedia.org/w/api.php", json={})


async def test_auto_image_on_search(hass: HomeAssistant, ai_calls, aioclient_mock, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    calls, responses = ai_calls
    _off(aioclient_mock, [
        {"product_name": "Rhum Reserva Exclusiva", "brands": "Diplomatico", "image_front_url": IMG_AUTRE},
        {"product_name": "Biscuits", "brands": "Lu", "image_front_url": "https://images.openfoodfacts.org/lu.png"},
    ])
    aioclient_mock.get(IMG_AUTRE, content=_png(color=(1, 2, 3)), headers={"Content-Type": "image/png"})
    aioclient_mock.get(IMG_OK, content=_png(), headers={"Content-Type": "image/png"})
    responses["fiche"] = json.dumps({**FICHE, "images": [IMG_OK]})
    verdicts = iter([{"correspond": False, "confiance": 90}, {"correspond": True, "confiance": 90}])

    entry = await _setup(hass, ai_calls)
    orig = entry.runtime_data._async_ai

    async def ai(task, instructions, attachments=None, internal=False):
        if task == "verify":
            calls.append({"task": task, "attachments": attachments})
            return next(verdicts)
        return await orig(task, instructions, attachments, internal)

    entry.runtime_data._async_ai = ai
    await _svc(hass, "search", {"nom": "Diplomatico", "type": "Rhum"})
    await hass.async_block_till_done(wait_background_tasks=True)

    snap = entry.runtime_data.snapshot()
    assert snap["job"] is None
    assert snap["search"]["image"]["source"] == IMG_OK  # 1re image refusée par l'IA, 2e (lien IA) retenue
    assert [c["task"] for c in calls if "task" in c] == ["verify", "verify"]
    assert all("media-source://image/" in c["attachments"][0]["media_content_id"] for c in calls if "task" in c)
    assert not any("lu.png" in str(r[1]) for r in aioclient_mock.mock_calls)  # produit non pertinent ignoré

    sid = (await _svc(hass, "add_to_inventory", response=True))["spirit_id"]
    assert Path(tmp_path, "bar_ia", "images", f"{sid}.jpg").exists()
    assert entry.runtime_data.spirits[sid]["image"]["source"] == IMG_OK


async def test_bulk_missing_images(hass: HomeAssistant, ai_calls, aioclient_mock, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    _, responses = ai_calls
    _off(aioclient_mock, [{"product_name": "Reserva Exclusiva", "brands": "Diplomatico", "image_front_url": IMG_OK}])
    aioclient_mock.get(IMG_OK, content=_png(), headers={"Content-Type": "image/png"})
    entry = await _setup(hass, ai_calls)
    m = entry.runtime_data
    m.spirits.update({
        "a": {"nom": {"valeur": "Diplomatico Reserva Exclusiva"}, "marque_distillerie": {"valeur": "Diplomatico"},
              "type": {"valeur": "Rhum"}, "nombre_bouteilles": 1},
        "b": {"nom": {"valeur": "Rhum Inconnu Confidentiel"}, "type": {"valeur": "Rhum"}, "nombre_bouteilles": 1},
        "c": {"nom": {"valeur": "Avec image"}, "nombre_bouteilles": 1, "image": {"type": "url", "url": "https://x.fr/a.jpg"}},
        "d": {"nom": {"valeur": "Limoncello"}, "nombre_bouteilles": 1, "maison": {"est_maison": True}},
    })

    async def ai(task, instructions, attachments=None, internal=False):
        if task == "verify":
            return {"correspond": "Diplomatico" in instructions, "confiance": 90}
        if task == "image_urls":
            return {"images": []}
        raise AssertionError(task)

    m._async_ai = ai
    res = await _svc(hass, "find_missing_images", response=True)
    assert res == {"total": 2}  # ni "c" (déjà une image) ni "d" (maison)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert m.job is None
    assert m.spirits["a"]["image"]["source"] == IMG_OK
    assert "image" not in m.spirits["b"]
    assert m.spirits["c"]["image"]["type"] == "url"

    # Bouteille par bouteille
    del m.spirits["a"]["image"]
    assert (await _svc(hass, "find_image", {"spirit_id": "a"}, response=True)) == {"trouvee": True}
