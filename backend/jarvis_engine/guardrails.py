"""Garde-fous de sécurité codés en dur et purs (sans effet de bord ni dépendance Windows)."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend import config

StatutDecision = Literal["autorise", "accord_requis", "interdit"]


@dataclass(frozen=True)
class Decision:
    """Résultat d'une évaluation de sécurité par les garde-fous Jarvis."""

    statut: StatutDecision
    raison: str


_HOMOGLYPHES: dict[str, str] = {
    # Cyrillique (majuscules et minuscules selon apparence visuelle)
    "\u0410": "a",
    "\u0430": "a",
    "\u0412": "b",
    "\u0432": "b",
    "\u0421": "c",
    "\u0441": "c",
    "\u0415": "e",
    "\u0435": "e",
    "\u0401": "e",
    "\u0451": "e",
    "\u0404": "e",
    "\u0454": "e",
    "\u041d": "h",
    "\u043d": "h",
    "\u04bb": "h",
    "\u0406": "i",
    "\u0456": "i",
    "\u0407": "i",
    "\u0457": "i",
    "\u04c0": "i",
    "\u04cf": "i",
    "\u0408": "j",
    "\u0458": "j",
    "\u041a": "k",
    "\u043a": "k",
    "\u049a": "k",
    "\u049b": "k",
    "\u041c": "m",
    "\u043c": "m",
    "\u041e": "o",
    "\u043e": "o",
    "\u04e8": "o",
    "\u04e9": "o",
    "\u0420": "p",
    "\u0440": "p",
    "\u0405": "s",
    "\u0455": "s",
    "\u0422": "t",
    "\u0442": "t",
    "\u0423": "y",
    "\u0443": "y",
    "\u04ae": "y",
    "\u04af": "y",
    "\u0425": "x",
    "\u0445": "x",
    "\u0501": "d",
    "\u051b": "q",
    "\u051d": "w",
    # Grec courant
    "\u0391": "a",
    "\u03b1": "a",
    "\u0392": "b",
    "\u03b2": "b",
    "\u0395": "e",
    "\u03b5": "e",
    "\u0396": "z",
    "\u03b6": "z",
    "\u0397": "h",
    "\u03b7": "n",
    "\u0399": "i",
    "\u03b9": "i",
    "\u039a": "k",
    "\u03ba": "k",
    "\u039c": "m",
    "\u03bc": "m",
    "\u039d": "n",
    "\u03bd": "v",
    "\u039f": "o",
    "\u03bf": "o",
    "\u03a1": "p",
    "\u03c1": "p",
    "\u03a4": "t",
    "\u03c4": "t",
    "\u03a5": "y",
    "\u03c5": "u",
    "\u03a7": "x",
    "\u03c7": "x",
    "\u03f9": "c",
    "\u03f2": "c",
}

_LIGATURES: dict[str, str] = {
    "\u0153": "oe",
    "\u0152": "oe",
    "\u00e6": "ae",
    "\u00c6": "ae",
    "\u00df": "ss",
}

_CARACTERES_INVISIBLES: frozenset[str] = frozenset(
    "\u200b\u200c\u200d\u200e\u200f"
    "\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064"
    "\u2066\u2067\u2068\u2069"
    "\ufeff\u00ad\u034f\u061c\u180e"
)

MOTS_PAIEMENT: tuple[str, ...] = (
    "payer",
    "paiement",
    "paiements",
    "acheter",
    "achat",
    "achats",
    "commander",
    "commande",
    "commandes",
    "valider la commande",
    "checkout",
    "panier",
    "paniers",
    "cart",
    "carts",
    "buy",
    "purchase",
    "pay now",
    "carte bancaire",
    "paypal",
    "stripe",
    "abonnement",
    "abonnements",
    "s'abonner",
    "s abonner",
    "subscribe",
)

RESEAUX_SOCIAUX: tuple[str, ...] = (
    "facebook",
    "instagram",
    "x.com",
    "twitter",
    "linkedin",
    "tiktok",
    "youtube",
    "reddit",
    "threads",
    "bluesky",
    "mastodon",
    "pinterest",
    "snapchat",
    "discord",
    "telegram",
    "whatsapp",
)

MOTS_PUBLICATION: tuple[str, ...] = (
    "publier",
    "poster",
    "partager",
    "tweeter",
    "tweet",
    "post",
    "share",
    "publish",
    "story",
)

