"""Sincronía y nivel de la mezcla sistema + micrófono en el mux de grabación.

Reproduce el fallo real de «grabar con Sistema + micrófono»: el audio salía saturado (las dos
fuentes sumadas a ganancia unidad) y desfasado (el `-itsoffset` de las pistas no sobrevivía al
`amix`). Sin hardware: los WAV se generan con numpy (un clic de 1 kHz de 20 ms en un instante
conocido), el vídeo con FFmpeg real, y después del mux se decodifica el audio a PCM para medir
dónde caen los clics (≤ 10 ms) y cuánto satura la mezcla.
"""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from recstudio.core.config import AudioConfig, RecordingConfig
from recstudio.platform import audio as audio_mod
from recstudio.platform import ffmpeg as F
from recstudio.services import recording as rec_mod

RATE = 48000
DURACION = 4.0
#: El clic de cada pista se escribe en este instante (s) de su WAV.
CLIC_EN = 1.0


@pytest.fixture(scope="module")
def exe() -> str:
    try:
        return F.ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


# -- medios sintéticos ---------------------------------------------------------

def _wav(path: Path, *, clics: tuple[float, ...] = (CLIC_EN,), canales: int = 2,
         amplitud: int = 8000, segundos: float = DURACION) -> Path:
    """WAV PCM 16 bits con un tono de 1 kHz de 20 ms en cada instante de `clics`."""
    datos = np.zeros(int(segundos * RATE))
    for instante in clics:
        inicio = int(instante * RATE)
        muestras = int(0.02 * RATE)
        onda = amplitud * np.sin(2 * math.pi * 1000.0 * np.arange(muestras) / RATE)
        datos[inicio:inicio + muestras] = onda
    pcm = np.repeat(datos.astype("<i2")[:, None], canales, axis=1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(canales)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm.tobytes())
    return path


def _wav_tono(path: Path, *, frecuencia: float, amplitud: int = 26000,
              segundos: float = DURACION) -> Path:
    t = np.arange(int(segundos * RATE)) / RATE
    onda = (amplitud * np.sin(2 * math.pi * frecuencia * t)).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(np.repeat(onda[:, None], 2, axis=1).tobytes())
    return path


def _video(path: Path, *, segundos: float = DURACION) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:size=160x90:rate=30:duration={segundos}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-f", "matroska", str(path)],
        check=True, capture_output=True,
    )
    return path


# -- medidas sobre el archivo final --------------------------------------------

def _pcm(path: Path, *, mono: bool = True) -> np.ndarray:
    filtros = ["-af", "pan=mono|c0=c0"] if mono else []
    proc = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
         *filtros, "-f", "f32le", "-ar", str(RATE), "-"],
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype="<f4")


def _clics_ms(path: Path, *, umbral: float = 0.05) -> list[float]:
    """Instantes (ms) en los que empieza cada clic del audio ya mezclado."""
    muestras = _pcm(path)
    alto = np.abs(muestras) > umbral
    instantes: list[float] = []
    i = 0
    while i < len(alto):
        if alto[i]:
            j = i
            while j < len(alto) and alto[j:j + 240].any():  # 5 ms de margen entre clics
                j += 240
            instantes.append(round(i / (RATE / 1000), 1))
            i = j
        else:
            i += 1
    return instantes


def _pico(path: Path) -> float:
    muestras = _pcm(path, mono=False)
    return float(np.abs(muestras).max()) if len(muestras) else 0.0


def _mux(exe: str, video: Path, tracks: list[F.MuxTrack], out: Path) -> Path:
    cmd = F.mux_cmd(exe, video=video, tracks=tracks, out_path=out, duration_s=DURACION)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-800:]
    return out


def _cerca(instantes: list[float], esperados: list[float], *, tolerancia_ms: float = 10.0) -> None:
    """Los clics medidos coinciden con los esperados (± tolerancia), uno a uno."""
    assert len(instantes) == len(esperados), f"clics {instantes} != esperados {esperados}"
    for medido, esperado in zip(instantes, esperados):
        assert abs(medido - esperado) <= tolerancia_ms, f"{medido} ms != {esperado} ms"


# -- alineación de la mezcla ---------------------------------------------------

