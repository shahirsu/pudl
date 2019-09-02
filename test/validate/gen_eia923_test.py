"""Validate post-ETL Generation data from EIA 923."""

import logging

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.eia923
@pytest.mark.post_etl
def test_gen_eia923(pudl_out_eia):
    """Sanity checks for EIA 923 Generation output."""
    logger.info("Reading EIA 923 Generation data...")
    logger.info(
        f"Successfully pulled {len(pudl_out_eia.gen_eia923())} records.")
