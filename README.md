# UniProtPy

A Python client and release-aware local SQLite cache for the current UniProt REST API.

UniProtPy preserves the complete nested UniProtKB JSON document while adding convenient accessors and indexed local queries. It models proteins and proteomes directly; it does not force UniProt data into a genomic interval schema.

## Installation

```bash
pip install uniprotpy
```

## Fetch an entry

```python
from uniprotpy import UniProtClient

client = UniProtClient()
response = client.get_entry("P04637")
entry = response.entry

print(entry.primary_protein_name)
print(entry.primary_gene_name)
print(entry.sequence)

fasta = client.get_entry_text("P04637", format="fasta").text
```

`entry.to_dict()` returns the faithful source JSON, including features, comments, cross-references, keywords, references, evidence, and fields unknown to this library version.

## Command line

```text
uniprotpy entry get P04637 --format json --output p53.json
uniprotpy entry get P04637 --format tsv --fields accession,gene_name,organism
uniprotpy install entries P04637 P38398 --release 2026_02
uniprotpy install proteome UP000005640 --release 2026_02
uniprotpy query --release 2026_02 --gene TP53 --format fasta
uniprotpy query --release 2026_02 --all --format tsv --fields accession,protein_name
```

Entry fetches support faithful source JSON, API-provided FASTA, and field-selected TSV. Cached queries support accession, exact gene or primary protein name, installed proteome, or all entries. TSV fields may name stable accessors (`accession`, `canonical_accession`, `uniprotkb_id`, `reviewed`, `protein_name`, `gene_name`, `taxon_id`, `sequence`), raw top-level keys, or dotted raw paths; nested values are JSON-encoded. Output defaults to stdout; pass `--output` to write UTF-8 text to a file.

## Install and query a release cache

```python
from uniprotpy import UniProtRelease

release = UniProtRelease("2026_02")
release.install_entries(["P04637", "P38398"])

p53 = release.entry("P04637")
tp53_entries = release.entries_by_gene("TP53")
named_entries = release.entries_by_name("Cellular tumor antigen p53")
release.close()
```

Set `UNIPROTPY_CACHE_DIR` to override the platform cache root, or pass `cache_dir=` explicitly. Data are stored under a release-specific directory. The observed `X-UniProt-Release` response header is checked during installation.

## Proteomes

```python
from uniprotpy import ProteomeSelector, UniProtClient, UniProtRelease

client = UniProtClient()
proteome = client.get_proteome("UP000005640").proteome

candidates = client.reference_proteomes(9606, scope="exact")
selection = ProteomeSelector().select(candidates)

release = UniProtRelease("2026_02", client=client)
release.install_proteome(proteome.upid)
```

Taxonomy-based proteome searches are lineage-aware. Exact scope filters descendant taxa. UniProt can intentionally designate multiple reference proteomes, so selection returns typed unique, ambiguous, or not-found results rather than silently treating API order or protein count as “best.”

Proteome installation uses cursor-paginated UniProtKB bulk search and persists rich entry JSON, proteome membership, release metadata, and provenance. It does not issue one direct request per protein.

## Current scope

Shipped: rich entry JSON and FASTA retrieval, resilient cursor pagination, lossless domain objects, release-scoped SQLite caching, indexed accession/gene/name queries, proteome metadata/discovery/selection, bulk proteome installation, and the entry/install/cached-query CLI with faithful JSON/FASTA/selected-TSV output.

Not yet shipped: taxonomy domain objects and asynchronous ID Mapping. See `NOTES.md` for the researched contracts and implementation plan.