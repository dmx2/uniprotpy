"""Explicit parsers for external FASTA artifacts.

FASTA is not treated as an authoritative UniProt annotation document. Callers
must state whether their source is a path, an open text handle, or literal text;
string inputs are never guessed to be paths.
"""

from dataclasses import dataclass
from io import StringIO
from os import PathLike
from pathlib import Path
from typing import Any, Iterable, TextIO, Tuple, Union

from Bio import SeqIO


@dataclass(frozen=True)
class FastaRecord:
  """One external FASTA record without invented UniProt annotations."""

  identifier: str
  description: str
  sequence: str

  @property
  def accession(self) -> str:
    parts = self.identifier.split("|")
    return parts[1] if len(parts) >= 3 and parts[1] else self.identifier

  def to_fasta(self, line_width: int = 60) -> str:
    if line_width < 1:
      raise ValueError("line_width must be positive")
    header = self.description or self.identifier
    lines = [
      self.sequence[offset:offset + line_width]
      for offset in range(0, len(self.sequence), line_width)
    ]
    return ">{}\n{}\n".format(header, "\n".join(lines))


def _parse(source: Any) -> Tuple[FastaRecord, ...]:
  return tuple(
    FastaRecord(
      identifier=str(record.id),
      description=str(record.description),
      sequence=str(record.seq),
    )
    for record in SeqIO.parse(source, "fasta")
  )


def parse_fasta_path(path: Union[str, PathLike[str]]) -> Tuple[FastaRecord, ...]:
  """Parse an explicitly path-valued FASTA source."""
  if not isinstance(path, (str, PathLike)):
    raise TypeError("path must be a string or path-like value")
  with Path(path).expanduser().open(encoding="utf-8") as handle:
    return _parse(handle)


def parse_fasta_handle(handle: TextIO) -> Tuple[FastaRecord, ...]:
  """Parse an explicitly supplied open text handle without closing it."""
  if not callable(getattr(handle, "read", None)):
    raise TypeError("handle must be a readable text handle")
  return _parse(handle)


def parse_fasta_text(text: str) -> Tuple[FastaRecord, ...]:
  """Parse literal FASTA text; the string is never interpreted as a path."""
  if not isinstance(text, str):
    raise TypeError("text must be a string")
  return _parse(StringIO(text))


def format_fasta(records: Iterable[FastaRecord], line_width: int = 60) -> str:
  """Serialize external FASTA records deterministically."""
  values = tuple(records)
  if any(not isinstance(record, FastaRecord) for record in values):
    raise TypeError("records must contain only FastaRecord values")
  return "".join(record.to_fasta(line_width=line_width) for record in values)


__all__ = [
  "FastaRecord",
  "format_fasta",
  "parse_fasta_handle",
  "parse_fasta_path",
  "parse_fasta_text",
]
