"""Polite, release-aware HTTP transport for UniProt REST resources."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import random
import re
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from .idmapping import (
  IDMappingConfiguration,
  IDMappingDetails,
  IDMappingJob,
  IDMappingMatch,
  IDMappingPage,
  IDMappingResult,
  IDMappingStatus,
)
from .models import UniProtEntry
from .proteomes import Proteome
from .taxonomy import Taxon


_DEFAULT_BASE_URL = "https://rest.uniprot.org"
_RETRY_STATUSES = frozenset((429, 500, 502, 503, 504))
_FORMAT = re.compile(r"^[A-Za-z0-9]+$")
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*[^,]*\brel\s*=\s*["\']?next["\']?', re.I)


@dataclass(frozen=True)
class ResponseMetadata:
  """Data-release and response provenance supplied by UniProt."""

  release: Optional[str]
  release_date: Optional[str]
  total_results: Optional[int]
  url: str
  content_type: Optional[str]
  status_code: Optional[int] = None
  location: Optional[str] = None

  @property
  def uniprot_release(self) -> Optional[str]:
    return self.release

  @property
  def uniprot_release_date(self) -> Optional[str]:
    return self.release_date


@dataclass(frozen=True)
class EntryResponse:
  entry: UniProtEntry
  metadata: ResponseMetadata


@dataclass(frozen=True)
class TextResponse:
  text: str
  metadata: ResponseMetadata


@dataclass(frozen=True)
class EntryPage:
  entries: Tuple[UniProtEntry, ...]
  metadata: ResponseMetadata
  next_url: Optional[str]

  @property
  def results(self) -> Tuple[UniProtEntry, ...]:
    return self.entries


@dataclass(frozen=True)
class ProteomeResponse:
  proteome: Proteome
  metadata: ResponseMetadata


@dataclass(frozen=True)
class ProteomePage:
  proteomes: Tuple[Proteome, ...]
  metadata: ResponseMetadata
  next_url: Optional[str]

  @property
  def results(self) -> Tuple[Proteome, ...]:
    return self.proteomes

@dataclass(frozen=True)
class TaxonResponse:
  taxon: Taxon
  metadata: ResponseMetadata


@dataclass(frozen=True)
class TaxonPage:
  taxa: Tuple[Taxon, ...]
  metadata: ResponseMetadata
  next_url: Optional[str]

  @property
  def results(self) -> Tuple[Taxon, ...]:
    return self.taxa


class UniProtError(RuntimeError):
  """Base class for UniProt transport and response errors."""


class UniProtHTTPError(UniProtError):
  def __init__(
    self, status_code: int, url: str, message: str, method: str = "GET"
  ):
    self.status_code = status_code
    self.url = url
    self.message = message
    self.method = method
    super().__init__("UniProt {} {} failed with HTTP {}: {}".format(
      method, url, status_code, message
    ))


class UniProtResponseError(UniProtError):
  """Raised when a successful response does not have the promised shape."""


class UniProtClient:
  """A pooled client for direct entries and cursor-paginated searches.

  ``max_retries`` counts retries after the initial attempt. Only idempotent GET
  requests are made here, and only transient statuses are retried.
  """

  def __init__(
    self,
    base_url: str = _DEFAULT_BASE_URL,
    user_agent: str = "UniProtPy/0.0.1 (+https://github.com/danielmarrama/uniprotpy)",
    timeout: Tuple[float, float] = (5.0, 30.0),
    max_retries: int = 3,
    backoff_factor: float = 0.5,
    max_backoff: float = 8.0,
    pool_connections: int = 10,
    pool_maxsize: int = 10,
    session: Optional[requests.Session] = None,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
  ):
    if not base_url:
      raise ValueError("base_url must not be empty")
    if not user_agent or user_agent.lower().startswith("python-requests"):
      raise ValueError("a descriptive user_agent is required")
    if len(timeout) != 2 or timeout[0] <= 0 or timeout[1] <= 0:
      raise ValueError("timeout must contain positive connect and read values")
    if max_retries < 0:
      raise ValueError("max_retries must be non-negative")
    if backoff_factor < 0 or max_backoff < 0:
      raise ValueError("backoff values must be non-negative")

    self.base_url = base_url.rstrip("/")
    self.timeout = timeout
    self.max_retries = max_retries
    self.backoff_factor = backoff_factor
    self.max_backoff = max_backoff
    self._sleep = sleep
    self._random = random_value
    self._owns_session = session is None
    self.session = session if session is not None else requests.Session()
    if self._owns_session:
      adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=0,
      )
      self.session.mount("https://", adapter)
      self.session.mount("http://", adapter)
    self.session.headers.setdefault("User-Agent", user_agent)

  def close(self) -> None:
    if self._owns_session:
      self.session.close()

  def __enter__(self) -> "UniProtClient":
    return self

  def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
    self.close()

  def get_entry(self, accession: str) -> EntryResponse:
    """Retrieve a faithful JSON entry and its release metadata."""
    response = self._get(self._entry_url(accession, "json"))
    data = self._json_object(response, "entry")
    return EntryResponse(UniProtEntry.from_json(data), self._metadata(response))

  def get_entry_text(self, accession: str, format: str = "fasta") -> TextResponse:
    """Retrieve an entry representation such as FASTA or UniProt flat text."""
    response = self._get(self._entry_url(accession, format))
    return TextResponse(response.text, self._metadata(response))

  def get_search_page(
    self,
    query: Optional[str] = None,
    *,
    size: int = 500,
    fields: Optional[str] = None,
    include_isoform: bool = False,
    url: Optional[str] = None,
  ) -> EntryPage:
    """Fetch one search page, or follow an opaque absolute cursor URL.

    When ``url`` is supplied it is requested verbatim and all other search
    arguments are ignored. This is intentional: cursor links are server-owned.
    """
    if url is not None:
      response = self._get(url)
    else:
      if query is None or not query.strip():
        raise ValueError("query must not be empty")
      if size < 1 or size > 500:
        raise ValueError("size must be between 1 and 500")
      params = {
        "query": query,
        "format": "json",
        "size": str(size),
      }
      if fields is not None:
        params["fields"] = fields
      if include_isoform:
        params["includeIsoform"] = "true"
      response = self._get(self.base_url + "/uniprotkb/search", params=params)
    payload = self._json_object(response, "search page")
    results = payload.get("results")
    if not isinstance(results, list):
      raise UniProtResponseError(
        "UniProt search response from {} has no results list".format(response.url)
      )
    entries = []
    for index, result in enumerate(results):
      if not isinstance(result, Mapping):
        raise UniProtResponseError(
          "UniProt search result {} from {} is not an object".format(
            index, response.url
          )
        )
      entries.append(UniProtEntry.from_json(result))
    return EntryPage(tuple(entries), self._metadata(response), self._next_url(response))

  def search_entries(
    self,
    query: str,
    *,
    size: int = 500,
    fields: Optional[str] = None,
    include_isoform: bool = False,
  ) -> Iterator[EntryPage]:
    """Yield cursor pages, following every next link verbatim."""
    page = self.get_search_page(
      query,
      size=size,
      fields=fields,
      include_isoform=include_isoform,
    )
    while True:
      yield page
      if page.next_url is None:
        return
      page = self.get_search_page(url=page.next_url)

  def get_proteome(self, upid: str) -> ProteomeResponse:
    """Retrieve a faithful proteome JSON document and response metadata."""
    if not isinstance(upid, str) or not upid.strip():
      raise ValueError("upid must not be empty")
    url = "{}/proteomes/{}".format(
      self.base_url, quote(upid.strip(), safe="")
    )
    response = self._get(url, params={"format": "json"})
    data = self._json_object(response, "proteome")
    return ProteomeResponse(Proteome.from_json(data), self._metadata(response))

  def get_proteome_search_page(
    self,
    query: Optional[str] = None,
    *,
    size: int = 500,
    url: Optional[str] = None,
  ) -> ProteomePage:
    """Fetch one proteome-search page or follow an opaque cursor URL."""
    if url is not None:
      response = self._get(url)
    else:
      if query is None or not query.strip():
        raise ValueError("query must not be empty")
      if size < 1 or size > 500:
        raise ValueError("size must be between 1 and 500")
      response = self._get(
        self.base_url + "/proteomes/search",
        params={"query": query, "format": "json", "size": str(size)},
      )
    payload = self._json_object(response, "proteome search page")
    results = payload.get("results")
    if not isinstance(results, list):
      raise UniProtResponseError(
        "UniProt proteome search response from {} has no results list".format(
          response.url
        )
      )
    proteomes = []
    for index, result in enumerate(results):
      if not isinstance(result, Mapping):
        raise UniProtResponseError(
          "UniProt proteome search result {} from {} is not an object".format(
            index, response.url
          )
        )
      proteomes.append(Proteome.from_json(result))
    return ProteomePage(
      tuple(proteomes), self._metadata(response), self._next_url(response)
    )

  def search_proteomes(
    self, query: str, *, size: int = 500
  ) -> Iterator[ProteomePage]:
    """Yield every cursor-paginated proteome search page."""
    page = self.get_proteome_search_page(query, size=size)
    while True:
      yield page
      if page.next_url is None:
        return
      page = self.get_proteome_search_page(url=page.next_url)

  def reference_proteomes(
    self, taxon_id: int, scope: str = "lineage", *, size: int = 500
  ) -> Tuple[Proteome, ...]:
    """Return reference proteomes for a taxon, optionally filtering exactly.

    UniProt's ``taxonomy_id`` query includes descendants. ``scope='exact'``
    therefore performs an explicit client-side taxon-ID filter; lineage scope
    preserves all server-returned descendants.
    """
    taxon_id = self.canonical_taxon_id(taxon_id)
    if scope not in ("exact", "lineage"):
      raise ValueError("scope must be 'exact' or 'lineage'")
    query = "(taxonomy_id:{}) AND (proteome_type:REFERENCE)".format(taxon_id)
    proteomes = tuple(
      proteome
      for page in self.search_proteomes(query, size=size)
      for proteome in page.proteomes
      if scope == "lineage" or proteome.taxon_id == taxon_id
    )
    return proteomes

  def get_taxon(self, taxon_id: int) -> TaxonResponse:
    """Retrieve one faithful taxonomy document and response metadata."""
    taxon_id = self._positive_taxon_id(taxon_id)
    response = self._get(
      "{}/taxonomy/{}".format(self.base_url, taxon_id),
      params={"format": "json"},
      allow_redirects=False,
    )
    data = self._json_object(response, "taxonomy entry")
    return TaxonResponse(Taxon.from_json(data), self._metadata(response))

  def canonical_taxon_id(self, taxon_id: int, *, max_redirects: int = 10) -> int:
    """Resolve merged inactive taxonomy IDs before downstream selection."""
    current = self._positive_taxon_id(taxon_id)
    if max_redirects < 0:
      raise ValueError("max_redirects must be non-negative")
    seen = set()
    for _ in range(max_redirects + 1):
      if current in seen:
        raise UniProtResponseError("taxonomy redirect cycle at {}".format(current))
      seen.add(current)
      taxon = self.get_taxon(current).taxon
      if taxon.active is not False or taxon.merged_to is None:
        return taxon.taxon_id or current
      current = taxon.merged_to
    raise UniProtResponseError(
      "taxonomy redirect chain exceeds {} hops".format(max_redirects)
    )

  def get_taxonomy_search_page(
    self,
    query: Optional[str] = None,
    *,
    size: int = 500,
    url: Optional[str] = None,
  ) -> TaxonPage:
    """Fetch one taxonomy-search page or follow an opaque cursor URL."""
    if url is not None:
      response = self._get(url)
    else:
      if query is None or not query.strip():
        raise ValueError("query must not be empty")
      if size < 1 or size > 500:
        raise ValueError("size must be between 1 and 500")
      response = self._get(
        self.base_url + "/taxonomy/search",
        params={"query": query, "format": "json", "size": str(size)},
      )
    payload = self._json_object(response, "taxonomy search page")
    results = payload.get("results")
    if not isinstance(results, list):
      raise UniProtResponseError(
        "UniProt taxonomy search response from {} has no results list".format(
          response.url
        )
      )
    taxa = []
    for index, result in enumerate(results):
      if not isinstance(result, Mapping):
        raise UniProtResponseError(
          "UniProt taxonomy result {} from {} is not an object".format(
            index, response.url
          )
        )
      taxa.append(Taxon.from_json(result))
    return TaxonPage(tuple(taxa), self._metadata(response), self._next_url(response))

  def search_taxa(self, query: str, *, size: int = 500) -> Iterator[TaxonPage]:
    """Yield every cursor-paginated taxonomy search page."""
    page = self.get_taxonomy_search_page(query, size=size)
    while True:
      yield page
      if page.next_url is None:
        return
      page = self.get_taxonomy_search_page(url=page.next_url)

  def get_id_mapping_configuration(self) -> IDMappingConfiguration:
    """Discover current mapping databases, valid pairs, and taxId support."""
    response = self._get(self.base_url + "/configure/idmapping/fields")
    return IDMappingConfiguration.from_json(
      self._json_object(response, "ID Mapping field configuration"),
      metadata=self._metadata(response),
    )

  def submit_id_mapping(
    self,
    from_db: str,
    to_db: str,
    ids: Iterable[str],
    *,
    taxon_id: Optional[int] = None,
    configuration: Optional[IDMappingConfiguration] = None,
  ) -> IDMappingJob:
    """Validate against discovered capabilities and submit one mapping job."""
    if not isinstance(from_db, str) or not from_db:
      raise ValueError("from_db must be a nonempty string")
    if not isinstance(to_db, str) or not to_db:
      raise ValueError("to_db must be a nonempty string")
    if isinstance(ids, (str, bytes)):
      raise TypeError("ids must be an iterable of identifier strings")
    values = tuple(ids)
    if not values:
      raise ValueError("ids must not be empty")
    if len(values) > 100000:
      raise ValueError("ID Mapping accepts at most 100000 source identifiers")
    if any(not isinstance(value, str) or not value or "," in value for value in values):
      raise ValueError("each ID Mapping identifier must be a nonempty string without commas")
    if taxon_id is not None:
      taxon_id = self._positive_taxon_id(taxon_id)
    capabilities = configuration or self.get_id_mapping_configuration()
    capabilities.validate(from_db, to_db, taxon_id)
    data = {"from": from_db, "to": to_db, "ids": ",".join(values)}
    if taxon_id is not None:
      data["taxId"] = str(taxon_id)
    response = self._post(self.base_url + "/idmapping/run", data=data)
    payload = self._json_object(response, "ID Mapping submission")
    job_id = payload.get("jobId")
    if not isinstance(job_id, str) or not job_id:
      raise UniProtResponseError(
        "UniProt ID Mapping submission from {} has no jobId".format(response.url)
      )
    return IDMappingJob(
      job_id,
      from_db,
      to_db,
      values,
      taxon_id,
      raw=deepcopy(dict(payload)),
      metadata=self._metadata(response),
    )

  def get_id_mapping_status(self, job: Any) -> IDMappingStatus:
    """Poll one mapping job without following its 303 result redirect."""
    job_id = self._job_id(job)
    response = self._get(
      "{}/idmapping/status/{}".format(
        self.base_url, quote(job_id, safe="")
      ),
      allow_redirects=False,
      allowed_statuses=(400, 500),
    )
    payload = self._optional_json_object(response, "ID Mapping status")
    if response.status_code >= 400 and payload.get("jobStatus") != "ERROR":
      raise self._http_error(response)
    location = response.headers.get("Location")
    return IDMappingStatus(
      job_id,
      payload,
      location if isinstance(location, str) and location else None,
      metadata=self._metadata(response),
    )

  def get_id_mapping_details(self, job: Any) -> IDMappingDetails:
    """Read job details and the authoritative results redirect URL."""
    job_id = self._job_id(job)
    response = self._get(
      "{}/idmapping/details/{}".format(
        self.base_url, quote(job_id, safe="")
      )
    )
    return IDMappingDetails(
      job_id,
      self._json_object(response, "ID Mapping details"),
      metadata=self._metadata(response),
    )

  def get_id_mapping_page(
    self,
    url: str,
    *,
    size: Optional[int] = None,
  ) -> IDMappingPage:
    """Fetch one JSON result page, following cursor URLs verbatim."""
    if not isinstance(url, str) or not url:
      raise ValueError("ID Mapping result URL must not be empty")
    if size is not None and (size < 1 or size > 500):
      raise ValueError("size must be between 1 and 500")
    params = {"format": "json", "size": str(size)} if size is not None else None
    response = self._get(url, params=params)
    payload = self._json_object(response, "ID Mapping results page")
    result_values = payload.get("results", ())
    if not isinstance(result_values, list):
      raise UniProtResponseError(
        "UniProt ID Mapping response from {} has no results list".format(response.url)
      )
    results = []
    for index, value in enumerate(result_values):
      if not isinstance(value, Mapping):
        raise UniProtResponseError(
          "UniProt ID Mapping result {} from {} is not an object".format(
            index, response.url
          )
        )
      results.append(IDMappingMatch(value))
    failed = payload.get("failedIds", ())
    warnings = payload.get("warnings", ())
    return IDMappingPage(
      tuple(results),
      tuple(value for value in failed if isinstance(value, str))
        if isinstance(failed, (list, tuple)) else (),
      tuple(deepcopy(value) for value in warnings)
        if isinstance(warnings, (list, tuple)) else (),
      self._metadata(response),
      self._next_url(response),
      deepcopy(dict(payload)),
    )

  def id_mapping_results(
    self, job: Any, *, size: int = 500
  ) -> Iterator[IDMappingPage]:
    """Yield all paginated mapping results from the details redirect."""
    if size < 1 or size > 500:
      raise ValueError("size must be between 1 and 500")
    details = job if isinstance(job, IDMappingDetails) else self.get_id_mapping_details(job)
    url = details.redirect_url
    if url is None:
      raise UniProtResponseError(
        "UniProt ID Mapping details for {} have no redirectURL".format(details.job_id)
      )
    page = self.get_id_mapping_page(url, size=size)
    while True:
      yield page
      if page.next_url is None:
        return
      page = self.get_id_mapping_page(page.next_url)

  def wait_for_mapping(
    self,
    job: IDMappingJob,
    *,
    poll_interval: float = 3.0,
    timeout: float = 300.0,
    size: int = 500,
  ) -> IDMappingResult:
    """Poll until ready, then consume every authoritative result page."""
    if not isinstance(job, IDMappingJob):
      raise TypeError("job must be an IDMappingJob")
    if poll_interval < 0:
      raise ValueError("poll_interval must be non-negative")
    if timeout <= 0:
      raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout
    while True:
      status = self.get_id_mapping_status(job)
      if status.failed:
        raise UniProtError(
          "UniProt ID Mapping job {} failed{}".format(
            job.job_id, ": " + status.message if status.message else ""
          )
        )
      if status.finished:
        break
      if time.monotonic() >= deadline:
        raise TimeoutError(
          "timed out waiting for UniProt ID Mapping job {}".format(job.job_id)
        )
      self._sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
    details = self.get_id_mapping_details(job)
    return IDMappingResult(
      job, status, details, tuple(self.id_mapping_results(details, size=size))
    )

  @staticmethod
  def _positive_taxon_id(taxon_id: int) -> int:
    if isinstance(taxon_id, bool) or not isinstance(taxon_id, int) or taxon_id < 1:
      raise ValueError("taxon_id must be a positive integer")
    return taxon_id

  @staticmethod
  def _job_id(job: Any) -> str:
    job_id = job.job_id if isinstance(job, (IDMappingJob, IDMappingDetails)) else job
    if not isinstance(job_id, str) or not job_id:
      raise ValueError("job must be a nonempty job ID or ID Mapping domain value")
    return job_id

  def _entry_url(self, accession: str, format: str) -> str:
    if not isinstance(accession, str) or not accession.strip():
      raise ValueError("accession must not be empty")
    if not isinstance(format, str) or _FORMAT.fullmatch(format) is None:
      raise ValueError("format must contain only letters and digits")
    return "{}/uniprotkb/{}.{}".format(
      self.base_url, quote(accession.strip(), safe=""), format.lower()
    )

  def _get(
    self,
    url: str,
    params: Optional[Mapping[str, str]] = None,
    *,
    allow_redirects: bool = True,
    allowed_statuses: Tuple[int, ...] = (),
  ) -> requests.Response:
    for attempt in range(self.max_retries + 1):
      try:
        if allow_redirects:
          response = self.session.get(url, params=params, timeout=self.timeout)
        else:
          response = self.session.get(
            url, params=params, timeout=self.timeout, allow_redirects=False
          )
      except requests.RequestException as error:
        raise UniProtError("UniProt GET {} failed: {}".format(url, error)) from error
      if response.status_code in allowed_statuses:
        return response
      if response.status_code not in _RETRY_STATUSES:
        if response.status_code >= 400:
          raise self._http_error(response)
        return response
      if attempt == self.max_retries:
        raise self._http_error(response)
      delay = self._retry_delay(response, attempt)
      response.close()
      self._sleep(delay)
    raise AssertionError("retry loop exhausted unexpectedly")

  def _post(self, url: str, data: Mapping[str, str]) -> requests.Response:
    """Issue one non-retried POST; duplicate mapping submissions are unsafe."""
    try:
      response = self.session.post(url, data=data, timeout=self.timeout)
    except requests.RequestException as error:
      raise UniProtError("UniProt POST {} failed: {}".format(url, error)) from error
    if response.status_code >= 400:
      raise self._http_error(response, method="POST")
    return response

  def _retry_delay(self, response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
      parsed = self._parse_retry_after(retry_after)
      if parsed is not None:
        return parsed
    exponential = min(self.max_backoff, self.backoff_factor * (2 ** attempt))
    return exponential * (0.5 + (0.5 * self._random()))

  @staticmethod
  def _parse_retry_after(value: str) -> Optional[float]:
    try:
      return max(0.0, float(value.strip()))
    except (TypeError, ValueError):
      pass
    try:
      target = parsedate_to_datetime(value)
      if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
      return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
      return None

  @staticmethod
  def _metadata(response: requests.Response) -> ResponseMetadata:
    total = response.headers.get("X-Total-Results")
    try:
      total_results = int(total) if total is not None else None
    except ValueError:
      total_results = None
    return ResponseMetadata(
      release=response.headers.get("X-UniProt-Release"),
      release_date=response.headers.get("X-UniProt-Release-Date"),
      total_results=total_results,
      url=response.url,
      content_type=response.headers.get("Content-Type"),
      status_code=response.status_code,
      location=response.headers.get("Location"),
    )

  @staticmethod
  def _next_url(response: requests.Response) -> Optional[str]:
    link = response.headers.get("Link")
    if not link:
      return None
    match = _NEXT_LINK.search(link)
    return match.group(1) if match else None

  @staticmethod
  def _json_object(response: requests.Response, description: str) -> Mapping[str, Any]:
    try:
      payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
      raise UniProtResponseError(
        "UniProt {} response from {} is not valid JSON".format(
          description, response.url
        )
      ) from error
    if not isinstance(payload, Mapping):
      raise UniProtResponseError(
        "UniProt {} response from {} is not a JSON object".format(
          description, response.url
        )
      )
    return payload

  @classmethod
  def _optional_json_object(
    cls, response: requests.Response, description: str
  ) -> Mapping[str, Any]:
    if not response.content:
      return {}
    return cls._json_object(response, description)

  @staticmethod
  def _http_error(
    response: requests.Response, method: str = "GET"
  ) -> UniProtHTTPError:
    message = "request failed"
    try:
      payload = response.json()
      if isinstance(payload, Mapping):
        messages = payload.get("messages")
        if isinstance(messages, list):
          rendered = [str(item) for item in messages if item is not None]
          if rendered:
            message = "; ".join(rendered)
        elif payload.get("message") is not None:
          message = str(payload["message"])
    except (ValueError, json.JSONDecodeError):
      text = response.text.strip()
      if text:
        message = text[:500]
    return UniProtHTTPError(
      response.status_code, response.url, message, method=method
    )
