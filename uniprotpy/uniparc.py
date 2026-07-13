"""Lossless, I/O-free domain values for UniParc sequence records."""

from copy import deepcopy
from functools import cached_property
from typing import Any, Dict, Mapping, Optional, Tuple


def _mapping_items(value: Any) -> Tuple[Mapping[str, Any], ...]:
  if not isinstance(value, (list, tuple)):
    return ()
  return tuple(item for item in value if isinstance(item, Mapping))


def _unique_strings(values: Any) -> Tuple[str, ...]:
  if isinstance(values, (str, bytes)):
    return ()
  try:
    iterator = iter(values)
  except TypeError:
    return ()
  seen = set()
  result = []
  for value in iterator:
    if isinstance(value, str) and value not in seen:
      seen.add(value)
      result.append(value)
  return tuple(result)


class UniParcCrossReference:
  """One heterogeneous UniParc source-database reference."""

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("UniParc cross-reference data must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "UniParcCrossReference":
    return cls(data)

  @classmethod
  def from_json(cls, data: Mapping[str, Any]) -> "UniParcCrossReference":
    return cls(data)

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  @property
  def data(self) -> Mapping[str, Any]:
    return self._data
  @property
  def raw_json(self) -> Mapping[str, Any]:
    return self._data

  def dict(self) -> Dict[str, Any]:
    return self.to_dict()


  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)
  def to_json(self) -> Dict[str, Any]:
    return self.to_dict()


  def __eq__(self, other: object) -> bool:
    if not isinstance(other, UniParcCrossReference):
      return NotImplemented
    return self._data == other._data

  def __repr__(self) -> str:
    return "UniParcCrossReference(database={!r}, identifier={!r})".format(
      self.database, self.identifier
    )

  @cached_property
  def database(self) -> Optional[str]:
    value = self._data.get("database")
    return value if isinstance(value, str) else None

  @cached_property
  def identifier(self) -> Optional[str]:
    value = self._data.get("id")
    return value if isinstance(value, str) else None

  @property
  def id(self) -> Optional[str]:
    return self.identifier

  @cached_property
  def active(self) -> Optional[bool]:
    value = self._data.get("active")
    return value if isinstance(value, bool) else None

  @cached_property
  def version(self) -> Optional[int]:
    value = self._data.get("version")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def version_i(self) -> Optional[int]:
    value = self._data.get("versionI")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def created(self) -> Optional[str]:
    value = self._data.get("created")
    return value if isinstance(value, str) else None

  @cached_property
  def last_updated(self) -> Optional[str]:
    value = self._data.get("lastUpdated")
    return value if isinstance(value, str) else None

  @cached_property
  def gene_name(self) -> Optional[str]:
    value = self._data.get("geneName")
    return value if isinstance(value, str) else None

  @cached_property
  def protein_name(self) -> Optional[str]:
    value = self._data.get("proteinName")
    return value if isinstance(value, str) else None

  @cached_property
  def organism(self) -> Optional[Mapping[str, Any]]:
    value = self._data.get("organism")
    return value if isinstance(value, Mapping) else None

  @cached_property
  def taxon_id(self) -> Optional[int]:
    if self.organism is None:
      return None
    value = self.organism.get("taxonId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def proteomes(self) -> Tuple[Mapping[str, Any], ...]:
    return _mapping_items(self._data.get("proteomes"))
  @cached_property
  def chain(self) -> Optional[str]:
    value = self._data.get("chain")
    return value if isinstance(value, str) else None

  @cached_property
  def ncbi_gi(self) -> Optional[str]:
    value = self._data.get("ncbiGi")
    return value if isinstance(value, str) else None

  @cached_property
  def properties(self) -> Tuple[Mapping[str, Any], ...]:
    return _mapping_items(self._data.get("properties"))



class UniParcEntry:
  """A faithful full or light UniParc JSON record.

  UniParc is sequence-centric, but its records are not merely UPI-to-sequence
  pairs. Full entries retain heterogeneous active and historical database
  references, organisms, proteomes, names, features, and date provenance.
  """

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("UniParc entry data must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "UniParcEntry":
    return cls(data)

  @classmethod
  def from_json(cls, data: Mapping[str, Any]) -> "UniParcEntry":
    return cls(data)

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  @property
  def data(self) -> Mapping[str, Any]:
    return self._data

  @property
  def raw_json(self) -> Mapping[str, Any]:
    return self._data

  def dict(self) -> Dict[str, Any]:
    return self.to_dict()

  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)

  def to_json(self) -> Dict[str, Any]:
    return self.to_dict()

  def __eq__(self, other: object) -> bool:
    if not isinstance(other, UniParcEntry):
      return NotImplemented
    return self._data == other._data

  def __repr__(self) -> str:
    return "UniParcEntry(upi={!r})".format(self.upi)

  @cached_property
  def upi(self) -> Optional[str]:
    value = self._data.get("uniParcId")
    return value if isinstance(value, str) else None

  @property
  def id(self) -> Optional[str]:
    return self.upi

  @property
  def uniparc_id(self) -> Optional[str]:
    return self.upi

  @cached_property
  def sequence_data(self) -> Mapping[str, Any]:
    value = self._data.get("sequence")
    return value if isinstance(value, Mapping) else {}

  @cached_property
  def sequence(self) -> Optional[str]:
    value = self.sequence_data.get("value")
    return value if isinstance(value, str) else None

  @cached_property
  def length(self) -> Optional[int]:
    value = self.sequence_data.get("length")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def molecular_weight(self) -> Optional[int]:
    value = self.sequence_data.get("molWeight")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def crc64(self) -> Optional[str]:
    value = self.sequence_data.get("crc64")
    return value if isinstance(value, str) else None

  @cached_property
  def md5(self) -> Optional[str]:
    value = self.sequence_data.get("md5")
    return value if isinstance(value, str) else None

  @cached_property
  def cross_references(self) -> Tuple[UniParcCrossReference, ...]:
    return tuple(
      UniParcCrossReference(item)
      for item in _mapping_items(self._data.get("uniParcCrossReferences"))
    )

  @cached_property
  def active_cross_references(self) -> Tuple[UniParcCrossReference, ...]:
    return tuple(reference for reference in self.cross_references if reference.active is True)

  @cached_property
  def sequence_features(self) -> Tuple[Mapping[str, Any], ...]:
    return _mapping_items(self._data.get("sequenceFeatures"))

  @cached_property
  def common_taxons(self) -> Tuple[Mapping[str, Any], ...]:
    return _mapping_items(self._data.get("commonTaxons"))
  @cached_property
  def organisms(self) -> Tuple[Mapping[str, Any], ...]:
    """Server-provided light-record organism summaries, without xref fallback."""
    return _mapping_items(self._data.get("organisms"))

  @cached_property
  def taxon_ids(self) -> Tuple[int, ...]:
    return tuple(
      value for value in (organism.get("taxonId") for organism in self.organisms)
      if isinstance(value, int) and not isinstance(value, bool)
    )
  @cached_property
  def gene_names(self) -> Tuple[str, ...]:
    return _unique_strings(self._data.get("geneNames", ()))

  @cached_property
  def protein_names(self) -> Tuple[str, ...]:
    return _unique_strings(self._data.get("proteinNames", ()))

  @cached_property
  def proteomes(self) -> Tuple[Mapping[str, Any], ...]:
    """Server-provided light-record proteome aggregates."""
    return _mapping_items(self._data.get("proteomes"))

  @cached_property
  def extra_attributes(self) -> Mapping[str, Any]:
    value = self._data.get("extraAttributes")
    return value if isinstance(value, Mapping) else {}



  @cached_property
  def uniprotkb_accessions(self) -> Tuple[str, ...]:
    values = self._data.get("uniProtKBAccessions")
    if isinstance(values, (list, tuple)):
      return _unique_strings(values)
    return _unique_strings(
      reference.identifier
      for reference in self.cross_references
      if reference.database is not None and reference.database.startswith("UniProtKB/")
    )

  @cached_property
  def organism_cross_references(self) -> Tuple[UniParcCrossReference, ...]:
    """References with organism data, preserving database and status context."""
    return tuple(
      reference for reference in self.cross_references
      if reference.organism is not None
    )

  @cached_property
  def cross_reference_count(self) -> Optional[int]:
    value = self._data.get("crossReferenceCount")
    if isinstance(value, int) and not isinstance(value, bool):
      return value
    if "uniParcCrossReferences" in self._data:
      return len(self.cross_references)
    return None

  @cached_property
  def oldest_cross_reference_created(self) -> Optional[str]:
    value = self._data.get("oldestCrossRefCreated")
    return value if isinstance(value, str) else None

  @cached_property
  def most_recent_cross_reference_updated(self) -> Optional[str]:
    value = self._data.get("mostRecentCrossRefUpdated")
    return value if isinstance(value, str) else None

  @cached_property
  def uniprot_exclusion_reason(self) -> Optional[str]:
    value = self._data.get("uniProtExclusionReason")
    return value if isinstance(value, str) else None


__all__ = ["UniParcCrossReference", "UniParcEntry"]
