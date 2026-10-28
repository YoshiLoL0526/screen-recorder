# 16 — Bloque A: cierre de la entrega

> Resultado del [bloque A de docs/15](15-post-v1.md#bloque-a--cerrar-la-entrega-de-la-v1-verificación-y-distribución),
> hecho el **2026-10-28**. A1, A3, A4 y A5 quedan cerrados y verificados; A2, A6, A7 y A8 son
> comprobaciones manuales (máquina limpia, móvil físico, soak y ratón real) y quedan preparadas
> con su procedimiento y criterio.

## Resumen

| # | Pieza | Estado | Evidencia |
|---|---|---|---|
| A1 | CI en verde | ✅ | run `40700123750`: 206 tests + build + smoke + instalador |
| A2 | Máquina limpia (CU-1→CU-5) | ⏳ | El CI ya instala y hace smoke en un runner limpio; falta el flujo manual |
| A3 | RNF-06/07 con ventana | ✅ | arranque 0.87–1.74 s · RSS 261–414 MB en reposo · 337 MB grabando |
| A4 | Instalador (Inno Setup) | ✅ | `RecStudio-1.0.0-setup.exe` (71 MB), instalación/desinstalación y smoke OK |
| A5 | Bundle más ligero | ✅ | 370 → **253 MB** (−117 MB) sin OpenCV; UPX descartado con datos |
| A6 | Export en móvil físico | ⏳ | Procedimiento listo; falta el teléfono |
| A7 | Soak de 30 min | ⏳ | Comando y criterio listos; no se ejecutó en esta sesión |
| A8 | «Sensación» del timeline | ⏳ | Drags/undo automatizados OK; falta el ratón real |

---

## A1 — CI en verde

El run `39171815584` (commit `5b3e48f`) falló en **un** test:

```
FAILED tests/test_f3_editor.py::test_drags_de_trim_y_mover_con_undo
ValueError: no se pudo decodificar la imagen de 0 bytes
```

**Causa real** (no era el runner): la UI lee la cache de miniaturas mientras el hilo de
`media.thumbstrip` las está generando. Un `write_bytes` directo deja el JPEG visible a medio
escribir; si la lectura cae en ese instante, la tira se cachea en memoria con un JPEG de 0 bytes y
el siguiente frame del timeline falla al decodificarlo. En esta máquina no se reproducía por
timing; el runner, más lento, lo destapó.

**Arreglo** (`fc5860a`):

- cada miniatura se publica con **tmp + `os.replace`** (`_write_bytes_atomic`);
- `cached_thumbstrip` rechaza cualquier archivo que no empiece por el SOI de JPEG (`\xff\xd8`) y
  la tira se regenera;
- test de regresión con una cache incompleta (`tests/test_filmstrip.py`).

**Verificación:** run `40700123750` ✅ — 206 tests en 29 s. El CI ahora hace, en orden: `pytest`,
`tools/check_build.py` (build ligero + smoke del exe) y `tools/check_installer.py` (instalador,
instalación silenciosa, smoke y desinstalación). Los dos artefactos (bundle e instalador) se suben
a Actions.

---

## A2 — Máquina limpia (pendiente manual)

El CI ya cubre la mitad automática: en un `windows-latest` sin Python ni FFmpeg instalados por el
usuario, `uv sync --locked` + build + smoke + instalación silenciosa + smoke del exe instalado.
Lo que **no** puede hacer el runner (sin monitor, GPU ni audio) es el flujo de producto completo.

Procedimiento para una VM o PC sin Python/FFmpeg:

1. Copiar `dist/installer/RecStudio-1.0.0-setup.exe` y ejecutarlo con doble clic (instalación por
   usuario, sin UAC). Comprobar que crea el acceso directo y que «Acerca de» muestra `1.0.0`.
2. **CU-1**: grabar 10 s de un monitor a 1080p60 con audio del sistema; parar y esperar el MP4.
3. **CU-2**: abrir la Biblioteca, comprobar miniatura, duración y reproducción del MP4.
4. **CU-3**: editar (3 clips, un recorte, una transición), undo/redo y autoguardado.
5. **CU-4**: exportar con el preset por defecto; comprobar progreso, ETA y cancelación.
6. **CU-5**: abrir el MP4 exportado en el reproductor de Windows y en otro reproductor.
7. Probar Ajustes → cambiar carpeta de grabaciones y reiniciar la app.
8. Desinstalar desde «Aplicaciones instaladas» y comprobar que no queda el ejecutable.

Criterio: todo el flujo sin abrir una consola y sin errores; anotar aquí la versión de Windows y
el resultado. **No ejecutado**: hace falta una máquina/VM limpia.

---

## A3 — RNF-06/07 con ventana real

Herramienta: `tools/rnf_measure.py` (mide con `psutil` y `EnumWindows`; la ventana es real).

```bash
# ejecutable empaquetado: arranque + reposo
uv run python tools/rnf_measure.py --exe "dist/Rec Studio/Rec Studio.exe" --idle-seconds 5

# ejecutable grabando 30 s (APPDATA/LOCALAPPDATA temporales, F9 por PostMessage)
uv run python tools/rnf_measure.py --exe "dist/Rec Studio/Rec Studio.exe" \
  --idle-seconds 5 --record-seconds 30

# app real (sin empaquetar): arranque + reposo + grabación
uv run python tools/rnf_measure.py --record-seconds 30
```

| Medición | Valor | Límite | ¿Cumple? |
|---|---:|---:|---|
| Arranque del exe (primera ejecución, frío) | 1.74 s | < 2 s | ✅ |
| Arranque del exe (caliente, 3 ejecuciones) | 1.02–1.66 s | < 2 s | ✅ |
| RSS en reposo (exe, settings por defecto) | 239–262 MB | < 600 MB | ✅ |
| RSS en reposo (exe, última sesión del usuario abierta) | 372–414 MB | < 600 MB | ✅ |
| RSS grabando 1080p60, 30 s (exe) | **337 MB** pico | < 1.2 GB | ✅ |
| RSS grabando (app real, 30 s) | 343 MB pico | < 1.2 GB | ✅ |
| fps reales durante la grabación | 60.1 | objetivo 60 | ✅ |

El arranque se mide desde `Popen` hasta que la ventana principal es visible; el primer frame puede
llegar unos milisegundos después. La grabación del exe usa un `settings.json` temporal con
`out_dir` en `%TEMP%` y `tray=false`; no toca la configuración del usuario.

---

## A4 — Instalador (Inno Setup)

| Pieza | Detalle |
|---|---|
| Script | `installer/rec-studio.iss` (Inno Setup 6.7.3, español, icono, accesos directos, desinstalador) |
| Instalación | **por usuario** (`PrivilegesRequired=lowest`, `{autopf}` → `%LOCALAPPDATA%\Programs`), sin UAC |
| Bundle | `dist/Rec Studio/` (onedir) |
| Salida | `dist/installer/RecStudio-<versión>-setup.exe` · **71 MB** |
| Verificación | `uv run python tools/check_installer.py` |

El verificador compila con `ISCC`, instala en una carpeta temporal con `/VERYSILENT`, ejecuta
`Rec Studio.exe --smoke`, comprueba el JSON y desinstala con `unins000.exe`. Resultado local:
instalado, smoke `rc=0`, desinstalado y ejecutable borrado. El CI repite el ciclo en un runner
limpio y sube el instalador como artefacto.

---

## A5 — Bundle más ligero

| Build | Tamaño | Arranque | RSS en reposo | Decisión |
|---|---:|---:|---:|---|
| Antes (OpenCV incluido) | 370 MB | — | — | referencia |
| **Sin `cv2`** | **253 MB** | 1.0–1.7 s | 261–414 MB | ✅ se queda |
| Sin `cv2` + UPX | 78 MB | **3.66 s** | **522 MB** | ❌ rompe RNF-06 |

OpenCV ocupaba **112 MB** (13 archivos; `cv2.pyd` 82 MB + `opencv_videoio_ffmpeg500_64.dll`
29 MB). `windows-capture` lo importa en su `__init__`, pero **solo** lo usa
`Frame.save_as_image()`; Rec Studio copia `frame.frame_buffer` y nunca llama a ese método.
`platform/cv2_shim.py` instala un sustituto mínimo (con `imwrite` sobre Pillow) antes de importar
`windows_capture`; si se elige el backend `dxcam`, el shim detecta el caso y da un
`CaptureError` explicable en vez de fallar dentro del hook.

Aurora 0.5 no expone `--exclude-module` (solo excluye `tkinter`), así que `tools/build_exe.py`
parchea `aurora.build.pyinstaller_args` y llama al mismo `build()` de Aurora. `tools/check_build.py`
usa ese wrapper. `upx` se probó y se descartó: el arranque de 3.66 s incumple RNF-06 y UPX suma
~107 MB de RSS; queda `upx = false` documentado en `pyproject.toml`.

---

## A6 — Export en móvil físico (pendiente)

El export de F4 ya produce H.264 High/yuv420p + AAC y `faststart` (docs/13). Falta comprobarlo en
un teléfono real:

1. Exportar un proyecto de 10–20 s con audio y transición.
2. Copiarlo al móvil (Android e iOS si se puede) y reproducirlo desde el carrete/archivos.
3. Comprobar: se ve, suena, el audio va sincronizado, se puede buscar hacia delante/atrás y no
   aparece «formato no compatible».
4. Anotar modelo, versión del sistema y reproductor.

Inspección rápida en Windows:

```bash
ffprobe -v error -show_entries stream=codec_name,profile,pix_fmt,sample_rate,channels \
  -show_entries format=format_name,duration -of default=nw=1 "export.mp4"
```

**No ejecutado**: hace falta el dispositivo físico.

---

## A7 — Soak de 30 min (pendiente)

Comando preparado (necesita escritorio y audio reales; no entra en CI):

```bash
uv run python tools/soak_test.py --minutes 30 --beep-every 60 --mic <índice>
```

Criterio de aceptación (F1): sin deriva A/V (desfase beep↔cursor estable, < 80 ms de diferencia
entre el primer y el último evento), sin drops sostenidos, pausa intermedia sin desincronía y MP4
válido al final. El script imprime un JSON con `slope_ppm`, `drift_10min_ms`, `residual_max_ms` y
`drift_ok`. **No se ejecutó en esta sesión** (se eligió dejar solo el procedimiento).

---

## A8 — «Sensación» del timeline (pendiente)

La parte automatizada ya pasa: `tools/f3_editor_test.py` y `tests/test_f3_editor.py` cubren
arrastrar clips, trim por ambos bordes, undo/redo, selección, zoom y filmstrip. Falta la
validación con ratón real:

- arrastrar un clip y un borde de trim: el borde sigue al cursor sin saltos;
- zoom con la rueda: el punto bajo el cursor se mantiene y el filmstrip no parpadea;
- snap: se engancha a clips/playhead sin pelearse con el cursor;
- `Supr`, `Ctrl+Z`/`Ctrl+Y`, `S` (split) e `I` (inspector) responden sin lag;
- con 20–30 clips, el timeline sigue fluido al hacer scroll y drag.

Criterio: uso durante 5 minutos sin sentir tirones ni clics perdidos. **No ejecutado**: es una
prueba manual de sensación.

---

## Cierre

Con A1/A3/A4/A5 cerrados, la v1 ya tiene red de seguridad (CI), medidas reales de arranque/memoria,
instalador y un bundle un 32 % más pequeño. Para dar el bloque A por terminado del todo quedan
A2, A6, A7 y A8, que necesitan máquina limpia, móvil, 30 minutos de grabación y ratón,
respectivamente; el procedimiento de cada una está arriba.
