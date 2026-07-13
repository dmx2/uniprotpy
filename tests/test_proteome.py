import json
from pathlib import Path

from uniprotpy.client import UniProtClient
from uniprotpy.release import UniProtRelease


from uniprotpy.proteomes import (
  Ambiguous,
  NotFound,
  Proteome,
  Unique,
  ProteomeSelector,
)


FIXTURES = Path(__file__).parent / "fixtures"
RELEASE_HEADERS = {
  "X-UniProt-Release": "2026_02",
  "X-UniProt-Release-Date": "15-Apr-2026",
  "Content-Type": "application/json",
}


class FakeResponse:
  def __init__(self, url, payload, headers=None):
    self.url = url
    self._payload = payload
    self.headers = dict(headers or {})
    self.status_code = 200
    self.text = ""
    self.closed = False

  def json(self):
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



def load_document(name):
  with (FIXTURES / name).open(encoding="utf-8") as handle:
    return json.load(handle)


def proteomes():
  return tuple(Proteome.from_json(load_document(name)) for name in (
    "proteome-human-primary.json",
    "proteome-human-alternate.json",
    "proteome-descendant.json",
  ))


def test_proteome_json_round_trip_and_accessors_are_faithful():
  document = load_document("proteome-human-primary.json")
  proteome = Proteome.from_json(document)
  document["id"] = "changed-after-construction"

  assert proteome.id == "UP000005640"
  assert proteome.upid == "UP000005640"
  assert proteome.taxon_id == 9606
  assert proteome.organism_name == "Homo sapiens"
  assert proteome.proteome_type == "Reference proteome"
  assert proteome.protein_count == 147506
  assert proteome.busco_score == 99.0
  assert proteome.components[0]["proteomeCrossReferences"] == [
    {"database": "GenomeAccession", "id": "CM000663"}
  ]
  assert proteome.to_json()["futureProteomeField"] == {
    "variant": "preserve-me", "evidence": ["ECO:0000269"]
  }
  assert proteome.to_json() == load_document("proteome-human-primary.json")


def test_selector_is_stateless_across_unique_ambiguous_missing_and_busco_calls():
  primary, alternate, descendant = proteomes()
  selector = ProteomeSelector()

  unique = selector.select([primary], taxon_id=9606)
  ambiguous = selector.select([primary, alternate], taxon_id=9606)
  missing = selector.select([], taxon_id=63221)
  tied = selector.select_highest_busco(
    [primary, alternate], taxon_id=9606
  )
  highest = selector.select_highest_busco(
    [primary, descendant], taxon_id=9606
  )

  assert isinstance(unique, Unique)
  assert unique.proteome is primary
  assert isinstance(ambiguous, Ambiguous)
  assert ambiguous.candidates == (primary, alternate)
  assert missing == NotFound(taxon_id=63221)
  assert isinstance(tied, Ambiguous)
  assert tied.candidates == (primary, alternate)
  assert isinstance(highest, Unique)
  assert highest.proteome is descendant


