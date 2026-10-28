# Run PNG Vector on your Mac

This is Python source, not a signed macOS .app. It was tested on Linux; installation and native GUI operation on macOS have **not** been verified. Installation downloads open-source dependencies. Image conversion then stays local.

## macOS 11.7.10 Big Sur on Intel

Use **MacPorts** for this older macOS version. The original Homebrew setup is intended for newer systems. The original Qt 6.9.3 wheel requires macOS 12; the separate `requirements-macos11.txt` selects Qt 6.6.3.1, whose official wheel targets macOS 11 and includes Intel support. Native macOS execution remains unverified.

1. Install Apple's Command Line Tools: open Terminal, run `xcode-select --install`, and complete the installation if needed. If Terminal says the tools are already installed, continue.
2. Go to https://www.macports.org/install.php and select the **macOS 11 Big Sur** installer. Install it; choose the version matching Big Sur, rather than the newest macOS package.
3. Download a **fresh source ZIP** from https://github.com/MatteoSemi/Converter/archive/refs/heads/main.zip and extract it into a new folder. Do not overwrite an older folder containing your images or settings.
4. In Terminal, type `cd `, drag that new extracted folder from Finder into Terminal, and press Return.
5. Run:

   ```sh
   bash scripts/setup-macports.sh
   ```

   This installs Python 3.12, Ghostscript, Potrace and Cairo through MacPorts. It may compile packages, which can take time. `sudo` will ask for your Mac login password; Terminal displays no characters while you type it. It installs the Python dependencies from the separate Big Sur profile.
6. Start the converter:

   ```sh
   bash 'Launch PNG Vector.command'
   ```

If MacPorts cannot install the Command Line Tools or a package on your system, stop and copy the error message. The compatibility checks described below do not guarantee that all system packages will install on your particular Mac.

## Newer macOS using Homebrew

1. Install **Homebrew** using the instructions at https://brew.sh if it is not already installed.
2. Download the source ZIP and double-click it to extract it. Keep the whole extracted folder together.
3. Open **Terminal** (Applications → Utilities). Type `cd ` (including the space), drag the extracted folder from Finder into Terminal, and press Return.
4. Paste this command and press Return:

   ```sh
   bash scripts/setup-mac.sh
   ```

   This installs Python 3.12, Ghostscript, Potrace and Cairo using Homebrew, then installs the pinned Python packages into this folder's `.venv`. Homebrew may ask for your computer's normal installation permissions.
5. When setup completes, paste:

   ```sh
   bash 'Launch PNG Vector.command'
   ```

   The converter window should open. On subsequent runs you can use this command from the folder, or open `Launch PNG Vector.command` in Finder if your macOS settings allow it. You do not need to publish a cloud environment to use the application on your Mac.

If a command fails, copy its error message. Avoid replacing dependencies or weakening macOS security settings just to make it run. The first diagnostic command is `.venv/bin/python -m pngvector.doctor`. For a wheel/install error, include your macOS version and whether About This Mac says Apple M-series or Intel.

The default preserves traced detail and leaves fully transparent regions unpainted. Partially transparent sources require an explicit EPS background choice or SVG/PDF export. Use Exact pixel grid for numerical pixel-colour preservation; smooth tracing is an approximation.
