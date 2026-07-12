"""Release-aware SQLite persistence for faithful UniProt entry documents."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from sqlalchemy import (
  Boolean,
  ForeignKeyConstraint,
  Index,
  Integer,
  JSON,
  String,
  Text,
  UniqueConstraint,
  create_engine,
  delete,
  event,
  select,
)
from sqlalchemy.engine import URL
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from uniprotpy import models as _models


class _StoreBase(DeclarativeBase):
  pass


class _DatasetRow(_StoreBase):
  __tablename__ = "datasets"

  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  key: Mapped[str] = mapped_column(String, unique=True, index=True)
  resource_kind: Mapped[str] = mapped_column(String, default="uniprotkb")
  requested_release: Mapped[Optional[str]] = mapped_column(String)
  observed_release: Mapped[Optional[str]] = mapped_column(String)
  observed_release_date: Mapped[Optional[str]] = mapped_column(String)
  source_url: Mapped[Optional[str]] = mapped_column(Text)
  source_query: Mapped[Optional[str]] = mapped_column(Text)
  source_format: Mapped[Optional[str]] = mapped_column(String)
  fields: Mapped[Optional[str]] = mapped_column(Text)
  include_isoforms: Mapped[Optional[bool]] = mapped_column(Boolean)
  fetched_at: Mapped[Optional[str]] = mapped_column(String)
  complete: Mapped[bool] = mapped_column(Boolean, default=False)
  next_page_url: Mapped[Optional[str]] = mapped_column(Text)
  provenance: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)


class _EntryRow(_StoreBase):
  __tablename__ = "entries"

  dataset_id: Mapped[int] = mapped_column(Integer, primary_key=True)
  accession: Mapped[str] = mapped_column(String, primary_key=True)
  canonical_accession: Mapped[str] = mapped_column(String, index=True)
  uniprotkb_id: Mapped[Optional[str]] = mapped_column(String, index=True)
  reviewed: Mapped[Optional[bool]] = mapped_column(Boolean, index=True)
  primary_protein_name: Mapped[Optional[str]] = mapped_column(Text)
  primary_protein_name_fold: Mapped[Optional[str]] = mapped_column(Text, index=True)
  primary_gene_name: Mapped[Optional[str]] = mapped_column(Text)
  taxon_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
  protein_existence: Mapped[Optional[str]] = mapped_column(String)
  entry_version: Mapped[Optional[int]] = mapped_column(Integer)
  sequence_version: Mapped[Optional[int]] = mapped_column(Integer)
  sequence: Mapped[Optional[str]] = mapped_column(Text)
  sequence_length: Mapped[Optional[int]] = mapped_column(Integer)
  sequence_checksum: Mapped[Optional[str]] = mapped_column(String)
  raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

  __table_args__ = (
    ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
    Index("ix_entries_dataset_protein_name", "dataset_id", "primary_protein_name_fold"),
  )


class _GeneNameRow(_StoreBase):
  __tablename__ = "entry_gene_names"

  id: Mapped[int] = mapped_column(Integer, primary_key=True)
  dataset_id: Mapped[int] = mapped_column(Integer, nullable=False)
  accession: Mapped[str] = mapped_column(String, nullable=False)
  name: Mapped[str] = mapped_column(Text, nullable=False)
  name_fold: Mapped[str] = mapped_column(Text, nullable=False, index=True)
  kind: Mapped[str] = mapped_column(String, nullable=False)
  ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

  __table_args__ = (
    ForeignKeyConstraint(
      ["dataset_id", "accession"],
      ["entries.dataset_id", "entries.accession"],
      ondelete="CASCADE",
    ),
    UniqueConstraint("dataset_id", "accession", "kind", "ordinal"),
    Index("ix_gene_names_dataset_name", "dataset_id", "name_fold"),
  )


def _database_url(value: Optional[Union[str, Path, URL]]) -> Union[str, URL]:
  if isinstance(value, URL):
    return value
  if value is None:
    return "sqlite:///:memory:"
  text = str(value)
  if text == ":memory:":
    return "sqlite:///:memory:"
  if "://" in text or text.startswith("sqlite:"):
    return text
  return "sqlite:///{}".format(Path(text).expanduser().resolve())


def _value(value: Any) -> Optional[str]:
  if isinstance(value, str):
    return value
  if isinstance(value, Mapping):
    nested = value.get("value")
    return nested if isinstance(nested, str) else None
  return None


def _primary_protein_name(raw: Mapping[str, Any]) -> Optional[str]:
  legacy = raw.get("protein_name")
  if isinstance(legacy, str):
    return legacy
  description = raw.get("proteinDescription")
  if not isinstance(description, Mapping):
    return None
  recommended = description.get("recommendedName")
  if isinstance(recommended, Mapping):
    name = _value(recommended.get("fullName"))
    if name:
      return name
  for key in ("submissionNames", "alternativeNames"):
    names = description.get(key)
    if isinstance(names, list):
      for item in names:
        if isinstance(item, Mapping):
          name = _value(item.get("fullName"))
          if name:
            return name
  return None


def _gene_names(raw: Mapping[str, Any]) -> list[tuple[str, str, int]]:
  result: list[tuple[str, str, int]] = []
  genes = raw.get("genes")
  if isinstance(genes, list):
    for gene in genes:
      if not isinstance(gene, Mapping):
        continue
      for source, kind in (
        ("geneName", "primary"),
        ("synonyms", "synonym"),
        ("orderedLocusNames", "ordered_locus"),
        ("orfNames", "orf"),
      ):
        values = gene.get(source)
        if source == "geneName":
          values = [values]
        if not isinstance(values, list):
          continue
        for item in values:
          name = _value(item)
          if name:
            result.append((name, kind, len(result)))
  legacy = raw.get("gene")
  if not result and isinstance(legacy, str) and legacy:
    result.append((legacy, "primary", 0))
  return result


def _int_value(value: Any) -> Optional[int]:
  if isinstance(value, bool):
    return None
  if isinstance(value, int):
    return value
  if isinstance(value, str):
    try:
      return int(value)
    except ValueError:
      return None
  return None


def _projections(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str, int]]]:
  accession = raw.get("primaryAccession", raw.get("protein_id"))
  if not isinstance(accession, str) or not accession:
    raise ValueError("UniProt entry has no primary accession")
  canonical = raw.get("canonicalAccession")
  if not isinstance(canonical, str) or not canonical:
    canonical = accession.split("-", 1)[0]
  names = _gene_names(raw)
  primary_gene = next((name for name, kind, _ in names if kind == "primary"), None)
  protein_name = _primary_protein_name(raw)
  organism = raw.get("organism")
  taxon_id = organism.get("taxonId") if isinstance(organism, Mapping) else raw.get("taxon_id")
  audit = raw.get("entryAudit")
  audit = audit if isinstance(audit, Mapping) else {}
  sequence = raw.get("sequence")
  if isinstance(sequence, Mapping):
    sequence_value = sequence.get("value")
    sequence_length = sequence.get("length")
    sequence_checksum = sequence.get("crc64") or sequence.get("md5")
  else:
    sequence_value = sequence if isinstance(sequence, str) else None
    sequence_length = len(sequence_value) if sequence_value is not None else None
    sequence_checksum = None
  entry_type = raw.get("entryType")
  reviewed = None
  if isinstance(entry_type, str):
    reviewed = "unreviewed" not in entry_type.casefold() and "reviewed" in entry_type.casefold()
  elif isinstance(raw.get("reviewed"), bool):
    reviewed = raw["reviewed"]
  existence = raw.get("proteinExistence", raw.get("pe_level"))
  return ({
    "accession": accession,
    "canonical_accession": canonical,
    "uniprotkb_id": raw.get("uniProtkbId"),
    "reviewed": reviewed,
    "primary_protein_name": protein_name,
    "primary_protein_name_fold": protein_name.casefold() if protein_name else None,
    "primary_gene_name": primary_gene,
    "taxon_id": _int_value(taxon_id),
    "protein_existence": str(existence) if existence is not None else None,
    "entry_version": _int_value(audit.get("entryVersion")),
    "sequence_version": _int_value(audit.get("sequenceVersion", raw.get("sequence_version"))),
    "sequence": sequence_value if isinstance(sequence_value, str) else None,
    "sequence_length": _int_value(sequence_length),
    "sequence_checksum": sequence_checksum if isinstance(sequence_checksum, str) else None,
  }, names)


def _entry_mapping(entry: Any) -> dict[str, Any]:
  if isinstance(entry, Mapping):
    return deepcopy(dict(entry))
  serializer = getattr(entry, "to_dict", None)
  if not callable(serializer):
    serializer = getattr(entry, "dict", None)
  if not callable(serializer):
    raise TypeError("entry must be a mapping or UniProtEntry domain value")
  raw = serializer()
  if not isinstance(raw, Mapping):
    raise TypeError("entry serializer must return a mapping")
  return deepcopy(dict(raw))


def _domain_entry(raw: Mapping[str, Any]) -> Any:
  entry_type = getattr(_models, "UniProtEntry", None)
  if entry_type is not None:
    factory = getattr(entry_type, "from_json", None)
    if callable(factory):
      return factory(deepcopy(dict(raw)))
    return entry_type(deepcopy(dict(raw)))
  legacy_type = getattr(_models, "UniprotEntry")
  return legacy_type(**deepcopy(dict(raw)))


class UniProtStore:
  """A SQLAlchemy 2.x SQLite store for release-scoped UniProt entries."""

  def __init__(
    self,
    database: Optional[Union[str, Path, URL]] = None,
    *,
    database_path: Optional[Union[str, Path, URL]] = None,
    url: Optional[Union[str, Path, URL]] = None,
    dataset_key: str = "default",
    resource_kind: Optional[str] = None,
    release: Optional[str] = None,
    requested_release: Optional[str] = None,
    observed_release: Optional[str] = None,
    observed_release_date: Optional[str] = None,
    source_url: Optional[str] = None,
    source_query: Optional[str] = None,
    source_format: Optional[str] = None,
    fields: Optional[Union[str, Iterable[str]]] = None,
    include_isoforms: Optional[bool] = None,
    fetched_at: Optional[Union[str, datetime]] = None,
    complete: Optional[bool] = None,
    next_page_url: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
  ) -> None:
    supplied = [item is not None for item in (database, database_path, url)]
    if sum(supplied) > 1:
      raise TypeError("pass only one of database, database_path, or url")
    target = database if database is not None else database_path if database_path is not None else url
    self.database_path = target
    self.dataset_key = dataset_key
    self.engine = create_engine(_database_url(target))
    if self.engine.dialect.name != "sqlite":
      self.engine.dispose()
      raise ValueError("UniProtStore currently supports SQLite URLs only")
    event.listen(self.engine, "connect", self._configure_sqlite)
    self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
    self._init_sqlite()
    self._ensure_dataset(
      resource_kind=resource_kind,
      requested_release=requested_release if requested_release is not None else release,
      observed_release=observed_release,
      observed_release_date=observed_release_date,
      source_url=source_url,
      source_query=source_query,
      source_format=source_format,
      fields=fields,
      include_isoforms=include_isoforms,
      fetched_at=fetched_at,
      complete=complete,
      next_page_url=next_page_url,
      provenance=provenance,
    )

  @staticmethod
  def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

  def _init_sqlite(self) -> None:
    _StoreBase.metadata.create_all(self.engine)

  @staticmethod
  def _timestamp(value: Optional[Union[str, datetime]]) -> Optional[str]:
    if value is None:
      return None
    if isinstance(value, datetime):
      if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
      return value.isoformat()
    return str(value)

  @staticmethod
  def _fields(value: Optional[Union[str, Iterable[str]]]) -> Optional[str]:
    if value is None or isinstance(value, str):
      return value
    return ",".join(value)

  def _ensure_dataset(self, *, overwrite_nulls: bool = False, **metadata: Any) -> None:
    metadata["fields"] = self._fields(metadata.get("fields"))
    metadata["fetched_at"] = self._timestamp(metadata.get("fetched_at"))
    if metadata.get("provenance") is not None:
      metadata["provenance"] = deepcopy(dict(metadata["provenance"]))
    with self.session.begin() as session:
      row = session.scalar(select(_DatasetRow).where(_DatasetRow.key == self.dataset_key))
      if row is None:
        metadata["resource_kind"] = metadata.get("resource_kind") or "uniprotkb"
        metadata["source_format"] = metadata.get("source_format") or "json"
        metadata["complete"] = bool(metadata.get("complete"))
        session.add(_DatasetRow(key=self.dataset_key, **metadata))
      else:
        for name, value in metadata.items():
          if value is not None or (overwrite_nulls and name == "next_page_url"):
            setattr(row, name, value)

  def set_release_metadata(self, **metadata: Any) -> None:
    allowed = {
      "resource_kind", "requested_release", "observed_release",
      "observed_release_date", "source_url", "source_query", "source_format",
      "fields", "include_isoforms", "fetched_at", "complete",
      "next_page_url", "provenance",
    }
    unexpected = set(metadata) - allowed
    if unexpected:
      raise TypeError("unexpected metadata: {}".format(", ".join(sorted(unexpected))))
    self._ensure_dataset(overwrite_nulls=True, **metadata)

  @property
  def release_metadata(self) -> dict[str, Any]:
    with self.session() as session:
      row = session.scalar(select(_DatasetRow).where(_DatasetRow.key == self.dataset_key))
      if row is None:
        raise RuntimeError("dataset metadata is missing")
      return {
        "dataset_key": row.key,
        "resource_kind": row.resource_kind,
        "requested_release": row.requested_release,
        "observed_release": row.observed_release,
        "observed_release_date": row.observed_release_date,
        "source_url": row.source_url,
        "source_query": row.source_query,
        "source_format": row.source_format,
        "fields": row.fields,
        "include_isoforms": row.include_isoforms,
        "fetched_at": row.fetched_at,
        "complete": row.complete,
        "next_page_url": row.next_page_url,
        "provenance": deepcopy(row.provenance),
      }

  def _dataset_id(self, session: Any) -> int:
    dataset_id = session.scalar(select(_DatasetRow.id).where(_DatasetRow.key == self.dataset_key))
    if dataset_id is None:
      raise RuntimeError("dataset metadata is missing")
    return dataset_id

  def add(self, entry: Any) -> Any:
    self.add_all([entry])
    return _domain_entry(_entry_mapping(entry))

  def add_all(self, entries: Iterable[Any]) -> int:
    prepared: list[tuple[dict[str, Any], dict[str, Any], list[tuple[str, str, int]]]] = []
    for entry in entries:
      raw = _entry_mapping(entry)
      projections, names = _projections(raw)
      prepared.append((raw, projections, names))
    if not prepared:
      return 0
    with self.session.begin() as session:
      dataset_id = self._dataset_id(session)
      for raw, projections, names in prepared:
        values = {"dataset_id": dataset_id, "raw_json": raw, **projections}
        statement = sqlite_insert(_EntryRow).values(**values)
        update_values = {
          column.name: statement.excluded[column.name]
          for column in _EntryRow.__table__.columns
          if column.name not in ("dataset_id", "accession")
        }
        session.execute(
          statement.on_conflict_do_update(
            index_elements=["dataset_id", "accession"],
            set_=update_values,
          )
        )
        session.execute(
          delete(_GeneNameRow).where(
            _GeneNameRow.dataset_id == dataset_id,
            _GeneNameRow.accession == projections["accession"],
          )
        )
        if names:
          session.execute(
            sqlite_insert(_GeneNameRow),
            [{
              "dataset_id": dataset_id,
              "accession": projections["accession"],
              "name": name,
              "name_fold": name.casefold(),
              "kind": kind,
              "ordinal": ordinal,
            } for name, kind, ordinal in names],
          )
    return len(prepared)

  batch_add = add_all
  upsert = add

  def get(self, accession: str) -> Optional[Any]:
    with self.session() as session:
      dataset_id = self._dataset_id(session)
      raw = session.scalar(
        select(_EntryRow.raw_json).where(
          _EntryRow.dataset_id == dataset_id,
          _EntryRow.accession == accession,
        )
      )
    return _domain_entry(raw) if raw is not None else None

  get_by_accession = get

  def entries_by_gene(self, name: str) -> list[Any]:
    folded = name.casefold()
    with self.session() as session:
      dataset_id = self._dataset_id(session)
      rows = session.scalars(
        select(_EntryRow.raw_json)
        .join(
          _GeneNameRow,
          (_GeneNameRow.dataset_id == _EntryRow.dataset_id)
          & (_GeneNameRow.accession == _EntryRow.accession),
        )
        .where(
          _EntryRow.dataset_id == dataset_id,
          _GeneNameRow.name_fold == folded,
        )
        .distinct()
        .order_by(_EntryRow.accession)
      ).all()
    return [_domain_entry(raw) for raw in rows]

  query_by_gene = entries_by_gene
  get_by_gene = entries_by_gene
  find_by_gene = entries_by_gene

  def entries_by_name(self, name: str) -> list[Any]:
    folded = name.casefold()
    with self.session() as session:
      dataset_id = self._dataset_id(session)
      rows = session.scalars(
        select(_EntryRow.raw_json)
        .where(
          _EntryRow.dataset_id == dataset_id,
          _EntryRow.primary_protein_name_fold == folded,
        )
        .order_by(_EntryRow.accession)
      ).all()
    return [_domain_entry(raw) for raw in rows]

  query_by_name = entries_by_name
  get_by_name = entries_by_name
  find_by_name = entries_by_name

  def list_identifiers(self) -> list[str]:
    with self.session() as session:
      dataset_id = self._dataset_id(session)
      return list(session.scalars(
        select(_EntryRow.accession)
        .where(_EntryRow.dataset_id == dataset_id)
        .order_by(_EntryRow.accession)
      ))

  list_accessions = list_identifiers

  def list(self) -> list[Any]:
    with self.session() as session:
      dataset_id = self._dataset_id(session)
      rows = session.scalars(
        select(_EntryRow.raw_json)
        .where(_EntryRow.dataset_id == dataset_id)
        .order_by(_EntryRow.accession)
      ).all()
    return [_domain_entry(raw) for raw in rows]

  def close(self) -> None:
    self.engine.dispose()

  def __enter__(self) -> "UniProtStore":
    return self

  def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
    self.close()


class UniprotDatabase(UniProtStore):
  """Compatibility spelling and constructor for the original database class."""

  def __init__(
    self,
    species: Optional[str] = None,
    proteome_id: Optional[str] = None,
    database_path: Optional[Union[str, Path, URL]] = None,
    **kwargs: Any,
  ) -> None:
    if database_path is None and proteome_id is None and species is not None:
      database_path = species
      species = None
    self.species = species
    self.proteome_id = proteome_id
    super().__init__(database_path=database_path, **kwargs)


UniProtDatabase = UniprotDatabase

__all__ = ["UniProtStore", "UniprotDatabase", "UniProtDatabase"]
