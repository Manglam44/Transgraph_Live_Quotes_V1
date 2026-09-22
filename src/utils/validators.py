import math


def valid_price(value) -> bool:
    return value is not None and not math.isnan(value) and value > 0


def valid_size(value) -> bool:
    return value is not None and value >= 0
