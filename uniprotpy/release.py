"""Release-scoped, lazily opened UniProt entry cache."""

from __future__ import annotations

from collections.abc import Iterable, Sized
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Optional, Union

from .cache import resolve_cache_dir
from .client import EntryResponse, UniProtClient
from .database import UniProtStore


_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DIRECT_ENTRY_LIMIT = 50


class ReleaseMismatchError(ValueError):
  """The service returned data from a release other than the requested one."""

  def __init__(self, requested: str, observed: Optional[str]):
    self.requested = requested
    self.observed = observed
    description = repr(observed) if observed is not None else "no release header"
    super().__init__(
      "requested UniProt release {!r}, but the response reported {}; "
      "pass allow_release_mismatch=True to accept it".format(requested, description)
    )


class UniProtRelease:
  """A release identity bound to a deterministic, persistent entry store.

  Constructing a handle performs no network or filesystem I/O. The SQLite
  store is created only when ``store`` or a store-backed operation is used.
  """

  def __init__(
    self,
    release: str,
    cache_dir: Optional[Union[str, Path]] = None,
    client: Optional[UniProtClient] = None,
    allow_release_mismatch: bool = False,
  ) -> None:
    if not isinstance(release, str) or not release:
      raise ValueError("release must be a nonempty string")
    if not _SAFE_RELEASE.fullmatch(release):
      raise ValueError(
        "release must contain only letters, numbers, '.', '_', or '-' and "
        "must start with a letter or number"
      )
    self.release = release
    self.cache_root = resolve_cache_dir(cache_dir)
    self.cache_dir = self.cache_root / release
    self.database_path = self.cache_dir / "entries.sqlite3"
    self.client = client if client is not None else UniProtClient()
    self.allow_release_mismatch = bool(allow_release_mismatch)
    self._owns_client = client is None
    self._store: Optional[UniProtStore] = None

  def __str__(self) -> str:
    return self.release

  def __repr__(self) -> str:
    return "UniProtRelease(release={!r}, cache_dir={!r})".format(
      self.release, str(self.cache_root)
    )

  def __eq__(self, other: object) -> bool:
    if not isinstance(other, UniProtRelease):
      return NotImplemented
    return self.release == other.release and self.cache_root == other.cache_root

  def __hash__(self) -> int:
    return hash((self.release, self.cache_root))

  @property
  def store(self) -> UniProtStore:
    if self._store is None:
      self.cache_dir.mkdir(parents=True, exist_ok=True)
      store = UniProtStore(
        self.database_path,
        dataset_key="uniprotkb:{}".format(self.release),
        resource_kind="uniprotkb",
        requested_release=self.release,
        source_format="json",
      )
      observed = store.release_metadata["observed_release"]
      if (
        observed is not None
        and observed != self.release
        and not self.allow_release_mismatch
      ):
        store.close()
        raise ReleaseMismatchError(self.release, observed)
      self._store = store
    return self._store

  @staticmethod
  def _bounded_accessions(accessions: Iterable[str]) -> Iterable[str]:
    if isinstance(accessions, (str, bytes)):
      raise TypeError("accessions must be an iterable of accession strings")
    if isinstance(accessions, Sized):
      count = len(accessions)
      bounded = accessions
    else:
      bounded = tuple(accessions)
      count = len(bounded)
    if count >= _DIRECT_ENTRY_LIMIT:
      raise ValueError(
        "install_entries accepts fewer than 50 accessions; use UniProt ID "
        "Mapping or a bulk query once bulk installation is available"
      )
    return bounded

  def _validate_observed_release(self, observed: Optional[str]) -> None:
    if observed != self.release and not self.allow_release_mismatch:
      raise ReleaseMismatchError(self.release, observed)

  def install_entries(self, accessions: Iterable[str]) -> int:
    """Fetch and atomically cache a small set of direct entry responses."""
    bounded = self._bounded_accessions(accessions)
    responses: list[EntryResponse] = []
    observed_release: Optional[str] = None
    observed_date: Optional[str] = None
    source_url: Optional[str] = None
    accession_values: list[str] = []

    for accession in bounded:
      if not isinstance(accession, str) or not accession:
        raise ValueError("each accession must be a nonempty string")
      response = self.client.get_entry(accession)
      self._validate_observed_release(response.metadata.release)
      if observed_release is None:
        observed_release = response.metadata.release
      elif response.metadata.release != observed_release:
        raise ReleaseMismatchError(self.release, response.metadata.release)
      if observed_date is None:
        observed_date = response.metadata.release_date
      if source_url is None:
        source_url = response.metadata.url
      responses.append(response)
      accession_values.append(accession)

    if not responses:
      return 0

    store = self.store
    store.set_release_metadata(
      requested_release=self.release,
      observed_release=observed_release,
      observed_release_date=observed_date,
      source_url=source_url,
      source_query="accession:({})".format(" OR ".join(accession_values)),
      source_format="json",
      fetched_at=datetime.now(timezone.utc),
      complete=True,
      provenance={
        "install_method": "direct_entries",
        "entry_count": len(responses),
      },
    )
    return store.add_all(response.entry for response in responses)

  def install_proteome(self, upid: str) -> int:
    """Install all UniProtKB entries belonging to one validated proteome.

    Entries are fetched through the cursor-paginated UniProtKB search endpoint
    and committed in page-sized atomic batches with explicit membership and
    release provenance. No direct per-entry requests are made.
    """
    if not isinstance(upid, str) or not upid.strip():
      raise ValueError("upid must be a nonempty string")
    upid = upid.strip()
    metadata_response = self.client.get_proteome(upid)
    proteome = metadata_response.proteome
    if proteome.upid != upid:
      raise ValueError(
        "proteome response ID {!r} does not match requested {!r}".format(
          proteome.upid, upid
        )
      )
    if proteome.taxon_id is None:
      raise ValueError("proteome response is missing a valid taxonomy taxonId")
    self._validate_observed_release(metadata_response.metadata.release)

    query = "proteome:{}".format(upid)
    store = self.store
    store.clear_proteome_membership(upid)
    provenance = {
      "install_method": "proteome_search",
      "proteome_id": upid,
      "proteome_taxon_id": proteome.taxon_id,
      "proteome": proteome.to_dict(),
      "entry_count": 0,
    }
    store.set_release_metadata(
      requested_release=self.release,
      observed_release=metadata_response.metadata.release,
      observed_release_date=metadata_response.metadata.release_date,
      source_url=metadata_response.metadata.url,
      source_query=query,
      source_format="json",
      fetched_at=datetime.now(timezone.utc),
      complete=False,
      next_page_url=None,
      provenance=provenance,
    )

    count = 0
    observed_release = metadata_response.metadata.release
    observed_date = metadata_response.metadata.release_date
    source_url = metadata_response.metadata.url
    for page in self.client.search_entries(query, size=500):
      self._validate_observed_release(page.metadata.release)
      if observed_release is None:
        observed_release = page.metadata.release
      elif page.metadata.release != observed_release:
        raise ReleaseMismatchError(self.release, page.metadata.release)
      if observed_date is None:
        observed_date = page.metadata.release_date
      source_url = page.metadata.url
      count += store.add_proteome_entries(upid, page.entries)
      provenance["entry_count"] = count
      store.set_release_metadata(
        observed_release=observed_release,
        observed_release_date=observed_date,
        source_url=source_url,
        next_page_url=page.next_url,
        complete=page.next_url is None,
        provenance=provenance,
      )

    store.set_release_metadata(
      observed_release=observed_release,
      observed_release_date=observed_date,
      source_url=source_url,
      next_page_url=None,
      complete=True,
      provenance=provenance,
    )
    return count

  def entry(self, accession: str) -> Any:
    return self.store.get(accession)

  def entries_by_gene(self, name: str) -> list[Any]:
    return self.store.entries_by_gene(name)

  def entries_by_name(self, name: str) -> list[Any]:
    return self.store.entries_by_name(name)

  def close(self) -> None:
    if self._store is not None:
      self._store.close()
      self._store = None
    if self._owns_client:
      self.client.close()

  def __enter__(self) -> "UniProtRelease":
    return self

  def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
    self.close()
