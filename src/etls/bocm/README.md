
# Estructura del BOCM  

[Estructura del BOCM](https://www.bocm.es/estructura)

# Normativa 

[Normativa del BOCM](https://www.bocm.es/normativa-reguladora)


# Portal de Legislación 

[wleg_pub](https://gestiona.comunidad.madrid/wleg_pub/secure/busquedaAvanzada/buscador.jsf?id=1)

# Funcionamiento del scraper

1. `POST https://www.bocm.es/search-day-month` con la fecha redirige a la página del boletín del día
   (p. ej. `/boletin/bocm-20261014-245`) o se queda en la búsqueda si no hay boletín.
2. Del sumario completo (`/boletin-completo/BOCM-20261014/245`) se descargan los documentos de la sección I.A
   (Disposiciones Generales), III (Administración Local) y IV (Administración de Justicia).
3. De la página HTML de cada documento (p. ej. `/bocm-20261014-32`) se extraen el CVE (`meta[name="TituloGSA"]`,
   que es el `identificador`), el título (`#entradilla`; si falta, `meta[name="description"]`), la cabecera
   (número de boletín, código de sección, páginas, PDF) y el texto (`div[property="content:encoded"]`).

# Campos según la sección

Los encabezados del cuerpo (`#cabeceras p`) empiezan por el nombre de la sección, seguido de:

SECCIÓN | SUBSECCIÓN | DEPARTAMENTO | APARTADO | RANGO
:---:|:---:|:---:|:---:|:---:
 1 | x (p. ej. "A) Disposiciones Generales") | x (consejería) | | x (p. ej. "DECRETO", del título)
 3 | | x (ayuntamiento) | x (materia, p. ej. "URBANISMO") |
 4 | | x (órgano judicial) | |

> NOTA ACLARATORIA:  (x = Tiene ese campo)

# IDEAS para la mejora de los metadatos del scrapeo

- Añadir una sección de análisis en los metadatos, cruzando info con el portal de legislación
