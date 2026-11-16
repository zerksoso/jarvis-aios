"""Application sécurisée des mises à jour légères (zip-slip) et complètes (Authenticode)."""

import os
import shutil
import signal
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from backend import config
from backend.update.downloader import ErreurInstallation

TAILLE_MAX_DECOMP_OCTETS: int = 1_500 * 1024 * 1024  # 1,5 Go
_TIMEOUT_AUTHENTISIGN_S: float = 15.0

OPTIONS_SETUP_SILENCIEUX: tuple[str, ...] = (
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/RESTARTAPPLICATIONS",
)


def _valider_membre_zip(info: zipfile.ZipInfo, base_resolue: Path) -> Path:
    """Valide qu'une entrée d'archive zip ne sort pas de ``base_resolue`` (protection zip-slip)."""
    nom_brut = info.filename
    if not nom_brut or "\x00" in nom_brut:
        raise ErreurInstallation(f"Chemin zip invalide : {nom_brut!r}")

    # Refuse les liens symboliques embarqués dans les archives POSIX.
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ErreurInstallation(f"Lien symbolique interdit dans l'archive : {nom_brut}")

    normalise = nom_brut.replace("\\", "/")
    lecteur, _ = os.path.splitdrive(nom_brut)
    if lecteur or normalise.startswith("/") or ":" in normalise:
        raise ErreurInstallation(f"Chemin absolu interdit dans l'archive (zip-slip) : {nom_brut}")

    posix = PurePosixPath(normalise)
    if ".." in posix.parts:
        raise ErreurInstallation(f"Traversée de répertoire interdite (zip-slip) : {nom_brut}")

    cible = (base_resolue / posix).resolve()
    if not cible.is_relative_to(base_resolue):
        raise ErreurInstallation(f"Sortie hors dossier de staging détectée (zip-slip) : {nom_brut}")
    return cible


def extraire_zip_securise(zip_path: Path, staging_dir: Path) -> Path:
    """Extrait une archive zip applicative vers ``staging_dir`` avec protection anti zip-slip.

    Supprime tout ``staging_dir`` préalable afin de garantir une extraction propre.

    Args:
        zip_path: Chemin du fichier ``JarvisAIOS-app-X.Y.Z.zip``.
        staging_dir: Dossier cible (typiquement ``APP_DIR/_maj_staging``).

    Returns:
        Chemin ``staging_dir`` rempli.

    Raises:
        ErreurInstallation: Si l'archive est corrompue, dépasse la taille décompressée maximale
            ou contient un chemin malveillant (zip-slip / lien symbolique).

    Effets de bord:
        Recrée ``staging_dir`` et écrit les fichiers extraits sur disque ; nettoie en cas d'erreur.
    """
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    base_resolue = staging_dir.resolve()

    total_octets = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                cible = _valider_membre_zip(info, base_resolue)
                if info.is_dir():
                    cible.mkdir(parents=True, exist_ok=True)
                    continue

                if cible == base_resolue:
                    raise ErreurInstallation("Entrée fichier invalide visant la racine.")

                total_octets += max(0, info.file_size)
                if total_octets > TAILLE_MAX_DECOMP_OCTETS:
                    raise ErreurInstallation("Archive trop volumineuse après décompression.")

                cible.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, cible.open("wb") as dst:
                    while True:
                        bloc = src.read(65_536)
                        if not bloc:
                            break
                        dst.write(bloc)
        return staging_dir
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ErreurInstallation(f"Archive zip corrompue : {exc}") from exc
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def verifier_signature_authenticode(chemin_exe: Path) -> None:
    """Vérifie la signature Authenticode d'un exécutable Setup via PowerShell.

    Si l'exécutable n'est pas signé (``NotSigned``), la vérification passe conformément
    au contrat (§5 et §8). S'il porte une signature, son statut doit être ``Valid``.

    Args:
        chemin_exe: Chemin complet vers ``JarvisAIOS-Setup-X.Y.Z.exe``.

    Raises:
        ErreurInstallation: Si une signature est présente mais invalide ou non approuvée.
    """
    if not chemin_exe.is_file():
        raise ErreurInstallation(f"Installeur introuvable : {chemin_exe}")

    env = dict(os.environ)
    env["JARVIS_SETUP_PATH"] = str(chemin_exe)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "(Get-AuthenticodeSignature -LiteralPath $env:JARVIS_SETUP_PATH).Status.ToString()",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise ErreurInstallation(
            f"Impossible de vérifier la signature Authenticode via PowerShell : {exc}"
        ) from exc

    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_AUTHENTISIGN_S)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise ErreurInstallation("Expiration du délai lors du contrôle Authenticode.") from exc

    if proc.returncode != 0:
        raise ErreurInstallation(
            f"Échec de contrôle Authenticode : {(stderr or stdout).strip() or proc.returncode}"
        )

    statut = (stdout or "").strip()
    if statut in ("Valid", "NotSigned"):
        return
    raise ErreurInstallation(f"Signature Authenticode invalide : {statut or 'inconnue'}")


