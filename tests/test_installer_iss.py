"""Test strutturali per ``build/installer.iss``.

Questi test verificano la **struttura** dello script Inno Setup, non il
comportamento runtime (che richiederebbe ISCC.exe + Windows).

L'idea: il file ``installer.iss`` e' un artifact che deve restare
semplice da revisionare e facile da far evolvere. Se qualcuno aggiunge
una direttiva che rompe il pattern dual-mode (es. rimuove il
``Check: InstallUsesMachineHive`` o il ``PrivilegesRequired=lowest``),
i test qui falliscono con un messaggio chiaro.

Cosa NON verifichiamo (perche' impossibile senza ISCC.exe):
- che il file compili correttamente;
- che la GUI si comporti come atteso a runtime;
- che Inno Setup chiami effettivamente le funzioni Pascal nel
  giusto ordine.

Cosa verifichiamo (via regex + slicing):
- header comment menziona "per-user" e "per-machine";
- ``[Setup]`` ha i flag corretti (``PrivilegesRequired=lowest``,
  ``DefaultDirName={code:GetDefaultDirName}``);
- ``#ifndef InstallScope`` esiste con default ``"either"``;
- ``[Registry]`` ha voci HKLM con ``Check: InstallUsesMachineHive``
  E voci HKCU con ``Check: InstallUsesUserHive``;
- ``[Run]`` pandoc MSI ha ``Check: PandocNeededAndMachineInstall``;
- ``[Code]`` contiene le funzioni ``GetDefaultDirName``,
  ``InstallUsesMachineHive``, ``InstallUsesUserHive``,
  ``InstallModePage``, ``InitializeWizard``, ``CurPageChanged``;
- ``DefaultInstallScope`` rispetta il valore di ``{#InstallScope}``;
- ``AppMutex`` dinamico via ``SetSetupSetting`` per evitare collisioni.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_ISS = PROJECT_ROOT / "build" / "installer.iss"


@pytest.fixture(scope="module")
def installer_text() -> str:
    """Legge installer.iss e ritorna il testo come stringa."""
    assert INSTALLER_ISS.exists(), f"installer.iss non trovato in {INSTALLER_ISS}"
    return INSTALLER_ISS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup_block(installer_text: str) -> str:
    """Estrae il contenuto di ``[Setup]`` (fino al prossimo ``[``)."""
    m = re.search(r"\[Setup\](.*?)(?=\n\[|\Z)", installer_text, re.DOTALL)
    assert m is not None, "Sezione [Setup] mancante"
    return m.group(1)


@pytest.fixture(scope="module")
def registry_block(installer_text: str) -> str:
    """Estrae il contenuto di ``[Registry]`` (fino al prossimo ``[``)."""
    m = re.search(r"\[Registry\](.*?)(?=\n\[|\Z)", installer_text, re.DOTALL)
    assert m is not None, "Sezione [Registry] mancante"
    return m.group(1)


@pytest.fixture(scope="module")
def run_block(installer_text: str) -> str:
    """Estrae il contenuto di ``[Run]`` (fino al prossimo ``[``)."""
    m = re.search(r"\[Run\](.*?)(?=\n\[|\Z)", installer_text, re.DOTALL)
    assert m is not None, "Sezione [Run] mancante"
    return m.group(1)


@pytest.fixture(scope="module")
def code_block(installer_text: str) -> str:
    """Estrae il contenuto di ``[Code]`` (fino al prossimo ``[`` o EOF)."""
    m = re.search(r"\[Code\](.*?)(?=\n\[|\Z)", installer_text, re.DOTALL)
    assert m is not None, "Sezione [Code] mancante"
    return m.group(1)


def _split_registry_entries(registry_block: str) -> list[str]:
    """Splitta il blocco ``[Registry]`` in entry separate.

    Le entry sono separate da righe vuote. Una entry inizia con
    ``Root:`` e continua con tutte le linee successive (incluse
    continuazioni con ``\\``) fino alla prossima riga vuota o EOF.
    I commenti ``;`` all'inizio del blocco vengono scartati.
    """
    entries: list[str] = []
    current: list[str] = []
    for line in registry_block.splitlines():
        if line.strip() == "":
            if current:
                entries.append("\n".join(current))
                current = []
        elif line.lstrip().startswith(";"):
            # commenti: scarta (oppure mantieni ma non iniziano entry)
            continue
        else:
            current.append(line)
    if current:
        entries.append("\n".join(current))
    return entries


# =====================================================================
# Header / intent
# =====================================================================


def test_header_documents_dual_mode(installer_text: str) -> None:
    """L'header deve menzionare esplicitamente entrambe le modalita'."""
    assert "per-user" in installer_text, "Header non menziona 'per-user'"
    assert "per-machine" in installer_text, "Header non menziona 'per-machine'"
    # Deve citare il flag build-time InstallScope
    assert "InstallScope" in installer_text, "Header non menziona /DInstallScope="


def test_header_documents_no_uac(installer_text: str) -> None:
    """L'header deve vantare esplicitamente il supporto no-UAC."""
    assert "UAC" in installer_text, "Header non menziona 'UAC'"


# =====================================================================
# [Setup] block
# =====================================================================


def test_setup_uses_lowest_privileges(setup_block: str) -> None:
    """PrivilegesRequired=lowest consente all'installer di partire senza UAC."""
    assert re.search(
        r"^PrivilegesRequired\s*=\s*lowest", setup_block, re.MULTILINE
    ), "[Setup] deve avere 'PrivilegesRequired=lowest'"


def test_setup_overrides_allowed_dialog(setup_block: str) -> None:
    """L'elevation prompt appare solo se serve (scelta per-machine)."""
    assert re.search(
        r"^PrivilegesRequiredOverridesAllowed\s*=\s*.*dialog", setup_block, re.MULTILINE
    ), "[Setup] deve avere 'PrivilegesRequiredOverridesAllowed=dialog'"


def test_setup_default_dirname_is_dynamic(setup_block: str) -> None:
    """DefaultDirName deve usare la funzione Pascal GetDefaultDirName."""
    assert re.search(
        r"^DefaultDirName\s*=\s*\{code:GetDefaultDirName\}",
        setup_block,
        re.MULTILINE,
    ), (
        "[Setup] deve avere 'DefaultDirName={code:GetDefaultDirName}' "
        "(dinamico in base alla scelta per-user/per-machine)"
    )


def test_setup_default_dirname_not_hardcoded(setup_block: str) -> None:
    """DefaultDirName NON deve essere hardcoded a {autopf} (romperebbe per-user)."""
    # Cerca pattern "DefaultDirName={autopf}" esplicito (senza {code:...})
    matches = re.findall(
        r"^DefaultDirName\s*=\s*\{autopf\}",
        setup_block,
        re.MULTILINE,
    )
    assert not matches, (
        "[Setup] NON deve avere DefaultDirName hardcoded a {autopf}: "
        "rompe l'installazione per-user"
    )


def test_setup_app_mutex_present(setup_block: str) -> None:
    """AppMutex deve essere presente (anche se dinamico)."""
    assert re.search(r"^AppMutex\s*=", setup_block, re.MULTILINE), (
        "[Setup] deve avere AppMutex=... (anche dinamico via SetSetupSetting)"
    )


def test_setup_appid_present(setup_block: str) -> None:
    """AppId deve essere presente (UUID Inno Setup).

    Inno Setup usa ``{{...}}`` per escapare le graffe nelle directive
    (vedi docs su Setup directive syntax). Accettiamo entrambe le forme.
    """
    assert re.search(
        r'^AppId\s*=\s*\{+[A-Fa-f0-9\-]+\}+', setup_block, re.MULTILINE
    ), "[Setup] deve avere AppId={UUID} (con {{...}} o {...})"


def test_setup_no_static_admin_privileges(setup_block: str) -> None:
    """PrivilegesRequired NON deve essere admin (romperebbe per-user)."""
    # Cerca pattern "PrivilegesRequired=admin" o "=poweruser"
    for forbidden in ("admin", "poweruser"):
        matches = re.findall(
            rf"^PrivilegesRequired\s*=\s*{forbidden}\b",
            setup_block,
            re.MULTILINE,
        )
        assert not matches, (
            f"[Setup] NON deve avere PrivilegesRequired={forbidden}: "
            "rompe l'installazione per-user"
        )


# =====================================================================
# InstallScope macro
# =====================================================================


def test_install_scope_macro_present(installer_text: str) -> None:
    """#ifndef InstallScope con default 'either' deve esistere."""
    m = re.search(
        r"#ifndef\s+InstallScope\s*\n\s*#define\s+InstallScope\s+\"([a-z\-]+)\"",
        installer_text,
    )
    assert m is not None, (
        "Manca '#ifndef InstallScope / #define InstallScope \"...\"' "
        "in cima al file (build-time default scope)"
    )
    # Default accettati: "either", "per-user", "per-machine"
    assert m.group(1) in ("either", "per-user", "per-machine"), (
        f"InstallScope default '{m.group(1)}' non e' un valore riconosciuto "
        "(attesi: either, per-user, per-machine)"
    )


def test_install_scope_macro_used_in_code(code_block: str) -> None:
    """Il macro {#InstallScope} deve essere referenziato nella sezione [Code]."""
    assert "{#InstallScope}" in code_block, (
        "[Code] non referenzia il macro {#InstallScope}: "
        "DefaultInstallScope() non potra' rispettare il build-time scope"
    )


# =====================================================================
# [Registry] block
# =====================================================================


def test_registry_has_machine_hive_entries(registry_block: str) -> None:
    """Tutte le voci HKLM devono essere condizionate a InstallUsesMachineHive.

    Le voci ``[Registry]`` sono multi-line con continuazioni ``\\``,
    quindi il ``Check:`` non e' sulla stessa riga di ``Root: HKLM;``.
    Splittiamo per entry (separate da righe vuote) e analizziamo ognuna.
    """
    entries = _split_registry_entries(registry_block)
    hklm_entries = [e for e in entries if re.search(r"^Root:\s*HKLM;", e, re.MULTILINE | re.IGNORECASE)]
    machine_check = [e for e in hklm_entries if "InstallUsesMachineHive" in e]
    assert len(hklm_entries) > 0, "[Registry] non ha voci HKLM"
    assert len(machine_check) == len(hklm_entries), (
        "[Registry] ha {n_hklm} voci HKLM ma solo {n_check} sono condizionate "
        "a InstallUsesMachineHive".format(n_hklm=len(hklm_entries), n_check=len(machine_check))
    )


def test_registry_has_user_hive_entries(registry_block: str) -> None:
    """Tutte le voci HKCU devono essere condizionate a InstallUsesUserHive."""
    entries = _split_registry_entries(registry_block)
    hkcu_entries = [e for e in entries if re.search(r"^Root:\s*HKCU;", e, re.MULTILINE | re.IGNORECASE)]
    user_check = [e for e in hkcu_entries if "InstallUsesUserHive" in e]
    assert len(hkcu_entries) > 0, "[Registry] non ha voci HKCU"
    assert len(user_check) == len(hkcu_entries), (
        "[Registry] ha {n_hkcu} voci HKCU ma solo {n_check} sono condizionate "
        "a InstallUsesUserHive".format(n_hkcu=len(hkcu_entries), n_check=len(user_check))
    )


def test_registry_hkcu_hklu_have_same_count(registry_block: str) -> None:
    """Le voci HKLM e HKCU devono essere speculari (entrambe le modalita' hanno
    tutti i metadati uninstall: DisplayName, Publisher, EstimatedSize, ecc.)."""
    entries = _split_registry_entries(registry_block)
    hklm_count = sum(
        1 for e in entries
        if re.search(r"^Root:\s*HKLM;", e, re.MULTILINE | re.IGNORECASE)
    )
    hkcu_count = sum(
        1 for e in entries
        if re.search(r"^Root:\s*HKCU;", e, re.MULTILINE | re.IGNORECASE)
    )
    assert hklm_count == hkcu_count, (
        "[Registry] ha {n_hklm} voci HKLM ma {n_hkcu} voci HKCU: "
        "dovrebbero essere speculari per simmetria uninstall".format(
            n_hklm=hklm_count, n_hkcu=hkcu_count
        )
    )


def test_registry_no_legacy_isadmin_check(registry_block: str) -> None:
    """Non devono esserci piu' riferimenti a IsAdminInstall (sostituito da
    InstallUsesMachineHive / InstallUsesUserHive).

    Cerchiamo sia a livello di blocco (per coprire commenti o pattern
    residui) sia a livello di entry (per le voci attive).
    """
    legacy_block = re.findall(
        r"Check:\s*(?:not\s+)?IsAdminInstall\b",
        registry_block,
    )
    entries = _split_registry_entries(registry_block)
    legacy_entries = [
        e for e in entries if re.search(r"Check:\s*(?:not\s+)?IsAdminInstall\b", e)
    ]
    assert not legacy_block and not legacy_entries, (
        "[Registry] ha ancora riferimenti legacy a IsAdminInstall "
        "(blocco: {n_block}, entry: {n_entry}): usare InstallUsesMachineHive "
        "/ InstallUsesUserHive".format(n_block=len(legacy_block), n_entry=len(legacy_entries))
    )


def test_registry_subkey_uses_appid(registry_block: str) -> None:
    """Tutte le voci uninstall devono usare l'AppId corrente (non hardcoded)."""
    # Cerca path con GUID hardcoded di sviluppo o UUID placeholder
    forbidden_guids = [
        "{A1B2C3D4-",  # placeholder di sviluppo (vedi scripts/new_appid.py)
        "{A1B2C3D4-E5F6-7890-ABCD-1234567890AB}",
        "{00000000-0000-0000-0000-000000000000}",  # zero UUID (mai valido)
    ]
    for forbidden in forbidden_guids:
        assert forbidden not in registry_block, (
            f"[Registry] contiene GUID hardcoded '{forbidden}': "
            "usare {#SetupSetting('AppId')} per evitare drift"
        )
    # Deve referenziare l'AppId via macro
    assert "{#SetupSetting('AppId')}" in registry_block, (
        "[Registry] non usa '{#SetupSetting('AppId')}' per le subkey uninstall: "
        "le voci rischiano di divergere se AppId viene rigenerato"
    )


# =====================================================================
# [Run] block
# =====================================================================


def test_run_pandoc_uses_machine_install_check(run_block: str) -> None:
    """L'MSI pandoc deve avere Check: PandocNeededAndMachineInstall (skip per-user)."""
    assert "PandocNeededAndMachineInstall" in run_block, (
        "[Run] MSI pandoc deve avere 'Check: PandocNeededAndMachineInstall' "
        "per skippare in installazioni per-user"
    )
    assert "msiexec.exe" in run_block, "[Run] deve lanciare msiexec.exe per pandoc"


def test_run_pandoc_not_legacy_check(run_block: str) -> None:
    """Niente piu' Check: PandocNeeded senza guard sullo scope."""
    # Cerca pattern "Check: PandocNeeded" standalone (senza "AndMachineInstall")
    matches = re.findall(
        r"Check:\s*PandocNeeded\b(?!AndMachineInstall)",
        run_block,
    )
    assert not matches, (
        f"[Run] ha {len(matches)} riferimenti a 'Check: PandocNeeded' standalone: "
        "usare 'Check: PandocNeededAndMachineInstall' per skippare per-user"
    )


# =====================================================================
# [Code] block: Pascal functions
# =====================================================================


def test_code_contains_required_functions(code_block: str) -> None:
    """Le funzioni Pascal chiave devono essere definite."""
    required = [
        "function GetDefaultDirName",
        "function InstallUsesMachineHive",
        "function InstallUsesUserHive",
        "function GetInstallScope",
        "function DefaultInstallScope",
        "function PandocNeededAndMachineInstall",
        "procedure InitializeWizard",
        "procedure CurPageChanged",
        "procedure UpdateAppMutexForScope",
        "procedure InstallModePageProc",
    ]
    missing = [f for f in required if f not in code_block]
    assert not missing, (
        f"[Code] mancano le seguenti funzioni/procedure Pascal: {missing}"
    )


def test_code_declares_wizard_state(code_block: str) -> None:
    """Lo stato wizard dual-mode deve essere dichiarato."""
    required_vars = [
        "InstallModePage",
        "RadioPerUser",
        "RadioPerMachine",
        "ChosenScope",
    ]
    missing = [v for v in required_vars if v not in code_block]
    assert not missing, (
        f"[Code] mancano le seguenti variabili globali: {missing}"
    )


def test_code_initializes_wizard_page(code_block: str) -> None:
    """InitializeWizard deve creare la InstallModePage con CreateCustomPage."""
    init_block = re.search(
        r"procedure\s+InitializeWizard.*?^(?=^procedure|^function)",
        code_block,
        re.MULTILINE | re.DOTALL,
    )
    assert init_block is not None, "InitializeWizard non trovata"
    body = init_block.group(0)
    assert "CreateCustomPage" in body, "InitializeWizard non chiama CreateCustomPage"
    assert "wpWelcome" in body, "InitializeWizard non posiziona la pagina dopo wpWelcome"
    assert "RadioPerUser" in body, "InitializeWizard non crea RadioPerUser"
    assert "RadioPerMachine" in body, "InitializeWizard non crea RadioPerMachine"


def test_code_curpagechanged_updates_dir(code_block: str) -> None:
    """CurPageChanged deve aggiornare DirEdit quando l'utente naviga."""
    cur_block = re.search(
        r"procedure\s+CurPageChanged.*?^(?=^procedure|^function)",
        code_block,
        re.MULTILINE | re.DOTALL,
    )
    assert cur_block is not None, "CurPageChanged non trovata"
    body = cur_block.group(0)
    assert "DirEdit" in body, "CurPageChanged non aggiorna DirEdit"
    assert "GetDefaultDirName" in body, "CurPageChanged non chiama GetDefaultDirName"


def test_code_default_install_scope_respects_macro(code_block: str) -> None:
    """DefaultInstallScope deve confrontarsi con il macro {#InstallScope}."""
    default_block = re.search(
        r"function\s+DefaultInstallScope.*?^(?=^function|^procedure)",
        code_block,
        re.MULTILINE | re.DOTALL,
    )
    assert default_block is not None, "DefaultInstallScope non trovata"
    body = default_block.group(0)
    assert "{#InstallScope}" in body, (
        "DefaultInstallScope non legge il macro {#InstallScope}: "
        "il build-time scope verra' ignorato"
    )
    # Deve gestire tutti e tre i valori
    assert "per-user" in body, "DefaultInstallScope non gestisce 'per-user'"
    assert "per-machine" in body, "DefaultInstallScope non gestisce 'per-machine'"


def test_code_update_app_mutex_dynamic(code_block: str) -> None:
    """UpdateAppMutexForScope deve usare SetSetupSetting per AppMutex dinamico."""
    update_block = re.search(
        r"procedure\s+UpdateAppMutexForScope.*?^(?=^procedure|^function)",
        code_block,
        re.MULTILINE | re.DOTALL,
    )
    assert update_block is not None, "UpdateAppMutexForScope non trovata"
    body = update_block.group(0)
    assert "SetSetupSetting" in body, (
        "UpdateAppMutexForScope non chiama SetSetupSetting: "
        "AppMutex restera' statico (collisioni tra per-user e per-machine)"
    )
    assert "AppMutex" in body, "UpdateAppMutexForScope non menziona 'AppMutex'"
    # L'identificatore deve includere lo scope per distinguerli
    assert "GetInstallScope" in body, (
        "UpdateAppMutexForScope non usa GetInstallScope: "
        "i mutex per-user e per-machine collideranno"
    )


# =====================================================================
# Output filename
# =====================================================================


def test_output_base_filename_overridable(installer_text: str) -> None:
    """OutputBaseFilename deve essere derivato da un macro (#ifndef)."""
    # Cerca "#ifndef OutputBaseFilename / #define OutputBaseFilename"
    m = re.search(
        r"#ifndef\s+OutputBaseFilename\s*\n\s*#define\s+OutputBaseFilename\s+\"([^\"]+)\"",
        installer_text,
    )
    assert m is not None, (
        "Manca #ifndef OutputBaseFilename: non e' possibile generare "
        "varianti '-peruser.exe' al build"
    )
    # Deve referenziare {#MyAppVersion} per restare consistente
    assert "{#MyAppVersion}" in m.group(1), (
        "OutputBaseFilename default '{0}' non usa {{#MyAppVersion}}".format(m.group(1))
    )


# =====================================================================
# Sentinel pattern (ripreso da installer 0.1.x, regression test)
# =====================================================================


def test_code_sentinel_still_present(code_block: str) -> None:
    """Le utility sentinel (WriteSentinel, ClearSentinel) devono restare invariate."""
    # La 0.2.0 mantiene il pattern sentinel introdotto in 0.1.1 per
    # rilevare installazioni interrotte. Verifichiamo che non sia stato
    # accidentalmente rimosso durante il refactor dual-mode.
    assert "procedure WriteSentinel" in code_block, "WriteSentinel mancante"
    assert "procedure ClearSentinel" in code_block, "ClearSentinel mancante"
    assert "SENTINEL_FILE" in code_block, "Costante SENTINEL_FILE mancante"


def test_code_uninstall_safety_still_present(code_block: str) -> None:
    """Le utility di cleanup uninstall (IsOrphanLnk, CleanupOrphanShortcuts)
    devono restare invariate."""
    assert "function IsOrphanLnk" in code_block, "IsOrphanLnk mancante"
    assert "procedure CleanupOrphanShortcuts" in code_block, "CleanupOrphanShortcuts mancante"


# =====================================================================
# Smoke: nessun BOM stray nelle sezioni critiche
# =====================================================================


def test_no_bom_in_code_block(code_block: str) -> None:
    """Il contenuto di [Code] non deve avere un BOM UTF-8 stray a meta."""
    assert "\ufeff" not in code_block, (
        "Trovato carattere BOM UTF-8 (U+FEFF) dentro [Code]: "
        "potrebbe rompere la compilazione Pascal-script"
    )
