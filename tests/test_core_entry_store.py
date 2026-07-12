import json
from pathlib import Path

from uniprotpy.database import UniProtStore
from uniprotpy.models import UniProtEntry


FIXTURES = Path(__file__).parent / "fixtures"


def load_document(name):
  with (FIXTURES / name).open(encoding="utf-8") as handle:
    return json.load(handle)


def accessions(entries):
  return [entry.accession for entry in entries]


def test_sqlite_reopen_round_trip_preserves_complete_documents_and_release_metadata(tmp_path):
  canonical_document = load_document("p04637.json")
  isoform_document = load_document("p04637-2.json")
  database_path = tmp_path / "reviewed-entries.sqlite"

  with UniProtStore(
    database_path,
    dataset_key="captured-review",
    requested_release="capture-request",
    observed_release="capture-observed",
    observed_release_date="capture-date",
    source_url="https://rest.uniprot.org/uniprotkb/search",
    source_query="accession:P04637",
    complete=True,
    provenance={"fixture": True, "unknownMetadata": {"kept": 7}},
  ) as store:
    assert store.add_all([
      UniProtEntry.from_json(canonical_document),
      isoform_document,
    ]) == 2

  with UniProtStore(database_path, dataset_key="captured-review") as reopened:
    canonical = reopened.get("P04637")
    isoform = reopened.get_by_accession("P04637-2")

    assert canonical is not None
    assert isoform is not None
    assert canonical.to_dict() == canonical_document
    assert isoform.to_dict() == isoform_document
    assert canonical.to_dict()["futureSchemaField"]["variant"] == "preserve-me"
    assert isoform.canonical_accession == canonical.accession
    assert reopened.get("NOT-AN-ACCESSION") is None
    assert reopened.list_accessions() == ["P04637", "P04637-2"]

    metadata = reopened.release_metadata
    assert metadata["requested_release"] == "capture-request"
    assert metadata["observed_release"] == "capture-observed"
    assert metadata["observed_release_date"] == "capture-date"
    assert metadata["source_query"] == "accession:P04637"
    assert metadata["provenance"] == {
      "fixture": True, "unknownMetadata": {"kept": 7}
    }


def test_indexed_queries_cover_primary_gene_synonyms_locus_orf_and_protein_name(tmp_path):
  database_path = tmp_path / "entry-indexes.sqlite"
  with UniProtStore(database_path) as store:
    store.add_all([
      load_document("p04637.json"),
      load_document("p04637-2.json"),
    ])

    assert accessions(store.find_by_gene("tp53")) == ["P04637", "P04637-2"]
    assert accessions(store.entries_by_gene("p53")) == ["P04637", "P04637-2"]
    assert accessions(store.query_by_gene("bcc7")) == ["P04637"]
    assert accessions(store.find_by_gene("lfs1")) == ["P04637"]
    assert accessions(store.find_by_gene("trp53")) == ["P04637"]
    assert accessions(store.find_by_gene("wrap53")) == ["P04637"]
    assert store.find_by_gene("missing") == []

    assert accessions(store.find_by_name("CELLULAR TUMOR ANTIGEN P53")) == [
      "P04637", "P04637-2"
    ]
    assert store.entries_by_name("Phosphoprotein p53") == []
    assert store.find_by_name("missing") == []
