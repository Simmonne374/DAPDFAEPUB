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
