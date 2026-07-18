import json
from pathlib import Path

import pytest

from uniprotpy.client import UniProtClient


FIXTURES = Path(__file__).parent / "fixtures"
RELEASE_HEADERS = {
  "X-UniProt-Release": "captured-release",
  "X-UniProt-Release-Date": "captured-date",
  "Content-Type": "application/json",
}


class FakeResponse:
  def __init__(self, url, *, payload=None, text="", status=200, headers=None):
    self.url = url
    self._payload = payload
    self.text = text
    self.status_code = status
    self.headers = dict(headers or {})
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

  def get(self, url, *, params=None, timeout=None):
    self.calls.append((url, params, timeout))
    response = self.responses.pop(0)
    if response.url is None:
      response.url = url
    return response


def load_document(name="p04637.json"):
  with (FIXTURES / name).open(encoding="utf-8") as handle:
    return json.load(handle)


def test_entry_json_and_fasta_retain_payload_and_release_metadata():
  entry_url = "https://rest.example/uniprotkb/P04637.json"
  fasta_url = "https://rest.example/uniprotkb/P04637.fasta"
  document = load_document()
  fasta = ">sp|P04637|P53_HUMAN Cellular tumor antigen p53\nMEEPQSDPSV\n"
  session = FakeSession([
    FakeResponse(entry_url, payload=document, headers=RELEASE_HEADERS),
    FakeResponse(
      fasta_url,
      text=fasta,
      headers={**RELEASE_HEADERS, "Content-Type": "text/plain; format=fasta"},
    ),
  ])
  client = UniProtClient(base_url="https://rest.example", session=session)

  json_response = client.get_entry("P04637")
  fasta_response = client.get_entry_text("P04637", format="fasta")

  assert json_response.entry.to_dict() == document
  assert json_response.entry.features[1]["alternativeSequence"] == {
    "originalSequence": "R", "alternativeSequences": ["H", "C"]
  }
  assert json_response.metadata.release == "captured-release"
  assert json_response.metadata.release_date == "captured-date"
  assert json_response.metadata.url == entry_url
  assert fasta_response.text == fasta
  assert fasta_response.metadata.release == "captured-release"
  assert fasta_response.metadata.content_type == "text/plain; format=fasta"


def test_transient_response_honors_retry_after_then_returns_entry():
  url = "https://rest.example/uniprotkb/P04637.json"
  transient = FakeResponse(
    url,
    payload={"messages": ["temporarily unavailable"]},
    status=503,
    headers={"Retry-After": "2.5"},
  )
  recovered = FakeResponse(url, payload=load_document(), headers=RELEASE_HEADERS)
  session = FakeSession([transient, recovered])
  delays = []
  client = UniProtClient(
    base_url="https://rest.example",
    session=session,
    max_retries=1,
    sleep=delays.append,
  )

  response = client.get_entry("P04637")

  assert response.entry.accession == "P04637"
  assert delays == [2.5]
  assert transient.closed is True
  assert len(session.calls) == 2


def test_search_follows_opaque_absolute_next_link_verbatim_and_preserves_totals():
  first_url = "https://rest.example/uniprotkb/search"
  next_url = "https://cursor.example/uniprotkb/search?cursor=opaque%2Btoken&size=1"
  first_headers = {
    **RELEASE_HEADERS,
    "X-Total-Results": "2",
    "Link": '<{}>; rel="next"'.format(next_url),
  }
  first = FakeResponse(
    first_url,
    payload={"results": [load_document("p04637.json")]},
    headers=first_headers,
  )
  second = FakeResponse(
    next_url,
    payload={"results": [load_document("p04637-2.json")]},
    headers={**RELEASE_HEADERS, "X-Total-Results": "2"},
  )
  session = FakeSession([first, second])
  client = UniProtClient(base_url="https://rest.example", session=session)

  pages = list(client.search_entries(
    "gene:TP53 AND organism_id:9606",
    size=1,
    include_isoform=True,
  ))

  assert [[entry.accession for entry in page.entries] for page in pages] == [
    ["P04637"], ["P04637-2"]
  ]
  assert pages[0].metadata.total_results == 2
  assert pages[0].next_url == next_url
  assert pages[1].next_url is None
  assert session.calls[0][1] == {
    "query": "gene:TP53 AND organism_id:9606",
    "format": "json",
    "size": "1",
    "includeIsoform": "true",
  }
  assert session.calls[1][0] == next_url
  assert session.calls[1][1] is None


def test_search_iterators_validate_arguments_eagerly():
  session = FakeSession([])
  client = UniProtClient(base_url="https://rest.example", session=session)

  with pytest.raises(ValueError):
    client.search_entries("")
  with pytest.raises(ValueError):
    client.search_proteomes("   ")
  with pytest.raises(ValueError):
    client.search_taxa("")

  assert session.calls == []
