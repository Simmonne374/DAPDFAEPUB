from unittest.mock import patch
from relictoepub.ui.components import check_model_status

def test_check_model_status_real():
    is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
    assert isinstance(is_ok, bool)
    assert "Modello" in status_str

def test_check_model_status_cached():
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load:
        def side_effect(model_id, filename):
            if filename == "config.json":
                return "/path/to/cached/config.json"
            if filename == "model.safetensors":
                return "/path/to/cached/model.safetensors"
            return None
        mock_load.side_effect = side_effect
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is True
        assert "🟢 **Modello rilevato localmente**" in status_str

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
    """Verifica il flusso di successo di ``_download_model_ui`` (snapshot_download)."""
    from relictoepub.ui.gradio_app import _download_model_ui

    with patch("huggingface_hub.snapshot_download") as mock_snap, \
         patch("relictoepub.ui.gradio_app.check_model_status") as mock_status:
        mock_snap.return_value = "/cache/baidu/Unlimited-OCR"
        mock_status.return_value = (True, "🟢 **Modello rilevato localmente**")

        generator = _download_model_ui()
        results = list(generator)

        # La funzione deve emettere almeno 2 yield: inizio + fine.
        assert len(results) >= 2
        # Ciascun yield è una 4-tupla (log, status, button, progress_update).
        for r in results:
            assert len(r) == 4

        # Verifica il primo yield (annuncio).
        log1, status1, btn1, _ = results[0]
        assert "Inizio download" in log1
        assert status1 == "⏳ **Download in corso...**"
        assert btn1.interactive is False

        # Verifica l'ultimo yield (successo).
        log_last, status_last, btn_last, _ = results[-1]
        assert "Modello scaricato in cache HuggingFace" in log_last
        assert status_last == "🟢 **Modello rilevato localmente**"
        assert btn_last.interactive is True

        # Verifica che il repo scaricato sia quello giusto.
        mock_snap.assert_called_once()
        kwargs = mock_snap.call_args.kwargs
        assert kwargs["repo_id"] == "baidu/Unlimited-OCR"


def test_download_model_ui_failure():
    """Verifica il flusso di fallimento (eccezione di snapshot_download)."""
    from relictoepub.ui.gradio_app import _download_model_ui

    with patch(
        "huggingface_hub.snapshot_download",
        side_effect=RuntimeError("network timeout"),
    ):
        generator = _download_model_ui()
        results = list(generator)

        # Deve esserci almeno il yield iniziale e quello d'errore.
        assert len(results) >= 2

        log_last, status_last, btn_last, _ = results[-1]
        assert "Download fallito" in log_last
        assert "network timeout" in log_last
        assert status_last == "🔴 **Errore nel download del modello**"
        assert btn_last.interactive is True


def test_download_model_ui_no_hub_dependency():
    """Verifica il ramo di errore se huggingface_hub non è importabile."""
    import builtins
    from relictoepub.ui.gradio_app import _download_model_ui

    # Rimuoviamo temporaneamente huggingface_hub dal sys.modules.
    import sys
    saved = sys.modules.pop("huggingface_hub", None)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "huggingface_hub":
            raise ImportError("huggingface_hub not installed")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        generator = _download_model_ui()
        results = list(generator)
        # 1 yield iniziale + 1 yield d'errore.
        assert len(results) == 2
        log_last, status_last, btn_last, _ = results[-1]
        assert "non installato" in log_last
        assert status_last == "🔴 **Dipendenza mancante**"
        assert btn_last.interactive is True
    finally:
        builtins.__import__ = real_import
        if saved is not None:
            sys.modules["huggingface_hub"] = saved


def test_run_pipeline_yields_four_values():
    from unittest.mock import MagicMock
    from relictoepub.ui.gradio_app import _run_pipeline
    
    with patch("relictoepub.ui.gradio_app.Pipeline") as mock_pipeline_cls:
        mock_pipeline = mock_pipeline_cls.return_value
        from relictoepub.pipeline import ProgressEvent
        mock_event = ProgressEvent(phase="rendering", current=1, total=10, message="Rendering page 1")
        mock_pipeline.run_iter.return_value = [mock_event]
        
        with patch("relictoepub.ui.gradio_app.check_model_status") as mock_status:
            mock_status.return_value = (True, "🟢 **Modello rilevato localmente**")
            
            with patch("pathlib.Path.is_file", return_value=True), \
                 patch("pathlib.Path.stat") as mock_stat:
                
                mock_stat_val = MagicMock()
                mock_stat_val.st_size = 102400
                mock_stat.return_value = mock_stat_val
                
                generator = _run_pipeline(
                    pdf_path="test.pdf",
                    pages_per_batch=2,
                    dpi=150,
                    quantization="none",
                    eink_optimize=False,
                    title="Test Book",
                    author="Test Author",
                    output_dir=""
                )
                
                results = list(generator)
                
                assert len(results) >= 2
                for res in results:
                    assert len(res) == 4
                    
                # Verify that the last yield has the check_model_status message as the 4th element
                assert results[-1][3] == "🟢 **Modello rilevato localmente**"

def test_check_model_status_sharded_complete():
    from unittest.mock import mock_open
    weight_map_json = '{"weight_map": {"model.embed_tokens.weight": "model-00001-of-00002.safetensors", "model.layers.0.self_attn.q_proj.weight": "model-00002-of-00002.safetensors"}}'
    
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load, \
         patch("builtins.open", mock_open(read_data=weight_map_json)):
         
        def side_effect(model_id, filename):
            if filename == "config.json":
                return "/path/to/cached/config.json"
            if filename == "model.safetensors.index.json":
                return "/path/to/cached/model.safetensors.index.json"
            if filename in ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]:
                return f"/path/to/cached/{filename}"
            return None
            
        mock_load.side_effect = side_effect
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is True
        assert "🟢 **Modello rilevato localmente**" in status_str

def test_check_model_status_sharded_incomplete():
    from unittest.mock import mock_open
    weight_map_json = '{"weight_map": {"model.embed_tokens.weight": "model-00001-of-00002.safetensors", "model.layers.0.self_attn.q_proj.weight": "model-00002-of-00002.safetensors"}}'
    
    with patch("relictoepub.ui.components.try_to_load_from_cache") as mock_load, \
         patch("builtins.open", mock_open(read_data=weight_map_json)):
         
        def side_effect(model_id, filename):
            if filename == "config.json":
                return "/path/to/cached/config.json"
            if filename == "model.safetensors.index.json":
                return "/path/to/cached/model.safetensors.index.json"
            if filename == "model-00001-of-00002.safetensors":
                return "/path/to/cached/model-00001-of-00002.safetensors"
            return None
            
        mock_load.side_effect = side_effect
        is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
        assert is_ok is False
        assert "🔴 **Modello incompleto**" in status_str
