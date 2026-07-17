"""Polymarket integration — real-money prediction-market odds via the Gamma API.

Public, read-only, no credentials. Recurring markets are addressed by
SERIES slug (stable: 'btc-multi-strikes-weekly', 'fomc'), never by market
slug (rotates per event: 'bitcoin-above-on-july-17-2026'). An evidence
class orthogonal to TA/flow: incentive-backed probabilities.
"""
from . import data_points
