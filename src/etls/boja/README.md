# BOJA

Boletín Oficial de la Junta de Andalucía. The scrapper downloads the dispositions of the sections "Disposiciones
generales" and "Otras disposiciones" of every bulletin of a day, including extraordinary bulletins.

- [Main page](https://www.juntadeandalucia.es/eboja.html)
- [Advanced search](https://www.juntadeandalucia.es/eboja/index.html)
- Example bulletins: [2024/9](https://www.juntadeandalucia.es/eboja/2024/9/index.html),
  [2024/39](https://www.juntadeandalucia.es/eboja/2024/39/index.html)
- Example disposition: [2024/7/1](https://www.juntadeandalucia.es/boja/2024/7/1)

## Flow

1. The year calendar (`https://www.juntadeandalucia.es/eboja/<year>`, or `/boja/<year>` before 2012) links each
   publication day to its bulletin. When an extraordinary bulletin was published, the day links to a page that lists
   all the bulletins of the day, e.g. `https://www.juntadeandalucia.es/eboja/20240111.html`.
2. The index of each bulletin links to its sections, and each section page to the HTML version of its dispositions.
3. Each disposition page gives the text and metadata. `identificador` is the last three path segments of its URL,
   e.g. `2024/7/1`, and `fecha_publicacion` is the day of the bulletin. Dispositions whose text has restricted access
   are skipped.

## Loading

```sh
python -m src.etls.cli bulletin dates BOJA 2026/10/16 2026/10/19
python -m src.etls.cli bulletin update BOJA
```

## Verification status

On 2026-10-22 the scrapper was run against the live site from Spain; the site refuses connections from other
countries. The days 2026-10-16 to 2026-10-21 gave 68 dispositions from both sections, with no failures; no bulletin
was published at the weekend. The 46 dispositions of 2026-10-19 to 2026-10-21 all have a title and a text.
2026-10-21 has two bulletins (2026/204 and 2026/204/c01), listed by its day page, and dispositions were downloaded
from both.
