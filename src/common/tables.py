from __future__ import annotations

TABLES = {
    ('commodity', 'live'): 'commodity_live',
    ('commodity', 'delayed'): 'commodity_delayed',
    ('currency', 'live'): 'currency_live',
    ('currency', 'delayed'): 'currency_delayed',
}


def get_table_name(asset_group: str, mode: str) -> str:
    try:
        return TABLES[(asset_group, mode)]
    except KeyError:
        raise ValueError(
            f"No table configured for asset_group={asset_group!r}, mode={mode!r}. "
            f"Known combinations: {sorted(TABLES.keys())}"
        )
