from src.config.logging import configure_logging, get_logger
from src.config.settings import get_settings
from src.mcp.server import create_mcp_server


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger = get_logger(__name__)

    mcp = create_mcp_server()

    logger.info(
        "mcp_server.run",
        host=settings.mcp_host,
        port=settings.mcp_port,
        transport="sse",
    )

    mcp.run(
        transport="sse",
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
