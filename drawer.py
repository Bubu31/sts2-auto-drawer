"""
Slay the Spire 2 - Auto Drawer
Dessine automatiquement des images dans le jeu en simulant la souris.
Supporte les images raster (PNG, JPG) et les fichiers SVG.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ctypes
import json
import pyautogui
import cv2
import numpy as np
from PIL import Image, ImageTk
import threading
import time
import keyboard
import sys
import os

# En mode exe (PyInstaller), sauvegarder à côté de l'exe, pas dans le dossier temp
if getattr(sys, 'frozen', False):
    _app_dir = os.path.dirname(sys.executable)
else:
    _app_dir = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_app_dir, "settings.json")

# Fix DPI scaling sur Windows — DOIT être avant tout appel tkinter/pyautogui
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Sécurité pyautogui
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.001

# --- Traitement d'image ---

def image_to_contours(image_path, threshold=128, simplify=2.0):
    """Charge une image et extrait les contours sous forme de liste de polylignes."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Impossible de charger l'image: {image_path}")

    # Seuillage pour obtenir une image binaire
    _, binary = cv2.threshold(img, threshold, 255, cv2.THRESH_BINARY_INV)

    # Trouver les contours
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    # Simplifier les contours
    simplified = []
    for contour in contours:
        epsilon = simplify * cv2.arcLength(contour, True) / 1000
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) >= 2:
            points = [(int(p[0][0]), int(p[0][1])) for p in approx]
            points.append(points[0])  # Fermer le contour
            simplified.append(points)

    return simplified, img.shape[:2]


def image_to_edges(image_path, canny_low=50, canny_high=150):
    """Utilise la détection de bords Canny pour extraire les traits."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Impossible de charger l'image: {image_path}")

    # Flou pour réduire le bruit
    blurred = cv2.GaussianBlur(img, (5, 5), 0)

    # Détection de bords
    edges = cv2.Canny(blurred, canny_low, canny_high)

    # Trouver les contours depuis les bords
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    lines = []
    for contour in contours:
        if len(contour) >= 2:
            points = [(int(p[0][0]), int(p[0][1])) for p in contour]
            lines.append(points)

    return lines, img.shape[:2]


# --- Traitement SVG ---

def svg_to_paths(svg_path, num_points_per_curve=20):
    """Parse un fichier SVG et retourne les chemins sous forme de polylignes."""
    try:
        from svgpathtools import svg2paths
    except ImportError:
        raise ImportError("Installe svgpathtools: pip install svgpathtools")

    paths, _ = svg2paths(svg_path)

    lines = []
    for path in paths:
        points = []
        for segment in path:
            for t in np.linspace(0, 1, num_points_per_curve):
                pt = segment.point(t)
                points.append((pt.real, pt.imag))
        if len(points) >= 2:
            lines.append(points)

    # Calculer les bornes pour le redimensionnement
    all_points = [p for line in lines for p in line]
    if not all_points:
        return [], (100, 100)

    xs, ys = zip(*all_points)
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)

    # Normaliser les coordonnées
    min_x, min_y = min(xs), min(ys)
    normalized = []
    for line in lines:
        normalized.append([(x - min_x, y - min_y) for x, y in line])

    return normalized, (int(h) + 1, int(w) + 1)


# --- Moteur de dessin ---

class DrawingEngine:
    def __init__(self):
        self.is_drawing = False
        self.is_paused = False
        self.stop_requested = False
        self.progress_callback = None
        self.speed = 0.002

    def scale_paths(self, paths, source_size, draw_offset, draw_scale, canvas_pos):
        """Redimensionne les chemins selon le placement choisi par l'utilisateur."""
        src_h, src_w = source_size
        cx, cy = canvas_pos
        ox, oy = draw_offset

        scaled = []
        for path in paths:
            scaled_path = [
                (int(x * draw_scale + cx + ox), int(y * draw_scale + cy + oy))
                for x, y in path
            ]
            scaled.append(scaled_path)

        return scaled

    @staticmethod
    def _interpolate_path(path, max_gap=3):
        """Ajoute des points intermédiaires tous les max_gap pixels.
        Évite les sauts de souris sur les longues distances."""
        if len(path) < 2:
            return path

        result = [path[0]]
        for i in range(1, len(path)):
            x0, y0 = result[-1]
            x1, y1 = path[i]
            dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

            if dist > max_gap:
                steps = int(dist / max_gap) + 1
                for s in range(1, steps):
                    t = s / steps
                    result.append((int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)))

            result.append((x1, y1))
        return result

    def draw(self, paths, canvas_pos, source_size, draw_offset, draw_scale):
        """Dessine les chemins avec pyautogui + interpolation pour un tracé fluide."""
        self.is_drawing = True
        self.stop_requested = False
        self.is_paused = False

        scaled = self.scale_paths(paths, source_size, draw_offset, draw_scale, canvas_pos)
        total_paths = len(scaled)

        try:
            for i, path in enumerate(scaled):
                if self.stop_requested:
                    break

                while self.is_paused:
                    time.sleep(0.1)
                    if self.stop_requested:
                        break

                if len(path) < 2:
                    continue

                # Interpoler : max 3px entre chaque point
                smooth = self._interpolate_path(path, max_gap=3)

                # Aller au premier point
                pyautogui.moveTo(smooth[0][0], smooth[0][1])
                time.sleep(0.01)

                # Clic-glisser le long du contour interpolé
                pyautogui.mouseDown(button='left')
                for point in smooth[1:]:
                    if self.stop_requested:
                        pyautogui.mouseUp(button='left')
                        break
                    pyautogui.moveTo(point[0], point[1])
                    if self.speed > 0:
                        time.sleep(self.speed)

                pyautogui.mouseUp(button='left')

                if self.progress_callback:
                    self.progress_callback(i + 1, total_paths)

                time.sleep(0.02)

        finally:
            self.is_drawing = False
            pyautogui.mouseUp(button='left')

    def stop(self):
        self.stop_requested = True

    def pause(self):
        self.is_paused = not self.is_paused
        return self.is_paused


# --- Interface graphique ---

class DrawerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("STS2 Auto Drawer")
        self.root.geometry("800x850")
        self.root.resizable(True, True)

        self.engine = DrawingEngine()
        self.paths = []
        self.source_size = (100, 100)
        self.canvas_pos = (0, 0)
        self.canvas_size = (500, 500)
        self.selecting_canvas = False

        # Placement visuel du dessin (offset en pixels source, échelle)
        self.draw_offset_x = 0.0  # offset X dans la zone de dessin (pixels réels)
        self.draw_offset_y = 0.0
        self.draw_scale = 1.0     # échelle appliquée aux contours

        # Drag state
        self._drag_start = None

        self._build_ui()
        self._setup_hotkeys()
        self._load_settings()

    def _build_ui(self):
        # --- Frame fichier ---
        file_frame = ttk.LabelFrame(self.root, text="Image source", padding=10)
        file_frame.pack(fill="x", padx=10, pady=5)

        self.file_path_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.file_path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(file_frame, text="Ouvrir image", command=self._open_image).pack(side="left", padx=2)
        ttk.Button(file_frame, text="Ouvrir SVG", command=self._open_svg).pack(side="left", padx=2)

        # --- Galerie ---
        gallery_frame = ttk.LabelFrame(self.root, text="Galerie", padding=5)
        gallery_frame.pack(fill="x", padx=10, pady=5)

        gallery_top = ttk.Frame(gallery_frame)
        gallery_top.pack(fill="x")

        self.gallery_dir_var = tk.StringVar()
        ttk.Entry(gallery_top, textvariable=self.gallery_dir_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(gallery_top, text="Choisir dossier", command=self._choose_gallery_dir).pack(side="left", padx=2)
        ttk.Button(gallery_top, text="Rafraîchir", command=self._refresh_gallery).pack(side="left", padx=2)

        # Zone scrollable pour les thumbnails
        gallery_scroll_frame = ttk.Frame(gallery_frame)
        gallery_scroll_frame.pack(fill="x", pady=(5, 0))

        self.gallery_canvas = tk.Canvas(gallery_scroll_frame, height=90, bg="#1e1e1e", highlightthickness=0)
        gallery_scrollbar = ttk.Scrollbar(gallery_scroll_frame, orient="horizontal", command=self.gallery_canvas.xview)
        self.gallery_canvas.configure(xscrollcommand=gallery_scrollbar.set)

        gallery_scrollbar.pack(side="bottom", fill="x")
        self.gallery_canvas.pack(side="top", fill="x")

        self.gallery_inner = ttk.Frame(self.gallery_canvas)
        self.gallery_canvas.create_window((0, 0), window=self.gallery_inner, anchor="nw")
        self.gallery_inner.bind('<Configure>', lambda e: self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_canvas.bind('<MouseWheel>', lambda e: self.gallery_canvas.xview_scroll(-1 * (e.delta // 120), "units"))

        self._gallery_thumbs = []  # garder les refs pour éviter le garbage collection

        # --- Frame méthode ---
        method_frame = ttk.LabelFrame(self.root, text="Méthode de détection", padding=10)
        method_frame.pack(fill="x", padx=10, pady=5)

        self.method_var = tk.StringVar(value="contours")
        ttk.Radiobutton(method_frame, text="Contours (seuillage)", variable=self.method_var, value="contours").pack(side="left", padx=10)
        ttk.Radiobutton(method_frame, text="Bords (Canny)", variable=self.method_var, value="edges").pack(side="left", padx=10)

        # Seuil
        ttk.Label(method_frame, text="Seuil:").pack(side="left", padx=(20, 5))
        self.threshold_var = tk.IntVar(value=128)
        ttk.Scale(method_frame, from_=0, to=255, variable=self.threshold_var, orient="horizontal", length=120).pack(side="left")
        ttk.Label(method_frame, textvariable=self.threshold_var, width=4).pack(side="left")

        # Simplification
        ttk.Label(method_frame, text="Simplif:").pack(side="left", padx=(10, 5))
        self.simplify_var = tk.DoubleVar(value=2.0)
        ttk.Scale(method_frame, from_=0.1, to=10.0, variable=self.simplify_var, orient="horizontal", length=100).pack(side="left")

        # Bouton recalculer
        ttk.Button(method_frame, text="Recalculer", command=self._reprocess).pack(side="left", padx=10)

        # --- Frame canvas du jeu ---
        canvas_frame = ttk.LabelFrame(self.root, text="Zone de dessin dans le jeu", padding=10)
        canvas_frame.pack(fill="x", padx=10, pady=5)

        row1 = ttk.Frame(canvas_frame)
        row1.pack(fill="x")

        ttk.Label(row1, text="X:").pack(side="left")
        self.canvas_x_var = tk.IntVar(value=0)
        ttk.Entry(row1, textvariable=self.canvas_x_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(row1, text="Y:").pack(side="left")
        self.canvas_y_var = tk.IntVar(value=0)
        ttk.Entry(row1, textvariable=self.canvas_y_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(row1, text="Largeur:").pack(side="left")
        self.canvas_w_var = tk.IntVar(value=500)
        ttk.Entry(row1, textvariable=self.canvas_w_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(row1, text="Hauteur:").pack(side="left")
        self.canvas_h_var = tk.IntVar(value=500)
        ttk.Entry(row1, textvariable=self.canvas_h_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Button(row1, text="Sélectionner zone (F2)", command=self._start_select_canvas).pack(side="left", padx=10)

        # --- Frame vitesse ---
        speed_frame = ttk.LabelFrame(self.root, text="Vitesse", padding=10)
        speed_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(speed_frame, text="Délai entre points (ms):").pack(side="left")
        self.speed_var = tk.IntVar(value=2)
        ttk.Scale(speed_frame, from_=0, to=50, variable=self.speed_var, orient="horizontal", length=200).pack(side="left", padx=5)
        ttk.Label(speed_frame, textvariable=self.speed_var, width=4).pack(side="left")

        # --- Éditeur visuel de placement ---
        editor_frame = ttk.LabelFrame(self.root, text="Placement du dessin (glisser + molette pour redimensionner)", padding=5)
        editor_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.preview_canvas = tk.Canvas(editor_frame, bg="#2c2c2c", cursor="fleur")
        self.preview_canvas.pack(fill="both", expand=True)

        # Bindings pour l'éditeur visuel
        self.preview_canvas.bind('<ButtonPress-1>', self._on_editor_press)
        self.preview_canvas.bind('<B1-Motion>', self._on_editor_drag)
        self.preview_canvas.bind('<ButtonRelease-1>', self._on_editor_release)
        self.preview_canvas.bind('<MouseWheel>', self._on_editor_scroll)
        self.preview_canvas.bind('<Configure>', lambda e: self._draw_preview())

        # Boutons de placement rapide
        place_frame = ttk.Frame(editor_frame)
        place_frame.pack(fill="x", pady=(5, 0))
        ttk.Button(place_frame, text="Centrer", command=self._place_center).pack(side="left", padx=3)
        ttk.Button(place_frame, text="Remplir", command=self._place_fill).pack(side="left", padx=3)
        ttk.Button(place_frame, text="Adapter (conserver ratio)", command=self._place_fit).pack(side="left", padx=3)
        ttk.Button(place_frame, text="Reset", command=self._place_reset).pack(side="left", padx=3)

        self.scale_label_var = tk.StringVar(value="Échelle: 100%")
        ttk.Label(place_frame, textvariable=self.scale_label_var).pack(side="right", padx=10)

        # --- Contrôles ---
        ctrl_frame = ttk.Frame(self.root, padding=10)
        ctrl_frame.pack(fill="x")

        self.test_btn = ttk.Button(ctrl_frame, text="Tester sur écran (F3)", command=self._test_overlay)
        self.test_btn.pack(side="left", padx=5)

        self.start_btn = ttk.Button(ctrl_frame, text="Dessiner (F5)", command=self._start_drawing)
        self.start_btn.pack(side="left", padx=5)

        self.pause_btn = ttk.Button(ctrl_frame, text="Pause (F6)", command=self._pause_drawing, state="disabled")
        self.pause_btn.pack(side="left", padx=5)

        self.stop_btn = ttk.Button(ctrl_frame, text="Stop (F7)", command=self._stop_drawing, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        # Barre de progression
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(ctrl_frame, variable=self.progress_var, maximum=100, length=200)
        self.progress_bar.pack(side="left", padx=10, fill="x", expand=True)

        self.status_var = tk.StringVar(value="Charge une image pour commencer")
        ttk.Label(ctrl_frame, textvariable=self.status_var).pack(side="right")

        # --- Info raccourcis ---
        info_frame = ttk.Frame(self.root, padding=(10, 0, 10, 5))
        info_frame.pack(fill="x")
        ttk.Label(
            info_frame,
            text="F2=Zone | F3=Test overlay | F5=Dessiner | F6=Pause | F7=Stop | ESC=Urgence | Molette=Taille | Glisser=Position",
            foreground="gray"
        ).pack()

    def _load_settings(self):
        """Charge les réglages depuis settings.json."""
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            self.canvas_x_var.set(s.get("canvas_x", 0))
            self.canvas_y_var.set(s.get("canvas_y", 0))
            self.canvas_w_var.set(s.get("canvas_w", 500))
            self.canvas_h_var.set(s.get("canvas_h", 500))
            self.method_var.set(s.get("method", "contours"))
            self.threshold_var.set(s.get("threshold", 128))
            self.simplify_var.set(s.get("simplify", 2.0))
            self.speed_var.set(s.get("speed", 2))
            # Recharger le dossier galerie
            gallery_dir = s.get("gallery_dir", "")
            if gallery_dir and os.path.isdir(gallery_dir):
                self.gallery_dir_var.set(gallery_dir)
                self._refresh_gallery()
            # Recharger la dernière image si elle existe encore
            last_file = s.get("last_file", "")
            if last_file and os.path.isfile(last_file):
                self.file_path_var.set(last_file)
                if last_file.lower().endswith(".svg"):
                    self._process_svg(last_file)
                else:
                    self._process_image(last_file)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_settings(self):
        """Sauvegarde les réglages dans settings.json."""
        s = {
            "canvas_x": self.canvas_x_var.get(),
            "canvas_y": self.canvas_y_var.get(),
            "canvas_w": self.canvas_w_var.get(),
            "canvas_h": self.canvas_h_var.get(),
            "method": self.method_var.get(),
            "threshold": self.threshold_var.get(),
            "simplify": self.simplify_var.get(),
            "speed": self.speed_var.get(),
            "gallery_dir": self.gallery_dir_var.get(),
            "last_file": self.file_path_var.get(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def _setup_hotkeys(self):
        keyboard.add_hotkey('F3', self._test_overlay)
        keyboard.add_hotkey('F5', self._start_drawing)
        keyboard.add_hotkey('F6', self._pause_drawing)
        keyboard.add_hotkey('F7', self._stop_drawing)
        keyboard.add_hotkey('F2', self._start_select_canvas)
        keyboard.add_hotkey('escape', self._emergency_stop)

    def _choose_gallery_dir(self):
        """Ouvre un sélecteur de dossier pour la galerie."""
        d = filedialog.askdirectory(title="Choisir le dossier de dessins")
        if d:
            self.gallery_dir_var.set(d)
            self._refresh_gallery()
            self._save_settings()

    def _refresh_gallery(self):
        """Scanne le dossier et affiche les thumbnails."""
        # Nettoyer
        for widget in self.gallery_inner.winfo_children():
            widget.destroy()
        self._gallery_thumbs.clear()

        d = self.gallery_dir_var.get()
        if not d or not os.path.isdir(d):
            return

        extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.svg')
        files = sorted([
            f for f in os.listdir(d)
            if f.lower().endswith(extensions)
        ])

        thumb_size = 70
        for f in files:
            filepath = os.path.join(d, f)
            frame = ttk.Frame(self.gallery_inner, padding=2)
            frame.pack(side="left", padx=2)

            # Créer le thumbnail
            try:
                if f.lower().endswith('.svg'):
                    # Pour les SVG, juste une icône texte
                    label = tk.Label(
                        frame, text="SVG", bg="#333", fg="white",
                        width=10, height=4, font=("Arial", 9)
                    )
                    label.pack()
                else:
                    img = Image.open(filepath)
                    img.thumbnail((thumb_size, thumb_size))
                    photo = ImageTk.PhotoImage(img)
                    self._gallery_thumbs.append(photo)
                    label = tk.Label(frame, image=photo, bg="#1e1e1e", cursor="hand2")
                    label.pack()

                # Nom du fichier
                name_label = ttk.Label(frame, text=f[:12], font=("Arial", 7))
                name_label.pack()

                # Clic = charger cette image
                label.bind('<Button-1>', lambda e, p=filepath: self._load_from_gallery(p))
                name_label.bind('<Button-1>', lambda e, p=filepath: self._load_from_gallery(p))

            except Exception:
                pass

    def _load_from_gallery(self, filepath):
        """Charge une image depuis la galerie."""
        self.file_path_var.set(filepath)
        if filepath.lower().endswith('.svg'):
            self._process_svg(filepath)
        else:
            self._process_image(filepath)
        self._save_settings()

    def _open_image(self):
        path = filedialog.askopenfilename(
            title="Choisir une image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("Tous", "*.*")]
        )
        if path:
            self.file_path_var.set(path)
            self._process_image(path)
            self._save_settings()

    def _open_svg(self):
        path = filedialog.askopenfilename(
            title="Choisir un SVG",
            filetypes=[("SVG", "*.svg"), ("Tous", "*.*")]
        )
        if path:
            self.file_path_var.set(path)
            self._process_svg(path)
            self._save_settings()

    def _process_image(self, path):
        try:
            if self.method_var.get() == "contours":
                self.paths, self.source_size = image_to_contours(
                    path,
                    threshold=self.threshold_var.get(),
                    simplify=self.simplify_var.get()
                )
            else:
                self.paths, self.source_size = image_to_edges(
                    path,
                    canny_low=max(0, self.threshold_var.get() - 50),
                    canny_high=self.threshold_var.get()
                )

            total_points = sum(len(p) for p in self.paths)
            self.status_var.set(f"{len(self.paths)} contours, {total_points} points détectés")
            self._place_fit()

        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    def _process_svg(self, path):
        try:
            self.paths, self.source_size = svg_to_paths(path)
            total_points = sum(len(p) for p in self.paths)
            self.status_var.set(f"SVG: {len(self.paths)} chemins, {total_points} points")
            self._place_fit()

        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    def _reprocess(self):
        path = self.file_path_var.get()
        if not path:
            return
        if path.lower().endswith('.svg'):
            self._process_svg(path)
        else:
            self._process_image(path)

    def _get_preview_transform(self):
        """Calcule la transformation preview_canvas <-> coordonnées réelles du jeu."""
        widget_w = self.preview_canvas.winfo_width() or 400
        widget_h = self.preview_canvas.winfo_height() or 300

        game_cw = self.canvas_w_var.get() or 500
        game_ch = self.canvas_h_var.get() or 500

        # Échelle pour afficher la zone du jeu dans le widget preview
        preview_scale = min((widget_w - 20) / game_cw, (widget_h - 20) / game_ch)
        # Offset pour centrer la zone du jeu dans le widget
        px = (widget_w - game_cw * preview_scale) / 2
        py = (widget_h - game_ch * preview_scale) / 2

        return preview_scale, px, py

    def _draw_preview(self):
        """Dessine l'éditeur visuel : zone du jeu + contours positionnables."""
        self.preview_canvas.delete("all")

        preview_scale, px, py = self._get_preview_transform()
        game_cw = self.canvas_w_var.get() or 500
        game_ch = self.canvas_h_var.get() or 500

        # Dessiner le cadre de la zone de dessin du jeu
        self.preview_canvas.create_rectangle(
            px, py,
            px + game_cw * preview_scale, py + game_ch * preview_scale,
            outline="#555555", width=2, dash=(4, 4)
        )
        self.preview_canvas.create_text(
            px + 5, py + 5, text="Zone de dessin du jeu",
            anchor="nw", fill="#666666", font=("Arial", 9)
        )

        if not self.paths:
            self.preview_canvas.create_text(
                px + game_cw * preview_scale / 2,
                py + game_ch * preview_scale / 2,
                text="Charge une image...",
                fill="#555555", font=("Arial", 14)
            )
            return

        # Dessiner les contours avec le placement actuel
        src_h, src_w = self.source_size
        colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"]

        for i, path in enumerate(self.paths):
            if len(path) < 2:
                continue
            color = colors[i % len(colors)]

            # Appliquer : source -> draw_scale + draw_offset -> preview_scale
            scaled = []
            for x, y in path:
                rx = x * self.draw_scale + self.draw_offset_x
                ry = y * self.draw_scale + self.draw_offset_y
                sx = rx * preview_scale + px
                sy = ry * preview_scale + py
                scaled.append((sx, sy))

            for j in range(len(scaled) - 1):
                self.preview_canvas.create_line(
                    scaled[j][0], scaled[j][1],
                    scaled[j + 1][0], scaled[j + 1][1],
                    fill=color, width=1
                )

        # Bounding box du dessin
        src_h, src_w = self.source_size
        bx = self.draw_offset_x * preview_scale + px
        by = self.draw_offset_y * preview_scale + py
        bw = src_w * self.draw_scale * preview_scale
        bh = src_h * self.draw_scale * preview_scale
        self.preview_canvas.create_rectangle(
            bx, by, bx + bw, by + bh,
            outline="#ffffff", width=1, dash=(2, 2)
        )

        self.scale_label_var.set(f"Échelle: {self.draw_scale * 100:.0f}%")

    # --- Interaction éditeur visuel ---

    def _on_editor_press(self, event):
        self._drag_start = (event.x, event.y)

    def _on_editor_drag(self, event):
        if self._drag_start is None:
            return

        preview_scale, _, _ = self._get_preview_transform()
        if preview_scale == 0:
            return

        dx = (event.x - self._drag_start[0]) / preview_scale
        dy = (event.y - self._drag_start[1]) / preview_scale

        self.draw_offset_x += dx
        self.draw_offset_y += dy
        self._drag_start = (event.x, event.y)

        self._draw_preview()

    def _on_editor_release(self, event):
        self._drag_start = None

    def _on_editor_scroll(self, event):
        """Molette = zoom du dessin."""
        factor = 1.1 if event.delta > 0 else 0.9

        # Zoomer autour du centre du dessin
        src_h, src_w = self.source_size
        cx = self.draw_offset_x + src_w * self.draw_scale / 2
        cy = self.draw_offset_y + src_h * self.draw_scale / 2

        self.draw_scale *= factor
        self.draw_scale = max(0.01, min(self.draw_scale, 50.0))

        # Recentrer après zoom
        self.draw_offset_x = cx - src_w * self.draw_scale / 2
        self.draw_offset_y = cy - src_h * self.draw_scale / 2

        self._draw_preview()

    def _place_center(self):
        """Centre le dessin dans la zone du jeu."""
        game_cw = self.canvas_w_var.get() or 500
        game_ch = self.canvas_h_var.get() or 500
        src_h, src_w = self.source_size

        self.draw_offset_x = (game_cw - src_w * self.draw_scale) / 2
        self.draw_offset_y = (game_ch - src_h * self.draw_scale) / 2
        self._draw_preview()

    def _place_fill(self):
        """Remplit toute la zone du jeu (peut déformer le ratio)."""
        game_cw = self.canvas_w_var.get() or 500
        game_ch = self.canvas_h_var.get() or 500
        src_h, src_w = self.source_size

        scale_x = game_cw / src_w if src_w > 0 else 1
        scale_y = game_ch / src_h if src_h > 0 else 1
        self.draw_scale = max(scale_x, scale_y)
        self.draw_offset_x = (game_cw - src_w * self.draw_scale) / 2
        self.draw_offset_y = (game_ch - src_h * self.draw_scale) / 2
        self._draw_preview()

    def _place_fit(self):
        """Adapte le dessin pour tenir dans la zone en conservant le ratio."""
        game_cw = self.canvas_w_var.get() or 500
        game_ch = self.canvas_h_var.get() or 500
        src_h, src_w = self.source_size

        scale_x = game_cw / src_w if src_w > 0 else 1
        scale_y = game_ch / src_h if src_h > 0 else 1
        self.draw_scale = min(scale_x, scale_y)
        self.draw_offset_x = (game_cw - src_w * self.draw_scale) / 2
        self.draw_offset_y = (game_ch - src_h * self.draw_scale) / 2
        self._draw_preview()

    def _place_reset(self):
        """Remet le dessin à l'échelle 1:1 en haut à gauche."""
        self.draw_scale = 1.0
        self.draw_offset_x = 0.0
        self.draw_offset_y = 0.0
        self._draw_preview()

    def _start_select_canvas(self):
        """Permet de sélectionner la zone de dessin du jeu avec la souris."""
        if self.selecting_canvas:
            return

        self.selecting_canvas = True
        self.status_var.set("Clic sur le coin HAUT-GAUCHE de la zone de dessin...")

        # Fenêtre overlay transparente
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes('-fullscreen', True)
        self.overlay.attributes('-alpha', 0.3)
        self.overlay.attributes('-topmost', True)
        self.overlay.configure(bg='blue')

        self.select_step = 0
        self.select_points = []

        self.overlay.bind('<Button-1>', self._on_select_click)
        self.overlay.bind('<Escape>', lambda e: self._cancel_select())

    def _on_select_click(self, event):
        self.select_points.append((event.x_root, event.y_root))

        if self.select_step == 0:
            self.select_step = 1
            self.status_var.set("Clic sur le coin BAS-DROIT de la zone de dessin...")
        else:
            x1, y1 = self.select_points[0]
            x2, y2 = self.select_points[1]

            self.canvas_x_var.set(min(x1, x2))
            self.canvas_y_var.set(min(y1, y2))
            self.canvas_w_var.set(abs(x2 - x1))
            self.canvas_h_var.set(abs(y2 - y1))

            self.overlay.destroy()
            self.selecting_canvas = False
            self.status_var.set(f"Zone définie: ({min(x1,x2)}, {min(y1,y2)}) {abs(x2-x1)}x{abs(y2-y1)}")
            self._save_settings()

    def _cancel_select(self):
        self.overlay.destroy()
        self.selecting_canvas = False
        self.status_var.set("Sélection annulée")

    def _test_overlay(self):
        """Affiche un overlay transparent sur l'écran avec le dessin aux positions réelles."""
        if not self.paths:
            self.status_var.set("Charge d'abord une image !")
            return

        canvas_pos = (self.canvas_x_var.get(), self.canvas_y_var.get())
        draw_offset = (self.draw_offset_x, self.draw_offset_y)

        # Calculer les positions réelles
        scaled = self.engine.scale_paths(
            self.paths, self.source_size, draw_offset, self.draw_scale, canvas_pos
        )

        # Créer un overlay plein écran transparent
        overlay = tk.Toplevel(self.root)
        overlay.attributes('-fullscreen', True)
        overlay.attributes('-topmost', True)
        overlay.attributes('-alpha', 0.7)
        overlay.configure(bg='black')
        overlay.bind('<Escape>', lambda e: overlay.destroy())
        overlay.bind('<Button-1>', lambda e: overlay.destroy())

        oc = tk.Canvas(overlay, bg='black', highlightthickness=0)
        oc.pack(fill="both", expand=True)

        # Dessiner le cadre de la zone du jeu
        cx, cy = canvas_pos
        cw = self.canvas_w_var.get()
        ch = self.canvas_h_var.get()
        oc.create_rectangle(cx, cy, cx + cw, cy + ch, outline='yellow', width=2, dash=(6, 4))
        oc.create_text(cx + 5, cy - 15, text="Zone du jeu", anchor="nw", fill="yellow", font=("Arial", 11))

        # Dessiner les contours aux positions exactes
        colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"]
        for i, path in enumerate(scaled):
            if len(path) < 2:
                continue
            color = colors[i % len(colors)]
            for j in range(len(path) - 1):
                oc.create_line(
                    path[j][0], path[j][1],
                    path[j + 1][0], path[j + 1][1],
                    fill=color, width=2
                )

        oc.create_text(
            cx + cw / 2, cy + ch + 30,
            text="Ceci est exactement ce que la souris va tracer. Clic ou ESC pour fermer.",
            fill="white", font=("Arial", 13)
        )

        self.status_var.set("Overlay de test — clic ou ESC pour fermer")

    def _start_drawing(self):
        if self.engine.is_drawing or not self.paths:
            if not self.paths:
                self.status_var.set("Charge d'abord une image !")
            return

        self.engine.speed = self.speed_var.get() / 1000.0
        self.engine.progress_callback = self._on_progress

        canvas_pos = (self.canvas_x_var.get(), self.canvas_y_var.get())
        draw_offset = (self.draw_offset_x, self.draw_offset_y)
        draw_scale = self.draw_scale

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")

        self.status_var.set("Dessin en cours... (ESC pour arrêter)")

        # Countdown avant de commencer
        def countdown_and_draw():
            for i in range(3, 0, -1):
                if self.engine.stop_requested:
                    return
                self.root.after(0, lambda n=i: self.status_var.set(f"Début dans {n}..."))
                time.sleep(1)

            self.root.after(0, lambda: self.status_var.set("Dessin en cours..."))
            self.engine.draw(self.paths, canvas_pos, self.source_size, draw_offset, draw_scale)
            self.root.after(0, self._on_drawing_done)

        thread = threading.Thread(target=countdown_and_draw, daemon=True)
        thread.start()

    def _on_progress(self, current, total):
        pct = (current / total) * 100
        self.root.after(0, lambda: self.progress_var.set(pct))
        self.root.after(0, lambda: self.status_var.set(f"Trait {current}/{total}"))

    def _on_drawing_done(self):
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.progress_var.set(100)
        self.status_var.set("Dessin terminé !")

    def _pause_drawing(self):
        if self.engine.is_drawing:
            paused = self.engine.pause()
            self.pause_btn.config(text="▶ Reprendre (F6)" if paused else "⏸ Pause (F6)")
            self.status_var.set("En pause" if paused else "Dessin en cours...")

    def _stop_drawing(self):
        self.engine.stop()
        self.status_var.set("Arrêté")
        self._on_drawing_done()

    def _emergency_stop(self):
        self.engine.stop()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self._save_settings()
        self.root.destroy()


if __name__ == "__main__":
    app = DrawerApp()
    app.run()
