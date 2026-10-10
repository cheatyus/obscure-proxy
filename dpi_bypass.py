import os
import sys
import ctypes
import time
import traceback
import site

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.append(user_site)

if not is_admin():
    print("Elevando privilégios para Administrador...")
    script_path = os.path.abspath(__file__)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script_path}"', None, 1)
    sys.exit()

try:
    import pydivert
except ImportError as e:
    print(f"Erro ao importar pydivert (Execute 'pip install pydivert'): {e}")
    input("Pressione ENTER para fechar...")
    sys.exit()

def start_dpi_bypass():
    print("=" * 50)
    print("🛡️ Anti-DPI do Discord Ativado (Python/WinDivert)")
    print("Interceptando tráfego UDP (pacotes de 74 bytes)...")
    print("=" * 50)
    
    filter_str = "outbound and udp and udp.PayloadLength == 74"
    
    # Guarda os IPs/Portas que já receberam o bypass para não floodar a conexão
    bypassed_destinations = set()
    
    try:
        with pydivert.WinDivert(filter_str) as w:
            for packet in w:
                dest_key = f"{packet.dst_addr}:{packet.dst_port}"
                
                if dest_key not in bypassed_destinations:
                    print(f"[*] Primeiro STUN WebRTC para {dest_key} -> Aplicando Bypass DPI...")
                    
                    p1 = pydivert.Packet(packet.raw, wd_addr=packet.wd_addr)
                    p1.payload = b'\x00'
                    
                    p2 = pydivert.Packet(packet.raw, wd_addr=packet.wd_addr)
                    p2.payload = b'\x01'
                    
                    try:
                        w.send(p1)
                        w.send(p2)
                        time.sleep(0.05) 
                        print(f"   [+] Bypass injetado para {dest_key}!")
                    except Exception as e:
                        print(f"Erro ao enviar pacote isca: {e}")
                        
                    bypassed_destinations.add(dest_key)
                else:
                    # Apenas deixa o pacote passar normalmente se já fizemos o bypass
                    pass
                
                # Envia o pacote original
                w.send(packet)
                
    except PermissionError:
        print("ERRO: O WinDivert precisa de privilégios de Administrador absolutos.")
        input("Pressione Enter para sair...")
    except Exception as e:
        print(f"Erro inesperado no Anti-DPI: {e}")
        traceback.print_exc()
        input("Pressione Enter para sair...")

if __name__ == "__main__":
    try:
        start_dpi_bypass()
    except Exception as e:
        print(f"CRASH FATAL: {e}")
        traceback.print_exc()
        input("Pressione ENTER para fechar...")
