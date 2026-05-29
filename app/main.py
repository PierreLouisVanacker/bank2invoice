"""App FastAPI principale.

Lot 6 : multi-utilisateurs avec authentification.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse

from app.db import init_db
from app.routers import auth, clients, factures, profil, releves, transactions

app = FastAPI(
    title="bank2invoice",
    description="Générateur de factures depuis relevés bancaires",
    version="0.6.0",
)

# Routes publiques (pas d'auth)
app.include_router(auth.router)

# Routes protégées
app.include_router(profil.router)
app.include_router(releves.router)
app.include_router(transactions.router)
app.include_router(clients.router)
app.include_router(factures.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def auth_redirect_handler(request: Request, exc: HTTPException):
    """Si un endpoint lève 401 (pas connecté), on redirige vers /login pour
    les requêtes navigateur. Les requêtes API/HTMX reçoivent le 401 tel quel."""
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        # HTMX : on demande au client de rediriger vers /login
        if "hx-request" in request.headers:
            return RedirectResponse(url="/login", status_code=303, headers={"HX-Redirect": "/login"})
        # Navigateur classique
        accept = request.headers.get("accept", "")
        if "text/html" in accept or accept == "":
            return RedirectResponse(url="/login", status_code=303)
    # Pour tous les autres cas (404, etc.), comportement par défaut
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", include_in_schema=False)
def index(request: Request):
    """Page d'accueil : redirige vers /releves (qui exigera login)."""
    return RedirectResponse(url="/releves", status_code=302)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
