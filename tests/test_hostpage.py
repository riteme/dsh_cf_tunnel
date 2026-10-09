"""The generated dsh patch: both files land together and the row points at the plugin."""

from __future__ import annotations

from dsh_cf_tunnel import hostpage


def test_patch_and_plugin_are_written_together(tmp_path) -> None:
    """The relative plugin name only resolves beside the patch, so both must exist."""
    patch = hostpage.write_host_page_patch(str(tmp_path))
    assert patch == str(tmp_path / hostpage.HOST_PAGE_PATCH_FILE)
    assert (tmp_path / hostpage.HOST_PAGE_PLUGIN_FILE).is_file()


def test_patch_row_targets_the_plugin(tmp_path) -> None:
    """The insert names the plugin file the loader anchors beside the patch."""
    with open(hostpage.write_host_page_patch(str(tmp_path)), encoding="utf-8") as stream:
        patch = stream.read()
    assert "- insert:" in patch
    assert "id: dsh-server-host-page" in patch
    assert f"name: ./{hostpage.HOST_PAGE_PLUGIN_FILE}" in patch


def test_plugin_publishes_the_host_owned_flag(tmp_path) -> None:
    """The injected global is exactly what the browser client reads."""
    hostpage.write_host_page_patch(str(tmp_path))
    plugin = (tmp_path / hostpage.HOST_PAGE_PLUGIN_FILE).read_text(encoding="utf-8")
    assert 'ctx.on("webserver/index-inject"' in plugin
    assert 'name: "__DSH_TRANSPORT__"' in plugin
    assert "ownsHost: true" in plugin


def test_writing_twice_is_idempotent(tmp_path) -> None:
    """Restarting regenerates the same files instead of stacking rows."""
    first = hostpage.write_host_page_patch(str(tmp_path))
    second = hostpage.write_host_page_patch(str(tmp_path))
    assert first == second
    assert (tmp_path / hostpage.HOST_PAGE_PATCH_FILE).read_text(encoding="utf-8").count("- insert:") == 1
