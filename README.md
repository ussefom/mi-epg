# mi-epg

Generador de guía XMLTV para los canales manual-json de Movistar.

La guía principal se publica como asset de la última GitHub Release:

```text
https://github.com/ussefom/mi-epg/releases/latest/download/movistar.xml.gz
```

También se publica una fuente de recuperación pequeña, pensada para dispositivos
que no descarguen el gzip completo:

```text
https://github.com/ussefom/mi-epg/releases/latest/download/movistar-compact.xml.gz
```

## Funcionamiento

GitHub Actions descarga la playlist indicada en el secreto `PLAYLIST_URL`, extrae
solo `tvg-id` (o el `CodCadenaTv` antiguo), nombre y logo, y consulta la rejilla
de Movistar por canal. Nunca se escribe ni se registra la playlist completa,
claves DRM, licencias o cabeceras.

La ejecución programada ocurre cada seis horas y puede iniciarse manualmente desde
**Actions → Publish XMLTV guide → Run workflow**. Cada ejecución sube los assets a
una release borrador, los verifica y solo entonces la publica. Si falla la
generación, la última release válida permanece disponible. Se conservan las cinco
releases XMLTV válidas más recientes.

El resultado contiene:

- `movistar.xml.gz`: fuente XMLTV principal de dos días.
- `movistar-compact.xml.gz`: fuente con programas actuales y próximos; intenta 24 h y reduce a 12 h si superaría 40 KiB. Solo incluye IDs, horarios y títulos.
- `status.json`: contadores, tamaño, hashes SHA-256 y ventana compacta, sin datos sensibles.

La antigua URL de GitHub Pages se conserva solo como fuente de migración para el
generador; no se actualiza y no debe configurarse en nuevos proveedores.

## Configuración inicial

Configura el secreto del repositorio `PLAYLIST_URL` con la URL del JSON manual. Localmente:

```powershell
$env:PLAYLIST_URL = 'https://…'
    python src/generate_epg.py --output-dir release-assets
```

Ejecuta las pruebas con:

```powershell
python -m unittest discover -s tests -v
```

## Integración posterior con OTT Navigator

Usa el metadato raíz del JSON manual:

```json
"url-tvg": "https://github.com/ussefom/mi-epg/releases/latest/download/movistar.xml.gz"
```

Si una TV no descarga la guía completa, sustituye únicamente esa URL por
`movistar-compact.xml.gz` y recarga EPG. No se debe usar la API JSON de Movistar
directamente como fuente EPG.
