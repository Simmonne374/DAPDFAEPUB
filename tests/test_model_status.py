import pytest
from unittest.mock import patch
from relictoepub.ui.components import check_model_status

def test_check_model_status_real():
    is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
    assert isinstance(is_ok, bool)
    assert "Modello" in status_str

def test_check_model_status_cached():
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load:
        mock_load.return_value = "/path/to/cached/config.json"
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is True
        assert "🟢 **Modello rilevato localmente**" in status_str
        mock_load.assert_called_once_with("baidu/Unlimited-OCR", "config.json")

def test_check_model_status_not_cached():
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load:
        mock_load.side_effect = Exception("Not found in cache")
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is False
        assert "🔴 **Modello non presente localmente**" in status_str
        mock_load.assert_called_once_with("baidu/Unlimited-OCR", "config.json")

def test_check_model_status_returns_none():
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load:
        mock_load.return_value = None
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is False
        assert "🔴 **Modello non presente localmente**" in status_str
        mock_load.assert_called_once_with("baidu/Unlimited-OCR", "config.json")


def test_download_model_ui_success():
    from unittest.mock import MagicMock
    from relictoepub.ui.gradio_app import _download_model_ui
    
    mock_process = MagicMock()
    mock_process.stdout.readline.side_effect = ["Downloading safetensors...\n", "Done!\n", ""]
    mock_process.returncode = 0
    
    with patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
         patch("relictoepub.ui.gradio_app.check_model_status") as mock_status:
        
        mock_status.return_value = (True, "🟢 **Modello rilevato localmente**")
        
        generator = _download_model_ui()
        results = list(generator)
        
        assert len(results) >= 4
        
        # Verify first yield
        log1, status1, btn1 = results[0]
        assert "Inizio download" in log1
        assert "⏳ **Download in corso...**" == status1
        assert btn1.interactive is False
        
        # Verify last yield
        log_last, status_last, btn_last = results[-1]
        assert "Modello scaricato e verificato con successo!" in log_last
        assert "🟢 **Modello rilevato localmente**" == status_last
        assert btn_last.interactive is True


def test_download_model_ui_failure():
    from unittest.mock import MagicMock
    from relictoepub.ui.gradio_app import _download_model_ui
    
    mock_process = MagicMock()
    mock_process.stdout.readline.side_effect = ["Error: network timeout\n", ""]
    mock_process.returncode = 1
    
    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        generator = _download_model_ui()
        results = list(generator)
        
        assert len(results) >= 3
        
        # Verify last yield
        log_last, status_last, btn_last = results[-1]
        assert "Errore durante il download" in log_last
        assert "🔴 **Errore nel download del modello**" == status_last
        assert btn_last.interactive is True


def test_download_model_ui_exception():
    from relictoepub.ui.gradio_app import _download_model_ui
    
    with patch("subprocess.Popen", side_effect=FileNotFoundError("huggingface-cli not found")):
        generator = _download_model_ui()
        results = list(generator)
        
        assert len(results) >= 2
        
        # Verify last yield
        log_last, status_last, btn_last = results[-1]
        assert "Errore imprevisto" in log_last
        assert "huggingface-cli not found" in log_last
        assert "🔴 **Errore nel download del modello**" == status_last
        assert btn_last.interactive is True
