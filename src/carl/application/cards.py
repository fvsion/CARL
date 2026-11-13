"""Model cards: the card of any model by name, and the user's card of a custom model, checked and stored in
models.json (the rules: domain/catalog/cards.py and records.py). Catalogue models are read-only.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from ..domain.catalog import cards
from ..domain.catalog.records import parse_custom_card
from ..domain.shared.errors import ConfigError
from ..domain.shared.types import CustomCard, ModelInfo
from .config import ConfigService
from .inventory import Inventory


class CardService:
    """A model's card in models.json: read, set, unset. Contract C-102."""

    def __init__(self, config: ConfigService, inventory: Inventory) -> None:
        self.config = config
        self.inventory = inventory

    def card_model(self, name: str) -> Tuple[ModelInfo, List[ModelInfo]]:
        """(the model, every model) for a card: by name, file name or path."""
        models = self.inventory.all_models(self.config.load_config())
        m = self.inventory.find(name, models)
        if m is None:
            raise ConfigError(f"unknown model '{name}' (see: {self.config.words.command} models)")
        return m, models

    def save_card(self, name: str, card: object) -> CustomCard:
        """Check and store the user's card of a custom model in models.json (an empty card
        removes it). Catalogue models are read-only."""
        m, models = self.card_model(name)
        key = m.get("name", "")
        if not m.get("custom"):
            raise ConfigError(f"{key} is a catalogue model, so you cannot change its card. You can edit the cards "
                              f"of custom models only: Hugging Face downloads and files in the models folder.")
        checked = parse_custom_card(card, {x.get("name", "") for x in models}, f"{key}: card", key,
                                    self.config.words)
        db = self.inventory.load_local()
        entry = db["models"].setdefault(key, {})
        entry.setdefault("path", m.get("path", ""))         # a file in the models folder: keep it a custom model
        entry.setdefault("source", m.get("source", "file"))
        if checked:
            entry["card"] = checked
        else:
            entry.pop("card", None)
        self.inventory.save_local(db)
        return checked

    def set_card_field(self, name: str, key: str, args: Sequence[str]) -> CustomCard:
        """One field of a custom model's card from command-line text, checked and stored."""
        m, _ = self.card_model(name)
        return self.save_card(name, cards.set_field(cards.editable_card(m), key, args))

    def unset_card_field(self, name: str, key: str) -> CustomCard:
        m, _ = self.card_model(name)
        return self.save_card(name, cards.unset_field(cards.editable_card(m), key))
