# Contributing

Thanks for your interest in AveDescargas. This is a personal Windows application and is currently maintained by its author.

## Before opening an issue

- Search existing issues for the same problem.
- Include Windows version, app version or commit, clear reproduction steps, and the full error text.
- Remove personal paths, cookies, tokens, account data, and private media from logs or screenshots.

## Development

1. Create a Python 3.12+ virtual environment.
2. Install dependencies from `requirements.txt`.
3. Run the editor with `py media_grabber_editor.py`.
4. Run checks with `py -3 -m unittest test_media_editor.py test_media_grabber.py`.

Keep changes focused and include tests for behavior changes. Do not commit downloaded media, credentials, local settings, build folders, or generated executables.

## Pull requests

Explain the user problem, summarize the implementation, list verification performed, and attach screenshots only when they contain no private information.
