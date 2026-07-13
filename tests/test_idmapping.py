import pytest

import uniprotpy.client as client_module
from uniprotpy import (
  IDMappingConfiguration,
  IDMappingDetails,
  IDMappingJob,
  UniProtClient,
  UniProtError,
  UniProtHTTPError,
)


BASE_URL = "https://rest.example"
RELEASE_HEADERS = {
  "X-UniProt-Release": "2026_03",
  "X-UniProt-Release-Date": "2026-06-17",
  "Content-Type": "application/json",
}

CONFIGURATION_PAYLOAD = {
  "groups": [
    {
      "groupName": "Sequence databases",
      "items": [
        {
          "name": "RefSeq",
          "displayName": "RefSeq protein",
          "from": True,
          "to": False,
          "ruleId": 1,
          "uriLink": "https://identifiers.example/refseq/%s",
          "futureField": {"kept": True},
        },
        {
          "name": "Gene_Name",
          "displayName": "Gene name",
          "from": True,
          "to": False,
          "ruleId": 2,
        },
        {
          "name": "UniProtKB",
          "displayName": "UniProtKB",
          "from": False,
          "to": True,
        },
        {
          "name": "PDB",
          "displayName": "PDB",
          "from": False,
          "to": True,
        },
      ],
    }
  ],
  "rules": [
    {
      "ruleId": 1,
      "tos": ["UniProtKB"],
      "defaultTo": "UniProtKB",
      "taxonId": True,
      "futureRuleField": ["preserved"],
    },
    {
      "ruleId": 2,
      "tos": ["UniProtKB"],
      "defaultTo": "UniProtKB",
      "taxonId": False,
    },
  ],
  "futureTopLevelField": {"schemaVersion": 2},
}


class FakeResponse:
  def __init__(
    self, url=None, *, payload=None, status=200, headers=None, content=None, text=""
  ):
    self.url = url
    self._payload = payload
    self.status_code = status
    self.headers = dict(headers or {})
    self.content = (
      content if content is not None else (b"{}" if payload is not None else b"")
    )
    self.text = text
    self.closed = False

  def json(self):
    if isinstance(self._payload, Exception):
      raise self._payload
    return self._payload

  def close(self):
    self.closed = True


class FakeSession:
  def __init__(self, responses):
    self.responses = list(responses)
    self.calls = []
    self.headers = {}

  def get(self, url, *, params=None, timeout=None, allow_redirects=True):
    self.calls.append(("GET", url, params, allow_redirects))
    response = self.responses.pop(0)
    if response.url is None:
      response.url = url
    return response

  def post(self, url, *, data=None, timeout=None):
    self.calls.append(("POST", url, data))
    response = self.responses.pop(0)
    if response.url is None:
      response.url = url
    return response


def make_client(responses, **kwargs):
  session = FakeSession(responses)
  return UniProtClient(base_url=BASE_URL, session=session, **kwargs), session


def configuration():
  return IDMappingConfiguration.from_json(CONFIGURATION_PAYLOAD)


def test_configuration_parses_discovered_fields_and_rules_losslessly():
  client, session = make_client([
    FakeResponse(payload=CONFIGURATION_PAYLOAD, headers=RELEASE_HEADERS)
  ])
  capabilities = client.get_id_mapping_configuration()

  refseq = capabilities.field("RefSeq")
  assert refseq is not None
  assert (
    refseq.name,
    refseq.display_name,
    refseq.from_supported,
    refseq.to_supported,
    refseq.rule_id,
    refseq.uri_link,
  ) == (
    "RefSeq",
    "RefSeq protein",
    True,
    False,
    1,
    "https://identifiers.example/refseq/%s",
  )
  assert refseq.raw["futureField"] == {"kept": True}

  rule = capabilities.rule_for("RefSeq")
  assert rule is not None
  assert (
    rule.rule_id,
    rule.tos,
    rule.default_to,
    rule.taxon_id_supported,
  ) == (1, ("UniProtKB",), "UniProtKB", True)
  assert rule.raw["futureRuleField"] == ["preserved"]
  assert capabilities.raw == CONFIGURATION_PAYLOAD
  assert capabilities.metadata.release == "2026_03"
  assert capabilities.metadata.release_date == "2026-06-17"
  assert capabilities.metadata.url == BASE_URL + "/configure/idmapping/fields"
  assert session.calls == [
    ("GET", BASE_URL + "/configure/idmapping/fields", None, True)
  ]