def test_mezcla_alinea_las_dos_pistas_con_desfases_de_signo_distinto(
    exe: str, tmp_path: Path
) -> None:
    """Sistema retrasado y micrófono adelantado: cada clic queda en su instante real."""
    video = _video(tmp_path / "v.mkv")
    sistema = _wav(tmp_path / "sys.wav", canales=2)
    micro = _wav(tmp_path / "mic.wav", canales=1)
    salida = _mux(
        exe, video,
        [F.MuxTrack(sistema, 0.10), F.MuxTrack(micro, -0.20)],
        tmp_path / "o.mp4",
    )
    # sistema: 1.0 + 0.10; micrófono: 1.0 − 0.20
    _cerca(_clics_ms(salida), [800.0, 1100.0])


def test_mezcla_alinea_cuando_las_dos_pistas_se_retrasan(exe: str, tmp_path: Path) -> None:
    """Dos desfases positivos: el `amix` no puede colapsar las dos pistas en el mismo instante."""
    video = _video(tmp_path / "v.mkv")
    sistema = _wav(tmp_path / "sys.wav", canales=2)
    micro = _wav(tmp_path / "mic.wav", canales=1)
    salida = _mux(
        exe, video,
        [F.MuxTrack(sistema, 0.20), F.MuxTrack(micro, 0.40)],
        tmp_path / "o.mp4",
    )
    _cerca(_clics_ms(salida), [1200.0, 1400.0])


def test_mezcla_conserva_los_desfases_negativos(exe: str, tmp_path: Path) -> None:
    """El camino `atrim` (audio que arrancó antes que el vídeo) sigue alineado."""
    video = _video(tmp_path / "v.mkv")
    sistema = _wav(tmp_path / "sys.wav", canales=2)
    micro = _wav(tmp_path / "mic.wav", canales=1)
    salida = _mux(
        exe, video,
        [F.MuxTrack(sistema, -0.30), F.MuxTrack(micro, -0.10)],
        tmp_path / "o.mp4",
    )
    _cerca(_clics_ms(salida), [700.0, 900.0])


def test_mezcla_de_dos_pistas_fuertes_no_satura(exe: str, tmp_path: Path) -> None:
    """Dos fuentes a −0.6 dBFS sumadas a ganancia unidad recortaban (picos ~+6 dB)."""
    video = _video(tmp_path / "v.mkv")
    tono1 = _wav_tono(tmp_path / "t1.wav", frecuencia=440.0)
    tono2 = _wav_tono(tmp_path / "t2.wav", frecuencia=660.0)
    salida = _mux(exe, video, [F.MuxTrack(tono1), F.MuxTrack(tono2)], tmp_path / "o.mp4")
    pico = _pico(salida)
    assert pico <= 0.99, f"la mezcla satura: pico {pico:.3f} (0 dBFS = 1.0)"


def test_deriva_corregida_y_retardo_conviven(exe: str, tmp_path: Path) -> None:
    """`asetrate`/`aresample` se aplican por pista y antes del retardo, con la tasa correcta."""
    video = _video(tmp_path / "v.mkv")
    # Un reloj 5000 ppm rápido: a 3 s de fuente el audio se adelanta 15 ms al corregirlo.
    fuente = _wav(tmp_path / "a.wav", clics=(1.0, 3.0), canales=2)
    salida = _mux(
        exe, video, [F.MuxTrack(fuente, 0.10, rate=RATE, drift_ppm=5000.0)], tmp_path / "o.mp4"
    )
    instantes = _clics_ms(salida)
    _cerca(instantes, [1095.0, 3085.0], tolerancia_ms=8.0)
    assert instantes[1] < 3090.0, "la deriva no se corrigió: el clic sigue en su sitio nominal"


# -- sesión completa: offset por pista y modo «pistas separadas» ---------------

def _sesion(tmp_path: Path, *, modo: str) -> rec_mod.RecordingSession:
    config = RecordingConfig(fps=30, audio=AudioConfig(system=True, mic_device_index=4, mode=modo))
    sesion = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    sesion._backend_name = "fake"
    sesion._encoder = "libx264"
    sesion._size = (160, 90)
    sesion._mic_device = "Mic de prueba"
    _video(sesion._mkv)
    return sesion


def _pista(path: Path) -> audio_mod.AudioTrack:
    with wave.open(str(path), "rb") as wav:
        rate, canales, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
    return audio_mod.AudioTrack(
        path=path, device=path.stem, rate=rate, channels=canales, frames=frames,
        first_callback_t=None,
    )


