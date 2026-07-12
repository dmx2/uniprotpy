import json
from pathlib import Path

import pytest

from uniprotpy.models import UniProtEntry


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def reviewed_document():
  with (FIXTURES / "p04637.json").open(encoding="utf-8") as handle:
    return json.load(handle)


@pytest.fixture
def reviewed_entry(reviewed_document):
  return UniProtEntry.from_json(reviewed_document)


def test_reviewed_entry_exposes_stable_projections_without_flattening(reviewed_entry):
  entry = reviewed_entry

  assert entry.accession == "P04637"
  assert entry.canonical_accession == "P04637"
  assert entry.is_isoform is False
  assert entry.reviewed is True
  assert entry.secondary_accessions == ("Q15086", "Q15087")
  assert entry.uniprotkb_id == "P53_HUMAN"
  assert entry.protein_name == "Cellular tumor antigen p53"
  assert "Phosphoprotein p53" in entry.protein_names
  assert "p53" in entry.protein_names
  assert entry.gene_name == "TP53"
  assert entry.gene_names == (
    "TP53", "P53", "BCC7", "LFS1", "TRP53", "TP53-AS1", "WRAP53"
  )
  assert entry.organism_name == "Homo sapiens"
  assert entry.taxon_id == 9606
  assert entry.protein_existence == "1: Evidence at protein level"
  assert entry.entry_version == 250
  assert entry.sequence_version == 4
  assert len(entry.sequence) == 393

  assert {feature["type"] for feature in entry.features} == {
    "Domain", "Mutagenesis", "Alternative sequence", "Binding site"
  }
  assert {comment["commentType"] for comment in entry.comments} == {
    "FUNCTION", "SUBCELLULAR LOCATION", "ALTERNATIVE PRODUCTS", "INTERACTION"
  }
  assert [xref["database"] for xref in entry.cross_references] == ["PDB", "Ensembl"]
  assert [keyword["name"] for keyword in entry.keywords] == ["Apoptosis", "Reference proteome"]


def test_entry_serialization_is_lossless_and_returns_defensive_copies(reviewed_document):
  entry = UniProtEntry.from_json(reviewed_document)

  assert entry.to_dict() == reviewed_document
  assert entry.dict() == reviewed_document
  assert entry.to_dict()["futureSchemaField"]["nested"][0] == {
    "flag": True, "nullable": None, "score": 0.125
  }

  reviewed_document["genes"][0]["geneName"]["value"] = "CHANGED-OUTSIDE"
  emitted = entry.to_dict()
  emitted["features"][0]["description"] = "CHANGED-COPY"

  assert entry.gene_name == "TP53"
  assert entry.features[0]["description"] == "DNA-binding"


def test_direct_isoform_projection_has_distinct_accession_and_canonical_parent():
  with (FIXTURES / "p04637-2.json").open(encoding="utf-8") as handle:
    document = json.load(handle)

  entry = UniProtEntry.from_json(document)

  assert entry.accession == "P04637-2"
  assert entry.canonical_accession == "P04637"
  assert entry.is_isoform is True
  assert entry.reviewed is True
  assert entry.sequence == document["sequence"]["value"]
  assert entry.to_dict() == document
  assert entry.to_dict()["futureIsoformProjection"] == {"preserve": ["unknown", 2]}
