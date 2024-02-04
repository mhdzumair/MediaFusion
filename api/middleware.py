import logging

from fastapi.requests import Request
from fastapi.responses import Response


class SecureLoggingMiddleware:
    async def __call__(self, request: Request, call_next):
        response = await call_next(request)
        self.custom_log(request, response)
        return response

    @staticmethod
    def custom_log(request: Request, response: Response):
        client_host = request.client.host if request.client else "unknown"
        url_path = str(request.url)
        if request.path_params.get("secret_str"):
            url_path = url_path.replace(
                request.path_params.get("secret_str"), "***MASKED***"
            )
        logging.info(
            f'{client_host} - "{request.method} {url_path} HTTP/1.1" {response.status_code}'
        )
