"""MR Signage backend package.

Explicit package marker. Without it `backend` is only an implicit namespace package,
which works when the project root happens to be on sys.path but breaks as soon as the
code is copied somewhere else — such as into the Docker image.
"""
