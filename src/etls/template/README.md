# Adding a bulletin source

A bulletin source downloads the documents published on each day. Its scrapper returns one metadata object per
document, with the plain text of the document in `text`. The shared ETL (`src/etls/common/etl.py`) splits the text
into chunks and indexes them with the metadata, and the CLI (`src/etls/cli.py`) runs every registered source, so a
source has no loader of its own. This directory is a minimal example of a fictitious bulletin.

## Files

Create `src/etls/<source>/` with an empty `__init__.py` and these two modules:

1. `metadata.py`: subclass `MetadataDocument` (`src/etls/common/metadata.py`) and set `source_name` (e.g. `"BOJA"`)
   and `source_type`. Canonical fields have the same meaning in every source, because they are shown to the LLM and
   used in filters:
   - `identificador`: stable identifier within the source. Documents already indexed with the same `source_name` and
     `identificador` are skipped.
   - `titulo`: describes the document. It is embedded with every chunk, so it must not be a bare number or code.
   - `numero_oficial`: only the official number of a norm, e.g. `39/2015`. Registry, page or bulletin numbers go in
     source-specific fields, e.g. `numero_registro` in the BOPZ.
   - `fecha_publicacion` and `fecha_disposicion`: ISO dates (`YYYY-MM-DD`). Always set `fecha_publicacion`:
     `bulletin update` resumes from the latest indexed one.

   Add source-specific fields only under names that do not reuse a canonical meaning, with JSON-serializable values.
   `text` is excluded from the chunk metadata and `datetime_insert` is generated; do not redeclare them.
2. `scrapper.py`: subclass `BaseScrapper` (`src/etls/common/scrapper.py`) and implement `download_day(day)`:
   - Make every request with `self._session`, created by `create_session()` with retries and a default timeout.
     `get_soup(self._session, url)` (`src/etls/common/http.py`) fetches and parses a page, using the charset of the
     `Content-Type` header when the site declares one.
   - If the summary of the day cannot be read, raise: `requests.HTTPError` from `raise_for_status()`, or
     `ScrapperError` (`src/etls/common/utils.py`) when a page lacks the expected structure. `download_days` records
     the day as failed and continues with the next one.
   - Download each document inside `try` / `except Exception: self._record_failure(url)`, so that one bad document
     is reported without aborting the day.
   - Return the documents with `text=...`, one line per paragraph. Keep the parsing in functions that take the parsed
     page, so that they can be tested with small inline HTML strings.

## Registering the source

1. Add the scrapper class to `BULLETIN_SCRAPPERS` in `src/etls/sources.py`, keyed by its `source_name`. The `daily`
   command updates the sources in that order.

   ```python
   BULLETIN_SCRAPPERS: dict[str, type[BaseScrapper]] = {
       ...
       "BOJA": BOJAScrapper,
   }
   ```

2. Add the source and a one-line description of its scope under `sources:` in `config/config.yaml`. The CLI refuses
   to run while a registered source has no scope.

   ```yaml
   sources:
     BOJA: Boletín Oficial de la Junta de Andalucía
   ```

## Running a load

```sh
python -m src.etls.cli bulletin dates <SOURCE> 2026/10/16 2026/10/19
python -m src.etls.cli bulletin update <SOURCE>
```

`dates` includes both endpoints. `update` loads from the latest indexed publication date (included, in case that day
was loaded partially) to today, going back at most 30 days; a source without indexed documents loads only today. Use
`dates` for the initial load. Every run emails a report and exits with status 1 if a
day or a document failed. Keep live checks of a scrapper small and sequential.
