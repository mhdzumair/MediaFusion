import logging

import httpx
from fastapi import Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)


async def handle_head_request(video_url: str) -> Response:
    async with httpx.AsyncClient() as client:
        try:
            head_response = await client.head(video_url)
            head_response.raise_for_status()
            return Response(
                headers={
                    "Content-Length": head_response.headers.get("content-length", ""),
                    "Accept-Ranges": head_response.headers.get(
                        "accept-ranges", "bytes"
                    ),
                },
                status_code=head_response.status_code,
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Upstream service error while handling HEAD request: {e}")
            return Response(status_code=502, content=f"Upstream service error: {e}")
        except Exception as e:
            logger.error(f"Internal server error while handling HEAD request: {e}")
            return Response(status_code=500, content=f"Internal server error: {e}")


class Streamer:
    def __init__(self, client):
        self.client = client
        self.response = None

    async def stream_content(
        self, video_url: str, headers: dict, chunk_size: int = 65536
    ):
        try:
            async with self.client.stream(
                "GET", video_url, headers=headers
            ) as self.response:
                self.response.raise_for_status()
                async for chunk in self.response.aiter_bytes(chunk_size):
                    yield chunk
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while streaming content: {e}")
            raise e
        except Exception as e:
            logger.error(f"Unexpected error while streaming content: {e}")
            raise e

    async def close(self):
        try:
            if self.response is not None:
                await self.response.aclose()
            if self.client is not None:
                await self.client.aclose()
        except Exception as e:
            logger.error(f"Error while closing Streamer: {e}")


async def handle_get_request(
    video_url: str, range_header: str
) -> StreamingResponse | Response:
    client = httpx.AsyncClient()
    try:
        head_response = await client.head(video_url, headers={"Range": range_header})
        head_response.raise_for_status()
        if head_response.status_code == 206:
            streamer = Streamer(client)
            return StreamingResponse(
                streamer.stream_content(video_url, {"Range": range_header}),
                status_code=206,
                headers={
                    "Content-Range": head_response.headers["content-range"],
                    "Content-Length": head_response.headers["content-length"],
                    "Accept-Ranges": "bytes",
                },
                background=BackgroundTask(streamer.close),
            )
    except httpx.HTTPStatusError as e:
        logger.error(f"Upstream service error while handling GET request: {e}")
        return Response(status_code=502, content=f"Upstream service error: {e}")
    except Exception as e:
        logger.error(f"Internal server error while handling GET request: {e}")
        return Response(status_code=500, content=f"Internal server error: {e}")

    logger.warning(f"Resource not found for video URL: {video_url}")
    return Response(status_code=404)
