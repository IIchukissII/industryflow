# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The read-only analytics server (ADR-0011 dec 5): rendered reports and their parameters, nothing else.

The hub runs it as the single-user server of the analytics profile (``JUPYTERHUB_SINGLEUSER_APP``), so
the hub's authentication wraps every route. It serves an allowlist of routes: the server status the
hub reads, the identity and CSP-report endpoints, and the reports. No kernel, session, contents,
file, terminal or extension route exists, so its user sends no code anywhere: a report is executed
here, with the parameters its ``parameters`` cell declares, each taken as a value of that cell's
type and injected as a literal, and comes back as HTML without its inputs.
"""
from __future__ import annotations

import ast
import html
import math
import os
import re
from pathlib import Path
from typing import Any

import nbformat
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.serverapp import ServerApp
from jupyter_server.services.contents.filemanager import FileContentsManager
from jupyter_server.utils import url_path_join
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from tornado import web
from traitlets import default

REPORTS_DIR_ENV = "IF_REPORTS_DIR"
DEFAULT_REPORTS_DIR = "/opt/industryflow/reports"
# How long one report may run before it is stopped.
REPORT_TIMEOUT_SECONDS = 300
MAX_TEXT_PARAMETER = 200
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ReportDefect(Exception):
    """A curated report whose parameters cell is not plain literal assignments."""


class ParameterRefused(ValueError):
    """A request parameter the report does not declare, or a value not of its declared type."""


def reports_dir() -> Path:
    return Path(os.environ.get(REPORTS_DIR_ENV, DEFAULT_REPORTS_DIR))


def report_path(name: str) -> Path | None:
    """The curated report a name names, or None: a plain name, never a path."""
    if not _NAME.match(name):
        return None
    path = reports_dir() / f"{name}.ipynb"
    return path if path.is_file() else None


def report_names() -> list[str]:
    root = reports_dir()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.ipynb") if _NAME.match(p.stem))


def _parameters_cell(nb) -> int | None:
    for i, cell in enumerate(nb.cells):
        if cell.cell_type == "code" and "parameters" in cell.get("metadata", {}).get("tags", []):
            return i
    return None


def parameters_of(nb) -> dict[str, Any]:
    """The report's parameters and their defaults: each statement of its ``parameters`` cell is one
    name assigned a literal number, text or truth value."""
    index = _parameters_cell(nb)
    if index is None:
        return {}
    try:
        tree = ast.parse(nb.cells[index].source)
    except SyntaxError as e:
        raise ReportDefect(f"its parameters cell does not parse: {e.msg}") from None
    found: dict[str, Any] = {}
    for stmt in tree.body:
        if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant) and type(stmt.value.value) in (bool, int, float, str)):
            raise ReportDefect("its parameters cell may only assign a literal number, text or truth value to a name")
        found[stmt.targets[0].id] = stmt.value.value
    return found


def coerce(name: str, raw: str, default_value: Any) -> Any:
    """``raw`` as a value of the type of the parameter's default, or ParameterRefused."""
    kind = type(default_value)
    try:
        if kind is bool:
            if raw not in ("true", "false"):
                raise ValueError
            return raw == "true"
        if kind is int:
            return int(raw)
        if kind is float:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError
            return value
    except ValueError:
        raise ParameterRefused(f"parameter {name!r} takes a {kind.__name__}, not {raw!r}") from None
    if len(raw) > MAX_TEXT_PARAMETER:
        raise ParameterRefused(f"parameter {name!r} is longer than {MAX_TEXT_PARAMETER} characters")
    return raw


