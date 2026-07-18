"""Lossless, I/O-free domain values for UniProt taxonomy records."""

from copy import deepcopy
from functools import cached_property
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple


class Taxon:
  """A faithful UniProt taxonomy JSON document with tolerant accessors."""

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("Taxon data must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "Taxon":
    return cls(data)

  @classmethod
  def from_json(cls, data: Mapping[str, Any]) -> "Taxon":
    return cls(data)

  @property
  def raw(self) -> Mapping[str, Any]:
    return MappingProxyType(self._data)
  @property
  def data(self) -> Mapping[str, Any]:
    return MappingProxyType(self._data)

  @property
  def raw_json(self) -> Mapping[str, Any]:
    return MappingProxyType(self._data)

  def dict(self) -> Dict[str, Any]:
    return self.to_dict()


  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)
  def to_json(self) -> Dict[str, Any]:
    return self.to_dict()


  def __eq__(self, other: object) -> bool:
    if not isinstance(other, Taxon):
      return NotImplemented
    return self._data == other._data

  def __repr__(self) -> str:
    return "Taxon(taxon_id={!r}, scientific_name={!r})".format(
      self.taxon_id, self.scientific_name
    )

  @cached_property
  def taxon_id(self) -> Optional[int]:
    value = self._data.get("taxonId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @property
  def id(self) -> Optional[int]:
    return self.taxon_id

  @cached_property
  def scientific_name(self) -> Optional[str]:
    value = self._data.get("scientificName")
    return value if isinstance(value, str) else None

  @property
  def name(self) -> Optional[str]:
    return self.scientific_name

  @cached_property
  def common_name(self) -> Optional[str]:
    value = self._data.get("commonName")
    return value if isinstance(value, str) else None

  @cached_property
  def mnemonic(self) -> Optional[str]:
    value = self._data.get("mnemonic")
    return value if isinstance(value, str) else None

  @cached_property
  def rank(self) -> Optional[str]:
    value = self._data.get("rank")
    return value if isinstance(value, str) else None

  @cached_property
  def active(self) -> Optional[bool]:
    value = self._data.get("active")
    return value if isinstance(value, bool) else None

  @cached_property
  def hidden(self) -> Optional[bool]:
    value = self._data.get("hidden")
    return value if isinstance(value, bool) else None

  @cached_property
  def parent(self) -> Optional["Taxon"]:
    value = self._data.get("parent")
    return Taxon(value) if isinstance(value, Mapping) else None

  @cached_property
  def lineage(self) -> Tuple["Taxon", ...]:
    value = self._data.get("lineage", ())
    if not isinstance(value, (list, tuple)):
      return ()
    return tuple(Taxon(item) for item in value if isinstance(item, Mapping))

  @cached_property
  def other_names(self) -> Tuple[str, ...]:
    value = self._data.get("otherNames", ())
    if not isinstance(value, (list, tuple)):
      return ()
    return tuple(item for item in value if isinstance(item, str))

  @cached_property
  def links(self) -> Tuple[str, ...]:
    value = self._data.get("links", ())
    if not isinstance(value, (list, tuple)):
      return ()
    return tuple(item for item in value if isinstance(item, str))

  @cached_property
  def statistics(self) -> Mapping[str, Any]:
    value = self._data.get("statistics")
    return value if isinstance(value, Mapping) else {}

  @cached_property
  def inactive_reason(self) -> Optional[str]:
    value = self._data.get("inactiveReason")
    if not isinstance(value, Mapping):
      return None
    reason = value.get("inactiveReasonType")
    return reason if isinstance(reason, str) else None

  @cached_property
  def merged_to(self) -> Optional[int]:
    inactive = self._data.get("inactiveReason")
    if not isinstance(inactive, Mapping):
      return None
    value = inactive.get("mergedTo")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["Taxon"]
