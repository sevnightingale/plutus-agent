"""submit_report (toolset: report) — a spawned desk agent's structured return.

The spawn runner injects this toolset into every run whose AGENT.md declares
``returns:`` — no agent lists it, none can forget it. The agent calls it once
with the contract payload; validation happens at the tool layer (a bad payload
bounces back for retry), and the final text message stays human-readable.
Contracts live in ``RETURN_CONTRACTS`` (harness/spawn.py).
"""

from harness.tools.registry import registry


def _submit(args, **kwargs):
    from harness.spawn import submit_report_handler
    return submit_report_handler(args)


registry.register(
    name="submit_report",
    toolset="report",
    schema={
        "name": "submit_report",
        "description": (
            "Submit this run's structured report (the JSON object your "
            "'# Output contract' section specifies). Call ONCE, after the "
            "work is done; then end with a short human-readable summary. "
            "If validation fails, fix the payload and call again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "report": {
                    "type": "object",
                    "description": "The contract payload — one JSON object.",
                },
            },
            "required": ["report"],
        },
    },
    handler=_submit,
    description="Submit the spawned agent's validated structured return.",
    emoji="📦",
)