@pytest.mark.parametrize(
  "from_db,to_db,taxon_id,match",
  [
    ("Unknown", "UniProtKB", None, "unsupported ID Mapping source 'Unknown'"),
    ("RefSeq", "Gene_Name", None, "unsupported ID Mapping target 'Gene_Name'"),
    ("RefSeq", "PDB", None, "from 'RefSeq' to 'PDB' is not supported"),
    ("Gene_Name", "UniProtKB", 9606, "does not support taxId"),
  ],
  ids=["source-role", "target-role", "pair-rule", "taxon-capability"],
)
def test_configuration_rejects_pairs_outside_discovered_capabilities(
  from_db, to_db, taxon_id, match
):
  with pytest.raises(ValueError, match=match):
    configuration().validate(from_db, to_db, taxon_id)




@pytest.mark.parametrize("taxon_id", [0, -1, True, "9606"])
def test_submission_rejects_non_positive_integer_taxon_ids_before_transport(taxon_id):
  client, session = make_client([])

  with pytest.raises(ValueError, match="taxon_id must be a positive integer"):
    client.submit_id_mapping(
      "RefSeq",
      "UniProtKB",
      ["NP_000537.3"],
      taxon_id=taxon_id,
      configuration=configuration(),
    )

  assert session.calls == []


def test_submission_sends_one_form_request_and_returns_the_submitted_job():
  submission_payload = {
    "jobId": "job-123",
    "messages": ["job expires in seven days"],
    "futureSubmissionField": {"queue": "async"},
  }
  client, session = make_client([
    FakeResponse(payload=submission_payload, headers=RELEASE_HEADERS),
  ])

  job = client.submit_id_mapping(
    "RefSeq",
    "UniProtKB",
    (value for value in ["NP_000537.3", "NP_001119584.1"]),
    taxon_id=9606,
    configuration=configuration(),
  )

  assert (
    job.job_id, job.from_db, job.to_db, job.ids, job.taxon_id
  ) == (
    "job-123",
    "RefSeq",
    "UniProtKB",
    ("NP_000537.3", "NP_001119584.1"),
    9606,
  )
  assert job.raw == submission_payload
  assert job.metadata.release == "2026_03"
  assert job.metadata.url == BASE_URL + "/idmapping/run"
  assert session.calls == [
    (
      "POST",
      BASE_URL + "/idmapping/run",
      {
        "from": "RefSeq",
        "to": "UniProtKB",
        "ids": "NP_000537.3,NP_001119584.1",
        "taxId": "9606",
      },
    )
  ]


def test_submission_does_not_retry_a_transient_post_failure():
  transient = FakeResponse(
    payload={"messages": ["temporarily unavailable"]},
    status=503,
  )
  delays = []
  client, session = make_client(
    [transient], max_retries=8, sleep=delays.append
  )

  with pytest.raises(UniProtHTTPError) as caught:
    client.submit_id_mapping(
      "RefSeq",
      "UniProtKB",
      ["NP_000537.3"],
      configuration=configuration(),
    )

  assert caught.value.status_code == 503
  assert caught.value.method == "POST"
  assert "temporarily unavailable" in str(caught.value)
  assert len(session.calls) == 1
  assert session.calls[0][0] == "POST"
  assert delays == []


def test_status_treats_303_location_as_finished_without_following_redirect():
  results_url = "https://rest.example/idmapping/results/job-303?format=json"
  status_payload = {
    "jobStatus": "FINISHED",
    "warnings": [{"code": "DELETED", "message": "one source ID is obsolete"}],
  }
  client, session = make_client([
    FakeResponse(
      status=303,
      headers={**RELEASE_HEADERS, "Location": results_url},
      payload=status_payload,
    ),
  ])

  status = client.get_id_mapping_status("job/303")

  assert status.job_id == "job/303"
  assert status.raw == status_payload
  assert status.warnings == (
    {"code": "DELETED", "message": "one source ID is obsolete"},
  )
  assert status.metadata.release == "2026_03"
  assert status.metadata.url == BASE_URL + "/idmapping/status/job%2F303"
  assert status.redirect_url == results_url
  assert status.finished is True
  assert status.failed is False
  assert session.calls == [
    (
      "GET",
      BASE_URL + "/idmapping/status/job%2F303",
      None,
      False,
    )
  ]


