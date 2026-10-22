# BOPZ

Boletín Oficial de la Provincia de Zaragoza. The scrapper only downloads the edicts published by the
**Ayuntamiento de Zaragoza**: the search form it submits filters by that publisher.

- [Main page](https://bop.dpz.es/BOPZ/)
- [Regulations](https://bop.dpz.es/BOPZ/portal/normativa.pdf)
- [Edict search](https://bop.dpz.es/BOPZ/portalBuscarEdictos.do)
- Example edicts: [729066](https://bop.dpz.es/BOPZ/obtenerContenidoEdicto.do?idEdicto=729066),
  [729162](https://bop.dpz.es/BOPZ/obtenerContenidoEdicto.do?idEdicto=729162)

## Flow

1. The edict search (`portalBuscarEdictos.do`) is submitted for the publication day, and every result page is read:
   pages have 20 edicts and are numbered from 0 in the `numPag` field. Rows without a bulletin PDF are not published
   edicts and are ignored.
2. The `idEdicto` of each row, taken from its `onclick` handler, is the `identificador` and gives the edict page
   (`obtenerContenidoEdicto.do?idEdicto=...`), which has the text and the summary. A row without an `idEdicto` is
   recorded as a failure under its PDF URL.
3. The row gives the registry number (`numero_registro`, e.g. `10885/2018`), the publisher (`departamento`), the
   subject (`materia`) and the publication date. The title combines the summary of the edict with its publisher and
   subject, e.g. `APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018 (AYUNTAMIENTO DE ZARAGOZA, Empleo
   Público)`.

The pages declare their charset (ISO-8859-1) only in the HTTP `Content-Type` header, so they are decoded by
`requests` rather than parsed from the raw bytes.

## Loading

```sh
python -m src.etls.cli bulletin dates BOPZ 2026/10/16 2026/10/19
python -m src.etls.cli bulletin update BOPZ
```

## Verification status

On 2026-10-22 the scrapper was run against the live site from Spain; the site refuses connections from other
countries. It downloaded 15 edicts published from 2026-10-15 to 2026-10-21, with no failures. All of them have a
summary and a text, and their PDF links download the edict. A search from 2026-09-01 to 2026-10-21 spans six result
pages and returns the same 113 edicts as the searches of each day. No day of the previous twelve months had more
than 11 edicts of the Ayuntamiento, so a single day spanning several pages was not observed.
