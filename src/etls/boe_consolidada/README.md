# BOE consolidated legislation

This ETL indexes the current consolidated text of the norms in force from the BOE open data API, split by article
or block (long blocks are split into chunks of `chunk_size` characters). Its chunks have `source_name` `BOE` and
`source_type` `Legislación consolidada`, and replace the originally published text of the same norm.

```sh
python -m src.etls.cli consolidated all                        # initial load of the catalogue
python -m src.etls.cli consolidated since 2026/01/01           # norms updated since a date
python -m src.etls.cli consolidated document BOE-A-2015-10565  # one norm
python -m src.etls.cli consolidated update                     # daily update
```

`update` reloads the norms updated since the latest indexed `fecha_actualizacion`, so it catches up after missed
runs, and the norms whose indexed text has reached its `fecha_revision` (the date of their next scheduled version).
Norms that are repealed, annulled or expired are deleted.
