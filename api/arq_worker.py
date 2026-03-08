"""
Deprecated module kept for compatibility with previous imports.

Task workers now use Taskiq and are exposed via `api.taskiq_worker`.
"""

from api.taskiq_worker import (  # noqa: F401
    broker_default as WorkerSettingsDefault,
    broker_import as WorkerSettingsImport,
    broker_priority as WorkerSettingsPriority,
    broker_scrapy as WorkerSettingsScrapy,
)
