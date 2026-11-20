from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers


class FrontendFiles(StaticFiles):
    def __init__(self, *, api_prefix: str, **kwargs):
        super().__init__(**kwargs)
        self.api_prefix = api_prefix.strip("/")

    async def get_response(self, path, scope):
        # Browser history routes belong to React; API and asset paths do not.
        parts = path.split("/")
        if (
            scope["method"] in {"GET", "HEAD"}
            and parts[0] not in {"api", "ws", "static", "assets"}
            and path != self.api_prefix
            and not path.startswith(self.api_prefix + "/")
            and (path == "." or not any("." in part for part in parts))
            and self.accepts_html(scope)
        ):
            path = "index.html"
        return await super().get_response(path, scope)

    @staticmethod
    def accepts_html(scope):
        for value in Headers(scope=scope).get("accept", "").split(","):
            media_type, *parameters = value.split(";")
            if media_type.strip().lower() != "text/html":
                continue
            quality = "1"
            for parameter in parameters:
                key, _, value = parameter.partition("=")
                if key.strip().lower() == "q":
                    quality = value.strip()
            try:
                if 0 < float(quality) <= 1:
                    return True
            except ValueError:
                pass
        return False
