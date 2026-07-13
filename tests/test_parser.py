from io import StringIO

import pytest

from uniprotpy.parser import (
  FastaRecord,
  format_fasta,
  parse_fasta_handle,
  parse_fasta_path,
  parse_fasta_text,
)


FASTA_TEXT = (
  ">sp|P04637|P53_HUMAN Cellular tumor antigen p53 "
  "OS=Homo sapiens OX=9606 GN=TP53 PE=1 SV=4\n"
  "MEEPQSDPSV\n"
  "EPPLSQETFS\n"
  ">A0A075B6G3 Dystrophin fragment\n"
  "MLWWEEVEDC\n"
)

EXPECTED_RECORDS = (
  FastaRecord(
    identifier="sp|P04637|P53_HUMAN",
    description=(
      "sp|P04637|P53_HUMAN Cellular tumor antigen p53 "
      "OS=Homo sapiens OX=9606 GN=TP53 PE=1 SV=4"
    ),
    sequence="MEEPQSDPSVEPPLSQETFS",
  ),
  FastaRecord(
    identifier="A0A075B6G3",
    description="A0A075B6G3 Dystrophin fragment",
    sequence="MLWWEEVEDC",
  ),
)


def test_literal_text_parses_multiple_records_without_inventing_annotations():
  assert parse_fasta_text(FASTA_TEXT) == EXPECTED_RECORDS


def test_explicit_path_source_reads_file_contents(tmp_path):
  fasta_path = tmp_path / "proteins.fasta"
  fasta_path.write_text(FASTA_TEXT, encoding="utf-8")

  assert parse_fasta_path(fasta_path) == EXPECTED_RECORDS


def test_literal_text_is_never_guessed_to_be_a_path(tmp_path, monkeypatch):
  source = ">literal"
  (tmp_path / source).write_text(">from-file\nAAAA\n", encoding="utf-8")
  monkeypatch.chdir(tmp_path)

  assert parse_fasta_text(source) == (
    FastaRecord(identifier="literal", description="literal", sequence=""),
  )


def test_handle_source_remains_owned_by_the_caller():
  handle = StringIO(FASTA_TEXT)

  assert parse_fasta_handle(handle) == EXPECTED_RECORDS
  assert handle.closed is False

  handle.seek(0)
  assert handle.read() == FASTA_TEXT
  handle.close()


@pytest.mark.parametrize(
  ("identifier", "accession"),
  [
    ("sp|P04637|P53_HUMAN", "P04637"),
    ("tr|A0A075B6G3|A0A075B6G3_HUMAN", "A0A075B6G3"),
    ("A0A075B6G3", "A0A075B6G3"),
    ("sp||MISSING_ACCESSION", "sp||MISSING_ACCESSION"),
  ],
)
def test_accession_extracts_only_well_formed_pipe_identifiers(identifier, accession):
  record = FastaRecord(identifier=identifier, description="", sequence="M")

  assert record.accession == accession


def test_format_fasta_preserves_order_and_wraps_sequences_deterministically():
  records = (
    FastaRecord("sp|P04637|P53_HUMAN", "sp|P04637|P53_HUMAN p53", "MEEPQSD"),
    FastaRecord("A0A075B6G3", "", "MLWW"),
  )

  assert format_fasta(records, line_width=4) == (
    ">sp|P04637|P53_HUMAN p53\n"
    "MEEP\n"
    "QSD\n"
    ">A0A075B6G3\n"
    "MLWW\n"
  )


@pytest.mark.parametrize(
  ("parser", "source", "message"),
  [
    (parse_fasta_path, StringIO(FASTA_TEXT), "path must be a string or path-like value"),
    (parse_fasta_handle, FASTA_TEXT, "handle must be a readable text handle"),
    (parse_fasta_text, FASTA_TEXT.encode(), "text must be a string"),
  ],
  ids=["path-rejects-handle", "handle-rejects-string", "text-rejects-bytes"],
)
def test_source_parsers_reject_the_wrong_source_type(parser, source, message):
  with pytest.raises(TypeError, match=message):
    parser(source)


def test_format_fasta_rejects_non_records_and_invalid_line_width():
  with pytest.raises(TypeError, match="records must contain only FastaRecord values"):
    format_fasta(["not-a-record"])

  with pytest.raises(ValueError, match="line_width must be positive"):
    format_fasta(EXPECTED_RECORDS, line_width=0)

