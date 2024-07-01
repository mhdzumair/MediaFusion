from .duplicates_pipeline import TorrentDuplicatesPipeline
from .formula_parser_pipeline import FormulaParserPipeline
from .moto_gp_parser_pipeline import MotoGPParserPipeline
from .store_pipelines import (
    QueueBasedPipeline,
    EventSeriesStorePipeline,
    TVStorePipeline,
    MovieStorePipeline,
    SeriesStorePipeline,
    LiveEventStorePipeline,
)
from .redis_cache_pipeline import RedisCacheURLPipeline
from .live_stream_resolver_pipeline import LiveStreamResolverPipeline
from .torrent_parser_pipeline import (
    TorrentDownloadAndParsePipeline,
    MagnetDownloadAndParsePipeline,
)
from .sport_video_parser_pipeline import SportVideoParserPipeline
