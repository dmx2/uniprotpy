from copy import deepcopy

import pytest

from uniprotpy.client import UniProtClient, UniProtResponseError
from uniprotpy.taxonomy import Taxon


RELEASE_HEADERS = {
  "X-UniProt-Release": "2026_02",
  "X-UniProt-Release-Date": "15-Apr-2026",
  "Content-Type": "application/json",
}


class FakeResponse:
  def __init__(self, url, payload, headers=None, status=200):
    self.url = url
    self._payload = payload
    self.headers = dict(headers or {})
    self.status_code = status
    self.text = ""
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
    self.calls.append((url, params, timeout, allow_redirects))
    return self.responses.pop(0)


def human_taxon_document():
  return {
    "taxonId": 9606,
    "scientificName": "Homo sapiens",
    "commonName": "Human",
    "mnemonic": "HUMAN",
    "rank": "species",
    "active": True,
    "hidden": False,
    "parent": {"taxonId": 9605, "scientificName": "Homo"},
    "lineage": [
      {"taxonId": 1, "scientificName": "root", "rank": "no rank"},
      "future non-object lineage member",
      {"taxonId": 2759, "scientificName": "Eukaryota"},
    ],
    "otherNames": ["Man", 9606, "Human being"],
    "links": ["https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=9606", None],
    "statistics": {"reviewedProteinCount": 20431, "futureMetric": 0.125},
    "futureTaxonomyField": {
      "variant": "preserve-me",
      "evidence": ["ECO:0000269", {"score": 0.99}],
    },
  }


def test_taxon_round_trip_preserves_unknown_data_and_tolerant_accessors():
  document = human_taxon_document()
  expected = deepcopy(document)
  taxon = Taxon.from_json(document)
  document["lineage"][0]["scientificName"] = "changed outside"
  document["futureTaxonomyField"]["variant"] = "changed outside"

  assert taxon.taxon_id == 9606
  assert taxon.id == 9606
  assert taxon.scientific_name == "Homo sapiens"
  assert taxon.name == "Homo sapiens"
  assert taxon.common_name == "Human"
  assert taxon.mnemonic == "HUMAN"
  assert taxon.rank == "species"
  assert taxon.active is True
  assert taxon.hidden is False
  assert taxon.parent == Taxon({"taxonId": 9605, "scientificName": "Homo"})
  assert [(item.taxon_id, item.scientific_name) for item in taxon.lineage] == [
    (1, "root"),
    (2759, "Eukaryota"),
  ]
  assert taxon.other_names == ("Man", "Human being")
  assert taxon.links == (
    "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=9606",
  )
  assert taxon.statistics == {
    "reviewedProteinCount": 20431,
    "futureMetric": 0.125,
  }
  assert taxon.to_dict() == expected

  emitted = taxon.to_dict()
  emitted["futureTaxonomyField"]["evidence"][1]["score"] = 0
  assert taxon.to_dict() == expected



def test_taxon_accessors_tolerate_wrong_shapes_without_corrupting_payload():
  document = {
    "taxonId": True,
    "scientificName": 9606,
    "commonName": ["Human"],
    "mnemonic": False,
    "rank": {"value": "species"},
    "active": "false",
    "hidden": 0,
    "parent": [9605],
    "lineage": {"taxonId": 1},
    "otherNames": "Human",
    "links": {"href": "https://example.test/taxonomy/9606"},
    "statistics": [20431],
    "inactiveReason": {
      "inactiveReasonType": ["MERGED"],
      "mergedTo": True,
    },
  }

  taxon = Taxon.from_json(document)

  assert taxon.taxon_id is None
  assert taxon.scientific_name is None
  assert taxon.common_name is None
  assert taxon.mnemonic is None
  assert taxon.rank is None
  assert taxon.active is None
  assert taxon.hidden is None
  assert taxon.parent is None
  assert taxon.lineage == ()
  assert taxon.other_names == ()
  assert taxon.links == ()
  assert taxon.statistics == {}
  assert taxon.inactive_reason is None
  assert taxon.merged_to is None
  assert taxon.to_dict() == document

@pytest.mark.parametrize(
  ("inactive_reason", "expected_reason", "expected_taxon_id"),
  [
    (
      {"inactiveReasonType": "MERGED", "mergedTo": 10090},
      "MERGED",
      10090,
    ),
    ({"inactiveReasonType": "DELETED"}, "DELETED", None),
    ({"inactiveReasonType": "MERGED", "mergedTo": True}, "MERGED", None),
  ],
  ids=("merged", "deleted", "malformed-merged-id"),
)
def test_inactive_taxon_variants_are_normalized_tolerantly(
  inactive_reason, expected_reason, expected_taxon_id
):
  document = {
    "taxonId": 9606,
    "scientificName": "Homo sapiens",
    "active": False,
    "inactiveReason": inactive_reason,
  }

  taxon = Taxon.from_json(document)

  assert taxon.active is False
  assert taxon.inactive_reason == expected_reason
  assert taxon.merged_to == expected_taxon_id
  assert taxon.to_dict() == document


