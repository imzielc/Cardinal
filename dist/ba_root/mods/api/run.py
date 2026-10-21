"""entry point for `uv run`. keeps package imports working."""

from api.server import main

if __name__ == "__main__":
    main()