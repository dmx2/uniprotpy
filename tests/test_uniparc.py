from copy import deepcopy

import pytest

from uniprotpy.client import UniProtClient, UniProtResponseError
from uniprotpy.uniparc import UniParcEntry


UPI = "UPI0000000001"
RELEASE_HEADERS = {
  "X-UniProt-Release": "2026_02",
  "X-UniProt-Release-Date": "15-Apr-2026",
  "Content-Type": "application/json",
}


class FakeResponse:
  def __init__(self, url, payload=None, *, text="", headers=None):
    self.url = url
    self._payload = payload
    self.text = text
    self.headers = dict(headers or {})
    self.status_code = 200
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


def full_document():
  return {
    "uniParcId": UPI,
    "uniParcCrossReferences": [
      {
        "database": "UniProtKB/Swiss-Prot",
        "id": "P07612",
        "versionI": 3,
        "version": 3,
        "active": True,
        "created": "1988-04-01",
        "lastUpdated": "2026-01-28",
        "geneName": "FABP1",
        "proteinName": "Fatty acid-binding protein, liver",
        "organism": {
          "scientificName": "Homo sapiens",
          "commonName": "Human",
          "taxonId": 9606,
        },
        "proteomes": [
          {"id": "UP000005640", "component": "Chromosome 2"},
        ],
      },
      {
        "database": "UniProtKB/TrEMBL",
        "id": "A0A024R161",
        "versionI": 1,
        "version": 1,
        "active": False,
        "created": "2014-06-11",
        "lastUpdated": "2017-02-22",
        "organism": {
          "scientificName": "Homo sapiens",
          "commonName": "Human",
          "taxonId": 9606,
        },
        "proteomes": [
          {"id": "UP000005640", "component": "Chromosome 2"},
        ],
      },
      {
        "database": "RefSeq",
        "id": "NP_001002858.1",
        "versionI": 1,
        "active": True,
        "created": "2004-10-25",
        "lastUpdated": "2025-08-03",
        "organism": {
          "scientificName": "Mus musculus",
          "commonName": "Mouse",
          "taxonId": 10090,
        },
        "proteomes": [
          {"id": "UP000000589", "component": "Chromosome 6"},
        ],
        "futureCrossReferenceField": {
          "source": "preserve-me",
          "scores": [0, 0.5, None],
        },
      },
      {
        "database": "PDB",
        "id": "1ABC",
        "versionI": 2,
        "active": True,
        "created": "2001-05-10",
        "lastUpdated": "2002-03-14",
      },
    ],
    "sequence": {
      "value": "MNFSGKYQV",
      "length": 9,
      "molWeight": 1088,
      "crc64": "4A30B4543609FDED",
      "md5": "77E68F9B0F907D673D061F45AE7DCA94",
    },
    "sequenceFeatures": [
      {
        "interproGroup": {"id": "IPR000463", "name": "Fatty acid binding"},
        "database": "Pfam",
        "databaseId": "PF00061",
        "locations": [{"start": 2, "end": 8, "alignment": "NFSGKYQ"}],
      },
    ],
    "oldestCrossRefCreated": "1988-04-01",
    "mostRecentCrossRefUpdated": "2026-01-28",
    "futureTopLevelField": {
      "variant": "preserve-me",
      "evidence": [{"code": "ECO:0000269"}],
    },
  }


def light_document(upi=UPI):
  return {
    "uniParcId": upi,
    "crossReferenceCount": 3,
    "commonTaxons": [
      {
        "topLevel": "Eukaryota",
        "commonTaxon": "Mammalia",
        "commonTaxonId": 40674,
      },
      {
        "topLevel": "Eukaryota",
        "commonTaxon": "Euarchontoglires",
        "commonTaxonId": 314146,
      },
    ],
    "organisms": [
      {
        "scientificName": "Homo sapiens",
        "commonName": "Human",
        "taxonId": 9606,
      },
      {
        "scientificName": "Mus musculus",
        "commonName": "Mouse",
        "taxonId": 10090,
      },
    ],
    "uniProtKBAccessions": ["P07612", "A0A024R161"],
    "sequence": {
      "value": "MNFSGKYQV",
      "length": 9,
      "molWeight": 1088,
      "crc64": "4A30B4543609FDED",
      "md5": "77E68F9B0F907D673D061F45AE7DCA94",
    },
    "sequenceFeatures": [
      {
        "database": "Pfam",
        "databaseId": "PF00061",
        "locations": [{"start": 2, "end": 8}],
      },
    ],
    "oldestCrossRefCreated": "1988-04-01",
    "mostRecentCrossRefUpdated": "2026-01-28",
    "extraAttributes": {
      "futureProjection": ["preserve-me", {"nullable": None}],
    },
  }