def test_direct_taxon_retrieval_preserves_document_and_release_metadata():
  url = "https://rest.example/taxonomy/9606"
  document = human_taxon_document()
  session = FakeSession([
    FakeResponse(url, document, RELEASE_HEADERS),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  response = client.get_taxon(9606)

  assert response.taxon.to_dict() == document
  assert response.metadata.release == "2026_02"
  assert response.metadata.release_date == "15-Apr-2026"
  assert response.metadata.content_type == "application/json"
  assert response.metadata.url == url
  assert session.calls == [(url, {"format": "json"}, client.timeout, False)]


def test_merged_taxon_retrieval_preserves_303_body_and_location_metadata():
  url = "https://rest.example/taxonomy/100"
  location = "/taxonomy/99?from=100"
  document = {
    "taxonId": 100,
    "scientificName": "Merged taxon",
    "active": False,
    "inactiveReason": {
      "inactiveReasonType": "MERGED",
      "mergedTo": 99,
    },
  }
  session = FakeSession([
    FakeResponse(
      url,
      document,
      {**RELEASE_HEADERS, "Location": location},
      status=303,
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  response = client.get_taxon(100)

  assert response.taxon.to_dict() == document
  assert response.taxon.inactive_reason == "MERGED"
  assert response.taxon.merged_to == 99
  assert response.metadata.status_code == 303
  assert response.metadata.location == location
  assert response.metadata.url == url
  assert session.calls == [(url, {"format": "json"}, client.timeout, False)]


def test_taxonomy_search_follows_absolute_cursor_link_verbatim():
  first_url = "https://rest.example/taxonomy/search"
  next_url = (
    "https://cursor.example/taxonomy/search?cursor=opaque%2Btoken%2Fpart&size=1"
  )
  human = human_taxon_document()
  mouse = {
    "taxonId": 10090,
    "scientificName": "Mus musculus",
    "commonName": "Mouse",
    "rank": "species",
    "active": True,
  }
  session = FakeSession([
    FakeResponse(
      first_url,
      {"results": [human]},
      {
        **RELEASE_HEADERS,
        "X-Total-Results": "2",
        "Link": '<{}>; rel="next"'.format(next_url),
      },
    ),
    FakeResponse(
      next_url,
      {"results": [mouse]},
      {**RELEASE_HEADERS, "X-Total-Results": "2"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  pages = list(client.search_taxa("rank:species", size=1))

  assert [[taxon.taxon_id for taxon in page.taxa] for page in pages] == [
    [9606],
    [10090],
  ]
  assert pages[0].metadata.total_results == 2
  assert pages[0].next_url == next_url
  assert pages[1].next_url is None
  assert session.calls[0] == (
    first_url,
    {"query": "rank:species", "format": "json", "size": "1"},
    client.timeout,
    True,
  )
  assert session.calls[1] == (next_url, None, client.timeout, True)


@pytest.mark.parametrize(
  ("payload", "message"),
  [
    ([], "taxonomy entry.*not a JSON object"),
    (ValueError("invalid JSON"), "taxonomy entry.*not valid JSON"),
  ],
  ids=("non-object", "invalid-json"),
)
def test_direct_taxon_retrieval_rejects_malformed_payloads(payload, message):
  url = "https://rest.example/taxonomy/9606"
  session = FakeSession([FakeResponse(url, payload, RELEASE_HEADERS)])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(UniProtResponseError, match=message):
    client.get_taxon(9606)


@pytest.mark.parametrize(
  ("payload", "message"),
  [
    ({}, "has no results list"),
    ({"results": "not a list"}, "has no results list"),
    ({"results": [{"taxonId": 9606}, "not an object"]}, "result 1.*not an object"),
  ],
  ids=("missing-results", "non-list-results", "non-object-result"),
)
def test_taxonomy_search_rejects_malformed_result_payloads(payload, message):
  url = "https://rest.example/taxonomy/search"
  session = FakeSession([FakeResponse(url, payload, RELEASE_HEADERS)])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(UniProtResponseError, match=message):
    client.get_taxonomy_search_page("human")


@pytest.mark.parametrize(
  "taxon_id",
  [True, 0, -1, 9606.0, "9606", None],
  ids=("boolean", "zero", "negative", "float", "string", "none"),
)
def test_direct_taxon_retrieval_rejects_invalid_ids_before_request(taxon_id):
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError, match="taxon_id must be a positive integer"):
    client.get_taxon(taxon_id)

  assert session.calls == []