MOTS_MOT_DE_PASSE: tuple[str, ...] = (
    "mot de passe",
    "mots de passe",
    "password",
    "passphrase",
    "passcode",
    "code pin",
    "mdp",
)

MOTS_CLIC_ACCORD: tuple[str, ...] = (
    "envoyer",
    "send",
    "valider",
    "confirmer",
    "supprimer",
    "delete",
    "effacer",
    "installer",
    "enregistrer sous",
    "oui",
    "ok",
    "partager",
    "share",
    "publier",
    "poster",
    "publish",
    "post",
    "tweeter",
    "tweet",
)

PROCESSUS_SENSIBLES: frozenset[str] = frozenset(
    {
        "regedit",
        "powershell",
        "pwsh",
        "cmd",
        "windowsterminal",
        "wt",
        "mmc",
        "services",
        "systemsettings",
    }
)

MOTS_FENETRES_SENSIBLES: tuple[str, ...] = (
    "regedit",
    "editeur du registre",
    "registry editor",
    "services.msc",
    "gestion de l'ordinateur",
    "computer management",
    "parametres windows",
    "parametres de windows",
    "windows settings",
    "systemsettings",
    "powershell",
    "invite de commandes",
    "command prompt",
    "cmd.exe",
    "cmd",
    "terminal",
)

TITRES_FENETRES_SENSIBLES_EXACTS: frozenset[str] = frozenset(
    {
        "services",
        "parametres",
        "settings",
    }
)

APPS_LISTE_BLANCHE: frozenset[str] = frozenset(
    {
        "notepad",
        "calc",
        "explorer",
        "mspaint",
        "chrome",
        "msedge",
        "obsidian",
    }
)

ACTIONS_CONNUES: frozenset[str] = frozenset(
    {
        "cliquer",
        "double_cliquer",
        "taper",
        "touche",
        "defiler",
        "ouvrir_application",
    }
)

TOUCHES_ENTREE: frozenset[str] = frozenset({"enter", "return", "entree"})

SOUS_DOSSIERS_ROOT_INTERDITS: tuple[str, ...] = (
    "backend",
    ".venv",
    "runtime",
    "overlay",
    "frontend",
)

SEGMENTS_TOUJOURS_INTERDITS: frozenset[str] = frozenset({".ssh", ".gnupg", ".git"})
EXTENSIONS_SCRIPTS_AUTORISEES: frozenset[str] = frozenset({".py", ".ps1"})

_CARTE_CHIFFRES_RE = re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)")


def _compiler_motif_mots(mots: tuple[str, ...]) -> re.Pattern[str]:
    """Compile un motif regex avec bornes alphanumériques pour une liste d'expressions."""
    echappes = sorted((re.escape(m) for m in mots), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(echappes) + r")(?![a-z0-9])")


_RE_PAIEMENT = _compiler_motif_mots(MOTS_PAIEMENT)
_RE_RESEAUX = _compiler_motif_mots(RESEAUX_SOCIAUX)
_RE_PUBLICATION = _compiler_motif_mots(MOTS_PUBLICATION)
_RE_CLIC_ACCORD = _compiler_motif_mots(MOTS_CLIC_ACCORD)
_RE_FENETRES_SENSIBLES = _compiler_motif_mots(MOTS_FENETRES_SENSIBLES)
_RE_MOT_DE_PASSE = _compiler_motif_mots(MOTS_MOT_DE_PASSE)
# Titre de fenêtre contenant un nom de fichier (document existant ouvert) : « rapport.docx - Word ».
_RE_TITRE_DOCUMENT = re.compile(r"[\w\-\(\) ]\.[a-z0-9]{1,5}(?![a-z0-9])")
# Expressions techniques contenant « commande(s) » sans lien avec un achat (retirées avant le test paiement).
_RE_COMMANDES_TECHNIQUES = _compiler_motif_mots(
    (
        "invite de commandes",
        "invite de commande",
        "ligne de commande",
        "ligne de commandes",
        "palette de commandes",
        "historique des commandes du terminal",
    )
)