def arreter_serveur_propre() -> None:
    """Déclenche l'arrêt propre du serveur courant via ``SIGINT`` (lifecycle FastAPI/Uvicorn)."""
    signal.raise_signal(signal.SIGINT)


def _lancer_detache(cmd: list[str], cwd: Path) -> None:
    """Lance un exécutable détaché sans fenêtre ni héritage de descripteurs."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise ErreurInstallation(f"Impossible de lancer le processus détaché {cmd[0]} : {exc}") from exc


def appliquer_maj_legere(
    zip_path: Path,
    app_dir: Path | None = None,
    arreter_serveur: Callable[[], None] | None = None,
) -> Path:
    """Extrait la mise à jour légère dans ``APP_DIR/_maj_staging``, lance ``Jarvis.exe --appliquer-maj`` et arrête le serveur.

    Args:
        zip_path: Archive ``JarvisAIOS-app-X.Y.Z.zip`` déjà vérifiée par SHA-256.
        app_dir: Dossier programme cible ; par défaut ``config.APP_DIR``.
        arreter_serveur: Callback optionnel pour arrêter le serveur courant (tests).

    Returns:
        Chemin du dossier ``_maj_staging`` préparé.

    Raises:
        ErreurInstallation: Si l'archive est invalide ou ``Jarvis.exe`` absent.

    Effets de bord:
        Extrait dans ``APP_DIR/_maj_staging``, démarre ``Jarvis.exe --appliquer-maj`` en
        processus détaché, puis invoque ``arreter_serveur``.
    """
    base_app = app_dir if app_dir is not None else config.APP_DIR
    staging_dir = base_app / "_maj_staging"
    extraire_zip_securise(zip_path, staging_dir)

    launcher = base_app / "Jarvis.exe"
    if not launcher.is_file():
        raise ErreurInstallation(f"Lanceur introuvable pour appliquer la mise à jour : {launcher}")

    _lancer_detache([str(launcher), "--appliquer-maj"], cwd=base_app)
    stopper = arreter_serveur if arreter_serveur is not None else arreter_serveur_propre
    stopper()
    return staging_dir


def appliquer_maj_complete(setup_exe: Path) -> None:
    """Vérifie la signature Authenticode du Setup puis le lance en mode silencieux détaché.

    Args:
        setup_exe: Installeur ``JarvisAIOS-Setup-X.Y.Z.exe`` déjà vérifié par SHA-256.

    Raises:
        ErreurInstallation: Si la signature est invalide ou le lancement échoue.

    Effets de bord:
        Démarre l'installeur Inno Setup détaché avec les options silencieuses du contrat.
    """
    verifier_signature_authenticode(setup_exe)
    _lancer_detache([str(setup_exe), *OPTIONS_SETUP_SILENCIEUX], cwd=setup_exe.parent)
