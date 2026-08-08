"""DeepSeek platform integration — provider account health, not market data.

DeepSeek is a PREPAID API: an exhausted balance fails the whole desk in the
same shape as a quota outage (every spawn dies at the provider), so the
balance is a watchdog surface exactly like trade-path readiness. Nothing
here reads markets.
"""
from . import data_points
