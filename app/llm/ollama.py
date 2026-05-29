"""Client LLM Ollama : appel HTTP local à http://localhost:11434.

Utilise l'endpoint `/api/chat` avec `format: <json_schema>` pour contraindre
la sortie. Ollama supporte les JSON schemas depuis fin 2024.

Si Ollama n'est pas joignable ou retourne quelque chose d'invalide, on retourne
un EmetteurExtrait avec type='inconnu' pour ne pas casser le pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from app.llm.base import EMETTEUR_JSON_SCHEMA, EmetteurExtrait

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """Tu extrais l'émetteur (nom et prénom, ou raison sociale) à \
partir d'un libellé de virement bancaire français.

RÈGLES IMPORTANTES :
- Tu ignores TOUT ce qui n'est pas le nom : les mots "Virement", "Vir Inst", \
"VIR SEPA", "de", "DE", les références, les motifs, les codes, les dates.
- Si c'est une personne physique : extrais civilité (M./Mme/Mlle), prénom, nom \
de famille. Le nom de famille est souvent en MAJUSCULES, le prénom souvent \
capitalisé.
- Si c'est une société/organisme (SARL, SAS, SCI, association, administration, \
ex: "Keter Conseil", "CARPA") : raison_sociale.
- Si tu ne peux pas trancher → type="inconnu".
- Tu réponds UNIQUEMENT en JSON conforme au schéma fourni, rien d'autre.

EXEMPLES :
  "Virement Vir Inst de Mme Khelili Houda" →
  {"type":"personne","civilite":"Mme","prenom":"Houda","nom":"Khelili"}

  "Virement Vir Inst de Keter Conseil" →
  {"type":"societe","raison_sociale":"Keter Conseil"}

  "Virement Carpa / 118071" →
  {"type":"societe","raison_sociale":"CARPA"}

  "Virement Vir Inst de M Aghiles Benmeziane" →
  {"type":"personne","civilite":"M.","prenom":"Aghiles","nom":"Benmeziane"}
"""


@dataclass
class OllamaClient:
    """Implémentation LLMClient via Ollama local."""

    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout_seconds: float = 60.0

    def extract_emetteur(self, libelle: str) -> EmetteurExtrait:
        payload = {
            "model": self.model,
            "stream": False,
            "format": EMETTEUR_JSON_SCHEMA,
            "options": {
                "temperature": 0.0,
                "num_predict": 200,
            },
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Libellé : {libelle}"},
            ],
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.host}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Ollama call failed for libelle=%r: %s", libelle, e)
            return EmetteurExtrait(type="inconnu")

        content = data.get("message", {}).get("content", "")
        if not content:
            logger.warning("Ollama empty response for libelle=%r", libelle)
            return EmetteurExtrait(type="inconnu")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(
                "Ollama returned non-JSON for libelle=%r: %s — content=%r",
                libelle, e, content[:200],
            )
            return EmetteurExtrait(type="inconnu")

        return _to_emetteur(parsed)


def _to_emetteur(d: dict) -> EmetteurExtrait:
    """Convertit le dict JSON en EmetteurExtrait, en validant les champs."""
    raw_type = d.get("type", "inconnu")
    if raw_type not in ("personne", "societe", "inconnu"):
        raw_type = "inconnu"

    def _str_or_none(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return EmetteurExtrait(
        type=raw_type,
        civilite=_str_or_none(d.get("civilite")),
        prenom=_str_or_none(d.get("prenom")),
        nom=_str_or_none(d.get("nom")),
        raison_sociale=_str_or_none(d.get("raison_sociale")),
        # Le LLM ne renvoie pas de confidence — on met une valeur informative
        # selon le type retourné.
        confidence=0.9 if raw_type != "inconnu" else 0.0,
    )
