"""Copie lisible des images dans /media/bar_ia et conversion des anciens liens."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.bar_ia import image_finder
from custom_components.bar_ia.images import ImageError

from .test_init import _setup, _svc

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 100


def _bottle(nom, annee="-", image=None):
    info = {"nom": {"valeur": nom}, "annee": {"valeur": annee}, "type": {"valeur": "Rhum"}, "nombre_bouteilles": 1}
    if image:
        info["image"] = image
    return info


async def test_readable_copies(hass: HomeAssistant, ai_calls, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path / "config")
    media = tmp_path / "media"
    hass.config.media_dirs = {"local": str(media)}
    entry = await _setup(hass, ai_calls)
    m = entry.runtime_data
    copies = media / "bar_ia"
    copies.mkdir(parents=True)
    (copies / "photo perso.jpg").write_bytes(b"ne pas toucher")

    for sid in ("a", "b", "c"):
        await m.images.async_save(sid, JPEG)
    m.spirits.update({
        "a": _bottle("Diplomatico: Reserva", "2012", {"type": "local", "v": 1}),
        "b": _bottle("Clément"),
        "c": _bottle("Clément", image={"type": "local", "v": 1}),
    })
    m.spirits["b"]["image"] = {"type": "local", "v": 1}
    await m._async_commit()
    assert sorted(p.name for p in copies.iterdir()) == [
        "Clément #2.jpg", "Clément.jpg", "Diplomatico Reserva (2012).jpg", "photo perso.jpg"
    ]
    assert (copies / "Clément.jpg").read_bytes() == JPEG
    assert m.snapshot()["spirits"][0]["copie"]

    # Renommage de la bouteille -> fichier renommé
    await _svc(hass, "update_spirit", {"spirit_id": "a", "nom": "Diplomatico Reserva Exclusiva"})
    assert (copies / "Diplomatico Reserva Exclusiva (2012).jpg").exists()
    assert not (copies / "Diplomatico Reserva (2012).jpg").exists()

    # Nouvelle photo -> copie remplacée
    await m.async_set_image_upload("a", JPEG + b"v2")
    assert (copies / "Diplomatico Reserva Exclusiva (2012).jpg").read_bytes() == JPEG + b"v2"

    # Suppression d'image / de bouteille -> copie supprimée ; fichier personnel intact
    await _svc(hass, "remove_image", {"spirit_id": "c"})
    await _svc(hass, "delete_spirit", {"spirit_id": "a"})
    assert sorted(p.name for p in copies.iterdir()) == ["Clément.jpg", "photo perso.jpg"]
    assert (copies / "photo perso.jpg").read_bytes() == b"ne pas toucher"


async def test_localize_old_links(hass: HomeAssistant, ai_calls, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path / "config")
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    entry = await _setup(hass, ai_calls)
    m = entry.runtime_data
    m.spirits.update({
        "ok": _bottle("Tanqueray", image={"type": "url", "url": "https://x.fr/ok.jpg"}),
        "mort": _bottle("Ancienne", image={"type": "url", "url": "https://x.fr/404.jpg"}),
    })

    async def fake(hass_, url):
        if "404" in url:
            raise ImageError("Le site a répondu 404")
        return JPEG

    with patch.object(image_finder, "download", AsyncMock(side_effect=fake)):
        assert await m.async_localize_links() == {"converties": 1, "echecs": 1}
    assert m.spirits["ok"]["image"]["type"] == "local"
    assert m.spirits["ok"]["image"]["source"] == "https://x.fr/ok.jpg"
    assert m.spirits["mort"]["image"]["type"] == "url"  # lien conservé
    assert Path(tmp_path, "media", "bar_ia", "Tanqueray.jpg").read_bytes() == JPEG
