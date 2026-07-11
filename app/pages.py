"""HTML page routes — the chat app shell plus static product pages.

All JSON/SSE endpoints live in app/main.py; this module only renders Jinja
templates. New footer pages (resources/about) get their routes here.
"""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

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