def bind_parameters(nb, given: dict[str, str]) -> dict[str, Any]:
    """Inject the given parameters as literals after the parameters cell; returns the bound values."""
    declared = parameters_of(nb)
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise ParameterRefused(f"the report takes no parameter {', '.join(map(repr, unknown))}")
    bound = {name: coerce(name, raw, declared[name]) for name, raw in given.items()}
    if bound:
        # repr of a bool, int, finite float or str is a Python literal of that value: no code.
        source = "\n".join(f"{name} = {value!r}" for name, value in bound.items())
        cell = nbformat.v4.new_code_cell(source, metadata={"tags": ["injected-parameters"]})
        nb.cells.insert(_parameters_cell(nb) + 1, cell)
    return {**declared, **bound}


async def render(path: Path, given: dict[str, str]) -> str:
    """The report executed with the given parameters, as HTML without its inputs."""
    nb = nbformat.read(path, as_version=4)
    bind_parameters(nb, given)
    client = NotebookClient(nb, timeout=REPORT_TIMEOUT_SECONDS, kernel_name="python3",
                            resources={"metadata": {"path": str(path.parent)}})
    await client.async_execute()
    exporter = HTMLExporter(exclude_input=True, exclude_input_prompt=True, exclude_output_prompt=True)
    body, _ = exporter.from_notebook_node(nb)
    return body


class ReportHandler(JupyterHandler):
    """Base for the report routes: every one requires the hub's authenticated user."""

    def base(self) -> str:
        return url_path_join(self.base_url, "reports")


class ReportListHandler(ReportHandler):
    @web.authenticated
    def get(self):
        rows = []
        for name in report_names():
            try:
                params = parameters_of(nbformat.read(report_path(name), as_version=4))
            except ReportDefect as e:
                rows.append(f"<li>{html.escape(name)}: not available ({html.escape(str(e))})</li>")
                continue
            fields = "".join(
                f'<label>{html.escape(k)} <input name="{html.escape(k)}" value="{html.escape(str(v).lower() if isinstance(v, bool) else str(v))}"></label> '
                for k, v in params.items())
            action = html.escape(url_path_join(self.base(), name))
            rows.append(f'<li><form method="get" action="{action}"><b>{html.escape(name)}</b> {fields}'
                        f'<button type="submit">Show</button></form></li>')
        listing = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>No reports are published here yet.</p>"
        self.finish(f"<!doctype html><title>Reports</title><h1>Reports</h1>{listing}")


class ReportRenderHandler(ReportHandler):
    @web.authenticated
    async def get(self, name: str):
        path = report_path(name)
        if path is None:
            raise web.HTTPError(404, "no such report")
        given = {k: self.get_argument(k) for k in self.request.arguments}
        try:
            body = await render(path, given)
        except (ParameterRefused, ReportDefect) as e:
            raise web.HTTPError(400, str(e)) from None
        self.set_header("Cache-Control", "no-store")
        self.finish(body)


class NoFilesContentsManager(FileContentsManager):
    """The contents manager the server needs internally, with none of its routes (no ``/files``)."""

    def get_extra_handlers(self):
        return []


class ReportServerApp(ServerApp):
    """A server whose only routes are its services' and the reports'."""

    name = "industryflow-reports"
    # Status (the hub reads it), identity, CSP reports. Nothing that reaches a kernel or a file.
    default_services = ("api", "auth", "security")

    @default("contents_manager_class")
    def _default_contents_manager_class(self):
        return NoFilesContentsManager

    @default("terminals_enabled")
    def _default_terminals_enabled(self):
        return False

    def find_server_extensions(self) -> None:
        # No extension: one installed beside this server (a terminal, a renderer) adds its routes.
        self.jpserver_extensions = {}

    def init_webapp(self):
        # The landing page is the reports, whatever the hub's wrapper or its spawner asked for.
        self.default_url = url_path_join(self.base_url, "reports")
        super().init_webapp()
        base = self.web_app.settings["base_url"]
        # A rule added later is matched before the catch-all 404 of the services above.
        self.web_app.add_handlers(".*$", [
            (url_path_join(base, "/reports/?"), ReportListHandler),
            (url_path_join(base, r"/reports/([A-Za-z0-9_-]+)"), ReportRenderHandler),
        ])
