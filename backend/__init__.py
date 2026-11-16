"""Package principal du backend Jarvis AIOS."""

from backend import config as _config

__version__: str = _config.VERSION  # lue depuis APP_DIR/VERSION (« 0.0.0-dev » en développement)
