# mi-epg

Generador de guía XMLTV para los canales manual-json de Movistar.

La guía publicada se sirve en:

```text
https://ussefom.github.io/mi-epg/movistar.xml.gz
```

## Funcionamiento

GitHub Actions descarga la playlist indicada en el secreto `PLAYLIST_URL`, extrae solo `CodCadenaTv`, nombre y logo, y consulta la rejilla de Movistar por canal. Nunca se escribe ni se registra la playlist completa, claves DRM, licencias o cabeceras.

La ejecución programada ocurre cada seis horas y puede iniciarse manualmente desde **Actions → Publish XMLTV guide → Run workflow**. GitHub puede retrasar ejecuciones programadas; una ejecución sin datos nuevos no sustituye la última guía publicada.

El resultado contiene:

- `movistar.xml.gz`: fuente XMLTV principal.
- `movistar.xml`: versión sin comprimir para diagnóstico.
- `status.json`: contadores y fecha de generación, sin datos sensibles.

## Configuración inicial

Configura el secreto del repositorio `PLAYLIST_URL` con la URL del JSON manual. Localmente:

```powershell
$env:PLAYLIST_URL = 'https://…'
python src/generate_epg.py --output-dir site
```

Ejecuta las pruebas con:

```powershell
python -m unittest discover -s tests -v
```

## Integración posterior con OTT Navigator

La primera fase solo publica la guía. Tras validar la URL, el bridge manual-json de la APK podrá emitir `url-tvg="https://ussefom.github.io/mi-epg/movistar.xml.gz"`; no se debe usar la API JSON de Movistar directamente como fuente EPG.
