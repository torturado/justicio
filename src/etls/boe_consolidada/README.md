# BOE consolidated legislation

This ETL indexes the BOE's current consolidated text, one article or block per chunk, in the `justicio` collection.

```sh
python -m src.etls.boe_consolidada.load all
python -m src.etls.boe_consolidada.load since 2026/01/01
python -m src.etls.boe_consolidada.load document BOE-A-2015-10565
python -m src.etls.boe_consolidada.load today
```

The daily job reloads norms updated since yesterday and norms whose indexed text has reached `fecha_revision`.
