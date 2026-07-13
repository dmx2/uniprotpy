"""Lossless domain values for the asynchronous UniProt ID Mapping service."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class IDMappingField:
  name: str
  display_name: Optional[str]
  from_supported: bool
  to_supported: bool
  rule_id: Optional[int]
  uri_link: Optional[str]
  raw: Mapping[str, Any]


@dataclass(frozen=True)
class IDMappingRule:
  rule_id: int
  tos: Tuple[str, ...]
  default_to: Optional[str]
  taxon_id_supported: bool
  raw: Mapping[str, Any]


class IDMappingConfiguration:
  """Server-discovered ID Mapping databases, valid pairs, and taxon support."""

  def __init__(self, data: Mapping[str, Any], metadata: Any = None):
    if not isinstance(data, Mapping):
      raise TypeError("ID Mapping configuration must be a mapping")
    self._data: Dict[str, Any] = deepcopy(dict(data))
    self.metadata = metadata

  @classmethod
  def from_json(
    cls, data: Mapping[str, Any], metadata: Any = None
  ) -> "IDMappingConfiguration":
    return cls(data, metadata=metadata)

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)

  @property
  def fields(self) -> Tuple[IDMappingField, ...]:
    fields = []
    groups = self._data.get("groups", ())
    if not isinstance(groups, (list, tuple)):
      return ()
    for group in groups:
      if not isinstance(group, Mapping):
        continue
      items = group.get("items", ())
      if not isinstance(items, (list, tuple)):
        continue
      for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
          continue
        rule_id = item.get("ruleId")
        fields.append(IDMappingField(
          name=item["name"],
          display_name=item.get("displayName") if isinstance(item.get("displayName"), str) else None,
          from_supported=item.get("from") is True,
          to_supported=item.get("to") is True,
          rule_id=rule_id if isinstance(rule_id, int) and not isinstance(rule_id, bool) else None,
          uri_link=item.get("uriLink") if isinstance(item.get("uriLink"), str) else None,
          raw=deepcopy(dict(item)),
        ))
    return tuple(fields)

  @property
  def rules(self) -> Tuple[IDMappingRule, ...]:
    rules = []
    values = self._data.get("rules", ())
    if not isinstance(values, (list, tuple)):
      return ()
    for value in values:
      if not isinstance(value, Mapping):
        continue
      rule_id = value.get("ruleId")
      if not isinstance(rule_id, int) or isinstance(rule_id, bool):
        continue
      tos = value.get("tos", ())
      rules.append(IDMappingRule(
        rule_id=rule_id,
        tos=tuple(item for item in tos if isinstance(item, str)) if isinstance(tos, (list, tuple)) else (),
        default_to=value.get("defaultTo") if isinstance(value.get("defaultTo"), str) else None,
        taxon_id_supported=value.get("taxonId") is True,
        raw=deepcopy(dict(value)),
      ))
    return tuple(rules)

  def field(self, name: str) -> Optional[IDMappingField]:
    return next((field for field in self.fields if field.name == name), None)

  def rule_for(self, from_db: str) -> Optional[IDMappingRule]:
    field = self.field(from_db)
    if field is None or not field.from_supported or field.rule_id is None:
      return None
    return next((rule for rule in self.rules if rule.rule_id == field.rule_id), None)

  def validate(self, from_db: str, to_db: str, taxon_id: Optional[int] = None) -> None:
    source = self.field(from_db)
    if source is None or not source.from_supported:
      raise ValueError("unsupported ID Mapping source {!r}".format(from_db))
    target = self.field(to_db)
    if target is None or not target.to_supported:
      raise ValueError("unsupported ID Mapping target {!r}".format(to_db))
    rule = self.rule_for(from_db)
    if rule is None or to_db not in rule.tos:
      raise ValueError("ID Mapping from {!r} to {!r} is not supported".format(from_db, to_db))
    if taxon_id is not None and not rule.taxon_id_supported:
      raise ValueError("ID Mapping source {!r} does not support taxId".format(from_db))


@dataclass(frozen=True)
class IDMappingJob:
  job_id: str
  from_db: str
  to_db: str
  ids: Tuple[str, ...]
  taxon_id: Optional[int] = None
  raw: Optional[Mapping[str, Any]] = None
  metadata: Any = None


class IDMappingStatus:
  def __init__(
    self,
    job_id: str,
    data: Mapping[str, Any],
    redirect_url: Optional[str] = None,
    metadata: Any = None,
  ):
    self.job_id = job_id
    self._data = deepcopy(dict(data))
    self.redirect_url = redirect_url
    self.metadata = metadata

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  @property
  def status(self) -> Optional[str]:
    value = self._data.get("jobStatus")
    return value if isinstance(value, str) else None

  @property
  def finished(self) -> bool:
    return self.redirect_url is not None or self.status == "FINISHED"

  @property
  def failed(self) -> bool:
    return self.status == "ERROR"

  @property
  def message(self) -> Optional[str]:
    for key in ("message", "error"):
      value = self._data.get(key)
      if isinstance(value, str):
        return value
    return None

  @property
  def warnings(self) -> Tuple[Any, ...]:
    value = self._data.get("warnings", ())
    return tuple(deepcopy(item) for item in value) if isinstance(value, (list, tuple)) else ()


class IDMappingDetails:
  def __init__(self, job_id: str, data: Mapping[str, Any], metadata: Any = None):
    self.job_id = job_id
    self._data = deepcopy(dict(data))
    self.metadata = metadata

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)

  @property
  def from_db(self) -> Optional[str]:
    value = self._data.get("from")
    return value if isinstance(value, str) else None

  @property
  def to_db(self) -> Optional[str]:
    value = self._data.get("to")
    return value if isinstance(value, str) else None

  @property
  def ids(self) -> Tuple[str, ...]:
    value = self._data.get("ids")
    if isinstance(value, str):
      return tuple(item for item in value.split(",") if item)
    if isinstance(value, (list, tuple)):
      return tuple(item for item in value if isinstance(item, str))
    return ()

  @property
  def taxon_id(self) -> Optional[int]:
    value = self._data.get("taxId")
    if isinstance(value, int) and not isinstance(value, bool):
      return value
    if isinstance(value, str) and value.isdigit():
      return int(value)
    return None

  @property
  def redirect_url(self) -> Optional[str]:
    value = self._data.get("redirectURL")
    return value if isinstance(value, str) and value else None

  @property
  def warnings(self) -> Tuple[Any, ...]:
    value = self._data.get("warnings", ())
    return tuple(deepcopy(item) for item in value) if isinstance(value, (list, tuple)) else ()


class IDMappingMatch:
  """One mapping pair; ``to`` may be a scalar ID or enriched target object."""

  def __init__(self, data: Mapping[str, Any]):
    if not isinstance(data, Mapping):
      raise TypeError("ID Mapping result must be a mapping")
    self._data = deepcopy(dict(data))

  @property
  def raw(self) -> Mapping[str, Any]:
    return self._data

  def to_dict(self) -> Dict[str, Any]:
    return deepcopy(self._data)

  @property
  def from_id(self) -> Optional[str]:
    value = self._data.get("from")
    return value if isinstance(value, str) else None

  @property
  def to(self) -> Any:
    return deepcopy(self._data.get("to"))


@dataclass(frozen=True)
class IDMappingPage:
  results: Tuple[IDMappingMatch, ...]
  failed_ids: Tuple[str, ...]
  warnings: Tuple[Any, ...]
  metadata: Any
  next_url: Optional[str]
  raw: Mapping[str, Any]


@dataclass(frozen=True)
class IDMappingResult:
  job: IDMappingJob
  status: IDMappingStatus
  details: IDMappingDetails
  pages: Tuple[IDMappingPage, ...]

  @property
  def results(self) -> Tuple[IDMappingMatch, ...]:
    return tuple(result for page in self.pages for result in page.results)

  @property
  def failed_ids(self) -> Tuple[str, ...]:
    seen = set()
    values = []
    for page in self.pages:
      for value in page.failed_ids:
        if value not in seen:
          seen.add(value)
          values.append(value)
    return tuple(values)

  @property
  def warnings(self) -> Tuple[Any, ...]:
    values = list(self.status.warnings)
    values.extend(self.details.warnings)
    values.extend(value for page in self.pages for value in page.warnings)
    return tuple(deepcopy(value) for value in values)


__all__ = [
  "IDMappingConfiguration",
  "IDMappingDetails",
  "IDMappingField",
  "IDMappingJob",
  "IDMappingMatch",
  "IDMappingPage",
  "IDMappingResult",
  "IDMappingRule",
  "IDMappingStatus",
]
