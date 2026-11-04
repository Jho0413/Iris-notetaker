"""``python -m iris …`` runs the CLI. The desktop app starts the backend this way: the ``iris``
console script has its build path baked into its shebang, so it can't move with the app."""

from iris.cli import main

if __name__ == "__main__":
    main()
