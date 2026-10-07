"""Helyi webes felület egy emberi játékosnak:  python -m liska.web"""
from .server import GameSession, serve

__all__ = ["GameSession", "serve"]
