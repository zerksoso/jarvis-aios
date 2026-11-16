"""Planificateur de routines AIOS (APScheduler 3 AsyncIOScheduler + rattrapage)."""

import asyncio
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import frontmatter
import yaml
from apscheduler.schedulers import SchedulerNotRunningError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from send2trash import send2trash

from backend import config, db
from backend.aios import skills_engine
from backend.brain import indexer
from backend.brain.parser import BRANCHES_IDS, normaliser_texte
from backend.brain.paths import chemin_sur, normaliser_id_note
from backend.llm import router as llm_router
from backend.llm.base import LLMRequest
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

TIMEZONE_PARIS = "Europe/Paris"
_TZ = ZoneInfo(TIMEZONE_PARIS)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TACHE_CASE_RE = re.compile(r"^\s*-\s*\[\s*\]\s+(.+?)\s*$")
_TITRE_SECTION_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")

_scheduler: AsyncIOScheduler | None = None
_taches_actives: set[asyncio.Task[Any]] = set()


def valider_id_routine(routine_id: str) -> str:
    """Valide qu'un identifiant de routine est un slug simple sûr sans chemin.

    Args:
        routine_id: Identifiant de routine brut.

    Returns:
        Identifiant nettoyé en minuscules.

    Raises:
        ValueError: Si l'identifiant est vide ou contient des caractères interdits.
    """
    if not isinstance(routine_id, str) or not routine_id.strip():
        raise ValueError("Identifiant de routine vide.")
    propre = routine_id.strip().lower()
    if not _SLUG_RE.match(propre):
        raise ValueError(f"Identifiant de routine invalide : {routine_id!r}")
    return propre


def _chemin_routine(routine_id: str) -> Path:
    """Résout le chemin ``ROUTINES_DIR/<id>.md`` de façon confinée."""
    slug = valider_id_routine(routine_id)
    base = config.ROUTINES_DIR.resolve()
    cible = (base / f"{slug}.md").resolve()
    if not cible.is_relative_to(base) or cible.parent != base:
        raise ValueError("Chemin de routine hors de ROUTINES_DIR.")
    return cible


def prochaine_execution(cron: str) -> str | None:
    """Calcule le prochain déclenchement ISO 8601 (fuseau ``Europe/Paris``) d'une expression cron.

    Args:
        cron: Expression crontab standard à 5 champs.

    Returns:
        Horodatage ISO 8601 local, ou ``None`` si l'expression cron est invalide.
    """
    try:
        trigger = CronTrigger.from_crontab(cron.strip(), timezone=_TZ)
        suivant = trigger.get_next_fire_time(None, datetime.now(_TZ))
        return suivant.isoformat(timespec="seconds") if suivant is not None else None
    except Exception:
        return None


