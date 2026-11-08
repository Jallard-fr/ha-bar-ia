"""Gestionnaire central : stockage persistant, appels IA, plan de rangement."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import image_finder, logic
from .const import (
    DEFAULT_COLS,
    DEFAULT_SHELVES,
    DOMAIN,
    LEGACY_ATTR_INV,
    LEGACY_COLS_ENTITY,
    LEGACY_PLAN_FILE,
    LEGACY_SHELVES_ENTITY,
    LEGACY_SUPERSENSOR,
    MAX_COLS,
    MAX_SHELVES,
    SIGNAL_PHOTO,
    SIGNAL_UPDATED,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_RUNNING,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .images import SEARCH_IMAGE, ImageError, ImageStore, valid_url

_LOGGER = logging.getLogger(__name__)

JOB_DELAY = 2  # secondes entre deux bouteilles (quota IA)

AI_TASK_NAMES = {
    "search": "bar_ia fiche spiritueux",
    "cocktail": "bar_ia accord du barman",
    "audit": "bar_ia audit du bar",
    "purchase": "bar_ia opportunite achat",
    "profile": "bar_ia profil de degustation",
    "verify": "bar_ia verification image",
    "image_urls": "bar_ia recherche image",
}


def _default_data() -> dict[str, Any]:
    return {
        "spirits": {},
        "plan": {},
        "layout": {"shelves": DEFAULT_SHELVES, "cols": DEFAULT_COLS},
        "search": None,
        "cocktail": None,
        "audit": None,
        "purchase": None,
        "profils": {},
        "migrated": False,
    }


class BarManager:
    """Détient l'état du bar et applique toutes les opérations."""

    def __init__(self, hass: HomeAssistant, ai_entity: str | None) -> None:
        self.hass = hass
        self.ai_entity = ai_entity or None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = _default_data()
        self.revision = 0
        self.status = STATUS_IDLE
        self.running_task: str | None = None
        self.last_error: str | None = None
        self.images = ImageStore(hass)
        self.search_photo: bytes | None = None
        self.photo_entity_id: str | None = None
        self.job: dict[str, Any] | None = None
        self.last_job: dict[str, Any] | None = None
        self._find_stats: dict[str, int] = {}
        self._job_cancel = False
        self._search_image_task: asyncio.Task | None = None
        self._search_added_sid: str | None = None

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Charge les données depuis .storage."""
        stored = await self._store.async_load()
        data = _default_data()
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in data})
        if not isinstance(data["spirits"], dict):
            data["spirits"] = {}
        if not isinstance(data["plan"], dict):
            data["plan"] = {}
        if not isinstance(data["profils"], dict):
            data["profils"] = {}
        self.data = data
        self._sanitize_layout()

    async def _async_commit(self) -> None:
        """Sauvegarde puis notifie entités et cartes."""
        await self._store.async_save(self.data)
        self._notify()

    @callback
    def _notify(self) -> None:
        self.revision += 1
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # ------------------------------------------------------------------
    # Accesseurs
    # ------------------------------------------------------------------

    @property
    def spirits(self) -> dict[str, dict]:
        return self.data["spirits"]

    @property
    def shelves(self) -> int:
        return self.data["layout"]["shelves"]

    @property
    def cols(self) -> int:
        return self.data["layout"]["cols"]

    def stats(self) -> dict:
        return logic.compute_stats(self.spirits)

    def plan_analysis(self) -> dict:
        return logic.analyze_plan(self.spirits, self.data["plan"], self.shelves, self.cols)

    def snapshot(self) -> dict[str, Any]:
        """État complet envoyé à la carte Lovelace."""
        labels = logic.labels_by_id(self.spirits)
        spirits = []
        for sid, info in self.spirits.items():
            spirits.append(
                {
                    "id": sid,
                    "label": labels.get(sid, sid),
                    "emoji": logic.emoji_for(info),
                    "categorie": logic.category_for(info),
                    "maison": logic.is_maison(info),
                    "quantite": logic.quantity(info),
                    "degustation": logic.tasting_summary(info),
                    "prix": logic.price(info),
                    "fiche": info,
                    "image": self._image_view(sid, info.get("image")),
                }
            )
        spirits.sort(key=lambda s: s["label"].lower())
        return {
            "revision": self.revision,
            "status": self.status,
            "running_task": self.running_task,
            "job": self.job,
            "last_job": self.last_job,
            "last_error": self.last_error,
            "ai_entity": self.ai_entity,
            "layout": dict(self.data["layout"]),
            "plan": dict(self.data["plan"]),
            "spirits": spirits,
            "stats": self.stats(),
            "plan_analysis": self.plan_analysis(),
            "search": (
                {**self.data["search"], "image": self._image_view(SEARCH_IMAGE, self.data["search"].get("image"))}
                if self.data["search"]
                else None
            ),
            "cocktail": self.data["cocktail"],
            "audit": self.data["audit"],
            "purchase": self.data["purchase"],
            "profils": self.data["profils"],
        }

    @staticmethod
    def _image_view(name: str, image: Any) -> dict | None:
        """Ce que la carte doit afficher pour une image stockée."""
        if not isinstance(image, dict):
            return None
        if image.get("type") == "url" and valid_url(str(image.get("url", ""))):
            return {"type": "url", "url": image["url"]}
        if image.get("type") == "local":
            return {
                "type": "local",
                "path": f"/api/bar_ia/image/{name}.jpg?v={image.get('v', 0)}",
                "source": image.get("source"),
            }
        return None

    def _image_error(self, err: Exception) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_image",
            translation_placeholders={"error": str(err)},
        )

    async def async_set_image_upload(self, sid: str, raw: bytes) -> None:
        """Enregistre la photo (JPEG déjà contrôlé) d'une bouteille."""
        info = self._require_spirit(sid)
        await self.images.async_save(sid, raw)
        info["image"] = {"type": "local", "v": int(dt_util.utcnow().timestamp())}
        await self._async_commit()

    async def async_set_image_url(self, sid: str | None, url: str) -> None:
        """Télécharge l'image collée (https ou data:image) et la stocke localement.

        sid absent : image du résultat de recherche en cours. Adresse vide : retire l'image.
        """
        url = (url or "").strip()
        if sid is None:
            search = self.data.get("search")
            if not search:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_search")
        else:
            info = self._require_spirit(sid)
            if not url:
                await self.async_remove_image(sid)
                return
        if not url.lower().startswith(("https://", "data:image/")):
            raise self._image_error(ImageError("Collez l'adresse de l'image (https://…)"))
        try:
            jpeg = await image_finder.download(self.hass, url)
        except ImageError as err:
            raise self._image_error(err) from err
        source = url if url.lower().startswith("https://") else "copie"
        if sid is None:
            search["image"] = await self._async_store_found(SEARCH_IMAGE, (jpeg, source))
        else:
            info["image"] = await self._async_store_found(sid, (jpeg, source))
        await self._async_commit()

    async def async_remove_image(self, sid: str) -> None:
        info = self._require_spirit(sid)
        await self.images.async_delete(sid)
        if info.pop("image", None) is not None:
            await self._async_commit()

    def _require_spirit(self, sid: str) -> dict:
        if sid not in self.spirits:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_spirit",
                translation_placeholders={"spirit_id": sid},
            )
        return self.spirits[sid]

    def _sanitize_layout(self) -> None:
        layout = self.data.get("layout") or {}
        shelves = min(MAX_SHELVES, max(1, logic.parse_int(layout.get("shelves"), DEFAULT_SHELVES)))
        cols = min(MAX_COLS, max(1, logic.parse_int(layout.get("cols"), DEFAULT_COLS)))
        self.data["layout"] = {"shelves": shelves, "cols": cols}
        self.data["plan"] = logic.prune_plan(self.data["plan"], self.spirits, shelves, cols)

    # ------------------------------------------------------------------
    # Inventaire
    # ------------------------------------------------------------------

    async def async_add_from_search(self, quantity: int = 1) -> str:
        """Ajoute la dernière fiche recherchée à l'inventaire."""
        search = self.data.get("search")
        if not search or not isinstance(search.get("fiche"), dict):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_search")
        item = deepcopy(search["fiche"])
        item["maison"] = search.get("maison") or {"est_maison": False}
        item["nombre_bouteilles"] = max(1, int(quantity))
        item["ajoute_le"] = dt_util.now().isoformat()
        item.pop("image", None)
        sid = f"spirit_{uuid.uuid4().hex[:12]}"
        if (search.get("image") or {}).get("type") == "local" and await self.images.async_copy(
            SEARCH_IMAGE, sid
        ):
            item["image"] = {
                "type": "local",
                "v": int(dt_util.utcnow().timestamp()),
                "source": (search.get("image") or {}).get("source"),
            }
        self.spirits[sid] = item
        if self._search_image_task and not self._search_image_task.done():
            self._search_added_sid = sid
        self.data["search"] = None
        await self._async_commit()
        return sid

    async def async_adjust_stock(self, sid: str, delta: int) -> int:
        """Ajoute/retire des bouteilles (minimum 0)."""
        info = self._require_spirit(sid)
        new_qty = max(0, logic.quantity(info) + int(delta))
        info["nombre_bouteilles"] = new_qty
        self.data["plan"] = logic.trim_placements(self.data["plan"], sid, new_qty)
        await self._async_commit()
        return new_qty

    async def async_set_stock(self, sid: str, qty: int) -> None:
        info = self._require_spirit(sid)
        info["nombre_bouteilles"] = max(0, int(qty))
        self.data["plan"] = logic.trim_placements(self.data["plan"], sid, info["nombre_bouteilles"])
        await self._async_commit()

    async def async_update_spirit(self, sid: str, changes: dict) -> list[str]:
        """Modifie les informations d'une bouteille."""
        info = self._require_spirit(sid)
        try:
            changed = logic.apply_edit(info, changes)
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="empty_name") from err
        if changed:
            info["modifie_le"] = dt_util.now().isoformat()
            await self._async_commit()
        return changed

    async def async_add_tasting(self, sid: str, params: dict) -> dict:
        """Ajoute une dégustation datée à une bouteille."""
        info = self._require_spirit(sid)
        date = params.get("date") or dt_util.now().date().isoformat()
        try:
            tasting = logic.make_tasting(params, uuid.uuid4().hex[:10], str(date))
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="tasting_empty"
            ) from err
        items = [t for t in info.get("degustations", []) if isinstance(t, dict)]
        items.append(tasting)
        items.sort(key=lambda t: str(t.get("date", "")))
        info["degustations"] = items[-logic.MAX_TASTINGS :]
        await self._async_commit()
        return tasting

    async def async_delete_tasting(self, sid: str, tasting_id: str) -> None:
        info = self._require_spirit(sid)
        items = info.get("degustations") or []
        kept = [t for t in items if not (isinstance(t, dict) and t.get("id") == tasting_id)]
        if len(kept) == len(items):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_tasting",
                translation_placeholders={"tasting_id": tasting_id},
            )
        info["degustations"] = kept
        await self._async_commit()

    async def async_taste_profile(self, category: str) -> dict:
        """Synthèse IA de toutes les dégustations d'une catégorie."""
        rated = logic.rated_spirits(self.spirits, category)
        if not rated:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_tastings",
                translation_placeholders={"categorie": logic.CATEGORY_LABELS.get(category, category)},
            )
        prompt = logic.prompt_taste_profile(category, rated)
        result = logic.normalize_profile(await self._async_ai("profile", prompt))
        result["categorie"] = category
        result["nb_bouteilles"] = len(rated)
        result["date"] = dt_util.now().isoformat()
        self.data["profils"][category] = result
        await self._async_commit()
        return result

    async def async_delete(self, sid: str) -> None:
        """Supprime une référence et ses emplacements."""
        self._require_spirit(sid)
        self.spirits.pop(sid)
        await self.images.async_delete(sid)
        self.data["plan"] = {c: s for c, s in self.data["plan"].items() if s != sid}
        await self._async_commit()

    async def async_clear_search(self) -> None:
        self.data["search"] = None
        await self._async_commit()

    async def async_clear_result(self, kind: str) -> None:
        if kind == "last_job":
            self.last_job = None
            self._notify()
            return
        if kind.startswith("profil_"):
            if self.data["profils"].pop(kind.removeprefix("profil_"), None) is not None:
                await self._async_commit()
            return
        if kind in ("cocktail", "audit", "purchase", "search"):
            self.data[kind] = None
            await self._async_commit()

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def _check_cell(self, cell: str) -> str:
        cell = (cell or "").strip().upper()
        if not logic.cell_in_layout(cell, self.shelves, self.cols):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_cell",
                translation_placeholders={"cell": cell},
            )
        return cell

    async def async_place(self, cell: str, sid: str) -> None:
        cell = self._check_cell(cell)
        self._require_spirit(sid)
        self.data["plan"][cell] = sid
        await self._async_commit()

    async def async_clear_cell(self, cell: str) -> None:
        cell = self._check_cell(cell)
        self.data["plan"].pop(cell, None)
        await self._async_commit()

    async def async_autofill(self) -> None:
        self.data["plan"] = logic.autofill(self.spirits, self.shelves, self.cols)
        await self._async_commit()

    async def async_empty_plan(self) -> None:
        self.data["plan"] = {}
        await self._async_commit()

    async def async_set_layout(self, shelves: int, cols: int) -> None:
        self.data["layout"] = {"shelves": shelves, "cols": cols}
        self._sanitize_layout()
        await self._async_commit()

    # ------------------------------------------------------------------
    # IA
    # ------------------------------------------------------------------

    def _check_free(self) -> None:
        if self.status == STATUS_RUNNING or self.job:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="ai_busy")

    async def _async_ai(
        self,
        task: str,
        instructions: str,
        attachments: list[dict] | None = None,
        internal: bool = False,
    ) -> dict:
        """Appelle ai_task.generate_data et renvoie le JSON extrait."""
        if not internal:
            self._check_free()
        self.status = STATUS_RUNNING
        self.running_task = task
        self.last_error = None
        self._notify()
        service_data: dict[str, Any] = {
            "task_name": AI_TASK_NAMES[task],
            "instructions": instructions,
        }
        if self.ai_entity:
            service_data["entity_id"] = self.ai_entity
        if attachments:
            service_data["attachments"] = attachments
        try:
            response = await self.hass.services.async_call(
                "ai_task",
                "generate_data",
                service_data,
                blocking=True,
                return_response=True,
            )
            result = logic.extract_json((response or {}).get("data"))
        except asyncio.CancelledError:
            self.status = STATUS_IDLE
            self.running_task = None
            raise
        except Exception as err:  # noqa: BLE001 - l'état IA doit toujours être réinitialisé
            self.status = STATUS_ERROR
            self.running_task = None
            self.last_error = logic.clean_text(str(err), 250)
            _LOGGER.warning("Tâche IA %s en échec : %s", task, err)
            self._notify()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ai_failed",
                translation_placeholders={"error": self.last_error},
            ) from err
        self.status = STATUS_IDLE
        self.running_task = None
        return result

    async def async_search(
        self,
        nom: str,
        typ: str,
        annee: str,
        maison_params: dict | None,
        pays: str = "",
        photo: bytes | None = None,
    ) -> dict:
        """Génère une fiche (IA, éventuellement depuis une photo) ou une fiche maison (sans IA)."""
        if self._search_image_task and not self._search_image_task.done():
            self._search_image_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._search_image_task
        self._search_added_sid = None
        ai_urls: list[str] = []
        nom = logic.clean_text(nom)
        typ = logic.clean_text(typ)
        annee = logic.clean_text(annee, 20)
        pays = logic.clean_text(pays, 100)
        image = None
        if photo is not None:
            await self.images.async_save(SEARCH_IMAGE, photo)
            self.search_photo = photo
            async_dispatcher_send(self.hass, SIGNAL_PHOTO)
            image = {"type": "local", "v": int(dt_util.utcnow().timestamp())}
        if maison_params is not None:
            maison = logic.compute_maison(maison_params)
            fiche = logic.build_maison_payload(nom, typ, annee, maison, pays)
        else:
            attachments = None
            if photo is not None:
                if not self.photo_entity_id:
                    raise self._image_error(ImageError("Entité photo indisponible"))
                attachments = [
                    {
                        "media_content_id": f"media-source://image/{self.photo_entity_id}",
                        "media_content_type": "image/jpeg",
                    }
                ]
            prompt = logic.prompt_search(nom, typ, annee, pays, photo=photo is not None)
            raw = await self._async_ai("search", prompt, attachments)
            ai_urls = logic.image_urls(raw)
            try:
                fiche = logic.normalize_spirit(raw)
            except ValueError as err:
                self.status = STATUS_ERROR
                self.last_error = (
                    "Bouteille non reconnue sur la photo" if photo is not None else str(err)
                )
                self._notify()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="ai_failed",
                    translation_placeholders={"error": self.last_error},
                ) from err
            maison = {"est_maison": False}
        self.data["search"] = {
            "fiche": fiche,
            "maison": maison,
            "requete": {"nom": nom, "type": typ, "annee": annee, "pays": pays, "photo": photo is not None},
            "date": dt_util.now().isoformat(),
            "image": image,
        }
        await self._async_commit()
        if photo is None and maison_params is None:
            # Recherche automatique de l'image en arrière-plan.
            self._search_image_task = self.hass.async_create_background_task(
                self._async_search_image(self.data["search"]["date"], logic.image_query(fiche), ai_urls),
                "bar_ia_search_image",
            )
        return self.data["search"]

    # ------------------------------------------------------------------
    # Recherche automatique d'image
    # ------------------------------------------------------------------

    async def _async_verify(self, q: dict, jpeg: bytes) -> bool:
        """L'IA regarde l'image (via l'entité photo) et confirme qu'il s'agit de la bouteille."""
        if not self.photo_entity_id:
            return False
        self.search_photo = jpeg
        async_dispatcher_send(self.hass, SIGNAL_PHOTO)
        attachments = [
            {
                "media_content_id": f"media-source://image/{self.photo_entity_id}",
                "media_content_type": "image/jpeg",
            }
        ]
        answer = await self._async_ai("verify", logic.prompt_verify_image(q), attachments, internal=True)
        return logic.image_matches(answer)

    async def _async_find_image(self, q: dict, ai_urls: list[str] | None = None) -> tuple[bytes, str] | None:
        """Cherche, télécharge et fait vérifier une image ; renvoie (jpeg, adresse source)."""
        stats = self._find_stats = {"candidats": 0, "refusees": 0}
        text = logic.image_search_text(q)
        if not text:
            return None
        tried: set[str] = set()
        ai_failures = 0

        async def _try(cands: list[image_finder.Candidate]) -> tuple[bytes, str] | None:
            nonlocal ai_failures
            verified = stats["refusees"]
            for cand in cands:
                if self._job_cancel or cand.url in tried or len(tried) >= 6 or verified >= 3:
                    continue
                tried.add(cand.url)
                try:
                    jpeg = await image_finder.download(self.hass, cand.url)
                except ImageError as err:
                    _LOGGER.debug("Image écartée %s : %s", cand.url, err)
                    continue
                stats["candidats"] += 1
                try:
                    verified += 1
                    if await self._async_verify(q, jpeg):
                        return jpeg, cand.url
                    stats["refusees"] += 1
                    _LOGGER.debug("Image refusée par l'IA : %s", cand.url)
                except HomeAssistantError:
                    ai_failures += 1
                    if ai_failures >= 2:
                        raise
            return None

        found = await _try(
            [
                *await image_finder.candidates_openfoodfacts(self.hass, text),
                *await image_finder.candidates_commons(self.hass, text),
                *[image_finder.Candidate(u, "ia") for u in ai_urls or []],
            ]
        )
        if found or ai_urls is not None or self._job_cancel:
            return found
        # Aucun résultat dans les bases : on demande des adresses à l'IA.
        try:
            urls = logic.image_urls(await self._async_ai("image_urls", logic.prompt_image_urls(q), internal=True))
        except HomeAssistantError:
            return None
        return await _try([image_finder.Candidate(u, "ia") for u in urls])

    async def _async_store_found(self, name: str, found: tuple[bytes, str]) -> dict:
        jpeg, source = found
        await self.images.async_save(name, jpeg)
        return {"type": "local", "v": int(dt_util.utcnow().timestamp()), "source": source}

    async def _async_search_image(self, token: str, q: dict, ai_urls: list[str]) -> None:
        """Image de la fiche recherchée (arrière-plan, après la fiche)."""
        self.job = {"type": "image_recherche", "label": q.get("nom") or ""}
        self._job_cancel = False
        self._notify()
        try:
            found = await self._async_find_image(q, ai_urls)
            if not found:
                return
            search = self.data.get("search")
            if search and search.get("date") == token:
                search["image"] = await self._async_store_found(SEARCH_IMAGE, found)
            elif (sid := self._search_added_sid) and sid in self.spirits and not self.spirits[sid].get("image"):
                self.spirits[sid]["image"] = await self._async_store_found(sid, found)
            else:
                return
            await self._async_commit()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - tâche de fond : ne jamais planter
            _LOGGER.warning("Recherche d'image en échec : %s", err)
        finally:
            self.job = None
            self._search_added_sid = None
            self._notify()

    async def async_find_image(self, sid: str) -> bool:
        """Cherche une image pour une bouteille (remplace l'image actuelle si trouvée)."""
        info = self._require_spirit(sid)
        self._check_free()
        self.job = {"type": "image", "label": logic.build_label(info), "total": 1, "fait": 0, "trouvees": 0}
        self._job_cancel = False
        self._notify()
        try:
            found = await self._async_find_image(logic.image_query(info))
            if found and sid in self.spirits:
                self.spirits[sid]["image"] = await self._async_store_found(sid, found)
                await self._async_commit()
            self.last_job = {
                "total": 1,
                "trouvees": int(bool(found)),
                "sans_candidat": int(not self._find_stats.get("candidats")),
                "refusees": self._find_stats.get("refusees", 0),
                "date": dt_util.now().isoformat(),
            }
            return bool(found)
        finally:
            self.job = None
            self._notify()

    async def async_find_missing_images(self, only_missing: bool = True) -> dict:
        """Lance en arrière-plan la recherche d'image pour les bouteilles existantes."""
        self._check_free()
        targets = [
            sid for sid, info in self.spirits.items()
            if not logic.is_maison(info) and (not only_missing or not info.get("image"))
        ]
        if not targets:
            return {"total": 0}
        self.job = {
            "type": "images", "label": "", "total": len(targets), "fait": 0, "trouvees": 0,
            "sans_candidat": 0, "refusees": 0,
        }
        self._job_cancel = False
        self._notify()
        self.hass.async_create_background_task(self._async_images_job(targets), "bar_ia_images")
        return {"total": len(targets)}

    async def _async_images_job(self, targets: list[str]) -> None:
        try:
            for sid in targets:
                if self._job_cancel:
                    break
                info = self.spirits.get(sid)
                if info is None:
                    self.job["fait"] += 1
                    continue
                self.job["label"] = logic.build_label(info)
                self._notify()
                try:
                    found = await self._async_find_image(logic.image_query(info))
                except HomeAssistantError as err:
                    self.last_error = logic.clean_text(f"Recherche d'images interrompue : {err}", 250)
                    break
                if found and sid in self.spirits:
                    self.spirits[sid]["image"] = await self._async_store_found(sid, found)
                    self.job["trouvees"] += 1
                    await self._async_commit()
                elif not self._find_stats.get("candidats"):
                    self.job["sans_candidat"] += 1
                else:
                    self.job["refusees"] += 1
                self.job["fait"] += 1
                self._notify()
                await asyncio.sleep(JOB_DELAY)  # ménage le quota de l'IA
        except Exception as err:  # noqa: BLE001 - tâche de fond
            _LOGGER.warning("Recherche d'images en échec : %s", err)
        finally:
            done = self.job or {}
            _LOGGER.info("Images trouvées : %s / %s", done.get("trouvees"), done.get("total"))
            self.last_job = {
                k: done.get(k, 0) for k in ("total", "fait", "trouvees", "sans_candidat", "refusees")
            } | {"date": dt_util.now().isoformat()}
            self.job = None
            self._notify()

    @callback
    def async_stop_job(self) -> None:
        """Demande l'arrêt de la recherche d'images en cours."""
        if self.job:
            self._job_cancel = True

    async def async_cocktail(
        self, sid: str | None, type_demande: str, ingredients: str, occasion: str
    ) -> dict:
        bouteille = self._require_spirit(sid) if sid else None
        prompt = logic.prompt_cocktail(
            bouteille,
            logic.clean_text(type_demande, 50),
            logic.clean_text(ingredients),
            logic.clean_text(occasion),
            self.spirits,
        )
        result = logic.normalize_cocktail(await self._async_ai("cocktail", prompt))
        result["bouteille"] = logic.build_label(bouteille) if bouteille else None
        result["date"] = dt_util.now().isoformat()
        self.data["cocktail"] = result
        await self._async_commit()
        return result

    async def async_audit(self) -> dict:
        if not self.spirits:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="empty_bar")
        prompt = logic.prompt_audit(self.spirits, self.stats())
        result = logic.normalize_audit(await self._async_ai("audit", prompt))
        result["date"] = dt_util.now().isoformat()
        self.data["audit"] = result
        await self._async_commit()
        return result

    async def async_purchase(
        self, nom: str, typ: str, prix: float | None, budget: float | None
    ) -> dict:
        nom = logic.clean_text(nom)
        typ = logic.clean_text(typ)
        profil = self.data["profils"].get(logic.category_for({"type": typ})) if typ else None
        if profil:
            profil = {k: v for k, v in profil.items() if k not in ("classement", "date")}
        prompt = logic.prompt_purchase(nom, typ, prix, budget, self.spirits, profil)
        result = logic.normalize_purchase(await self._async_ai("purchase", prompt))
        result["bouteille"] = nom
        result["prix_propose"] = prix
        result["budget_max"] = budget
        if prix and budget and prix > budget and result["verdict"] == "acheter":
            result["verdict"] = "attendre"
            result["raison"] = (result["raison"] + " Prix supérieur au budget fixé.").strip()
        result["date"] = dt_util.now().isoformat()
        self.data["purchase"] = result
        await self._async_commit()
        return result

    # ------------------------------------------------------------------
    # Migration depuis l'ancien package YAML
    # ------------------------------------------------------------------

    async def async_import_legacy(self, force: bool = False) -> dict:
        """Importe sensor.bar_supersensor + /config/bar_plan.json."""
        result = {"importees": 0, "cases": 0, "source": False}
        if self.data.get("migrated") and not force:
            return result

        state = self.hass.states.get(LEGACY_SUPERSENSOR)
        raw_inv = state.attributes.get(LEGACY_ATTR_INV) if state else None
        legacy = logic.parse_legacy_inventory(raw_inv)

        plan_path = Path(self.hass.config.path(LEGACY_PLAN_FILE))

        def _read_plan() -> Any:
            if not plan_path.is_file():
                return None
            try:
                return json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

        raw_plan = await self.hass.async_add_executor_job(_read_plan)

        if legacy:
            result["source"] = True
            for sid, item in legacy.items():
                if sid not in self.spirits:
                    self.spirits[sid] = item
                    result["importees"] += 1
            for entity_id, key in ((LEGACY_SHELVES_ENTITY, "shelves"), (LEGACY_COLS_ENTITY, "cols")):
                st = self.hass.states.get(entity_id)
                if st and logic.parse_int(st.state, 0) > 0:
                    self.data["layout"][key] = logic.parse_int(st.state, 0)
            self._sanitize_layout()
            if raw_plan and not self.data["plan"]:
                plan = logic.parse_legacy_plan(raw_plan, self.spirits)
                self.data["plan"] = logic.prune_plan(plan, self.spirits, self.shelves, self.cols)
                result["cases"] = len(self.data["plan"])

        self.data["migrated"] = True
        await self._async_commit()
        return result
