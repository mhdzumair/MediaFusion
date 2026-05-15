"""Shared SQL expressions for catalog browse ordering (web API, Stremio, query builders)."""

from sqlalchemy import case, func

from db.models import Media


def effective_release_date():
    """Sort key for release date: use full date when set, else Dec 31 of ``Media.year``.

    Aligns ordering with the year users see on cards when ``release_date`` was never backfilled.
    """
    year_end = case(
        (Media.year.is_not(None), func.make_date(Media.year, 12, 31)),
        else_=None,
    )
    return func.coalesce(Media.release_date, year_end)
