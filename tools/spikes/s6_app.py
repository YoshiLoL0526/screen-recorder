"""App mínima para empaquetar con aurora build en el S6 (no es código de producto).

Ejercita en el ejecutable empaquetado: numpy, binario de FFmpeg (imageio-ffmpeg),
PyAV, Pillow, sounddevice y los imports de captura (windows-capture, dxcam, mss).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="s6_"))

    import numpy as np

    data = np.random.default_rng(0).random((1080, 1920, 4), dtype=np.float32)

    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = tmp / "gen.mp4"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", str(mp4)],
        check=True,
    )

    import av
    from PIL import Image

    container = av.open(str(mp4))
    frame = next(container.decode(container.streams.video[0]))
    arr = frame.to_ndarray(format="rgb24")
    container.close()
    jpg = tmp / "frame.jpg"
    Image.fromarray(arr).save(jpg, "JPEG", quality=80)

    import sounddevice as sd

    devices = len(sd.query_devices())

    import dxcam
    import mss
    import windows_capture

    print(
        "PACKAGING_OK"
        f" numpy={np.__version__} av={av.__version__} pillow={Image.__version__}"
        f" sd_devices={devices} mss={mss.__version__}"
        f" dxcam={dxcam is not None} wc={windows_capture is not None}"
        f" arr={data.shape} jpg={jpg.stat().st_size}B mp4={mp4.stat().st_size}B"
        f" ffmpeg={Path(exe).name}"
    )


if __name__ == "__main__":
    main()
