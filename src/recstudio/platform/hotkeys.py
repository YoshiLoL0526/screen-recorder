"""Atajos globales de teclado (`pynput`): la app responde aunque no tenga el foco.

Es un *hook* de teclado a nivel de sistema, así que vive en `platform/` y es **opcional**
(Ajustes → «Atajos globales»): si `pynput` no está instalado o el hook falla (permisos,
antivirus, sin escritorio), `start()` devuelve `False`, la app sigue con los atajos de la ventana
y el controlador avisa y deshace el ajuste.

`pynput` entrega las pulsaciones en su propio hilo: `on_trigger` se llama desde ahí, así que debe
ser seguro desde otro hilo (los handlers del motor toman su `Lock` y publican por `Signal`).

Los tests inyectan un `backend` falso; nunca se instala un hook de verdad.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol


class _Listener(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class _Backend(Protocol):
    def GlobalHotKeys(self, mapping: Mapping[str, Callable[[], Any]]) -> _Listener: ...  # noqa: N802


#: Atajos que ofrece la app, en sintaxis de `pynput` (los mismos de la ventana, docs/05).
BINDINGS: dict[str, str] = {"record": "<f9>", "pause": "<f10>"}


def load_backend() -> _Backend:
    """Backend real: `pynput.keyboard`. Import perezoso (es opcional)."""
    from pynput import keyboard  # noqa: PLC0415

    return keyboard


class GlobalHotkeys:
    """Instala y retira el hook; nunca lanza hacia el llamador."""

    def __init__(
        self,
        bindings: Mapping[str, str],
        on_trigger: Callable[[str], None],
        *,
        backend: _Backend | None = None,
    ) -> None:
        self._bindings = dict(bindings)
        self._on_trigger = on_trigger
        self._backend = backend
        self._listener: _Listener | None = None

    @property
    def running(self) -> bool:
        return self._listener is not None

    def _fire(self, name: str) -> None:
        try:
            self._on_trigger(name)
        except Exception:  # un handler que falle no puede tumbar el hilo del hook
            pass

    def start(self) -> bool:
        """Instala el hook. `False` si no se pudo (sin pynput, sin escritorio o sin permisos)."""
        if self._listener is not None:
            return True
        try:
            backend = self._backend or load_backend()
            mapping = {
                combo: (lambda name=name: self._fire(name))
                for name, combo in self._bindings.items()
            }
            listener = backend.GlobalHotKeys(mapping)
            if hasattr(listener, "daemon"):  # no dejar el proceso colgado al salir
                listener.daemon = True
            listener.start()
        except Exception:
            self._listener = None
            return False
        self._listener = listener
        return True

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass
