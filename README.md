# STS2 Auto Drawer

Auto-drawing tool for **Slay the Spire 2**. Loads an image (PNG, JPG, SVG...), extracts its contours, and draws them in-game by simulating mouse movements.

## Features

- **Image contour extraction** — threshold-based or Canny edge detection
- **SVG support** — parses SVG paths directly
- **Visual placement editor** — drag to position, scroll to resize, with fit/fill/center presets
- **Gallery** — browse a folder of drawings and load them with one click
- **Fullscreen overlay preview** — see exactly what will be drawn before starting
- **Drawing zone selection** — click two corners (F2) to define the in-game canvas area
- **Speed control** — adjustable delay between points
- **Hotkeys** — F2 (select zone), F3 (preview), F5 (draw), F6 (pause), F7 (stop), ESC (emergency stop)
- **Settings persistence** — all parameters are saved and restored automatically

## Requirements

- Windows (uses Windows DPI APIs and mouse simulation)
- Python 3.10+

## Installation

```bash
pip install -r requirements.txt
```

Dependencies: `pyautogui`, `opencv-python`, `numpy`, `Pillow`, `keyboard`, `svgpathtools`

## Usage

### Run from source

```bash
python drawer.py
```

### Build standalone executable

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name "STS2 Auto Drawer" drawer.py
```

The executable will be in the `dist/` folder.

### Step-by-step

1. **Open an image** — click "Ouvrir image" (raster) or "Ouvrir SVG", or pick one from the gallery
2. **Define the drawing zone** — press **F2**, then click the top-left and bottom-right corners of the in-game canvas
3. **Adjust placement** — drag the drawing in the visual editor, scroll to resize. Use "Adapter" to fit with aspect ratio preserved
4. **Tune detection** — adjust threshold and simplification sliders, then click "Recalculer"
5. **Preview** — press **F3** to see the exact overlay on screen
6. **Draw** — press **F5**. You have 3 seconds to switch to the game window. The mouse will start drawing automatically
7. **Pause/Stop** — **F6** to pause, **F7** to stop, **ESC** for emergency stop

### Tips

- Move your mouse to any screen corner to trigger pyautogui's failsafe (instant stop)
- Lower the speed slider for more accurate drawings, raise it for faster (but rougher) results
- The "Contours" method works best for high-contrast images, "Bords (Canny)" for detailed drawings
- Settings are saved next to the executable/script — they persist between sessions

## License

MIT
