"""
Tor + Discord Launcher
------------------------
App unico embutido num .exe so (via PyInstaller --onefile).

tor.exe, torrc, geoip e geoip6 ficam EMPACOTADOS DENTRO do .exe
final (nao ficam soltos do lado). Em tempo de execucao, o script
extrai esses arquivos para uma pasta temporaria do Windows
(%TEMP%\\Obscure Proxy), roda o Tor de la, e ao fechar o
app tenta limpar essa pasta. Do lado do .exe, nao sobra nada.

--------------------------------------------------------------
COMO EMPACOTAR (gerar o .exe com os arquivos embutidos):

    python -m PyInstaller --onefile --noconsole --name "Obscure Proxy" ^
        --icon "app_icon.ico" ^
        --add-data "app_icon.ico;." ^
        --add-data "tor.exe;." ^
        --add-data "torrc;." ^
        --add-data "geoip;." ^
        --add-data "geoip6;." ^
        tor_app.py
--------------------------------------------------------------
"""

import os
import re
import sys
import glob
import json
import ctypes
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.error
import webview
import pystray
from PIL import Image

# ---------- Otimizacoes de Performance ----------
# Desativa a aceleracao de hardware (GPU) do WebView2
# para evitar quedas de FPS em jogos e reduzir o uso do PC.
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer"

# ---------- Instancia unica (impede multi-open) ----------
_SINGLE_INSTANCE_MUTEX_NAME = "Global\\ObscureProxy_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS       = 183