def test_full_entry_round_trip_retains_xref_provenance_and_defensive_copies():
  document = full_document()
  expected = deepcopy(document)
  entry = UniParcEntry.from_json(document)

  document["sequence"]["value"] = "CHANGED"
  document["uniParcCrossReferences"][0]["organism"]["taxonId"] = -1
  document["futureTopLevelField"]["evidence"][0]["code"] = "CHANGED"

  assert entry.upi == UPI
  assert entry.sequence == "MNFSGKYQV"
  assert entry.length == 9
  assert entry.molecular_weight == 1088
  assert entry.crc64 == "4A30B4543609FDED"
  assert entry.md5 == "77E68F9B0F907D673D061F45AE7DCA94"
  assert entry.oldest_cross_reference_created == "1988-04-01"
  assert entry.most_recent_cross_reference_updated == "2026-01-28"
  assert entry.sequence_features == (expected["sequenceFeatures"][0],)

  references = entry.cross_references
  assert [reference.database for reference in references] == [
    "UniProtKB/Swiss-Prot",
    "UniProtKB/TrEMBL",
    "RefSeq",
    "PDB",
  ]
  assert [reference.identifier for reference in references] == [
    "P07612",
    "A0A024R161",
    "NP_001002858.1",
    "1ABC",
  ]
  assert [reference.version_i for reference in references] == [3, 1, 1, 2]
  assert [reference.version for reference in references] == [3, 1, None, None]
  assert [reference.active for reference in references] == [True, False, True, True]
  assert [reference.taxon_id for reference in references] == [
    9606, 9606, 10090, None
  ]
  assert references[0].organism == expected["uniParcCrossReferences"][0]["organism"]
  assert references[0].proteomes == (
    {"id": "UP000005640", "component": "Chromosome 2"},
  )
  assert references[1].organism == expected["uniParcCrossReferences"][1]["organism"]
  assert references[1].proteomes == (
    {"id": "UP000005640", "component": "Chromosome 2"},
  )
  assert references[2].organism == expected["uniParcCrossReferences"][2]["organism"]
  assert references[2].proteomes == (
    {"id": "UP000000589", "component": "Chromosome 6"},
  )
  assert entry.organism_cross_references == references[:3]
  assert entry.organisms == ()
  assert entry.taxon_ids == ()
  assert entry.active_cross_references == (references[0], references[2], references[3])
  assert entry.uniprotkb_accessions == ("P07612", "A0A024R161")
  assert entry.to_json() == expected

  emitted = entry.to_json()
  emitted["uniParcCrossReferences"][2]["proteomes"][0]["id"] = "CHANGED"
  emitted["futureTopLevelField"]["evidence"][0]["code"] = "CHANGED"
  assert entry.to_json() == expected


def test_light_entry_round_trip_preserves_projection_and_defensive_copies():
  document = light_document()
  expected = deepcopy(document)
  entry = UniParcEntry.from_json(document)

  document["commonTaxons"][0]["commonTaxonId"] = -1
  document["organisms"][0]["scientificName"] = "changed"
  document["extraAttributes"]["futureProjection"][1]["nullable"] = "changed"

  assert entry.upi == UPI
  assert entry.cross_reference_count == 3
  assert entry.uniprotkb_accessions == ("P07612", "A0A024R161")
  assert entry.common_taxons == tuple(expected["commonTaxons"])
  assert entry.organisms == tuple(expected["organisms"])
  assert entry.taxon_ids == (9606, 10090)
  assert entry.sequence_features == (expected["sequenceFeatures"][0],)
  assert entry.cross_references == ()
  assert entry.organism_cross_references == ()
  assert entry.to_dict() == expected

  emitted = entry.to_dict()
  emitted["commonTaxons"][1]["commonTaxon"] = "changed"
  assert entry.to_dict() == expected


