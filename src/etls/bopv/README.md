
# Web principal

[Web principal del BOPV](https://www.euskadi.eus/web01-bopv/es/bopv2/datos/Ultimo.shtml)


# Portal de búsqueda avanzada

[Portal de búsqueda avanzada](https://www.euskadi.eus/web01-bopv/es/p43aBOPVWebWar/buscarAvanzada.do?idioma=es&tipoBusqueda=2)

# Ejemplo de documentos scrapeados

[Doc1] (https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2024/02/2400757a.shtml)
[Doc2] (https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2024/02/2400759a.shtml)

## Live scraper status

The scraper could not be validated for 2026-10-19. Its date lookup expects a monthly page at
`/web01-bopv/es/bopv2/datos/102026.shtml` with the JavaScript arrays `diasHabilitados` and `enlaces`.
The current official index is [`Ultimo.shtml`](https://www.euskadi.eus/bopv2/datos/Ultimo.shtml), and official pages
are indexed under `/bopv2/datos/`, with daily summaries such as
[`s26_0194.shtml`](https://www.euskadi.eus/bopv2/datos/2026/10/s26_0194.shtml) and individual documents such as
[`2604192a.shtml`](https://www.euskadi.eus/bopv2/datos/2026/10/2604192a.shtml). Mapping a date to the new daily
summary and adapting its HTML requires a scraper rewrite, so it is not included here.
The official latest index showed issue 198, dated 2026-10-16, when checked.

Direct requests from this environment to both the old monthly URL and the current official `Ultimo.shtml` endpoint
timed out while connecting. The current structure evidence comes from official indexed pages; scraping the new
pages still needs live validation from an environment that can reach `www.euskadi.eus`.
