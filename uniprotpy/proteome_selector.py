"""Compatibility imports for the pure proteome selection API."""

from .proteomes import (
  Ambiguous,
  NotFound,
  ProteomeSelector,
  SelectionResult,
  Unique,
  select_highest_busco,
  select_highest_busco_proteome,
  select_proteome,
)

__all__ = [
  "Ambiguous", "NotFound", "ProteomeSelector", "SelectionResult", "Unique",
  "select_highest_busco", "select_highest_busco_proteome", "select_proteome",
]