def normaliser(texte: str) -> str:
    """Normalise un texte pour neutraliser contournements Unicode, accents et homoglyphes.

    Supprime les caractères invisibles et marques de format (``Cf``), applique NFKD sans
    diacritiques (``Mn``), remplace les homoglyphes cyrilliques/grecs courants et les
    ligatures, passe en minuscules et compacte les espaces.

    Args:
        texte: Chaîne brute à normaliser.

    Returns:
        Chaîne normalisée en ASCII/minuscules avec espaces simples.
    """
    if not texte:
        return ""

    # 1. Suppression des caractères invisibles / Unicode de format avant décomposition.
    filtre = "".join(
        ch
        for ch in texte
        if ch not in _CARACTERES_INVISIBLES and unicodedata.category(ch) != "Cf"
    )
    # 2. Décomposition NFKD (transforme aussi les espaces insécables en espaces standard) puis retrait des accents.
    nfkd = unicodedata.normalize("NFKD", filtre)
    sans_accents = "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")

    # 3. Substitution des homoglyphes et ligatures avant mise en minuscules.
    remplace = "".join(_LIGATURES.get(ch, _HOMOGLYPHES.get(ch, ch)) for ch in sans_accents)
    return " ".join(remplace.lower().split())


def luhn_valide(chiffres: str) -> bool:
    """Vérifie la validité Luhn d'une séquence de chiffres (carte bancaire).

    Args:
        chiffres: Chaîne numérique pouvant contenir espaces ou tirets.

    Returns:
        ``True`` si la séquence contient au moins 2 chiffres ASCII et passe Luhn.
    """
    if not isinstance(chiffres, str):
        return False
    propre = chiffres.replace(" ", "").replace("-", "").replace("\u00a0", "").strip()
    if len(propre) < 2 or not propre.isascii() or not propre.isdigit():
        return False

    total = 0
    for idx, ch in enumerate(reversed(propre)):
        val = int(ch)
        if idx % 2 == 1:
            val *= 2
            if val > 9:
                val -= 9
        total += val
    return total % 10 == 0


def _extraire_contexte_normalise(contexte: dict[str, Any] | None) -> dict[str, Any]:
    """Extrait et normalise titre, processus, URL et élément cible depuis ``contexte``."""
    ctx = contexte if contexte is not None else {}
    if not isinstance(ctx, dict):
        raise TypeError("Le contexte doit être un dictionnaire.")

    fenetre = ctx.get("fenetre")
    fenetre_dict = fenetre if isinstance(fenetre, dict) else {}

    titre_brut = str(ctx.get("titre") or ctx.get("fenetre_titre") or fenetre_dict.get("titre") or "")
    proc_brut = str(
        ctx.get("processus") or ctx.get("fenetre_processus") or fenetre_dict.get("processus") or ""
    )
    url_brut = str(ctx.get("url") or fenetre_dict.get("url") or "")

    elem_brut = ctx.get("element")
    elem_nom_brut = str(ctx.get("element_nom") or "")
    elem_role_brut = str(ctx.get("element_role") or "")
    est_mdp = bool(ctx.get("est_mot_de_passe") or ctx.get("is_password"))

    if isinstance(elem_brut, dict):
        elem_nom_brut = str(elem_brut.get("nom") or elem_nom_brut)
        elem_role_brut = str(elem_brut.get("role") or elem_role_brut)
        est_mdp = est_mdp or bool(elem_brut.get("est_mot_de_passe") or elem_brut.get("is_password"))
    elif isinstance(elem_brut, str) and not elem_nom_brut:
        elem_nom_brut = elem_brut

    proc_norm = normaliser(proc_brut).removesuffix(".exe").strip()
    return {
        "titre": normaliser(titre_brut),
        "processus": proc_norm,
        "url": normaliser(url_brut),
        "element_nom": normaliser(elem_nom_brut),
        "element_role": normaliser(elem_role_brut),
        "est_mot_de_passe": est_mdp,
    }


def _contient_carte_luhn(texte: str) -> bool:
    """Détecte toute séquence de 13 à 19 chiffres valide selon l'algorithme de Luhn."""
    for match in _CARTE_CHIFFRES_RE.finditer(texte):
        chiffres = re.sub(r"\D", "", match.group(0))
        if 13 <= len(chiffres) <= 19 and luhn_valide(chiffres):
            return True
    return False


def _est_champ_mot_de_passe(ctx_norm: dict[str, Any]) -> bool:
    """Détermine si l'élément cible correspond à un champ mot de passe."""
    if ctx_norm["est_mot_de_passe"]:
        return True
    role = ctx_norm["element_role"]
    nom = ctx_norm["element_nom"]
    # Certains contrôles UIA/web exposent "PasswordBox" ou "PasswordControl" sans espace.
    if "password" in role or "mot de passe" in role:
        return True
    return bool(_RE_MOT_DE_PASSE.search(role) or _RE_MOT_DE_PASSE.search(nom))