def test_la_sesion_aplica_el_desfase_de_cada_pista_al_mezclar(tmp_path: Path) -> None:
    """De punta a punta: `_mux` recibe un offset por pista y la mezcla los respeta."""
    sesion = _sesion(tmp_path, modo="mix")
    sistema = _pista(_wav(sesion.temp_dir / "audio_system.wav", canales=2))
    micro = _pista(_wav(sesion.temp_dir / "audio_mic.wav", canales=1))
    offsets = {"audio_system.wav": 0.10, "audio_mic.wav": -0.20}

    resultado = sesion._mux([sistema, micro], offsets, recover=False)

    _cerca(_clics_ms(resultado.mp4), [800.0, 1100.0])
    assert F.probe_media(resultado.mp4).audio_codec == "aac"


def test_modo_separado_deja_el_microfono_igual_de_alineado(tmp_path: Path) -> None:
    """El WAV del micrófono, con su `offset_s`, cae en el mismo instante que en la mezcla."""
    sesion = _sesion(tmp_path, modo="separate")
    sistema = _pista(_wav(sesion.temp_dir / "audio_system.wav", canales=2))
    micro = _pista(_wav(sesion.temp_dir / "audio_mic.wav", canales=1))
    offsets = {"audio_system.wav": 0.10, "audio_mic.wav": -0.20}

    resultado = sesion._mux([sistema, micro], offsets, recover=False)

    # El MP4 lleva solo el sistema: su clic queda en 1.0 + 0.10.
    _cerca(_clics_ms(resultado.mp4), [1100.0])
    # El WAV hermano se copia tal cual (clic en 1.0) y su offset lo coloca en 1.0 − 0.20.
    assert resultado.mic is not None
    assert resultado.mic_offset_s == pytest.approx(-0.20)
    clic_wav = _clics_ms_wav(resultado.mic)
    assert abs(clic_wav[0] - CLIC_EN * 1000) <= 10.0
    assert abs(clic_wav[0] + resultado.mic_offset_s * 1000 - 800.0) <= 10.0


# -- desfase medido y deriva --------------------------------------------------

def test_el_desfase_de_cada_pista_se_mide_contra_el_arranque_del_video(tmp_path: Path) -> None:
    """Las dos pistas comparten referencia (`t0_video`); negativo = arrancó antes."""
    sesion = _sesion(tmp_path, modo="mix")
    sesion._t0_video = 100.0

    def pista(nombre: str, callback: float) -> audio_mod.AudioTrack:
        return audio_mod.AudioTrack(
            path=sesion.temp_dir / nombre, device=nombre, rate=RATE, channels=2,
            frames=RATE, first_callback_t=callback,
        )

    sistema = pista("audio_system.wav", 99.80)
    micro = pista("audio_mic.wav", 99.65)
    assert sesion._audio_offset(sistema) == pytest.approx(-0.20, abs=1e-6)
    assert sesion._audio_offset(micro) == pytest.approx(-0.35, abs=1e-6)
    # Lo que se guarda y lo que llega al mux es el desfase de cada pista, no un cero ni el ajeno.
    sesion._recorders = [sistema, micro]  # type: ignore[list-item]
    assert sesion._current_offsets() == {
        "audio_system.wav": -0.2, "audio_mic.wav": -0.35,
    }


def test_deriva_medida_por_regresion_tiene_el_signo_del_reloj(tmp_path: Path) -> None:
    """Un reloj rápido da ppm > 0 (el mux acelera la pista) y uno lento, ppm < 0."""
    def deriva(ppm: float) -> float:
        recorder = audio_mod.AudioRecorder(
            audio_mod.AudioDevice(index=1, name="Falso", rate=RATE, channels=2, kind="input"),
            tmp_path / "deriva.wav",
        )
        recorder.first_callback_t = 0.0
        recorder.last_callback_t = 120.0
        recorder._samples = [
            (t, int(t * RATE * (1.0 + ppm / 1e6))) for t in (10.0 * i for i in range(1, 12))
        ]
        return recorder._measure_drift()

    assert deriva(1000.0) == pytest.approx(1000.0, abs=5.0)
    assert deriva(-900.0) == pytest.approx(-900.0, abs=5.0)


def _clics_ms_wav(path: Path, *, umbral: int = 1500) -> list[float]:
    """Instantes (ms) de los clics de un WAV PCM 16 bits, sin pasar por FFmpeg."""
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        datos = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        canales = wav.getnchannels()
    mono = np.abs(datos.reshape(-1, canales)).max(axis=1)
    alto = mono > umbral
    instantes: list[float] = []
    i = 0
    while i < len(alto):
        if alto[i]:
            j = i
            while j < len(alto) and alto[j:j + 240].any():
                j += 240
            instantes.append(round(i / (rate / 1000), 1))
            i = j
        else:
            i += 1
    return instantes
