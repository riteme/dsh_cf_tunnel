"""The cloudflared quick-tunnel launch: what counts as a tunnel URL, and what does not."""

from __future__ import annotations

from dsh_cf_tunnel import tunnel


def test_tunnel_pattern_matches_the_boxed_line() -> None:
    """cloudflared prints the hostname inside a box; that line is the origin."""
    box = "INF |  https://talking-bald-states-speaker.trycloudflare.com   |"
    assert tunnel.QUICK_TUNNEL_URL_PATTERN.search(box).group(0) == (
        "https://talking-bald-states-speaker.trycloudflare.com"
    )


def test_tunnel_pattern_ignores_the_api_host() -> None:
    """cloudflared's own error lines embed api.trycloudflare.com; that is not a tunnel."""
    failure = 'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": context deadline exceeded'
    assert tunnel.QUICK_TUNNEL_URL_PATTERN.search(failure) is None
    assert tunnel.QUICK_TUNNEL_FAILURE_PATTERN.search(failure)


def test_reserved_labels_never_look_like_a_tunnel() -> None:
    """Reserved Cloudflare hostnames must not be mistaken for the tunnel that was made."""
    for label in ("api", "www", "developers", "blog", "community", "support", "dash", "one", "update"):
        line = f"see https://{label}.trycloudflare.com for details"
        assert tunnel.QUICK_TUNNEL_URL_PATTERN.search(line) is None


def test_failure_pattern_covers_both_wording_variants() -> None:
    """cloudflared has used both verbs across releases."""
    assert tunnel.QUICK_TUNNEL_FAILURE_PATTERN.search("failed to request quick Tunnel")
    assert tunnel.QUICK_TUNNEL_FAILURE_PATTERN.search("failed to create tunnel")
    assert not tunnel.QUICK_TUNNEL_FAILURE_PATTERN.search("Your quick Tunnel has been created!")
