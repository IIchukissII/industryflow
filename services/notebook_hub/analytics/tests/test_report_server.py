# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The analytics server serves rendered reports and nothing that runs a user's code (ADR-0011 dec 5).

The routes are read from the server as the hub runs it (``make_singleuser_app``), so a route the hub's
wrapper or an installed extension adds is seen too; a report is executed on a real kernel.
"""
import asyncio
import os
import sys
from pathlib import Path

import nbformat
import pytest
from tornado.httputil import HTTPHeaders, HTTPServerRequest, RequestStartLine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report_server  # noqa: E402

PREFIX = "/user/u/"
FRAME = "frame-ancestors https://app.example.com"
HUB_ENV = {
    "JUPYTERHUB_API_TOKEN": "t", "JUPYTERHUB_CLIENT_ID": "jupyterhub-user-u",
    "JUPYTERHUB_API_URL": "http://127.0.0.1:1/hub/api", "JUPYTERHUB_BASE_URL": "/",
    "JUPYTERHUB_SERVICE_PREFIX": PREFIX, "JUPYTERHUB_USER": "u",
    "JUPYTERHUB_OAUTH_CALLBACK_URL": PREFIX + "oauth_callback",
}


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    saved = dict(os.environ)
    os.environ.update(HUB_ENV, HOME=str(home))
    from jupyterhub.singleuser import make_singleuser_app
    server = make_singleuser_app(report_server.ReportServerApp)()
    # The frame policy as the hub's spawner passes it (jupyterhub_config.py, c.Spawner.args).
    server.initialize(argv=["--port=0", "--ip=127.0.0.1", f"--ServerApp.root_dir={home}",
                            "--ServerApp.tornado_settings={'headers': {'Content-Security-Policy': \"" + FRAME + "\"}}"])
    try:
        yield server
    finally:
        server.http_server.stop()
        os.environ.clear()
        os.environ.update(saved)


def _handler(app, path):
    request = HTTPServerRequest(start_line=RequestStartLine("GET", path, "HTTP/1.1"),
                                headers=HTTPHeaders({"Host": "notebooks.example"}))
    return app.web_app.find_handler(request).handler_class.__name__


@pytest.mark.parametrize("path", [
    "api/kernels", "api/kernels/k/channels", "api/sessions", "api/contents", "api/contents/x.ipynb",
    "files/x.ipynb", "api/terminals", "terminals/websocket/1", "api/kernelspecs", "kernelspecs/python3/x",
    "api/nbconvert", "nbconvert/html/x.ipynb", "api/config/x", "edit/x", "view/x", "tree",
    "lab", "voila", "voila/render/x.ipynb", "api/events/subscribe", "api/shutdown",
])
def test_no_route_reaches_a_kernel_a_file_or_a_terminal(app, path):
    assert _handler(app, PREFIX + path) == "Template404"


def test_the_reports_status_and_hub_callback_are_served(app):
    assert _handler(app, PREFIX + "reports") == "ReportListHandler"
    assert _handler(app, PREFIX + "reports/overview") == "ReportRenderHandler"
    assert _handler(app, PREFIX + "api/status") == "APIStatusHandler"
    assert _handler(app, PREFIX + "oauth_callback") == "OAuthCallbackHandler"


def test_over_http_code_routes_are_absent_and_reports_need_the_hub_with_its_frame_policy(app):
    from tornado.httpclient import AsyncHTTPClient
    from tornado.httpserver import HTTPServer
    from tornado.testing import bind_unused_port

    async def fetch_all():
        sock, port = bind_unused_port()
        server = HTTPServer(app.web_app)
        server.add_sockets([sock])
        try:
            client = AsyncHTTPClient()
            return {path: await client.fetch(f"http://127.0.0.1:{port}{PREFIX}{path}", follow_redirects=False,
                                             raise_error=False)
                    for path in ("reports", "reports/overview", "api/kernels", "api/contents")}
        finally:
            server.stop()

    answers = asyncio.run(fetch_all())
    for path in ("reports", "reports/overview"):
        assert answers[path].code == 302 and "/hub/" in answers[path].headers["Location"], path
        assert answers[path].headers["Content-Security-Policy"].startswith(FRAME), path
    assert answers["api/kernels"].code == answers["api/contents"].code == 404


def test_the_server_lands_on_the_reports_and_loads_no_extension(app):
    assert app.default_url == PREFIX + "reports"
    assert _handler(app, PREFIX) == "RedirectWithParams"
    assert app.extension_manager.extensions == {}
    assert app.terminals_enabled is False


def _report(tmp_path, params="days = 7\nlabel = 'pump'\nscaled = False\nfactor = 0.5", body="print(days, label, scaled, factor)"):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(params, metadata={"tags": ["parameters"]}),
                nbformat.v4.new_code_cell(body)]
    path = tmp_path / "overview.ipynb"
    nbformat.write(nb, path)
    return path


def test_a_report_runs_with_its_typed_parameters_and_shows_no_input(tmp_path):
    path = _report(tmp_path)
    page = asyncio.run(report_server.render(path, {"days": "30", "label": "x'); import os; ('", "scaled": "true"}))
    # The text arrives as text: the would-be code is printed, not run.
    assert "30 x&#39;); import os; (&#39; True 0.5" in page or "30 x'); import os; (' True 0.5" in page
    assert "print(days" not in page


@pytest.mark.parametrize("given, needle", [
    ({"other": "1"}, "takes no parameter"),
    ({"days": "7.5"}, "takes a int"),
    ({"factor": "nan"}, "takes a float"),
    ({"scaled": "yes"}, "takes a bool"),
    ({"label": "x" * 201}, "longer than"),
])
def test_a_parameter_the_report_does_not_declare_or_type_is_refused(tmp_path, given, needle):
    nb = nbformat.read(_report(tmp_path), as_version=4)
    with pytest.raises(report_server.ParameterRefused, match=needle):
        report_server.bind_parameters(nb, given)


@pytest.mark.parametrize("params", ["days = 1 + 1", "import os", "days = len('x')", "a, b = 1, 2"])
def test_a_parameters_cell_that_is_not_plain_literals_is_a_defect(tmp_path, params):
    nb = nbformat.read(_report(tmp_path, params=params), as_version=4)
    with pytest.raises(report_server.ReportDefect):
        report_server.parameters_of(nb)


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "", "x" * 65, "overview.ipynb"])
def test_a_report_is_named_never_addressed_by_path(tmp_path, monkeypatch, name):
    monkeypatch.setenv(report_server.REPORTS_DIR_ENV, str(tmp_path))
    _report(tmp_path)
    assert report_server.report_path(name) is None
    assert report_server.report_path("overview") == tmp_path / "overview.ipynb"
