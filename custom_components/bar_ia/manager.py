"""Gestionnaire central : stockage persistant, appels IA, plan de rangement."""

from __future__ import annotations

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

from . import logic
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

AI_TASK_NAMES = {
    "search": "bar_ia fiche spiritueux",
    "cocktail": "bar_ia accord du barman",
    "audit": "bar_ia audit du bar",
    "purchase": "bar_ia opportunite achat",
    "profile": "bar_ia profil de degustation",
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
            return {"type": "local", "path": f"/api/bar_ia/image/{name}.jpg?v={image.get('v', 0)}"}
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

    async def async_set_image_url(self, sid: str, url: str) -> None:
        """Associe une image web (https) ou retire l'image si l'adresse est vide."""
        info = self._require_spirit(sid)
        url = (url or "").strip()
        if not url:
            await self.async_remove_image(sid)
            return
        if not valid_url(url):
            raise self._image_error(ImageError("Adresse https:// attendue"))
        await self.images.async_delete(sid)
        info["image"] = {"type": "url", "url": url}
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
            item["image"] = {"type": "local", "v": int(dt_util.utcnow().timestamp())}
        self.spirits[sid] = item
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

    async def _async_ai(self, task: str, instructions: str, attachments: list[dict] | None = None) -> dict:
        """Appelle ai_task.generate_data et renvoie le JSON extrait."""
        if self.status == STATUS_RUNNING:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="ai_busy")
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
        return self.data["search"]

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
