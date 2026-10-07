# notes

A small command-line tool that keeps short notes in a JSON file.

    python3 -m notes add "Buy milk" --tag home
    python3 -m notes list [--tag TAG] [--all]
    python3 -m notes done ID

The notes file is `notes.json` in the current folder. Set `NOTES_FILE` to use another file.

## Layout

- `notes/model.py`: the `Note` record
- `notes/store.py`: load and save the notes file
- `notes/cli.py`: the commands (argparse)
- `tests/`: the tests

## Tests

    python3 -m pytest -q        # or: python3 -m unittest discover -s tests
