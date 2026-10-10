<div align="center">

# Obscure Proxy

**A self-contained, portable Tor proxy with DPI bypass capabilities for isolated application sessions.**

[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
[![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)](#)
[![Next.js](https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)](#)
[![Windows](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)](#)

[Features](#features) · [Architecture](#architecture) · [Installation](#installation) · [Configuration](#configuration) · [FAQ](#faq)

</div>

---

Obscure Proxy is a specialized SOCKS5 proxy wrapper built in Python. It seamlessly bundles the Tor network executable, configuration, and GeoIP data into a single portable binary. It is designed to provide secure, isolated proxy sessions with built-in DPI (Deep Packet Inspection) bypass techniques primarily targeting UDP/WebRTC traffic.

## Features

| Capability | Description | Requirements |
|---|---|---|
| **Portable Bundling** | Single `.exe` deployment containing `tor.exe`, configurations, and local web assets. | PyInstaller, Windows x64 |
| **DPI Bypass** | Intercepts and mitigates Deep Packet Inspection on STUN/WebRTC traffic (74-byte UDP packets). | WinDivert (`pydivert`), Admin privileges |
| **Multi-Client Isolation** | Simultaneously routes distinct clients (Discord, PTB, Canary, Dorion) through isolated proxy connections. | Windows OS |
| **Modern UI** | Lightweight frontend built with React, Next.js, and Tailwind, rendered via Webview2. | `pywebview` |

## Architecture

Obscure Proxy operates by extracting its bundled dependencies to a secure temporary directory at runtime. The architecture is composed of three main layers:

1. **Proxy Engine**: A standalone, headless Tor instance configured for SOCKS5 routing on `127.0.0.1`.
2. **Packet Manipulation**: A kernel-level interceptor using WinDivert to deploy decoy packets and bypass DPI heuristics before the real traffic is transmitted.
3. **Application Interface**: A hardware-accelerated (or dynamically throttled) Webview2 interface that manages proxy lifecycle, client binding, and logging without relying on external web browsers.

## Installation

### Prerequisites

Ensure you have Python 3.12 or higher installed. The following dependencies are required to build and run the source code:

```bash
pip install pywebview pystray pillow pydivert
```

### Running from Source

To run the application directly from the source tree:

```bash
python tor_app.py
```

### Building the Executable

Obscure Proxy can be compiled into a single executable using PyInstaller. The build process packages the necessary Tor binaries, Python scripts, and UI assets.

```bash
python -m PyInstaller --onefile --noconsole --name "Obscure Proxy" ^
    --icon "app_icon.ico" ^
    --add-data "app_icon.ico;." ^
    --add-data "tor.exe;." ^
    --add-data "torrc;." ^
    --add-data "geoip;." ^
    --add-data "geoip6;." ^
    tor_app.py
```

## Configuration

Settings are persistently stored in `%LOCALAPPDATA%\Obscure Proxy\settings.json`. The proxy listens on port `9050` by default.

### Multi-Client Mode

The developer mode allows you to bind the proxy to multiple concurrent client installations. Supported targets include:

* Discord Normal
* Discord PTB
* Discord Canary
* Dorion

Each client instance operates in a separate data folder, preventing session collision and ensuring complete network isolation per instance.

## FAQ

<details>
<summary><strong>Do I need to install Tor separately?</strong></summary>

No. The required Tor binaries (`tor.exe`), configurations (`torrc`), and routing tables (`geoip`, `geoip6`) are completely bundled within the application.
</details>

<details>
<summary><strong>Why does the DPI Bypass require Administrator privileges?</strong></summary>

The DPI bypass module (`dpi_bypass.py`) relies on `pydivert`, which interfaces with the WinDivert kernel driver. Packet interception and injection at the network layer strictly require administrative elevation in Windows.
</details>

<details>
<summary><strong>Are temporary files left behind?</strong></summary>

The application extracts operational files to `%TEMP%\Obscure Proxy` during execution. These files are securely purged when the application is gracefully terminated.
</details>

## Disclaimer

Obscure Proxy is provided for educational and analytical purposes. You are responsible for ensuring that your use of packet interception and proxying complies with all applicable local, state, and federal laws.

## License

MIT License
