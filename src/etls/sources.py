"""Scrappers of the daily bulletins, keyed by the source_name of their documents."""

from src.etls.boa.scrapper import BOAScrapper
from src.etls.bocm.scrapper import BOCMScrapper
from src.etls.boe.scrapper import BOEScrapper
from src.etls.boja.scrapper import BOJAScrapper
from src.etls.bopv.scrapper import BOPVScrapper
from src.etls.bopz.scrapper import BOPZScrapper
from src.etls.common.scrapper import BaseScrapper

# The daily ETL runs them in this order. BOE goes first so that the consolidated load, which runs after
# all of them, replaces the originally published text of newly consolidated norms.
BULLETIN_SCRAPPERS: dict[str, type[BaseScrapper]] = {
    "BOE": BOEScrapper,
    "BOCM": BOCMScrapper,
    "BOPZ": BOPZScrapper,
    "BOPV": BOPVScrapper,
    "BOJA": BOJAScrapper,
    "BOA": BOAScrapper,
}
