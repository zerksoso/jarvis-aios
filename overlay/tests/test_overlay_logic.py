"""Tests unitaires de la logique pure de l'overlay (sans créer ni afficher de fenêtres)."""

from __future__ import annotations

from overlay.cards import MAX_CARTES, CarteOverlay, FileCartes
from overlay.orb import (
    PRESETS_ORBE,
    MachineEtatsOrbe,
    calculer_position_pres_de_cadre,
    calculer_position_repos,
)
from overlay.win32_utils import GeometrieEcran, convertir_rect_physique_vers_logique


def test_conversion_coordonnees_ecran_unique_et_multiecran() -> None:
    """Vérifie la conversion pixels physiques -> coordonnées logiques Qt selon le DPR."""
    ecran_principal = GeometrieEcran(
        log_x=0,
        log_y=0,
        log_w=1280,
        log_h=720,
        dpr=1.5,
        phys_x=0,
        phys_y=0,
        phys_w=1920,
        phys_h=1080,
    )
    ecran_droite = GeometrieEcran(
        log_x=1280,
        log_y=0,
        log_w=1920,
        log_h=1080,
        dpr=1.0,
        phys_x=1920,
        phys_y=0,
        phys_w=1920,
        phys_h=1080,
    )
    ecran_gauche = GeometrieEcran(
        log_x=-1280,
        log_y=0,
        log_w=1280,
        log_h=720,
        dpr=1.5,
        phys_x=-1920,
        phys_y=0,
        phys_w=1920,
        phys_h=1080,
    )
    ecrans = [ecran_principal, ecran_droite, ecran_gauche]

    # Sur l'écran principal (DPR 1.5)
    assert convertir_rect_physique_vers_logique(1500, 750, 300, 150, ecrans=ecrans) == (
        1000,
        500,
        200,
        100,
    )
    # Sur l'écran secondaire à droite (DPR 1.0, origine physique 1920 -> logique 1280)
    assert convertir_rect_physique_vers_logique(2020, 100, 200, 80, ecrans=ecrans) == (
        1380,
        100,
        200,
        80,
    )
    # Sur l'écran négatif à gauche (DPR 1.5)
    assert convertir_rect_physique_vers_logique(-1620, 150, 150, 90, ecrans=ecrans) == (
        -1080,
        100,
        100,
        60,
    )
    # Repli sans liste d'écrans
    assert convertir_rect_physique_vers_logique(400, 200, 100, 50, dpr_defaut=2.0) == (
        200,
        100,
        50,
        25,
    )


def test_file_cartes_limitee_a_quatre() -> None:
    """Vérifie le plafond strict de 4 cartes, le dédoublonnage par id et le retrait."""
    file = FileCartes(max_cartes=MAX_CARTES)
    assert MAX_CARTES == 4

    for i in range(1, 6):
        file.ajouter(CarteOverlay(id=f"c-{i}", titre=f"Carte {i}", contenu_md=f"Contenu {i}"))

    actives = file.lister()
    assert len(actives) == 4
    # La plus récente est en tête, la plus ancienne (c-1) a été évincée.
    assert [c.id for c in actives] == ["c-5", "c-4", "c-3", "c-2"]

    # Mise à jour d'une carte existante : reste à 4 cartes et remonte en tête.
    file.ajouter(CarteOverlay(id="c-3", titre="Carte 3 MAJ", contenu_md="Maj"))
    actives = file.lister()
    assert len(actives) == 4
    assert [c.id for c in actives] == ["c-3", "c-5", "c-4", "c-2"]
    assert actives[0].titre == "Carte 3 MAJ"

    assert file.marquer_epinglee("c-3", True) is True
    assert file.lister()[0].epinglee is True
    assert file.retirer("c-4") is True
    assert [c.id for c in file.lister()] == ["c-3", "c-5", "c-2"]

    file.vider()
    assert file.lister() == []


def test_machine_etats_orbe_et_positionnement() -> None:
    """Vérifie les 6 états de l'orbe, la cadence 20/60 i/s et le placement près des cadres."""
    machine = MachineEtatsOrbe()
    assert machine.etat_effectif == "deconnecte"
    assert machine.intervalle_timer_ms() == 50

    machine.set_connecte(True)
    assert machine.etat_effectif == "repos"
    assert machine.intervalle_timer_ms() == 50

    couleurs = {k: v.couleur_hex for k, v in PRESETS_ORBE.items()}
    assert len(set(couleurs.values())) == 6

    for etat_actif in ("ecoute", "reflexion", "parole", "action"):
        assert machine.set_etat(etat_actif) == etat_actif
        assert machine.intervalle_timer_ms() == 16

    machine.set_etat("etat_inconnu")
    assert machine.etat_effectif == "repos"
    assert machine.intervalle_timer_ms() == 50

    # Mouvement vers un cadre passe temporairement à 60 i/s même au repos.
    machine.set_en_mouvement(True)
    assert machine.intervalle_timer_ms() == 16
    machine.set_en_mouvement(False)
    assert machine.intervalle_timer_ms() == 50

    # Déconnexion force "deconnecte", reconnexion restaure "repos".
    machine.set_etat("ecoute")
    assert machine.set_connecte(False) == "deconnecte"
    assert machine.set_connecte(True) == "repos"

    # Positionnement de repos en bas à droite avec marge 32 px.
    assert calculer_position_repos(0, 0, 1920, 1080, taille=110, marge=32) == (1778, 938)

    # Placement près d'un cadre actif sans chevauchement ni sortie de l'écran.
    avail = (0, 0, 1920, 1080)
    cadre = (500, 400, 180, 60)
    ox, oy = calculer_position_pres_de_cadre(cadre, avail, taille=110, ecart=24)
    assert ox >= 8 and oy >= 8
    assert ox + 110 <= 1912 and oy + 110 <= 1072
    chevauche = not (
        ox + 110 <= cadre[0] or ox >= cadre[0] + cadre[2] or oy + 110 <= cadre[1] or oy >= cadre[1] + cadre[3]
    )
    assert not chevauche