def ensure_single_instance():
    """Cria um mutex nomeado do Windows. Se ele ja existir, outra instancia
    do app ja esta rodando: traz a janela dela pra frente (ou avisa por
    MessageBox, se nao achar a janela) e encerra esta segunda instancia.
    O handle retornado precisa ficar vivo ate o app fechar, senao o
    mutex e liberado e o multi-open volta a acontecer."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        hwnd = ctypes.windll.user32.FindWindowW(None, "Obscure Proxy")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        else:
            ctypes.windll.user32.MessageBoxW(0, "Obscure Proxy ja esta em execucao.", "Obscure Proxy", 0x40)
        sys.exit(0)
    return handle

# ---------- Versao e GitHub ----------
CURRENT_VERSION     = "1.0.0"
GITHUB_USER         = "SEU_USUARIO_AQUI"
GITHUB_REPO         = "SEU_REPOSITORIO_AQUI"
UPDATE_VERSION_URL  = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.txt"
UPDATE_DOWNLOAD_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/latest/download/Obscure Proxy.exe"

WIN_W, WIN_H = 980, 840


# ============================================================
# Logica de diretorios / arquivos
# ============================================================

def bundled_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def runtime_data_dir():
    d = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "Obscure Proxy")
    os.makedirs(d, exist_ok=True)
    return d


def extract_tor_files():
    src = bundled_dir()
    dst = runtime_data_dir()
    for fname in ("tor.exe", "torrc", "geoip", "geoip6"):
        src_path = os.path.join(src, fname)
        dst_path = os.path.join(dst, fname)
        if os.path.isfile(src_path):
            if fname == "torrc" or not os.path.isfile(dst_path):
                shutil.copy2(src_path, dst_path)
    return dst


def app_dir():
    return extract_tor_files()


def cleanup_runtime_dir():
    try:
        shutil.rmtree(runtime_data_dir(), ignore_errors=True)
    except Exception:
        pass


def _query_registry_install(display_name):
    """Procura no registro do Windows o InstallLocation de um programa pelo DisplayName."""
    import winreg
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as root:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, subkey_name) as sk:
                            try:
                                name, _ = winreg.QueryValueEx(sk, "DisplayName")
                                if display_name.lower() in name.lower():
                                    try:
                                        loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                                        if loc:
                                            return loc.strip().rstrip("\\")
                                    except FileNotFoundError:
                                        pass
                            except FileNotFoundError:
                                pass
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
    return None


def _get_running_exe_path(process_name):
    """Pega o caminho completo de um processo rodando pelo nome via WMIC."""
    try:
        cmd = f'cmd /c "wmic process where name=\'{process_name}\' get executablepath /value 2>nul"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                             creationflags=0x08000000, timeout=5)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("ExecutablePath="):
                path = line.split("=", 1)[1].strip()
                if path and os.path.isfile(path):
                    return path
    except Exception:
        pass
    return None


# Mapeamento: nome interno -> (nome do processo .exe, nomes de registro, pastas comuns)
DISCORD_CLIENTS = {
    "Discord":        {"exe": "Discord.exe",        "reg": "Discord",        "folders": ["Discord"]},
    "DiscordPTB":     {"exe": "DiscordPTB.exe",     "reg": "Discord PTB",    "folders": ["DiscordPTB"]},
    "DiscordCanary":  {"exe": "DiscordCanary.exe",  "reg": "Discord Canary", "folders": ["DiscordCanary"]},
    "Dorion":         {"exe": "Dorion.exe",          "reg": "Dorion",         "folders": ["Dorion"]},
}


def _find_exe_for_client(client_name):
    """Acha o executavel de um cliente Discord especifico. Retorna o path ou None."""
    info = DISCORD_CLIENTS.get(client_name)
    if not info:
        return None

    exe_name = info["exe"]

    # 1. Processo rodando? Pega o path direto do Windows
    path = _get_running_exe_path(exe_name)
    if path:
        return path

    # 2. Registro do Windows (acha qualquer disco/pasta)
    reg_loc = _query_registry_install(info["reg"])
    if reg_loc:
        candidate = os.path.join(reg_loc, exe_name)
        if os.path.isfile(candidate):
            return candidate

    # 3. Pastas conhecidas como fallback
    localappdata = os.environ.get("LOCALAPPDATA", "")
    for folder in info["folders"]:
        # Squirrel (app-*)
        base = os.path.join(localappdata, folder)
        if os.path.isdir(base):
            pattern = os.path.join(base, "app-*", exe_name)
            matches = glob.glob(pattern)
            if matches:
                matches.sort()
                return matches[-1]
            direct = os.path.join(base, exe_name)
            if os.path.isfile(direct):
                return direct

    return None


def find_discord_exe(preferred_client="auto"):
    """Retorna uma lista com o path do executavel do cliente escolhido, ou [] se nao achar."""
    if preferred_client != "auto":
        path = _find_exe_for_client(preferred_client)
        return [path] if path else []

    # Auto: procura qual esta aberto primeiro, depois tenta achar qualquer um instalado
    for name in ("Dorion", "DiscordCanary", "DiscordPTB", "Discord"):
        info = DISCORD_CLIENTS[name]
        path = _get_running_exe_path(info["exe"])
        if path:
            return [path]

    for name in ("Dorion", "DiscordCanary", "DiscordPTB", "Discord"):
        path = _find_exe_for_client(name)
        if path:
            return [path]

    return []

def detect_socks_port(torrc_path, fallback="9050"):
    try:
        with open(torrc_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r"\s*SocksPort\s+(\d+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return fallback

def get_settings_file():
    d = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Obscure Proxy")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")

def load_settings():
    sf = get_settings_file()
    if os.path.isfile(sf):
        try:
            with open(sf, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_settings_to_disk(data):
    sf = get_settings_file()
    try:
        with open(sf, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

# ============================================================
# Auto-updater
# ============================================================

def check_for_update():
    try:
        with urllib.request.urlopen(UPDATE_VERSION_URL, timeout=5) as r:
            latest = r.read().decode().strip()
        def _ver(v):
            return tuple(int(x) for x in v.strip().lstrip("v").split("."))
        if _ver(latest) > _ver(CURRENT_VERSION):
            return latest
    except Exception:
        pass
    return None


def perform_update(new_exe_dest, progress_callback=None):
    tmp_path = new_exe_dest + ".tmp"
    try:
        with urllib.request.urlopen(UPDATE_DOWNLOAD_URL, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 65536
            with open(tmp_path, "wb") as f:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if total and progress_callback:
                        progress_callback(int(downloaded * 100 / total))
        os.replace(tmp_path, new_exe_dest)
        return True
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


def launch_self_replace_and_restart(new_exe_path, current_exe_path):
    bat = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "TorDiscordUpdater.bat")
    script = (
        "@echo off\n"
        ":wait\n"
        f"  2>nul (\n"
        f"    del /f \"{current_exe_path}\"\n"
        "  ) && goto replace\n"
        "  timeout /t 1 /nobreak >nul\n"
        "  goto wait\n"
        ":replace\n"
        f"  move /y \"{new_exe_path}\" \"{current_exe_path}\"\n"
        f"  start \"\" \"{current_exe_path}\"\n"
        f"  del \"%~f0\"\n"
    )
    with open(bat, "w") as f:
        f.write(script)
    subprocess.Popen(["cmd", "/c", bat],
                     creationflags=subprocess.CREATE_NO_WINDOW,
                     close_fds=True)


# ============================================================
# HTML da interface (inline — sem arquivos externos)
# ============================================================

HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Obscure Proxy</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;border:none;outline:none;}
:root{
  --bg:#0d0f15;--sidebar:#08090d;--card:#12151c;--panel:#181c26;
  --input:#1e2230;--fg:#e8e9ec;--muted:#6b7280;--dim:#374151;
  --purple:#7c5cff;--purple-glow:#9d7eff;--discord:#5865f2;
  --green:#22c55e;--border:#1f2433;--border2:#2d3347;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--fg);font-family:'Segoe UI',Arial,sans-serif;font-size:13px;}
button{cursor:pointer;background:none;color:inherit;font:inherit;}
input{font:inherit;color:var(--fg);background:var(--input);}

/* titlebar */
#titlebar{
  position:relative;
  height:56px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:0 20px;border-bottom:1px solid var(--border);background:#0b0d12;
  user-select:none;
  cursor:grab;
}
#tor-title{
  display:flex;align-items:center;gap:11px;
  /* fills the entire 1fr left column — big drag area */
}
.onion-mark{
  width:32px;height:32px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--purple),#4c3a9b);border-radius:8px;color:#fff;flex:none;
}
.onion-mark svg{width:17px;height:17px;}
#tor-title-text strong{font-size:13px;display:block;}
#tor-title-text span{display:block;margin-top:2px;color:var(--muted);font-size:11px;}
#titlebar-status{
  position:absolute;
  left:216px;right:0;top:0;bottom:0;
  display:flex;align-items:center;justify-content:center;gap:7px;
  color:var(--muted);font-size:11px;
  pointer-events:none;
}
#titlebar-dot{width:7px;height:7px;border-radius:50%;background:#ef4444;display:inline-block;transition:background .3s;}
#titlebar-dot.green{background:var(--green);}
#titlebar-dot.purple{background:var(--purple);animation:blink .9s infinite alternate;}
@keyframes blink{to{opacity:.35;}}
.win-controls{display:flex;gap:6px;justify-self:end;-webkit-app-region:no-drag;}
.win-btn{
  width:28px;height:28px;border-radius:5px;display:grid;place-items:center;
  color:var(--muted);transition:background .15s,color .15s;
}
.win-btn:hover{background:#1e2230;color:var(--fg);}
#btn-close:hover{background:#c0392b!important;color:#fff!important;}

/* layout */
#layout{display:flex;height:calc(100vh - 56px);}
#sidebar{
  width:216px;flex:none;display:flex;flex-direction:column;
  background:var(--sidebar);border-right:1px solid var(--border);
}
.sidebar-caption{padding:20px 20px 14px;color:var(--dim);font-size:9px;font-weight:700;letter-spacing:.14em;}
#tor-nav{display:flex;flex-direction:column;gap:4px;}
.nav-btn{
  display:flex;align-items:center;gap:11px;width:100%;height:46px;padding:0 20px;
  color:var(--muted);background:transparent;
  border-left:3px solid transparent;font-size:12px;transition:background .12s,color .12s;
}
.nav-btn svg{width:16px;height:16px;flex:none;}
.nav-btn:hover{background:#14171f;color:var(--fg);}
.nav-btn.active{color:#fff;background:#181c26;border-left-color:var(--purple);}
.nav-btn.active svg{color:var(--purple-glow);}
.sidebar-bottom{margin-top:auto;padding:20px;}
.side-line{height:1px;margin-bottom:18px;background:var(--border);}
.runtime{display:flex;flex-direction:column;}
.runtime span{color:var(--dim);font-size:8px;font-weight:700;letter-spacing:.1em;}
.runtime strong{margin-top:8px;color:var(--muted);font-size:10px;font-weight:400;display:flex;align-items:center;gap:6px;}
.side-dot{width:7px;height:7px;border-radius:50%;background:#ef4444;display:inline-block;transition:background .3s;flex:none;}
.side-dot.green{background:var(--green);}
.version{display:block;margin-top:18px;color:var(--dim);font-size:10px;}

/* content */
#content{flex:1;min-width:0;overflow:hidden;padding:30px 38px 36px;background:linear-gradient(135deg,#10131a,#0d0f15 65%);display:flex;flex-direction:column;}
.page{display:none;flex-direction:column;flex:1;min-height:0;overflow-y:auto;}
.page.active{display:flex;}
#page-log{overflow-y:hidden;}
.page-heading{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:26px;}
.section-kicker{color:var(--purple-glow);font-size:9px;font-weight:700;letter-spacing:.16em;display:block;}
.page-heading h1{margin:9px 0 7px;font-size:22px;letter-spacing:-.03em;}
.page-heading p{margin:0;color:var(--muted);font-size:12px;}
.help-btn{display:flex;align-items:center;gap:7px;color:var(--muted);background:var(--panel);border:1px solid var(--border);border-radius:5px;padding:9px 11px;font-size:11px;}
.help-btn svg{width:14px;height:14px;}

/* hero card */
.hero-card{padding:20px 24px 23px;background:var(--card);border:1px solid var(--border2);border-radius:8px;box-shadow:inset 0 1px #ffffff05;transition:border-color .3s;}
.hero-card.connected{border-color:#205b37;}
.hero-card.connecting{border-color:#3e2c76;}
.hero-top{display:flex;justify-content:space-between;align-items:center;}
.status-chip{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:9px;font-weight:700;letter-spacing:.1em;}
.chip-dot{width:7px;height:7px;border-radius:50%;background:#ef4444;transition:background .3s;}
.connected .chip-dot{background:var(--green);}
.connecting .chip-dot{background:var(--purple);}
.tor-address{color:var(--dim);font-family:monospace;font-size:10px;}
.orb-wrap{display:flex;flex-direction:column;align-items:center;padding:30px 0 17px;}
.orb{
  position:relative;display:grid;place-items:center;
  width:142px;height:142px;border-radius:50%;
  background:var(--panel);border:2px solid #3a3f52;transition:border-color .3s;
}
.connected .orb{border-color:var(--green);}
.orb-ring{
  position:absolute;inset:-5px;border-radius:50%;
  background:conic-gradient(from -90deg,var(--purple-glow) 0deg,var(--purple) 120deg,transparent 120deg);
  -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 4px),#000 0);
  mask:radial-gradient(farthest-side,transparent calc(100% - 4px),#000 0);
  transition:transform .18s linear;opacity:0;
}
.connecting .orb-ring{opacity:1;animation:orb-spin 1.2s linear infinite;}
.connected .orb-ring{opacity:1;background:conic-gradient(from -90deg,#4ade80,var(--green));animation:none;transform:rotate(0deg)!important;}
@keyframes orb-spin{to{transform:rotate(360deg);}}
.orb-core{display:grid;place-items:center;width:92px;height:92px;border-radius:50%;background:#1e2230;color:var(--muted);transition:color .3s;}
.connecting .orb-core{color:var(--purple-glow);}
.connected .orb-core{color:var(--green);}
.orb-core svg{width:38px;height:38px;}
.orb-label{margin-top:12px;color:var(--muted);font-size:11px;}
.hero-copy{display:flex;flex-direction:column;align-items:center;text-align:center;}
.hero-copy h2{font-size:17px;}
.hero-copy p{margin:7px 0 0;color:var(--muted);font-size:11px;}
.progress-track{width:70%;height:4px;margin:20px auto 0;background:var(--panel);border-radius:10px;display:none;}
.connecting .progress-track{display:block;}
.progress-fill{height:100%;background:var(--purple);border-radius:10px;transition:width .14s;}
.main-connect{
  display:flex;align-items:center;justify-content:center;gap:9px;
  width:250px;height:43px;margin:23px auto 0;color:#fff;
  background:var(--purple);border-radius:5px;box-shadow:0 8px 24px #7c5cff33;
  font-size:12px;font-weight:700;transition:background .15s;
}
.main-connect:hover{background:var(--purple-glow);}
.main-connect.disconnect{background:#ef4444;}
.main-connect.disconnect:hover{background:#c0392b;}
.main-connect.loading{background:#2a2e3d;cursor:not-allowed;pointer-events:none;}
.main-connect svg{width:15px;height:15px;}

/* quick grid */
.quick-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;}
.quick-card{display:flex;align-items:center;gap:12px;min-height:76px;padding:15px;background:var(--card);border:1px solid var(--border);border-radius:6px;}
.quick-icon{display:grid;place-items:center;width:32px;height:32px;flex:none;border-radius:6px;}
.quick-icon svg{width:16px;height:16px;}
.quick-icon.purple{color:var(--purple-glow);background:#251d45;}
.quick-icon.blue{color:#8790ff;background:#20244a;}
.quick-card-info{min-width:0;flex:1;}
.quick-card-info span{display:block;color:var(--dim);font-size:8px;font-weight:700;letter-spacing:.1em;}
.quick-card-info strong{display:block;margin-top:5px;font-family:monospace;font-size:11px;}
.quick-card-info small{display:block;margin-top:3px;color:var(--muted);font-size:10px;}
.quick-card>button{margin-left:auto;color:var(--muted);}
.quick-card>button svg{width:15px;height:15px;}
.quick-card>button:hover{color:var(--fg);}
.notice{display:flex;align-items:center;gap:12px;margin-top:12px;padding:13px 15px;background:#141126;border:1px solid #30255b;border-radius:6px;}
.notice>svg{width:17px;height:17px;color:var(--purple-glow);flex:none;}
.notice-info{flex:1;}
.notice-info strong{display:block;font-size:11px;}
.notice-info span{display:block;margin-top:3px;color:var(--muted);font-size:10px;}
.notice>button{color:var(--purple-glow);font-size:10px;}

/* settings */
.settings-page{display:flex;flex-direction:column;gap:12px;max-width:600px;}
.settings-card{padding:22px;background:var(--card);border:1px solid var(--border);border-radius:7px;}
.card-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px;}
.card-title h2{margin:8px 0 0;font-size:16px;}
.card-title>svg{color:var(--purple-glow);width:20px;height:20px;}
.field-label{display:block;color:var(--fg);font-size:11px;font-weight:700;}
.field-label input{display:block;width:100%;margin-top:9px;padding:11px;background:var(--input);border:1px solid var(--border2);border-radius:4px;font-family:monospace;}
::-webkit-scrollbar{width:6px;height:6px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px;}
::-webkit-scrollbar-thumb:hover{background:var(--muted);}

.field-label input:focus{border-color:var(--purple);outline:none;}
.custom-select{position:relative;display:block;width:100%;margin-top:9px;font-family:inherit;font-weight:400;}
.select-selected{background:var(--input);border:1px solid var(--border2);border-radius:4px;padding:11px;color:var(--fg);cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:border-color .2s;}
.select-selected:hover{border-color:var(--dim);}
.select-selected.select-arrow-active{border-color:var(--purple);}
.select-selected:after{content:"";border:4px solid transparent;border-top-color:var(--muted);display:inline-block;transform:translateY(2.5px);}
.select-selected.select-arrow-active:after{border-top-color:transparent;border-bottom-color:var(--muted);transform:translateY(-2.5px);}
.select-items{position:absolute;background:var(--panel);border:1px solid var(--border);top:100%;left:0;right:0;z-index:99;border-radius:4px;margin-top:4px;overflow:hidden;box-shadow:0 8px 16px rgba(0,0,0,0.4);}
.select-items div{padding:11px;cursor:pointer;border-bottom:1px solid var(--border);transition:background .15s,color .15s;font-size:11px;}
.select-items div:last-child{border-bottom:none;}
.select-items div:hover{background:#1a1e29;color:var(--purple-glow);}
.select-hide{display:none;}
.field-label small{display:block;margin-top:6px;color:var(--muted);font-size:10px;font-weight:400;}
.setting{display:flex;align-items:center;gap:12px;padding:17px 0;border-top:1px solid var(--border);}
.setting:first-of-type{margin-top:19px;}
.setting-icon{display:grid;place-items:center;width:30px;height:30px;color:var(--purple-glow);background:#211a3d;border-radius:5px;flex:none;}
.setting-icon svg{width:15px;height:15px;}
.setting-info{flex:1;}
.setting-info strong{display:block;font-size:11px;}
.setting-info span{display:block;margin-top:4px;color:var(--muted);font-size:10px;}
.toggle{position:relative;width:34px;height:19px;padding:2px;background:var(--dim);border-radius:20px;transition:background .15s;flex:none;}
.toggle-thumb{display:block;width:15px;height:15px;background:#9095a4;border-radius:50%;transition:transform .15s,background .15s;}
.toggle.on{background:var(--purple);}
.toggle.on .toggle-thumb{transform:translateX(15px);background:#fff;}
.info-card{display:flex;gap:14px;}
.info-card>svg{color:var(--purple-glow);width:19px;height:19px;flex:none;margin-top:3px;}
.info-card p{margin:10px 0 0;color:var(--muted);font-size:11px;line-height:1.5;}
.save-row{display:flex;align-items:center;gap:14px;margin-top:18px;}
.save-btn{padding:10px 20px;background:var(--purple);color:#fff;border-radius:5px;font-size:11px;font-weight:700;transition:background .15s;}
.save-btn:hover{background:var(--purple-glow);}
#settings-saved{color:var(--green);font-size:11px;display:none;}

/* log */
.log-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex:none;}
.log-toolbar h2{font-size:16px;}
.log-clear-btn{display:flex;align-items:center;gap:7px;color:var(--muted);background:var(--panel);border:1px solid var(--border);border-radius:5px;padding:9px 11px;font-size:11px;}
.log-clear-btn svg{width:14px;height:14px;}
.log-card{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;background:var(--card);border:1px solid var(--border);border-radius:7px;scrollbar-width:thin;scrollbar-color:var(--border2) transparent;}
.log-entry{display:flex;align-items:center;gap:13px;min-height:50px;padding:0 17px;border-bottom:1px solid var(--border);}
.log-entry:last-child{border:0;}
.log-time{width:42px;color:var(--dim);font-family:monospace;font-size:9px;}
.log-dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex:none;}
.log-dot.ok{background:var(--green);}
.log-dot.accent{background:var(--purple);}
.log-dot.discord{background:var(--discord);}
.log-dot.err{background:#ef4444;}
.log-dot.muted{background:var(--muted);}
.log-text{flex:1;color:#c8cad1;font-size:11px;word-break:break-all;}
.log-check svg{width:14px;height:14px;color:var(--green);}
.log-empty{display:flex;align-items:center;justify-content:center;gap:10px;min-height:170px;color:var(--muted);font-size:11px;}
.log-empty svg{width:17px;height:17px;}

  svg{display:inline-block;vertical-align:middle;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
  
  /* modal */
  .modal-overlay{
    position:absolute;top:0;left:0;right:0;bottom:0;
    background:rgba(0,0,0,0.6);
    display:none;align-items:center;justify-content:center;
    z-index:9999;
  }
  .modal-overlay.show{display:flex;}
  .modal-card{
    background:var(--card);border:1px solid var(--border);border-radius:8px;
    width:360px;padding:24px;box-shadow:0 12px 32px rgba(0,0,0,0.4);
    display:flex;flex-direction:column;gap:18px;
  }
  .modal-title{font-size:15px;font-weight:700;}
  .modal-text{color:var(--muted);font-size:12px;line-height:1.4;}
  .modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:6px;}
  .modal-btn{
    padding:8px 16px;border-radius:5px;font-size:12px;font-weight:600;
    background:var(--panel);color:var(--fg);border:1px solid var(--border);
    transition:background .15s;
  }
  .modal-btn:hover{background:var(--border2);}
  .modal-btn.primary{background:var(--purple);color:#fff;border-color:var(--purple);}
  .modal-btn.primary:hover{background:var(--purple-glow);}
  .modal-btn.danger{background:#ef4444;color:#fff;border-color:#ef4444;}
  .modal-btn.danger:hover{background:#c0392b;}
</style>
</head>
<body>

<!-- Titlebar -->
<div id="titlebar" class="pywebview-drag-region">
  <div id="tor-title">
    <div class="onion-mark">
      <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
    </div>
    <div id="tor-title-text">
      <strong>Obscure Proxy</strong>
      <span>Private connection</span>
    </div>
  </div>

  <div class="win-controls">
    <button class="win-btn" id="btn-min" title="Minimizar">&#8212;</button>
    <button class="win-btn" id="btn-close" title="Fechar">
      <svg viewBox="0 0 24 24" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
</div>

<!-- Layout -->
<div id="layout">

  <aside id="sidebar">
    <div class="sidebar-caption">PRIVATE CONNECTION</div>
    <nav id="tor-nav">
      <button class="nav-btn active" onclick="showPage('home',this)">
        <svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
        <span>Conexão</span>
      </button>
      <button class="nav-btn" onclick="showPage('settings',this)">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>Configurações</span>
      </button>
      <button class="nav-btn" onclick="showPage('log',this)">
        <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span>Atividade</span>
      </button>
    </nav>
    <div class="sidebar-bottom">
      <div class="side-line"></div>
      <div class="runtime">
        <span>STATUS DO SISTEMA</span>
        <strong><span id="side-dot" class="side-dot"></span><span id="side-label">Pronto para iniciar</span></strong>
      </div>
      <span class="version">v1.0.0</span>
    </div>
  </aside>

  <main id="content">

    <!-- HOME -->
    <section id="page-home" class="page active">
      <div class="page-heading">
        <div>
          <span class="section-kicker">OBSCURE PROXY / CONEXAO</span>
          <h1>Conecte sua sessao com privacidade</h1>
          <p>Proteja sua conexao com um proxy local privado.</p>
        </div>
        <button class="help-btn" onclick="addLog('Central de ajuda aberta','muted')">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          Ajuda
        </button>
      </div>

      <div id="hero-card" class="hero-card">
        <div class="hero-top">
          <div class="status-chip"><span class="chip-dot"></span><span id="status-chip-label">DESCONECTADO</span></div>
          <span class="tor-address">SOCKS5 / 127.0.0.1:<span id="port-display">9050</span></span>
        </div>
        <div class="orb-wrap">
          <div class="orb">
            <div class="orb-ring"></div>
            <div class="orb-core">
              <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </div>
          </div>
          <span class="orb-label" id="orb-label">Privacidade</span>
        </div>
        <div class="hero-copy">
          <h2 id="hero-title">Tudo pronto para comecar</h2>
          <p id="hero-sub">Inicie o proxy para rotear sua sessao por uma rede privada.</p>
        </div>
        <div class="progress-track"><div class="progress-fill" id="prog-fill" style="width:0%"></div></div>
        <button id="main-btn" class="main-connect" onclick="mainAction()">
          Iniciar conexao
          <svg viewBox="0 0 24 24" width="15" height="15"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>
        </button>
      </div>

      <div class="quick-grid">
        <div class="quick-card">
          <div class="quick-icon purple">
            <svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
          </div>
          <div class="quick-card-info">
            <span>PROXY LOCAL</span>
            <strong>127.0.0.1:<span id="proxy-port">9050</span></strong>
            <small>Porta SOCKS5 configurada</small>
          </div>
          <button onclick="copyProxy()" title="Copiar endereco do proxy">
            <svg id="copy-icon" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>
          <button id="renew-btn" onclick="renewCircuit()" title="Renovar circuito (use se a tela de alguem ficar carregando)">
            <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
          <button id="restart-discord-btn" onclick="restartDiscordOnly()" title="Reiniciar so o Discord (sem mexer no proxy) - use pra testar se o travamento e do Discord ou do proxy">
            <svg viewBox="0 0 24 24"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>
          </button>
          <button id="stream-status-btn" onclick="checkStreamStatus()" title="Ver quais conexoes estao passando pelo Tor agora (debug do bypass)">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          </button>
        </div>
        <div class="quick-card">
          <div class="quick-icon blue">
            <svg viewBox="0 0 24 24"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>
          </div>
          <div class="quick-card-info">
            <span>APLICATIVO</span>
            <strong id="app-status-strong">Aguardando servico</strong>
            <small id="app-status-small">Sera preparado automaticamente</small>
          </div>
        </div>
      </div>

      <div class="notice">
        <svg viewBox="0 0 24 24"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
        <div class="notice-info">
          <strong>Arquivos empacotados com seguranca</strong>
          <span>Os componentes necessarios sao extraidos apenas durante a execucao.</span>
        </div>
        <button onclick="showPage('settings',document.querySelectorAll('.nav-btn')[1])">Configurar</button>
      </div>
    </section>

    <!-- SETTINGS -->
    <section id="page-settings" class="page">
      <div class="page-heading">
        <div>
          <span class="section-kicker">OBSCURE PROXY / CONFIGURACOES</span>
          <h1>Configuracoes</h1>
          <p>Ajuste o comportamento do launcher.</p>
        </div>
      </div>
      <div class="settings-page">
        <div class="settings-card">
          <div class="card-title">
            <div>
              <span class="section-kicker">CONNECTION CONFIGURATION</span>
              <h2>Preferencias do launcher</h2>
            </div>
            <svg viewBox="0 0 24 24" width="20" height="20"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
          </div>
          <label class="field-label">Porta SOCKS5
            <input id="port-input" type="text" value="9050" maxlength="5" oninput="this.value=this.value.replace(/\\D/g,'').slice(0,5)"/>
            <small>Porta local usada pelo servico e pelo aplicativo.</small>
          </label>
          <label class="field-label" style="margin-top:15px; position: relative; z-index: 10;">Cliente Discord
            <div class="custom-select" id="discord-select">
              <div class="select-selected" onclick="toggleSelect()">Automático (Detectar aberto)</div>
              <div class="select-items select-hide">
                <div onclick="pickSelect('auto', 'Automático (Detectar aberto)')">Automático (Detectar aberto)</div>
                <div onclick="pickSelect('Discord', 'Discord Normal')">Discord Normal</div>
                <div onclick="pickSelect('DiscordPTB', 'Discord PTB')">Discord PTB</div>
                <div onclick="pickSelect('DiscordCanary', 'Discord Canary')">Discord Canary</div>
                <div onclick="pickSelect('Dorion', 'Dorion')">Dorion</div>
              </div>
            </div>
            <input type="hidden" id="client-input" value="auto" />
            <small>Escolha qual aplicativo iniciar com o proxy.</small>
          </label>
          <div class="setting" style="margin-top:15px;">
            <div class="setting-icon"><svg viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></div>
            <div class="setting-info">
              <strong>Modo desenvolvedor: multi-conta</strong>
              <span>Abre o proxy em mais de um cliente Discord ao mesmo tempo (ex: Discord + Canary), cada um com sua propria conta logada.</span>
            </div>
            <button class="toggle" id="tog-dev-multi" onclick="toggleDevMulti(this)"><i class="toggle-thumb"></i></button>
          </div>
          <div id="multi-client-group" style="display:none;">
            <div class="setting">
              <div class="setting-info"><strong>Discord Normal</strong></div>
              <button class="toggle" id="mc-Discord" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
            </div>
            <div class="setting">
              <div class="setting-info"><strong>Discord PTB</strong></div>
              <button class="toggle" id="mc-DiscordPTB" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
            </div>
            <div class="setting">
              <div class="setting-info"><strong>Discord Canary</strong></div>
              <button class="toggle" id="mc-DiscordCanary" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
            </div>
            <div class="setting">
              <div class="setting-info"><strong>Dorion</strong></div>
              <button class="toggle" id="mc-Dorion" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
            </div>
            <small>Marque 2 ou mais. Cada cliente ja usa sua propria pasta de dados, entao da pra logar contas diferentes em cada um sem derrubar a outra sessao.</small>
          </div>
          <div class="setting">
            <div class="setting-icon"><svg viewBox="0 0 24 24"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg></div>
            <div class="setting-info">
              <strong>Iniciar Discord automaticamente</strong>
              <span>Abra o Discord assim que o Tor estiver pronto.</span>
            </div>
            <button class="toggle on" id="tog-discord" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
          </div>
          <div class="setting">
            <div class="setting-icon"><svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></div>
            <div class="setting-info">
              <strong>Verificar atualizacoes</strong>
              <span>Confira novas versoes no GitHub ao iniciar.</span>
            </div>
            <button class="toggle on" id="tog-update" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
          </div>
          <div class="setting">
            <div class="setting-icon"><svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg></div>
            <div class="setting-info">
              <strong>Minimizar para a bandeja</strong>
              <span>Fechar a janela oculta o app na bandeja do sistema. Duplo clique no icone para reabrir.</span>
            </div>
            <button class="toggle on" id="tog-tray" onclick="toggleSetting(this)"><i class="toggle-thumb"></i></button>
          </div>
          <div class="save-row">
            <button class="save-btn" onclick="saveSettings()">Salvar alteracoes</button>
            <span id="settings-saved">Configuracoes salvas</span>
          </div>
        </div>
        <div class="settings-card info-card">
          <svg viewBox="0 0 24 24" width="19" height="19"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          <div>
            <span class="section-kicker">RUNTIME</span>
            <h2>Dados temporarios</h2>
            <p>Os arquivos do Tor ficam em uma pasta temporaria e sao removidos quando o launcher e encerrado.</p>
          </div>
        </div>
      </div>
    </section>

    <!-- LOG -->
    <section id="page-log" class="page">
      <div class="page-heading">
        <div>
          <span class="section-kicker">OBSCURE PROXY / ATIVIDADE</span>
          <h1>Atividade</h1>
          <p>Acompanhe o que acontece nesta sessao.</p>
        </div>
      </div>
      <div class="log-toolbar">
        <div>
          <span class="section-kicker">SESSION LOG</span>
          <h2>Atividade recente</h2>
        </div>
        <button class="log-clear-btn" onclick="clearLog()">
          <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          Limpar atividade
        </button>
      </div>
      <div class="log-card" id="log-card">
        <div class="log-empty">
          <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          Nenhuma atividade nesta sessao.
        </div>
      </div>
    </section>

  </main>
</div>

  <!-- Close Modal -->
  <div id="close-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-title">Deseja sair do Obscure Proxy?</div>
      <div class="modal-text">Você pode encerrar o aplicativo completamente ou deixá-lo minimizado na bandeja do sistema para continuar roteando seu tráfego.</div>
      <div class="modal-actions">
        <button class="modal-btn" id="btn-close-cancel">Cancelar</button>
        <button class="modal-btn primary" id="btn-close-tray">Bandeja</button>
        <button class="modal-btn danger" id="btn-close-full">Encerrar</button>
      </div>
    </div>
  </div>

  <!-- Discord Picker Modal -->
  <div id="discord-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-title">Escolha o cliente Discord</div>
      <div class="modal-text">Selecione qual cliente voce quer abrir com o proxy Tor.</div>
      <div id="discord-picker-list" style="display:flex;flex-direction:column;gap:8px;margin:12px 0;"></div>
      <div class="modal-actions">
        <button class="modal-btn" onclick="document.getElementById('discord-modal').classList.remove('show')">Cancelar</button>
      </div>
    </div>
  </div>

<script>
// ─── Navigation ───
function showPage(name, btn) {
  document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
}

// ─── Window buttons ───
document.getElementById('btn-min').addEventListener('click', function() {
  pywebview.api.minimize();
});
document.getElementById('btn-close').addEventListener('click', function() {
  document.getElementById('close-modal').classList.add('show');
});
document.getElementById('btn-close-cancel').addEventListener('click', function() {
  document.getElementById('close-modal').classList.remove('show');
});
document.getElementById('btn-close-tray').addEventListener('click', function() {
  document.getElementById('close-modal').classList.remove('show');
  pywebview.api.minimize_to_tray();
});
document.getElementById('btn-close-full').addEventListener('click', function() {
  document.getElementById('close-modal').classList.remove('show');
  pywebview.api.destroy_app();
});

// ─── Log ───
var logEntries = [];
function _ts() {
  var d = new Date();
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}
function addLog(text, tone) {
  if (!tone) tone = 'ok';
  logEntries.push({ time: _ts(), text: text, tone: tone });
  renderLog();
}
function renderLog() {
  var card = document.getElementById('log-card');
  if (!logEntries.length) {
    card.innerHTML = '<div class="log-empty"><svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg> Nenhuma atividade nesta sessao.</div>';
    return;
  }
  card.innerHTML = logEntries.slice().reverse().map(function(e) {
    var esc = e.text.replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return '<div class="log-entry"><span class="log-time">'+e.time+'</span><span class="log-dot '+e.tone+'"></span><span class="log-text">'+esc+'</span></div>';
  }).join('');
}
function clearLog() { logEntries = []; renderLog(); addLog('Log limpo pelo usuario', 'muted'); }
window._addLog = addLog;

// ─── Status ───
function setStatus(status) {
  var card  = document.getElementById('hero-card');
  var sdot  = document.getElementById('side-dot');
  var slbl  = document.getElementById('side-label');
  var chip  = document.getElementById('status-chip-label');
  var title = document.getElementById('hero-title');
  var sub   = document.getElementById('hero-sub');
  var olbl  = document.getElementById('orb-label');
  var mbtn  = document.getElementById('main-btn');
  var ast   = document.getElementById('app-status-strong');
  var ass   = document.getElementById('app-status-small');

  card.className = 'hero-card' + (status === 'connected' ? ' connected' : status === 'connecting' ? ' connecting' : '');

  sdot.className = 'side-dot' + (status === 'connected' ? ' green' : '');

  if (status === 'connected') {
    slbl.textContent = 'Proxy ativo';
    chip.textContent = 'CONEXAO ATIVA';
    title.textContent = 'Conexao segura ativa';
    sub.textContent   = 'O aplicativo esta usando o proxy local.';
    olbl.textContent  = 'Protegido';
    mbtn.className    = 'main-connect disconnect';
    mbtn.innerHTML    = 'Desconectar <svg viewBox="0 0 24 24" width="15" height="15"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>';
    ast.textContent   = 'Proxy configurado';
    ass.textContent   = 'Trafego roteado com seguranca';
  } else if (status === 'connecting') {
    slbl.textContent = 'Iniciando...';
    chip.textContent  = 'INICIANDO CONEXAO';
    title.textContent = 'Preparando conexao segura...';
    sub.textContent   = 'Inicializando o servico e negociando uma rota...';
    olbl.textContent  = 'Aguarde...';
    mbtn.className    = 'main-connect loading';
    mbtn.innerHTML    = 'Conectando... <svg viewBox="0 0 24 24" width="15" height="15"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>';
    ast.textContent   = 'Aguardando servico';
    ass.textContent   = 'Sera preparado automaticamente';
  } else {
    slbl.textContent = 'Pronto para iniciar';
    chip.textContent  = 'DESCONECTADO';
    title.textContent = 'Tudo pronto para comecar';
    sub.textContent   = 'Inicie o proxy para rotear sua sessao por uma rede privada.';
    olbl.textContent  = 'Privacidade';
    mbtn.className    = 'main-connect';
    mbtn.innerHTML    = 'Iniciar conexao <svg viewBox="0 0 24 24" width="15" height="15"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>';
    ast.textContent   = 'Aguardando servico';
    ass.textContent   = 'Sera preparado automaticamente';
  }
}
window._setStatus = setStatus;

function setProgress(pct) {
  document.getElementById('prog-fill').style.width = pct + '%';
}
window._setProgress = setProgress;

function setPort(p) {
  document.getElementById('port-display').textContent = p;
  document.getElementById('proxy-port').textContent   = p;
  document.getElementById('port-input').value         = p;
}
window._setPort = setPort;

// ─── Main action ───
function mainAction() {
  var mbtn = document.getElementById('main-btn');
  if (mbtn.classList.contains('loading')) return;
  if (mbtn.classList.contains('disconnect')) {
    pywebview.api.stop_tor();
  } else {
    // Verifica se o auto-discord esta ligado
    var autoDiscord = document.getElementById('tog-discord').classList.contains('on');
    if (!autoDiscord) {
      pywebview.api.start_tor_with_client('none');
      return;
    }
    // Detecta clientes disponiveis e mostra o picker
    pywebview.api.detect_clients().then(function(clients) {
      if (!clients || clients.length === 0) {
        pywebview.api.start_tor_with_client('none');
      } else if (clients.length === 1) {
        pywebview.api.start_tor_with_client(clients[0].name);
      } else {
        _showDiscordPicker(clients);
      }
    });
  }
}

function _showDiscordPicker(clients) {
  var list = document.getElementById('discord-picker-list');
  var labels = {
    'Discord': 'Discord',
    'DiscordPTB': 'Discord PTB',
    'DiscordCanary': 'Discord Canary',
    'Dorion': 'Dorion'
  };
  var html = '';
  for (var i = 0; i < clients.length; i++) {
    var c = clients[i];
    var label = labels[c.name] || c.name;
    var status = c.running ? ' (aberto)' : '';
    html += '<button class="modal-btn primary" style="width:100%;text-align:left;" data-client="' + c.name + '">' + label + status + '</button>';
  }
  list.innerHTML = html;
  var btns = list.querySelectorAll('button');
  for (var j = 0; j < btns.length; j++) {
    btns[j].addEventListener('click', function() {
      _pickDiscord(this.getAttribute('data-client'));
    });
  }
  document.getElementById('discord-modal').classList.add('show');
}

function _pickDiscord(name) {
  document.getElementById('discord-modal').classList.remove('show');
  pywebview.api.start_tor_with_client(name);
}

// ─── Copy proxy ───
function copyProxy() {
  var port = document.getElementById('proxy-port').textContent;
  if (navigator.clipboard) {
    navigator.clipboard.writeText('socks5://127.0.0.1:' + port).catch(function(){});
  }
  var icon = document.getElementById('copy-icon');
  icon.innerHTML = '<polyline points="20 6 9 17 4 12"/>';
  setTimeout(function() {
    icon.innerHTML = '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
  }, 1600);
}

// ─── Renovar circuito ───
function renewCircuit() {
  var btn = document.getElementById('renew-btn');
  if (btn) btn.disabled = true;
  pywebview.api.renew_circuit().finally(function() {
    if (btn) setTimeout(function(){ btn.disabled = false; }, 3000);
  });
}

// ─── Reiniciar so o Discord (sem mexer no Tor) ───
function restartDiscordOnly() {
  var btn = document.getElementById('restart-discord-btn');
  if (btn) btn.disabled = true;
  pywebview.api.restart_discord_only().finally(function() {
    if (btn) setTimeout(function(){ btn.disabled = false; }, 3000);
  });
}

// ─── Ver streams ativos no Tor agora (debug do bypass) ───
function checkStreamStatus() {
  var btn = document.getElementById('stream-status-btn');
  if (btn) btn.disabled = true;
  pywebview.api.check_stream_status().finally(function() {
    if (btn) setTimeout(function(){ btn.disabled = false; }, 3000);
  });
}

// ─── Settings ───
function toggleSetting(btn) { btn.classList.toggle('on'); }

function toggleDevMulti(btn) {
  btn.classList.toggle('on');
  var grp = document.getElementById('multi-client-group');
  if (grp) grp.style.display = btn.classList.contains('on') ? '' : 'none';
}

function toggleSelect() {
  var sel = document.querySelector('.select-selected');
  sel.classList.toggle('select-arrow-active');
  document.querySelector('.select-items').classList.toggle('select-hide');
}
function pickSelect(val, text) {
  document.getElementById('client-input').value = val;
  document.querySelector('.select-selected').innerText = text;
  toggleSelect();
}
document.addEventListener('click', function(e) {
  var container = document.getElementById('discord-select');
  if (container && !container.contains(e.target)) {
    document.querySelector('.select-selected').classList.remove('select-arrow-active');
    document.querySelector('.select-items').classList.add('select-hide');
  }
});

function saveSettings() {
  var port    = document.getElementById('port-input').value;
  var discord = document.getElementById('tog-discord').classList.contains('on');
  var closeTray = false; // deprecated field, kept for compat
  var upd     = document.getElementById('tog-update').classList.contains('on');
  var useTray = document.getElementById('tog-tray').classList.contains('on');
  var client  = document.getElementById('client-input').value;
  var devMulti = document.getElementById('tog-dev-multi').classList.contains('on');
  var multiClients = ['Discord', 'DiscordPTB', 'DiscordCanary', 'Dorion'].filter(function(name) {
    var el = document.getElementById('mc-' + name);
    return el && el.classList.contains('on');
  });
  pywebview.api.save_settings(port, discord, closeTray, upd, useTray, client, devMulti, multiClients);
  var lbl = document.getElementById('settings-saved');
  lbl.style.display = 'inline';
  setTimeout(function() { lbl.style.display = 'none'; }, 2500);
}

window._syncSettings = function(discord, upd, tray, client, devMulti, multiClients) {
  if (discord) document.getElementById('tog-discord').classList.add('on');
  else document.getElementById('tog-discord').classList.remove('on');
  
  if (upd) document.getElementById('tog-update').classList.add('on');
  else document.getElementById('tog-update').classList.remove('on');
  
  if (tray) document.getElementById('tog-tray').classList.add('on');
  else document.getElementById('tog-tray').classList.remove('on');

  var devBtn = document.getElementById('tog-dev-multi');
  if (devBtn) {
    if (devMulti) devBtn.classList.add('on');
    else devBtn.classList.remove('on');
  }
  var grp = document.getElementById('multi-client-group');
  if (grp) grp.style.display = devMulti ? '' : 'none';
  (multiClients || []).forEach(function(name) {
    var el = document.getElementById('mc-' + name);
    if (el) el.classList.add('on');
  });
  
  var sel = document.getElementById('client-input');
  if (sel) sel.value = client;
  
  var texts = {
    'auto': 'Automático (Detectar aberto)',
    'Discord': 'Discord Normal',
    'DiscordPTB': 'Discord PTB',
    'DiscordCanary': 'Discord Canary',
    'Dorion': 'Dorion'
  };
  var text = texts[client] || texts['auto'];
  var visual = document.querySelector('.select-selected');
  if (visual) visual.innerText = text;
};

// ─── Init ───
setStatus('idle');
addLog('Aplicacao iniciada', 'ok');
addLog('Arquivos do servico verificados', 'ok');
addLog('Aguardando conexao', 'muted');
</script>
</body>
</html>
"""