def _est_touche_entree(combinaison: Any) -> bool:
    """Vérifie si une combinaison clavier inclut la touche Entrée / Return."""
    if isinstance(combinaison, list | tuple):
        brut = "+".join(str(x) for x in combinaison)
    else:
        brut = str(combinaison or "")
    norm = normaliser(brut)
    if not norm:
        return False
    touches = [part.strip() for part in re.split(r"[+\s,]+", norm) if part.strip()]
    return any(t in TOUCHES_ENTREE for t in touches)


def _est_fenetre_sensible(ctx_norm: dict[str, Any]) -> bool:
    """Indique si la fenêtre active correspond à un outil système ou terminal sensible."""
    proc = ctx_norm["processus"]
    titre = ctx_norm["titre"]
    if proc in PROCESSUS_SENSIBLES:
        return True
    if titre in TITRES_FENETRES_SENSIBLES_EXACTS:
        return True
    return bool(_RE_FENETRES_SENSIBLES.search(titre))


def verifier(action: dict[str, Any], contexte: dict[str, Any] | None = None) -> Decision:
    """Évalue une action PC face aux garde-fous non contournables.

    Priorité absolue : toute erreur interne ou violation critique retourne ``interdit`` ;
    en cas d'ambiguïté ou d'action sensible, retourne ``accord_requis``.

    Args:
        action: Dictionnaire décrivant l'action envisagée (``type``, arguments).
        contexte: Contexte observé juste avant l'action (``titre``, ``processus``, ``url``, ``element``).

    Returns:
        Instance ``Decision(statut, raison)``.
    """
    try:
        if not isinstance(action, dict):
            return Decision("interdit", "Action invalide : dictionnaire attendu.")

        ctx_norm = _extraire_contexte_normalise(contexte)
        action_type = normaliser(str(action.get("type") or action.get("action") or ""))
        if not action_type:
            return Decision("interdit", "Type d'action manquant.")

        # 1. INTERDIT — Paiement / commande / abonnement dans titre, URL ou élément ciblé.
        for champ_nom, valeur in (
            ("titre de fenêtre", ctx_norm["titre"]),
            ("URL", ctx_norm["url"]),
            ("élément visé", ctx_norm["element_nom"]),
        ):
            # « Invite de commandes », « ligne de commande »… ne sont pas des commandes d'achat.
            match_paiement = _RE_PAIEMENT.search(_RE_COMMANDES_TECHNIQUES.sub(" ", valeur))
            if match_paiement:
                return Decision(
                    "interdit",
                    f"Zone de paiement détectée ({match_paiement.group(0)}) dans {champ_nom}.",
                )

        # 2. INTERDIT — Publication sur réseau social ou plateforme externe.
        sur_reseau = bool(
            _RE_RESEAUX.search(ctx_norm["titre"]) or _RE_RESEAUX.search(ctx_norm["url"])
        )
        if sur_reseau:
            for champ in (ctx_norm["element_nom"], ctx_norm["titre"], ctx_norm["url"]):
                match_pub = _RE_PUBLICATION.search(champ)
                if match_pub:
                    return Decision(
                        "interdit",
                        f"Publication sur réseau social/plateforme interdite ({match_pub.group(0)}).",
                    )

        # 3. INTERDIT — Saisie de carte bancaire (Luhn valide) ou dans un champ mot de passe.
        if action_type == "taper":
            texte_brut = str(action.get("texte") or "")
            if _est_champ_mot_de_passe(ctx_norm):
                return Decision("interdit", "Saisie dans un champ mot de passe interdite.")
            if _contient_carte_luhn(texte_brut) or _contient_carte_luhn(normaliser(texte_brut)):
                return Decision(
                    "interdit",
                    "Saisie d'un numéro de carte bancaire (Luhn valide) interdite.",
                )

        # 4. INTERDIT — Lancement d'application hors liste blanche.
        if action_type == "ouvrir_application":
            nom_app_brut = str(action.get("nom") or action.get("application") or "")
            if any(sep in nom_app_brut for sep in ("/", "\\", ":", "..", "\x00")):
                return Decision("interdit", f"Chemin d'application interdit : {nom_app_brut!r}.")
            nom_app = normaliser(nom_app_brut).removesuffix(".exe").strip()
            if nom_app not in APPS_LISTE_BLANCHE:
                return Decision(
                    "interdit",
                    f"Application hors liste blanche interdite : {nom_app_brut!r}.",
                )

        # 5. ACCORD REQUIS — Fenêtre système, terminal, registre, services ou paramètres Windows.
        if _est_fenetre_sensible(ctx_norm):
            return Decision(
                "accord_requis",
                "Action dans une fenêtre système ou terminal sensible : accord utilisateur requis.",
            )

        # 6. ACCORD REQUIS — Touche Entrée ou Ctrl+Entrée.
        if action_type == "touche":
            comb = action.get("combinaison") or action.get("touches") or action.get("touche")
            if _est_touche_entree(comb):
                return Decision(
                    "accord_requis",
                    "Appui sur la touche Entrée soumis à accord utilisateur.",
                )

        # 6 ter. ACCORD REQUIS — Saisie dans un document existant (titre avec nom de fichier, ex.
        # « notes.txt – Bloc-notes ») : Windows 11 rouvre les onglets précédents, la saisie
        # modifierait alors un fichier de l'utilisateur.
        if action_type == "taper" and _RE_TITRE_DOCUMENT.search(ctx_norm["titre"]):
            return Decision("accord_requis", "Saisie dans un document existant de l'utilisateur : accord requis.")

        # 6 bis. ACCORD REQUIS — Texte multiligne (un retour à la ligne peut valider un formulaire).
        if action_type == "taper" and ("\n" in str(action.get("texte") or "") or "\r" in str(action.get("texte") or "")):
            return Decision("accord_requis", "Texte multiligne (peut valider un formulaire) : accord requis.")

        # 7. ACCORD REQUIS — Clic sur un élément illisible (cible inconnue) ou engageant/destructif.
        if action_type in ("cliquer", "double_cliquer") and contexte is not None and contexte.get("element") is None:
            return Decision("accord_requis", "Élément sous le curseur illisible : accord utilisateur requis.")
        if action_type in ("cliquer", "double_cliquer"):
            match_clic = _RE_CLIC_ACCORD.search(ctx_norm["element_nom"])
            if match_clic:
                return Decision(
                    "accord_requis",
                    f"Clic sur élément sensible « {match_clic.group(0)} » soumis à accord utilisateur.",
                )

        # 8. Par sécurité, tout type d'action inconnu requiert l'accord utilisateur.
        if action_type not in ACTIONS_CONNUES:
            return Decision(
                "accord_requis",
                f"Type d'action non reconnu ({action_type}) : accord utilisateur requis.",
            )

        return Decision("autorise", "Action autorisée.")
    except Exception as exc:
        return Decision("interdit", f"Erreur interne lors de la vérification : {exc}")