def test_proteome_cursor_pagination_follows_opaque_link_verbatim():
  first_url = "https://rest.example/proteomes/search"
  next_url = (
    "https://cursor.example/proteomes/search?cursor=opaque%2Btoken&size=2"
  )
  primary, alternate, descendant = (
    load_document("proteome-human-primary.json"),
    load_document("proteome-human-alternate.json"),
    load_document("proteome-descendant.json"),
  )
  session = FakeSession([
    FakeResponse(
      first_url,
      {"results": [primary, alternate]},
      {**RELEASE_HEADERS, "X-Total-Results": "3", "Link": (
        '<{}>; rel="next"'.format(next_url)
      )},
    ),
    FakeResponse(
      next_url,
      {"results": [descendant]},
      {**RELEASE_HEADERS, "X-Total-Results": "3"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  pages = list(client.search_proteomes(
    "(taxonomy_id:9606) AND (proteome_type:REFERENCE)", size=2
  ))

  assert [[item.upid for item in page.proteomes] for page in pages] == [
    ["UP000005640", "UP000999901"], ["UP000999902"]
  ]
  assert pages[0].metadata.total_results == 3
  assert pages[0].next_url == next_url
  assert session.calls[0][1] == {
    "query": "(taxonomy_id:9606) AND (proteome_type:REFERENCE)",
    "format": "json",
    "size": "2",
  }
  assert session.calls[1][0] == next_url
  assert session.calls[1][1] is None


def test_reference_proteomes_canonicalize_active_taxon_before_exact_selection():
  documents = [
    load_document("proteome-human-primary.json"),
    load_document("proteome-descendant.json"),
  ]
  taxonomy_url = "https://rest.example/taxonomy/9606"
  search_url = "https://rest.example/proteomes/search"
  session = FakeSession([
    FakeResponse(
      taxonomy_url,
      {"taxonId": 9606, "scientificName": "Homo sapiens", "active": True},
      RELEASE_HEADERS,
    ),
    FakeResponse(
      search_url,
      {"results": documents},
      {**RELEASE_HEADERS, "X-Total-Results": "2"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  exact = client.reference_proteomes(9606, scope="exact")

  assert [item.upid for item in exact] == ["UP000005640"]
  selection = ProteomeSelector().select(exact, taxon_id=9606)
  assert isinstance(selection, Unique)
  assert selection.proteome.upid == "UP000005640"
  assert session.calls == [
    (taxonomy_url, {"format": "json"}, client.timeout, False),
    (search_url, {
      "query": "(taxonomy_id:9606) AND (proteome_type:REFERENCE)",
      "format": "json",
      "size": "500",
    }, client.timeout, True),
  ]


def test_reference_proteomes_resolve_merged_taxon_before_canonical_query():
  obsolete_taxon_id = 12345
  taxonomy_url = "https://rest.example/taxonomy/{}".format(obsolete_taxon_id)
  canonical_taxonomy_url = "https://rest.example/taxonomy/9606"
  search_url = "https://rest.example/proteomes/search"
  session = FakeSession([
    FakeResponse(
      taxonomy_url,
      {
        "taxonId": obsolete_taxon_id,
        "scientificName": "Obsolete human taxon",
        "active": False,
        "inactiveReason": {
          "inactiveReasonType": "MERGED",
          "mergedTo": 9606,
        },
      },
      RELEASE_HEADERS,
    ),
    FakeResponse(
      canonical_taxonomy_url,
      {"taxonId": 9606, "scientificName": "Homo sapiens", "active": True},
      RELEASE_HEADERS,
    ),
    FakeResponse(
      search_url,
      {"results": [
        load_document("proteome-human-primary.json"),
        load_document("proteome-descendant.json"),
      ]},
      {**RELEASE_HEADERS, "X-Total-Results": "2"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  exact = client.reference_proteomes(obsolete_taxon_id, scope="exact")

  assert [item.upid for item in exact] == ["UP000005640"]
  assert session.calls == [
    (taxonomy_url, {"format": "json"}, client.timeout, False),
    (canonical_taxonomy_url, {"format": "json"}, client.timeout, False),
    (search_url, {
      "query": "(taxonomy_id:9606) AND (proteome_type:REFERENCE)",
      "format": "json",
      "size": "500",
    }, client.timeout, True),
  ]


def test_release_installs_two_page_proteome_with_membership_and_provenance(
  tmp_path,
):
  upid = "UP000005640"
  proteome_url = "https://rest.example/proteomes/{}".format(upid)
  search_url = "https://rest.example/uniprotkb/search"
  next_url = (
    "https://cursor.example/uniprotkb/search?cursor=entry%2Bpage&size=500"
  )
  session = FakeSession([
    FakeResponse(
      proteome_url,
      load_document("proteome-human-primary.json"),
      RELEASE_HEADERS,
    ),
    FakeResponse(
      search_url,
      {"results": [load_document("p04637.json")]},
      {**RELEASE_HEADERS, "X-Total-Results": "2", "Link": (
        '<{}>; rel="next"'.format(next_url)
      )},
    ),
    FakeResponse(
      next_url,
      {"results": [load_document("p04637-2.json")]},
      {**RELEASE_HEADERS, "X-Total-Results": "2"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)
  release = UniProtRelease("2026_02", cache_dir=tmp_path, client=client)

  assert release.install_proteome(upid) == 2
  assert release.store.proteome_accessions(upid) == ["P04637", "P04637-2"]
  assert [entry.accession for entry in release.store.entries_for_proteome(upid)] == [
    "P04637", "P04637-2"
  ]
  metadata = release.store.release_metadata
  assert metadata["requested_release"] == "2026_02"
  assert metadata["observed_release"] == "2026_02"
  assert metadata["source_query"] == "proteome:UP000005640"
  assert metadata["source_url"] == next_url
  assert metadata["complete"] is True
  assert metadata["next_page_url"] is None
  assert metadata["provenance"] == {
    "install_method": "proteome_search",
    "proteome_id": upid,
    "proteome_taxon_id": 9606,
    "proteome": load_document("proteome-human-primary.json"),
    "entry_count": 2,
  }
  assert len(session.calls) == 3
  assert session.calls[0][0] == proteome_url
  assert session.calls[0][1] == {"format": "json"}
  assert session.calls[1][0] == search_url
  assert session.calls[1][1] == {
    "query": "proteome:UP000005640",
    "format": "json",
    "size": "500",
  }
  assert session.calls[2][0] == next_url
  assert session.calls[2][1] is None
  assert not any(
    "/uniprotkb/P04637" in url
    for url, _params, _timeout, _allow_redirects in session.calls
  )
  release.close()

  reopened = UniProtRelease("2026_02", cache_dir=tmp_path)
  assert reopened.store.proteome_accessions(upid) == ["P04637", "P04637-2"]
  assert reopened.entry("P04637").to_dict() == load_document("p04637.json")
  assert reopened.entry("P04637-2").to_dict() == load_document("p04637-2.json")
  assert reopened.store.release_metadata["provenance"]["entry_count"] == 2
  reopened.close()
