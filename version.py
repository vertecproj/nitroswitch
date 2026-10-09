"""
version.py
----------
Versão do app e repositório das atualizações. ÚNICA fonte: o app, o instalador
e o build (build.ps1 / GitHub Actions) leem daqui. Para lançar uma versão nova:
mude __version__, faça commit e crie a tag `vX.Y.Z` (ver CLAUDE.md).
"""

__version__ = "2.1.0"

# dono/nome no GitHub. As atualizações vêm de Releases deste repositório.
REPO = "vertecproj/nitroswitch"
