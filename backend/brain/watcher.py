"""Surveillance temps réel du second cerveau via watchdog avec debounce de 500 ms."""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from backend import config
from backend.brain import indexer
from backend.brain.paths import id_depuis_chemin
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

_DEBOUNCE_S = 0.5


class _BrainEventHandler(FileSystemEventHandler):
    """Gestionnaire d'événements fichier accumulant les changements pendant 500 ms."""

    def __init__(self, brain_dir: Path, delai_s: float = _DEBOUNCE_S) -> None:
        """Initialise le handler avec la racine du coffre et le délai de debounce."""
        super().__init__()
        self._brain_dir = brain_dir
        self._delai_s = delai_s
        self._lock = threading.Lock()
        self._modifies: set[Path] = set()
        self._supprimes: set[Path] = set()
        self._reindex_complet = False
        self._timer: threading.Timer | None = None

    def _planifier(self) -> None:
        """Réarme le minuteur de debounce de 500 ms sous verrou."""
        if self._timer is not None:
            self._timer.cancel()
        timer = threading.Timer(self._delai_s, self._executer_flush)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def arreter_timer(self) -> None:
        """Annule le minuteur en attente lors de l'arrêt du watcher."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _est_pertinent(self, chemin: Path) -> bool:
        """Vérifie qu'un chemin ``.md`` ne correspond pas à un fichier temporaire ignoré."""
        nom = chemin.name
        if not nom or nom.startswith(("~", ".")) or nom.endswith((".tmp", ".swp", "~")):
            return False
        if chemin.suffix.lower() != ".md":
            return False
        for part in chemin.parts:
            if part in (".obsidian", ".trash", ".git") or (part.startswith(".") and len(part) > 1):
                return False
        return True

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Filtre les événements Watchdog et planifie leur traitement groupé."""
        if event.event_type in ("opened", "closed"):
            return

        src = Path(str(event.src_path))
        if event.is_directory:
            if event.event_type in ("deleted", "moved") and src.name not in (".obsidian", ".trash", ".git"):
                with self._lock:
                    self._reindex_complet = True
                    self._planifier()
            return

        with self._lock:
            if event.event_type == "moved" and hasattr(event, "dest_path"):
                dest = Path(str(event.dest_path))
                if self._est_pertinent(src):
                    self._supprimes.add(src)
                    self._modifies.discard(src)
                if self._est_pertinent(dest):
                    self._modifies.add(dest)
                    self._supprimes.discard(dest)
                if self._est_pertinent(src) or self._est_pertinent(dest):
                    self._planifier()
                return

            if not self._est_pertinent(src):
                return

            if event.event_type == "deleted":
                self._supprimes.add(src)
                self._modifies.discard(src)
            elif event.event_type in ("created", "modified"):
                self._modifies.add(src)
                self._supprimes.discard(src)
            else:
                return
            self._planifier()

    def _executer_flush(self) -> None:
        """Applique les mises à jour d'index en attente puis diffuse ``cerveau_maj``."""
        with self._lock:
            reindex_complet = self._reindex_complet
            modifies = list(self._modifies)
            supprimes = list(self._supprimes)
            self._reindex_complet = False
            self._modifies.clear()
            self._supprimes.clear()
            self._timer = None

        a_change = False
        try:
            if reindex_complet:
                indexer.reindexer_tout()
                a_change = True
            else:
                for chemin in supprimes:
                    try:
                        note_id = id_depuis_chemin(chemin, brain_dir=self._brain_dir)
                        indexer.retirer(note_id)
                        a_change = True
                    except ValueError:
                        pass
                for chemin in modifies:
                    if chemin.is_file():
                        if indexer.indexer_fichier(chemin):
                            a_change = True
                    else:
                        try:
                            note_id = id_depuis_chemin(chemin, brain_dir=self._brain_dir)
                            indexer.retirer(note_id)
                            a_change = True
                        except ValueError:
                            pass
        except Exception as exc:
            logger.warning("Erreur lors de la mise à jour incrémentale du cerveau : %s", exc)

        if a_change:
            hub.broadcast_threadsafe("cerveau_maj", {})


class BrainWatcher:
    """Cycle de vie du watcher Watchdog sur ``config.BRAIN_DIR``."""

    def __init__(self) -> None:
        """Initialise un watcher arrêté."""
        self._lock = threading.Lock()
        self._observer: object | None = None
        self._handler: _BrainEventHandler | None = None

    def est_actif(self) -> bool:
        """Indique si l'observateur Watchdog est en cours d'exécution."""
        with self._lock:
            return self._observer is not None and bool(getattr(self._observer, "is_alive", lambda: False)())

    def demarrer(self) -> None:
        """Démarre la surveillance récursive de ``BRAIN_DIR`` (redémarre si déjà actif)."""
        self.arreter()
        brain_dir = config.BRAIN_DIR
        brain_dir.mkdir(parents=True, exist_ok=True)

        handler = _BrainEventHandler(brain_dir=brain_dir)
        observer = Observer()
        observer.schedule(handler, str(brain_dir), recursive=True)
        observer.start()

        with self._lock:
            self._handler = handler
            self._observer = observer
        logger.info("Watcher du second cerveau démarré sur %s", brain_dir)

    def arreter(self) -> None:
        """Arrête proprement l'observateur Watchdog et annule tout debounce en attente."""
        with self._lock:
            handler = self._handler
            observer = self._observer
            self._handler = None
            self._observer = None

        if handler is not None:
            handler.arreter_timer()
        if observer is not None:
            try:
                observer.stop()  # type: ignore[attr-defined]
                observer.join(timeout=2.0)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("Erreur à l'arrêt du watcher cerveau : %s", exc)


_watcher = BrainWatcher()


def demarrer() -> None:
    """Démarre le watcher global du second cerveau."""
    _watcher.demarrer()


def arreter() -> None:
    """Arrête le watcher global du second cerveau."""
    _watcher.arreter()


def est_actif() -> bool:
    """Retourne ``True`` si le watcher global est actif."""
    return _watcher.est_actif()
