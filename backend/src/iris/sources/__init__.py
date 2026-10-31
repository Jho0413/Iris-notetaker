from iris.models import SourceKind
from iris.sources.base import Source
from iris.sources.browser_live import BrowserLiveSource
from iris.sources.upload import UploadSource

SOURCES: dict[SourceKind, Source] = {
    SourceKind.UPLOAD: UploadSource(),
    SourceKind.BROWSER_LIVE: BrowserLiveSource(),
}

__all__ = ["SOURCES", "Source"]