def verifier_chemin_suppression(path: str | Path) -> Decision:
    """Vérifie qu'un chemin peut être envoyé à la corbeille après accord utilisateur.

    Refuse (``interdit``) les racines de lecteur, dossiers système Windows, dossier
    utilisateur, ``AppData``, ``.ssh``, ``.gnupg``, ``.git``, le dossier ``ROOT`` et
    ses sous-dossiers techniques (``backend``, ``.venv``, ``runtime``, ``overlay``,
    ``frontend``). Tous les autres chemins retournent ``accord_requis``.

    Args:
        path: Chemin cible à évaluer.

    Returns:
        Instance ``Decision(statut, raison)``.
    """
    try:
        if not isinstance(path, str | Path):
            return Decision("interdit", "Chemin invalide.")
        brut = str(path).strip()
        if not brut or "\x00" in brut:
            return Decision("interdit", "Chemin de suppression vide ou invalide.")

        p_obj = Path(brut).expanduser()
        root_res = config.ROOT.resolve()
        cible = p_obj.resolve() if p_obj.is_absolute() else (root_res / p_obj).resolve()

        # Racine de lecteur (ex. C:\) ou racine UNC.
        if cible == Path(cible.anchor) or len(cible.parts) <= 1:
            return Decision("interdit", f"Suppression de racine interdite : {cible}")

        parts_lower = [part.lower() for part in cible.parts]

        # Tout segment .ssh, .gnupg, .git est strictement interdit quel que soit l'emplacement.
        for segment in parts_lower:
            if segment in SEGMENTS_TOUJOURS_INTERDITS:
                return Decision("interdit", f"Suppression dans {segment} interdite : {cible}")

        # Dossiers système Windows sous la racine de lecteur.
        if len(parts_lower) >= 2:
            premier_dossier = parts_lower[1]
            if premier_dossier in ("windows", "programdata") or premier_dossier.startswith("program files"):
                return Decision("interdit", f"Suppression dans un dossier système interdite : {cible}")

        # Variables d'environnement système Windows si disponibles.
        for var_env in ("WINDIR", "SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"):
            brut_env = f"%{var_env}%"
            val_env = os.path.expandvars(brut_env)
            if val_env and val_env != brut_env:
                env_path = Path(val_env).resolve()
                if cible == env_path or cible.is_relative_to(env_path):
                    return Decision("interdit", f"Suppression système ({var_env}) interdite : {cible}")

        # Dossier utilisateur courant (Path.home() et %USERPROFILE%) + racine des profils.
        home = Path.home().resolve()
        racines_user: list[Path] = [home]
        userprofile = os.path.expandvars("%USERPROFILE%")
        if userprofile and userprofile != "%USERPROFILE%":
            racines_user.append(Path(userprofile).resolve())

        for racine_user in racines_user:
            if cible == racine_user or racine_user.is_relative_to(cible):
                return Decision("interdit", f"Suppression du dossier utilisateur interdite : {cible}")
        if len(parts_lower) >= 3 and parts_lower[1] == "users" and len(parts_lower) == 3:
            return Decision("interdit", f"Suppression du dossier utilisateur interdite : {cible}")

        # ROOT lui-même (ou un de ses parents) et ses sous-dossiers techniques sont interdits.
        if cible == root_res or root_res.is_relative_to(cible):
            return Decision("interdit", f"Suppression du dossier projet Jarvis interdite : {cible}")

        for sous_dossier in SOUS_DOSSIERS_ROOT_INTERDITS:
            protege = (root_res / sous_dossier).resolve()
            if cible == protege or cible.is_relative_to(protege):
                return Decision("interdit", f"Suppression dans ROOT/{sous_dossier} interdite : {cible}")

        # AppData : bloque tout AppData (via Path.home() et %APPDATA%/%LOCALAPPDATA%)
        # hors du cas où JARVIS_ROOT de test vit sous AppData\Local\Temp.
        dossiers_appdata: list[Path] = [(home / "AppData").resolve()]
        for var_app in ("%APPDATA%", "%LOCALAPPDATA%"):
            exp_app = os.path.expandvars(var_app)
            if exp_app and exp_app != var_app:
                dossiers_appdata.append(Path(exp_app).resolve())

        for appdata in dossiers_appdata:
            root_dans_appdata = root_res.is_relative_to(appdata)
            if cible == appdata or cible.is_relative_to(appdata):
                if not (root_dans_appdata and cible.is_relative_to(root_res)):
                    return Decision("interdit", f"Suppression dans AppData interdite : {cible}")
        if cible.is_relative_to(root_res):
            rel_parts = [p.lower() for p in cible.relative_to(root_res).parts]
            if "appdata" in rel_parts:
                return Decision("interdit", f"Suppression dans AppData interdite : {cible}")
        elif "appdata" in parts_lower:
            return Decision("interdit", f"Suppression dans AppData interdite : {cible}")

        return Decision("accord_requis", f"Suppression via corbeille soumise à accord : {cible}")
    except Exception as exc:
        return Decision("interdit", f"Erreur interne lors de la vérification du chemin : {exc}")


def verifier_script(nom: str) -> Decision:
    """Valide le nom d'un script avant exécution dans ``aios/scripts/``.

    Args:
        nom: Nom simple de fichier (``.py`` ou ``.ps1`` sans séparateur de chemin).

    Returns:
        ``accord_requis`` si le nom est sûr, sinon ``interdit``.
    """
    try:
        if not isinstance(nom, str) or not nom.strip():
            return Decision("interdit", "Nom de script vide.")
        propre = nom.strip()
        if any(car in propre for car in ("/", "\\", "..", ":", "\x00")):
            return Decision("interdit", f"Nom de script ou traversée de chemin interdit : {nom!r}.")
        suffixe = Path(propre).suffix.lower()
        if suffixe not in EXTENSIONS_SCRIPTS_AUTORISEES:
            return Decision(
                "interdit",
                f"Extension de script non autorisée ({suffixe or 'aucune'}) : .py ou .ps1 uniquement.",
            )
        return Decision("accord_requis", f"Exécution du script « {propre} » soumise à accord utilisateur.")
    except Exception as exc:
        return Decision("interdit", f"Erreur interne lors de la vérification du script : {exc}")
