# Adding a bulletin ETL

A bulletin is a collection of documents published by date. Each scraper returns one metadata object per document,
with the document text written to a temporary file for the shared ETL to chunk and index.

## Files to implement

1. `defs.py`: set a unique `COLLECTION_NAME` and use it in `config/config.yaml` under `collections`.
2. `metadata.py`: extend `MetadataDocument`. Inherit its required `filepath` and stable string `identificador`, and
   declare `source_name` and `source_type`. Add bulletin-specific fields as needed. Keep every metadata value
   JSON-serializable. `datetime_insert` is inherited and generated for each document; do not redeclare it.
3. `scrapper.py`: implement `download_day(day: date)` and `download_document(url)`. Return metadata objects whose
   `filepath` points to a UTF-8 text file. Use a stable identifier so already indexed documents can be skipped.
   `BaseScrapper.download_days(date_start, date_end)` loops through the inclusive date range.
4. `load.py`: construct `ETL(config_loader, vector_store)` and call `run(docs)`. It returns the number of newly
   indexed documents. Report both downloaded and indexed counts; the vector store is selected by
   `init_objects.vector_store[COLLECTION_NAME]` and is not configured through a `vector_store` YAML key.
5. Register the collection in `config/config.yaml`. To run it daily, add the loader to `src/etls/jobs.py` and schedule
   its `today` function.

## Run a load

```sh
python -m src.etls.template.load today
python -m src.etls.template.load dates 2026/10/16 2026/10/19
```

The `dates` command includes both endpoints. Keep live scraper checks small and sequential, and remove temporary
download files when validating a scraper without calling `ETL.run`.
