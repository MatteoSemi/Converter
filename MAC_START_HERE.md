# Run PNG Vector on your Mac

This is Python source, not a signed macOS .app. It was tested on Linux; installation and native GUI operation on macOS have **not** been verified. Installation downloads open-source dependencies. Image conversion then stays local.

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
