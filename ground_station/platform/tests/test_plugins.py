import pytest

from ground_station.platform.plugins import (
    AuthorityArbiter, Plugin, PluginDescriptor, PluginState,
)


def test_plugin_lifecycle_keeps_shadow_from_driving_output():
    plugin = Plugin(PluginDescriptor("baseline_pid", "controller", "control"))
    assert not plugin.drives_output
    plugin.transition(PluginState.SHADOW)
    assert not plugin.drives_output
    plugin.transition(PluginState.CANDIDATE)
    plugin.transition(PluginState.ACTIVE)
    assert plugin.drives_output
    plugin.fault("nan output")
    assert plugin.state is PluginState.FAULTED
    assert not plugin.drives_output
    assert plugin.last_fault == "nan output"


def test_plugin_rejects_illegal_activation():
    plugin = Plugin(PluginDescriptor("ekf", "estimator", "estimation"))
    with pytest.raises(ValueError, match="illegal"):
        plugin.transition(PluginState.ACTIVE)


def test_authority_is_exclusive_and_expires():
    arbiter = AuthorityArbiter(heartbeat_timeout_s=0.5)
    arbiter.claim("ground_station", now=10.0)
    arbiter.heartbeat("ground_station", now=10.4)
    with pytest.raises(RuntimeError, match="owned"):
        arbiter.claim("pilot", now=10.4)
    assert not arbiter.expire(now=10.8)
    assert arbiter.expire(now=11.0)
    assert not arbiter.active
    assert arbiter.reason == "heartbeat_expired"

