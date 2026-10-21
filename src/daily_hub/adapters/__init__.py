from .alas import AlasAdapter
from .base import Adapter
from .bettergi import BetterGIAdapter
from .maa_arknights import MaaArknightsAdapter
from .maa_bbb import MaaBbbAdapter
from .maaend import MaaEndAdapter
from .march7th import March7thAdapter
from .okww import OkWwAdapter
from .zzz_onedragon import ZzzOneDragonAdapter

ADAPTERS: dict[str, Adapter] = {
    a.key: a for a in (BetterGIAdapter(), ZzzOneDragonAdapter(), MaaEndAdapter(), OkWwAdapter(),
                       MaaBbbAdapter(), March7thAdapter(), MaaArknightsAdapter(), AlasAdapter())
}


def get(key: str) -> Adapter:
    return ADAPTERS[key]
