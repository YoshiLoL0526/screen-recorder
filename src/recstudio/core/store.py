"""Undo/redo por snapshots, transacciones de drag y persistencia del proyecto (docs/04).

`ProjectStore` es puro (sin aurora): avisa de cada cambio con `on_change(project)` y la UI lo
conecta a un `Signal`. Como `Project` es `frozen`, un snapshot es solo una referencia.

    store.begin("mover clip")   # snapshot del estado ANTES
    store.update(p1); store.update(p2) ...   # el drag actualiza sin tocar el historial
    store.commit()              # una sola entrada de undo si el proyecto cambió

Persistencia: `project.json` dentro de una carpeta `.rsproj`, escritura atómica
(`project.json.tmp` → `replace`) y copia `.bak` de la versión anterior; si al abrir el JSON está
corrupto se recurre al `.bak` y se avisa.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ProjectError
from .project import SCHEMA_VERSION, Project, from_dict, to_dict

UNDO_LIMIT = 50
AUTOSAVE_INTERVAL = 30.0
PROJECT_SUFFIX = ".rsproj"
PROJECT_FILE = "project.json"


class ProjectStore:
    def __init__(
        self,
        project: Project | None = None,
        *,
        on_change: Callable[[Project], None] | None = None,
        limit: int = UNDO_LIMIT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._project = project or Project()
        self._on_change = on_change
        self._limit = limit
        self._clock = clock
        self._undo: list[tuple[str, Project]] = []
        self._redo: list[tuple[str, Project]] = []
        self._tx: tuple[str, Project] | None = None
        self._lock = threading.RLock()
        self.path: Path | None = None  # project.json
        self.read_only = False
        self.last_saved: Project | None = self._project
        self.last_save_time = clock()

    # -- estado -----------------------------------------------------------------

    @property
    def project(self) -> Project:
        return self._project

    @property
    def dirty(self) -> bool:
        return self._project != self.last_saved

    @property
    def in_transaction(self) -> bool:
        return self._tx is not None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1][0] if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1][0] if self._redo else None

    def _set(self, project: Project) -> None:
        if project == self._project:
            return
        self._project = project
        if self._on_change is not None:
            self._on_change(project)

    def _push_undo(self, label: str, before: Project) -> None:
        self._undo.append((label, before))
        del self._undo[: -self._limit]
        self._redo.clear()

    # -- comandos ---------------------------------------------------------------

    def reset(self, project: Project, path: Path | None = None, *, saved: bool = True) -> None:
        """Cambia de proyecto: historial vacío; `saved` indica si `project` ya está en disco."""
        with self._lock:
            self._undo.clear()
            self._redo.clear()
            self._tx = None
            self.path = path
            self.read_only = False
            self.last_saved = project if saved else None
            self.last_save_time = self._clock()
            self._project = project
            if self._on_change is not None:
                self._on_change(project)

    def apply(self, label: str, project: Project) -> bool:
        """Comando atómico (una entrada de undo). Dentro de una transacción equivale a `update`."""
        with self._lock:
            if self._tx is not None:
                self._set(project)
                return project != self._tx[1]
            if project == self._project:
                return False
            self._push_undo(label, self._project)
            self._set(project)
            return True

    def begin(self, label: str) -> None:
        """Abre una transacción (drag). Si había otra abierta, se confirma antes."""
        with self._lock:
            if self._tx is not None:
                self.commit()
            self._tx = (label, self._project)

    def update(self, project: Project) -> None:
        """Estado intermedio de una transacción (o cambio sin historial si no hay ninguna)."""
        with self._lock:
            self._set(project)

    def commit(self) -> bool:
        with self._lock:
            if self._tx is None:
                return False
            label, before = self._tx
            self._tx = None
            if self._project == before:
                return False
            self._push_undo(label, before)
            return True

    def cancel(self) -> None:
        """Esc durante un drag: vuelve al snapshot del `begin`."""
        with self._lock:
            if self._tx is None:
                return
            _, before = self._tx
            self._tx = None
            self._set(before)

    @property
    def transaction_start(self) -> Project | None:
        return self._tx[1] if self._tx is not None else None

    def undo(self) -> str | None:
        with self._lock:
            self.cancel()
            if not self._undo:
                return None
            label, previous = self._undo.pop()
            self._redo.append((label, self._project))
            self._set(previous)
            return label

    def redo(self) -> str | None:
        with self._lock:
            self.cancel()
            if not self._redo:
                return None
            label, following = self._redo.pop()
            self._undo.append((label, self._project))
            self._set(following)
            return label

    # -- persistencia -----------------------------------------------------------

    def autosave_due(self, interval: float = AUTOSAVE_INTERVAL) -> bool:
        with self._lock:
            return (
                self.path is not None
                and not self.read_only
                and self._tx is None
                and self.dirty
                and self._clock() - self.last_save_time >= interval
            )

    def save(self, path: Path | None = None) -> Path:
        with self._lock:
            target = path or self.path
            if target is None:
                raise ProjectError("el proyecto no tiene ruta de guardado")
            if self.read_only:
                raise ProjectError("proyecto abierto en modo lectura (es de una versión más nueva)")
            snapshot = self._project
        save_project(target, snapshot)
        with self._lock:
            self.path = target
            self.last_saved = snapshot
            self.last_save_time = self._clock()
        return target


# -- archivos -------------------------------------------------------------------


@dataclass(frozen=True)
class LoadResult:
    project: Project
    read_only: bool = False  # schema más nuevo que la app
    from_backup: bool = False  # el JSON principal estaba corrupto: se abrió el .bak
    warnings: tuple[str, ...] = ()


def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Lleva `data` al esquema actual. Devuelve `(data, read_only)`.

    Cada cambio de esquema añadirá aquí su paso `vN → vN+1`. Un archivo de una versión más
    nueva se abre en modo lectura (se interpreta lo que se entiende y no se sobrescribe).
    """
    version = int(data.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        return data, True
    # (v1 es el esquema inicial: no hay migraciones todavía)
    data = {**data, "schema_version": SCHEMA_VERSION}
    return data, False


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def save_project(path: Path, project: Project) -> None:
    """Escritura atómica con copia `.bak` de la versión anterior."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(to_dict(project), indent=2, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        try:
            os.replace(path, _backup_path(path))
        except OSError:
            pass
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProjectError("el archivo no contiene un proyecto")
    return data


def load_project(path: Path) -> LoadResult:
    path = Path(path)
    warnings: list[str] = []
    from_backup = False
    try:
        data = _read(path)
    except FileNotFoundError as exc:
        raise ProjectError(f"no existe {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, ProjectError, OSError) as exc:
        backup = _backup_path(path)
        if not backup.exists():
            raise ProjectError(f"proyecto corrupto y sin copia de seguridad: {exc}") from exc
        try:
            data = _read(backup)
        except (json.JSONDecodeError, UnicodeDecodeError, ProjectError, OSError) as exc2:
            raise ProjectError(f"proyecto y copia de seguridad corruptos: {exc2}") from exc2
        from_backup = True
        warnings.append("el proyecto estaba dañado: se abrió la copia anterior (.bak)")
    data, read_only = migrate(data)
    if read_only:
        warnings.append("el proyecto es de una versión más nueva: se abre en modo lectura")
    project = from_dict(data)
    if not read_only:
        project.validate()
    return LoadResult(project, read_only, from_backup, tuple(warnings))


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(name: str) -> str:
    """Nombre de carpeta válido en Windows a partir del nombre del proyecto."""
    cleaned = re.sub(r"\s+", " ", _UNSAFE.sub(" ", name)).strip().rstrip(". ")
    return cleaned[:80].strip() or "Proyecto"


def project_file(projects_dir: Path, name: str) -> Path:
    return Path(projects_dir) / f"{safe_name(name)}{PROJECT_SUFFIX}" / PROJECT_FILE


def unique_project_file(projects_dir: Path, name: str) -> Path:
    """Como `project_file` pero sin pisar uno existente (`nombre (2).rsproj`…)."""
    candidate = project_file(projects_dir, name)
    counter = 2
    while candidate.exists():
        candidate = project_file(projects_dir, f"{name} ({counter})")
        counter += 1
    return candidate


def list_projects(projects_dir: Path) -> list[Path]:
    """`project.json` de cada `.rsproj` de la carpeta, el modificado más recientemente primero."""
    folder = Path(projects_dir)
    if not folder.is_dir():
        return []
    found = [p / PROJECT_FILE for p in folder.glob(f"*{PROJECT_SUFFIX}") if (p / PROJECT_FILE).exists()]
    found.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return found
