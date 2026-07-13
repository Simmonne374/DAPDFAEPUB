"""PyInstaller hook per il pacchetto ``groovy``.

Gradio importa ``groovy`` dinamicamente (``from groovy import transpile``).
PyInstaller non segue questo import se non vede l'uso statico, quindi il
modulo bytecode non finisce nel PYZ. Questo hook forza l'inclusione del
modulo + i suoi data files (in particolare ``version.txt`` che il modulo
legge a runtime tramite ``Path(__file__).parent / 'version.txt'``).
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("groovy")
datas = collect_data_files("groovy")