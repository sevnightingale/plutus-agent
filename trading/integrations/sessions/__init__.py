"""Sessions integration — trading-session and liquidity context.

Derived from the clock plus HL candle history, no external API: which
global session is live (Asia/Europe/US/overlap), whether it's a weekend,
and how liquid this hour of day typically is vs the 24-hour profile.
"""
from . import data_points
