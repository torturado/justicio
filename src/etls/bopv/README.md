
# Web principal

[Web principal del BOPV](https://www.euskadi.eus/web01-bopv/es/bopv2/datos/Ultimo.shtml)


# Portal de búsqueda avanzada

[Portal de búsqueda avanzada](https://www.euskadi.eus/web01-bopv/es/p43aBOPVWebWar/buscarAvanzada.do?idioma=es&tipoBusqueda=2)

# Ejemplo de documentos scrapeados

[Doc1] (https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2024/02/2400757a.shtml)
[Doc2] (https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2024/02/2400759a.shtml)

# Funcionamiento del scraper

1. Calendario del mes: [`/bopv2/datos/MMYYYY.shtml`](https://www.euskadi.eus/bopv2/datos/102026.shtml) define en
   JavaScript los días con boletín y sus sumarios, p. ej.
   `var diasHabilitados = ['20261001', ...];` y `var enlaces = [['s26_0188.shtml'], ...];`. Los arrays se leen como
   literales (`ast.literal_eval`), nunca se ejecutan. Un día puede tener más de un boletín (p. ej. 2025-12-01:
   `s25_0231.shtml` y `s25_0232.shtml`).
2. Sumario del boletín: `/web01-bopv/es/bopv2/datos/YYYY/MM/<sumario>`, p. ej.
   [`s26_0194.shtml`](https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/s26_0194.shtml). Cada documento es un
   `div.txtBloque`; el encabezado de sección (`h4.BOPVSumarioSeccion`) solo se rellena antes del primer documento de
   cada sección. Se descargan las secciones "Disposiciones generales" y "Otras disposiciones".
3. Documento: p. ej. [`2604178a.shtml`](https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604178a.shtml).
   El texto son los `p` y `h6` con clase `BOPV*` de `div.colCentralinterior`; el identificador es la ruta
   `2026/10/2604178a`.

## Verificación en vivo (2026-10-21)

- La página mensual que usaba el scraper anterior, `/web01-bopv/es/bopv2/datos/MMYYYY.shtml`, ya no incluye los arrays
  `diasHabilitados` y `enlaces` (solo carga el calendario por JavaScript), por lo que el scraper anterior no encontraba
  ningún boletín. Los arrays están en `/bopv2/datos/MMYYYY.shtml`.
- Los sumarios y los documentos mantienen la estructura HTML que esperaba el scraper anterior, tanto bajo
  `/web01-bopv/es/bopv2/datos/` como bajo `/bopv2/datos/`.
- 2026-10-14 (sumario 196) y 2026-10-15 (sumario 197): 6 y 9 documentos de "Otras disposiciones", todos con título,
  texto, órgano y PDF. 2025-12-01: 6 documentos de sus dos boletines.
