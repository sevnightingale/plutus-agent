"""Flow integration — computed from HL candle data, no external API.

Order-flow indicators derived from OHLCV candles: Cumulative Volume Delta,
buy/sell pressure estimates, volume climax detection. Complements the TA
suite by showing what smart money is actually doing vs what oscillators say.
"""
from . import data_points
