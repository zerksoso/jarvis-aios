"""Tests exhaustifs des garde-fous purs : normalisation, paiements, réseaux, Luhn, chemins et erreurs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.jarvis_engine import guardrails
from backend.jarvis_engine.guardrails import (
    Decision,
    luhn_valide,
    normaliser,
    verifier,
    verifier_chemin_suppression,
    verifier_script,
)

# Plus de 50 cas paramétrés couvrant tous les vecteurs du contrat §11.10 et du lot 4.
CAS_VERIFIER: list[tuple[str, dict[str, Any], dict[str, Any], str]] = [
    # --- 1. Paiement FR/EN dans titre, URL ou élément (interdit) ---
    ("paiement_titre_payer", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Payer votre panier"}, "interdit"),
    ("paiement_titre_paiement", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Validation Paiement"}, "interdit"),
    ("paiement_titre_acheter", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Acheter maintenant"}, "interdit"),
    ("paiement_titre_achat", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Confirmer votre achat"}, "interdit"),
    ("paiement_titre_commander", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Commander en 1 clic"}, "interdit"),
    ("paiement_url_checkout", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Boutique", "url": "https://shop.example/checkout"}, "interdit"),
    ("paiement_url_stripe", {"type": "taper", "texte": "test"}, {"titre": "Formulaire", "url": "https://checkout.stripe.com/pay"}, "interdit"),
    ("paiement_url_paypal", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Navigateur", "url": "https://www.paypal.com/signin"}, "interdit"),
    ("paiement_elem_valider_commande", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Boutique", "element": {"nom": "Valider la commande"}}, "interdit"),
    ("paiement_elem_pay_now", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Shop", "element": {"nom": "Pay Now"}}, "interdit"),
    ("paiement_elem_buy", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Store", "element": {"nom": "Buy"}}, "interdit"),
    ("paiement_elem_purchase", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Store", "element": {"nom": "Purchase"}}, "interdit"),
    ("paiement_elem_cart", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Store", "element": {"nom": "Cart (2)"}}, "interdit"),
    ("paiement_elem_panier", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Boutique", "element": {"nom": "Mon panier"}}, "interdit"),
    ("paiement_elem_carte_bancaire", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Banque", "element": {"nom": "Carte bancaire"}}, "interdit"),
    ("paiement_elem_abonnement", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "SaaS", "element": {"nom": "Abonnement Pro"}}, "interdit"),
    ("paiement_elem_sabonner", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Offres", "element": {"nom": "S'abonner"}}, "interdit"),
    ("paiement_elem_subscribe", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Pricing", "element": {"nom": "Subscribe"}}, "interdit"),
    # --- 2. Obfuscation Unicode : majuscules, accents, espaces insécables, U+200B, homoglyphes cyrilliques ---
    ("obf_majuscules_accents", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "PÂIÈMÉNT SÉCURISÉ"}, "interdit"),
    ("obf_espaces_insecables", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Shop", "element": {"nom": "Valider\u00a0la\u00a0commande"}}, "interdit"),
    ("obf_invisible_u200b", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Boutique", "element": {"nom": "pa\u200byer"}}, "interdit"),
    ("obf_homoglyphe_cyrillique_payer", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Boutique", "element": {"nom": "раyer"}}, "interdit"),
    ("obf_homoglyphe_cyrillique_paypal", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "раураl"}, "interdit"),
    ("obf_homoglyphe_cyrillique_mixte", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "РА\u200bУЕR"}, "interdit"),
    # --- 3. Publication sur réseaux sociaux (interdit) vs Partager dans l'explorateur (accord_requis) ---
    ("social_facebook_publier", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Facebook", "element": {"nom": "Publier"}}, "interdit"),
    ("social_instagram_story", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Instagram", "element": {"nom": "Story"}}, "interdit"),
    ("social_x_com_post", {"type": "cliquer", "x": 10, "y": 10}, {"url": "https://x.com/home", "element": {"nom": "Post"}}, "interdit"),
    ("social_twitter_tweeter", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Twitter", "element": {"nom": "Tweeter"}}, "interdit"),
    ("social_linkedin_partager", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Fil LinkedIn", "element": {"nom": "Partager"}}, "interdit"),
    ("social_youtube_share", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "YouTube", "element": {"nom": "Share"}}, "interdit"),
    ("social_reddit_publish", {"type": "cliquer", "x": 10, "y": 10}, {"url": "https://www.reddit.com/submit", "element": {"nom": "Publish"}}, "interdit"),
    ("social_discord_poster", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Discord", "element": {"nom": "Poster"}}, "interdit"),
    ("social_whatsapp_partager", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "WhatsApp", "element": {"nom": "Partager"}}, "interdit"),
    ("explorateur_partager_accord", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Documents", "processus": "explorer.exe", "element": {"nom": "Partager"}}, "accord_requis"),
    ("explorateur_share_accord", {"type": "cliquer", "x": 10, "y": 10}, {"titre": "Pictures", "processus": "explorer.exe", "element": {"nom": "Share"}}, "accord_requis"),
    # --- 4. Numéro de carte Luhn (interdit) vs non Luhn (autorisé) ---
    ("luhn_valide_16_chiffres", {"type": "taper", "texte": "4111111111111111"}, {"titre": "Bloc-notes"}, "interdit"),
    ("luhn_valide_espaces", {"type": "taper", "texte": "Carte 4532 0151 1283 0366"}, {"titre": "Bloc-notes"}, "interdit"),
    ("luhn_valide_tirets", {"type": "taper", "texte": "4111-1111-1111-1111"}, {"titre": "Bloc-notes"}, "interdit"),
    ("luhn_valide_13_chiffres", {"type": "taper", "texte": "4000000000006"}, {"titre": "Bloc-notes"}, "interdit"),
    ("luhn_valide_19_chiffres", {"type": "taper", "texte": "4000000000000000006"}, {"titre": "Bloc-notes"}, "interdit"),
    ("luhn_invalide_16_chiffres", {"type": "taper", "texte": "4111111111111112"}, {"titre": "Bloc-notes"}, "autorise"),
    ("luhn_invalide_suite", {"type": "taper", "texte": "1234567890123456"}, {"titre": "Bloc-notes"}, "autorise"),
    # --- 5. Champ mot de passe (interdit) ---
    ("mdp_role_passwordbox", {"type": "taper", "texte": "secret"}, {"titre": "Connexion", "element": {"nom": "Champ", "role": "PasswordBox"}}, "interdit"),
    ("mdp_nom_mot_de_passe", {"type": "taper", "texte": "secret"}, {"titre": "Connexion", "element": {"nom": "Mot de passe", "role": "EditControl"}}, "interdit"),
    ("mdp_flag_is_password", {"type": "taper", "texte": "secret"}, {"titre": "App", "element": {"nom": "Saisie", "role": "EditControl", "est_mot_de_passe": True}}, "interdit"),
    # --- 6. Touches Entrée / Ctrl+Entrée (accord_requis) ---
    ("touche_entree", {"type": "touche", "combinaison": "Entrée"}, {"titre": "Bloc-notes"}, "accord_requis"),
    ("touche_ctrl_entree", {"type": "touche", "combinaison": "ctrl+entrée"}, {"titre": "Messagerie"}, "accord_requis"),
    ("touche_enter", {"type": "touche", "combinaison": "enter"}, {"titre": "Formulaire"}, "accord_requis"),
    # --- 7. Clic sur éléments sensibles (accord_requis) ---
    ("clic_supprimer", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Explorateur", "element": {"nom": "Supprimer"}}, "accord_requis"),
    ("clic_envoyer", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Courrier", "element": {"nom": "Envoyer"}}, "accord_requis"),
    ("clic_valider", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Outil", "element": {"nom": "Valider"}}, "accord_requis"),
    ("clic_confirmer", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Outil", "element": {"nom": "Confirmer"}}, "accord_requis"),
    ("clic_installer", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Setup", "element": {"nom": "Installer"}}, "accord_requis"),
    ("clic_enregistrer_sous", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Bloc-notes", "element": {"nom": "Enregistrer sous..."}}, "accord_requis"),
    ("clic_ok", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Dialogue", "element": {"nom": "OK"}}, "accord_requis"),
    ("clic_oui", {"type": "cliquer", "x": 50, "y": 50}, {"titre": "Dialogue", "element": {"nom": "Oui"}}, "accord_requis"),
    # --- 8. Fenêtres système sensibles (accord_requis) ---
    ("fenetre_regedit", {"type": "cliquer", "x": 20, "y": 20}, {"titre": "Éditeur du Registre", "processus": "regedit.exe"}, "accord_requis"),
    ("fenetre_powershell", {"type": "taper", "texte": "dir"}, {"titre": "Windows PowerShell", "processus": "powershell.exe"}, "accord_requis"),
    ("fenetre_cmd", {"type": "taper", "texte": "echo hi"}, {"titre": "Invite de commandes", "processus": "cmd.exe"}, "accord_requis"),
    ("fenetre_terminal", {"type": "cliquer", "x": 20, "y": 20}, {"titre": "Terminal", "processus": "WindowsTerminal.exe"}, "accord_requis"),
    ("fenetre_services", {"type": "cliquer", "x": 20, "y": 20}, {"titre": "Services", "processus": "mmc.exe"}, "accord_requis"),
    ("fenetre_gestion_ordi", {"type": "cliquer", "x": 20, "y": 20}, {"titre": "Gestion de l'ordinateur", "processus": "mmc.exe"}, "accord_requis"),
    ("fenetre_parametres_win", {"type": "cliquer", "x": 20, "y": 20}, {"titre": "Paramètres", "processus": "SystemSettings.exe"}, "accord_requis"),
    # --- 9. Ouvrir application : liste blanche vs hors liste blanche ---
    ("app_notepad_autorise", {"type": "ouvrir_application", "nom": "notepad"}, {}, "autorise"),
    ("app_obsidian_autorise", {"type": "ouvrir_application", "nom": "Obsidian.exe"}, {}, "autorise"),
    ("app_powershell_interdit", {"type": "ouvrir_application", "nom": "powershell"}, {}, "interdit"),
    ("app_cmd_interdit", {"type": "ouvrir_application", "nom": "cmd.exe"}, {}, "interdit"),
    ("app_chemin_interdit", {"type": "ouvrir_application", "nom": "C:\\temp\\notepad.exe"}, {}, "interdit"),
    # --- 10. Actions courantes sûres (autorise) ---
    ("action_clic_fichier_blocnotes", {"type": "cliquer", "x": 15, "y": 35}, {"titre": "Sans titre - Bloc-notes", "processus": "notepad.exe", "element": {"nom": "Fichier", "role": "MenuItemControl"}}, "autorise"),
    ("action_taper_blocnotes", {"type": "taper", "texte": "Bonjour Jarvis !"}, {"titre": "Sans titre - Bloc-notes", "processus": "notepad.exe"}, "autorise"),
    ("action_defiler_normal", {"type": "defiler", "dy": -3}, {"titre": "Documentation Python", "processus": "chrome.exe"}, "autorise"),
    ("action_touche_ctrl_s", {"type": "touche", "combinaison": "ctrl+s"}, {"titre": "Sans titre - Bloc-notes", "processus": "notepad.exe"}, "autorise"),
    ("action_inconnue_doute", {"type": "glisser_special", "x": 10}, {"titre": "Bloc-notes"}, "accord_requis"),
]


@pytest.mark.parametrize("nom_cas,action,contexte,attendu", CAS_VERIFIER, ids=[c[0] for c in CAS_VERIFIER])
def test_verifier_cas_parametres(
    nom_cas: str,
    action: dict[str, Any],
    contexte: dict[str, Any],
    attendu: str,
) -> None:
    """Vérifie chaque règle de garde-fou sur les cas paramétrés (plus de 50 scénarios)."""
    del nom_cas
    dec = verifier(action, contexte)
    assert isinstance(dec, Decision)
    assert dec.statut == attendu
    assert dec.raison


def test_normaliser_et_luhn_valide() -> None:
    """Vérifie normaliser() sur Unicode invisible/homoglyphes et luhn_valide()."""
    assert normaliser("  PÂ\u200bYÉr\u00a0Éxprèss  ") == "payer express"
    assert normaliser("раyer") == "payer"
    assert normaliser("Ρay\u200bРаl") == "paypal"
    assert luhn_valide("4111 1111 1111 1111") is True
    assert luhn_valide("4111-1111-1111-1112") is False
    assert luhn_valide("abc") is False


def test_verifier_chemin_suppression(jarvis_root: Path) -> None:
    """Vérifie les chemins strictement interdits vs autorisés avec accord + corbeille."""
    interdits = [
        "C:\\",
        "C:\\Windows\\System32\\x",
        "C:\\Program Files (x86)\\Truc",
        "C:\\ProgramData\\Secret",
        str(Path.home()),
        str(Path.home() / "AppData" / "Local" / "Temp"),
        str(jarvis_root / "dossier" / ".ssh" / "id_rsa"),
        str(jarvis_root / "repo" / ".git" / "config"),
        str(jarvis_root / ".gnupg" / "key"),
        str(jarvis_root / "backend"),
        str(jarvis_root / "backend" / "main.py"),
        str(jarvis_root / ".venv" / "Scripts"),
        str(jarvis_root / "runtime" / "jarvis.db"),
        str(jarvis_root / "overlay" / "main.py"),
        str(jarvis_root / "frontend" / "package.json"),
        str(jarvis_root),
        "",
    ]
    for chemin in interdits:
        dec = verifier_chemin_suppression(chemin)
        assert dec.statut == "interdit", f"Chemin censé être interdit : {chemin}"

    autorise_accord = jarvis_root / "second-brain" / "brouillon.md"
    dec_ok = verifier_chemin_suppression(autorise_accord)
    assert dec_ok.statut == "accord_requis"


def test_verifier_script() -> None:
    """Vérifie verifier_script() sur extensions autorisées et traversées interdites."""
    assert verifier_script("bonjour.py").statut == "accord_requis"
    assert verifier_script("nettoyage.ps1").statut == "accord_requis"
    for mauvais in ("../evil.py", "sous/evil.py", "C:\\evil.py", "outil.exe", ""):
        assert verifier_script(mauvais).statut == "interdit"


def test_erreur_interne_verifier_retourne_interdit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Toute erreur interne pendant la vérification doit retourner strictement interdit."""
    def _boom(_ctx: Any) -> dict[str, Any]:
        raise RuntimeError("Défaillance simulée")

    monkeypatch.setattr(guardrails, "_extraire_contexte_normalise", _boom)
    dec = verifier({"type": "cliquer", "x": 10, "y": 10}, {"titre": "Bloc-notes"})
    assert dec.statut == "interdit"
    assert "erreur interne" in dec.raison.lower()

    # Action non-dict retourne également interdit.
    assert verifier(None, {}).statut == "interdit"  # type: ignore[arg-type]


def test_saisie_dans_document_existant_demande_accord() -> None:
    """Taper dans un onglet de fichier existant (ex. Bloc-notes Windows 11) exige l'accord."""
    from backend.jarvis_engine.guardrails import verifier

    ctx_doc = {"titre": "*CODES-INITIAUX.txt – Bloc-notes", "processus": "Notepad.exe"}
    assert verifier({"type": "taper", "texte": "bonjour"}, ctx_doc).statut == "accord_requis"
    ctx_vide = {"titre": "Sans titre – Bloc-notes", "processus": "Notepad.exe"}
    assert verifier({"type": "taper", "texte": "bonjour"}, ctx_vide).statut == "autorise"
