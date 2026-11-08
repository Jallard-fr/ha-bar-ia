"""Constantes de l'intégration Bar IA."""

from __future__ import annotations

DOMAIN = "bar_ia"
VERSION = "2.4.0"

STORAGE_KEY = f"{DOMAIN}.data"
STORAGE_VERSION = 1

SIGNAL_UPDATED = f"{DOMAIN}_updated"
SIGNAL_PHOTO = f"{DOMAIN}_photo"

CONF_AI_ENTITY = "ai_task_entity"

STATIC_URL = f"/{DOMAIN}_static"
CARD_FILENAME = "bar-ia-card.js"

DEFAULT_SHELVES = 5
DEFAULT_COLS = 4
MAX_SHELVES = 30
MAX_COLS = 12

# Anciennes entités / fichiers (version "package YAML") utilisés pour la migration.
LEGACY_SUPERSENSOR = "sensor.bar_supersensor"
LEGACY_ATTR_INV = "spiritueux"
LEGACY_PLAN_FILE = "bar_plan.json"
LEGACY_SHELVES_ENTITY = "input_number.bar_nb_etageres"
LEGACY_COLS_ENTITY = "input_number.bar_nb_colonnes"

STATUS_IDLE = "inactif"
STATUS_RUNNING = "en_cours"
STATUS_ERROR = "erreur"