def test_direct_full_light_and_fasta_requests_preserve_parameters_and_metadata():
  full_url = "https://rest.example/uniparc/{}".format(UPI)
  light_url = full_url + "/light"
  fasta = ">upi|{} status=active\nMNFSGKYQV\n".format(UPI)
  session = FakeSession([
    FakeResponse(full_url, full_document(), headers=RELEASE_HEADERS),
    FakeResponse(light_url, light_document(), headers=RELEASE_HEADERS),
    FakeResponse(
      full_url,
      text=fasta,
      headers={**RELEASE_HEADERS, "Content-Type": "text/plain; format=fasta"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  full = client.get_uniparc_entry(
    UPI,
    fields="upi,sequence",
    db_types=("UniProtKB/Swiss-Prot", "RefSeq"),
    active=False,
    taxon_ids=(9606, 10090),
  )
  light = client.get_uniparc_entry_light(UPI, fields="upi,accession")
  text = client.get_uniparc_entry_text(UPI, format="FASTA")

  assert full.entry.to_dict() == full_document()
  assert full.metadata.release == "2026_02"
  assert full.metadata.release_date == "15-Apr-2026"
  assert full.metadata.content_type == "application/json"
  assert full.metadata.url == full_url
  assert light.entry.to_dict() == light_document()
  assert light.metadata.url == light_url
  assert text.text == fasta
  assert text.metadata.content_type == "text/plain; format=fasta"
  assert text.metadata.url == full_url
  assert session.calls == [
    (
      full_url,
      {
        "format": "json",
        "fields": "upi,sequence",
        "dbTypes": "UniProtKB/Swiss-Prot,RefSeq",
        "active": "false",
        "taxonIds": "9606,10090",
      },
      client.timeout,
      True,
    ),
    (
      light_url,
      {"format": "json", "fields": "upi,accession"},
      client.timeout,
      True,
    ),
    (full_url, {"format": "fasta"}, client.timeout, True),
  ]


def test_search_sends_projection_sort_and_size_then_follows_cursor_verbatim():
  first_url = "https://rest.example/uniparc/search"
  next_url = (
    "https://cursor.example/uniparc/search?cursor=opaque%2Btoken%2Fpart&size=1"
  )
  first_document = light_document()
  second_document = light_document("UPI0000000002")
  session = FakeSession([
    FakeResponse(
      first_url,
      {"results": [first_document]},
      headers={
        **RELEASE_HEADERS,
        "X-Total-Results": "2",
        "Link": '<{}>; rel="next"'.format(next_url),
      },
    ),
    FakeResponse(
      next_url,
      {"results": [second_document]},
      headers={**RELEASE_HEADERS, "X-Total-Results": "2"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  pages = list(client.search_uniparc_entries(
    "database:P07612",
    size=1,
    fields="upi,accession,common_taxons",
    sort="oldest_cross_ref_created asc",
  ))

  assert [[entry.upi for entry in page.entries] for page in pages] == [
    [UPI],
    ["UPI0000000002"],
  ]
  assert pages[0].entries[0].to_dict() == first_document
  assert pages[1].entries[0].to_dict() == second_document
  assert pages[0].metadata.total_results == 2
  assert pages[0].next_url == next_url
  assert pages[1].next_url is None
  assert session.calls == [
    (
      first_url,
      {
        "query": "database:P07612",
        "format": "json",
        "size": "1",
        "fields": "upi,accession,common_taxons",
        "sort": "oldest_cross_ref_created asc",
      },
      client.timeout,
      True,
    ),
    (next_url, None, client.timeout, True),
  ]


def test_xref_filters_and_identifier_are_preserved_before_opaque_pagination():
  first_url = "https://rest.example/uniparc/{}/databases".format(UPI)
  next_url = (
    "https://cursor.example/uniparc/{}/databases?cursor=xref%2Btoken&size=2".format(
      UPI
    )
  )
  references = full_document()["uniParcCrossReferences"]
  expected_references = deepcopy(references)
  session = FakeSession([
    FakeResponse(
      first_url,
      {"results": references[:2]},
      headers={
        **RELEASE_HEADERS,
        "X-Total-Results": "4",
        "Link": '<{}>; rel="next"'.format(next_url),
      },
    ),
    FakeResponse(
      next_url,
      {"results": references[2:]},
      headers={**RELEASE_HEADERS, "X-Total-Results": "4"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  pages = list(client.uniparc_cross_references(
    UPI,
    identifier="P07612",
    size=2,
    fields="database,id,organism,proteome",
    db_types=("UniProtKB/Swiss-Prot", "UniProtKB/TrEMBL"),
    active=False,
    taxon_ids=(9606, 10090),
  ))

  references[0]["organism"]["taxonId"] = -1
  references[2]["futureCrossReferenceField"]["scores"][1] = -1
  flattened = [reference for page in pages for reference in page.cross_references]
  assert [reference.to_dict() for reference in flattened] == expected_references
  emitted = flattened[2].to_dict()
  emitted["futureCrossReferenceField"]["scores"][0] = -1
  assert flattened[2].to_dict() == expected_references[2]
  assert [reference.active for reference in flattened] == [True, False, True, True]
  assert flattened[0].organism["taxonId"] == 9606
  assert flattened[0].proteomes == (
    {"id": "UP000005640", "component": "Chromosome 2"},
  )
  assert flattened[2].organism["taxonId"] == 10090
  assert flattened[2].proteomes == (
    {"id": "UP000000589", "component": "Chromosome 6"},
  )
  assert pages[0].metadata.total_results == 4
  assert pages[0].next_url == next_url
  assert pages[1].next_url is None
  assert session.calls == [
    (
      first_url,
      {
        "format": "json",
        "size": "2",
        "id": "P07612",
        "fields": "database,id,organism,proteome",
        "dbTypes": "UniProtKB/Swiss-Prot,UniProtKB/TrEMBL",
        "active": "false",
        "taxonIds": "9606,10090",
      },
      client.timeout,
      True,
    ),
    (next_url, None, client.timeout, True),
  ]


@pytest.mark.parametrize(
  ("method_name", "payload", "message"),
  [
    ("get_uniparc_entry", [], "UniParc entry.*not a JSON object"),
    (
      "get_uniparc_entry",
      ValueError("invalid JSON"),
      "UniParc entry.*not valid JSON",
    ),
    ("get_uniparc_entry_light", [], "light UniParc entry.*not a JSON object"),
    (
      "get_uniparc_entry_light",
      ValueError("invalid JSON"),
      "light UniParc entry.*not valid JSON",
    ),
  ],
  ids=("full-object", "full-json", "light-object", "light-json"),
)
def test_direct_entry_requests_reject_malformed_documents(
  method_name, payload, message
):
  session = FakeSession([FakeResponse("https://rest.example", payload, headers=RELEASE_HEADERS)])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(UniProtResponseError, match=message):
    getattr(client, method_name)(UPI)


@pytest.mark.parametrize(
  ("page_kind", "payload", "message"),
  [
    ("search", {}, "search response.*has no results list"),
    ("search", {"results": "not a list"}, "search response.*has no results list"),
    (
      "search",
      {"results": [light_document(), "not an object"]},
      "result 1.*not an object",
    ),
    ("xref", {}, "cross-reference response.*has no results list"),
    (
      "xref",
      {"results": "not a list"},
      "cross-reference response.*has no results list",
    ),
    (
      "xref",
      {"results": [full_document()["uniParcCrossReferences"][0], None]},
      "cross-reference 1.*not an object",
    ),
  ],
  ids=(
    "search-missing-results",
    "search-non-list-results",
    "search-non-object-member",
    "xref-missing-results",
    "xref-non-list-results",
    "xref-non-object-member",
  ),
)
def test_page_requests_reject_malformed_result_collections(
  page_kind, payload, message
):
  session = FakeSession([FakeResponse("https://rest.example", payload, headers=RELEASE_HEADERS)])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(UniProtResponseError, match=message):
    if page_kind == "search":
      client.get_uniparc_search_page("upi:{}".format(UPI))
    else:
      client.get_uniparc_cross_reference_page(UPI)


@pytest.mark.parametrize(
  "upi",
  [None, "", " \t", 7],
  ids=("none", "empty", "whitespace", "non-string"),
)
def test_direct_entry_rejects_nonempty_string_violations_before_request(upi):
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError, match="upi must not be empty"):
    client.get_uniparc_entry(upi)

  assert session.calls == []


@pytest.mark.parametrize(
  "query",
  [None, "", " \t", 7],
  ids=("none", "empty", "whitespace", "non-string"),
)
def test_search_rejects_invalid_queries_before_request(query):
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError, match="query must be a nonempty string"):
    client.get_uniparc_search_page(query)

  assert session.calls == []


@pytest.mark.parametrize(
  ("page_kind", "size"),
  [
    ("search", 0),
    ("search", 501),
    ("search", True),
    ("search", 1.5),
    ("search", "1"),
    ("search", None),
    ("xref", 0),
    ("xref", 501),
    ("xref", True),
    ("xref", 1.5),
    ("xref", "1"),
    ("xref", None),
  ],
  ids=(
    "search-zero",
    "search-too-large",
    "search-boolean",
    "search-float",
    "search-string",
    "search-none",
    "xref-zero",
    "xref-too-large",
    "xref-boolean",
    "xref-float",
    "xref-string",
    "xref-none",
  ),
)
def test_page_requests_reject_invalid_sizes_before_request(page_kind, size):
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError, match="size must be between 1 and 500"):
    if page_kind == "search":
      client.get_uniparc_search_page("upi:{}".format(UPI), size=size)
    else:
      client.get_uniparc_cross_reference_page(UPI, size=size)

  assert session.calls == []


@pytest.mark.parametrize(
  "format",
  [None, "", "fa-sta", "fasta/../../entry", 7],
  ids=("none", "empty", "punctuation", "path", "non-string"),
)
def test_text_entry_rejects_invalid_formats_before_request(format):
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError, match="format must contain only letters and digits"):
    client.get_uniparc_entry_text(UPI, format=format)

  assert session.calls == []
