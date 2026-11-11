"""Point d'entrée du moteur embarqué dans l'application de bureau (construit par PyInstaller)."""

import multiprocessing
import sys

from moto_blur.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
