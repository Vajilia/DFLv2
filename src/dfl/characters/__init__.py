"""Character creator (Design Document 8.49, section 13): builds the character card for every player and club leader.

The creator is a standalone module. In: a world seed plus a request (role, position, age range, class quality). Out: complete cards.
The ledger and the engine read cards; they never generate characters. Attribute lists and distributions are data in data/characters/.
"""
from .card import Card, Identity, GROWTH_KEYS
from .creator import Creator, load_tables

__all__ = ["Card", "Identity", "Creator", "load_tables", "GROWTH_KEYS"]
