"""Lossless, I/O-free domain models for UniProtKB records."""

from copy import deepcopy
from functools import cached_property
import re
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


_ISOFORM_ACCESSION = re.compile(r"^(?P<canonical>.+)-(?P<number>[1-9][0-9]*)$")


def _value(item: Any) -> Optional[str]:
  """Return the text of a UniProt named-value object, if present."""
  if isinstance(item, str):
    return item
  if isinstance(item, Mapping):
    value = item.get("value")
    if isinstance(value, str):
      return value
  return None


def _unique(values: Iterable[Optional[str]]) -> Tuple[str, ...]:
  seen = set()
  result = []
  for value in values:
    if value is not None and value not in seen:
      seen.add(value)
      result.append(value)
  return tuple(result)


def _protein_name_values(name: Any) -> Iterable[Optional[str]]:
  if not isinstance(name, Mapping):
    return
  yield _value(name.get("fullName"))
  short_names = name.get("shortNames", ())
  if isinstance(short_names, (list, tuple)):
    for short_name in short_names:
      yield _value(short_name)


class UniProtEntry:
  """A faithful UniProtKB JSON document with tolerant convenience accessors.

  Construction is deliberately pure: no network or filesystem access occurs. The
  complete input mapping, including unknown fields and tagged-union variants, is
  retained. ``to_dict`` returns a defensive deep copy suitable for persistence.
  """

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("UniProtEntry data must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "UniProtEntry":
    return cls(data)

  @classmethod
  def from_json(cls, data: Mapping[str, Any]) -> "UniProtEntry":
    return cls(data)

  @property
  def raw(self) -> Mapping[str, Any]:
    """A read-only view of the authoritative document; use ``to_dict`` to edit."""
    return MappingProxyType(self._data)

  def dict(self) -> Dict[str, Any]:
    return self.to_dict()

  @property
  def data(self) -> Mapping[str, Any]:
    return MappingProxyType(self._data)

  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)

  def __eq__(self, other: object) -> bool:
    if not isinstance(other, UniProtEntry):
      return NotImplemented
    return self._data == other._data

  def __repr__(self) -> str:
    return "UniProtEntry(accession={!r})".format(self.accession)

  @cached_property
  def accession(self) -> Optional[str]:
    value = self._data.get("primaryAccession")
    return value if isinstance(value, str) else None

  @cached_property
  def canonical_accession(self) -> Optional[str]:
    if self.accession is None:
      return None
    match = _ISOFORM_ACCESSION.match(self.accession)
    return match.group("canonical") if match else self.accession

  @property
  def primary_accession(self) -> Optional[str]:
    return self.accession

  @cached_property
  def is_isoform(self) -> bool:
    return self.accession is not None and self.canonical_accession != self.accession

  @cached_property
  def secondary_accessions(self) -> Tuple[str, ...]:
    values = self._data.get("secondaryAccessions", ())
    if not isinstance(values, (list, tuple)):
      return ()
    return tuple(value for value in values if isinstance(value, str))

  @cached_property
  def reviewed(self) -> Optional[bool]:
    entry_type = self._data.get("entryType")
    if not isinstance(entry_type, str):
      return None
    lowered = entry_type.lower()
    if "unreviewed" in lowered or "trembl" in lowered:
      return False
    if "reviewed" in lowered or "swiss-prot" in lowered:
      return True
    return None

  @cached_property
  def uniprotkb_id(self) -> Optional[str]:
    value = self._data.get("uniProtkbId")
    return value if isinstance(value, str) else None

  @property
  def uniprot_id(self) -> Optional[str]:
    return self.uniprotkb_id

  @cached_property
  def sequence_data(self) -> Mapping[str, Any]:
    value = self._data.get("sequence")
    return value if isinstance(value, Mapping) else {}

  @cached_property
  def sequence(self) -> Optional[str]:
    return _value(self.sequence_data)

  @cached_property
  def protein_names(self) -> Tuple[str, ...]:
    description = self._data.get("proteinDescription")
    if not isinstance(description, Mapping):
      return ()
    values = []
    recommended = description.get("recommendedName")
    values.extend(_protein_name_values(recommended))
    for key in ("submissionNames", "alternativeNames"):
      names = description.get(key, ())
      if isinstance(names, (list, tuple)):
        for name in names:
          values.extend(_protein_name_values(name))
    for key in ("allergenName", "biotechName"):
      values.append(_value(description.get(key)))
    for key in ("cdAntigenNames", "innNames"):
      names = description.get(key, ())
      if isinstance(names, (list, tuple)):
        values.extend(_value(name) for name in names)
    includes = description.get("includes", ())
    if isinstance(includes, (list, tuple)):
      for component in includes:
        if isinstance(component, Mapping):
          values.extend(_protein_name_values(component.get("recommendedName")))
          names = component.get("alternativeNames", ())
          if isinstance(names, (list, tuple)):
            for name in names:
              values.extend(_protein_name_values(name))
    contains = description.get("contains", ())
    if isinstance(contains, (list, tuple)):
      for component in contains:
        if isinstance(component, Mapping):
          values.extend(_protein_name_values(component.get("recommendedName")))
          names = component.get("alternativeNames", ())
          if isinstance(names, (list, tuple)):
            for name in names:
              values.extend(_protein_name_values(name))
    return _unique(values)

  @property
  def all_protein_names(self) -> Tuple[str, ...]:
    return self.protein_names

  @cached_property
  def protein_name(self) -> Optional[str]:
    return self.protein_names[0] if self.protein_names else None

  @property
  def primary_protein_name(self) -> Optional[str]:
    return self.protein_name

  @cached_property
  def gene_names(self) -> Tuple[str, ...]:
    genes = self._data.get("genes", ())
    if not isinstance(genes, (list, tuple)):
      return ()
    values = []
    for gene in genes:
      if not isinstance(gene, Mapping):
        continue
      values.append(_value(gene.get("geneName")))
      for key in ("synonyms", "orderedLocusNames", "orfNames"):
        names = gene.get(key, ())
        if isinstance(names, (list, tuple)):
          values.extend(_value(name) for name in names)
    return _unique(values)

  @property
  def all_gene_names(self) -> Tuple[str, ...]:
    return self.gene_names

  @cached_property
  def gene_name(self) -> Optional[str]:
    genes = self._data.get("genes", ())
    if isinstance(genes, (list, tuple)):
      for gene in genes:
        if isinstance(gene, Mapping):
          name = _value(gene.get("geneName"))
          if name is not None:
            return name
    return self.gene_names[0] if self.gene_names else None

  @property
  def primary_gene_name(self) -> Optional[str]:
    return self.gene_name

  @cached_property
  def organism(self) -> Mapping[str, Any]:
    value = self._data.get("organism")
    return value if isinstance(value, Mapping) else {}

  @cached_property
  def organism_name(self) -> Optional[str]:
    value = self.organism.get("scientificName")
    return value if isinstance(value, str) else None

  @cached_property
  def taxon_id(self) -> Optional[int]:
    value = self.organism.get("taxonId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def protein_existence(self) -> Optional[str]:
    value = self._data.get("proteinExistence")
    return value if isinstance(value, str) else None

  @property
  def pe(self) -> Optional[str]:
    return self.protein_existence

  @cached_property
  def entry_audit(self) -> Mapping[str, Any]:
    value = self._data.get("entryAudit")
    return value if isinstance(value, Mapping) else {}

  @cached_property
  def entry_version(self) -> Optional[int]:
    value = self.entry_audit.get("entryVersion")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def sequence_version(self) -> Optional[int]:
    value = self.entry_audit.get("sequenceVersion")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

  @cached_property
  def features(self) -> Tuple[Mapping[str, Any], ...]:
    return self._mapping_items("features")

  @cached_property
  def comments(self) -> Tuple[Mapping[str, Any], ...]:
    return self._mapping_items("comments")

  @cached_property
  def cross_references(self) -> Tuple[Mapping[str, Any], ...]:
    return self._mapping_items("uniProtKBCrossReferences")

  @property
  def xrefs(self) -> Tuple[Mapping[str, Any], ...]:
    return self.cross_references

  @cached_property
  def keywords(self) -> Tuple[Mapping[str, Any], ...]:
    return self._mapping_items("keywords")

  def _mapping_items(self, key: str) -> Tuple[Mapping[str, Any], ...]:
    values = self._data.get(key, ())
    if not isinstance(values, (list, tuple)):
      return ()
    return tuple(value for value in values if isinstance(value, Mapping))