# ============================================================
# JS API exposta para o webview
# ============================================================

class TorAPI:
    STEP_IDLE       = 0
    STEP_STARTING   = 1
    STEP_TOR_READY  = 2
    STEP_DISCORD_UP = 3

    def __init__(self):
        self._win            = None   # set after window creation
        self.tor_process     = None
        self.socks_port      = "9050"
        self.control_port    = "9051"
        self.step            = self.STEP_IDLE
        self.current_exe     = sys.executable if getattr(sys, "frozen", False) else None
        self._renew_stop     = None   # threading.Event para parar o loop de renovacao
        
        saved = load_settings()
        self.cfg_auto_discord = saved.get("auto_discord", True)
        self.cfg_close_tray   = saved.get("close_tray", True)   # legacy — kept for compat
        self.cfg_auto_update  = saved.get("auto_update", True)
        self.cfg_use_tray     = saved.get("use_tray", True)   # minimizar para bandeja ao fechar
        self.cfg_discord_client = saved.get("discord_client", "auto")
        self.cfg_dev_multi    = saved.get("dev_multi", False)      # modo dev: abrir varios clientes juntos
        self.cfg_multi_clients = saved.get("multi_clients", [])    # quais clientes (chaves de DISCORD_CLIENTS)
        self.tray_icon        = None


    # ── Window management ──

    def minimize(self):
        if self._win:
            self._win.minimize()

    def minimize_to_tray(self):
        self._minimize_to_tray()

    def destroy_app(self):
        self._destroy_app()

    def _minimize_to_tray(self):
        """Minimiza para bandeja do sistema."""
        if not self._win:
            return
        # Move a janela pra fora da tela em vez de esconder
        # (hide() mata o message loop do pywebview e quebra o pystray)
        self._win.move(-32000, -32000)
        self._win.minimize()
        if self.tray_icon:
            return
        try:
            ico_path = os.path.join(bundled_dir(), "app_icon.ico")
            img = Image.open(ico_path) if os.path.isfile(ico_path) \
                else Image.new("RGBA", (64, 64), (124, 92, 255, 255))
        except Exception:
            img = Image.new("RGBA", (64, 64), (124, 92, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Abrir Obscure Proxy", self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._quit_from_tray),
        )
        self.tray_icon = pystray.Icon("ObscureProxy", img, "Obscure Proxy", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_from_tray(self, icon=None, item=None):
        """Restore window from tray."""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        if self._win:
            self._win.move(200, 100)
            self._win.restore()

    def _quit_from_tray(self, icon=None, item=None):
        """Sair from tray menu."""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self._destroy_app()

    def _destroy_app(self):
        """Kill tor.exe, clean up and destroy the window."""
        self._kill_tor()
        cleanup_runtime_dir()
        if self._win:
            self._win.destroy()



    # ── Settings ──

    def save_settings(self, port, auto_discord, close_tray, auto_update, use_tray, discord_client="auto",
                       dev_multi=False, multi_clients=None):
        port = str(port).strip()
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            self._js("_addLog('Porta invalida. Use 1 a 65535.','err')")
            return
        self.socks_port       = port
        self.cfg_auto_discord = bool(auto_discord)
        self.cfg_close_tray   = bool(close_tray)
        self.cfg_auto_update  = bool(auto_update)
        self.cfg_use_tray     = bool(use_tray)
        self.cfg_discord_client = discord_client
        self.cfg_dev_multi    = bool(dev_multi)
        self.cfg_multi_clients = [c for c in (multi_clients or []) if c in DISCORD_CLIENTS]
        save_settings_to_disk({
            "port": self.socks_port,
            "auto_discord": self.cfg_auto_discord,
            "close_tray": self.cfg_close_tray,
            "auto_update": self.cfg_auto_update,
            "use_tray": self.cfg_use_tray,
            "discord_client": self.cfg_discord_client,
            "dev_multi": self.cfg_dev_multi,
            "multi_clients": self.cfg_multi_clients
        })
        self._js(f"_setPort('{port}')")
        self._js(f"_addLog('Configuracoes salvas. Porta: {port}','ok')")

    # ── Tor control ──

    # Dominios do Discord que NAO devem passar pelo Tor:
    # CDN (upload/download de arquivos, imagens, videos) e WebRTC (chamadas, tela compartilhada)
    PROXY_BYPASS = ";".join([
        "cdn.discordapp.com",
        "media.discordapp.net",
        "images-ext-1.discordapp.net",
        "images-ext-2.discordapp.net",
        "*.discord.media",
        "*.discordapp.net",
        "*.googleapis.com",
        "*.spotify.com",
        "*.scdn.co",
        "*.hcaptcha.com",
        "*.recaptcha.net",
        "challenges.cloudflare.com",
        "<local>",
    ])

    def detect_clients(self):
        """Retorna lista de clientes Discord detectados no PC (rapido)."""
        # 1 chamada so pra ver quais processos estao rodando
        running_set = set()
        try:
            out = subprocess.check_output('tasklist /NH /FO CSV', shell=True, text=True,
                                          creationflags=0x08000000, timeout=3)
            for line in out.splitlines():
                if line:
                    name = line.split('","')[0].strip('"').lower()
                    running_set.add(name)
        except Exception:
            pass

        found = []
        for name, info in DISCORD_CLIENTS.items():
            # Verifica registro + pastas (instantaneo, sem WMIC)
            exe_name = info["exe"]
            installed = False
            
            # Registro
            reg_loc = _query_registry_install(info["reg"])
            if reg_loc and os.path.isfile(os.path.join(reg_loc, exe_name)):
                installed = True
            
            # Pastas conhecidas
            if not installed:
                localappdata = os.environ.get("LOCALAPPDATA", "")
                base = os.path.join(localappdata, name)
                if os.path.isdir(base):
                    pattern = os.path.join(base, "app-*", exe_name)
                    if glob.glob(pattern):
                        installed = True
                    elif os.path.isfile(os.path.join(base, exe_name)):
                        installed = True

            is_running = exe_name.lower() in running_set
            
            if installed or is_running:
                found.append({"name": name, "running": is_running})
        return found

    def start_tor_with_client(self, client_name):
        """Inicia o Tor e abre o cliente Discord escolhido."""
        if self.step != self.STEP_IDLE:
            return
            
        self.last_discord_exe = None
        self._chosen_client = client_name  # Guarda a escolha do usuario
        
        if client_name and client_name != "none":
            exes = find_discord_exe(client_name)
            if exes:
                self.last_discord_exe = exes[0]
                proc_name = os.path.basename(exes[0])
                subprocess.run(["taskkill", "/F", "/IM", proc_name], capture_output=True)
                time.sleep(0.5)
                
        self._start_tor_internal()

    def start_tor(self):
        """Fallback — inicia sem escolher cliente (usa config)."""
        if self.step != self.STEP_IDLE:
            return
        self.last_discord_exe = None
        self._chosen_client = None
        if self.cfg_auto_discord:
            exes = find_discord_exe(self.cfg_discord_client)
            if exes:
                self.last_discord_exe = exes[0]
                proc_name = os.path.basename(exes[0])
                subprocess.run(["taskkill", "/F", "/IM", proc_name], capture_output=True)
                time.sleep(0.5)
        self._start_tor_internal()

    def _start_tor_internal(self):
        base    = app_dir()
        tor_exe = os.path.join(base, "tor.exe")
        torrc   = os.path.join(base, "torrc")
        if not os.path.isfile(tor_exe):
            self._js("_addLog('tor.exe nao encontrado.','err')")
            return

        # Limpa o lock do Tor anterior para permitir reiniciar
        for lockfile in ("lock", "state"):
            lf = os.path.join(base, lockfile)
            try:
                if os.path.isfile(lf):
                    os.remove(lf)
            except Exception:
                pass

        self.step = self.STEP_STARTING
        self._js("_setStatus('connecting')")
        self._js("_setProgress(0)")
        self._js("_addLog('Iniciando o servico e preparando o proxy...','accent')")

        # ControlPort proprio para permitir renovar o circuito (NEWNYM)
        # sem precisar derrubar o Tor inteiro. Usa a porta SOCKS + 1.
        try:
            self.control_port = str(int(self.socks_port) + 1)
        except (TypeError, ValueError):
            self.control_port = "9051"

        cmd = [tor_exe, "-f", torrc,
               "--DataDirectory", base,
               "--GeoIPFile",     os.path.join(base, "geoip"),
               "--GeoIPv6File",   os.path.join(base, "geoip6"),
               "--DisableNetwork", "0",
               "--ControlPort", self.control_port,
               "--CookieAuthentication", "1",
               # Evita que o Tor "aprenda" um timeout de circuito cada vez
               # mais alto/tolerante ao longo de sessoes muito longas — isso
               # e o que costuma travar coisas sensiveis a latencia (video,
               # compartilhamento de tela) depois de horas ligado.
               "--LearnCircuitBuildTimeout", "0",
               "--CircuitBuildTimeout", "20",
               "--MaxCircuitDirtiness", "300"]
        try:
            self.tor_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            threading.Thread(target=self._read_tor, daemon=True).start()
        except Exception as e:
            self.step = self.STEP_IDLE
            self._js("_setStatus('idle')")
            msg = str(e)[:80].replace("'", "\\'")
            self._js(f"_addLog('Falha ao iniciar Tor: {msg}','err')")

    def stop_tor(self):
        self._kill_tor()
        self.step = self.STEP_IDLE
        self._js("_setStatus('idle')")
        self._js("_setProgress(0)")
        self._js("_addLog('Conexao encerrada pelo usuario','muted')")

    def _kill_tor(self):
        self._stop_auto_renew()
        if self.tor_process and self.tor_process.poll() is None:
            self.tor_process.terminate()
            try:
                self.tor_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.tor_process.kill()
        self.tor_process = None
        # Mata qualquer tor.exe preso que ficou de sessao anterior
        subprocess.run(["taskkill", "/F", "/IM", "tor.exe"], capture_output=True)
        time.sleep(0.5)
        # Limpa o lock para garantir que o proximo start funcione
        base = runtime_data_dir()
        for lockfile in ("lock",):
            lf = os.path.join(base, lockfile)
            try:
                if os.path.isfile(lf):
                    os.remove(lf)
            except Exception:
                pass

    def _read_tor(self):
        if not self.tor_process:
            return
        for line in self.tor_process.stdout:
            line = line.rstrip()
            # Sanitize for JS string
            safe = line.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "")
            self._js(f"_addLog('{safe}','muted')")
            if "Bootstrapped 100%" in line:
                self._schedule(self._on_tor_ready)
            elif "Bootstrapped" in line:
                m = re.search(r"Bootstrapped (\d+)%", line)
                if m:
                    pct = int(m.group(1))
                    self._schedule(lambda p=pct: self._on_pct(p))
        if self.step == self.STEP_STARTING:
            self._schedule(self._on_tor_failed)

    def _on_pct(self, pct):
        self._js(f"_setProgress({pct})")
        self._js(f"_addLog('Bootstrap {pct}%...','accent')")

    def _on_tor_ready(self):
        self.step = self.STEP_TOR_READY
        self._js("_setStatus('connected')")
        self._js("_setProgress(100)")
        port = self.socks_port
        self._js(f"_addLog('Servico conectado na porta SOCKS5 {port}','ok')")
        chosen = getattr(self, '_chosen_client', None)
        if chosen and chosen != "none":
            time.sleep(0.5)
            self._launch_discord()
        elif chosen is None and self.cfg_auto_discord:
            time.sleep(0.5)
            self._launch_discord()
        self._start_auto_renew()

    def _on_tor_failed(self):
        self.step = self.STEP_IDLE
        self._js("_setStatus('idle')")
        self._js("_addLog('Tor encerrou inesperadamente.','err')")

    # ── Renovacao de circuito (ControlPort) ──
    #
    # Problema que isso resolve: um circuito Tor que fica em uso continuo
    # por horas (ex: a conexao de WebSocket do Discord com o gateway) nunca
    # e trocado sozinho pelo Tor. Se um dos relays daquele caminho comecar a
    # ficar congestionado/lento com o tempo, tudo que depende de varias
    # trocas de pacote rapidas (like negociar assistir a tela de alguem)
    # comeca a "travar carregando", mesmo que texto/voz ainda pareçam ok
    # (porque sao menos sensiveis a latencia extra).
    #
    # SIGNAL NEWNYM nao migra streams ja abertos para um circuito novo, mas
    # garante que a PROXIMA conexao nova (ex: o pedido pra assistir a tela)
    # va por um circuito fresco em vez de reaproveitar um antigo degradado.

    def _read_control_cookie(self):
        base = runtime_data_dir()
        cookie_path = os.path.join(base, "control_auth_cookie")
        for _ in range(20):
            if os.path.isfile(cookie_path):
                break
            time.sleep(0.25)
        with open(cookie_path, "rb") as f:
            return f.read().hex()

    def _control_read_reply(self, sock, timeout=5):
        """Le uma resposta do protocolo de controle do Tor ate a linha final
        (codigo de 3 digitos seguido de ESPACO, ex: '250 OK'). Linhas de
        continuacao usam '-' ou '+' depois do codigo e nao contam como fim."""
        sock.settimeout(timeout)
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            for line in buf.decode(errors="ignore").split("\r\n"):
                if re.match(r"^\d{3} ", line):
                    return buf.decode(errors="ignore")
        return buf.decode(errors="ignore")

    def _control_session(self):
        """Abre e autentica uma conexao no ControlPort. Retorna o socket
        (ja autenticado) ou None se falhar. Quem chama e responsavel por
        fechar o socket."""
        import socket
        try:
            cookie_hex = self._read_control_cookie()
        except Exception as e:
            self._js(f"_addLog('Nao consegui ler o cookie do ControlPort: {str(e)[:60]}','err')")
            return None
        try:
            s = socket.create_connection(("127.0.0.1", int(self.control_port)), timeout=5)
            s.sendall(f"AUTHENTICATE {cookie_hex}\r\n".encode())
            if not self._control_read_reply(s).startswith("250"):
                s.close()
                return None
            return s
        except Exception:
            return None

    def _control_send(self, command):
        """Sessao curta de um comando so (autentica, manda, fecha)."""
        s = self._control_session()
        if s is None:
            return None
        try:
            s.sendall((command.strip() + "\r\n").encode())
            return self._control_read_reply(s)
        except Exception:
            return None
        finally:
            s.close()

    def renew_circuit(self):
        """Chamavel pela UI (botao) ou automaticamente. Faz duas coisas:

        1) SIGNAL NEWNYM — evita que conexoes NOVAS reaproveitem circuitos
           velhos.
        2) Fecha a forca os circuitos que ja estao BUILT (em uso) agora.
           Isso e o que realmente resolve pra conexoes de longa duracao
           como o WebSocket do gateway do Discord: como o Tor nunca troca
           sozinho a rota de uma conexao ja aberta, so fechando o circuito
           por baixo dela ela cai — e o Discord ja reconecta sozinho (igual
           quando cai o wifi um instante), sem precisar fechar o app.
           Voz e tela compartilhada nao sao afetadas (nao passam pelo Tor).
        """
        if self.step not in (self.STEP_TOR_READY, self.STEP_DISCORD_UP):
            return False
        s = self._control_session()
        if s is None:
            self._js("_addLog('Nao consegui renovar o circuito (ControlPort indisponivel).','err')")
            return False
        try:
            s.sendall(b"SIGNAL NEWNYM\r\n")
            self._control_read_reply(s)

            s.sendall(b"GETINFO circuit-status\r\n")
            info = self._control_read_reply(s)

            closed = 0
            for line in info.splitlines():
                line = line.strip()
                m = re.match(r"^(?:250[+-])?(\d+)\s+BUILT\b", line)
                if m:
                    circ_id = m.group(1)
                    s.sendall(f"CLOSECIRCUIT {circ_id}\r\n".encode())
                    self._control_read_reply(s)
                    closed += 1

            self._js(f"_addLog('Circuito Tor renovado ({closed} conexao(oes) antiga(s) derrubada(s)).','ok')")
            return True
        except Exception as e:
            self._js(f"_addLog('Erro ao renovar circuito: {str(e)[:60]}','err')")
            return False
        finally:
            s.close()

    def check_stream_status(self):
        """Lista os streams TCP ativos no circuito Tor AGORA (GETINFO
        stream-status no ControlPort). Serve pra ver, sem Wireshark e sem
        nada extra pra instalar, quais hosts estao de fato passando pelo
        tunel Tor neste instante — por exemplo, se um host tipo
        c-ewr08-xxxx.discord.media aparecer aqui, quer dizer que aquela
        conexao NAO esta respeitando o PROXY_BYPASS."""
        if self.step not in (self.STEP_TOR_READY, self.STEP_DISCORD_UP):
            self._js("_addLog('Proxy nao esta conectado.','err')")
            return False

        reply = self._control_send("GETINFO stream-status")
        if reply is None:
            self._js("_addLog('Nao consegui consultar o ControlPort.','err')")
            return False

        streams = []
        for line in reply.splitlines():
            line = line.strip()
            # formato tipico: "1610 SUCCEEDED 15 host:port SOURCE_ADDR=... PURPOSE=..."
            m = re.match(r"^(?:250[+-])?(\d+)\s+(\S+)\s+(\d+)\s+(\S+)", line)
            if m:
                streams.append(m.groups())  # (stream_id, status, circ_id, target)

        if not streams:
            self._js("_addLog('Nenhum stream ativo no Tor agora (nada passando pelo tunel neste instante).','muted')")
            return True

        self._js(f"_addLog('{len(streams)} stream(s) ativo(s) no Tor agora:','accent')")
        discord_media_hits = []
        for stream_id, status, circ_id, target in streams:
            safe_target = target.replace("'", "\\'")
            self._js(f"_addLog('  #{stream_id} [{status}] circuito {circ_id} -> {safe_target}','muted')")
            if "discord.media" in target or "discordapp" in target:
                discord_media_hits.append(target)

        if discord_media_hits:
            hosts = ", ".join(discord_media_hits).replace("'", "\\'")
            self._js(f"_addLog('ATENCAO: indo pelo Tor apesar do bypass -> {hosts}','err')")
        else:
            self._js("_addLog('Nenhum host discord.media/discordapp nos streams do Tor agora (bypass ok neste instante).','ok')")
        return True

    def _start_auto_renew(self, interval_sec=600):
        self._stop_auto_renew()
        self._renew_stop = threading.Event()
        stop_evt = self._renew_stop

        def loop():
            while not stop_evt.wait(interval_sec):
                if self.step not in (self.STEP_TOR_READY, self.STEP_DISCORD_UP):
                    return
                self.renew_circuit()

        threading.Thread(target=loop, daemon=True).start()

    def _stop_auto_renew(self):
        if self._renew_stop is not None:
            self._renew_stop.set()
            self._renew_stop = None

    def restart_discord_only(self):
        """Fecha e reabre o Discord SEM tocar no Tor/proxy — o circuito e a
        porta SOCKS continuam exatamente os mesmos. Serve pra isolar se o
        problema e da sessao de midia do Discord (fica velha depois de
        horas) ou do proxy em si. Se isso sozinho ja destravar a tela de
        alguem, o proxy nunca foi o culpado."""
        if self.step not in (self.STEP_TOR_READY, self.STEP_DISCORD_UP):
            self._js("_addLog('Proxy nao esta conectado.','err')")
            return False
        self._js("_addLog('Reiniciando apenas o Discord (Tor continua o mesmo)...','accent')")
        self._launch_discord()
        return True

    # ── Discord ──

    def _launch_one_client(self, discord_exe):
        """Fecha e abre UM executavel de cliente Discord especifico com o
        proxy Tor. Retorna True se conseguiu lancar o processo."""
        proc_name = os.path.basename(discord_exe)
        is_electron = proc_name.lower() in ("discord.exe", "discordptb.exe", "discordcanary.exe")

        self._js(f"_addLog('Fechando {proc_name}...','muted')")
        subprocess.run(["taskkill", "/F", "/IM", proc_name], capture_output=True)
        time.sleep(1.5)

        proxy_arg = f"--proxy-server=socks5://127.0.0.1:{self.socks_port}"
        bypass_arg = f"--proxy-bypass-list={self.PROXY_BYPASS}"
        self._js(f"_addLog('Abrindo {proc_name} via Tor (porta {self.socks_port})...','accent')")
        try:
            if is_electron:
                # Discord/PTB/Canary: Electron aceita --proxy-server direto
                subprocess.Popen(
                    [discord_exe, proxy_arg, bypass_arg],
                    cwd=os.path.dirname(discord_exe),
                    creationflags=0x00000008  # DETACHED_PROCESS
                )
            else:
                # Dorion (Tauri/WebView2): passa proxy via env var do WebView2
                env = os.environ.copy()
                env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{proxy_arg} {bypass_arg}"
                subprocess.Popen(
                    [discord_exe],
                    cwd=os.path.dirname(discord_exe),
                    env=env,
                    creationflags=0x00000008  # DETACHED_PROCESS
                )
            self._js(f"_addLog('{proc_name} iniciado com proxy Tor.','discord')")
            return True
        except Exception as e:
            msg = str(e)[:80].replace("'", "\\'")
            self._js(f"_addLog('Falha ao abrir {proc_name}: {msg}','err')")
            return False

    def _launch_discord(self):
        # Modo dev: multi-conta -> abre todos os clientes marcados, um a um,
        # todos apontando pro mesmo circuito/porta Tor.
        if self.cfg_dev_multi and self.cfg_multi_clients:
            launched_any = False
            for name in self.cfg_multi_clients:
                exe = _find_exe_for_client(name)
                if not exe:
                    self._js(f"_addLog('{name} nao encontrado, pulando.','err')")
                    continue
                if self._launch_one_client(exe):
                    launched_any = True
            if launched_any:
                self.step = self.STEP_DISCORD_UP
            else:
                self._js("_addLog('Nenhum dos clientes marcados foi encontrado.','err')")
            return

        # Modo normal: um unico cliente (auto-detectado ou escolhido)
        if getattr(self, 'last_discord_exe', None):
            exes = [self.last_discord_exe]
        else:
            exes = find_discord_exe(self.cfg_discord_client)

        if not exes or not exes[0]:
            if self.cfg_discord_client != "auto":
                self._js(f"_addLog('{self.cfg_discord_client} nao encontrado.','err')")
            else:
                self._js("_addLog('Nenhum cliente Discord encontrado.','err')")
            return

        if self._launch_one_client(exes[0]):
            self.step = self.STEP_DISCORD_UP

    # ── Helpers ──

    def _js(self, code):
        try:
            if self._win:
                self._win.evaluate_js(code)
        except Exception:
            pass

    def _schedule(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    # ── Called once the window is loaded ──

    def on_loaded(self):
        base = app_dir()
        missing = [f for f in ("tor.exe", "torrc", "geoip", "geoip6")
                   if not os.path.isfile(os.path.join(base, f))]
        if missing:
            lst = ", ".join(missing)
            self._js(f"_addLog('Arquivos ausentes: {lst}','err')")
        else:
            detected = load_settings().get("port", detect_socks_port(os.path.join(base, "torrc")))
            self.socks_port = str(detected)
            self._js(f"_setPort('{self.socks_port}')")
            self._js(f"_addLog('Pronto. Porta SOCKS5: {self.socks_port}','ok')")

        self._js(f"_syncSettings({str(self.cfg_auto_discord).lower()}, {str(self.cfg_auto_update).lower()}, {str(self.cfg_use_tray).lower()}, '{self.cfg_discord_client}', {str(self.cfg_dev_multi).lower()}, {json.dumps(self.cfg_multi_clients)})")

        if self.cfg_auto_update:
            threading.Thread(target=self._bg_check_update, daemon=True).start()

    def _bg_check_update(self):
        new_ver = check_for_update()
        if new_ver:
            safe = str(new_ver).replace("'", "\\'")
            self._js(f"_addLog('Nova versao disponivel: {safe}','accent')")


# ============================================================
# Entry point
# ============================================================

def _on_loaded(window, api):
    api.on_loaded()


if __name__ == "__main__":
    _single_instance_mutex = ensure_single_instance()  # sai aqui mesmo se ja tiver outra instancia aberta

    api    = TorAPI()
    window = webview.create_window(
        title="Obscure Proxy",
        html=HTML,
        width=WIN_W,
        height=WIN_H,
        resizable=False,
        frameless=True,
        easy_drag=False,
        js_api=api,
        background_color="#0d0f15",
    )
    api._win = window
    webview.start(func=_on_loaded, args=(window, api), gui="edgechromium")