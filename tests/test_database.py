import json
from pathlib import Path

import pytest

from uniprotpy.database import UniProtStore
from uniprotpy.models import UniProtEntry


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def uniprot_document():
  with (FIXTURES / "p04637.json").open(encoding="utf-8") as handle:
    return json.load(handle)


def accessions(entries):
  return [entry.accession for entry in entries]


@pytest.mark.parametrize("domain_value", [False, True], ids=["json-document", "domain-entry"])
def test_store_round_trip_and_indexes_faithful_uniprot_entries(
  tmp_path, uniprot_document, domain_value
):
  database_path = tmp_path / "entries.sqlite"
  value = (
    UniProtEntry.from_json(uniprot_document)
    if domain_value
    else uniprot_document
  )

  with UniProtStore(database_path) as store:
    added = store.add(value)
    assert added.to_dict() == uniprot_document

  with UniProtStore(database_path) as reopened:
    stored = reopened.get("P04637")

    assert stored is not None
    assert stored.to_dict() == uniprot_document
    assert (
      stored.accession,
      stored.uniprotkb_id,
      stored.protein_name,
      stored.gene_name,
      stored.taxon_id,
      stored.protein_existence,
      stored.entry_version,
      stored.sequence_version,
    ) == (
      "P04637",
      "P53_HUMAN",
      "Cellular tumor antigen p53",
      "TP53",
      9606,
      "1: Evidence at protein level",
      250,
      4,
    )
    assert reopened.list_accessions() == ["P04637"]

    expected_gene_matches = {
      "tp53": ["P04637"],
      "p53": ["P04637"],
      "bcc7": ["P04637"],
      "lfs1": ["P04637"],
      "trp53": ["P04637"],
      "wrap53": ["P04637"],
    }
    assert {
      query: accessions(reopened.find_by_gene(query))
      for query in expected_gene_matches
    } == expected_gene_matches
    assert accessions(
      reopened.find_by_name("CELLULAR TUMOR ANTIGEN P53")
    ) == ["P04637"]
    assert reopened.find_by_gene("missing") == []
    assert reopened.find_by_name("missing") == []
