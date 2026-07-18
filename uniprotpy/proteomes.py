"""Lossless UniProt proteome models and evidence-honest selection."""

from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union


class Proteome:
  """A faithful proteome JSON document with tolerant convenience accessors.

  Unknown fields and unknown values of tagged string fields are retained exactly.
  Construction and all accessors are free of network and filesystem I/O.
  """

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("Proteome data must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "Proteome":
    return cls(data)

  @classmethod
  def from_json(cls, data: Mapping[str, Any]) -> "Proteome":
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
    if not isinstance(other, Proteome):
      return NotImplemented
    return self._data == other._data

  def __repr__(self) -> str:
    return "Proteome(upid={!r})".format(self.upid)

  @cached_property
  def upid(self) -> Optional[str]:
    value = self._data.get("id")
    return value if isinstance(value, str) else None

  @property
  def id(self) -> Optional[str]:
    return self.upid

  @cached_property
  def taxonomy(self) -> Optional[Mapping[str, Any]]:
    value = self._data.get("taxonomy")
    return value if isinstance(value, Mapping) else None

  @cached_property
  def taxon_id(self) -> Optional[int]:
    if self.taxonomy is None:
      return None
    value = self.taxonomy.get("taxonId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @property
  def organism_taxon_id(self) -> Optional[int]:
    return self.taxon_id

  @cached_property
  def organism_name(self) -> Optional[str]:
    if self.taxonomy is None:
      return None
    value = self.taxonomy.get("scientificName")
    return value if isinstance(value, str) else None

  @cached_property
  def proteome_type(self) -> Optional[str]:
    value = self._data.get("proteomeType")
    return value if isinstance(value, str) else None

  @cached_property
  def protein_count(self) -> Optional[int]:
    value = self._data.get("proteinCount")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def components(self) -> Tuple[Mapping[str, Any], ...]:
    value = self._data.get("components")
    if not isinstance(value, (list, tuple)):
      return ()
    return tuple(item for item in value if isinstance(item, Mapping))

  @cached_property
  def busco_report(self) -> Optional[Mapping[str, Any]]:
    completeness = self._data.get("proteomeCompletenessReport")
    if not isinstance(completeness, Mapping):
      return None
    report = completeness.get("buscoReport")
    return report if isinstance(report, Mapping) else None

  @cached_property
  def busco_score(self) -> Optional[float]:
    if self.busco_report is None:
      return None
    value = self.busco_report.get("score")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
      return float(value)
    return None


@dataclass(frozen=True)
class Unique:
  """Exactly one proteome is supported by the requested selection policy."""

  proteome: Proteome

  @property
  def selected(self) -> Proteome:
    return self.proteome


@dataclass(frozen=True)
class Ambiguous:
  """More than one proteome remains supported; no implicit ranking is applied."""

  candidates: Tuple[Proteome, ...]


@dataclass(frozen=True)
class NotFound:
  """No candidate proteome was supplied."""

  taxon_id: Optional[int] = None


SelectionResult = Union[Unique, Ambiguous, NotFound]


def _candidates(proteomes: Iterable[Proteome]) -> Tuple[Proteome, ...]:
  values = tuple(proteomes)
  if any(not isinstance(item, Proteome) for item in values):
    raise TypeError("proteomes must contain only Proteome objects")
  return values


def select_proteome(
  proteomes: Iterable[Proteome], *, taxon_id: Optional[int] = None
) -> SelectionResult:
  """Select only when uniqueness is established, without ranking candidates."""
  candidates = _candidates(proteomes)
  if not candidates:
    return NotFound(taxon_id)
  if len(candidates) == 1:
    return Unique(candidates[0])
  return Ambiguous(candidates)


def select_highest_busco(
  proteomes: Iterable[Proteome], *, taxon_id: Optional[int] = None
) -> SelectionResult:
  """Apply the explicitly requested highest-BUSCO policy.

  A missing BUSCO score cannot honestly be ordered below an observed score, so
  it remains ambiguous with every observed top scorer. Equal top scores also
  remain ambiguous; input order is retained to make the result deterministic.
  """
  candidates = _candidates(proteomes)
  if len(candidates) < 2:
    return select_proteome(candidates, taxon_id=taxon_id)
  scored = tuple(item for item in candidates if item.busco_score is not None)
  if not scored:
    return Ambiguous(candidates)
  highest = max(item.busco_score for item in scored)
  finalists = tuple(
    item for item in candidates
    if item.busco_score is None or item.busco_score == highest
  )
  if len(finalists) == 1:
    return Unique(finalists[0])
  return Ambiguous(finalists)




class ProteomeSelector:
  """Pure candidate selection with no constructor I/O or retained state."""

  def select(
    self,
    proteomes: Iterable[Proteome],
    *,
    taxon_id: Optional[int] = None,
  ) -> SelectionResult:
    return select_proteome(proteomes, taxon_id=taxon_id)

  def select_highest_busco(
    self,
    proteomes: Iterable[Proteome],
    *,
    taxon_id: Optional[int] = None,
  ) -> SelectionResult:
    return select_highest_busco(proteomes, taxon_id=taxon_id)


__all__ = [
  "Ambiguous", "NotFound", "Proteome", "ProteomeSelector", "SelectionResult",
  "Unique", "select_highest_busco", "select_proteome",
]
