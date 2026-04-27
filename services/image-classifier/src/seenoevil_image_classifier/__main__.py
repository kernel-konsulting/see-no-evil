"""Entry point for ``python -m seenoevil_image_classifier`` and the installed script."""

from .server import serve


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
