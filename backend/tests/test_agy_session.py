"""Tests unitaires de ``SessionAgyChaude`` avec un faux exécutable ``agy`` Python NDJSON."""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from backend.llm.agy_session import SessionAgyChaude
from backend.llm.base import EngineError


def _creer_faux_agy(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Crée un script Python simulant ``agy --input-format stream-json --output-format stream-json``."""
    script = tmp_path / "fake_agy.py"
    trace = tmp_path / "trace.jsonl"
    crash_flag = tmp_path / "crashed_once.flag"

    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import sys
            from pathlib import Path

            # Comme le vrai agy : entrées et sorties en UTF-8, quel que soit l'encodage de la console.
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
            trace = Path({str(trace)!r})
            crash_flag = Path({str(crash_flag)!r})

            def log_event(obj):
                with trace.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(obj, ensure_ascii=False) + "\\n")

            log_event({{
                "kind": "start",
                "pid": os.getpid(),
                "argv": sys.argv[1:],
                "has_gemini_key": "GEMINI_API_KEY" in os.environ,
                "has_google_key": "GOOGLE_API_KEY" in os.environ,
            }})

            sys.stdout.write(json.dumps({{"event": "init", "conversation_id": "conv-chaude-1"}}) + "\\n")
            sys.stdout.flush()

            for raw_line in sys.stdin:
                line = raw_line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                log_event({{"kind": "stdin", "pid": os.getpid(), "payload": msg}})
                content = str(msg.get("message", {{}}).get("content", ""))

                if content == "CRASH_ONCE" and not crash_flag.exists():
                    crash_flag.write_text("1", encoding="utf-8")
                    sys.exit(1)

                if content == "ERREUR_FATALE":
                    sys.stdout.write(
                        json.dumps({{
                            "event": "result",
                            "result": {{
                                "status": "ERROR",
                                "error": "Échec simulé du tour",
                            }},
                        }}) + "\\n"
                    )
                    sys.stdout.flush()
                    continue

                fragments = ["Bonjour ", f"pour : {{content}}."]
                for frag in fragments:
                    sys.stdout.write(
                        json.dumps({{
                            "event": "step_update",
                            "step_update": {{
                                "step_type": "agent_response",
                                "text_delta": frag,
                            }},
                        }}) + "\\n"
                    )
                    sys.stdout.flush()

                sys.stdout.write(
                    json.dumps({{
                        "event": "result",
                        "result": {{
                            "status": "SUCCESS",
                            "conversation_id": "conv-chaude-1",
                            "response": "".join(fragments),
                            "usage": {{"input_tokens": 12, "output_tokens": 6}},
                        }},
                    }}) + "\\n"
                )
                sys.stdout.flush()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return script, trace, crash_flag


async def test_session_agy_chaude_tours_ndjson_deltas_et_arret(
    jarvis_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le format NDJSON exact, le nettoyage d'env, la continuité de PID et l'arrêt."""
    del jarvis_root
    monkeypatch.setenv("GEMINI_API_KEY", "secret-qui-doit-disparaitre")
    monkeypatch.setenv("GOOGLE_API_KEY", "autre-secret")

    script, trace, _ = _creer_faux_agy(tmp_path)
    session = SessionAgyChaude(
        modele="gemini-4-hydrogen-low",
        inactivite_s=60.0,
        executable=str(script),
    )

    try:
        await session.demarrer()
        assert session.est_active
        assert session.tours_sur_processus == 0

        deltas_1 = [d async for d in session.envoyer("Salut Jarvis")]
        assert deltas_1 == ["Bonjour ", "pour : Salut Jarvis."]
        assert session.tours_sur_processus == 1
        assert session.conversation_id == "conv-chaude-1"

        deltas_2 = [d async for d in session.envoyer("Deuxième question")]
        assert deltas_2 == ["Bonjour ", "pour : Deuxième question."]
        assert session.tours_sur_processus == 2
    finally:
        await session.arreter()

    assert not session.est_active

    evenements = [
        json.loads(ligne)
        for ligne in trace.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]
    starts = [e for e in evenements if e["kind"] == "start"]
    stdins = [e for e in evenements if e["kind"] == "stdin"]

    assert len(starts) == 1
    assert starts[0]["has_gemini_key"] is False
    assert starts[0]["has_google_key"] is False
    argv = starts[0]["argv"]
    assert "--sandbox" in argv
    assert "--disable-slash-commands" in argv
    assert "--input-format" in argv
    assert "--output-format" in argv
    assert "--model" in argv
    assert "gemini-4-hydrogen-low" in argv

    assert len(stdins) == 2
    assert stdins[0]["pid"] == stdins[1]["pid"]
    assert stdins[0]["payload"] == {
        "event": "user",
        "message": {"role": "user", "content": "Salut Jarvis"},
    }
    assert stdins[1]["payload"] == {
        "event": "user",
        "message": {"role": "user", "content": "Deuxième question"},
    }


async def test_session_agy_chaude_redemarrage_auto_si_processus_meurt(
    jarvis_root: Path,
    tmp_path: Path,
) -> None:
    """Si le processus meurt pendant un tour ou entre deux tours, il redémarre automatiquement."""
    del jarvis_root
    script, trace, _ = _creer_faux_agy(tmp_path)
    session = SessionAgyChaude(inactivite_s=60.0, executable=str(script))

    try:
        await session.demarrer()
        res_1 = [d async for d in session.envoyer("CRASH_ONCE")]
        assert res_1 == ["Bonjour ", "pour : CRASH_ONCE."]
        assert session.tours_sur_processus == 1

        # Tue explicitement le processus entre deux tours puis envoie un tour suivant.
        assert session._proc is not None
        session._proc.kill()
        session._proc.wait(timeout=2)

        res_2 = [d async for d in session.envoyer("Après mort")]
        assert res_2 == ["Bonjour ", "pour : Après mort."]
        assert session.tours_sur_processus == 1
    finally:
        await session.arreter()

    evenements = [
        json.loads(ligne)
        for ligne in trace.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]
    pids_start = [e["pid"] for e in evenements if e["kind"] == "start"]
    assert len(pids_start) == 3
    assert len(set(pids_start)) == 3


async def test_session_agy_chaude_inactivite_verrou_et_erreur(
    jarvis_root: Path,
    tmp_path: Path,
) -> None:
    """Vérifie l'arrêt après inactivité, le verrou un tour à la fois et la remontée d'erreur."""
    del jarvis_root
    script, _, _ = _creer_faux_agy(tmp_path)
    session = SessionAgyChaude(inactivite_s=0.15, executable=str(script))

    try:
        await session.demarrer()
        assert session.est_active

        async def _collecter(texte: str) -> str:
            return "".join([d async for d in session.envoyer(texte)])

        r1, r2 = await asyncio.gather(_collecter("Un"), _collecter("Deux"))
        assert r1 == "Bonjour pour : Un."
        assert r2 == "Bonjour pour : Deux."

        with pytest.raises(EngineError, match="Échec simulé du tour"):
            async for _ in session.envoyer("ERREUR_FATALE"):
                pass

        await asyncio.sleep(0.35)
        assert not session.est_active
    finally:
        await session.arreter()
