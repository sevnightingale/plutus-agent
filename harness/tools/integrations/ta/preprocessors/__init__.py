"""Technical-indicator preprocessors — flat module-level dict.

Each preprocessor is a stateless object that takes a pandas Series + raw
prices and returns a structured dict (zone analysis, divergence detection,
trend, summary). Looked up by indicator name via :func:`get_preprocessor`.

Design note: the upstream ggbot version wrapped this in a
``PreprocessorFactory`` class with try/except imports per module. We
flattened to a module-level dict — preprocessors are stateless lookups,
not configurable services, and silent ImportError swallowing hides real
breakage. If a preprocessor fails to import here, that's a bug to fix,
not a missing-feature to skip.
"""

from .adx import ADXPreprocessor
from .aroon import AroonPreprocessor
from .atr import ATRPreprocessor
from .bbands import BollingerBandsPreprocessor
from .bbwidth import BollingerWidthPreprocessor
from .cci import CCIPreprocessor
from .donchian import DonchianChannelsPreprocessor
from .ema import EMAPreprocessor
from .keltner import KeltnerChannelsPreprocessor
from .macd import MACDPreprocessor
from .mfi import MFIPreprocessor
from .obv import OBVPreprocessor
from .psar import ParabolicSARPreprocessor
from .roc import ROCPreprocessor
from .rsi import RSIPreprocessor
from .sma import SMAPreprocessor
from .stochastic import StochasticPreprocessor
from .trix import TRIXPreprocessor
from .vortex import VortexPreprocessor
from .vwap import VWAPPreprocessor
from .williams_r import WilliamsRPreprocessor


PREPROCESSORS = {
    "adx": ADXPreprocessor(),
    "aroon": AroonPreprocessor(),
    "atr": ATRPreprocessor(),
    "bbands": BollingerBandsPreprocessor(),
    "bbwidth": BollingerWidthPreprocessor(),
    "cci": CCIPreprocessor(),
    "donchian": DonchianChannelsPreprocessor(),
    "ema": EMAPreprocessor(),
    "keltner": KeltnerChannelsPreprocessor(),
    "macd": MACDPreprocessor(),
    "mfi": MFIPreprocessor(),
    "obv": OBVPreprocessor(),
    "psar": ParabolicSARPreprocessor(),
    "roc": ROCPreprocessor(),
    "rsi": RSIPreprocessor(),
    "sma": SMAPreprocessor(),
    "stochastic": StochasticPreprocessor(),
    "trix": TRIXPreprocessor(),
    "vortex": VortexPreprocessor(),
    "vwap": VWAPPreprocessor(),
    "williams_r": WilliamsRPreprocessor(),
}


def get_preprocessor(name: str):
    """Return the preprocessor instance for ``name`` (lowercased), or None."""
    return PREPROCESSORS.get(name.lower())


def is_preprocessor_available(name: str) -> bool:
    return name.lower() in PREPROCESSORS


def list_available_preprocessors() -> list[str]:
    return sorted(PREPROCESSORS.keys())