def test_details_exposes_the_authoritative_results_redirect_and_raw_payload():
  payload = {
    "from": "RefSeq",
    "to": "UniProtKB",
    "ids": "NP_000537.3,NP_001119584.1",
    "taxId": "9606",
    "redirectURL": "https://results.example/idmapping/results/job?cursor=first%2Bpage",
    "futureDetail": {"expiresInDays": 7},
    "warnings": [{"code": "DELETED", "message": "obsolete source ID"}],
  }
  client, session = make_client([
    FakeResponse(payload=payload, headers=RELEASE_HEADERS)
  ])

  details = client.get_id_mapping_details("job/with space")

  assert details.job_id == "job/with space"
  assert details.from_db == "RefSeq"
  assert details.to_db == "UniProtKB"
  assert details.ids == ("NP_000537.3", "NP_001119584.1")
  assert details.taxon_id == 9606
  assert details.redirect_url == payload["redirectURL"]
  assert details.raw == payload
  assert details.warnings == (
    {"code": "DELETED", "message": "obsolete source ID"},
  )
  assert details.metadata.release == "2026_03"
  assert details.metadata.url == BASE_URL + "/idmapping/details/job%2Fwith%20space"
  assert session.calls[0][1] == BASE_URL + "/idmapping/details/job%2Fwith%20space"


def test_results_follow_verbatim_next_link_and_preserve_every_page_shape():
  first_url = "https://results.example/idmapping/results/job?fields=accession%2Cgene"
  next_url = (
    "https://cursor.example/idmapping/results/job"
    "?cursor=opaque%2Btoken%2Fpart&format=json&size=1"
  )
  first_payload = {
    "results": [{"from": "A0", "to": "P04637"}],
    "failedIds": ["missing-A", 17],
    "warnings": [{"code": "PARTIAL", "context": {"field": "gene"}}],
    "suggestedIds": ["A0.1"],
    "obsoleteCount": 1,
    "futurePageField": {"preserved": [1, 2]},
  }
  enriched_target = {
    "primaryAccession": "Q9TEST",
    "proteinDescription": {"recommendedName": {"fullName": {"value": "β protein"}}},
    "futureTargetField": [{"score": 0.75}],
  }
  second_payload = {
    "results": [{"from": "A1", "to": enriched_target}],
    "failedIds": ["missing-B"],
    "warnings": ["results truncated by an upstream source"],
  }
  first_headers = {
    "Link": '<{}>; rel="next"'.format(next_url),
    "X-UniProt-Release": "2026_03",
    "X-UniProt-Release-Date": "2026-06-17",
    "X-Total-Results": "2",
    "Content-Type": "application/json",
  }
  second_headers = {
    "X-UniProt-Release": "2026_03",
    "X-UniProt-Release-Date": "2026-06-17",
    "X-Total-Results": "2",
    "Content-Type": "application/json",
  }
  client, session = make_client([
    FakeResponse(first_url, payload=first_payload, headers=first_headers),
    FakeResponse(next_url, payload=second_payload, headers=second_headers),
  ])
  details = IDMappingDetails("job", {"redirectURL": first_url})

  pages = list(client.id_mapping_results(details, size=1))

  assert [page.results[0].from_id for page in pages] == ["A0", "A1"]
  assert pages[0].results[0].to == "P04637"
  assert pages[1].results[0].to == enriched_target
  assert pages[0].failed_ids == ("missing-A",)
  assert pages[1].failed_ids == ("missing-B",)
  assert pages[0].warnings == (
    {"code": "PARTIAL", "context": {"field": "gene"}},
  )
  assert pages[1].warnings == ("results truncated by an upstream source",)
  assert pages[0].raw == first_payload
  assert pages[1].raw == second_payload
  assert pages[0].metadata.release == "2026_03"
  assert pages[0].metadata.release_date == "2026-06-17"
  assert pages[0].metadata.total_results == 2
  assert pages[0].metadata.url == first_url
  assert pages[0].metadata.content_type == "application/json"
  assert pages[0].next_url == next_url
  assert pages[1].next_url is None
  assert session.calls == [
    (
      "GET",
      first_url,
      {"format": "json", "size": "1"},
      True,
    ),
    ("GET", next_url, None, True),
  ]


