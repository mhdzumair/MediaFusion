import aiohttp
import httpx
from fastapi.responses import StreamingResponse, Response
from starlette.background import BackgroundTask


async def handle_head_request(video_url: str) -> Response:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(video_url) as head_response:
                head_response.raise_for_status()
                return Response(
                    headers={
                        "Content-Length": head_response.headers.get(
                            "Content-Length", ""
                        ),
                        "Accept-Ranges": head_response.headers.get(
                            "Accept-Ranges", "bytes"
                        ),
                    },
                    status_code=head_response.status,
                )
    except aiohttp.ClientError as e:
        return Response(status_code=502, content=f"Upstream service error: {e}")
    except Exception as e:
        return Response(status_code=500, content=f"Internal server error: {e}")


async def handle_get_request(
    video_url: str, range_header: str
) -> StreamingResponse | Response:
    try:
        async with aiohttp.ClientSession() as session:

            class Streamer:
                def __init__(self):
                    self.response = None

                async def stream_content(self, headers: dict):
                    async with httpx.AsyncClient() as client, client.stream(
                        "GET", video_url, headers=headers
                    ) as self.response:
                        self.response.raise_for_status()
                        async for chunk in self.response.aiter_raw():
                            yield chunk

                async def close(self):
                    if self.response is not None:
                        await self.response.aclose()

            async with session.head(
                video_url, headers={"Range": range_header}
            ) as head_response:
                head_response.raise_for_status()
                if head_response.status == 206:
                    streamer = Streamer()

                    return StreamingResponse(
                        streamer.stream_content({"Range": range_header}),
                        status_code=206,
                        headers={
                            "Content-Range": head_response.headers["Content-Range"],
                            "Content-Length": head_response.headers["Content-Length"],
                            "Accept-Ranges": "bytes",
                        },
                        background=BackgroundTask(streamer.close),
                    )
    except aiohttp.ClientError as e:
        return Response(status_code=502, content=f"Upstream service error: {e}")
    except httpx.HTTPStatusError as e:
        return Response(status_code=502, content=f"Streaming service error: {e}")
    except Exception as e:
        return Response(status_code=500, content=f"Internal server error: {e}")
    return Response(status_code=404)
