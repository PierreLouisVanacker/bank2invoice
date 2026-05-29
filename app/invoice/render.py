r"""Environnement Jinja2 configuré pour LaTeX.

Conflit habituel : LaTeX utilise `{`, `}`, `{%`, `%}` partout. On change donc
les délimiteurs Jinja2 pour ne pas se marcher sur les pieds :

    Jinja2 standard       →     Jinja2 LaTeX-safe (ici)
    {{ variable }}        →     \VAR{variable}
    {% if x %}            →     \BLOCK{ if x }
    {# comment #}         →     \#{ comment }

L'autoescape est désactivé globalement, mais le filtre `latex_escape` est appliqué
automatiquement à toute variable simple via un filtre par défaut (voir la classe).
"""

from pathlib import Path
import shutil
import subprocess
import tempfile

import jinja2

from app.config import settings
from app.invoice.latex_escape import latex_escape

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_env() -> jinja2.Environment:
    env = jinja2.Environment(
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    )
    env.filters["latex_escape"] = latex_escape
    return env


# Singleton réutilisable
latex_env = _build_env()


class LatexCompileError(RuntimeError):
    """Levée quand la compilation LaTeX échoue."""

    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def render_template(template_name: str, context: dict) -> str:
    """Rend un template LaTeX (source .tex) avec le contexte donné."""
    template = latex_env.get_template(template_name)
    return template.render(**context)


def compile_facture(context: dict, output_pdf: Path) -> Path:
    """Génère un PDF de facture à partir du contexte.

    Étapes :
      1. Rend header / main / footer en .tex via Jinja2
      2. Écrit les 3 fichiers dans un répertoire temporaire
         (le main fait \\input{facture_header.tex} et {facture_footer.tex})
      3. Compile avec xelatex (2 passes pour les références/positions)
      4. Copie le PDF résultat vers output_pdf

    Lève LatexCompileError si la compilation échoue.
    """
    latex_binary = shutil.which(settings.latex_binary)
    if latex_binary is None:
        raise LatexCompileError(
            f"Binaire LaTeX introuvable : '{settings.latex_binary}'. "
            f"Installe une distribution LaTeX (TeX Live, MiKTeX) et vérifie le PATH."
        )

    header_tex = render_template("facture_header.tex.j2", context)
    main_tex = render_template("facture_main.tex.j2", context)
    footer_tex = render_template("facture_footer.tex.j2", context)

    with tempfile.TemporaryDirectory(prefix="b2i_latex_") as tmpdir:
        tmp = Path(tmpdir)
        # Le main fait \input{facture_header.tex} et \input{facture_footer.tex}
        # (sans extension .j2), donc on écrit sous ces noms.
        (tmp / "facture_header.tex").write_text(header_tex, encoding="utf-8")
        (tmp / "facture_footer.tex").write_text(footer_tex, encoding="utf-8")
        main_path = tmp / "facture_main.tex"
        main_path.write_text(main_tex, encoding="utf-8")

        # Compilation (2 passes pour stabiliser positions/totaux)
        log = ""
        for pass_num in range(2):
            proc = subprocess.run(
                [
                    latex_binary,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(tmp),
                    str(main_path),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp),
            )
            log = proc.stdout + "\n" + proc.stderr
            if proc.returncode != 0:
                raise LatexCompileError(
                    f"Échec compilation LaTeX (passe {pass_num + 1}, "
                    f"code {proc.returncode}). Voir le log.",
                    log=log,
                )

        produced_pdf = tmp / "facture_main.pdf"
        if not produced_pdf.exists():
            raise LatexCompileError(
                "Compilation terminée mais aucun PDF produit.", log=log
            )

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(produced_pdf, output_pdf)

    return output_pdf
