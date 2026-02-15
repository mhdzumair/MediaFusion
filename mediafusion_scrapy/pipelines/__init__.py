from .catalog_parse_pipeline import CatalogParsePipeline
from .duplicates_pipeline import TorrentDuplicatesPipeline
from .formula_parser_pipeline import FormulaParserPipeline
from .live_stream_resolver_pipeline import LiveStreamResolverPipeline
from .metadata_search_pipeline import MetadataSearchPipeline
from .moto_gp_parser_pipeline import MotoGPParserPipeline
from .movie_tv_parser_pipeline import MovieTVParserPipeline
from .redis_cache_pipeline import RedisCacheURLPipeline
from .sport_video_parser_pipeline import SportVideoParserPipeline
from .sports_parser_pipeline import UFCParserPipeline, WWEParserPipeline
from .store_pipelines import (
    EventSeriesStorePipeline,
    LiveEventStorePipeline,
    MovieStorePipeline,
    QueueBasedPipeline,
    SeriesStorePipeline,
    TVStorePipeline,
)
from .torrent_parser_pipeline import (
    MagnetDownloadAndParsePipeline,
    TorrentDownloadAndParsePipeline,
)

__all__ = [
    "TorrentDuplicatesPipeline",
    "FormulaParserPipeline",
    "LiveStreamResolverPipeline",
    "MetadataSearchPipeline",
    "MotoGPParserPipeline",
    "RedisCacheURLPipeline",
    "SportVideoParserPipeline",
    "QueueBasedPipeline",
    "EventSeriesStorePipeline",
    "TVStorePipeline",
    "MovieStorePipeline",
    "SeriesStorePipeline",
    "LiveEventStorePipeline",
    "TorrentDownloadAndParsePipeline",
    "MagnetDownloadAndParsePipeline",
    "WWEParserPipeline",
    "UFCParserPipeline",
    "CatalogParsePipeline",
    "MovieTVParserPipeline",
]
