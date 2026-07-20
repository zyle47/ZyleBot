"""HTML page routes — the chat app shell plus static product pages.

All JSON/SSE endpoints live in app/main.py; this module only renders Jinja
templates. New footer pages (resources/about) get their routes here.
"""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# --- Product pages (footer "Product" column) --------------------------------

@router.get("/product/agent-loop")
async def product_agent_loop(request: Request):
    return templates.TemplateResponse(request, "product/agent_loop.html")


@router.get("/product/tool-arsenal")
async def product_tool_arsenal(request: Request):
    return templates.TemplateResponse(request, "product/tool_arsenal.html")


@router.get("/product/model-control")
async def product_model_control(request: Request):
    return templates.TemplateResponse(request, "product/model_control.html")


@router.get("/product/approval-gate")
async def product_approval_gate(request: Request):
    return templates.TemplateResponse(request, "product/approval_gate.html")


# --- Resource pages (footer "Resources" column) ----------------------------

@router.get("/resources/readme")
async def resources_readme(request: Request):
    return templates.TemplateResponse(request, "resources/readme.html")


@router.get("/style-lab")
async def style_lab(request: Request):
    return templates.TemplateResponse(request, "style_lab.html")


# --- About pages ------------------------------------------------------------

@router.get("/about/how-it-works")
async def about_how_it_works(request: Request):
    return templates.TemplateResponse(request, "about/how_it_works.html")


# --- Game -------------------------------------------------------------------

def _default_initials() -> str:
    # Default arcade initials from USER_NAME ("Nemanja" -> "NEM"); USER_NAME
    # defaults to "", so fall back to "ZYL".
    return "".join(c for c in settings.user_name.upper() if c.isalnum())[:3] or "ZYL"


@router.get("/game")
async def game(request: Request, embed: bool = False):
    # embed=1 renders the board-only template for arena iframes / pop-outs;
    # absent/false keeps the ordinary full page. Same initials for both.
    template = "game_embed.html" if embed else "game.html"
    return templates.TemplateResponse(request, template, {"default_initials": _default_initials()})


@router.get("/game/arena")
async def game_arena(request: Request):
    return templates.TemplateResponse(request, "game_arena.html", {"default_initials": _default_initials()})
