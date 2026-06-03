"""Shared calculator for TA indicators.

Wires Hyperliquid candle data into ggbot's preprocessors via pandas-ta.
Each indicator function takes a DataFrame and returns the full preprocessor output dict.
"""

import logging

import pandas as pd
import pandas_ta as ta

from tools.integrations.hyperliquid.data_points import hl_candles as _hl_candles
from .preprocessors import get_preprocessor

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────

def candles_to_df(result: dict) -> pd.DataFrame:
    """Convert hl_candles() output to a pandas DataFrame ready for pandas-ta."""
    candles = result["candles"]
    df = pd.DataFrame(candles)
    df = df.rename(columns={
        "t": "timestamp", "o": "open", "h": "high",
        "l": "low", "c": "close", "v": "volume",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    return df


# ── momentum indicators ────────────────────────────────────────────────────

def calc_rsi(df: pd.DataFrame, length: int = 14) -> dict:
    series = ta.rsi(df["close"], length=length)
    return get_preprocessor("rsi").preprocess(series, prices=df["close"],
                                               period=length)


def calc_stochastic(df: pd.DataFrame, k: int = 14, d: int = 3) -> dict:
    result = ta.stoch(df["high"], df["low"], df["close"], k=k, d=d)
    k_line = result.iloc[:, 0]
    d_line = result.iloc[:, 1]
    return get_preprocessor("stochastic").preprocess(k_line, d_line,
                                                      prices=df["close"],
                                                      k=k, d=d)


def calc_williams_r(df: pd.DataFrame, length: int = 14) -> dict:
    series = ta.willr(df["high"], df["low"], df["close"], length=length)
    return get_preprocessor("williams_r").preprocess(series,
                                                      prices=df["close"],
                                                      length=length)


def calc_cci(df: pd.DataFrame, length: int = 20) -> dict:
    series = ta.cci(df["high"], df["low"], df["close"], length=length)
    return get_preprocessor("cci").preprocess(series,
                                              prices=df["close"],
                                              length=length)


def calc_mfi(df: pd.DataFrame, length: int = 14) -> dict:
    series = ta.mfi(df["high"], df["low"], df["close"], df["volume"],
                    length=length)
    return get_preprocessor("mfi").preprocess(series,
                                              prices=df["close"],
                                              length=length)


def calc_roc(df: pd.DataFrame, length: int = 10) -> dict:
    series = ta.roc(df["close"], length=length)
    return get_preprocessor("roc").preprocess(series,
                                              prices=df["close"],
                                              length=length)


# ── trend indicators ───────────────────────────────────────────────────────

def calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
              signal: int = 9) -> dict:
    result = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    macd_line = result[f"MACD_{fast}_{slow}_{signal}"]
    macd_signal = result[f"MACDs_{fast}_{slow}_{signal}"]
    histogram = result[f"MACDh_{fast}_{slow}_{signal}"]
    return get_preprocessor("macd").preprocess(macd_line, macd_signal,
                                                histogram,
                                                prices=df["close"])


def calc_adx(df: pd.DataFrame, length: int = 14) -> dict:
    result = ta.adx(df["high"], df["low"], df["close"], length=length)
    # pandas_ta returns DataFrame with ADX, DMP, DMN columns
    adx_series = result[f"ADX_{length}"]
    dmp_series = result[f"DMP_{length}"]
    dmn_series = result[f"DMN_{length}"]
    return get_preprocessor("adx").preprocess(adx_series, dmp_series, dmn_series,
                                              prices=df["close"],
                                              length=length)


def calc_aroon(df: pd.DataFrame, length: int = 14) -> dict:
    result = ta.aroon(df["high"], df["low"], length=length)
    aroon_up = result.iloc[:, 0]
    aroon_down = result.iloc[:, 1]
    return get_preprocessor("aroon").preprocess(aroon_up, aroon_down,
                                                 prices=df["close"],
                                                 length=length)


def calc_trix(df: pd.DataFrame, length: int = 14) -> dict:
    result = ta.trix(df["close"], length=length)
    # pandas_ta returns DataFrame with TRIX and TRIXs (signal) columns
    trix_series = result[f"TRIX_{length}"]
    trix_signal = result.get(f"TRIXs_{length}")  # signal line, optional
    return get_preprocessor("trix").preprocess(trix_series, trix_signal,
                                               prices=df["close"],
                                               length=length)


def calc_vortex(df: pd.DataFrame, length: int = 14) -> dict:
    result = ta.vortex(df["high"], df["low"], df["close"], length=length)
    vt_plus = result.iloc[:, 0]
    vt_minus = result.iloc[:, 1]
    return get_preprocessor("vortex").preprocess(vt_plus, vt_minus,
                                                  prices=df["close"],
                                                  length=length)


# ── moving average indicators ──────────────────────────────────────────────

def calc_sma(df: pd.DataFrame, length: int = 20) -> dict:
    series = ta.sma(df["close"], length=length)
    return get_preprocessor("sma").preprocess(series,
                                              prices=df["close"],
                                              length=length)


def calc_ema(df: pd.DataFrame, length: int = 20) -> dict:
    series = ta.ema(df["close"], length=length)
    return get_preprocessor("ema").preprocess(series,
                                              prices=df["close"],
                                              length=length)


def calc_vwap(df: pd.DataFrame) -> dict:
    series = ta.vwap(df["high"], df["low"], df["close"], df["volume"])
    return get_preprocessor("vwap").preprocess(series, prices=df["close"])


# ── volatility indicators ──────────────────────────────────────────────────

def calc_bbands(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> dict:
    result = ta.bbands(df["close"], length=length, std=std)
    upper = result[f"BBU_{length}_{std}"]
    middle = result[f"BBM_{length}_{std}"]
    lower = result[f"BBL_{length}_{std}"]
    return get_preprocessor("bbands").preprocess(upper, middle, lower,
                                                  df["close"],
                                                  length=length, std=std)


def calc_bbwidth(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> dict:
    series = ta.bbands(df["close"], length=length, std=std)[f"BBB_{length}_{std}"]
    return get_preprocessor("bbwidth").preprocess(series,
                                                   prices=df["close"],
                                                   length=length, std=std)


def calc_keltner(df: pd.DataFrame, length: int = 20,
                 multiplier: float = 2.0) -> dict:
    result = ta.kc(df["high"], df["low"], df["close"],
                   length=length, scalar=multiplier)
    upper = result.iloc[:, 0]
    middle = result.iloc[:, 1]
    lower = result.iloc[:, 2]
    return get_preprocessor("keltner").preprocess(upper, middle, lower,
                                                   df["close"],
                                                   length=length,
                                                   multiplier=multiplier)


def calc_donchian(df: pd.DataFrame, length: int = 20) -> dict:
    upper = df["high"].rolling(length).max()
    middle = (upper + df["low"].rolling(length).min()) / 2
    lower = df["low"].rolling(length).min()
    return get_preprocessor("donchian").preprocess(upper, middle, lower,
                                                    df["close"],
                                                    length=length)


def calc_atr(df: pd.DataFrame, length: int = 14) -> dict:
    series = ta.atr(df["high"], df["low"], df["close"], length=length)
    return get_preprocessor("atr").preprocess(series,
                                              prices=df["close"],
                                              length=length)


# ── volume / other indicators ──────────────────────────────────────────────

def calc_obv(df: pd.DataFrame) -> dict:
    series = ta.obv(df["close"], df["volume"])
    return get_preprocessor("obv").preprocess(series, prices=df["close"])


def calc_psar(df: pd.DataFrame, af_start: float = 0.02,
              af_increment: float = 0.02, af_max: float = 0.2) -> dict:
    result = ta.psar(df["high"], df["low"], df["close"],
                     af0=af_start, af=af_increment, max_af=af_max)
    # pandas_ta returns DataFrame with PSARl, PSARs, PSARaf, PSARr columns
    psar_series = result["PSARl_0.02_0.2"]  # long psar
    return get_preprocessor("psar").preprocess(psar_series,
                                               prices=df["close"],
                                               af_start=af_start,
                                               af_increment=af_increment,
                                               af_max=af_max)