def _dernier_run(routine_id: str) -> dict[str, Any] | None:
    """Lit la dernière exécution enregistrée dans ``runs_routines`` pour une routine."""
    return db.query_one(
        """
        SELECT id, routine_id, debut, fin, statut, sortie_path, resume, erreur
        FROM runs_routines
        WHERE routine_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (routine_id,),
    )


def _parser_fichier_routine(chemin: Path) -> dict[str, Any]:
    """Lit et normalise un fichier Markdown de routine."""
    brut = chemin.read_text(encoding="utf-8", errors="replace")
    meta, contenu = frontmatter.parse(brut)

    routine_id = str(meta.get("id") or chemin.stem).strip().lower()
    cron = str(meta.get("cron") or "0 6 * * *").strip()
    actif = bool(meta.get("actif", True))

    return {
        "id": routine_id,
        "nom": str(meta.get("nom") or routine_id).strip(),
        "cron": cron,
        "agent": str(meta.get("agent") or "jarvis").strip() or "jarvis",
        "skill": str(meta["skill"]).strip() if meta.get("skill") else None,
        "sortie": str(meta.get("sortie") or f"04-Analytics-Resultats/{{date}}-{routine_id}.md").strip(),
        "actif": actif,
        "description": str(meta.get("description") or "").strip(),
        "consigne": contenu.strip(),
        "prochaine_execution": prochaine_execution(cron) if actif else None,
        "dernier_run": _dernier_run(routine_id),
    }


def lister_routines() -> list[dict[str, Any]]:
    """Liste toutes les routines définies dans ``aios/routines/*.md``.

    Returns:
        Liste triée de routines enrichies avec ``prochaine_execution`` et ``dernier_run``.
    """
    routines_dir = config.ROUTINES_DIR
    if not routines_dir.exists():
        return []

    resultat: list[dict[str, Any]] = []
    for fichier in sorted(routines_dir.glob("*.md")):
        if not fichier.is_file() or fichier.name.lower() == "readme.md":
            continue
        try:
            resultat.append(_parser_fichier_routine(fichier))
        except Exception as exc:
            logger.warning("Routine illisible %s : %s", fichier, exc)
    return resultat


def charger_routine(routine_id: str) -> dict[str, Any] | None:
    """Charge une routine par son identifiant.

    Args:
        routine_id: Identifiant slug de la routine.

    Returns:
        Dictionnaire complet de la routine, ou ``None`` si introuvable.
    """
    chemin = _chemin_routine(routine_id)
    if not chemin.is_file():
        return None
    return _parser_fichier_routine(chemin)


def enregistrer_routine(donnees: dict[str, Any], ecraser: bool = False) -> dict[str, Any]:
    """Valide et écrit une routine ``aios/routines/<id>.md``, puis recharge le planificateur.

    Args:
        donnees: Champs de la routine (``id``, ``nom``, ``cron``, ``agent``, ``sortie``, etc.).
        ecraser: Autorise la mise à jour d'un fichier existant.

    Returns:
        Dictionnaire de la routine enregistrée.

    Raises:
        ValueError: Si l'identifiant ou l'expression cron est invalide.
        FileExistsError: Si la routine existe déjà et ``ecraser`` est ``False``.
    """
    routine_id = valider_id_routine(str(donnees.get("id") or ""))
    cron = str(donnees.get("cron") or "").strip()
    if not cron:
        raise ValueError("L'expression cron est obligatoire.")
    # Valide la syntaxe crontab avant toute écriture sur disque.
    CronTrigger.from_crontab(cron, timezone=_TZ)

    chemin = _chemin_routine(routine_id)
    if chemin.exists() and not ecraser:
        raise FileExistsError(f"La routine '{routine_id}' existe déjà.")

    meta: dict[str, Any] = {
        "id": routine_id,
        "nom": str(donnees.get("nom") or routine_id).strip(),
        "cron": cron,
        "agent": str(donnees.get("agent") or "jarvis").strip() or "jarvis",
        "skill": str(donnees["skill"]).strip() if donnees.get("skill") else None,
        "sortie": str(
            donnees.get("sortie") or f"04-Analytics-Resultats/{{date}}-{routine_id}.md"
        ).strip(),
        "actif": bool(donnees.get("actif", True)),
        "description": str(donnees.get("description") or "").strip(),
    }
    consigne = str(donnees.get("consigne") or "").strip()
    yaml_txt = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(f"---\n{yaml_txt}\n---\n\n{consigne}\n", encoding="utf-8")

    recharger()
    return charger_routine(routine_id) or meta


def supprimer_routine(routine_id: str) -> bool:
    """Envoie le fichier d'une routine à la corbeille système et recharge le planificateur.

    Args:
        routine_id: Identifiant slug de la routine.

    Returns:
        ``True`` si le fichier existait et a été envoyé à la corbeille.
    """
    chemin = _chemin_routine(routine_id)
    if not chemin.is_file():
        return False
    send2trash(str(chemin))
    recharger()
    return True


def _extraire_taches_brief(contenu_md: str) -> list[str]:
    """Extrait les tâches ``- [ ] ...`` de la section ``## Tâches du jour``."""
    taches: list[str] = []
    dans_section = False

    for ligne in contenu_md.splitlines():
        titre_match = _TITRE_SECTION_RE.match(ligne)
        if titre_match:
            titre_norm = normaliser_texte(titre_match.group(1))
            if "taches du jour" in titre_norm:
                dans_section = True
                continue
            if dans_section:
                break

        if not dans_section:
            continue

        case_match = _TACHE_CASE_RE.match(ligne)
        if case_match:
            titre = case_match.group(1).strip()
            if titre:
                taches.append(titre)

    return taches


def _inserer_taches_du_jour(taches: list[str], jour: str) -> int:
    """Insère les tâches extraites du brief du matin sans créer de doublon pour le jour."""
    if not taches:
        return 0

    existantes = db.query("SELECT titre FROM taches WHERE date = ?", (jour,))
    titres_existants = {normaliser_texte(str(row["titre"])) for row in existantes}

    ajoutees = 0
    for titre in taches:
        cle = normaliser_texte(titre)
        if cle in titres_existants:
            continue
        db.execute(
            "INSERT INTO taches (titre, fait, date, source, ordre) VALUES (?, 0, ?, 'brief', 0)",
            (titre, jour),
        )
        titres_existants.add(cle)
        ajoutees += 1
    return ajoutees


def _formater_sortie_markdown(
    texte: str,
    routine: dict[str, Any],
    sortie_rel: str,
    jour: str,
) -> str:
    """Garantit un frontmatter YAML valide sur la note produite par une routine."""
    stripped = texte.lstrip()
    if stripped.startswith("---"):
        return texte

    dossier = sortie_rel.split("/", 1)[0] if "/" in sortie_rel else ""
    branche = dossier if dossier in BRANCHES_IDS else "autres"
    meta = {
        "titre": f"{routine['nom']} ({jour})",
        "tags": ["routine", routine["id"]],
        "branche": branche,
        "statut": "actif",
        "date": jour,
    }
    yaml_txt = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_txt}\n---\n\n{texte.strip()}\n"


async def lancer_routine(routine_id: str) -> int:
    """Exécute immédiatement une routine, écrit sa sortie dans le cerveau et trace le run.

    Effectue un import tardif de ``backend.agents.agent_runtime.executer_tache`` et, si le
    module agents n'est pas encore installé, appelle directement ``llm_router.generer``.

    Args:
        routine_id: Identifiant de la routine à lancer.

    Returns:
        Identifiant ``run_id`` créé dans ``runs_routines``.

    Raises:
        FileNotFoundError: Si la routine n'existe pas.
    """
    routine = charger_routine(routine_id)
    if routine is None:
        raise FileNotFoundError(f"Routine introuvable : {routine_id}")

    debut = db.maintenant_iso()
    run_id = await db.async_execute(
        "INSERT INTO runs_routines (routine_id, debut, statut) VALUES (?, ?, 'en_cours')",
        (routine["id"], debut),
    )
    await hub.broadcast(
        "routine_etat",
        {"id": routine["id"], "statut": "en_cours", "run_id": run_id},
    )

    try:
        try:
            from backend.agents.agent_runtime import executer_tache  # type: ignore[import-not-found]
        except ImportError:
            executer_tache = None

        consigne = routine["consigne"] or routine["description"] or f"Exécute la routine {routine['nom']}."
        skill_nom = routine.get("skill")

        if executer_tache is not None:
            texte = await executer_tache(routine["agent"], consigne, skill_nom)
        else:
            systeme = (
                f"Tu es l'agent {routine['agent']} de Jarvis AIOS. "
                "Rédige la sortie directement en Markdown structuré en français."
            )
            if skill_nom:
                skill_info = await asyncio.to_thread(skills_engine.charger_skill, skill_nom)
                if skill_info is not None:
                    systeme += f"\n\nSkill '{skill_info['nom']}' :\n{skill_info['contenu_md']}"
            res = await llm_router.generer(
                LLMRequest(
                    prompt=consigne,
                    systeme=systeme,
                    session_cle=None,
                )
            )
            texte = res.texte

        jour = date.today().isoformat()
        sortie_modele = str(routine["sortie"]).replace("{date}", jour)
        sortie_rel = normaliser_id_note(sortie_modele)
        chemin_sortie = chemin_sur(sortie_rel)
        contenu_final = _formater_sortie_markdown(texte, routine, sortie_rel, jour)

        def _ecrire_sortie() -> None:
            chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
            chemin_sortie.write_text(contenu_final, encoding="utf-8")
            indexer.indexer_fichier(chemin_sortie)

        await asyncio.to_thread(_ecrire_sortie)
        await hub.broadcast("cerveau_maj", {})

        if routine["id"] == "brief-du-matin":
            taches_ext = _extraire_taches_brief(contenu_final)
            nb_ajoutees = await asyncio.to_thread(_inserer_taches_du_jour, taches_ext, jour)
            if nb_ajoutees > 0 or taches_ext:
                await hub.broadcast("taches_maj", {})

        fin = db.maintenant_iso()
        resume = " ".join(texte.strip().split())[:200]
        await db.async_execute(
            """
            UPDATE runs_routines
            SET fin = ?, statut = 'ok', sortie_path = ?, resume = ?, erreur = NULL
            WHERE id = ?
            """,
            (fin, sortie_rel, resume, run_id),
        )
        await hub.broadcast(
            "routine_etat",
            {"id": routine["id"], "statut": "ok", "run_id": run_id},
        )
        return run_id
    except Exception as exc:
        logger.exception("Erreur lors de l'exécution de la routine %s", routine["id"])
        fin = db.maintenant_iso()
        await db.async_execute(
            "UPDATE runs_routines SET fin = ?, statut = 'erreur', erreur = ? WHERE id = ?",
            (fin, str(exc), run_id),
        )
        await hub.broadcast(
            "routine_etat",
            {"id": routine["id"], "statut": "erreur", "run_id": run_id},
        )
        return run_id


def _est_quotidienne_manquee(routine: dict[str, Any], maintenant: datetime) -> bool:
    """Détermine si une routine quotidienne active n'a pas encore eu son run réussi du jour."""
    if not routine.get("actif"):
        return False

    champs = str(routine.get("cron") or "").split()
    if len(champs) != 5:
        return False
    minute_s, heure_s, jour_s, mois_s, semaine_s = champs
    if jour_s != "*" or mois_s != "*" or semaine_s != "*":
        return False
    if not (minute_s.isdigit() and heure_s.isdigit()):
        return False

    heure_prevue = maintenant.replace(
        hour=int(heure_s),
        minute=int(minute_s),
        second=0,
        microsecond=0,
    )
    if maintenant < heure_prevue:
        return False

    jour_iso = maintenant.date().isoformat()
    deja_ok = db.query_one(
        """
        SELECT id FROM runs_routines
        WHERE routine_id = ? AND statut = 'ok' AND debut LIKE ?
        LIMIT 1
        """,
        (routine["id"], f"{jour_iso}%"),
    )
    return deja_ok is None


def _planifier_tache_routine(routine_id: str) -> None:
    """Lance une routine en tâche asyncio de fond suivie proprement."""
    tache = asyncio.create_task(lancer_routine(routine_id))
    _taches_actives.add(tache)
    tache.add_done_callback(_taches_actives.discard)


def _enregistrer_jobs_dans_scheduler(sched: AsyncIOScheduler) -> None:
    """Synchronise les jobs APScheduler avec les routines actives sur disque."""
    for job in list(sched.get_jobs()):
        sched.remove_job(job.id)

    for routine in lister_routines():
        if not routine.get("actif"):
            continue
        try:
            trigger = CronTrigger.from_crontab(routine["cron"], timezone=_TZ)
        except ValueError as exc:
            logger.warning("Cron invalide pour la routine %s (%s) : %s", routine["id"], routine["cron"], exc)
            continue

        sched.add_job(
            lancer_routine,
            trigger=trigger,
            args=[routine["id"]],
            id=f"routine:{routine['id']}",
            name=routine["nom"],
            coalesce=True,
            misfire_grace_time=6 * 3600,
            max_instances=1,
            replace_existing=True,
        )


def demarrer() -> None:
    """Démarre ``AsyncIOScheduler`` (Europe/Paris) et déclenche les rattrapages du jour.

    Doit être appelé depuis une boucle asyncio active.
    """
    global _scheduler
    arreter()

    # Les runs restés « en_cours » viennent d'un arrêt brutal du serveur : on les clôt en erreur
    # pour qu'ils ne bloquent pas l'affichage ni le rattrapage du jour.
    db.execute(
        "UPDATE runs_routines SET statut = 'erreur', fin = ?, erreur = 'Interrompu (serveur arrêté)' "
        "WHERE statut = 'en_cours'",
        (datetime.now().isoformat(timespec="seconds"),),
    )

    sched = AsyncIOScheduler(timezone=TIMEZONE_PARIS)
    _enregistrer_jobs_dans_scheduler(sched)
    sched.start()
    _scheduler = sched

    maintenant = datetime.now(_TZ)
    for routine in lister_routines():
        if _est_quotidienne_manquee(routine, maintenant):
            logger.info("Rattrapage de la routine quotidienne manquée : %s", routine["id"])
            _planifier_tache_routine(routine["id"])


def recharger() -> None:
    """Recharge dynamiquement les jobs du planificateur s'il est démarré."""
    if _scheduler is not None and _scheduler.running:
        _enregistrer_jobs_dans_scheduler(_scheduler)


def arreter() -> None:
    """Arrête proprement le planificateur et annule les tâches de rattrapage en cours."""
    global _scheduler
    for tache in list(_taches_actives):
        if not tache.done():
            tache.cancel()
    _taches_actives.clear()

    if _scheduler is not None:
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=False)
        except (SchedulerNotRunningError, RuntimeError) as exc:
            logger.debug("Arrêt scheduler ignoré : %s", exc)
        finally:
            _scheduler = None
