"""Serveur TTS local Kyutai (français/anglais), API compatible OpenAI ``/v1/audio/speech``.

À lancer avec l'interpréteur du venv dédié (G:/ai-cli/kyutai-tts) : le modèle est chargé une
seule fois sur le GPU, puis chaque requête renvoie un WAV 24 kHz mono. Aucune clé API, tout en local.
Licence du modèle : CC-BY 4.0 (Kyutai).
"""

from __future__ import annotations

import argparse
import io
import threading
import time
import wave

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

VOIX_DEFAUT = "cml-tts/fr/12080_11650_000047-0001.wav"
ALIAS_VOIX = {
    "defaut": VOIX_DEFAUT,
    "fr-FR-HenriNeural": VOIX_DEFAUT,
    "fr-FR-DeniseNeural": "cml-tts/fr/10177_10625_000134-0003.wav",
}


class RequeteParole(BaseModel):
    """Corps de requête compatible OpenAI."""

    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str | None = None


class Moteur:
    """Charge Kyutai TTS et synthétise sérialisé (un seul tour GPU à la fois)."""

    def __init__(self, device: str) -> None:
        import torch
        from moshi.models.loaders import CheckpointInfo
        from moshi.models.tts import DEFAULT_DSM_TTS_REPO, TTSModel

        self._torch = torch
        info = CheckpointInfo.from_hf_repo(DEFAULT_DSM_TTS_REPO)
        self.tts = TTSModel.from_checkpoint_info(info, n_q=32, temp=0.6, device=device)
        self.verrou = threading.Lock()
        self._voix: dict[str, object] = {}

    def _condition(self, voix: str):
        nom = ALIAS_VOIX.get(voix, voix) if voix else VOIX_DEFAUT
        if nom not in self._voix:
            chemin = nom if nom.endswith(".safetensors") else self.tts.get_voice_path(nom)
            self._voix[nom] = self.tts.make_condition_attributes([chemin], cfg_coef=2.0)
        return self._voix[nom]

    def synthetiser(self, texte: str, voix: str) -> bytes:
        """Retourne un WAV 16 bits mono du texte donné."""
        torch = self._torch
        with self.verrou, torch.no_grad():
            cond = self._condition(voix)
            entrees = self.tts.prepare_script([texte], padding_between=1)
            resultat = self.tts.generate([entrees], [cond])
            morceaux = []
            with self.tts.mimi.streaming(1):
                for frame in resultat.frames[self.tts.delay_steps :]:
                    pcm = self.tts.mimi.decode(frame[:, 1:, :]).cpu().numpy()
                    morceaux.append(np.clip(pcm[0, 0], -1, 1))
        audio = np.concatenate(morceaux) if morceaux else np.zeros(0, dtype=np.float32)
        tampon = io.BytesIO()
        with wave.open(tampon, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(self.tts.mimi.sample_rate))
            w.writeframes((audio * 32767).astype("<i2").tobytes())
        return tampon.getvalue()


def creer_app(device: str) -> FastAPI:
    """Construit l'application FastAPI (modèle chargé au démarrage)."""
    app = FastAPI(title="Kyutai TTS local")
    debut = time.time()
    moteur = Moteur(device)
    for _ in range(2):  # chauffe : capture des graphes CUDA (la 1re requête dure ~20 s sinon)
        moteur.synthetiser("Bonjour.", "")

    @app.get("/health")
    def sante() -> dict[str, object]:
        return {"ok": True, "modele": "kyutai/tts-1.6b-en_fr", "demarre_il_y_a_s": round(time.time() - debut)}

    @app.post("/v1/audio/speech")
    def parole(req: RequeteParole) -> Response:
        texte = (req.input or "").strip()
        if not texte:
            raise HTTPException(400, "Texte vide")
        if len(texte) > 1200:
            raise HTTPException(400, "Texte trop long (1200 caractères max)")
        wav = moteur.synthetiser(texte, (req.voice or "").strip())
        return Response(content=wav, media_type="audio/wav")

    return app


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description="Serveur TTS Kyutai local")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    uvicorn.run(creer_app(args.device), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
