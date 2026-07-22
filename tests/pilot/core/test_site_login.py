from pathlib import Path
from unittest.mock import patch

from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.core.site.login import SiteLogin

from .test_core import make_bench


def make_site(tmp_path: Path, name: str = "site.local") -> Site:
    bench = make_bench(tmp_path)
    return Site(SiteConfig(name=name, apps=[]), bench)


def test_warm_session_requests_local_http_with_sid_cookie(tmp_path: Path) -> None:
    site = make_site(tmp_path)
    site.bench.config.http_port = 8123

    with patch("pilot.core.site.login.urllib.request.urlopen") as urlopen:
        SiteLogin(site)._warm_session("abc123")

    urlopen.assert_called_once()
    request = urlopen.call_args[0][0]
    assert request.full_url == "http://127.0.0.1:8123/api/method/frappe.ping"
    assert request.get_header("Host") == "site.local"
    assert request.get_header("Cookie") == "sid=abc123"


def test_warm_session_skips_in_production(tmp_path: Path) -> None:
    site = make_site(tmp_path)
    site.bench.config.production.enabled = True

    with patch("pilot.core.site.login.urllib.request.urlopen") as urlopen:
        SiteLogin(site)._warm_session("abc123")

    urlopen.assert_not_called()


def test_warm_session_suppresses_connection_errors(tmp_path: Path) -> None:
    site = make_site(tmp_path)

    with patch("pilot.core.site.login.urllib.request.urlopen", side_effect=OSError("refused")):
        SiteLogin(site)._warm_session("abc123")  # must not raise


def test_admin_url_calls_warm_session_after_create_session(tmp_path: Path) -> None:
    site = make_site(tmp_path)

    with (
        patch("pilot.core.site.login.SiteLogin.create_session", return_value="abc123"),
        patch("pilot.core.site.login.SiteLogin._warm_session") as warm_session,
        patch("pilot.core.site.login.read_site_config", return_value={}),
    ):
        url = SiteLogin(site).admin_url()

    warm_session.assert_called_once_with("abc123")
    assert url is not None
    assert "sid=abc123" in url


def test_admin_url_skips_warm_session_when_no_sid(tmp_path: Path) -> None:
    site = make_site(tmp_path)

    with (
        patch("pilot.core.site.login.SiteLogin.create_session", return_value=None),
        patch("pilot.core.site.login.SiteLogin._warm_session") as warm_session,
        patch("pilot.core.site.login.read_site_config", return_value={}),
    ):
        url = SiteLogin(site).admin_url()

    warm_session.assert_not_called()
    assert url is None
