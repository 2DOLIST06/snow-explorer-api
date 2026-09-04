"""Render production server settings."""

# The HTTP worker must outlive the bounded 20-second media conversion.
timeout = 90
workers = 1
