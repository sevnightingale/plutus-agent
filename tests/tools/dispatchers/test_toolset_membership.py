"""Verify each Phase 4a dispatcher registers under the right PLUTUS toolset.

The toolset name is what powers ``registry.get_tool_names_for_toolset()`` and
``resolve_toolset()`` once the dispatchers are wired into ``plutus-agent-cli``
in Phase 4b. Getting the toolset name wrong silently breaks that wire-up,
so cement the contract here.
"""

# Importing the modules triggers their top-level registry.register(...) call.
import tools.dispatchers.account_state              # noqa: F401
import tools.dispatchers.cancel_order               # noqa: F401
import tools.dispatchers.close_position             # noqa: F401
import tools.dispatchers.fetch_data_point           # noqa: F401
import tools.dispatchers.list_accounts              # noqa: F401
import tools.dispatchers.list_data_points           # noqa: F401
import tools.dispatchers.list_event_types           # noqa: F401
import tools.dispatchers.list_identity_systems      # noqa: F401
import tools.dispatchers.list_venues                # noqa: F401
import tools.dispatchers.modify_order               # noqa: F401
import tools.dispatchers.place_order                # noqa: F401
import tools.dispatchers.record_event               # noqa: F401

from tools.registry import registry


EXPECTED = {
    "perception": {"fetch_data_point", "list_data_points", "account_state"},
    "execution":  {"place_order", "close_position", "modify_order",
                   "cancel_order", "list_venues"},
    "reflection": {"record_event", "list_event_types"},
    "identity":   {"list_accounts", "list_identity_systems"},
}


def test_each_dispatcher_landed_in_expected_toolset():
    for toolset, expected_names in EXPECTED.items():
        actual = set(registry.get_tool_names_for_toolset(toolset))
        # Other tests may temporarily register tools to these same toolsets,
        # so check that *at least* the expected set is present (subset check).
        missing = expected_names - actual
        assert not missing, (
            f"toolset '{toolset}' is missing dispatchers: {missing} "
            f"(actually has: {sorted(actual)})"
        )


def test_toolsets_dict_resolves_dispatchers():
    """The static TOOLSETS perception/execution/reflection/identity entries
    must list the dispatcher tools so resolve_toolset() picks them up."""
    from toolsets import resolve_toolset

    for toolset, expected_names in EXPECTED.items():
        resolved = set(resolve_toolset(toolset))
        missing = expected_names - resolved
        assert not missing, (
            f"resolve_toolset('{toolset}') missing: {missing}"
        )
