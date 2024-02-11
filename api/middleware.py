import logging

from fastapi.requests import Request
from fastapi.responses import Response


def get_client_ip(request: Request) -> str | None:
    """
    Extract the client's real IP address from the request headers or fallback to the client host.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # In some cases, this header can contain multiple IPs
        # separated by commas.
        # The first one is the original client's IP.
        return x_forwarded_for.split(",")[0].strip()
    # Fallback to X-Real-IP if X-Forwarded-For is not available
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip
    return request.client.host if request.client else "Unknown"


class SecureLoggingMiddleware:
    async def __call__(self, request: Request, call_next):
        response = await call_next(request)
        self.custom_log(request, response)
        return response

    @staticmethod
    def custom_log(request: Request, response: Response):
        ip = get_client_ip(request)
        url_path = str(request.url)
        if request.path_params.get("secret_str"):
            url_path = url_path.replace(
                request.path_params.get("secret_str"), "***MASKED***"
            )
        logging.info(
            f'{ip} - "{request.method} {url_path} HTTP/1.1" {response.status_code}'
        )
