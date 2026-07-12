"""Polite, release-aware HTTP transport for UniProt REST resources."""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import random
import re
import time
from typing import Any, Callable, Iterator, Mapping, Optional, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from .models import UniProtEntry
from .proteomes import Proteome


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


class UniProtError(RuntimeError):
  """Base class for UniProt transport and response errors."""


class UniProtHTTPError(UniProtError):
  def __init__(self, status_code: int, url: str, message: str):
    self.status_code = status_code
    self.url = url
    self.message = message
    super().__init__("UniProt GET {} failed with HTTP {}: {}".format(
      url, status_code, message
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
    if isinstance(taxon_id, bool) or not isinstance(taxon_id, int) or taxon_id < 1:
      raise ValueError("taxon_id must be a positive integer")
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
  ) -> requests.Response:
    for attempt in range(self.max_retries + 1):
      try:
        response = self.session.get(url, params=params, timeout=self.timeout)
      except requests.RequestException as error:
        raise UniProtError("UniProt GET {} failed: {}".format(url, error)) from error
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

  @staticmethod
  def _http_error(response: requests.Response) -> UniProtHTTPError:
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
    return UniProtHTTPError(response.status_code, response.url, message)