def test_wait_polls_running_to_finished_then_collects_authoritative_results(
  monkeypatch,
):
  details_payload = {
    "from": "RefSeq",
    "to": "UniProtKB",
    "ids": ["NP_000537.3", "missing"],
    "redirectURL": BASE_URL + "/idmapping/results/job-wait",
    "warnings": [{"code": "DETAIL", "message": "details warning"}],
  }
  page_payload = {
    "results": [{"from": "NP_000537.3", "to": "P04637"}],
    "failedIds": ["missing", "missing"],
    "warnings": [{"message": "one identifier was not mapped"}],
  }
  client, session = make_client([
    FakeResponse(payload={"jobStatus": "RUNNING"}, headers=RELEASE_HEADERS),
    FakeResponse(
      payload={
        "jobStatus": "FINISHED",
        "warnings": [{"code": "STATUS", "message": "status warning"}],
      },
      headers=RELEASE_HEADERS,
    ),
    FakeResponse(payload=details_payload, headers=RELEASE_HEADERS),
    FakeResponse(payload=page_payload, headers=RELEASE_HEADERS),
  ], sleep=lambda delay: sleeps.append(delay))
  sleeps = []
  times = iter([100.0, 100.25, 100.25])
  monkeypatch.setattr(client_module.time, "monotonic", lambda: next(times))
  job = IDMappingJob(
    "job-wait", "RefSeq", "UniProtKB", ("NP_000537.3", "missing")
  )

  result = client.wait_for_mapping(
    job, poll_interval=0.5, timeout=10.0, size=2
  )

  assert result.job == job
  assert result.details.to_dict() == details_payload
  assert result.status.raw == {
    "jobStatus": "FINISHED",
    "warnings": [{"code": "STATUS", "message": "status warning"}],
  }
  assert result.status.metadata.release == "2026_03"
  assert [match.to for match in result.results] == ["P04637"]
  assert result.failed_ids == ("missing",)
  assert result.warnings == (
    {"code": "STATUS", "message": "status warning"},
    {"code": "DETAIL", "message": "details warning"},
    {"message": "one identifier was not mapped"},
  )
  assert sleeps == [0.5]
  assert [call[1] for call in session.calls] == [
    BASE_URL + "/idmapping/status/job-wait",
    BASE_URL + "/idmapping/status/job-wait",
    BASE_URL + "/idmapping/details/job-wait",
    BASE_URL + "/idmapping/results/job-wait",
  ]
  assert session.calls[0][3] is False
  assert session.calls[1][3] is False
  assert session.calls[3][2] == {"format": "json", "size": "2"}


def test_wait_surfaces_terminal_error_without_fetching_details(monkeypatch):
  client, session = make_client([
    FakeResponse(payload={"jobStatus": "ERROR", "message": "invalid source ID"}),
  ])
  monkeypatch.setattr(client_module.time, "monotonic", lambda: 100.0)
  job = IDMappingJob("job-failed", "RefSeq", "UniProtKB", ("bad",))

  with pytest.raises(
    UniProtError,
    match="UniProt ID Mapping job job-failed failed: invalid source ID",
  ):
    client.wait_for_mapping(job, poll_interval=0.0, timeout=1.0)

  assert len(session.calls) == 1
  assert session.calls[0][1] == BASE_URL + "/idmapping/status/job-failed"


def test_wait_times_out_while_job_remains_running_without_fetching_details(
  monkeypatch,
):
  client, session = make_client([
    FakeResponse(payload={"jobStatus": "RUNNING"}),
  ], sleep=lambda delay: pytest.fail("timeout boundary must not sleep"))
  times = iter([100.0, 101.0])
  monkeypatch.setattr(client_module.time, "monotonic", lambda: next(times))
  job = IDMappingJob("job-slow", "RefSeq", "UniProtKB", ("NP_000537.3",))

  with pytest.raises(
    TimeoutError,
    match="timed out waiting for UniProt ID Mapping job job-slow",
  ):
    client.wait_for_mapping(job, poll_interval=0.5, timeout=1.0)

  assert len(session.calls) == 1
  assert session.calls[0][1] == BASE_URL + "/idmapping/status/job-slow"
