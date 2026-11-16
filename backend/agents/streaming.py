"""Extraction incrémentale en streaming du champ ``"reponse"`` d'un flux JSON LLM."""

import re

_FENCE_JSON_RE = re.compile(r"^\s*```(?:json)?\s*\r?\n?", re.IGNORECASE)
_FENCE_AUTRE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]+\s*\r?\n")


class ExtracteurReponse:
    """Extrait au fil de l'eau la valeur de la clé ``"reponse"`` depuis des deltas LLM.

    - Si le flux commence par ``{`` ou par un bloc ```` ```json ```` suivi de ``{``,
      décode caractère par caractère la chaîne JSON associée à la clé ``"reponse"`` de
      premier niveau (gère ``\\"``, ``\\n``, ``\\t``, ``\\\\``, ``\\uXXXX`` et l'UTF-8).
    - Si le premier caractère non blanc n'est ni ``{`` ni un bloc ```` ```json ````,
      transmet les deltas bruts tels quels sans perte.
    """

    def __init__(self) -> None:
        """Initialise l'automate d'extraction dans l'état indéterminé."""
        self._mode: str = "indetermine"  # "indetermine" | "texte" | "json"
        self._pre_buffer: str = ""
        self._texte_complet: list[str] = []
        self._reponse_extraite: list[str] = []

        # État de l'automate JSON
        self._debut_json_trouve: bool = False
        self._profondeur: int = 0
        self._dans_chaine: bool = False
        self._echappe: bool = False
        self._unicode_restant: int = 0
        self._unicode_hex: list[str] = []

        # Suivi des clés/valeurs au niveau racine (profondeur == 1)
        self._attente_objet: str = "cle"  # "cle" | "deux_points" | "valeur" | "virgule"
        self._role_chaine: str = "aucun"  # "cle" | "reponse" | "autre"
        self._cle_courante: list[str] = []
        self._derniere_cle: str = ""
        self._reponse_terminee: bool = False

    @property
    def texte_complet(self) -> str:
        """Retourne l'intégralité du texte brut reçu depuis le début."""
        return "".join(self._texte_complet)

    @property
    def reponse_extraite(self) -> str:
        """Retourne l'intégralité du texte extrait jusqu'ici."""
        return "".join(self._reponse_extraite)

    @property
    def est_json(self) -> bool | None:
        """Indique si le flux est détecté comme JSON (``True``), texte (``False``) ou indécis (``None``)."""
        if self._mode == "json":
            return True
        if self._mode == "texte":
            return False
        return None

    def alimenter(self, delta: str) -> str:
        """Ingère un fragment brut du modèle et retourne le nouveau texte à afficher.

        Args:
            delta: Morceau de texte brut émis par le moteur LLM.

        Returns:
            Texte décodé à diffuser immédiatement (chaîne vide si rien n'est encore prêt).
        """
        if not delta:
            return ""

        self._texte_complet.append(delta)

        if self._mode == "texte":
            self._reponse_extraite.append(delta)
            return delta

        if self._mode == "indetermine":
            self._pre_buffer += delta
            sortie_detection = self._detecter_mode()
            if sortie_detection is not None:
                return sortie_detection
            return ""

        return self._consommer_json(delta)

    def feed(self, delta: str) -> str:
        """Alias anglophone de ``alimenter``."""
        return self.alimenter(delta)

    def __call__(self, delta: str) -> str:
        """Permet d'utiliser l'instance directement comme callback synchrone."""
        return self.alimenter(delta)

    def _detecter_mode(self) -> str | None:
        """Détermine si ``_pre_buffer`` correspond à du JSON, un bloc ```json ou du texte brut."""
        buf = self._pre_buffer
        strip_buf = buf.lstrip()
        if not strip_buf:
            return None

        premier = strip_buf[0]
        if premier == "{":
            self._mode = "json"
            self._pre_buffer = ""
            return self._consommer_json(strip_buf)

        if premier == "`":
            # Attend d'avoir au moins 3 caractères pour confirmer ```
            if len(strip_buf) < 3:
                return None
            if not strip_buf.startswith("```"):
                self._mode = "texte"
                self._pre_buffer = ""
                self._reponse_extraite.append(buf)
                return buf

            # Si on a déjà une accolade ouvrante après ``` ou ```json
            match_fence = _FENCE_JSON_RE.match(buf)
            if match_fence:
                reste = buf[match_fence.end() :]
                reste_strip = reste.lstrip()
                if not reste_strip:
                    return None
                if reste_strip[0] == "{":
                    self._mode = "json"
                    self._pre_buffer = ""
                    return self._consommer_json(reste_strip)
                self._mode = "texte"
                self._pre_buffer = ""
                self._reponse_extraite.append(buf)
                return buf

            # Si une autre langue de bloc code est explicitement indiquée (ex: ```python\n)
            if _FENCE_AUTRE_RE.match(buf):
                self._mode = "texte"
                self._pre_buffer = ""
                self._reponse_extraite.append(buf)
                return buf

            if len(strip_buf) > 24:
                self._mode = "texte"
                self._pre_buffer = ""
                self._reponse_extraite.append(buf)
                return buf
            return None

        # Premier caractère non blanc ni '{' ni '`' -> mode texte brut
        self._mode = "texte"
        self._pre_buffer = ""
        self._reponse_extraite.append(buf)
        return buf

    def _decoder_caractere_chaine(self, ch: str) -> tuple[str, bool]:
        """Décode un caractère à l'intérieur d'un littéral de chaîne JSON.

        Returns:
            Tuple ``(caractere_decode, fin_de_chaine)``.
        """
        if self._unicode_restant > 0:
            self._unicode_hex.append(ch)
            self._unicode_restant -= 1
            if self._unicode_restant == 0:
                hex_str = "".join(self._unicode_hex)
                self._unicode_hex.clear()
                try:
                    return chr(int(hex_str, 16)), False
                except ValueError:
                    return "", False
            return "", False

        if self._echappe:
            self._echappe = False
            if ch == "u":
                self._unicode_restant = 4
                self._unicode_hex.clear()
                return "", False
            table_echappements = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "b": "\b",
                "f": "\f",
            }
            return table_echappements.get(ch, ch), False

        if ch == "\\":
            self._echappe = True
            return "", False

        if ch == '"':
            return "", True

        return ch, False

    def _consommer_json(self, fragment: str) -> str:
        """Avance l'automate JSON sur ``fragment`` et retourne les caractères extraits de ``"reponse"``."""
        if self._reponse_terminee:
            return ""

        emis: list[str] = []

        for ch in fragment:
            if not self._debut_json_trouve:
                if ch == "{":
                    self._debut_json_trouve = True
                    self._profondeur = 1
                    self._attente_objet = "cle"
                continue

            if self._dans_chaine:
                decode, fin_chaine = self._decoder_caractere_chaine(ch)
                if fin_chaine:
                    self._dans_chaine = False
                    if self._role_chaine == "cle":
                        self._derniere_cle = "".join(self._cle_courante)
                        self._cle_courante.clear()
                        self._attente_objet = "deux_points"
                    elif self._role_chaine == "reponse":
                        self._reponse_terminee = True
                        self._attente_objet = "virgule"
                    elif self._profondeur == 1:
                        self._attente_objet = "virgule"
                    self._role_chaine = "aucun"
                else:
                    if decode:
                        if self._role_chaine == "cle":
                            self._cle_courante.append(decode)
                        elif self._role_chaine == "reponse":
                            emis.append(decode)
                continue

            # Hors chaîne JSON
            if ch in " \t\r\n":
                continue

            if ch == '"':
                self._dans_chaine = True
                if self._profondeur == 1 and self._attente_objet == "cle":
                    self._role_chaine = "cle"
                    self._cle_courante.clear()
                elif (
                    self._profondeur == 1
                    and self._attente_objet == "valeur"
                    and self._derniere_cle == "reponse"
                ):
                    self._role_chaine = "reponse"
                else:
                    self._role_chaine = "autre"
                continue

            if self._profondeur == 1:
                if ch == ":" and self._attente_objet == "deux_points":
                    self._attente_objet = "valeur"
                    continue
                if ch == ",":
                    self._attente_objet = "cle"
                    self._derniere_cle = ""
                    continue

            if ch in "{[":
                self._profondeur += 1
            elif ch in "}]":
                self._profondeur = max(0, self._profondeur - 1)

        sortie = "".join(emis)
        if sortie:
            self._reponse_extraite.append(sortie)
        return sortie
