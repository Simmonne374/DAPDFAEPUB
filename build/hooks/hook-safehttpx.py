"""PyInstaller hook per ``safehttpx``.

Gradio dipende da safehttpx che legge ``version.txt`` a runtime
(``Path(__file__).parent / 'version.txt'``). Senza questo hook PyInstaller
non include il modulo né il data file, causando
``FileNotFoundError: .../safehttpx/version.txt`` all'avvio dell'app.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("safehttpx")
datas = collect_data_files("safehttpx")